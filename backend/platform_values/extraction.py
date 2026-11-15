"""BACKGROUND EXTRACTION (V2 W3a): the attempt record's gate and the runner that keeps platform values current.

``docs/PLATFORM_VALUES.md`` ("Background extraction"). Three pieces, no caller yet (the nightly leg, the backfill
CLI and the onboarding step are later PRs):

- **The gate (pure).** ``inputs_key`` names exactly what the extractor reads: the company's CIK plus the newest
  10-Q, 10-K, 20-F and 40-F (``PERIODIC_FORMS``, exact match, like ``ingest.edgar.submissions.filings_of``).
  ``due`` decides from a company's attempts and its stored filing index whether it is due: ``never`` (no attempt),
  ``new_filing`` (a periodic filing newer than the ones its last attempt read, public for ``hold_h`` hours, so
  companyfacts has caught up), or ``retry`` (the last attempt failed fewer than ``retry_max`` times in a row on the
  same key, or another writer held the company). A steward's failed Extract now click (``run_kind`` ``steward``)
  never counts toward ``retry_max`` (operator decision 2026-11-15), though a steward attempt counts for everything
  else. Otherwise it is not due: ``up to date``, ``held`` or ``waiting``.
  "Public" is the SEC acceptance, else the end of the filing date in ET (late, never early); a filing not public
  at ``now`` never makes a company due (CLAUDE.md invariant #1).
- **The per-company step.** ``extract_one`` rolls back any open transaction, FETCHES with the company free
  (through the client the caller passes in), then calls the machine writer (``platform_values.machine``, which takes
  the company lock), then appends exactly ONE attempt row: ``done``, ``error`` (a bounded one-line reason, never
  the raw exception), ``busy`` or ``skipped``. It never raises for a company.
- **The run loop.** ``run_extraction`` walks a work list (``work_list``: due companies, most cards first, through
  ``platform_values.cards.card_counts``) under a count and a time budget, fail-open per company, honoring the kill
  switch and a ``should_stop`` check, and returns an ``ExtractionRun`` summary for a run artifact.

Nothing here is a call input. It lives in ``platform_values/``, never in ``domain/`` (the editions fingerprint hashes
``domain/``).
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Literal, NamedTuple
from uuid import UUID

import psycopg

from db.write_lock import CompanyLockBusy
from domain.market_time import market_tz
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.edgar.acceptance import filing_date_bound, parse_filing_date, resolve_public_acceptance
from ingest.edgar.client import CachedNotFound, InvalidBody
from ingest.edgar.extract import extract_with_annual_fallback
from ingest.edgar.submissions import fetch_submissions, index_filings
from platform_values.cards import card_counts
from platform_values.machine import MachineOutcome, record_extraction
from repositories import platform_extraction_repo as repo

# The forms the extractor chooses from (``ingest.edgar.extract``: the 10-Q / 10-K periodic path, else the 20-F /
# 40-F annual path). Exact match: an amendment (10-Q/A) or a current report (8-K, 6-K) is never read.
PERIODIC_FORMS: tuple[str, ...] = ("10-Q", "10-K", "20-F", "40-F")

DueReason = Literal["never", "new_filing", "retry"]
AttemptStatus = Literal["done", "error", "busy", "skipped"]
RunKind = Literal["nightly", "onboarding", "backfill", "steward", "get_data"]


@dataclass(frozen=True)
class PeriodicFiling:
    """One periodic filing of a company, from either source: the stored filing index (``fact_filing_index``) or
    the submissions JSON at extraction time. ``accepted`` is the TRUSTED SEC acceptance, or None."""

    form: str
    accession: str
    filed: date
    accepted: datetime | None = None

    @property
    def public_at(self) -> datetime:
        """When it became public: the acceptance, else the end of the filing date in ET (never early)."""
        return self.accepted if self.accepted is not None else filing_date_bound(self.filed)

    def _order(self) -> tuple:
        return (self.filed, self.public_at, self.accession)


@dataclass(frozen=True)
class AttemptRow:
    """The part of a stored attempt the gate reads. ``run_kind`` None = unknown (counted like any other run)."""

    status: str
    inputs_key: str
    recorded_at: datetime
    run_kind: str | None = None


class Due(NamedTuple):
    """``reason`` None = not due; ``note`` says why in a word or two (``up to date``, ``held``, ``waiting``)."""

    reason: DueReason | None
    note: str


def _norm_cik(cik: str | int | None) -> str:
    text = str(cik or "").strip()
    return str(int(text)) if text.isdigit() else text


def _latest(filings: Iterable[PeriodicFiling]) -> dict[str, PeriodicFiling]:
    out: dict[str, PeriodicFiling] = {}
    for f in filings:
        if f.form not in PERIODIC_FORMS:
            continue
        cur = out.get(f.form)
        if cur is None or f._order() > cur._order():
            out[f.form] = f
    return out


def inputs_key(cik: str | int | None, filings: Iterable[PeriodicFiling]) -> str:
    """``"<cik>|10-Q:<acc>|10-K:<acc>|20-F:<acc>|40-F:<acc>"`` (an empty part when the company has none). Pure;
    the same answer from the stored filing index and from the submissions JSON."""
    latest = _latest(filings)
    parts = [
        f"{form}:{latest[form].accession if form in latest else ''}" for form in PERIODIC_FORMS
    ]
    return "|".join([_norm_cik(cik), *parts])


def parse_inputs_key(key: str) -> tuple[str, dict[str, str]]:
    """``inputs_key``'s inverse: ``(cik, {form: accession or ""})``."""
    head, *parts = (key or "").split("|")
    accs = dict.fromkeys(PERIODIC_FORMS, "")
    for part in parts:
        form, _, acc = part.partition(":")
        if form in accs:
            accs[form] = acc
    return head, accs


def _new_filings(ref_key: str, filings: Sequence[PeriodicFiling]) -> list[PeriodicFiling]:
    """The newest filing of each form that is NEWER than the one the attempt keyed on ``ref_key`` read. An
    attempt that read a filing the stored index does not have yet read a NEWER one (the index lags a night).
    """
    _, read = parse_inputs_key(ref_key)
    by_acc = {(f.form, f.accession): f for f in filings}
    new: list[PeriodicFiling] = []
    for form, f in _latest(filings).items():
        seen_acc = read.get(form, "")
        if f.accession == seen_acc:
            continue
        if seen_acc:
            seen = by_acc.get((form, seen_acc))
            if seen is None or f._order() <= seen._order():
                continue
        new.append(f)
    return new


def due(
    attempts: Sequence[AttemptRow],
    filings: Iterable[PeriodicFiling],
    *,
    cik: str | int | None,
    now: datetime,
    hold_h: float,
    retry_max: int,
) -> Due:
    """Is the company due for extraction? ``attempts`` newest first; ``filings`` its stored periodic filings
    (any order). Pure. The rules are the module docstring's. The failure streak skips ``busy`` attempts and a
    steward's ``error`` attempts (``AttemptRow.run_kind``), so only unattended failures reach ``retry_max``.
    """
    if not attempts:
        return Due("never", "never attempted")
    if attempts[0].status == "busy":
        return Due("retry", "busy")  # another writer held it: not a failure, take it next time
    ref = next(a for a in attempts if a.status != "busy")
    public = [f for f in filings if f.form in PERIODIC_FORMS and f.public_at <= now]
    ref_cik, _ = parse_inputs_key(ref.inputs_key)
    if _norm_cik(cik) != ref_cik:
        return Due("new_filing", "cik changed")
    new = _new_filings(ref.inputs_key, public)
    if new:
        newest = max(f.public_at for f in new)
        if now - newest < timedelta(hours=hold_h):
            return Due(None, "held")  # companyfacts lags acceptance: extract it on a later night
        return Due("new_filing", "new filing")
    if ref.status == "error":
        streak = 0
        for a in attempts:
            if a.status == "busy":
                continue
            if a.run_kind == "steward" and a.status == "error":
                continue  # a failed steward click never counts toward the cap (2026-11-15)
            if a.status != "error" or a.inputs_key != ref.inputs_key:
                break
            streak += 1
        if streak < retry_max:
            return Due("retry", "failed")
        return Due(None, "waiting")  # for a new filing or the steward
    return Due(None, "up to date")


def filings_from_rows(rows: Iterable[Mapping]) -> list[PeriodicFiling]:
    """Rows with ``form``, ``accession``, ``filed`` (a date) and ``accepted`` (a trusted instant or None)."""
    out = []
    for r in rows:
        accepted = r.get("accepted")
        if accepted is not None and accepted.tzinfo is None:
            accepted = accepted.replace(tzinfo=timezone.utc)
        out.append(PeriodicFiling(r["form"], r["accession"], r["filed"], accepted))
    return out


def filings_from_submissions(submissions: Mapping[str, Any]) -> list[PeriodicFiling]:
    """The periodic filings of a submissions JSON (``filings.recent``), with the trusted acceptance (the F7 rule,
    request-free) or None. A row with no readable accession or filing date is skipped."""
    out = []
    for r in index_filings(dict(submissions)):
        filed = parse_filing_date(r["filed"])
        if r["form"] not in PERIODIC_FORMS or not r["accession"] or filed is None:
            continue
        accepted = resolve_public_acceptance(r["accepted_raw"] or None, filed).at
        out.append(PeriodicFiling(r["form"], r["accession"], filed, accepted))
    return out


# --- the work list -------------------------------------------------------------------------------------------------

# Due reasons in the order a run takes them (Q5): a new filing first, then a retry, then a never-extracted company.
_REASON_RANK = {"new_filing": 0, "retry": 1, "never": 2}
# Attempts read per company: enough for any retry streak (busy rows in between included).
_ATTEMPTS_READ = 20


@dataclass(frozen=True)
class WorkItem:
    """One due company: who it is, its card count (``card_counts``), why it is due and the gate's inputs key."""

    security_id: UUID
    ticker: str | None
    cik: str | None
    instrument_kind: str
    cards: int
    pen_only: bool
    reason: DueReason
    note: str
    inputs_key: str


@dataclass(frozen=True)
class WorkList:
    """The due companies in run order, and what was left out: never-extracted companies withheld until the first
    backfill (B4), companies held (a filing too new) and companies waiting (failed ``retry_max`` times).
    """

    items: list[WorkItem]
    withheld_never: int = 0
    held: int = 0
    waiting: int = 0


def extractable(company: Mapping[str, Any]) -> bool:
    """Is this company extracted at all? ETFs are not (their share count is ``fact_fund_shares``; the extractor would
    only write "no annual filing" for them). A decision that can wait: this is the ONE line to flip.
    """
    return company.get("instrument_kind") != "etf"


def backfill_applied(conn: psycopg.Connection) -> bool:
    """THE B4 MARKER (approved B4 (a)): has a backfill run been applied at least once (any ``backfill`` attempt)?
    Until it has, the nightly and onboarding steps take no never-extracted company."""
    return repo.any_attempt(conn, "backfill")


def work_list(
    conn: psycopg.Connection,
    *,
    now: datetime | None = None,
    include_never: bool | None = None,
    hold_h: float | None = None,
    retry_max: int | None = None,
) -> WorkList:
    """Every DUE company on a live card (any workspace), in run order: ``new_filing``, then ``retry``, then
    ``never``; within each, pen-only companies last, then most cards first (``card_counts``), then ticker and id.

    ``include_never`` None = the B4 rule (never-extracted companies only once ``backfill_applied``); the backfill
    passes True. ``hold_h`` / ``retry_max`` default to the settings. A fixed number of queries; no network.
    """
    s = get_settings()
    at = now or datetime.now(timezone.utc)
    hold = s.platform_extraction_hold_h if hold_h is None else hold_h
    retries = s.platform_extraction_retry_max if retry_max is None else retry_max
    with_never = backfill_applied(conn) if include_never is None else include_never
    companies = [c for c in repo.on_card_companies(conn) if extractable(c)]
    ids = [c["security_id"] for c in companies]
    counts = card_counts(conn, ids)
    filings = repo.periodic_filings(conn, ids)
    attempts = repo.latest_attempts(conn, ids, per_company=_ATTEMPTS_READ)
    items: list[WorkItem] = []
    withheld = held = waiting = 0
    for c in companies:
        sid = c["security_id"]
        count = counts.get(sid)
        if count is None:  # archived between the two reads: on no live card now
            continue
        rows = [
            AttemptRow(a["status"], a["inputs_key"], a["recorded_at"], a["run_kind"])
            for a in attempts.get(sid, [])
        ]
        fs = filings_from_rows(filings.get(sid, []))
        d = due(rows, fs, cik=c["cik"], now=at, hold_h=hold, retry_max=retries)
        if d.reason is None:
            held += d.note == "held"
            waiting += d.note == "waiting"
            continue
        if d.reason == "never" and not with_never:
            withheld += 1
            continue
        items.append(
            WorkItem(
                security_id=sid,
                ticker=c["ticker"],
                cik=c["cik"],
                instrument_kind=c["instrument_kind"],
                cards=count.cards,
                pen_only=count.pen_only,
                reason=d.reason,
                note=d.note,
                inputs_key=inputs_key(c["cik"], [f for f in fs if f.public_at <= at]),
            )
        )
    items.sort(
        key=lambda i: (
            _REASON_RANK[i.reason],
            i.pen_only,
            -i.cards,
            i.ticker or "",
            str(i.security_id),
        )
    )
    return WorkList(items, withheld, held, waiting)


# --- the per-company step ------------------------------------------------------------------------------------------

_ERROR_MAX = 200  # the table's check: one line, at most 200 characters


def error_reason(exc: BaseException) -> str:
    """A bounded, ONE-LINE, operator-facing reason for a failed attempt. Built from the exception's CLASS, never its
    text (which can carry a URL, a path or a secret); the text is only matched to choose a fixed sentence.
    """
    import httpx  # the SEC client's transport; imported here so the pure gate needs no HTTP stack

    if isinstance(exc, CacheMiss):
        reason = "not cached"
    elif isinstance(exc, CachedNotFound):
        reason = "SEC has no such document (404)"
    elif isinstance(exc, InvalidBody):
        reason = "SEC sent an unreadable document"
    elif isinstance(exc, httpx.HTTPStatusError):
        reason = f"SEC answered HTTP {int(exc.response.status_code)}"
    elif isinstance(exc, httpx.TimeoutException):
        reason = "SEC timed out"
    elif isinstance(exc, httpx.TransportError):
        reason = "SEC unreachable"
    elif isinstance(exc, RuntimeError) and "USER_AGENT" in str(exc):
        reason = "no SEC User-Agent set"
    else:
        reason = f"extraction failed ({type(exc).__name__})"
    return " ".join(reason.split())[:_ERROR_MAX]


@dataclass(frozen=True)
class AttemptResult:
    """What one ``extract_one`` did: the attempt's ``status`` and row id, the machine writer's reason per value type
    (``done``), the bounded ``error`` / the ``detail``, and the SEC requests it made."""

    status: AttemptStatus
    attempt_id: UUID | None
    shares_reason: str | None = None
    cash_reason: str | None = None
    error: str | None = None
    detail: str | None = None
    live_fetches: int | None = None
    outcomes: Mapping[str, MachineOutcome] = field(default_factory=dict)


def _fetches(client: Any) -> int | None:
    n = getattr(client, "live_fetches", None)
    return n if isinstance(n, int) else None


def extract_one(
    conn: psycopg.Connection,
    company: WorkItem,
    *,
    client: Any,
    wait_s: float,
    run_kind: RunKind,
) -> AttemptResult:
    """Extract ONE company's platform values and append exactly ONE attempt row. Never raises for the company.

    1. Rolls back any open transaction on ``conn`` (the writer commits; nothing of the caller's rides along).
    2. FETCHES with the company free, through ``client`` (the caller's; this function never builds one): the
       submissions index (the inputs key) and ``extract_with_annual_fallback(..., values_only=True)``.
    3. Only then calls the machine writer (``record_extraction``, which takes the company lock for at most
       ``wait_s`` and commits), with ``cache_dir`` (the client's) and ``cik``.
    4. Appends the attempt and commits: ``done`` (the writer's reason per type), ``error`` (``error_reason``: a
       fetch or write failure; nothing else is written, so a value awaiting the steward stays awaiting), ``busy``
       (``CompanyLockBusy``: nothing written, not a failure) or ``skipped`` (no CIK: nothing fetched).

    Only a failure to write the attempt row itself (the database gone) propagates."""
    conn.rollback()
    before = _fetches(client)
    key = company.inputs_key
    if not company.cik:
        return _append(conn, company, run_kind, key, "skipped", detail="no CIK", fetched=None)
    status: AttemptStatus = "done"
    outcomes: Mapping[str, MachineOutcome] = {}
    err = detail = None
    try:
        key = inputs_key(
            company.cik, filings_from_submissions(fetch_submissions(client, company.cik))
        )
        result = extract_with_annual_fallback(client, company.cik, values_only=True)
        outcomes = record_extraction(
            conn,
            company.security_id,
            result,
            wait_s=wait_s,
            cache_dir=getattr(client, "cache_dir", None),
            cik=company.cik,
        )
    except CompanyLockBusy:
        status, detail = "busy", "company busy"
    except Exception as exc:  # noqa: BLE001 — fail-open per company: recorded as an error attempt
        status, err = "error", error_reason(exc)
    conn.rollback()  # the writer committed or rolled back already; leave nothing open
    after = _fetches(client)
    fetched = after - before if before is not None and after is not None else None
    return _append(
        conn,
        company,
        run_kind,
        key,
        status,
        outcomes=outcomes,
        error=err,
        detail=detail,
        fetched=fetched,
    )


def _append(
    conn: psycopg.Connection,
    company: WorkItem,
    run_kind: RunKind,
    key: str,
    status: AttemptStatus,
    *,
    outcomes: Mapping[str, MachineOutcome] | None = None,
    error: str | None = None,
    detail: str | None = None,
    fetched: int | None,
) -> AttemptResult:
    outcomes = outcomes or {}
    shares = outcomes.get("shares_outstanding")
    cash = outcomes.get("cash_burn")
    try:
        aid = repo.append_attempt(
            conn,
            security_id=company.security_id,
            run_kind=run_kind,
            inputs_key=key,
            status=status,
            shares_reason=shares.reason if shares else None,
            cash_reason=cash.reason if cash else None,
            error=error,
            detail=detail,
            live_fetches=fetched,
        )
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return AttemptResult(
        status=status,
        attempt_id=aid,
        shares_reason=shares.reason if shares else None,
        cash_reason=cash.reason if cash else None,
        error=error,
        detail=detail,
        live_fetches=fetched,
        outcomes=outcomes,
    )


# --- the run loop --------------------------------------------------------------------------------------------------

# The run kinds the kill switch turns off (``platform_extraction_max_names = 0``); a backfill or a steward's
# "extract now" still runs by hand.
KILL_SWITCHED: frozenset[str] = frozenset({"nightly", "onboarding"})
_LABELS_MAX = 20  # failed companies named in the summary
_LINE_LABELS = 5  # ... and on the Admin line


def _company_bucket(r: AttemptResult) -> str:
    reasons = {o.reason for o in r.outcomes.values()}
    if any(o.applied for o in r.outcomes.values()):
        return "applied"
    if reasons & {"flagged", "pinned"}:
        return "to_steward"
    if reasons and reasons <= {"nothing_found"}:
        return "nothing_found"
    return "unchanged"


@dataclass
class ExtractionRun:
    """The summary of one run (a later PR writes ``to_json()`` into its run artifact and shows ``admin_line()``).

    ``status``: ``ran`` (the loop ran, perhaps stopped early: ``stopped_by`` = ``cap`` / ``budget`` / ``stop``),
    ``off`` (the kill switch) or ``error`` (no database connection; ``error`` says why). ``attempted`` = attempts
    made; ``companies`` buckets the ``done`` ones; ``outcomes`` counts the writer's reasons per value type;
    ``deferred`` = due companies left for the next run."""

    run_kind: str
    started_at: datetime
    max_names: int | None
    budget_s: float | None
    status: str = "ran"
    finished_at: datetime | None = None
    seconds: float = 0.0
    due_total: int = 0
    due_by_reason: dict[str, int] = field(default_factory=dict)
    attempted: int = 0
    companies: dict[str, int] = field(
        default_factory=lambda: dict.fromkeys(
            ("applied", "to_steward", "nothing_found", "unchanged"), 0
        )
    )
    outcomes: dict[str, dict[str, int]] = field(
        default_factory=lambda: {"shares_outstanding": {}, "cash_burn": {}}
    )
    failed: int = 0
    failed_labels: list[str] = field(default_factory=list)
    busy: int = 0
    skipped: int = 0
    deferred: int = 0
    stopped_by: str | None = None
    live_fetches: int = 0
    error: str | None = None

    @property
    def extracted(self) -> int:
        return sum(self.companies.values())

    @property
    def attention(self) -> bool:
        """Loud on Admin: a company failed, or the run itself did."""
        return self.failed > 0 or self.status == "error"

    def _add(self, item: WorkItem, r: AttemptResult) -> None:
        self.attempted += 1
        if r.live_fetches:
            self.live_fetches += r.live_fetches
        if r.status == "done":
            self.companies[_company_bucket(r)] += 1
            for ft, o in r.outcomes.items():
                per = self.outcomes.setdefault(ft, {})
                per[o.reason] = per.get(o.reason, 0) + 1
        elif r.status == "error":
            self._fail(item, r.error or "failed")
        elif r.status == "busy":
            self.busy += 1
        else:
            self.skipped += 1

    def _fail(self, item: WorkItem, reason: str) -> None:
        self.failed += 1
        if len(self.failed_labels) < _LABELS_MAX:
            self.failed_labels.append(f"{item.ticker or item.security_id}: {reason}")

    def admin_line(self) -> str:
        """One plain-English line, e.g. ``Shared facts · Nov 14: 42 extracted: 31 applied, 6 to the steward,
        5 nothing found · 2 failed (ACME: SEC unreachable) · 118 still due · 84 SEC requests · 3m 12s``.
        """
        local = self.started_at.astimezone(market_tz())
        head = f"Shared facts · {local.strftime('%b')} {local.day}"
        if self.status == "off":
            return f"{head}: off (the kill switch, ALPHADECK_PLATFORM_EXTRACTION_MAX_NAMES=0)"
        if self.status == "error":
            return f"{head}: did not run ({self.error})"
        c = self.companies
        parts = [
            f"{head}: {self.extracted} extracted: {c['applied']} applied, {c['to_steward']} to the steward, "
            f"{c['nothing_found']} nothing found"
            + (f", {c['unchanged']} unchanged" if c["unchanged"] else "")
        ]
        if self.failed:
            named = ", ".join(self.failed_labels[:_LINE_LABELS])
            more = ", …" if self.failed > _LINE_LABELS else ""
            parts.append(f"{self.failed} failed ({named}{more})")
        if self.busy:
            parts.append(f"{self.busy} busy")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.deferred:
            parts.append(f"{self.deferred} still due")
        parts.append(f"{self.live_fetches} SEC requests")
        m, sec = divmod(int(round(self.seconds)), 60)
        parts.append(f"{m}m {sec:02d}s" if m else f"{sec}s")
        return " · ".join(parts)

    def to_json(self) -> dict[str, Any]:
        """A JSON-ready dict (the run artifact's ``platform_extraction`` block), ``admin_line`` and ``attention``
        included."""
        return {
            "kind": "platform_extraction",
            "run_kind": self.run_kind,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "seconds": round(self.seconds, 3),
            "max_names": self.max_names,
            "budget_s": self.budget_s,
            "due_total": self.due_total,
            "due_by_reason": dict(self.due_by_reason),
            "attempted": self.attempted,
            "extracted": self.extracted,
            "companies": dict(self.companies),
            "outcomes": {ft: dict(v) for ft, v in self.outcomes.items()},
            "failed": self.failed,
            "failed_labels": list(self.failed_labels),
            "busy": self.busy,
            "skipped": self.skipped,
            "deferred": self.deferred,
            "stopped_by": self.stopped_by,
            "live_fetches": self.live_fetches,
            "error": self.error,
            "attention": self.attention,
            "admin_line": self.admin_line(),
        }


def _defaults(run_kind: str) -> tuple[int | None, float | None]:
    s = get_settings()
    if run_kind == "nightly":
        return s.platform_extraction_max_names, s.platform_extraction_budget_s
    if run_kind == "onboarding":
        return s.platform_extraction_onboard_max_names, None
    return None, None


def run_extraction(
    connect: Callable[[], psycopg.Connection],
    work: Sequence[WorkItem],
    *,
    client: Any,
    run_kind: RunKind,
    should_stop: Callable[[], bool] | None = None,
    max_names: int | None = None,
    budget_s: float | None = None,
    wait_s: float | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> ExtractionRun:
    """Walk ``work`` (a ``work_list``'s items, in its order) with ``extract_one`` on ONE connection from
    ``connect`` (re-opened if it breaks), and return the ``ExtractionRun`` summary. Never raises.

    - THE KILL SWITCH: for a nightly or onboarding run, ``Settings.platform_extraction_max_names == 0`` is
      ``status = "off"``: nothing is fetched or written.
    - THE BUDGET, checked BETWEEN companies: ``should_stop()`` true (``stop``), ``max_names`` attempts made
      (``cap``), or the slowest company so far would not finish inside ``budget_s`` (``budget``: it never starts
      one it cannot afford). Defaults: a nightly run takes ``platform_extraction_max_names`` and
      ``platform_extraction_budget_s``, an onboarding run ``platform_extraction_onboard_max_names``; a backfill
      or steward run has no cap unless given. The rest is ``deferred`` (still due next time).
    - FAIL-OPEN per company: a company's fault is its error attempt; the loop goes on.
    - The lock wait defaults to ``ingest_lock_request_wait_s`` (15 s), so one busy company cannot spend the budget.
    """
    cap_default, budget_default = _defaults(run_kind)
    cap = cap_default if max_names is None else max_names
    budget = budget_default if budget_s is None else budget_s
    wait = get_settings().ingest_lock_request_wait_s if wait_s is None else wait_s
    run = ExtractionRun(
        run_kind=run_kind,
        started_at=datetime.now(timezone.utc),
        max_names=cap,
        budget_s=budget,
        due_total=len(work),
        due_by_reason=dict(Counter(i.reason for i in work)),
    )
    t0 = clock()
    if run_kind in KILL_SWITCHED and get_settings().platform_extraction_max_names == 0:
        run.status = "off"
        run.deferred = len(work)
        return _finish(run, clock() - t0)
    conn: psycopg.Connection | None = None
    slowest = 0.0
    try:
        for n, item in enumerate(work):
            start = clock()
            if should_stop is not None and should_stop():
                run.stopped_by = "stop"
            elif cap is not None and run.attempted >= cap:
                run.stopped_by = "cap"
            elif budget is not None and (start - t0) + slowest > budget:
                run.stopped_by = "budget"
            if run.stopped_by:
                run.deferred = len(work) - n
                break
            if conn is None or conn.closed or conn.broken:
                conn = _reconnect(conn, connect)
            try:
                result = extract_one(conn, item, client=client, wait_s=wait, run_kind=run_kind)
                run._add(item, result)
            except (
                Exception
            ):  # noqa: BLE001 — the attempt row itself could not be written (database trouble)
                run.attempted += 1
                run._fail(item, "could not record the attempt")
            slowest = max(slowest, clock() - start)
    except Exception as exc:  # noqa: BLE001 — no connection: the run reports it, never raises
        run.status, run.error = "error", f"no database connection ({type(exc).__name__})"
    finally:
        if conn is not None and not conn.closed:
            conn.close()
    return _finish(run, clock() - t0)


def _reconnect(
    conn: psycopg.Connection | None, connect: Callable[[], psycopg.Connection]
) -> psycopg.Connection:
    if conn is not None and not conn.closed:
        conn.close()
    return connect()


def _finish(run: ExtractionRun, seconds: float) -> ExtractionRun:
    run.seconds = seconds
    run.finished_at = datetime.now(timezone.utc)
    return run


__all__ = [
    "KILL_SWITCHED",
    "PERIODIC_FORMS",
    "AttemptResult",
    "AttemptRow",
    "Due",
    "DueReason",
    "ExtractionRun",
    "PeriodicFiling",
    "RunKind",
    "WorkItem",
    "WorkList",
    "backfill_applied",
    "due",
    "error_reason",
    "extract_one",
    "extractable",
    "filings_from_rows",
    "filings_from_submissions",
    "inputs_key",
    "parse_inputs_key",
    "run_extraction",
    "work_list",
]
