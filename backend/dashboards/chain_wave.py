"""Chain Wave (``docs/dashboards/chain-wave.md``): ONE card's links in its chain order down the side, calendar weeks
across, each cell that link's weekly return minus SPY's, so a move traveling along the chain shows as a diagonal.
Below the chain: the card's counting names as one row, and one row per context role the card has set (payers
first). Follows the as-of dial.

PURE: the router reads the card's roster as of the as-of (``thesis_repo.get_asof``, today's roster standing in
before its first snapshot), every company's closes and SPY's through the point-in-time view and the price flags,
and hands them to ``wave_inputs(...)``, which builds every row's weekly cells for all ``WEEKS`` weeks ONCE (cached
per card and as-of, ``dashboards/cache.py``), Context and Bench included. ``chain_wave(...)`` then slices the
window and applies the Context/Bench toggle per request. Display only: nothing here feeds a call, scores an idea
or writes anything (rule 1).

The rules, in one place. This is our own simple measure, stated here and on the page; not a published method.
- **The rows.** The card's real links in its chain order (``thesis.segments`` as loaded: today's chain,
  Discovered wherever it sits), each link = its companies on the roster at the as-of, each ONCE (by
  ``security_id``; a row with no resolved security by its ticker). A roster row whose link is not on the card
  today gets its own row under the old label, after the real links ("not a link today"); rows with no link make
  one "No link" row after those. Context and Bench are out of the chain unless ``include_silent``, which adds them
  as the last two chain rows. Then **Counting names** (every company that counts toward the call: every row not
  in Context or Bench, each once) and one row per **context role** the card has set, in ``ROLE_ORDER`` (payers
  first), each company once, Context and Bench names included whatever the toggle says (that is where the roles
  live). Every row is shown: there is no name floor.
- **The weeks.** Calendar weeks, Monday to Sunday, on the shared session calendar (``dashboards/series.py``:
  SPY's bar dates, plus any company bar after SPY's last; with no SPY tape, the companies'), read from the Monday
  ``WEEKS`` weeks before the as-of's week. A week's close = its last session in the calendar. The last column is
  the week holding the as-of ("to date" when the as-of falls before that week's Friday). ``WEEKS`` columns are
  built; a window is the last 13, 26 (default) or 52 of them.
- **The line.** Each row's equal-weighted line, rebalanced daily, through the shared chain (``series.chain``);
  SPY's runs through the same chain (one member).
- **The cell** = the row's weekly return minus SPY's, in percentage points. A weekly return = the line's level at
  this week's close ÷ its level at last week's close (the last session before this week's Monday) − 1. "—"
  (``gap``) when the week has no session (``no_session``), when the line had not started by last week's close
  (``not_started``: ``series.line_start``), when no member contributed a daily return that week (``no_return``: a
  fully flat week is not a 0% week), or when SPY has no return that week (``no_spy``).
- **Priced and thin.** A name is priced in a window when it contributed a daily return in one of the window's
  weeks. A row with fewer than ``THIN_BELOW`` priced names is ``thin`` (drawn at reduced strength, never hidden).
- **A flagged price history.** A company whose data flags reach its price readings at the as-of
  (``dashboards.baskets.price_flags``) never contributes a return and its bars never shape the calendar. It still
  counts in its rows' names, never in priced, and is named with its reason (the Rotation Quadrant's rule).
- **The readout.** Only when the card has payers: each of the payers' and the counting names' lines from the close
  before the last ``READOUT_WEEKS`` week columns to the last session, minus SPY's over the same span, in pp.
  Numbers only.
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from dashboards.baskets import (  # noqa: F401  (CardChoice / card_choices: the picker, moved to baskets)
    CardChoice,
    FlaggedCompany,
    Membership,
    card_choices,
    flagged_in_view,
    link_count,
)
from dashboards.series import (
    Chain,
    SessionSource,
    aligned,
    chain,
    line_start,
    session_calendar,
)
from domain.thesis import SILENT_LABELS

# the week columns built (and cached); a window is the last WINDOWS[i] of them
WEEKS = 52
WINDOWS: tuple[int, ...] = (13, 26, 52)
DEFAULT_WEEKS = 26
# a row with fewer priced names than this is thin (drawn at reduced strength)
THIN_BELOW = 3
# the readout's span: the last this-many week columns
READOUT_WEEKS = 4
BENCHMARK = "SPY"
# the role rows, in this order (payers first): every ``domain.enums.ContextRole`` value
ROLE_ORDER: tuple[str, ...] = (
    "payer",
    "counter_case",
    "direct_read",
    "participant",
    "financier",
    "benchmark",
)
NO_LINK = "No link"
COUNTING = "Counting names"
FRIDAY = 4  # ``date.weekday()``

RowKind = Literal["link", "old_link", "no_link", "silent", "counting", "role"]
Gap = Literal["no_session", "not_started", "no_return", "no_spy"]


# --- the weeks ----------------------------------------------------------------------------------------------


def week_monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def read_start(asof: date, weeks: int = WEEKS) -> date:
    """The Monday of the week before the first column: the first column's base close lives in it."""
    return week_monday(asof) - timedelta(weeks=weeks)


@dataclass(frozen=True)
class Week:
    """One week column: its Monday, its first and last session in the calendar (``close``: its close) and how
    many sessions it holds (0 = none; every cell "—"). ``to_date`` = the as-of's week with the as-of before its
    Friday."""

    monday: date
    first_session: date | None
    close: date | None
    sessions: int
    to_date: bool


@dataclass(frozen=True)
class WeekSpan:
    """A week column on the session indices: ``base`` = last week's close (the last session before this week's
    Monday; ``None`` = none in the calendar), ``end`` = this week's close (``None`` = no session this week).
    """

    week: Week
    base: int | None
    end: int | None


def week_spans(sessions: Sequence[date], asof: date, weeks: int = WEEKS) -> tuple[WeekSpan, ...]:
    """The ``weeks`` week columns ending with the as-of's week, oldest first, on the sessions (sorted)."""
    last = week_monday(asof)
    out: list[WeekSpan] = []
    for k in range(weeks):
        monday = last - timedelta(weeks=weeks - 1 - k)
        lo = bisect_left(sessions, monday)
        hi = bisect_left(sessions, monday + timedelta(days=7))
        has = hi > lo
        out.append(
            WeekSpan(
                week=Week(
                    monday=monday,
                    first_session=sessions[lo] if has else None,
                    close=sessions[hi - 1] if has else None,
                    sessions=hi - lo,
                    to_date=k == weeks - 1 and asof.weekday() < FRIDAY,
                ),
                base=lo - 1 if lo > 0 else None,
                end=hi - 1 if has else None,
            )
        )
    return tuple(out)


# --- the cell ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """One row's week. ``diff`` = its return minus SPY's, in percentage points; ``ret`` / ``spy`` = the two weekly
    returns in percent (``None`` = no return); ``names`` = members contributing a daily return that week;
    ``gap`` = why ``diff`` is "—" (``None`` = it is shown). On the SPY row ``ret`` is SPY's own return and
    ``diff`` is ``None``."""

    diff: float | None
    ret: float | None
    spy: float | None
    names: int
    gap: Gap | None


def _pct(v: float | None) -> float | None:
    return None if v is None else round(v * 100.0, 4)


def stepped(ch: Chain, base: int | None, end: int | None) -> int:
    """Sessions in ``(base, end]`` the line stepped into (some member contributed a daily return)."""
    if end is None:
        return 0
    lo = 0 if base is None else base + 1
    return sum(1 for t in range(lo, end + 1) if ch.contributors[t] > 0)


def span_return(
    ch: Chain | None, start: int | None, base: int | None, end: int | None
) -> tuple[float | None, Gap | None]:
    """The line's return from session ``base`` to session ``end`` (``start`` = its ``line_start``), or why there
    is none: ``no_session`` (no end), ``not_started`` (no base, or the line had not started by it),
    ``no_return`` (no member contributed a daily return in between: a fully flat span is not a 0% span).
    """
    if end is None:
        return None, "no_session"
    if ch is None or base is None or start is None or start > base:
        return None, "not_started"
    if stepped(ch, base, end) == 0:
        return None, "no_return"
    return ch.levels[end] / ch.levels[base] - 1.0, None


def line_of(closes: Sequence[Sequence[float | None]], n: int) -> tuple[Chain, int | None]:
    ch = chain(closes, n)
    return ch, line_start(ch)


def spy_cells(spy: Chain | None, spans: Sequence[WeekSpan]) -> tuple[Cell, ...]:
    """SPY's own weekly return per week (``ret``; ``diff`` None)."""
    start = line_start(spy)
    out: list[Cell] = []
    for s in spans:
        ret, gap = span_return(spy, start, s.base, s.end)
        names = 1 if spy is not None and stepped(spy, s.base, s.end) else 0
        out.append(Cell(diff=None, ret=_pct(ret), spy=_pct(ret), names=names, gap=gap))
    return tuple(out)


def row_cells(
    ch: Chain,
    start: int | None,
    spy: Chain | None,
    spans: Sequence[WeekSpan],
    names: Sequence[int],
) -> tuple[Cell, ...]:
    """A line's weekly cells vs SPY; ``names[k]`` = its members contributing in week ``k``."""
    spy_start = line_start(spy)
    out: list[Cell] = []
    for k, s in enumerate(spans):
        ret, gap = span_return(ch, start, s.base, s.end)
        spy_ret, _ = span_return(spy, spy_start, s.base, s.end)
        if gap is None and spy_ret is None:
            gap = "no_spy"
        diff = None
        if gap is None and ret is not None and spy_ret is not None:
            diff = (ret - spy_ret) * 100.0
        out.append(
            Cell(
                diff=None if diff is None else round(diff, 4),
                ret=_pct(ret),
                spy=_pct(spy_ret),
                names=names[k],
                gap=gap,
            )
        )
    return tuple(out)


# --- the rows -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WaveMember:
    """One company on a row, each once. ``key`` = its ``security_id`` (``ticker:<T>`` for a row with no resolved
    security). ``cells`` = its own weekly return vs SPY (a one-name line), every week; empty when it is never
    read (``flag`` set: a flagged price history; or no resolved security)."""

    key: str
    ticker: str
    security_id: UUID | None
    flag: str | None
    cells: tuple[Cell, ...]


@dataclass(frozen=True)
class WaveRow:
    key: str
    kind: RowKind
    label: str  # the link, "No link", "Counting names", or the role's value (the page words its own label)
    role: str | None  # a role row's role
    members: tuple[WaveMember, ...]  # each company once, roster order
    cells: tuple[Cell, ...]  # every week


def _company_key(m: Membership) -> str:
    return str(m.security_id) if m.security_id is not None else f"ticker:{m.ticker.strip().upper()}"


def companies_once(rows: Iterable[Membership]) -> list[Membership]:
    """Each company's first row, in roster order (by ``security_id``; a row with no resolved security by its
    ticker)."""
    seen: dict[str, Membership] = {}
    for m in rows:
        seen.setdefault(_company_key(m), m)
    return list(seen.values())


def row_groups(
    memberships: Sequence[Membership], segments: Sequence[str]
) -> list[tuple[str, RowKind, str, str | None, list[Membership]]]:
    """Every row in page order, Context and Bench included: ``(key, kind, label, role, its rows)``. The chain
    (today's real links in order, then the roster's links that are not on the card today in roster order, then
    "No link", then Context and Bench), then Counting names, then one row per role set (``ROLE_ORDER``).
    """
    by_segment: dict[str | None, list[Membership]] = {}
    for m in memberships:
        by_segment.setdefault(m.segment, []).append(m)
    links = [s for s in dict.fromkeys(segments) if s not in SILENT_LABELS]
    out: list[tuple[str, RowKind, str, str | None, list[Membership]]] = [
        (f"link:{s}", "link", s, None, by_segment.get(s, [])) for s in links
    ]
    for s, ms in by_segment.items():
        if s is not None and s not in SILENT_LABELS and s not in links:
            out.append((f"old:{s}", "old_link", s, None, ms))
    if None in by_segment:
        out.append(("no_link", "no_link", NO_LINK, None, by_segment[None]))
    for s in SILENT_LABELS:
        out.append((f"silent:{s}", "silent", s, None, by_segment.get(s, [])))
    out.append(("counting", "counting", COUNTING, None, [m for m in memberships if not m.silent]))
    for role in ROLE_ORDER:
        ms = [m for m in memberships if m.context_role == role]
        if ms:
            out.append((f"role:{role}", "role", role, role, ms))
    return out


# --- the cached read: every row's weekly cells ---------------------------------------------------------------


@dataclass(frozen=True)
class Readout:
    """The last ``weeks`` week columns: from ``start`` (the close before them) to ``end`` (the last session), each
    line's return minus SPY's in pp (``None`` = "—")."""

    weeks: int
    start: date | None
    end: date | None
    payers: float | None
    counting: float | None


@dataclass(frozen=True)
class WaveInputs:
    """Everything the heavy read produces for one card: cached per (card, clock, as-of), every window and the
    toggle derived."""

    thesis_id: UUID
    thesis_name: str
    roster_fallback: bool  # no roster snapshot by the as-of: today's roster stands in
    links_today: int  # the card's real links today (Context and Bench not counted)
    basket_rows: int  # every roster row
    silent_rows: int  # of those, in Context or Bench
    unresolved_rows: int  # of those, with no resolved security (counted as names, never priced)
    sessions: tuple[date, ...]  # the sessions read, oldest first
    session_source: SessionSource
    spy_available: bool
    weeks: tuple[Week, ...]  # ``WEEKS`` columns, oldest first
    spy: tuple[Cell, ...]  # SPY's own weekly return
    rows: tuple[WaveRow, ...]  # every row in page order, Context and Bench included
    readout: Readout | None  # only when the card has payers
    flagged: tuple[FlaggedCompany, ...]  # the card's companies left out for a flagged price history


def wave_inputs(
    *,
    thesis_id: UUID,
    thesis_name: str,
    segments: Sequence[str],
    memberships: Sequence[Membership],
    closes: Mapping[UUID, Mapping[date, float]],
    spy: Mapping[date, float],
    asof: date,
    roster_fallback: bool = False,
    flagged: Mapping[UUID, str] | None = None,
) -> WaveInputs:
    """Every row's weekly cells for one card. ``segments`` = its chain order (link labels as loaded);
    ``memberships`` = its roster rows at the as-of (``baskets.memberships_of``); ``closes`` = each company's
    ``{day: close}`` up to the as-of; ``spy`` = SPY's (empty = no tape); ``flagged`` = the companies whose data
    flags reach their price readings, with the reason (``baskets.price_flags``): their closes are never read.
    """
    flags = dict(flagged or {})
    rows_in = list(memberships)
    resolved = {m.security_id for m in rows_in if m.security_id is not None}
    read = {sid for sid in resolved if sid not in flags}  # a flagged company's bars are never read

    lo = read_start(asof)
    sessions, source = session_calendar(
        (d for d in spy if d >= lo),
        (d for sid in read for d in closes.get(sid, {}) if d >= lo),
        asof,
        # an upper bound, never a cut: at most seven dates a week from the read's first Monday
        (WEEKS + 1) * 7,
    )
    n = len(sessions)
    spans = week_spans(sessions, asof)

    spy_closes = aligned(spy, sessions)
    spy_chain = chain([spy_closes], n) if any(c is not None for c in spy_closes) else None
    aligned_closes = {sid: aligned(closes.get(sid, {}), sessions) for sid in read}

    # each company's own weekly cells (a one-name line), computed once and shared by every row it sits on
    member_cells: dict[UUID, tuple[Cell, ...]] = {}
    for sid in sorted(read, key=str):
        ch, start = line_of([aligned_closes[sid]], n)
        names = [1 if stepped(ch, s.base, s.end) else 0 for s in spans]
        member_cells[sid] = row_cells(ch, start, spy_chain, spans, names)

    def member(m: Membership) -> WaveMember:
        sid = m.security_id
        return WaveMember(
            key=_company_key(m),
            ticker=m.ticker,
            security_id=sid,
            flag=flags.get(sid) if sid is not None else None,
            cells=member_cells.get(sid, ()) if sid is not None else (),
        )

    rows: list[WaveRow] = []
    lines: dict[str, tuple[Chain, int | None]] = {}
    for key, kind, label, role, ms in row_groups(rows_in, segments):
        members = tuple(member(m) for m in companies_once(ms))
        read_ids = [m.security_id for m in members if m.security_id is not None and m.cells]
        ch, start = line_of([aligned_closes[sid] for sid in read_ids], n)
        lines[key] = (ch, start)
        names = [
            sum(1 for sid in read_ids if member_cells[sid][k].names) for k in range(len(spans))
        ]
        rows.append(
            WaveRow(key, kind, label, role, members, row_cells(ch, start, spy_chain, spans, names))
        )

    readout = None
    if "role:payer" in lines:
        base = spans[-READOUT_WEEKS].base
        end = n - 1 if n else None

        def vs_spy(key: str) -> float | None:
            ch, start = lines[key]
            ret, _ = span_return(ch, start, base, end)
            spy_ret, _ = span_return(spy_chain, line_start(spy_chain), base, end)
            if ret is None or spy_ret is None:
                return None
            return round((ret - spy_ret) * 100.0, 4)

        readout = Readout(
            weeks=READOUT_WEEKS,
            start=sessions[base] if base is not None else None,
            end=sessions[end] if end is not None else None,
            payers=vs_spy("role:payer"),
            counting=vs_spy("counting"),
        )

    return WaveInputs(
        thesis_id=thesis_id,
        thesis_name=thesis_name,
        roster_fallback=roster_fallback,
        links_today=link_count(segments),
        basket_rows=len(rows_in),
        silent_rows=sum(1 for m in rows_in if m.silent),
        unresolved_rows=sum(1 for m in rows_in if m.security_id is None),
        sessions=sessions,
        session_source=source,
        spy_available=spy_chain is not None,
        weeks=tuple(s.week for s in spans),
        spy=spy_cells(spy_chain, spans),
        rows=tuple(rows),
        readout=readout,
        flagged=flagged_in_view([m for m in rows_in if m.security_id is not None], flags),
    )


# --- one view: a window and the toggle ----------------------------------------------------------------------


@dataclass(frozen=True)
class MemberView:
    key: str
    ticker: str
    security_id: UUID | None
    flag: str | None
    priced: bool  # contributed a daily return in one of the window's weeks
    cells: tuple[
        Cell, ...
    ]  # the window's weeks; empty when never read (flagged, or no resolved security)


@dataclass(frozen=True)
class RowView:
    key: str
    kind: RowKind
    label: str
    role: str | None
    names: int  # companies on the row, each once (flagged and unresolved ones included)
    priced: int  # of those, priced in the window (never a flagged or unresolved one)
    thin: bool  # fewer than ``THIN_BELOW`` priced names
    cells: tuple[Cell, ...]
    members: tuple[MemberView, ...]  # priced first, then not priced, then left out; by ticker


@dataclass(frozen=True)
class ChainWave:
    asof: date
    thesis_id: UUID
    thesis_name: str
    weeks: int
    include_silent: bool
    roster_fallback: bool
    links_today: int
    basket_rows: int
    silent_rows: int
    unresolved_rows: int
    sessions: int  # daily returns read
    first_session: date | None
    last_session: date | None
    session_source: SessionSource
    spy_available: bool
    week_columns: tuple[Week, ...]  # the window's weeks, oldest first
    spy: tuple[Cell, ...]
    rows: tuple[RowView, ...]
    readout: Readout | None
    flagged: tuple[FlaggedCompany, ...]
    roles_set: int  # role rows on the card


def _member_sort_key(m: MemberView) -> tuple:
    left_out = m.flag is not None or m.security_id is None
    return (left_out, not m.priced, m.ticker.casefold(), m.key)


def chain_wave(
    inputs: WaveInputs, *, asof: date, weeks: int = DEFAULT_WEEKS, include_silent: bool = False
) -> ChainWave:
    """The page for one window (the last ``weeks`` week columns) with Context and Bench in the chain only when
    ``include_silent``."""
    if weeks not in WINDOWS:
        raise ValueError(f"weeks must be one of {WINDOWS}, not {weeks}")
    w = min(weeks, len(inputs.weeks))
    cut = len(inputs.weeks) - w

    def view(r: WaveRow) -> RowView:
        members = tuple(
            sorted(
                (
                    MemberView(
                        key=m.key,
                        ticker=m.ticker,
                        security_id=m.security_id,
                        flag=m.flag,
                        priced=any(c.names for c in m.cells[cut:]),
                        cells=m.cells[cut:],
                    )
                    for m in r.members
                ),
                key=_member_sort_key,
            )
        )
        priced = sum(1 for m in members if m.priced)
        return RowView(
            key=r.key,
            kind=r.kind,
            label=r.label,
            role=r.role,
            names=len(members),
            priced=priced,
            thin=priced < THIN_BELOW,
            cells=r.cells[cut:],
            members=members,
        )

    rows = tuple(view(r) for r in inputs.rows if include_silent or r.kind != "silent")
    return ChainWave(
        asof=asof,
        thesis_id=inputs.thesis_id,
        thesis_name=inputs.thesis_name,
        weeks=w,
        include_silent=include_silent,
        roster_fallback=inputs.roster_fallback,
        links_today=inputs.links_today,
        basket_rows=inputs.basket_rows,
        silent_rows=inputs.silent_rows,
        unresolved_rows=inputs.unresolved_rows,
        sessions=max(0, len(inputs.sessions) - 1),
        first_session=inputs.sessions[0] if inputs.sessions else None,
        last_session=inputs.sessions[-1] if inputs.sessions else None,
        session_source=inputs.session_source,
        spy_available=inputs.spy_available,
        week_columns=inputs.weeks[cut:],
        spy=inputs.spy[cut:],
        rows=rows,
        readout=inputs.readout,
        flagged=inputs.flagged,
        roles_set=sum(1 for r in inputs.rows if r.kind == "role"),
    )
