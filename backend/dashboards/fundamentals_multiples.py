"""Fundamentals Multiples (``docs/dashboards/fundamentals-multiples.md``): ONE card's revenue series, name by name
and link by link. Each name is a small chart of its last quarterly revenue prints, each print placed by its period
end and DATED by the filing that made it public; each link (and the card) carries the median year-over-year change
of its counting names above the row. Follows the as-of dial.

PURE: the router reads the card's roster as of the as-of (``thesis_repo.get_asof``, today's roster standing in
before its first snapshot) and, for each resolved company, the as-of ``fundamentals_facts`` through the same
point-in-time view the Cockpit builds (the request's one ``known_at, clock = serve_view(asof)`` pair, invariant #1),
and hands them to ``fundamentals_multiples(...)``, which windows, dates and aggregates them. The whole page is
cached per (card, clock, as-of) (``dashboards/cache.py``); the Context/Bench toggle is client side. Display only:
the revenue-acceleration detector reads the same series, and this page only DRAWS it. Nothing here feeds a call,
scores an idea or writes anything (rule 1).

The rules, in one place:
- **The series.** ``fundamentals_facts`` rows with ``metric_key == "revenue"`` (the as-of read: the latest version
  per period end, an active data-quality set-aside already falling back to the version it superseded), on or
  before the as-of by period end. A row whose own filing date (``valid_from``) is after the as-of cannot occur on
  a well-formed clock; one is dropped here, never drawn (#1).
- **The axis.** Calendar quarters (fiscal calendars differ: two names' "Q2" may be different months, so a bar
  says its period end on hover). The card's axis is the ``QUARTERS`` (twelve) calendar quarters ending at the
  newest quarter any roster name's latest print falls in, Context and Bench included (the toggle is client side,
  so the axis must not move with it). A name's bars are its period ends inside that axis.
- **The dots.** A bar's year-over-year change is against the row for the same period end one year earlier (within
  ``filings.YOY_TOLERANCE_DAYS``, ``filings.prior_year_row``, the Filing Stream's rule) from the SAME as-of read,
  the prior row needing no bar of its own. No prior row, or a prior of zero or less: no dot. Two bars in one
  calendar quarter (a fiscal-year change): the later period end is that quarter's dot.
- **The medians.** Per calendar quarter, the median of the dots of the row's counting names that have one; drawn
  only where at least two names contribute (a row with exactly one counting name shows that name's dots as the
  line). Equal weight, no score. The card line is the same over every counting name ONCE (a company in two links
  counts once). Context and Bench names are NEVER in a median: they have no median row of their own and no
  counting name is ever one of them.
- **The names.** Every roster company is drawn, each ONCE per row it sits in (by ``security_id``; a row with no
  resolved security by its ticker), in roster order. A name with no revenue series, a name whose series ends before
  the axis and a name with no resolved security draw an empty frame and are counted, never dropped. A name with
  fewer than ``THIN_BELOW`` bars is ``thin``: drawn as is and marked.
- **The rows.** The card's real links in chain order (``thesis.segments`` as loaded; a real link is always a row,
  even with nobody in it), then a roster link that is not on the card today, then "No link", then Context, then
  Bench (the last three only when somebody sits in them).
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

from dashboards.baskets import Membership
from dashboards.filings import filing_index_url, prior_year_row
from domain.thesis import SILENT_LABELS, Segment

# the fact_fundamentals metric_key the revenue ingest writes (``ingest.fundamentals.REVENUE_METRIC``), kept a
# literal here as ``signals/revenue_acceleration.py`` does: a dashboard imports no writer module
REVENUE_METRIC = "revenue"
# the card's axis: twelve calendar quarters
QUARTERS = 12
# a name with fewer bars than this is drawn and marked thin
THIN_BELOW = 5
# a median needs this many contributing names (a row with exactly one counting name draws that name's dots)
MIN_MEDIAN_NAMES = 2
NO_LINK = "No link"

RowKind = Literal["link", "old_link", "no_link", "silent"]
CompanyState = Literal["series", "thin", "stale", "none", "unresolved"]


# --- the inputs ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CompanyRead:
    """What the router reads about one resolved company: its master ``name`` and SEC ``cik`` (the EDGAR link's),
    its as-of ``fundamentals_facts`` rows (any metric; this page keeps ``revenue``) and ``set_aside`` = how many
    of its revenue rows an active data-quality set-aside covers (read only)."""

    name: str | None = None
    cik: str | None = None
    rows: tuple[Mapping[str, Any], ...] = ()
    set_aside: int = 0


@dataclass(frozen=True)
class MultiplesInputs:
    """Everything the heavy read produces for one card: cached (as the built page) per (card, clock, as-of)."""

    thesis_id: UUID
    thesis_name: str
    segments: tuple[
        Segment, ...
    ]  # the chain as loaded: today's links, in order, Context and Bench last
    roster: tuple[Membership, ...]  # the card's rows at the as-of (``baskets.memberships_of``)
    facts: Mapping[UUID, CompanyRead]  # each resolved company's reads
    roster_fallback: bool = False  # no roster snapshot by the as-of: today's roster stands in


# --- small pure helpers --------------------------------------------------------------------------------------------


def _num(v: Any) -> float | None:
    """A stored number as a float; ``None``, a non-number and a non-finite value stay ``None``."""
    if v is None or isinstance(v, bool):
        return None
    try:
        out = float(v)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _day(v: Any) -> date | None:
    if isinstance(v, datetime):
        return v.date()
    return v if isinstance(v, date) else None


def quarter_of(d: date) -> str:
    """The calendar quarter of a date, sortable as text (``2026-Q2``)."""
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def quarter_index(d: date) -> int:
    """A running count of calendar quarters (consecutive quarters differ by one)."""
    return d.year * 4 + (d.month - 1) // 3


def quarter_label(index: int) -> str:
    """The ``quarter_of`` text for a ``quarter_index`` value."""
    return f"{index // 4}-Q{index % 4 + 1}"


def build_axis(last_period_ends: Iterable[date]) -> tuple[str, ...]:
    """The card's axis: ``QUARTERS`` calendar quarters ending at the newest quarter any latest print falls in
    (empty when no name has a print)."""
    ends = [quarter_index(d) for d in last_period_ends]
    if not ends:
        return ()
    last = max(ends)
    return tuple(quarter_label(i) for i in range(last - QUARTERS + 1, last + 1))


def revenue_rows(facts: Iterable[Mapping[str, Any]], asof: date) -> list[Mapping[str, Any]]:
    """The company's revenue periods on or before the as-of, ascending by period end: ``metric_key`` revenue, a
    period end and a number and a filing date (``valid_from``) all stated, and neither after the as-of (a row
    filed after it cannot occur on a well-formed clock, and is dropped, never drawn: #1)."""
    out = []
    for f in facts:
        if f.get("metric_key") != REVENUE_METRIC:
            continue
        end, filed, value = (
            _day(f.get("period_end")),
            _day(f.get("valid_from")),
            _num(f.get("value")),
        )
        if end is None or filed is None or value is None or end > asof or filed > asof:
            continue
        out.append(f)
    out.sort(
        key=lambda f: (_day(f.get("period_end")) or date.min, _day(f.get("valid_from")) or date.min)
    )
    return out


def yoy_pct(row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> float | None:
    """The change from the same period a year earlier, in percent, ``None`` when that row is not in the same read
    or is zero or less (``filings.prior_year_row``, the Filing Stream's rule)."""
    prior = prior_year_row(row, rows)
    now, then = _num(row.get("value")), (_num(prior.get("value")) if prior else None)
    if now is None or then is None or then <= 0:
        return None
    return (now / then - 1.0) * 100.0


# --- the output ----------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Bar:
    """One quarterly revenue print: placed by its ``period_end`` (``quarter`` = its calendar quarter), DATED by
    the filing that made it public (``filed``, the picked version's ``valid_from``; ``accession`` + ``url`` = the
    filing and its EDGAR index). ``first_knowable`` = the earlier date the value was first public, set only when
    it is earlier than ``filed`` (a re-report), with its accession. ``yoy`` = percent change against the same
    period a year earlier (``prior_period_end``), ``None`` when there is none."""

    quarter: str
    period_end: date
    value: float
    basis: str
    fiscal_period: str | None
    fiscal_year: int | None
    filed: date
    accession: str | None
    url: str | None
    first_knowable: date | None
    first_knowable_accession: str | None
    yoy: float | None
    prior_period_end: date | None


@dataclass(frozen=True)
class LastPrint:
    """A name's newest print by period end (even when it is older than the axis): its calendar quarter, period
    end, filing date, ``days_before`` the as-of it was filed and its year-over-year change."""

    quarter: str
    period_end: date
    filed: date
    days_before: int
    yoy: float | None


@dataclass(frozen=True)
class Company:
    """One roster company. ``state``: ``series`` (five or more bars), ``thin`` (one to four), ``stale`` (a series
    whose newest print is before the axis: no bar), ``none`` (a resolved company with no revenue stored) or
    ``unresolved`` (a row with no resolved security). ``periods`` = its revenue period ends stored on or before the
    as-of. ``set_aside`` = its revenue rows an active set-aside covers."""

    company_key: str
    ticker: str
    name: str | None
    security_id: UUID | None
    silent: bool
    state: CompanyState
    periods: int
    bars: tuple[Bar, ...]
    last_print: LastPrint | None
    set_aside: int


@dataclass(frozen=True)
class MedianPoint:
    """One axis quarter of a median line: ``median`` (percent; ``None`` = not drawn) and ``n`` = the names that
    had a dot that quarter."""

    quarter: str
    median: float | None
    n: int


@dataclass(frozen=True)
class Median:
    """A median line over the axis. ``counting`` = the counting names the line is over (the denominator); each
    point's ``n`` is how many of them contributed."""

    counting: int
    points: tuple[MedianPoint, ...]


@dataclass(frozen=True)
class Row:
    """A row of names: a real link (``link``), a roster link that is not on the card today (``old_link``), the
    rows with no link (``no_link``) or the Context / Bench names (``silent``, label says which; no median).
    ``companies`` = the company keys in roster order."""

    key: str
    kind: RowKind
    label: str
    descriptor: str | None
    companies: tuple[str, ...]
    median: Median | None


@dataclass(frozen=True)
class Counts:
    """The work shown on the page's quiet line. ``companies`` = distinct roster companies, of which ``counting``
    sit in at least one real, old or No-link row and ``silent_companies`` only in Context or Bench.
    ``with_series`` / ``without_series`` split them by whether any revenue period end is stored; ``thin`` and
    ``stale`` are of the ones with a series (``stale``: the newest print is before the axis). ``set_aside`` =
    revenue rows covered by an active set-aside on the card's names."""

    links_today: int
    old_links: int
    roster_rows: int
    resolved_rows: int
    unresolved_rows: int
    silent_rows: int
    companies: int
    counting: int
    silent_companies: int
    with_series: int
    without_series: int
    thin: int
    stale: int
    set_aside: int


@dataclass(frozen=True)
class Multiples:
    asof: date
    thesis_id: UUID
    thesis_name: str
    roster_fallback: bool
    axis: tuple[str, ...]
    card_line: Median
    rows: tuple[Row, ...]
    companies: tuple[Company, ...]
    counts: Counts


# --- the page ------------------------------------------------------------------------------------------------------


def _company_key(m: Membership) -> str:
    return str(m.security_id) if m.security_id is not None else f"ticker:{m.ticker.strip().upper()}"


def _bar(row: Mapping[str, Any], rows: Sequence[Mapping[str, Any]], cik: str | None) -> Bar:
    end, filed = _day(row.get("period_end")), _day(row.get("valid_from"))
    assert end is not None and filed is not None  # revenue_rows kept only rows that state both
    knowable = _day(row.get("knowable_from"))
    re_reported = knowable is not None and knowable < filed
    prior = prior_year_row(row, rows)
    accession = str(row.get("accession") or "") or None
    first = row.get("knowable_accession")
    fiscal_year = row.get("fiscal_year")
    return Bar(
        quarter=quarter_of(end),
        period_end=end,
        value=_num(row.get("value")) or 0.0,
        basis=str(row.get("basis") or "") or "basis not stated",
        fiscal_period=str(row["fiscal_period"]) if row.get("fiscal_period") else None,
        fiscal_year=int(fiscal_year) if isinstance(fiscal_year, int | float) else None,
        filed=filed,
        accession=accession,
        url=filing_index_url(cik, accession),
        first_knowable=knowable if re_reported else None,
        first_knowable_accession=(str(first) if first else None) if re_reported else None,
        yoy=yoy_pct(row, rows),
        prior_period_end=_day(prior.get("period_end")) if prior else None,
    )


def _company(
    m: Membership,
    silent: bool,
    read: CompanyRead | None,
    rows: Sequence[Mapping[str, Any]],
    axis: Sequence[str],
    asof: date,
) -> Company:
    """One roster company's chart data: its bars inside the axis, its newest print and its state. ``rows`` =
    its ``revenue_rows``."""
    ck = _company_key(m)
    if m.security_id is None:
        return Company(ck, m.ticker, None, None, silent, "unresolved", 0, (), None, 0)
    read = read or CompanyRead()
    on_axis = set(axis)
    bars = tuple(
        _bar(r, rows, read.cik)
        for r in rows
        if quarter_of(_day(r.get("period_end")) or date.min) in on_axis
    )
    last_print = None
    if rows:
        last = _bar(rows[-1], rows, read.cik)
        last_print = LastPrint(
            last.quarter, last.period_end, last.filed, (asof - last.filed).days, last.yoy
        )
    state: CompanyState
    if not rows:
        state = "none"
    elif not bars:
        state = "stale"
    else:
        state = "thin" if len(bars) < THIN_BELOW else "series"
    return Company(
        ck,
        m.ticker,
        read.name,
        m.security_id,
        silent,
        state,
        len(rows),
        bars,
        last_print,
        read.set_aside,
    )


def quarter_dots(company: Company) -> dict[str, float]:
    """A company's dot per axis quarter (percent). Two bars in one quarter: the later period end decides, a
    missing dot included."""
    out: dict[str, float] = {}
    for b in company.bars:  # ascending by period end
        if b.yoy is None:
            out.pop(b.quarter, None)
        else:
            out[b.quarter] = b.yoy
    return out


def median_line(companies: Sequence[Company], axis: Sequence[str]) -> Median:
    """The median of the companies' dots per axis quarter: drawn (``median`` set) only where at least
    ``MIN_MEDIAN_NAMES`` names have one, or where the only counting name has one. Equal weight."""
    need = 1 if len(companies) == 1 else MIN_MEDIAN_NAMES
    dots = [quarter_dots(c) for c in companies]
    points = []
    for q in axis:
        vals = [d[q] for d in dots if q in d]
        points.append(
            MedianPoint(q, statistics.median(vals) if len(vals) >= need else None, len(vals))
        )
    return Median(len(companies), tuple(points))


def fundamentals_multiples(inputs: MultiplesInputs, *, asof: date) -> Multiples:
    """Window, date and aggregate every roster company's revenue series: the axis, the charts, the medians and the
    counts."""
    roster = list(inputs.roster)
    real = list(dict.fromkeys(s.label for s in inputs.segments if s.label not in SILENT_LABELS))
    real_set = set(real)
    descriptor = {s.label: s.descriptor for s in inputs.segments}

    # a company is silent only when EVERY row it holds is Context or Bench (the promote refuses the mix; a legacy
    # mixed company counts as a voting one, listed under its voting rows only)
    by_company: dict[str, list[Membership]] = {}
    for m in roster:
        by_company.setdefault(_company_key(m), []).append(m)
    silent_of = {ck: all(m.silent for m in ms) for ck, ms in by_company.items()}

    # the axis first (it needs every company's newest print), then each company against it
    newest: list[date] = []
    revenue_of: dict[str, list[Mapping[str, Any]]] = {}
    for ck, ms in by_company.items():
        sid = ms[0].security_id
        read = inputs.facts.get(sid) if sid is not None else None
        revenue_of[ck] = revenue_rows(read.rows, asof) if read else []
        if revenue_of[ck]:
            newest.append(_day(revenue_of[ck][-1].get("period_end")) or date.min)
    axis = build_axis(newest)

    companies = {
        ck: _company(
            ms[0],
            silent_of[ck],
            inputs.facts.get(ms[0].security_id) if ms[0].security_id else None,
            revenue_of[ck],
            axis,
            asof,
        )
        for ck, ms in by_company.items()
    }

    def row_of(segment: str | None) -> tuple[str, RowKind, str]:
        if segment is None:
            return "no_link", "no_link", NO_LINK
        if segment in SILENT_LABELS:
            return f"silent:{segment}", "silent", segment
        if segment in real_set:
            return f"link:{segment}", "link", segment
        return f"old:{segment}", "old_link", segment

    # a real link is always a row; the others exist once somebody sits in them
    members: dict[str, tuple[RowKind, str, list[str]]] = {
        f"link:{s}": ("link", s, []) for s in real
    }
    for m in roster:  # roster order, so a row's names keep it
        ck = _company_key(m)
        if m.silent and not silent_of[ck]:
            continue
        key, kind, label = row_of(m.segment)
        names = members.setdefault(key, (kind, label, []))[2]
        if ck not in names:
            names.append(ck)

    def rank(kind: RowKind, label: str) -> tuple[int, int]:
        if kind == "link":
            return 0, real.index(label)
        if kind == "old_link":
            return 1, 0
        if kind == "no_link":
            return 2, 0
        return 3, SILENT_LABELS.index(label)

    rows = tuple(
        Row(
            key,
            kind,
            label,
            descriptor.get(label) if kind == "link" else None,
            tuple(names),
            None if kind == "silent" else median_line([companies[ck] for ck in names], axis),
        )
        for key, (kind, label, names) in sorted(
            members.items(), key=lambda kv: rank(kv[1][0], kv[1][1])
        )
    )
    counting = [c for c in companies.values() if not c.silent]
    with_series = [c for c in companies.values() if c.periods > 0]
    counts = Counts(
        links_today=len(real),
        old_links=sum(1 for r in rows if r.kind == "old_link"),
        roster_rows=len(roster),
        resolved_rows=sum(1 for m in roster if m.security_id is not None),
        unresolved_rows=sum(1 for m in roster if m.security_id is None),
        silent_rows=sum(1 for m in roster if m.silent),
        companies=len(companies),
        counting=len(counting),
        silent_companies=len(companies) - len(counting),
        with_series=len(with_series),
        without_series=len(companies) - len(with_series),
        thin=sum(1 for c in companies.values() if c.state == "thin"),
        stale=sum(1 for c in companies.values() if c.state == "stale"),
        set_aside=sum(c.set_aside for c in companies.values()),
    )
    return Multiples(
        asof=asof,
        thesis_id=inputs.thesis_id,
        thesis_name=inputs.thesis_name,
        roster_fallback=inputs.roster_fallback,
        axis=axis,
        card_line=median_line(counting, axis),
        rows=rows,
        companies=tuple(companies.values()),
        counts=counts,
    )
