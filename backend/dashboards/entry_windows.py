"""Entry Windows (``docs/dashboards/entry-windows.md``): each Armed card's entry window and hold horizon on one date
axis, where the as-of sits in them, and what the armed names have done since the arm night. Follows the as-of dial.

PURE: the router reads the cards, the ``calls`` log (``NightRow`` per row, no card blob; the winning card of every
night of every run), the master's tickers and CIKs, the data flags, the closes and the two benchmarks through the
point-in-time view, and hands them to ``entry_windows(...)``. Time is always a parameter (``asof``); nothing here
reads a clock. Display only: nothing here feeds a call, scores an idea or writes anything
(``docs/DASHBOARDS.md`` rule 1).

The rules, in one place:
- **A row.** A counted card whose recorded call in effect at the as-of is Armed. A Managing card (the hold horizon
  still runs) is a row too; the page hides it behind a toggle. Warming, Incubating and a card with no recorded call
  are counted, never rows (rule 5).
- **The night's row.** The log appends only on change, and a night with several rows reads its LAST one (the
  highest ``seq``, the Idea Timeline's rule). A night with no row carries the previous state through it.
- **The arm night.** The first night of the card's CURRENT recorded Armed run: walk the nights back from the night in
  effect while the winning row reads ``armed``. A run broken by any other recorded night starts over. A Managing card
  walks back through ``armed`` and ``managing`` nights and takes the first ``armed`` night of that span (the Armed
  run the position came out of); one with no ``armed`` night in it has no arm night and is counted, not drawn. The
  arm night is the RECORDED night (the public clock: when the operator could first have seen the name Armed), never
  a trigger's fire date. A reconstructed night counts as a night and is marked.
- **A member's own arm night.** The first night of the run on which it appears in the recorded card's
  ``armed_members``.
- **Sessions** are counted on the IWM bar calendar (``dashboards/series.py::session_calendar``), the arm night
  excluded and the later date included: ``sessions_between(calendar, a, b)`` = the sessions ``d`` with
  ``a < d <= b``. "Into the window" = ``(arm night, as-of]`` on the bars. "Left to ``arm_until``" = the sessions
  strictly after the as-of through ``arm_until``; no bar exists after the as-of, so those are the Mon-Fri days the
  call path's own ``sessions_left`` counts (``domain/trading_days.py``: a market holiday is counted as a session,
  stated on the page). At or past ``arm_until`` the window is closed: ``(arm_until, as-of]`` on the bars.
- **Since the arm night.** As traded (the view's split scale, no dividends): the first close on or after the arm
  night to the last close on or before the as-of (the Scoreboard's entry rule: the arm night's own close). A row
  shows the median over its priced armed names (with n), IWM and SPY over the same span, and how many names sit above
  their arm-night close. A name with no bar on or after the arm night, or on the flagged-price list, is unpriced:
  counted, never in the median, never silently dropped.
- **The sparkline.** The armed names' equal-weighted, daily-rebalanced line against IWM, both from 100 at the first
  session the line steps from (``dashboards/series.py``), ending at the as-of's session.
- **The clocks.** ``arm_until`` (the entry window) and ``exit_by`` (the hold horizon) are the recorded card's; both
  bars start at the arm night. A missing clock is ``None``, drawn as no bar. The two flags (``entry_window_closing``,
  ``lapsing``) are the headline member's (``armed_members[0]``, the member whose clocks the card carries).
- **The axis.** One for the page: from five sessions before the earliest arm night to the latest of the as-of and
  every clock.
"""

from __future__ import annotations

import statistics
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from itertools import groupby
from uuid import UUID

from dashboards.filings import source_url
from dashboards.series import aligned, chain, line_start, session_calendar
from domain.call import CallCard, MemberCall, TriggerRef
from domain.enums import Grade, Kind, State
from domain.thesis import Catalyst
from domain.trading_days import is_scheduled_day

BENCHMARKS: tuple[str, ...] = ("IWM", "SPY")  # IWM's bars are the session calendar
# the axis starts this many sessions before the earliest arm night
AXIS_LEAD_SESSIONS = 5
# ... or this many calendar days when the calendar does not reach that far back
AXIS_LEAD_FALLBACK_DAYS = 7
# the bar calendar's depth: far past any Armed or Managing run, so a long hold still counts every session
CALENDAR_SESSIONS = 1000
# calendar days of bars read before the earliest arm night (five sessions and a long weekend)
LOOKBACK_PAD_DAYS = 14

_ARMED = State.ARMED.value
_MANAGING = State.MANAGING.value


# ---------------------------------------------------------------------------------------------------------
# the log: nights and the current run
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class NightRow:
    """One ``calls`` row, without its card blob."""

    asof: date
    seq: int
    state: str
    reconstructed: bool


@dataclass(frozen=True)
class RunNight:
    """A night of a run: the winning row's ``seq`` (the router reads that card) and whether
    ``pipeline.backfill`` wrote it."""

    asof: date
    seq: int
    reconstructed: bool


@dataclass(frozen=True)
class Run:
    """The card's current run, from its arm night to the night in effect. ``state`` is the night in effect's
    (``armed`` or ``managing``)."""

    state: str
    nights: tuple[RunNight, ...]

    @property
    def first(self) -> date:
        return self.nights[0].asof

    @property
    def last(self) -> date:
        return self.nights[-1].asof


def night_winners(rows: Iterable[NightRow]) -> list[NightRow]:
    """One row per night, ascending: the night's LAST row (highest ``seq``)."""
    ordered = sorted(rows, key=lambda r: (r.asof, r.seq))
    return [list(g)[-1] for _, g in groupby(ordered, key=lambda r: r.asof)]


def armed_run(rows: Iterable[NightRow]) -> Run | None:
    """The card's current Armed run from its rows at or before the as-of: ``None`` when the night in effect is
    not Armed or Managing, or (Managing) when no night of its span reads ``armed``.

    Armed: walk back while the night's winning row reads ``armed``; the run's first night is the arm night.
    Managing: walk back while it reads ``armed`` or ``managing`` and start at the first ``armed`` night of that
    span (the Managing nights before it are not part of the run).
    """
    nights = night_winners(rows)
    if not nights or nights[-1].state not in (_ARMED, _MANAGING):
        return None
    state = nights[-1].state
    keep = {_ARMED} if state == _ARMED else {_ARMED, _MANAGING}
    i = len(nights)
    while i > 0 and nights[i - 1].state in keep:
        i -= 1
    span = nights[i:]
    first = next((j for j, n in enumerate(span) if n.state == _ARMED), None)
    if first is None:
        return None
    return Run(state, tuple(RunNight(n.asof, n.seq, n.reconstructed) for n in span[first:]))


def member_arm_nights(run_cards: Iterable[tuple[date, Iterable[UUID]]]) -> dict[UUID, date]:
    """``security_id -> its own arm night``: the first night of the run on which it appears in the recorded
    card's ``armed_members`` (``run_cards`` = ``(night, armed security ids)`` per night of the run). A name that
    drops out and returns keeps its first night."""
    out: dict[UUID, date] = {}
    for night, sids in sorted(run_cards, key=lambda c: c[0]):
        for sid in sids:
            out.setdefault(sid, night)
    return out


# ---------------------------------------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------------------------------------


def sessions_between(calendar: Sequence[date], a: date, b: date) -> int:
    """The calendar's sessions ``d`` with ``a < d <= b`` (``calendar`` ascending). ``0`` when ``b <= a``."""
    return max(bisect_right(calendar, b) - bisect_right(calendar, a), 0)


def weekdays_between(a: date, b: date) -> int:
    """The Mon-Fri days ``d`` with ``a < d <= b``: the call path's own session count
    (``calls.assembler.sessions_left``; a market holiday counts as a session, ``domain/trading_days.py``).
    """
    n = 0
    d = a + timedelta(days=1)
    while d <= b:
        if is_scheduled_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def entry_clock(
    calendar: Sequence[date], asof: date, arm_until: date | None
) -> tuple[int | None, int | None]:
    """``(sessions left, sessions since it closed)`` for the entry window, one of them set: ``None, None`` with no
    ``arm_until``; ``(left, None)`` while ``arm_until`` is after the as-of; ``(None, closed)`` at or past it, with
    ``closed`` = the bar sessions after ``arm_until`` through the as-of (``0`` on the window's last day).
    """
    if arm_until is None:
        return None, None
    if arm_until > asof:
        return weekdays_between(asof, arm_until), None
    return None, sessions_between(calendar, arm_until, asof)


# ---------------------------------------------------------------------------------------------------------
# since the arm night
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SinceArm:
    """One tape from the first close on or after an arm night to the last close on or before the as-of.
    ``ret`` is a percent."""

    entry_on: date
    entry_close: float
    last_on: date
    last_close: float
    ret: float
    above: bool  # the last close is above the entry close


def since_arm(closes: Mapping[date, float], arm_night: date, asof: date) -> SinceArm | None:
    """The tape since ``arm_night``; ``None`` with no bar on or after it (by the as-of). A non-positive close is
    not a bar."""
    bars = sorted((d, c) for d, c in closes.items() if arm_night <= d <= asof and c > 0)
    if not bars:
        return None
    (entry_on, entry_close), (last_on, last_close) = bars[0], bars[-1]
    return SinceArm(
        entry_on,
        entry_close,
        last_on,
        last_close,
        (last_close / entry_close - 1.0) * 100.0,
        last_close > entry_close,
    )


@dataclass(frozen=True)
class Since:
    """A row's tape since its arm night: ``names`` armed names, ``priced`` of them in the median, the rest
    ``flagged`` (a flagged price history) or ``no_bar`` (no close on or after the arm night). ``median`` and the
    benchmarks' returns are percents; ``above`` = priced names whose last close is above their arm-night close.
    """

    names: int
    priced: int
    flagged: int
    no_bar: int
    above: int
    median: float | None
    iwm: float | None
    spy: float | None


def row_since(
    sids: Sequence[UUID],
    closes: Mapping[UUID, Mapping[date, float]],
    flagged: Mapping[UUID, str],
    bench: Mapping[str, Mapping[date, float]],
    arm_night: date,
    asof: date,
) -> Since:
    """The row's tape since ``arm_night`` over ``sids`` (each once)."""
    spans = [
        s
        for sid in sids
        if sid not in flagged and (s := since_arm(closes.get(sid, {}), arm_night, asof)) is not None
    ]
    n_flagged = sum(1 for sid in sids if sid in flagged)

    def bench_ret(symbol: str) -> float | None:
        got = since_arm(bench.get(symbol, {}), arm_night, asof)
        return got.ret if got is not None else None

    return Since(
        names=len(sids),
        priced=len(spans),
        flagged=n_flagged,
        no_bar=len(sids) - n_flagged - len(spans),
        above=sum(1 for s in spans if s.above),
        median=statistics.median(s.ret for s in spans) if spans else None,
        iwm=bench_ret("IWM"),
        spy=bench_ret("SPY"),
    )


@dataclass(frozen=True)
class Spark:
    """The armed names' equal-weighted line and IWM's, from 100 at ``dates[0]``, one point per session."""

    dates: tuple[date, ...]
    line: tuple[float, ...]
    iwm: tuple[float, ...] | None  # None = IWM has no steps over these sessions


def spark(
    calendar: Sequence[date],
    arm_night: date,
    members: Sequence[Mapping[date, float]],
    iwm: Mapping[date, float],
) -> Spark | None:
    """The sparkline from the first session on or after ``arm_night`` to the calendar's end. Both lines start at
    the session the names' line first steps from (an earlier session would only carry it flat), so they share
    one base. ``None`` with no names priced, or fewer than two sessions to draw."""
    sessions = [d for d in calendar if d >= arm_night]
    if not members or len(sessions) < 2:
        return None
    start = line_start(chain([aligned(c, sessions) for c in members], len(sessions)))
    if start is None:
        return None
    sessions = sessions[start:]
    names = chain([aligned(c, sessions) for c in members], len(sessions))
    bench = chain([aligned(iwm, sessions)], len(sessions))
    return Spark(
        dates=tuple(sessions),
        line=names.levels,
        iwm=bench.levels if line_start(bench) is not None else None,
    )


# ---------------------------------------------------------------------------------------------------------
# catalysts and the axis
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalystTick:
    """A dated catalyst of the recorded card inside the window. ``past`` = its date is before the as-of (a hollow
    tick); one dated on or after the as-of is still ahead."""

    label: str
    kind: str | None
    when_date: date
    past: bool


def window_end(arm_until: date | None, exit_by: date | None) -> date | None:
    """Where a row's window ends for placing catalysts: ``exit_by``, else ``arm_until``, else nowhere."""
    return exit_by if exit_by is not None else arm_until


def catalysts_in_window(
    catalysts: Iterable[Catalyst], start: date, end: date | None, asof: date
) -> tuple[CatalystTick, ...]:
    """The dated catalysts with ``start <= date <= end`` (a card with no clock to bound them places none), each
    once by id, by date then label."""
    if end is None:
        return ()
    seen: set[UUID] = set()
    out: list[CatalystTick] = []
    for c in catalysts:
        if c.when_date is None or not (start <= c.when_date <= end) or c.id in seen:
            continue
        seen.add(c.id)
        out.append(CatalystTick(c.label, c.kind, c.when_date, c.when_date < asof))
    out.sort(key=lambda t: (t.when_date, t.label))
    return tuple(out)


def axis_bounds(
    calendar: Sequence[date],
    arm_nights: Iterable[date],
    ends: Iterable[date | None],
    asof: date,
) -> tuple[date, date] | None:
    """The page's one date axis: from ``AXIS_LEAD_SESSIONS`` sessions before the earliest arm night (a week
    before it when the calendar does not reach back that far) to the latest of the as-of and every clock.
    ``None`` with no arm night (no rows)."""
    nights = list(arm_nights)
    if not nights:
        return None
    earliest = min(nights)
    i = bisect_left(calendar, earliest) - AXIS_LEAD_SESSIONS
    start = calendar[i] if i >= 0 else earliest - timedelta(days=AXIS_LEAD_FALLBACK_DAYS)
    return start, max([asof, *(e for e in ends if e is not None)])


def lookback_days(arm_nights: Iterable[date], asof: date) -> int:
    """Calendar days of bars to read before the as-of: back past the earliest arm night, with the axis's lead."""
    nights = list(arm_nights)
    return (asof - min(nights)).days + LOOKBACK_PAD_DAYS if nights else LOOKBACK_PAD_DAYS


# ---------------------------------------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SourceRow:
    source: str
    ref: str
    url: str | None  # the EDGAR filing index (or the stored link); None for a price reference


@dataclass(frozen=True)
class TriggerRow:
    """A fired trigger on a member. ``event_date`` is the trigger's FIRE date (the market event), not the arm
    night."""

    label: str
    kind: Kind
    grade: Grade | None
    event_date: date | None
    sources: tuple[SourceRow, ...]


@dataclass(frozen=True)
class MemberRow:
    """An armed name. ``since`` is the as-traded return (a percent) from the first close on or after its OWN arm
    night; ``None`` with ``flag`` set = left out for a flagged price history, ``None`` alone = no bar.
    """

    security_id: UUID
    ticker: str | None
    arm_night: date
    entry_on: date | None
    since: float | None
    above: bool | None
    flag: str | None
    triggers: tuple[TriggerRow, ...]


@dataclass(frozen=True)
class Row:
    """One Armed (or Managing) card. ``in_effect`` = the recorded night whose card the row shows;
    ``reconstructed`` = the ARM night's row was written by ``pipeline.backfill`` and ``reconstructed_nights`` the
    nights of the run that were."""

    thesis_id: UUID
    thesis_name: str
    state: str
    arm_night: date
    in_effect: date
    reconstructed: bool
    reconstructed_nights: int
    run_nights: int
    arm_until: date | None
    exit_by: date | None
    confidence: float | None
    entry_window_closing: bool
    lapsing: bool
    sessions_into: int | None
    sessions_left: int | None
    closed_ago: int | None
    hold_days: int | None
    catalysts: tuple[CatalystTick, ...]
    members: tuple[MemberRow, ...]
    since: Since
    spark: Spark | None


@dataclass(frozen=True)
class FlaggedName:
    security_id: UUID
    ticker: str
    reason: str


@dataclass(frozen=True)
class Counts:
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    armed: int
    managing: int
    managing_without_arm_night: int
    warming: int
    incubating: int
    no_call: int
    reconstructed_arm_nights: int
    armed_names: int  # distinct armed names across the rows
    priced_names: int
    unpriced_names: int  # flagged or without a bar since the arm night, counted once each


@dataclass(frozen=True)
class EntryWindows:
    asof: date
    axis_start: date | None
    axis_end: date | None
    session_source: str  # where the calendar came from: "benchmarks" (IWM) | "companies" | "none"
    calendar_sessions: int
    calendar_last: date | None
    counts: Counts
    flagged: tuple[FlaggedName, ...]
    rows: tuple[Row, ...]  # by card name


def _trigger_row(t: TriggerRef, ciks: Mapping[UUID, str | None]) -> TriggerRow:
    cik = ciks.get(t.security_id)
    return TriggerRow(
        label=t.label,
        kind=t.kind,
        grade=t.grade,
        event_date=t.event_date,
        sources=tuple(SourceRow(p.source, p.ref, source_url(p.ref, cik)) for p in t.sources),
    )


def _armed_sids(card: CallCard) -> list[UUID]:
    """The card's armed names, each once, in the recorded (ranked) order."""
    return list(dict.fromkeys(m.security_id for m in card.armed_members))


def entry_windows(
    cards: Sequence[tuple[UUID, str]],
    nights: Mapping[UUID, Sequence[NightRow]],
    recorded: Mapping[tuple[UUID, date], CallCard],
    tickers: Mapping[UUID, str | None],
    ciks: Mapping[UUID, str | None],
    closes: Mapping[UUID, Mapping[date, float]],
    flagged: Mapping[UUID, str],
    bench: Mapping[str, Mapping[date, float]],
    *,
    asof: date,
    live_cards: int,
    not_yet_created: int = 0,
) -> EntryWindows:
    """The whole page for ``asof``.

    ``cards`` = the counted cards ``(id, name)``; ``nights`` = each card's ``calls`` rows at or before the as-of;
    ``recorded`` = the winning card of every night of every run, keyed ``(thesis id, night)``; ``closes`` = the
    armed names' closes (a flagged name need not be there: it is never read); ``flagged`` = ``security_id ->
    reason`` from ``price_flags``; ``bench`` = ``{"IWM": {day: close}, "SPY": ...}``.
    """
    ordered = sorted(cards, key=lambda c: (c[1].casefold(), str(c[0])))
    runs: dict[UUID, Run] = {}
    states = {"warming": 0, "incubating": 0, "managing_no_run": 0, "no_call": 0}
    for tid, _ in ordered:
        rows = nights.get(tid, ())
        if not rows:
            states["no_call"] += 1
            continue
        run = armed_run(rows)
        if run is not None:
            runs[tid] = run
        elif (state := night_winners(rows)[-1].state) == State.WARMING.value:
            states["warming"] += 1
        elif state == State.INCUBATING.value:
            states["incubating"] += 1
        else:  # Managing with no Armed night in its span
            states["managing_no_run"] += 1

    # the shared calendar: IWM's bars, plus any priced armed name's bar after IWM's last one
    in_view = list(
        dict.fromkeys(
            sid
            for tid, run in runs.items()
            for sid in _armed_sids(recorded[(tid, run.last)])
            if sid not in flagged
        )
    )
    calendar, source = session_calendar(
        bench.get("IWM", {}).keys(),
        (d for sid in in_view for d in closes.get(sid, {})),
        asof,
        CALENDAR_SESSIONS,
    )

    rows_out: list[Row] = []
    flagged_seen: dict[UUID, FlaggedName] = {}
    for tid, name in ordered:
        run = runs.get(tid)
        if run is None:
            continue
        card = recorded[(tid, run.last)]
        arm_night = run.first
        sids = _armed_sids(card)
        own = member_arm_nights((n.asof, _armed_sids(recorded[(tid, n.asof)])) for n in run.nights)
        by_sid: dict[UUID, MemberCall] = {}
        for m in card.armed_members:
            by_sid.setdefault(m.security_id, m)
        members: list[MemberRow] = []
        for sid in sids:
            m = by_sid[sid]
            arm = own.get(sid, arm_night)
            span = since_arm(closes.get(sid, {}), arm, asof) if sid not in flagged else None
            if sid in flagged and sid not in flagged_seen:
                flagged_seen[sid] = FlaggedName(sid, tickers.get(sid) or "?", flagged[sid])
            members.append(
                MemberRow(
                    security_id=sid,
                    ticker=tickers.get(sid),
                    arm_night=arm,
                    entry_on=span.entry_on if span else None,
                    since=span.ret if span else None,
                    above=span.above if span else None,
                    flag=flagged.get(sid),
                    triggers=tuple(_trigger_row(t, ciks) for t in m.triggers),
                )
            )
        left, closed = entry_clock(calendar, asof, card.arm_until)
        head = card.armed_members[0] if card.armed_members else None
        rows_out.append(
            Row(
                thesis_id=tid,
                thesis_name=name,
                state=run.state,
                arm_night=arm_night,
                in_effect=run.last,
                reconstructed=run.nights[0].reconstructed,
                reconstructed_nights=sum(1 for n in run.nights if n.reconstructed),
                run_nights=len(run.nights),
                arm_until=card.arm_until,
                exit_by=card.exit_by,
                confidence=card.confidence,
                entry_window_closing=bool(head and head.entry_window_closing),
                lapsing=bool(head and head.lapsing),
                sessions_into=sessions_between(calendar, arm_night, asof) if calendar else None,
                sessions_left=left,
                closed_ago=closed,
                hold_days=(card.exit_by - asof).days if card.exit_by is not None else None,
                catalysts=catalysts_in_window(
                    card.catalyst_surface,
                    arm_night,
                    window_end(card.arm_until, card.exit_by),
                    asof,
                ),
                members=tuple(members),
                since=row_since(sids, closes, flagged, bench, arm_night, asof),
                spark=spark(
                    calendar,
                    arm_night,
                    [closes[sid] for sid in sids if sid not in flagged and closes.get(sid)],
                    bench.get("IWM", {}),
                ),
            )
        )

    names = list(dict.fromkeys(m.security_id for r in rows_out for m in r.members))
    priced = {m.security_id for r in rows_out for m in r.members if m.since is not None}
    axis = axis_bounds(
        calendar,
        (r.arm_night for r in rows_out),
        (e for r in rows_out for e in (r.arm_until, r.exit_by)),
        asof,
    )
    counts = Counts(
        live_cards=live_cards,
        cards_counted=len(ordered),
        cards_not_yet_created=not_yet_created,
        armed=sum(1 for r in rows_out if r.state == _ARMED),
        managing=sum(1 for r in rows_out if r.state == _MANAGING),
        managing_without_arm_night=states["managing_no_run"],
        warming=states["warming"],
        incubating=states["incubating"],
        no_call=states["no_call"],
        reconstructed_arm_nights=sum(1 for r in rows_out if r.reconstructed),
        armed_names=len(names),
        priced_names=len(priced),
        unpriced_names=len(names) - len(priced),
    )
    return EntryWindows(
        asof=asof,
        axis_start=axis[0] if axis else None,
        axis_end=axis[1] if axis else None,
        session_source=source,
        calendar_sessions=len(calendar),
        calendar_last=calendar[-1] if calendar else None,
        counts=counts,
        flagged=tuple(
            sorted(flagged_seen.values(), key=lambda f: (f.ticker.casefold(), str(f.security_id)))
        ),
        rows=tuple(rows_out),
    )
