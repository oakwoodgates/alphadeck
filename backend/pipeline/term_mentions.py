"""THE TERM-MENTIONS COUNT — how many EDGAR filings mention each card's SIGNAL terms, per month. DRY RUN by default.

The data the Narrative Adoption Curve dashboard reads (``docs/dashboards/narrative-adoption.md`` §The job): for
every live card and every SIGNAL term in its persisted term set (``thesis.term_set``, ``tier = signal`` — the
operator's seeds; BROAD terms are collision-prone and never counted), ONE EDGAR full-text search per calendar
month, stored as ``fact_term_mentions`` (migration 0071): the documents that matched, split by who files them
(reports / events / issuance / other), the distinct filer CIKs seen and how many of them no earlier stored month
had. DISPLAY ONLY: it never feeds a call, never places a name (discovery is untouched), never calls the LLM.

    # DRY RUN (the default): the plan — what each mode would fetch, the requests the backfill needs NOW and the
    # steady-state weekly count after it. No SEC request, nothing written.
    python -m pipeline.term_mentions --backfill
    python -m pipeline.term_mentions --weekly
    # APPLY: fetch + write (store-on-diff) + a run artifact under data/term_mentions/
    python -m pipeline.term_mentions --backfill --apply          # every window month not yet stored (resumable)
    python -m pipeline.term_mentions --weekly --apply            # the same missing-month fill + re-fetch the
                                                                 # current month and the one before
    python -m pipeline.term_mentions --backfill --apply --thesis <id> --months 12 --no-live

THE RULES, each the plan's (``docs/dashboards/narrative-adoption.md``) or named there under "Choices to confirm":

- **One request per (term, month), across cards.** Cards that share a term (compared case-insensitively) share
  its request; the parsed reply is fanned out to every card that needs that month and stored under each card's
  own spelling. Through ``EdgarClient`` on the RECURRING lane (``RECURRING_CACHE_TTL_S``): the declared
  User-Agent, the global SEC budget, the 429 pause — all inherited. A term's months are fetched concurrently
  with a small pool (the budget bounds the rate), then computed in ascending month order.
- **Honest counts.** ``documents`` is ``hits.total`` when exact, else (a capped "gte" total) the form
  aggregation's total, which MEASURED equal to the exact total on all 126 cached exact replies. The four form
  groups always sum to ``documents``. ``filers`` is exact only when the reply shows every filer (the page holds
  every document, or the entity aggregation lists every filer with its CIK); else ``filers_sampled`` ("at least").
- **A failure is never a zero.** A failed request (after the client's retries, an error reply included) leaves
  the month unstored (or its older version standing) and is LISTED in the run report and the artifact. When a
  month that is NOT stored fails, that card-term's LATER months are deferred this run, so ``new_filers`` is never
  computed across a gap.
- **Store-on-diff.** A re-fetched month appends a new version only when a stored value changed; a re-run appends
  nothing (idempotency is proven by counting the table). A stored month is never re-fetched on a backfill; the
  weekly re-fetches exactly two months per term. ``new_filers`` is derived from the stored CIK sets, so when an
  earlier month is filled after a later one was stored, the later month's count is re-derived (no request).
- **Never the first backfill by itself.** ``--weekly`` with an EMPTY table for the tenant is a no-op line: the
  first backfill (~an hour of SEC requests) is the operator's call — cost is the operator's to spend.

THE WEEKLY JOB. The cron sidecar fires ``--weekly --apply --run-kind cron`` every Saturday at 10:00 market time
(``term_mentions_loop`` in ``scripts/daily_cron.sh``), plus once at boot with ``--if-stale-days 7``. An
``--apply`` run REFUSES (exit 75, one line, nothing written) inside the nightly window, while another run holds
its lock (advisory id 6), or while a pass holds the call-of-record lock (a ``pg_locks`` probe — never taken); it
re-checks the window and the probe between terms and STOPS cleanly (exit 75, the rest listed deferred; the terms
already written are kept). It never takes the fact-writer gate and writes no ``data_repair`` journal row: this is
ordinary display data flow, not a repair (a journal row would start a Scoreboard editions repair trial).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import urllib.parse
from collections.abc import Callable, Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from uuid import UUID

import psycopg

from db.bitemporal import append_fact
from db.session import connect, current_tenant_id
from domain.market_time import market_tz
from domain.settings import get_settings
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from ingest.edgar.fulltext import _EFTS_URL, _check_efts_page
from pipeline.onboarding import window_bounds
from pipeline.run_lock import (
    EXIT_ALREADY_RUNNING,
    LOCK_ID_TERM_MENTIONS,
    JobAlreadyRunning,
    JobLock,
    acquire_job_lock,
)
from pipeline.schedule import parse_run_at

PASS_KIND = "term_mentions"
MODE_APPLY = "apply"
TABLE = "fact_term_mentions"
BACKFILL = "backfill"
WEEKLY = "weekly"
DEFAULT_MONTHS = 36
# EFTS returns 100 hits per page (MEASURED: every cached reply's ``query.size`` and page length) — the "page holds
# every document" rule for an exact filer count reads it
PAGE_SIZE = 100
# A term's months are fetched by a small pool: the client's per-client cap and the global SEC budget bound the
# rate, so more threads would only queue
FETCH_WORKERS = 4
# Runtime artifacts beside the other jobs' (the repo's gitignored data/ == the container's /data); tests redirect.
DEFAULT_RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "term_mentions"

_STATUS_FINISHED = "finished"
_STATUS_STOPPED = "stopped"
_STATUS_FAILED = "failed"
_EXIT_FAILED = 1

# A display string's embedded CIK (``"NAME  (TKR)  (CIK 0001234567)"``) — the entity aggregation's keys carry it
_EMBED_CIK_RE = re.compile(r"\(CIK\s*(\d{10})\)")


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# --- months -------------------------------------------------------------------------------------------------


def month_last_day(month: date) -> date:
    """The last calendar day of ``month`` (any day in it)."""
    nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return nxt - timedelta(days=1)


def month_window(today: date, months: int) -> list[date]:
    """The ``months`` calendar months ending with ``today``'s month, each as its first day, ascending. PURE."""
    if months < 1:
        raise ValueError(f"the window needs at least one month (got {months})")
    out: list[date] = []
    y, m = today.year, today.month
    for _ in range(months):
        out.append(date(y, m, 1))
        y, m = (y - 1, 12) if m == 1 else (y, m - 1)
    return out[::-1]


def month_ranges(months: Iterable[date]) -> str:
    """``2023-11..2026-10, 2026-12`` — consecutive months collapsed into ranges, for the plan and the artifact."""
    ms = sorted(set(months))
    parts: list[str] = []
    i = 0
    while i < len(ms):
        j = i
        while j + 1 < len(ms) and month_window(ms[j + 1], 2)[0] == ms[j]:
            j += 1
        parts.append(f"{ms[i]:%Y-%m}" if i == j else f"{ms[i]:%Y-%m}..{ms[j]:%Y-%m}")
        i = j + 1
    return ", ".join(parts)


# --- the request ------------------------------------------------------------------------------------------


def month_url(term: str, month: date) -> str:
    """The EFTS phrase search for ``term`` over ``month`` (``dateRange=custom`` on the filing date)."""
    q = urllib.parse.quote(f'"{term}"')
    return (
        f"{_EFTS_URL}?q={q}&dateRange=custom&startdt={month:%Y-%m-%d}"
        f"&enddt={month_last_day(month):%Y-%m-%d}"
    )


def month_cache_key(term: str, month: date) -> str:
    """``efts_month/<slug>_<hash>_<YYYY-MM>.json`` — its own prefix (never the discovery walk's ``efts/``). The
    hash of the case-folded term makes the key unique per term: two terms whose slugs collide (``"a.b"`` /
    ``"a b"``) must never read each other's reply."""
    folded = term.casefold()
    slug = re.sub(r"[^a-z0-9_-]", "_", folded)[:60]
    digest = hashlib.sha1(folded.encode("utf-8")).hexdigest()[:10]
    return f"efts_month/{slug}_{digest}_{month:%Y-%m}.json"


# --- the parse (pure) -------------------------------------------------------------------------------------

GROUPS = ("reports", "events", "issuance")
_REPORT_FORMS = frozenset({"10-K", "10-Q", "20-F", "40-F", "6-K", "10-KT", "10-QT"})
_EVENT_FORMS = frozenset({"8-K"})
_ISSUANCE_FORMS = frozenset(
    f"{base}{variant}"
    for base in ("S-1", "S-3", "F-1", "F-3", "S-11")
    for variant in ("", "MEF", "ASR")
) | {"FWP"}


def form_group(form: str) -> str:
    """The form group of one form type: ``reports`` (10-K / 10-Q / 20-F / 40-F / 6-K, 10-KT / 10-QT), ``events``
    (8-K), ``issuance`` (S-1 / S-3 / F-1 / F-3 / S-11 and their MEF / ASR variants, every 424B*, FWP) or
    ``other``. An amendment (``/A``) sits with its form. (EFTS's ``form_filter`` aggregates on ``root_forms``,
    which already rolls an amendment into its root — MEASURED: no ``/A`` key in any cached reply.)
    """
    f = " ".join(form.split()).upper()
    root = f[:-2] if f.endswith("/A") else f
    if root in _REPORT_FORMS:
        return "reports"
    if root in _EVENT_FORMS:
        return "events"
    if root in _ISSUANCE_FORMS or root.startswith("424B"):
        return "issuance"
    return "other"


class MonthParseError(ValueError):
    """A valid EFTS page whose counts this job cannot state honestly (a capped total with no aggregation to count
    from, named groups above the total, a malformed bucket). The month FAILS visibly — never a clamped row.
    """


@dataclass(frozen=True)
class MonthCount:
    """One term-month as parsed from one EFTS reply (before ``new_filers``, which needs the stored history)."""

    documents: int
    reports: int
    events: int
    issuance: int
    other: int
    filer_ciks: tuple[str, ...]  # sorted, distinct
    filers_sampled: bool

    @property
    def filers(self) -> int:
        return len(self.filer_ciks)


def _int(v: Any, what: str) -> int:
    if not isinstance(v, int) or isinstance(v, bool) or v < 0:
        raise MonthParseError(f"{what} is not a non-negative int ({v!r:.40})")
    return v


def _buckets(agg: Any, name: str) -> tuple[list[tuple[str, int]], int]:
    """An aggregation's ``(key, doc_count)`` buckets + its ``sum_other_doc_count``, validated."""
    if not isinstance(agg, dict) or not isinstance(agg.get("buckets"), list):
        raise MonthParseError(f"the {name} aggregation has no bucket list")
    out: list[tuple[str, int]] = []
    for b in agg["buckets"]:
        if not isinstance(b, dict) or not isinstance(b.get("key"), str):
            raise MonthParseError(f"a {name} bucket has no key ({b!r:.60})")
        out.append((b["key"], _int(b.get("doc_count"), f"{name} bucket {b['key']!r} doc_count")))
    return out, _int(agg.get("sum_other_doc_count", 0), f"{name} sum_other_doc_count")


def parse_month(doc: Any, *, page_size: int = PAGE_SIZE) -> MonthCount:
    """One EFTS reply -> its ``MonthCount``. PURE. Raises ``EftsErrorBody`` (not a search-results page) or
    ``MonthParseError`` (counts that cannot be stated honestly).

    - ``documents``: ``hits.total.value`` when its relation is ``eq``; when ``gte`` (EFTS caps the total at
      10,000) the ``form_filter`` aggregation's total (its buckets + ``sum_other_doc_count``), which is not capped
      — never below the reported floor.
    - groups: ``form_filter``'s buckets by ``form_group``; ``other`` = documents minus the three named groups, so
      ``sum_other_doc_count`` lands in other and the four sum to documents. Named groups above documents fail.
    - ``filer_ciks``: the first page's ``_source.ciks`` UNION the CIKs embedded in ``entity_filter``'s keys.
      Exact (``filers_sampled = False``) when the page holds every document (``documents <= len(page)``,
      ``page_size`` at most) OR the entity aggregation lists every filer (``sum_other_doc_count == 0``) and every
      bucket key carries a CIK; else "at least".
    """
    _check_efts_page(doc)
    hits = doc["hits"]
    total = hits["total"]
    value = _int(total["value"], "hits.total.value")
    relation = total.get("relation", "eq")
    aggs = doc.get("aggregations")
    aggs = aggs if isinstance(aggs, dict) else {}
    forms = aggs.get("form_filter")
    form_buckets: list[tuple[str, int]] = []
    form_other = 0
    if forms is not None:
        form_buckets, form_other = _buckets(forms, "form_filter")
    if relation == "eq":
        documents = value
    elif relation == "gte":
        if forms is None:
            raise MonthParseError(
                f"a capped total (gte {value}) and no form_filter aggregation to count from"
            )
        documents = sum(n for _, n in form_buckets) + form_other
        if documents < value:
            raise MonthParseError(
                f"the form aggregation's total ({documents}) is below the capped floor (gte {value})"
            )
    else:
        raise MonthParseError(f"unknown hits.total.relation {relation!r}")
    groups = dict.fromkeys(GROUPS, 0)
    if forms is None:
        if documents:
            raise MonthParseError(
                f"{documents} documents and no form_filter aggregation to group them"
            )
    else:
        for key, n in form_buckets:
            g = form_group(key)
            if g != "other":
                groups[g] += n
    named = sum(groups.values())
    if named > documents:
        raise MonthParseError(f"the named form groups ({named}) exceed the documents ({documents})")
    page = hits["hits"][:page_size]
    ciks: set[str] = set()
    for h in page:
        src = h.get("_source") if isinstance(h, dict) else None
        for c in (src or {}).get("ciks") or []:
            ciks.add(str(c))
    entities_complete = False
    entities = aggs.get("entity_filter")
    if entities is not None:
        entity_buckets, entity_other = _buckets(entities, "entity_filter")
        every_key_has_cik = True
        for key, _ in entity_buckets:
            found = _EMBED_CIK_RE.findall(key)
            every_key_has_cik = every_key_has_cik and bool(found)
            ciks.update(found)
        entities_complete = entity_other == 0 and every_key_has_cik
    exact = documents <= len(page) or entities_complete
    return MonthCount(
        documents=documents,
        reports=groups["reports"],
        events=groups["events"],
        issuance=groups["issuance"],
        other=documents - named,
        filer_ciks=tuple(sorted(ciks)),
        filers_sampled=not exact,
    )


# --- the cards and the stored history ---------------------------------------------------------------------


@dataclass(frozen=True)
class Card:
    thesis_id: UUID
    name: str
    terms: tuple[
        str, ...
    ]  # its SIGNAL terms, stripped, de-duplicated (case-insensitively), as written


def signal_terms(term_set: Any) -> tuple[str, ...]:
    """A card's SIGNAL terms from its stored ``term_set``: ``tier == "signal"`` only (BROAD is never counted),
    stripped, blanks dropped, de-duplicated case-insensitively (the first spelling kept). PURE."""
    out: list[str] = []
    seen: set[str] = set()
    for entry in term_set if isinstance(term_set, list) else []:
        if not isinstance(entry, dict) or entry.get("tier") != "signal":
            continue
        term = entry.get("term")
        if not isinstance(term, str) or not term.strip():
            continue
        term = term.strip()
        if term.casefold() in seen:
            continue
        seen.add(term.casefold())
        out.append(term)
    return tuple(out)


def load_cards(
    conn: psycopg.Connection, *, tenant_id: UUID, thesis_id: UUID | None = None
) -> list[Card]:
    """The live cards (``archived_at IS NULL``) of ``tenant_id`` — one, with ``thesis_id`` — by name."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, name, term_set FROM thesis WHERE tenant_id = %(t)s AND archived_at IS NULL"
            " AND (%(id)s::uuid IS NULL OR id = %(id)s::uuid) ORDER BY name, id",
            {"t": tenant_id, "id": thesis_id},
        )
        rows = cur.fetchall()
    return [Card(r["id"], r["name"], signal_terms(r["term_set"])) for r in rows]


_VALUE_COLS = (
    "documents",
    "reports",
    "events",
    "issuance",
    "other",
    "filers",
    "filers_sampled",
    "filer_ciks",
    "new_filers",
)


def load_stored(
    conn: psycopg.Connection, *, tenant_id: UUID, thesis_ids: Sequence[UUID]
) -> dict[tuple[UUID, str], dict[date, dict[str, Any]]]:
    """The NEWEST stored version of every (card, term, month) — the job's own bookkeeping (what is stored, what
    the store-on-diff compares against), not a history read: every month, in the window or not."""
    out: dict[tuple[UUID, str], dict[date, dict[str, Any]]] = {}
    if not thesis_ids:
        return out
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (thesis_id, term, month) thesis_id, term, month, documents, reports, events,"
            " issuance, other, filers, filers_sampled, filer_ciks, new_filers, source_ref, fetched_at"
            f" FROM {TABLE} WHERE tenant_id = %s AND thesis_id = ANY(%s)"
            " ORDER BY thesis_id, term, month, recorded_at DESC, id DESC",
            (tenant_id, list(thesis_ids)),
        )
        for r in cur.fetchall():
            out.setdefault((r["thesis_id"], r["term"]), {})[r["month"]] = dict(r)
    return out


def table_is_empty(conn: psycopg.Connection, *, tenant_id: UUID) -> bool:
    with conn.cursor() as cur:
        cur.execute(f"SELECT 1 FROM {TABLE} WHERE tenant_id = %s LIMIT 1", (tenant_id,))
        return cur.fetchone() is None


# --- the plan (pure) --------------------------------------------------------------------------------------


@dataclass
class Target:
    """One card-term: its stored months and the months this run fetches for it."""

    card: Card
    term: str  # as the card writes it (the stored spelling)
    stored: dict[date, dict[str, Any]]
    planned: list[date]


@dataclass
class Group:
    """One distinct term (case-insensitive) — ONE request per month, fanned out to its cards."""

    key: str  # the case-folded term
    query: str  # the spelling sent (the smallest of the cards' spellings: deterministic)
    targets: list[Target] = field(default_factory=list)

    @property
    def months(self) -> list[date]:
        return sorted({m for t in self.targets for m in t.planned})


def build_plan(
    cards: Sequence[Card],
    stored: Mapping[tuple[UUID, str], Mapping[date, dict[str, Any]]],
    *,
    window: Sequence[date],
    schedule: str,
) -> list[Group]:
    """Every distinct term with the months each card needs. ``backfill`` = the window months not stored for that
    card-term; ``weekly`` = the same PLUS the current month and the one before (a filing accepted late settles
    the prior month). PURE; groups sorted by their case-folded term."""
    refresh = set(window[-2:]) if schedule == WEEKLY else set()
    groups: dict[str, Group] = {}
    for card in cards:
        for term in card.terms:
            have = dict(stored.get((card.thesis_id, term), {}))
            planned = sorted((set(window) - set(have)) | refresh)
            g = groups.setdefault(term.casefold(), Group(key=term.casefold(), query=term))
            g.query = min(g.query, term)
            g.targets.append(Target(card=card, term=term, stored=have, planned=planned))
    return [groups[k] for k in sorted(groups)]


def request_count(groups: Iterable[Group]) -> int:
    return sum(len(g.months) for g in groups)


# --- one card-term's rows (pure) --------------------------------------------------------------------------


@dataclass
class Fetched:
    """One term-month request's outcome: the parse, or the error (never both)."""

    count: MonthCount | None
    error: str | None
    url: str
    fetched_at: datetime
    # answered from the disk cache (no completed network fetch on this request)
    cached: bool = False


@dataclass
class TargetOutcome:
    rows: list[dict[str, Any]] = field(default_factory=list)  # to append
    unchanged: int = 0
    rederived: int = 0  # stored months whose new_filers was re-derived (no request; in ``rows``)
    deferred: list[date] = field(default_factory=list)
    failed: list[date] = field(default_factory=list)


def _same(stored: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    return all(
        (
            sorted(stored[c] or []) == sorted(row[c] or [])
            if c == "filer_ciks"
            else stored[c] == row[c]
        )
        for c in _VALUE_COLS
    )


def target_rows(
    target: Target, results: Mapping[date, Fetched], *, tenant_id: UUID
) -> TargetOutcome:
    """The rows one card-term appends this run. PURE over its stored months and this run's replies.

    Walks every month the card-term has (stored or planned) in ascending order, carrying the union of the
    EARLIER months' filer CIKs (``None`` until a first month is seen, so the first month's ``new_filers`` is NULL):

    - a planned month that came back: its row (store-on-diff: identical to the stored version -> no row);
    - a planned month that FAILED: listed; if it is stored, its older version stands and the walk goes on; if it
      is NOT stored, every later planned month that came back is DEFERRED (held back, unwritten) and the walk
      stops (no count across a gap);
    - a stored month not fetched this run: its ``new_filers`` re-derived from the stored CIK sets — a new version
      only when it changed (an earlier month filled after it was stored).
    """
    out = TargetOutcome()
    planned = set(target.planned)
    months = sorted(set(target.stored) | planned)
    seen: set[str] | None = None
    for i, m in enumerate(months):
        st = target.stored.get(m)
        if m in planned:
            res = results[m]
            if res.count is None:
                out.failed.append(m)
                if st is None:
                    # held back: the later months that DID come back (a later month that failed is listed
                    # among the failed requests already, never twice)
                    out.deferred = [
                        x for x in months[i + 1 :] if x in planned and results[x].count is not None
                    ]
                    break
                seen = (seen or set()) | set(st["filer_ciks"] or [])
                continue
            c = res.count
            row = {
                "tenant_id": tenant_id,
                "thesis_id": target.card.thesis_id,
                "term": target.term,
                "month": m,
                "documents": c.documents,
                "reports": c.reports,
                "events": c.events,
                "issuance": c.issuance,
                "other": c.other,
                "filers": c.filers,
                "filers_sampled": c.filers_sampled,
                "filer_ciks": list(c.filer_ciks),
                "new_filers": None if seen is None else len(set(c.filer_ciks) - seen),
                "source_ref": res.url,
                "fetched_at": res.fetched_at,
                "valid_from": month_last_day(m),
            }
            if st is not None and _same(st, row):
                out.unchanged += 1
            else:
                out.rows.append(row)
            seen = (seen or set()) | set(c.filer_ciks)
        else:
            ciks = set(st["filer_ciks"] or [])
            new = None if seen is None else len(ciks - seen)
            if new != st["new_filers"]:
                out.rows.append(
                    {
                        "tenant_id": tenant_id,
                        "thesis_id": target.card.thesis_id,
                        "term": target.term,
                        "month": m,
                        **{c: st[c] for c in _VALUE_COLS if c != "new_filers"},
                        "new_filers": new,
                        "source_ref": st["source_ref"],
                        "fetched_at": st["fetched_at"],
                        "valid_from": month_last_day(m),
                    }
                )
                out.rederived += 1
            seen = (seen or set()) | ciks
    return out


# --- fetching -----------------------------------------------------------------------------------------------


class _Client(Protocol):
    def get_json(
        self, url: str, cache_key: str, *, validate: Callable[[Any], None] | None = None
    ) -> dict[str, Any]: ...


def _fetched_at(client: Any, cache_key: str, fallback: datetime) -> datetime:
    """When SEC served the body: the cache file's write time (a live fetch writes it now; a cache hit read an
    earlier reply), else ``fallback``."""
    try:
        mtime = (Path(client.cache_dir) / cache_key).stat().st_mtime
    except (AttributeError, OSError, TypeError):
        return fallback
    return datetime.fromtimestamp(mtime, timezone.utc)


def fetch_group(
    client: _Client,
    group: Group,
    *,
    now: Callable[[], datetime] = _now_utc,
    workers: int = FETCH_WORKERS,
) -> dict[date, Fetched]:
    """ONE request per month of ``group`` (concurrently, a small pool). Never raises for a month: its error is
    returned in its ``Fetched`` (the client's retries already spent), so one bad month never costs the others.
    """

    def completed_here() -> int:
        # the client's completed network fetches made by THIS worker thread (``EdgarClient.thread_completed``)
        fn = getattr(client, "thread_completed", None)
        return fn() if callable(fn) else 0

    def one(m: date) -> tuple[date, Fetched]:
        url, key = month_url(group.query, m), month_cache_key(group.query, m)
        before = completed_here()
        try:
            doc = client.get_json(url, key, validate=_check_efts_page)
            count = parse_month(doc)
        except Exception as e:  # noqa: BLE001 — one month's failure, listed; never a zero row
            return m, Fetched(None, f"{type(e).__name__}: {e}", url, now())
        cached = completed_here() == before
        return m, Fetched(count, None, url, _fetched_at(client, key, now()), cached)

    months = group.months
    if not months:
        return {}
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(months)))) as pool:
        return dict(pool.map(one, months))


# --- the run ------------------------------------------------------------------------------------------------


def latest_run(*, runs_dir: Path | None = None) -> dict[str, Any] | None:
    """The newest parseable APPLIED run artifact, or ``None``. Fail-open per file; no DB, no network."""
    d = runs_dir or DEFAULT_RUNS_DIR
    if not d.exists():
        return None
    for p in sorted(d.glob("*.json"), reverse=True):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — skip-unreadable, never a failed read
            continue
        if isinstance(doc, dict) and doc.get("kind") == PASS_KIND and doc.get("mode") == MODE_APPLY:
            return doc
    return None


def _write_artifact(doc: dict[str, Any], runs_dir: Path) -> Path | None:
    """Write one run artifact (``<started>.json``); fail-open — a write fault costs the record, never the run."""
    try:
        runs_dir.mkdir(parents=True, exist_ok=True)
        started = datetime.fromisoformat(doc["started_at"])
        path = runs_dir / f"{started.strftime('%Y%m%dT%H%M%SZ')}.json"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
        tmp.replace(path)
        return path
    except Exception as e:  # noqa: BLE001
        print(f"term-mentions: could not write the run artifact ({type(e).__name__}: {e})")
        return None


def _window_now(now: datetime) -> tuple[datetime, datetime] | None:
    s = get_settings()
    return window_bounds(
        now.astimezone(market_tz()),
        run_at=parse_run_at(s.cron_run_at),
        before_min=s.onboard_window_before_min,
        after_min=s.onboard_window_after_min,
    )


def _stop_reason(lock: JobLock | None, now: datetime) -> str | None:
    """Why an ``--apply`` run must stop before its next term: the nightly window, or a pass holding the
    call-of-record lock (a probe, never a take) — else ``None``."""
    if lock is None:
        return None
    w = _window_now(now)
    if w is not None:
        return f"inside the nightly window ({w[0]:%H:%M}-{w[1]:%H:%M})"
    holder = lock.call_of_record_holder()
    if holder is not None:
        return f"a pass holds the call-of-record lock ({holder}); the job yields to it"
    return None


def _plan_doc(groups: Sequence[Group]) -> dict[str, Any]:
    return {
        g.query: {
            "months": month_ranges(g.months),
            "requests": len(g.months),
            "cards": sorted({t.card.name for t in g.targets}),
        }
        for g in groups
        if g.months
    }


def run(
    *,
    schedule: str,
    apply: bool,
    live: bool = True,
    tenant_id: UUID,
    months: int = DEFAULT_MONTHS,
    thesis_id: UUID | None = None,
    run_kind: str = "manual",
    if_stale_days: float | None = None,
    runs_dir: Path | None = None,
    now: Callable[[], datetime] = _now_utc,
    client: _Client | None = None,
    workers: int = FETCH_WORKERS,
) -> int:
    """ONE run (see the module docstring); returns the process exit code — 0 done (failed months are listed, not
    fatal), 1 crashed (nothing after the failure written) or a bad ``--thesis``, 75 refused or stopped (the
    window, another run, a running pass). ``client`` defaults to a recurring-lane ``EdgarClient``; tests inject a
    fake."""
    runs_dir = runs_dir or DEFAULT_RUNS_DIR
    started = now()
    if if_stale_days is not None:
        last = latest_run(runs_dir=runs_dir)
        if last is None:
            print(
                "term-mentions: no applied run on record — the first backfill is the operator's call; "
                "nothing to catch up"
            )
            return 0
        try:
            ran = datetime.fromisoformat(str(last.get("finished_at") or last["started_at"]))
        except (KeyError, ValueError):
            ran = None
        if ran is not None and started - ran < timedelta(days=if_stale_days):
            print(
                f"term-mentions: ran {ran:%Y-%m-%d} (under {if_stale_days:g} days ago) — no catch-up"
            )
            return 0

    lock: JobLock | None = None
    if apply:
        w = _window_now(started)
        if w is not None:
            print(
                f"term-mentions: REFUSED — inside the nightly window ({w[0]:%H:%M}-{w[1]:%H:%M}); "
                f"nothing fetched, nothing written (exit {EXIT_ALREADY_RUNNING})"
            )
            return EXIT_ALREADY_RUNNING
        try:
            lock = acquire_job_lock(
                lock_id=LOCK_ID_TERM_MENTIONS, holder="term-mentions", what="term-mentions run"
            )
        except JobAlreadyRunning as e:
            print(f"term-mentions: REFUSED — {e}; nothing written (exit {EXIT_ALREADY_RUNNING})")
            return EXIT_ALREADY_RUNNING
        holder = lock.call_of_record_holder()
        if holder is not None:
            lock.release()
            print(
                f"term-mentions: REFUSED — a pass holds the call-of-record lock ({holder}); the job "
                f"yields to it, nothing written (exit {EXIT_ALREADY_RUNNING})"
            )
            return EXIT_ALREADY_RUNNING
    try:
        return _run(
            schedule=schedule,
            apply=apply,
            live=live,
            tenant_id=tenant_id,
            months=months,
            thesis_id=thesis_id,
            run_kind=run_kind,
            started=started,
            runs_dir=runs_dir,
            now=now,
            client=client,
            workers=workers,
            lock=lock,
        )
    finally:
        if lock is not None:
            lock.release()


def _run(
    *,
    schedule: str,
    apply: bool,
    live: bool,
    tenant_id: UUID,
    months: int,
    thesis_id: UUID | None,
    run_kind: str,
    started: datetime,
    runs_dir: Path,
    now: Callable[[], datetime],
    client: _Client | None,
    workers: int,
    lock: JobLock | None,
) -> int:
    today = started.astimezone(market_tz()).date()
    window = month_window(today, months)
    span = f"window {window[0]:%Y-%m}..{window[-1]:%Y-%m} ({months} months)"
    conn = connect()
    try:
        if not apply:
            conn.read_only = True  # structurally a look: the dry run cannot write a row
        cards = load_cards(conn, tenant_id=tenant_id, thesis_id=thesis_id)
        if thesis_id is not None and not cards:
            print(f"term-mentions: {thesis_id} is not a live card of tenant {tenant_id}")
            return _EXIT_FAILED
        stored = load_stored(conn, tenant_id=tenant_id, thesis_ids=[c.thesis_id for c in cards])
        empty = table_is_empty(conn, tenant_id=tenant_id)
        groups = build_plan(cards, stored, window=window, schedule=schedule)
        backfill_now = request_count(build_plan(cards, stored, window=window, schedule=BACKFILL))
        weekly_after = len(groups) * len(window[-2:])
        n_terms = sum(len(c.terms) for c in cards)
        head = (
            f"{len(cards)} cards · {n_terms} SIGNAL terms ({len(groups)} distinct)"
            if cards
            else "0 cards"
        )
        cost = (
            f"term-mentions: cost · the backfill needs {backfill_now:,} requests now · each weekly run "
            f"after it: {len(groups)} distinct terms × {len(window[-2:])} = {weekly_after:,} requests "
            "(+ any missing months: a term added since)"
        )
        if schedule == WEEKLY and empty:
            print(
                f"term-mentions: weekly · no backfill yet (the table is empty for tenant {tenant_id}) — the "
                "first backfill is the operator's call: `python -m pipeline.term_mentions --backfill "
                f"--apply` ({backfill_now:,} requests); nothing fetched, nothing written"
            )
            print(cost)
            return 0
        if not apply:
            conn.rollback()
            print(f"term-mentions: {schedule} DRY RUN · {span} · tenant {tenant_id}")
            for g in groups:
                if g.months:
                    names = ", ".join(sorted({t.card.name for t in g.targets}))
                    print(
                        f'  "{g.query}": {len(g.months)} months ({month_ranges(g.months)}) · {names}'
                    )
            print(
                f"term-mentions: {schedule} · {head} · {request_count(groups):,} requests planned "
                "(nothing fetched, nothing written)"
            )
            print(cost)
            print(
                "term-mentions: DRY RUN — no SEC request, nothing written (re-run with --apply to fetch "
                "and write)"
            )
            return 0

        if client is None:
            if live and not get_settings().user_agent:
                print(
                    "term-mentions: REFUSED — set ALPHADECK_USER_AGENT (SEC requires a declared "
                    "User-Agent with contact); nothing fetched, nothing written"
                )
                return _EXIT_FAILED
            client = EdgarClient(allow_live=live, cache_ttl_s=RECURRING_CACHE_TTL_S)
        conn.rollback()  # end the planning reads' transaction: never idle in one while SEC answers
        print(
            f"term-mentions: {schedule} APPLY · {span} · {'live' if live else 'cache only'} · "
            f"tenant {tenant_id} · {request_count(groups):,} requests planned"
        )
        return _execute(
            conn,
            groups,
            client=client,
            schedule=schedule,
            live=live,
            tenant_id=tenant_id,
            window=window,
            run_kind=run_kind,
            started=started,
            runs_dir=runs_dir,
            now=now,
            workers=workers,
            lock=lock,
            head=head,
            cost={"backfill_now": backfill_now, "weekly_after": weekly_after},
        )
    finally:
        conn.close()


def _execute(
    conn: psycopg.Connection,
    groups: Sequence[Group],
    *,
    client: _Client,
    schedule: str,
    live: bool,
    tenant_id: UUID,
    window: Sequence[date],
    run_kind: str,
    started: datetime,
    runs_dir: Path,
    now: Callable[[], datetime],
    workers: int,
    lock: JobLock | None,
    head: str,
    cost: dict[str, int],
) -> int:
    attempts0 = getattr(client, "live_fetches", 0)
    completed0 = getattr(client, "live_completed", 0)
    requests = ok = from_cache = appended = unchanged = rederived = 0
    failed: list[dict[str, Any]] = []
    deferred: list[dict[str, Any]] = []
    status, stop, error = _STATUS_FINISHED, None, None
    for i, g in enumerate(groups):
        stop = _stop_reason(lock, now())  # re-checked between terms: the window, a pass starting
        if stop is not None:
            status = _STATUS_STOPPED
            for rest in groups[i:]:
                for t in rest.targets:
                    if t.planned:
                        deferred.append(_deferred(t, t.planned, "the run stopped"))
            break
        # a term with nothing to fetch still walks its stored months (a new_filers re-derive, no request)
        results = fetch_group(client, g, now=now, workers=workers)
        if results:
            requests += len(results)
            ok += sum(1 for r in results.values() if r.count is not None)
            from_cache += sum(1 for r in results.values() if r.count is not None and r.cached)
            for m, r in sorted(results.items()):
                if r.error is not None:
                    cards = sorted({t.card.name for t in g.targets if m in t.planned})
                    failed.append(
                        {
                            "term": g.query,
                            "month": f"{m:%Y-%m}",
                            "error": r.error,
                            "cards": cards,
                        }
                    )
                    print(
                        f'term-mentions: FAILED "{g.query}" {m:%Y-%m} — {r.error} (cards: {", ".join(cards)})'
                    )
        outcomes = [(t, target_rows(t, results, tenant_id=tenant_id)) for t in g.targets]
        rows = [row for _, o in outcomes for row in o.rows]
        try:
            for row in rows:
                append_fact(conn, TABLE, row)
            conn.commit()  # ONE transaction per term: its cards' rows land together or not at all
        except Exception as e:  # noqa: BLE001 — rolled back: this term wrote nothing; the run stops
            conn.rollback()
            status, error = _STATUS_FAILED, f'writing "{g.query}": {type(e).__name__}: {e}'
            print(f"TERM MENTIONS FAILED — {error} (rolled back; earlier terms kept)")
            for rest in groups[i:]:
                for t in rest.targets:
                    if t.planned:
                        deferred.append(_deferred(t, t.planned, "the run failed"))
            break
        for t, o in outcomes:
            appended += len(o.rows)
            unchanged += o.unchanged
            rederived += o.rederived
            if o.deferred:
                why = "an earlier unstored month failed: " + month_ranges(o.failed)
                deferred.append(_deferred(t, o.deferred, why))
                print(
                    f'term-mentions: deferred "{t.term}" on {t.card.name} — '
                    f"{month_ranges(o.deferred)} ({why})"
                )
    attempts = getattr(client, "live_fetches", 0) - attempts0
    completed = getattr(client, "live_completed", 0) - completed0
    n_deferred = sum(d["n"] for d in deferred)
    summary = (
        f"term-mentions: {schedule} · {head} · {requests:,} requests ({from_cache:,} from cache) · "
        f"{appended:,} rows appended · {unchanged:,} unchanged · {len(failed):,} failed · "
        f"{n_deferred:,} deferred"
    )
    doc: dict[str, Any] = {
        "kind": PASS_KIND,
        "mode": MODE_APPLY,
        "schedule": schedule,
        "run_kind": run_kind,
        "tenant_id": str(tenant_id),
        "live": live,
        "window": [f"{window[0]:%Y-%m}", f"{window[-1]:%Y-%m}"],
        "started_at": started.isoformat(),
        "finished_at": now().isoformat(),
        "status": status,
        "stopped": stop,
        "error": error,
        "counts": {
            "requests": requests,
            "requests_ok": ok,
            "from_cache": from_cache,
            "network_attempts": attempts,
            "network_completed": completed,
            "rows_appended": appended,
            "rows_rederived": rederived,
            "rows_unchanged": unchanged,
            "failed_requests": len(failed),
            "deferred_rows": n_deferred,
        },
        "cost": cost,
        "failed": failed,
        "deferred": deferred,
        "plan": _plan_doc(groups),
        "summary": summary,
        "attention": bool(failed or deferred or status != _STATUS_FINISHED),
    }
    path = _write_artifact(doc, runs_dir)
    print(summary + (f" · {path}" if path else ""))
    if status == _STATUS_STOPPED:
        print(
            f"term-mentions: STOPPED — {stop}; the terms already written are kept, the rest are listed "
            f"deferred (exit {EXIT_ALREADY_RUNNING})"
        )
        return EXIT_ALREADY_RUNNING
    return _EXIT_FAILED if status == _STATUS_FAILED else 0


def _deferred(t: Target, months: Sequence[date], why: str) -> dict[str, Any]:
    return {
        "card": t.card.name,
        "thesis_id": str(t.card.thesis_id),
        "term": t.term,
        "months": month_ranges(months),
        "n": len(months),
        "reason": why,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Count each live card's SIGNAL-term mentions in EDGAR full-text search, per month "
        "(dry-run by default)."
    )
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--backfill",
        action="store_true",
        help="fetch every window month not yet stored for each card-term (resumable)",
    )
    mode.add_argument(
        "--weekly",
        action="store_true",
        help="the same missing-month fill PLUS a re-fetch of the current month and the one before; a no-op "
        "while the table is empty (the first backfill is the operator's call)",
    )
    p.add_argument(
        "--months",
        type=int,
        default=DEFAULT_MONTHS,
        help=f"the window: this many calendar months ending with the current one (default {DEFAULT_MONTHS})",
    )
    p.add_argument("--thesis", default=None, help="scope to one live card (its UUID)")
    p.add_argument(
        "--apply",
        action="store_true",
        help="fetch + write + a run artifact; without it the run is a dry run (no SEC request, no write)",
    )
    p.add_argument(
        "--no-live",
        action="store_true",
        help="cache only: never reach SEC (a month not cached fails, listed — never a zero)",
    )
    p.add_argument(
        "--tenant-id",
        default=None,
        help="target tenant UUID; defaults to the deployment tenant ($ALPHADECK_TENANT_ID / demo)",
    )
    p.add_argument(
        "--run-kind",
        choices=("manual", "cron"),
        default="manual",
        help="stamped on the artifact; the cron sidecar passes cron",
    )
    p.add_argument(
        "--if-stale-days",
        type=float,
        default=None,
        help="run only when an applied run exists and is older than this many days (the boot catch-up)",
    )
    a = p.parse_args(argv)
    if a.months < 1:
        p.error("--months must be at least 1")
    return run(
        schedule=BACKFILL if a.backfill else WEEKLY,
        apply=a.apply,
        live=not a.no_live,
        tenant_id=UUID(a.tenant_id) if a.tenant_id else current_tenant_id(),
        months=a.months,
        thesis_id=UUID(a.thesis) if a.thesis else None,
        run_kind=a.run_kind,
        if_stale_days=a.if_stale_days,
    )


if __name__ == "__main__":
    sys.exit(main())
