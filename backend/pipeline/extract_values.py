"""THE PLATFORM-VALUE BACKFILL (V2 W3d): extract every due company's share count and cash/burn. DRY RUN by default.

``docs/PLATFORM_EXTRACTION.md`` ("The backfill"). It walks the same work list as the nightly
leg (``platform_values.extraction.work_list``, with ``include_never=True``: never-extracted companies are taken
too), in its order (a new filing, then a retry, then never extracted; within each, pen-only companies last, most
cards first), and runs each company through ``run_extraction(..., run_kind="backfill")``: fetch with the company
free, the machine writer, exactly one attempt row. Nothing here is a call input.

    python -m pipeline.extract_values                         # DRY RUN: the cost plan (no SEC request, no write)
    python -m pipeline.extract_values --no-live               # DRY RUN of a cache-only run: who extracts, who fails
    python -m pipeline.extract_values --apply                 # extract every due company (live, recurring lane)
    python -m pipeline.extract_values --apply --no-live       # cache only (the fork rehearsal): never reaches SEC
    python -m pipeline.extract_values --apply --max-names 200 --budget-s 1800 --ticker OKLO --ticker LEU

THE DRY RUN (the default) reads the work list and the EDGAR cache ON DISK, nothing else: it builds no client,
makes no SEC request, opens its database session read-only and writes no file. Per company and in total it says
what a real run would fetch live and what it would read from the cache, by the rules the client applies:

- the submissions index (``submissions/CIK…json``): re-fetched when older than the recurring 5 minutes;
- companyfacts (``companyfacts/CIK…json``): filing-gated (``ingest.edgar.companyfacts_gate``, judged on the cached
  index), a known 404 answered from the negative cache;
- the ONE values document (``forms/<accession>/<document>``, cached forever): the newer of the latest 10-Q and
  10-K (decision B1, values only), else the newest 20-F / 40-F;
- an annual filer's statements may sit in an exhibit: a bound of ``exhibit_bound()`` more requests (the index
  headers, the filing index, up to ``exhibit_scan_max`` EX-99 documents) that only reading the document can settle;
- a company whose index is not cached: one request known, the rest a range until the index is read.

Bytes: a missing document is bounded by the index's ``size`` column (the whole filing's size, so an upper bound);
an index or companyfacts refresh is estimated by its cached copy's size; anything else is "unknown". What it CANNOT
know without fetching is printed with every plan (``CANNOT_KNOW``).

``--no-live`` (cache only, the repo's convention): the client never reaches SEC (``allow_live=False``; a stale copy
is served). A company whose documents are not all cached is recorded as an ``error`` attempt with the reason
``not cached`` and counted; nothing is fetched. This is the rehearsal on a fork, which has no SEC User-Agent.

``--apply`` takes advisory lock 7 (``pipeline.run_lock.LOCK_ID_EXTRACTION``; one backfill at a time), REFUSES
inside the nightly window (the onboarding job's ``window_bounds``) and while a pass holds the call-of-record lock
(a ``pg_locks`` probe, never a take), and re-checks both between companies: it STOPS cleanly when either begins
(the rest stays due). It is resumable by construction: a finished company is no longer due, so a second run does
only what is left. ``--max-names`` and ``--budget-s`` bound it (checked between companies); ``--ticker`` scopes it
(a ticker on no live card refuses the run). It uses ONE recurring-dial ``EdgarClient``, which places every request
in the global SEC budget's recurring lane (X2). It writes a run artifact (``data/extraction_runs/``, through
``pipeline.extraction_run_log``) and prints a summary.

DECISION B4 (tightened 2026-11-15). The nightly leg and the onboarding step take no never-extracted company until
a backfill has EXTRACTED at least one company: a ``backfill`` attempt with status ``done`` (applied, a candidate
recorded, or nothing found; ``backfill_applied``). Failed (a cache-only ``not cached`` error included), busy and
skipped attempts unlock nothing. The output says where B4 stands before (the dry run) and after (``--apply``).

Exit codes: 0 ran (failed companies are listed, not fatal) or the dry run; 1 the run could not run (no SEC
User-Agent for a live run, no database); 2 a bad argument (an unknown ``--ticker``); 75 refused or stopped (the
nightly window, another backfill, a running pass; nothing more written).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import psycopg

from db.session import connect, name_session
from domain import market_time
from domain.config import DEFAULT_EXTRACTOR_CONFIG
from domain.scopes import DATA_TENANT_ID
from domain.settings import get_settings
from ingest.edgar.client import (
    _DEFAULT_CACHE,
    _NOT_FOUND_SUFFIX,
    RECURRING_CACHE_TTL_S,
    EdgarClient,
)
from ingest.edgar.companyfacts_gate import companyfacts_due
from ingest.edgar.extract import _newer, _takes_annual_path
from ingest.edgar.submissions import filings_of
from pipeline.extraction_run_log import write_extraction_run
from pipeline.onboarding import window_bounds
from pipeline.run_lock import (
    EXIT_ALREADY_RUNNING,
    LOCK_ID_EXTRACTION,
    JobAlreadyRunning,
    JobLock,
    acquire_job_lock,
)
from pipeline.schedule import parse_run_at
from platform_values.extraction import (
    ExtractionRun,
    WorkItem,
    backfill_applied,
    extractable,
    run_extraction,
    work_list,
)
from repositories import platform_extraction_repo as repo

NAME = "extract-values"
RUN_KIND = "backfill"
EXIT_FAILED = 1
EXIT_USAGE = 2

# What the dry run cannot know without fetching (printed with every plan, so the estimate is never mistaken for
# a measurement).
CANNOT_KNOW: tuple[str, ...] = (
    "whether the refreshed submissions index lists a NEWER filing than the cached one (a different document would "
    "be read, and companyfacts could become due)",
    "the exact bytes of a document not on disk (the index's size is the whole filing's, an upper bound) and of an "
    "index or companyfacts refresh (estimated from the cached copy)",
    "for an annual filer, whether its statements are in the primary document or an exhibit (the exhibit bound)",
    "for a company whose index is not cached, which documents it needs at all",
    "SEC throttling (429 pauses) and how much of the one global SEC budget the Workbench's lane takes meanwhile",
    "the CPU time per company (parsing): the time shown is a floor from the request rate alone",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def exhibit_bound() -> int:
    """The most requests an annual filer's exhibit search adds: the index headers, the filing index and up to
    ``exhibit_scan_max`` EX-99 documents (``ingest.edgar.statement_sources``)."""
    return DEFAULT_EXTRACTOR_CONFIG.exhibit_scan_max + 2


# --- the dry run's cost plan: from the disk only -------------------------------------------------------------------


@dataclass(frozen=True)
class DocPlan:
    """One read a real run would make. ``on_disk``: a cached copy exists; ``stale``: a live client would re-fetch it
    anyway; ``known_404``: a cached 404 answers it with no request (the extraction then fails on it).
    ``bytes_on_disk``: the cached copy's size; ``size_hint``: the index's filing size (an upper bound).
    """

    key: str
    kind: str  # "index" | "companyfacts" | "document"
    label: str
    on_disk: bool
    stale: bool = False
    known_404: bool = False
    bytes_on_disk: int | None = None
    size_hint: int | None = None

    @property
    def live(self) -> bool:
        """Would a LIVE run reach SEC for it?"""
        return (not self.on_disk or self.stale) and not self.known_404


@dataclass
class CompanyPlan:
    """What a real run would read for one company. ``path``: ``periodic`` (10-Q / 10-K), ``annual`` (20-F / 40-F),
    ``no periodic filing`` (the index only; a nothing-found attempt), ``index not cached`` (a range) or
    ``no CIK`` (a ``skipped`` attempt, nothing read). ``extra_max``: requests beyond ``docs`` a run MAY make.
    """

    ticker: str
    reason: str
    cards: int
    path: str
    filing: str | None = None
    docs: list[DocPlan] = field(default_factory=list)
    extra_max: int = 0

    @property
    def live_requests(self) -> int:
        return sum(d.live for d in self.docs)

    @property
    def max_requests(self) -> int:
        return self.live_requests + self.extra_max

    @property
    def from_cache(self) -> int:
        return sum(1 for d in self.docs if d.on_disk and not d.stale)

    @property
    def not_cached(self) -> list[DocPlan]:
        """The reads a CACHE-ONLY run cannot serve (a stale copy is served; a missing one is a ``not cached``)."""
        return [d for d in self.docs if not d.on_disk]

    @property
    def offline_ok(self) -> bool:
        return self.path not in ("index not cached",) and not self.not_cached

    @property
    def bytes_bound(self) -> int:
        """Upper bound, in bytes, of the DOCUMENTS a live run fetches whose filing size the index lists."""
        return sum(d.size_hint or 0 for d in self.docs if d.live and d.kind == "document")

    @property
    def refresh_bytes(self) -> int:
        """About the bytes of the index / companyfacts refreshes (their cached copies' size)."""
        return sum(d.bytes_on_disk or 0 for d in self.docs if d.live and d.kind != "document")

    @property
    def unknown_size(self) -> int:
        """Live reads whose size nothing on disk tells."""
        return sum(
            1
            for d in self.docs
            if d.live
            and (
                (d.kind == "document" and d.size_hint is None)
                or (d.kind != "document" and d.bytes_on_disk is None)
            )
        )


def _size(path: Path) -> int | None:
    try:
        return path.stat().st_size
    except OSError:
        return None


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return doc if isinstance(doc, dict) else None


def _filing_size(subs: Mapping[str, Any], accession: str) -> int | None:
    recent = (subs.get("filings") or {}).get("recent") or {}
    accns = recent.get("accessionNumber") or []
    sizes = recent.get("size") or []
    for i, acc in enumerate(accns):
        if acc == accession and i < len(sizes):
            try:
                n = int(sizes[i])
            except (TypeError, ValueError):
                return None
            return n if n > 0 else None
    return None


def _latest_annual(subs: Mapping[str, Any]) -> tuple[str, dict[str, str]] | None:
    """The annual path's selection (``ingest.edgar.annual_shares._latest_annual_filing``, request-free): the newest
    20-F or 40-F by period of report (else filing date); a tie keeps the 20-F."""
    best: tuple[date, str, dict[str, str]] | None = None
    for form in ("20-F", "40-F"):
        hits = filings_of(dict(subs), form)
        if not hits:
            continue
        f = hits[0]
        try:
            d = date.fromisoformat(f.get("report_date") or f["filed"])
        except ValueError:
            continue
        if best is None or d > best[0]:
            best = (d, form, f)
    return None if best is None else (best[1], best[2])


def _doc_plan(cache: Path, subs: Mapping[str, Any], label: str, row: Mapping[str, str]) -> DocPlan:
    key = f"forms/{row['accession']}/{str(row['primary_doc']).rsplit('/', 1)[-1]}"
    path = cache / key
    return DocPlan(
        key=key,
        kind="document",
        label=f"{label} {row['accession']}",
        on_disk=path.exists(),
        bytes_on_disk=_size(path),
        size_hint=_filing_size(subs, row["accession"]),
    )


def _companyfacts_plan(cache: Path, cik: int, subs: Mapping[str, Any], now_ts: float) -> DocPlan:
    key = f"companyfacts/CIK{cik:010d}.json"
    path = cache / key
    if path.exists():
        due = companyfacts_due(fetched_at=path.stat().st_mtime, now=now_ts, submissions=subs).due
        return DocPlan(
            key, "companyfacts", "companyfacts", True, stale=due, bytes_on_disk=_size(path)
        )
    marker = path.with_name(path.name + _NOT_FOUND_SUFFIX)
    known_404 = (
        marker.exists()
        and not companyfacts_due(
            fetched_at=marker.stat().st_mtime, now=now_ts, submissions=subs
        ).due
    )
    return DocPlan(key, "companyfacts", "companyfacts", False, known_404=known_404)


def plan_company(item: WorkItem, *, cache_dir: Path, now: datetime) -> CompanyPlan:
    """What a real run would read for ``item``, from the cache ON DISK only (no client, no request). Pure apart
    from reading files; the rules mirror ``EdgarClient`` on the recurring dial and ``extract_with_annual_fallback``
    with ``values_only=True``."""
    base = CompanyPlan(
        ticker=item.ticker or str(item.security_id), reason=item.reason, cards=item.cards, path=""
    )
    if not item.cik or not str(item.cik).strip().isdigit():
        base.path = "no CIK"
        return base
    cik = int(str(item.cik).strip())
    now_ts = now.timestamp()
    key = f"submissions/CIK{cik:010d}.json"
    path = cache_dir / key
    subs = _read_json(path) if path.exists() else None
    if subs is None:
        base.path = "index not cached"
        base.docs = [DocPlan(key, "index", "submissions index", False)]
        base.extra_max = (
            2 + exhibit_bound()
        )  # companyfacts + one document + the exhibit search, at most
        return base
    stale = (now_ts - path.stat().st_mtime) > RECURRING_CACHE_TTL_S
    base.docs = [
        DocPlan(key, "index", "submissions index", True, stale=stale, bytes_on_disk=_size(path))
    ]
    tenq = next(iter(filings_of(subs, "10-Q")), None)
    tenk = next(iter(filings_of(subs, "10-K")), None)
    # the router's own decision (a company whose newest periodic filing is a 20-F/40-F goes annual)
    values_row = None if _takes_annual_path(dict(subs)) else _newer(tenq, tenk)
    if values_row is not None:
        form = "10-K" if values_row is tenk else "10-Q"
        base.path = "periodic"
        base.filing = f"{form} {values_row['accession']}"
        base.docs.append(_companyfacts_plan(cache_dir, cik, subs, now_ts))
        base.docs.append(_doc_plan(cache_dir, subs, form, values_row))
        return base
    annual = _latest_annual(subs)
    if annual is None:
        base.path = "no periodic filing"
        return base
    form, row = annual
    base.path = "annual"
    base.filing = f"{form} {row['accession']}"
    base.docs.append(_doc_plan(cache_dir, subs, form, row))
    base.docs.append(_companyfacts_plan(cache_dir, cik, subs, now_ts))
    base.extra_max = exhibit_bound()
    return base


def plan_work(items: Iterable[WorkItem], *, cache_dir: Path, now: datetime) -> list[CompanyPlan]:
    """``plan_company`` for each item, in the work list's order."""
    return [plan_company(i, cache_dir=cache_dir, now=now) for i in items]


@dataclass
class PlanTotals:
    companies: int = 0
    live: int = 0
    max_requests: int = 0
    from_cache: int = 0
    bytes_bound: int = 0
    refresh_bytes: int = 0
    unknown_size: int = 0
    offline_ok: int = 0
    offline_missing: int = 0
    no_cik: int = 0
    by_path: dict[str, int] = field(default_factory=dict)

    @classmethod
    def of(cls, plans: Sequence[CompanyPlan]) -> PlanTotals:
        t = cls(companies=len(plans))
        for p in plans:
            t.live += p.live_requests
            t.max_requests += p.max_requests
            t.from_cache += p.from_cache
            t.bytes_bound += p.bytes_bound
            t.refresh_bytes += p.refresh_bytes
            t.unknown_size += p.unknown_size
            t.by_path[p.path] = t.by_path.get(p.path, 0) + 1
            if p.path == "no CIK":
                t.no_cik += 1
            elif p.offline_ok:
                t.offline_ok += 1
            else:
                t.offline_missing += 1
        return t


def _mb(n: int) -> str:
    if n < 1_000_000:
        return f"{n / 1000:,.0f} KB"
    return f"{n / 1_000_000:,.1f} MB"


def _plan_line(p: CompanyPlan, *, live: bool) -> str:
    head = f"  {p.ticker} ({p.reason}, {p.cards} card{'s' if p.cards != 1 else ''}): {p.path}"
    if p.filing:
        head += f" {p.filing}"
    if p.path == "no CIK":
        return f"{head}: nothing read (a skipped attempt)"
    if not live:
        if p.offline_ok:
            extra = " (statements may be in an exhibit not cached)" if p.extra_max else ""
            return f"{head}: everything cached, would extract{extra}"
        missing = ", ".join(d.label for d in p.not_cached) or "the index"
        return f"{head}: NOT CACHED ({missing}): an error attempt 'not cached', no request"
    fetch = [d.label + (" (stale)" if d.on_disk and d.stale else "") for d in p.docs if d.live]
    rng = f"{p.live_requests}" + (f" to {p.max_requests}" if p.extra_max else "")
    parts = [f"{rng} live request{'s' if p.max_requests != 1 else ''}"]
    if fetch:
        parts[0] += f" ({', '.join(fetch)})"
    parts.append(f"{p.from_cache} from cache")
    if any(d.known_404 for d in p.docs):
        parts.append("companyfacts is a cached 404: the extraction fails on it, no request")
    if p.bytes_bound:
        parts.append(f"documents up to {_mb(p.bytes_bound)}")
    return f"{head}: " + " · ".join(parts)


def _print_plan(
    plans: Sequence[CompanyPlan],
    all_plans: Sequence[CompanyPlan],
    *,
    live: bool,
    out: Callable[[str], None],
) -> None:
    for p in plans:
        out(_plan_line(p, live=live))
    rate = get_settings().sec_global_rate_per_sec
    for label, ps in (("this run", plans), ("every due company", all_plans)):
        if label == "every due company" and len(all_plans) == len(plans):
            continue
        t = PlanTotals.of(ps)
        paths = ", ".join(f"{n} {k}" for k, n in sorted(t.by_path.items())) or "none"
        if live:
            out(
                f"{NAME}: {label}: {t.companies:,} companies ({paths}) · {t.live:,} SEC requests known, up to "
                f"{t.max_requests:,} with what only a fetch can settle · {t.from_cache:,} reads from cache · at "
                f"least {t.live / rate:,.0f}s to {t.max_requests / rate:,.0f}s at {rate:g} requests/s (the one "
                "global SEC budget, shared with the Workbench)"
            )
            out(
                f"{NAME}: {label}: bytes: documents up to {_mb(t.bytes_bound)} (the index's filing sizes, an "
                f"upper bound) · index and companyfacts refreshes about {_mb(t.refresh_bytes)} (their cached "
                f"copies) · {t.unknown_size:,} live reads of unknown size"
            )
        else:
            out(
                f"{NAME}: {label}: {t.companies:,} companies ({paths}) · cache only: {t.offline_ok:,} fully "
                f"cached (would extract) · {t.offline_missing:,} not cached (error attempts 'not cached', no "
                f"request) · {t.no_cik:,} with no CIK (skipped) · 0 SEC requests"
            )
    out(f"{NAME}: CANNOT KNOW without fetching: " + "; ".join(CANNOT_KNOW) + ".")


# --- the B4 line ---------------------------------------------------------------------------------------------------


def _b4_before(applied: bool) -> str:
    if applied:
        return (
            f"{NAME}: B4: a backfill has already extracted at least one company (a 'done' backfill attempt exists): "
            "the nightly and the onboarding job already take never-extracted companies."
        )
    return (
        f"{NAME}: B4: no backfill has extracted a company yet (no 'done' backfill attempt), so the nightly and the "
        "onboarding job take NO never-extracted company. The first --apply that extracts at least one company "
        "(applied, a candidate recorded, or nothing found) unlocks them, for good; failed, busy and skipped attempts "
        "do not."
    )


def _b4_after(before: bool, after: bool, *, live: bool) -> str:
    if after and not before:
        mode = (
            ""
            if live
            else " (a cache-only run counts: on a fork this is expected, and a re-seed resets it)"
        )
        return (
            f"{NAME}: B4: this run extracted the FIRST companies of any backfill{mode}: from now on the nightly and "
            "the onboarding job also take never-extracted companies."
        )
    if after:
        return f"{NAME}: B4: already unlocked before this run (never-extracted companies were already open)."
    return (
        f"{NAME}: B4: nothing was extracted (no backfill attempt is 'done': every one failed, was busy or was "
        "skipped), so the nightly is still holding never-extracted companies, and so is the onboarding job, until "
        "a backfill extracts at least one company."
    )


# --- the run -------------------------------------------------------------------------------------------------------


def _window_now(now: datetime) -> tuple[datetime, datetime] | None:
    s = get_settings()
    return window_bounds(
        now.astimezone(market_time.market_tz()),
        run_at=parse_run_at(s.cron_run_at),
        before_min=s.onboard_window_before_min,
        after_min=s.onboard_window_after_min,
    )


def _stop_reason(lock: JobLock, now: datetime) -> str | None:
    w = _window_now(now)
    if w is not None:
        return f"entered the nightly window ({w[0]:%H:%M}-{w[1]:%H:%M})"
    holder = lock.call_of_record_holder()
    if holder is not None:
        return f"a pass holds the call-of-record lock ({holder}); the backfill yields to it"
    return None


def _scope(
    conn: psycopg.Connection, items: Sequence[WorkItem], tickers: Sequence[str]
) -> tuple[list[WorkItem], list[str], list[str]]:
    """``(items in scope, unknown tickers, known but not due)``. A ticker is known when a company on a live card,
    extracted at all (``extractable``), carries it (case-insensitive)."""
    want = {t.strip().upper() for t in tickers if t.strip()}
    known = {(c["ticker"] or "").upper() for c in repo.on_card_companies(conn) if extractable(c)}
    unknown = sorted(want - known)
    scoped = [i for i in items if (i.ticker or "").upper() in want]
    not_due = sorted((want & known) - {(i.ticker or "").upper() for i in scoped})
    return scoped, unknown, not_due


def _db_now(conn: psycopg.Connection) -> datetime:
    with conn.cursor() as cur:
        cur.execute("SELECT now() AS t")
        return cur.fetchone()["t"]


def _errors_since(conn: psycopg.Connection, since: datetime) -> dict[str, int]:
    """This run's failed attempts by reason (one backfill at a time: lock 7)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT error, count(*) AS n FROM platform_extraction_attempt "
            "WHERE tenant_id = %s AND run_kind = %s AND status = 'error' AND recorded_at >= %s "
            "GROUP BY error ORDER BY count(*) DESC, error",
            (DATA_TENANT_ID, RUN_KIND, since),
        )
        return {r["error"]: r["n"] for r in cur.fetchall()}


def _print_summary(
    r: ExtractionRun, errors: Mapping[str, int], stop: str | None, out: Callable[[str], None]
) -> None:
    c = r.companies
    reasons = ", ".join(f"{why} {n}" for why, n in errors.items())
    out(f"{NAME}: {r.admin_line()}")
    out(
        f"{NAME}: attempted {r.attempted:,} of {r.due_total:,} due · applied {c['applied']:,} · candidates "
        f"{c['to_steward'] + c['nothing_found']:,} ({c['to_steward']:,} flagged to the steward, "
        f"{c['nothing_found']:,} nothing found) · unchanged {c['unchanged']:,} · skipped {r.skipped:,} · busy "
        f"{r.busy:,} · failed {r.failed:,}" + (f" ({reasons})" if reasons else "") + f" · deferred "
        f"{r.deferred:,}"
        + (f" ({r.stopped_by})" if r.stopped_by else "")
        + f" · {r.live_fetches:,} live "
        "SEC fetches"
    )
    for label in r.failed_labels:
        out(f"  failed: {label}")
    for ft, counts in r.outcomes.items():
        if counts:
            per = ", ".join(f"{k} {n}" for k, n in sorted(counts.items()))
            out(f"  {ft}: {per}")
    if stop:
        out(
            f"{NAME}: STOPPED: {stop}. The companies already extracted are kept; the {r.deferred:,} left stay due "
            f"(re-run to resume; exit {EXIT_ALREADY_RUNNING})"
        )


def run(
    *,
    apply: bool,
    live: bool = True,
    tickers: Sequence[str] = (),
    max_names: int | None = None,
    budget_s: float | None = None,
    cache_dir: Path | None = None,
    runs_dir: Path | None = None,
    client: Any = None,
    now: Callable[[], datetime] = _now_utc,
    connect_fn: Callable[[], psycopg.Connection] = connect,
    out: Callable[[str], None] = print,
) -> int:
    """ONE invocation (the module docstring); returns the exit code. ``cache_dir`` defaults to the client's EDGAR
    cache; ``client`` (tests) replaces the recurring ``EdgarClient`` an applied run builds; a dry run never uses
    either beyond reading ``cache_dir`` from disk."""
    cache = (
        Path(cache_dir)
        if cache_dir is not None
        else getattr(client, "cache_dir", None) or _DEFAULT_CACHE
    )
    mode = "live" if live else "cache only"
    lock: JobLock | None = None
    if apply:
        w = _window_now(now())
        if w is not None:
            out(
                f"{NAME}: REFUSED: inside the nightly window ({w[0]:%H:%M}-{w[1]:%H:%M}); nothing fetched, nothing "
                f"written (exit {EXIT_ALREADY_RUNNING})"
            )
            return EXIT_ALREADY_RUNNING
        try:
            lock = acquire_job_lock(
                lock_id=LOCK_ID_EXTRACTION, holder=NAME, what="platform-value backfill"
            )
        except JobAlreadyRunning as e:
            out(f"{NAME}: REFUSED: {e}; nothing written (exit {EXIT_ALREADY_RUNNING})")
            return EXIT_ALREADY_RUNNING
        holder = lock.call_of_record_holder()
        if holder is not None:
            lock.release()
            out(
                f"{NAME}: REFUSED: a pass holds the call-of-record lock ({holder}); the backfill yields to it, "
                f"nothing written (exit {EXIT_ALREADY_RUNNING})"
            )
            return EXIT_ALREADY_RUNNING
        if client is None and live and not get_settings().user_agent:
            lock.release()
            out(
                f"{NAME}: REFUSED: set ALPHADECK_USER_AGENT (SEC requires a declared User-Agent with contact), or "
                f"run cache-only with --no-live; nothing fetched, nothing written (exit {EXIT_FAILED})"
            )
            return EXIT_FAILED
    try:
        return _run(
            apply=apply,
            live=live,
            mode=mode,
            tickers=tickers,
            max_names=max_names,
            budget_s=budget_s,
            cache=cache,
            runs_dir=runs_dir,
            client=client,
            now=now,
            connect_fn=connect_fn,
            lock=lock,
            out=out,
        )
    finally:
        if lock is not None:
            lock.release()


def _run(
    *,
    apply: bool,
    live: bool,
    mode: str,
    tickers: Sequence[str],
    max_names: int | None,
    budget_s: float | None,
    cache: Path,
    runs_dir: Path | None,
    client: Any,
    now: Callable[[], datetime],
    connect_fn: Callable[[], psycopg.Connection],
    lock: JobLock | None,
    out: Callable[[str], None],
) -> int:
    conn = connect_fn()
    try:
        if not apply:
            conn.read_only = True  # structurally a look: the dry run cannot write a row
        at = now()
        wl = work_list(conn, now=at, include_never=True)
        items = list(wl.items)
        if tickers:
            items, unknown, not_due = _scope(conn, items, tickers)
            if unknown:
                conn.rollback()
                out(
                    f"{NAME}: REFUSED: unknown ticker(s) {', '.join(unknown)} (not a company on a live card, or an "
                    f"ETF, which is not extracted); nothing fetched, nothing written (exit {EXIT_USAGE})"
                )
                return EXIT_USAGE
            for t in not_due:
                out(f"{NAME}: {t} is not due (up to date, held for a new filing, or waiting)")
        applied_before = backfill_applied(conn)
        started_db = _db_now(conn)
        conn.rollback()  # end the reads' transaction: never idle in one while SEC answers
        by_reason: dict[str, int] = {}
        for i in items:
            by_reason[i.reason] = by_reason.get(i.reason, 0) + 1
        reasons = ", ".join(f"{k} {n}" for k, n in sorted(by_reason.items())) or "none"
        left_out = f"held {wl.held}, waiting {wl.waiting}"
        this_run = items[:max_names] if max_names is not None else items
        if not apply:
            out(
                f"{NAME}: DRY RUN · {mode} · {len(items):,} due ({reasons}; not due: {left_out})"
                + (
                    f" · --max-names {max_names}: this run takes the first {len(this_run):,}"
                    if max_names
                    else ""
                )
                + (f" · --budget-s {budget_s:g} may stop it sooner" if budget_s else "")
                + f" · cache {cache}"
            )
            all_plans = plan_work(items, cache_dir=cache, now=at)
            _print_plan(all_plans[: len(this_run)], all_plans, live=live, out=out)
            out(_b4_before(applied_before))
            out(
                f"{NAME}: DRY RUN: no SEC request, nothing written (re-run with --apply to extract"
                + (", --no-live for cache only" if live else "")
                + ")"
            )
            return 0
        if not items:
            out(
                f"{NAME}: APPLY · {mode} · nothing due (not due: {left_out}); nothing fetched, nothing written"
            )
            out(_b4_before(applied_before))
            return 0
        assert lock is not None
        if client is None:
            client = EdgarClient(
                allow_live=live, cache_ttl_s=RECURRING_CACHE_TTL_S, cache_dir=cache
            )
        out(
            f"{NAME}: APPLY · {mode} · {len(items):,} due ({reasons}) · run kind {RUN_KIND}"
            + (f" · --max-names {max_names}" if max_names is not None else "")
            + (f" · --budget-s {budget_s:g}" if budget_s is not None else "")
        )
        stop: list[str] = []

        def should_stop() -> bool:
            reason = _stop_reason(lock, now())
            if reason is not None:
                stop.append(reason)
                return True
            return False

        def writer() -> psycopg.Connection:
            c = connect_fn()
            name_session(c, f"alphadeck-writer:{NAME}")  # how a waiting writer names this one (W1)
            return c

        result = run_extraction(
            writer,
            items,
            client=client,
            run_kind=RUN_KIND,
            should_stop=should_stop,
            max_names=max_names,
            budget_s=budget_s,
        )
        conn.rollback()
        errors = _errors_since(conn, started_db)
        applied_after = backfill_applied(conn)
        conn.rollback()
        detail = stop[0] if stop else None
        payload = result.to_json()
        payload.update(
            {
                "mode": "live" if live else "cache_only",
                "tickers": sorted({t.strip().upper() for t in tickers if t.strip()}),
                "errors_by_reason": errors,
                "stop_detail": detail,
                "backfill_applied_before": applied_before,
                "backfill_applied_after": applied_after,
                "not_due": {"held": wl.held, "waiting": wl.waiting},
            }
        )
        path = write_extraction_run(payload, base_dir=runs_dir)
        _print_summary(result, errors, detail, out)
        out(
            f"{NAME}: run artifact {path}" if path else f"{NAME}: run artifact NOT written (logged)"
        )
        out(_b4_after(applied_before, applied_after, live=live))
        if result.status == "error":
            return EXIT_FAILED
        if detail is not None:
            return EXIT_ALREADY_RUNNING
        return 0
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="The platform-value backfill: extract every due company's share count and cash/burn "
        "(dry-run by default: the SEC cost plan, no request, no write)."
    )
    p.add_argument(
        "--apply",
        action="store_true",
        help="extract and write (attempts, values, candidates) + a run artifact; without it: the dry run",
    )
    p.add_argument(
        "--no-live",
        action="store_true",
        help="cache only: never reach SEC; a company whose documents are not all cached gets an error attempt "
        "'not cached' (the fork rehearsal)",
    )
    p.add_argument(
        "--max-names",
        "--limit",
        dest="max_names",
        type=int,
        default=None,
        help="companies this run at most (default: every due company)",
    )
    p.add_argument(
        "--budget-s",
        type=float,
        default=None,
        help="wall-time budget in seconds, checked between companies (default: none)",
    )
    p.add_argument(
        "--ticker",
        action="append",
        default=[],
        help="scope to these companies (repeatable or comma-separated); an unknown ticker refuses the run",
    )
    a = p.parse_args(argv)
    if a.max_names is not None and a.max_names < 1:
        p.error("--max-names must be at least 1")
    if a.budget_s is not None and a.budget_s <= 0:
        p.error("--budget-s must be positive")
    tickers = [t for arg in a.ticker for t in arg.split(",") if t.strip()]
    return run(
        apply=a.apply,
        live=not a.no_live,
        tickers=tickers,
        max_names=a.max_names,
        budget_s=a.budget_s,
    )


__all__ = [
    "CANNOT_KNOW",
    "CompanyPlan",
    "DocPlan",
    "PlanTotals",
    "exhibit_bound",
    "main",
    "plan_company",
    "plan_work",
    "run",
]


if __name__ == "__main__":
    sys.exit(main())
