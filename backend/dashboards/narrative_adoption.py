"""Narrative Adoption Curve (``docs/dashboards/narrative-adoption.md``): how many filings mention ONE card's
SIGNAL terms each month, over three years, split by who files them, with the card's basket line against SPY
on the same axis. It shows which forms carry the card's terms and whether the filer count is widening; it
never says "early" or "crowded" (the reading is the operator's). Follows the as-of dial.

PURE: the router reads the card, its stored term counts (``PointInTimeData.term_mentions``: migration 0071,
written by ``pipeline.term_mentions``), the roster's closes and SPY's through the point-in-time view, and
hands them to ``narrative_adoption(...)``. Display only: nothing here feeds a call, scores an idea or writes
anything (rule 1). The only imports from the pipeline are its pure helpers (``signal_terms``, ``month_window``,
``month_last_day``) and its run-artifact reader's shape; no writer is called.

The rules, in one place:
- **The axis.** The ``MONTHS`` (36) calendar months ending with the as-of's month, each as its first day.
  A month is on the axis whether or not it was counted: a month with no stored count for any of the card's
  terms is ABSENT (``counted`` false, every number ``None``), never a zero. The as-of's own month has not
  closed (the job stores a month under ``valid_from`` = its last day), so at a past as-of the curve ends at
  the last month that had closed by then.
- **The terms.** The card's CURRENT SIGNAL terms (``pipeline.term_mentions.signal_terms``: tier ``signal``,
  stripped, de-duplicated case-insensitively). BROAD terms are collision-prone and never counted (counted
  here as left out). A stored term that is no longer in the card's term set is ignored by the curve and
  counted (``retired_terms``): history is never deleted, and the page shows the card's current terms. A
  stored row is matched to a term by its exact spelling, the job's own rule (an edited spelling is a new term
  to the job, so it reads "not counted yet" until the next weekly run).
- **The stacking.** Per month, documents and each form group (reports, events, issuance, other) are SUMMED
  over the terms that have a stored row that month. A document matching two terms counts twice (the page
  says so); ``terms_counted`` says how many terms the month's sums rest on, so a month where fewer than every
  term was stored reads as a floor. ``top_terms`` = the three terms with the most documents that month.
- **The filers.** Per month, distinct CIKs SUMMED over terms (a company filing under two terms counts
  twice). ``filers_sampled`` ("at least") when any contributing term-month was sampled. ``new_filers`` = the
  sum of the terms' own ``new_filers`` (the job's: CIKs no earlier stored month of that term had); a term's
  first stored month has none (NULL), and a month where no term has one is ``None``, not 0. The new-filers
  line takes the filers line's mark.
- **A month read before it closed.** ``read_early`` = some contributing row was read (``fetched_at``, market
  time) on or before the month's last day: the weekly job re-reads a month after it closes, so until then
  the count is partial.
- **The term table.** One row per current SIGNAL term: its documents in the page's last counted month, the
  last three and the last twelve (calendar months ending there; the sum is over the months stored, with how
  many of them), its filers in the last month, and the month it first had a document in the stored window
  (``first_at_start``: that is the earliest stored month, so it may be older). Each term is counted alone (no
  double count). Sorted by last-month documents, a term with none last; a term with no stored month is
  ``counted`` false ("not counted yet"), never zero, and sorts after a counted term with none in the last
  month.
- **The overlay.** The card's basket as of the as-of, looking back, as ONE equal-weighted, daily-rebalanced
  line against SPY (``dashboards/series.py``, the Narrative Index's own math), from the last session before
  the axis starts. Context and Bench names are left out (the Narrative Index's default), a name with a
  flagged price history is left out and named, a row with no resolved security is counted and never priced.
  Both lines are rebased to 100 at ONE common session: the later of the two lines' first real step.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

from dashboards.baskets import Membership, counted_rows
from dashboards.series import BASE, SessionSource, aligned, chain, line_start, session_calendar
from domain.market_time import market_tz
from pipeline.term_mentions import month_last_day, month_window, signal_terms

MONTHS = 36
TOP_TERMS = 3
# the term table's windows, in calendar months ending at the page's last counted month
LAST_3 = 3
LAST_12 = 12
# more sessions than 36 months hold (about 756), so the calendar is only ever cut by the axis start
SESSION_CAP = 1000
# calendar days of bars read per company and SPY: 36 months is about 1,130 days (the display PIT's own
# bound is 630, which would cut the line short of the axis), plus the base session before the axis
PRICE_LOOKBACK_DAYS = 1200
BENCHMARK = "SPY"


# --- the card's terms -----------------------------------------------------------------------------------


def card_terms(term_set: Iterable[Mapping[str, Any]]) -> tuple[tuple[str, ...], int]:
    """``(SIGNAL terms, BROAD terms left out)`` from a card's stored term set (entries as ``{term, tier}``).
    The SIGNAL list is the job's own (``pipeline.term_mentions.signal_terms``); BROAD is counted by the same
    rule (stripped, blanks dropped, de-duplicated case-insensitively)."""
    entries = list(term_set)
    broad: set[str] = set()
    for entry in entries:
        if isinstance(entry, Mapping) and entry.get("tier") == "broad":
            term = entry.get("term")
            if isinstance(term, str) and term.strip():
                broad.add(term.strip().casefold())
    return signal_terms(entries), len(broad)


# --- the months -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TermShare:
    term: str
    documents: int


@dataclass(frozen=True)
class MonthPoint:
    month: date  # the calendar month, as its first day
    closed: bool  # the month had closed by the as-of
    counted: bool  # at least one current term has a stored count for it
    terms_counted: int  # how many current terms the sums below rest on
    documents: int | None  # None = not counted (never 0)
    reports: int | None
    events: int | None
    issuance: int | None
    other: int | None
    filers: int | None  # distinct CIKs summed over terms
    filers_sampled: bool  # "at least": some contributing term-month showed only part of its filers
    new_filers: int | None  # None = no contributing term has one
    read_early: bool  # some contributing count was read before the month closed
    top_terms: tuple[TermShare, ...]  # up to three, most documents first


def _read_early(row: Mapping[str, Any], month: date) -> bool:
    at = row.get("fetched_at")
    if not isinstance(at, datetime):
        return False
    return at.astimezone(market_tz()).date() <= month_last_day(month)


def _month_point(
    month: date, asof: date, contributing: Sequence[tuple[str, Mapping[str, Any]]]
) -> MonthPoint:
    closed = month_last_day(month) <= asof
    if not contributing:
        return MonthPoint(
            month, closed, False, 0, None, None, None, None, None, None, False, None, False, ()
        )

    def total(col: str) -> int:
        return sum(int(r[col]) for _, r in contributing)

    news = [int(r["new_filers"]) for _, r in contributing if r.get("new_filers") is not None]
    ranked = sorted(
        ((t, int(r["documents"])) for t, r in contributing if int(r["documents"]) > 0),
        key=lambda x: (-x[1], x[0].casefold()),
    )
    return MonthPoint(
        month=month,
        closed=closed,
        counted=True,
        terms_counted=len(contributing),
        documents=total("documents"),
        reports=total("reports"),
        events=total("events"),
        issuance=total("issuance"),
        other=total("other"),
        filers=total("filers"),
        filers_sampled=any(bool(r["filers_sampled"]) for _, r in contributing),
        new_filers=sum(news) if news else None,
        read_early=any(_read_early(r, month) for _, r in contributing),
        top_terms=tuple(TermShare(t, n) for t, n in ranked[:TOP_TERMS]),
    )


# --- the term table ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TermRow:
    term: str
    counted: bool  # at least one stored month in the axis; False = "not counted yet"
    months_stored: int  # axis months with a stored count
    last_month_documents: int | None  # in the page's last counted month; None = not stored there
    last_3_documents: int | None  # sum over the stored months of the last three; None = none stored
    months_last_3: int  # how many of those three months are stored
    last_12_documents: int | None
    months_last_12: int
    filers: int | None  # in the last counted month
    filers_sampled: bool
    first_month: (
        date | None
    )  # the first stored month with a document; None = none in the stored months
    first_at_start: bool  # that is also the earliest stored month: the term may be older


def _window_sum(
    by_month: Mapping[date, Mapping[str, Any]], axis: Sequence[date], last_idx: int, width: int
) -> tuple[int | None, int]:
    months = axis[max(0, last_idx - width + 1) : last_idx + 1]
    stored = [by_month[m] for m in months if m in by_month]
    if not stored:
        return None, 0
    return sum(int(r["documents"]) for r in stored), len(stored)


def _term_row(
    term: str,
    by_month: Mapping[date, Mapping[str, Any]],
    axis: Sequence[date],
    last_idx: int | None,
) -> TermRow:
    if not by_month:
        return TermRow(term, False, 0, None, None, 0, None, 0, None, False, None, False)
    first = min((m for m, r in by_month.items() if int(r["documents"]) > 0), default=None)
    earliest = min(by_month)
    if last_idx is None:  # unreachable while a month is stored, kept so the row never lies
        return TermRow(
            term, True, len(by_month), None, None, 0, None, 0, None, False, first, first == earliest
        )
    last = by_month.get(axis[last_idx])
    d3, n3 = _window_sum(by_month, axis, last_idx, LAST_3)
    d12, n12 = _window_sum(by_month, axis, last_idx, LAST_12)
    return TermRow(
        term=term,
        counted=True,
        months_stored=len(by_month),
        last_month_documents=int(last["documents"]) if last else None,
        last_3_documents=d3,
        months_last_3=n3,
        last_12_documents=d12,
        months_last_12=n12,
        filers=int(last["filers"]) if last else None,
        filers_sampled=bool(last["filers_sampled"]) if last else False,
        first_month=first,
        first_at_start=first is not None and first == earliest,
    )


def _table_order(r: TermRow) -> tuple[bool, bool, int, str]:
    """Last-month documents, most first; a counted term with nothing stored in that month after them, and a
    term "not counted yet" last of all (ties by term)."""
    return (
        not r.counted,
        r.last_month_documents is None,
        -(r.last_month_documents or 0),
        r.term.casefold(),
    )


# --- the Narrative Index overlay --------------------------------------------------------------------------


@dataclass(frozen=True)
class FlaggedName:
    """A name left out of the card's line: its data flags reach its price readings at the as-of."""

    ticker: str
    reason: str


@dataclass(frozen=True)
class Overlay:
    dates: tuple[date, ...]  # the sessions both lines are drawn on, oldest first
    card: (
        tuple[float, ...] | None
    )  # one per date, 100 at the first; None = no line (see card_reason)
    spy: tuple[float, ...] | None
    card_reason: str | None
    spy_reason: str | None
    session_source: SessionSource
    names: int  # companies on the card (each once) + rows with no resolved security
    priced: int  # of those, names that contributed at least one daily return to the line
    unresolved: int
    silent_left_out: int  # Context and Bench rows left out
    flagged: tuple[FlaggedName, ...]  # names left out for a flagged price history, card order


def _roster(
    rows: Sequence[Membership],
) -> tuple[list[tuple[UUID, str]], list[str], int]:
    """One card's rows -> ``(companies, unresolved tickers, silent rows left out)``: each resolved company ONCE
    (first-seen ticker, first-seen order), Context and Bench left out, each unresolved ticker once.
    """
    counted = counted_rows(rows, include_silent=False)
    seen: dict[UUID, str] = {}
    for m in counted.rows:
        assert m.security_id is not None
        seen.setdefault(m.security_id, m.ticker)
    unresolved: list[str] = []
    for m in rows:
        if m.security_id is None and not m.silent and m.ticker not in unresolved:
            unresolved.append(m.ticker)
    return list(seen.items()), unresolved, counted.silent_rows_left_out


def _rebased(levels: Sequence[float], start: int) -> tuple[float, ...]:
    base = levels[start]
    return tuple(v / base * BASE for v in levels[start:])


def overlay(
    memberships: Sequence[Membership],
    closes: Mapping[UUID, Mapping[date, float]],
    spy_closes: Mapping[date, float],
    flagged: Mapping[UUID, str],
    *,
    asof: date,
    axis_start: date,
) -> Overlay:
    """The card's equal-weighted line and SPY over the axis (see the module docstring). ``closes`` = each
    company's ``{day: close}`` up to the as-of; ``spy_closes`` = SPY's (empty = no tape); ``flagged`` = the
    companies whose data flags reach their price readings, each with its reason: their bars are never read.
    """
    companies, unresolved, silent_out = _roster(memberships)
    read = [sid for sid, _ in companies if sid not in flagged]
    sessions, source = session_calendar(
        spy_closes.keys(), (d for sid in read for d in closes.get(sid, {})), asof, SESSION_CAP
    )
    # the base session is the last one before the axis starts, so the first month's whole move is in the line
    base = max(0, bisect_left(sessions, axis_start) - 1)
    sessions = sessions[base:]
    n = len(sessions)
    no_bars: tuple[float | None, ...] = (None,) * n
    members = [
        aligned(closes.get(sid, {}), sessions) if sid in read else no_bars for sid, _ in companies
    ]
    card_chain = chain(members, n)
    card_start = line_start(card_chain)
    spy_series = aligned(spy_closes, sessions)
    has_spy = any(c is not None for c in spy_series)
    spy_chain = chain([spy_series], n) if has_spy else None
    spy_start = line_start(spy_chain)

    starts = [s for s in (card_start, spy_start) if s is not None]
    start = max(starts) if starts else None
    card_line = (
        _rebased(card_chain.levels, start) if card_start is not None and start is not None else None
    )
    spy_line = (
        _rebased(spy_chain.levels, start)
        if spy_chain is not None and spy_start is not None and start is not None
        else None
    )

    if card_line is not None:
        card_reason = None
    elif not companies and not unresolved:
        card_reason = "no names on the card's roster"
    elif not read:
        card_reason = "every name is left out or has no resolved security"
    else:
        card_reason = "no priced name in the window"
    if spy_line is not None:
        spy_reason = None
    elif not has_spy:
        spy_reason = f"no {BENCHMARK} tape at this as-of"
    else:
        spy_reason = f"no {BENCHMARK} bars in this window"

    return Overlay(
        dates=tuple(sessions[start:]) if start is not None else (),
        card=card_line,
        spy=spy_line,
        card_reason=card_reason,
        spy_reason=spy_reason,
        session_source=source,
        names=len(companies) + len(unresolved),
        priced=sum(1 for contributed in card_chain.contributed if contributed),
        unresolved=len(unresolved),
        silent_left_out=silent_out,
        flagged=tuple(FlaggedName(t, flagged[sid]) for sid, t in companies if sid in flagged),
    )


# --- the page -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Counts:
    """The work the quiet line shows."""

    signal_terms: int  # the card's current SIGNAL terms
    terms_counted: int  # of those, terms with at least one stored month on the axis
    terms_not_counted: int  # ... and those with none: "not counted yet"
    broad_terms_left_out: int
    retired_terms: int  # stored terms no longer in the card's term set (ignored by the curve)
    months_total: int
    months_counted: int  # axis months with a stored count for at least one term
    months_complete: int  # ... for every current term
    months_sampled: int  # counted months whose filers read "at least"
    months_read_early: int  # counted months whose count was read before the month closed
    months_not_closed: int  # axis months that had not closed at the as-of (the as-of's own month)
    last_counted_month: date | None


@dataclass(frozen=True)
class Adoption:
    asof: date
    thesis_id: UUID
    thesis_name: str
    months: tuple[MonthPoint, ...]  # the axis, oldest first
    terms: tuple[TermRow, ...]  # the table, sorted by last-month documents
    overlay: Overlay
    counts: Counts


def narrative_adoption(
    *,
    thesis_id: UUID,
    thesis_name: str,
    asof: date,
    term_set: Iterable[Mapping[str, Any]],
    rows: Iterable[Mapping[str, Any]],
    roster: Sequence[Membership],
    closes: Mapping[UUID, Mapping[date, float]],
    spy_closes: Mapping[date, float],
    flagged: Mapping[UUID, str],
    months: int = MONTHS,
) -> Adoption:
    """The page for one card at the as-of. ``rows`` = the card's stored term counts as the point-in-time view
    returns them (``PointInTimeData.term_mentions``: the newest version of each (term, month) public by the
    as-of)."""
    terms, broad = card_terms(term_set)
    axis = month_window(asof, months)
    on_axis = set(axis)
    by_term: dict[str, dict[date, Mapping[str, Any]]] = {t: {} for t in terms}
    retired: set[str] = set()
    for row in rows:
        term = row["term"]
        if term not in by_term:
            retired.add(term)
        elif row["month"] in on_axis:
            by_term[term][row["month"]] = row

    points = tuple(
        _month_point(m, asof, [(t, by_term[t][m]) for t in terms if m in by_term[t]]) for m in axis
    )
    last_idx = max((i for i, p in enumerate(points) if p.counted), default=None)
    table = sorted((_term_row(t, by_term[t], axis, last_idx) for t in terms), key=_table_order)
    counted_points = [p for p in points if p.counted]
    counts = Counts(
        signal_terms=len(terms),
        terms_counted=sum(1 for r in table if r.counted),
        terms_not_counted=sum(1 for r in table if not r.counted),
        broad_terms_left_out=broad,
        retired_terms=len(retired),
        months_total=len(axis),
        months_counted=len(counted_points),
        months_complete=sum(1 for p in counted_points if p.terms_counted == len(terms)),
        months_sampled=sum(1 for p in counted_points if p.filers_sampled),
        months_read_early=sum(1 for p in counted_points if p.read_early),
        months_not_closed=sum(1 for p in points if not p.closed),
        last_counted_month=points[last_idx].month if last_idx is not None else None,
    )
    return Adoption(
        asof=asof,
        thesis_id=thesis_id,
        thesis_name=thesis_name,
        months=points,
        terms=tuple(table),
        overlay=overlay(roster, closes, spy_closes, flagged, asof=asof, axis_start=axis[0]),
        counts=counts,
    )


# --- the last run of the job (its artifact) -----------------------------------------------------------------


@dataclass(frozen=True)
class RunView:
    """The newest applied run of ``pipeline.term_mentions``, as its artifact records it: when it finished,
    which months it refreshed and what it could not read. The run is the job's (every card), so the card's own
    share of its failed and deferred months is stated beside the totals."""

    schedule: str  # "weekly" (re-reads the last two months) or "backfill" (the whole window)
    run_kind: str  # "cron" or "manual"
    status: str
    finished_on: date | None  # market time
    months_from: str  # "2026-09"
    months_to: str
    requests: int
    failed_requests: int
    deferred_months: int
    card_failed_requests: int  # failed requests whose cards include this one
    card_deferred_months: int


def _prior_month(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    return f"{y - 1}-12" if m == 1 else f"{y}-{m - 1:02d}"


def run_identity(doc: Mapping[str, Any] | None) -> tuple[Any, ...] | None:
    """What makes a run THE newest one (its start, status and finish): the cache key's third part, so a new
    weekly run is a new read."""
    if not doc:
        return None
    return (doc.get("started_at"), doc.get("status"), doc.get("finished_at"))


def run_summary(doc: Mapping[str, Any] | None, thesis_id: UUID, thesis_name: str) -> RunView | None:
    """The artifact -> ``RunView`` (``None`` = no applied run on record). A failed request names its cards by
    name, a deferred one by id: the card's share is matched that way."""
    if not doc:
        return None
    window = doc.get("window") or ["", ""]
    schedule = str(doc.get("schedule") or "")
    counts = doc.get("counts") or {}
    last = str(window[-1])
    first = _prior_month(last) if schedule == "weekly" and last else str(window[0])
    finished = doc.get("finished_at") or doc.get("started_at")
    finished_on: date | None = None
    if isinstance(finished, str):
        try:
            finished_on = datetime.fromisoformat(finished).astimezone(market_tz()).date()
        except ValueError:
            finished_on = None
    deferred = [d for d in doc.get("deferred") or [] if str(d.get("thesis_id")) == str(thesis_id)]
    failed = [f for f in doc.get("failed") or [] if thesis_name in (f.get("cards") or [])]
    return RunView(
        schedule=schedule,
        run_kind=str(doc.get("run_kind") or ""),
        status=str(doc.get("status") or ""),
        finished_on=finished_on,
        months_from=first,
        months_to=last,
        requests=int(counts.get("requests") or 0),
        failed_requests=int(counts.get("failed_requests") or 0),
        deferred_months=int(counts.get("deferred_rows") or 0),
        card_failed_requests=len(failed),
        card_deferred_months=sum(int(d.get("n") or 0) for d in deferred),
    )
