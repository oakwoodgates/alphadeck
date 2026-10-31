"""Hot Board (``docs/dashboards/hot-board.md``): every name the RECORD shows Armed, one row each, with the tape beside
it and a stated heat formula to rank them by. Follows the as-of dial.

PURE: the router reads the cards counted at the as-of (``dashboards/baskets.py``), the ``calls`` log (the recorded card
in effect per card, and the log rows the arm-night walk reads), the master's tickers and, for the names on a row, the
display members' readings through the point-in-time view (``baskets.per_company``); ``candidates(...)`` lists the rows
and ``assemble(...)`` builds the payload. Display only: nothing here feeds a call, judges an idea or writes anything
(``docs/DASHBOARDS.md`` rule 1); the ranking is a formula the page prints (rule 3), never a verdict (#4).

The rules, in one place:
- **Who is a row.** From each counted card's RECORDED call in effect at the as-of (the greatest ``(asof, seq)`` with
  ``asof <=`` the as-of: the What Changed read). An ARMED card: one row per ``armed_members`` entry (kind ``armed``)
  and, behind a toggle, one per ``watch_members`` entry not already armed (``watch``). A WARMING card, behind a toggle:
  one row per name with at least one live entry trigger in ``triggers_fired`` (``warming``), saying which key the
  trigger turns (``trigger_key``: the call config's own kind sets). A MANAGING card, behind a toggle: one row per
  ``armed_members`` entry, the held name first (``managing``). An INCUBATING card, and a card with no recorded call
  at the as-of, are counted and have no row. A name on two cards is two rows.
- **Two reads of one candidate set** (``read_candidates``). ``candidates(...)`` lists EVERY candidate row from the
  recorded cards alone (no tape). The page's default read (``rows="armed"``) runs the arm nights and the tape for the
  ``armed`` rows only and carries the other kinds as COUNTS (``count_candidates``, off the same recorded cards: no
  display member runs for a row that is only counted); the on-demand read (``rows="all"``) runs them for every row.
  Both go through the one ``assemble``, so a row of the armed read is the same row in the full read.
- **The arm night** (``arm_night``): the first night of the member's CURRENT recorded run on its card. Walk the log
  rows back from the night in effect, one winning row per night (the highest ``seq``, the Idea Timeline's rule),
  while that night's state belongs to the run (``ARMED_RUN`` for an Armed card, ``MANAGING_RUN`` for a Managing one
  that walks back over its Armed nights) AND the member appears in that night's ``armed_members``; the earliest night
  of that unbroken streak is the arm night. A member that joined an already-Armed card later has its own, later arm
  night; one that dropped out and came back starts again. The night is the RECORDED night (the public clock: when the
  operator could first have seen the name), never a trigger's fire date; a reconstructed night counts and is marked.
- **Sessions.** ``sessions_since`` = the sessions strictly after the arm night through the as-of, on the benchmark's
  bar calendar (``series.session_calendar``). ``sessions_left`` = the Mon-Fri sessions strictly after the as-of through
  ``arm_until`` (the call path's own count: ``entry_window_closing`` is computed the same way, and a future session
  has no bar yet); ``closed_ago`` = the calendar sessions strictly after ``arm_until`` through the as-of.
- **Days to the exit-by** (``days_to_exit``) is a COUNTDOWN, so it counts calendar days from the countdown origin (F2,
  ``domain.market_time.countdown_origin``): the action day on a live view, the as-of on a past one. ``assemble`` takes
  it as ``origin`` (the as-of when none is given). The session counts above are not re-based.
- **Since the arm night** (``since_arm``): the name's as-traded return from the first close on or after the arm night
  (the Scoreboard's entry rule) to the last close on or before the as-of, and the benchmark's over the same dates.
- **Heat** (``heat``): four parts, each the row's percentile among the rows SHOWN that have that reading: fresh (fewer
  sessions since the arm night ranks higher), loud (higher RVOL 20), leading (a higher 30-day return minus IWM's) and
  unextended (a smaller return since the arm night). The percentile is the mid-rank over ``n - 1`` (the best of n rows
  is 1, the worst 0, a tie shares the average of its ranks, a lone row is 0.5). Heat = the mean of the four x 100; a
  row missing any part has no heat, sorts after every scored row and is counted. The page recomputes it for the rows
  it shows (``frontend/src/dashboards/hot-board/heat.ts`` mirrors this and is tested against the same fixtures).
- **A flagged price history** keeps its row: its price-derived cells read the flag's reason and it has no heat; the
  insider reading (a Form 4 count, not a price) stays, as the Peer Table keeps it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from dashboards.baskets import FlaggedCompany
from dashboards.peer_table import CapCell, metric_of
from domain.call import CallCard, MemberCall, TriggerRef
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, State, Verdict
from domain.trading_days import is_scheduled_day
from signals.display import (
    insider_flow,
    price_path,
    range52w,
    relative_strength,
    rvol,
    sma,
    trailing_returns,
    volume_regime,
)
from signals.display.base import DisplaySignal

SLUG = "hot-board"
BENCHMARK = "IWM"

# the display members the tape reads, by registered name (a renamed member fails loud in ``display_members``)
MEMBERS: tuple[str, ...] = (
    rvol.MEMBER_NAME,
    sma.MEMBER_NAME,
    range52w.MEMBER_NAME,
    relative_strength.MEMBER_NAME,
    trailing_returns.MEMBER_NAME,
    insider_flow.MEMBER_NAME,
    volume_regime.MEMBER_NAME,
    price_path.MEMBER_NAME,
)

RowKind = Literal["armed", "warming", "watch", "managing"]
RowsRead = Literal[
    "armed", "all"
]  # which candidate rows a read carries (the router's ``rows`` parameter)
KeyName = Literal["conviction", "confirmation"]
SinceGap = Literal["no_arm_night", "flagged", "before_window", "no_bar"]

# the card states whose nights belong to one run (the arm-night walk's stop rule)
ARMED_RUN: frozenset[State] = frozenset({State.ARMED})
MANAGING_RUN: frozenset[State] = frozenset({State.ARMED, State.MANAGING})

# a calendar long enough that no price window outruns it (``series.session_calendar`` keeps the last N + 1 dates)
CALENDAR_SESSIONS = 1000

# the heat's four parts, in the order the page words them
HEAT_PARTS: tuple[str, ...] = ("fresh", "loud", "leading", "unextended")


# ---------------------------------------------------------------------------------------------------------
# the rows as read
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recorded:
    """One ``calls`` row in effect on a night: its own night (``asof``), its insertion order (``seq``), whether
    ``pipeline.backfill`` wrote it (``reconstructed``) and the card."""

    asof: date
    seq: int
    reconstructed: bool
    card: CallCard


@dataclass(frozen=True)
class LogRow:
    """One ``calls`` row as the arm-night walk reads it: its night, its order, its state and the securities in its
    ``armed_members`` (the card blob is not read)."""

    asof: date
    seq: int
    reconstructed: bool
    state: State
    armed_ids: frozenset[UUID]


@dataclass(frozen=True)
class ArmNight:
    night: date
    reconstructed: bool  # the night's row was written by ``pipeline.backfill``


# ---------------------------------------------------------------------------------------------------------
# the arm night
# ---------------------------------------------------------------------------------------------------------


def winning_nights(rows: Iterable[LogRow]) -> list[LogRow]:
    """One row per night, the one with the highest ``seq``, newest night first."""
    best: dict[date, LogRow] = {}
    for r in rows:
        cur = best.get(r.asof)
        if cur is None or r.seq > cur.seq:
            best[r.asof] = r
    return sorted(best.values(), key=lambda r: r.asof, reverse=True)


def arm_night(
    rows: Iterable[LogRow], security_id: UUID, run: frozenset[State] = ARMED_RUN
) -> ArmNight | None:
    """The first night of ``security_id``'s current recorded run: walk the nights back from the newest (the
    night in effect) while its winning row's state is in ``run`` AND the security is among its ``armed_members``;
    the earliest night of that unbroken streak. ``None`` when the newest night does not list it (it is no armed
    row), or the card's state there is outside the run."""
    first: LogRow | None = None
    for night in winning_nights(rows):
        if night.state not in run or security_id not in night.armed_ids:
            break
        first = night
    return None if first is None else ArmNight(first.asof, first.reconstructed)


def run_states(card_state: State) -> frozenset[State]:
    """The states a card's run walks back over: an Armed card only its Armed nights; a Managing card also its
    Armed ones (it was armed before the position was entered)."""
    return MANAGING_RUN if card_state == State.MANAGING else ARMED_RUN


# ---------------------------------------------------------------------------------------------------------
# the candidate rows
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """A row before the tape is attached: the recorded call's own fields for one (card, security)."""

    kind: RowKind
    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    member: MemberCall | None  # the member's own call (None on a warming row)
    triggers: tuple[TriggerRef, ...]
    keys: tuple[KeyName, ...]
    risks: tuple[TriggerRef, ...]  # the card's risk signals that name this security
    arm_until: date | None
    exit_by: date | None
    card_state: State


def trigger_key(kind: Kind) -> KeyName | None:
    """Which key a trigger kind turns, by the call config's own kind sets (``domain.config``)."""
    if kind in DEFAULT_CONFIG.conviction_kinds:
        return "conviction"
    if kind in DEFAULT_CONFIG.confirmation_kinds:
        return "confirmation"
    return None


def _keys_of(triggers: Iterable[TriggerRef]) -> tuple[KeyName, ...]:
    got = {trigger_key(t.kind) for t in triggers}
    return tuple(k for k in ("conviction", "confirmation") if k in got)  # type: ignore[misc]


def _risks(card: CallCard, sid: UUID) -> tuple[TriggerRef, ...]:
    return tuple(t for t in card.risk_signals if t.security_id == sid)


def _from_member(
    kind: RowKind, thesis_id: UUID, name: str, card: CallCard, m: MemberCall
) -> Candidate:
    return Candidate(
        kind=kind,
        thesis_id=thesis_id,
        thesis_name=name,
        security_id=m.security_id,
        member=m,
        triggers=tuple(m.triggers),
        keys=_keys_of(m.triggers),
        risks=_risks(card, m.security_id),
        arm_until=m.arm_until or card.arm_until,
        exit_by=m.exit_by or card.exit_by,
        card_state=card.state,
    )


def card_candidates(thesis_id: UUID, name: str, card: CallCard) -> list[Candidate]:
    """One card's rows from its recorded call (see the module docstring for who is a row)."""
    out: list[Candidate] = []
    if card.state in (State.ARMED, State.MANAGING):
        kind: RowKind = "armed" if card.state == State.ARMED else "managing"
        seen: set[UUID] = set()
        for m in card.armed_members:
            if m.security_id not in seen:
                seen.add(m.security_id)
                out.append(_from_member(kind, thesis_id, name, card, m))
        if card.state == State.ARMED:
            for m in card.watch_members:
                if m.security_id not in seen:
                    seen.add(m.security_id)
                    out.append(_from_member("watch", thesis_id, name, card, m))
    elif card.state == State.WARMING:
        by_sid: dict[UUID, list[TriggerRef]] = {}
        for t in card.triggers_fired:
            by_sid.setdefault(t.security_id, []).append(t)
        for sid, triggers in by_sid.items():
            out.append(
                Candidate(
                    kind="warming",
                    thesis_id=thesis_id,
                    thesis_name=name,
                    security_id=sid,
                    member=None,
                    triggers=tuple(triggers),
                    keys=_keys_of(triggers),
                    risks=_risks(card, sid),
                    arm_until=card.arm_until,
                    exit_by=card.exit_by,
                    card_state=card.state,
                )
            )
    return out


def candidates(names: Mapping[UUID, str], recorded: Mapping[UUID, Recorded]) -> list[Candidate]:
    """Every candidate row of the counted cards (``names``: ``thesis_id -> name``), cards by name, a card's rows in
    its recorded order. A card with no recorded call has none."""
    out: list[Candidate] = []
    for tid in sorted(names, key=lambda t: (names[t].casefold(), str(t))):
        rec = recorded.get(tid)
        if rec is not None:
            out += card_candidates(tid, names[tid], rec.card)
    return out


def read_candidates(cands: Sequence[Candidate], rows: RowsRead) -> list[Candidate]:
    """The candidates a read runs the arm nights and the tape for, in their order: the ``armed`` rows alone for
    ``rows="armed"`` (the default read), every row for ``"all"``. The one place the two reads differ; everything
    after it takes the subset it is handed."""
    return [c for c in cands if c.kind == "armed"] if rows == "armed" else list(cands)


@dataclass(frozen=True)
class CandidateCounts:
    """How many candidate rows of each kind the recorded cards in effect list, whatever the read carries: the
    toggles' "+N". Counted off the recorded cards, so no display member runs for a row that is only counted.
    """

    armed: int = 0
    warming: int = 0
    watch: int = 0
    managing: int = 0


def count_candidates(cands: Iterable[Candidate]) -> CandidateCounts:
    n = {"armed": 0, "warming": 0, "watch": 0, "managing": 0}
    for c in cands:
        n[c.kind] += 1
    return CandidateCounts(**n)


def card_state_counts(
    names: Mapping[UUID, str], recorded: Mapping[UUID, Recorded]
) -> dict[str, int]:
    """The counted cards by their recorded state at the as-of, plus ``no_call`` (nothing recorded by then)."""
    out = {"armed": 0, "warming": 0, "managing": 0, "incubating": 0, "no_call": 0}
    for tid in names:
        rec = recorded.get(tid)
        if rec is None:
            out["no_call"] += 1
        else:
            out[rec.card.state.value] += 1
    return out


# ---------------------------------------------------------------------------------------------------------
# the clocks
# ---------------------------------------------------------------------------------------------------------


def sessions_since(calendar: Sequence[date], arm: date | None, asof: date) -> int | None:
    """The calendar's sessions strictly after the arm night through the as-of; ``None`` without either."""
    if arm is None or not calendar:
        return None
    return sum(1 for d in calendar if arm < d <= asof)


def sessions_left(asof: date, until: date) -> int:
    """The Mon-Fri sessions strictly after ``asof`` through ``until`` (inclusive): how many more an entry window
    ending on ``until`` stays open once the as-of's session is done. ``0`` when ``until <= asof``. The call path's
    own count (``calls.assembler.sessions_left``, ``domain.trading_days``): market holidays count as sessions.
    """
    n, d = 0, asof + timedelta(days=1)
    while d <= until:
        if is_scheduled_day(d):
            n += 1
        d += timedelta(days=1)
    return n


def closed_ago(calendar: Sequence[date], until: date, asof: date) -> int:
    """The calendar's sessions strictly after ``until`` through the as-of."""
    return sum(1 for d in calendar if until < d <= asof)


@dataclass(frozen=True)
class Clocks:
    arm_night: date | None
    arm_reconstructed: bool
    sessions_since: int | None
    arm_until: date | None
    sessions_left: int | None  # the sessions left in an open window (0 on its last day)
    closed_ago: int | None  # the sessions since a closed window ended
    exit_by: date | None
    days_to_exit: (
        int | None
    )  # calendar days from the countdown origin; negative = the horizon has passed


def clocks(
    arm: ArmNight | None,
    arm_until: date | None,
    exit_by: date | None,
    calendar: Sequence[date],
    asof: date,
    *,
    window_start: date | None = None,
    origin: date | None = None,
) -> Clocks:
    """A row's clocks. An arm night before ``window_start`` (the earliest bar the point-in-time view reads) gets no
    session count: the calendar would stop short of it. ``days_to_exit`` is a COUNTDOWN, so it counts from ``origin``
    (F2: the action day on a live view; ``domain.market_time.countdown_origin``), the as-of when none is given. The
    session counts (``sessions_since`` / ``sessions_left`` / ``closed_ago``) stay on the as-of: the call path's own.
    """
    night = arm.night if arm is not None else None
    counted = (
        night if night is not None and (window_start is None or night >= window_start) else None
    )
    return Clocks(
        arm_night=night,
        arm_reconstructed=bool(arm and arm.reconstructed),
        sessions_since=sessions_since(calendar, counted, asof),
        arm_until=arm_until,
        sessions_left=sessions_left(asof, arm_until) if arm_until and arm_until >= asof else None,
        closed_ago=(
            closed_ago(calendar, arm_until, asof) if arm_until and arm_until < asof else None
        ),
        exit_by=exit_by,
        days_to_exit=(exit_by - (origin or asof)).days if exit_by else None,
    )


# ---------------------------------------------------------------------------------------------------------
# since the arm night
# ---------------------------------------------------------------------------------------------------------

Bars = Sequence[tuple[date, float]]  # ascending (date, close), positive closes only


@dataclass(frozen=True)
class SinceArm:
    """The name's as-traded return from the first close on or after the arm night to its last close on or before
    the as-of, and the benchmark's over the same dates. ``gap`` says why ``ret_pct`` is blank."""

    entry_date: date | None = None
    entry_close: float | None = None
    last_date: date | None = None
    last_close: float | None = None
    ret_pct: float | None = None
    iwm_pct: float | None = None
    gap: SinceGap | None = None


def _first_on_or_after(bars: Bars, day: date) -> tuple[date, float] | None:
    return next((b for b in bars if b[0] >= day), None)


def since_arm(
    bars: Bars,
    iwm_bars: Bars,
    arm: date | None,
    *,
    flag: str | None = None,
    window_start: date | None = None,
) -> SinceArm:
    """See ``SinceArm``. A flagged price history, no arm night, an arm night before the price window and no close
    on or after the arm night each say so."""
    if arm is None:
        return SinceArm(gap="no_arm_night")
    if flag is not None:
        return SinceArm(gap="flagged")
    if window_start is not None and arm < window_start:
        return SinceArm(gap="before_window")
    entry = _first_on_or_after(bars, arm)
    if entry is None:
        return SinceArm(gap="no_bar")
    last = bars[-1]
    ret = round((last[1] / entry[1] - 1.0) * 100.0, 2)
    iwm_entry = _first_on_or_after(iwm_bars, arm)
    iwm_pct = None
    if iwm_entry is not None and iwm_bars[-1][0] >= iwm_entry[0]:
        iwm_pct = round((iwm_bars[-1][1] / iwm_entry[1] - 1.0) * 100.0, 2)
    return SinceArm(entry[0], entry[1], last[0], last[1], ret, iwm_pct)


# ---------------------------------------------------------------------------------------------------------
# the tape
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Cell:
    """A number or the reason it is blank (the member's own note where it gave one). Blank is never zero."""

    value: float | None = None
    note: str | None = None


@dataclass(frozen=True)
class Tape:
    """One company's display-member readings at the as-of. ``flag`` = the data flags' labels when they reach its
    price readings: every price-derived cell is then blank and says so, the insider counts stay."""

    flag: str | None = None
    rvol20: Cell = field(default_factory=Cell)
    vs_50d: Cell = field(default_factory=Cell)
    off_high: Cell = field(default_factory=Cell)
    vs_iwm: Cell = field(default_factory=Cell)  # the 30-day return minus IWM's, percentage points
    rs_leading: bool | None = (
        None  # relative_strength's headline: RS at a fresh 13-week high (None = no verdict)
    )
    rs_leading_vs: tuple[str, ...] = ()
    ret_7d: Cell = field(default_factory=Cell)
    ret_30d: Cell = field(default_factory=Cell)
    buys_30d: int | None = None  # None = no Form 4 ingested
    buyers_30d: int | None = None
    adv_usd_20d: Cell = field(default_factory=Cell)
    spark: tuple[float | None, ...] = ()  # the last 90 closes, None = no bar (left padding)


@dataclass(frozen=True)
class CompanyTape:
    """What the per-company read produces: the tape, the approximate cap, and the closes on or after the company's
    earliest arm night (the since-arm return reads them per row; they never reach the wire)."""

    tape: Tape
    cap: CapCell
    bars: tuple[tuple[date, float], ...] = ()


def _cell(sig: DisplaySignal | None, key: str, missing: str) -> Cell:
    if sig is None:
        return Cell(note=missing)
    m = metric_of(sig, key)
    if m is None:
        return Cell(note="not stated")
    return Cell(value=m.value, note=None if m.value is not None else m.note)


def _count(sig: DisplaySignal | None, key: str) -> int | None:
    m = metric_of(sig, key)
    return None if m is None or m.value is None else round(m.value)


def rs_leading(sig: DisplaySignal | None) -> tuple[bool | None, tuple[str, ...]]:
    """``(leading, the benchmarks it leads)``: the member's headline, ``(None, ())`` without a 13-week verdict
    (no RS at all, or fewer than ``MIN_BARS`` aligned bars)."""
    if sig is None or (sig.basis.bars_used or 0) < relative_strength.MIN_BARS:
        return None, ()
    events = {e.key for e in sig.events}
    vs = tuple(s for s in relative_strength.BENCHMARKS if f"rs_high_{s.lower()}" in events)
    return sig.headline is not None and sig.headline.key == "leading", vs


def vs_iwm_cell(own: DisplaySignal | None, iwm: DisplaySignal | None) -> Cell:
    """The company's 30-trading-day return minus IWM's, in percentage points (the Peer Table's and the
    Read-Throughs' tape rule, here against IWM): blank without bars, without IWM's, when the two last closes are
    not the same day (the returns would end on different days) or when a window is short."""
    if own is None or own.basis.window_end is None:
        return Cell(note="no price bars")
    if iwm is None or iwm.basis.window_end is None:
        return Cell(note=f"no {BENCHMARK} bars")
    if own.basis.window_end != iwm.basis.window_end:
        return Cell(
            note=f"last close {own.basis.window_end} is not {BENCHMARK}'s {iwm.basis.window_end}"
        )
    ret, bench = metric_of(own, "ret_30d"), metric_of(iwm, "ret_30d")
    if ret is None or ret.value is None:
        return Cell(note=ret.note if ret else "not stated")
    if bench is None or bench.value is None:
        return Cell(note=bench.note if bench else "not stated")
    return Cell(value=round(ret.value - bench.value, 2))


def _spark(sig: DisplaySignal | None) -> tuple[float | None, ...]:
    if sig is None or not sig.series:
        return ()
    return tuple(None if v is None else round(v, 4) for v in sig.series[0].values)


def company_tape(
    signals: Mapping[str, DisplaySignal | None],
    iwm: DisplaySignal | None,
    cap: CapCell,
    bars: Bars = (),
    flag: str | None = None,
) -> CompanyTape:
    """One company's tape off the display members' signals (keyed by member name). A flagged price history reads
    no price member (``signals`` then holds the insider member alone, or nothing): every price cell carries the
    flag's reason."""
    ins = signals.get(insider_flow.MEMBER_NAME)
    buys, buyers = _count(ins, "buy_count_30d"), _count(ins, "distinct_buyers_30d")
    insider = {"buys_30d": buys if ins is not None else None, "buyers_30d": buyers}
    if flag is not None:
        blank = Cell(note=f"flagged: {flag}")
        return CompanyTape(
            Tape(
                flag=flag,
                rvol20=blank,
                vs_50d=blank,
                off_high=blank,
                vs_iwm=blank,
                ret_7d=blank,
                ret_30d=blank,
                adv_usd_20d=blank,
                **insider,
            ),
            cap,
        )
    none = "no price bars"
    leading, vs = rs_leading(signals.get(relative_strength.MEMBER_NAME))
    trailing = signals.get(trailing_returns.MEMBER_NAME)
    return CompanyTape(
        Tape(
            rvol20=_cell(signals.get(rvol.MEMBER_NAME), "rvol20", none),
            vs_50d=_cell(signals.get(sma.MEMBER_NAME), "pct_vs_fast", none),
            off_high=_cell(signals.get(range52w.MEMBER_NAME), "pct_off_52w_high", none),
            vs_iwm=vs_iwm_cell(trailing, iwm),
            rs_leading=leading,
            rs_leading_vs=vs,
            ret_7d=_cell(trailing, "ret_7d", none),
            ret_30d=_cell(trailing, "ret_30d", none),
            adv_usd_20d=_cell(signals.get(volume_regime.MEMBER_NAME), "adv_usd_20d", none),
            spark=_spark(signals.get(price_path.MEMBER_NAME)),
            **insider,
        ),
        cap,
        tuple(bars),
    )


# ---------------------------------------------------------------------------------------------------------
# heat
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HeatInput:
    """What the heat reads of a row: the four readings. ``None`` = no reading (the part is blank)."""

    key: str
    sessions_since: int | None = None
    rvol20: float | None = None
    vs_iwm: float | None = None  # the 30-day return minus IWM's, percentage points
    since_arm_pct: float | None = None


@dataclass(frozen=True)
class Heat:
    """A row's four percentiles (0 to 1; ``None`` = that part has no reading) and its heat (0 to 100; ``None`` when
    any part is missing)."""

    fresh: float | None
    loud: float | None
    leading: float | None
    unextended: float | None
    score: float | None


def percentiles(values: Sequence[float | None]) -> list[float | None]:
    """Each present value's percentile among the present values, higher value = higher percentile, the mid-rank
    over ``n - 1``: ``(below + (equal - 1) / 2) / (n - 1)`` where ``equal`` counts the value itself. The best of
    n is 1, the worst 0, a tie shares the average of the ranks it spans, a lone value is 0.5 (nothing to compare
    it with). ``None`` stays ``None``."""
    present = [v for v in values if v is not None]
    n = len(present)
    out: list[float | None] = []
    for v in values:
        if v is None:
            out.append(None)
        elif n == 1:
            out.append(0.5)
        else:
            below = sum(1 for w in present if w < v)
            equal = sum(1 for w in present if w == v)
            out.append((below + (equal - 1) / 2) / (n - 1))
    return out


def heat(rows: Sequence[HeatInput]) -> list[Heat]:
    """The heat of each row among ``rows`` (the rows shown), in order. Fresh ranks fewer sessions higher, loud a
    higher RVOL 20, leading a higher return-minus-IWM, unextended a smaller return since the arm night. Each part's
    percentile is taken among the rows with that reading; the heat is the mean of the four times 100, and ``None``
    for a row missing any part. Equal weights (PROPOSED)."""

    def neg(v: float | int | None) -> float | None:
        return None if v is None else -float(v)

    fresh = percentiles([neg(r.sessions_since) for r in rows])
    loud = percentiles([r.rvol20 for r in rows])
    leading = percentiles([r.vs_iwm for r in rows])
    unextended = percentiles([neg(r.since_arm_pct) for r in rows])
    out: list[Heat] = []
    for parts in zip(fresh, loud, leading, unextended):
        score = None if any(p is None for p in parts) else sum(parts) / 4 * 100.0  # type: ignore[arg-type]
        out.append(Heat(*parts, score))
    return out


# ---------------------------------------------------------------------------------------------------------
# the payload
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardRef:
    thesis_id: UUID
    thesis_name: str


@dataclass(frozen=True)
class SourceLink:
    """One place a trigger points at: the kind of source as stored, its reference and a link (``None`` = none
    known: a price reference, a free-text one), by the Evidence Ledger's rule (``filings.source_url``).
    """

    source: str
    ref: str
    url: str | None


@dataclass(frozen=True)
class TriggerOut:
    label: str
    kind: Kind
    grade: Grade | None
    event_date: (
        date | None
    )  # the trigger's FIRE date (the market event), not the night it was recorded
    sources: tuple[SourceLink, ...]


@dataclass(frozen=True)
class HotRow:
    """One (card, name). ``kind`` says which toggle it sits behind (``armed`` is the default set)."""

    key: str  # ``<thesis_id>:<security_id>``
    kind: RowKind
    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: (
        str  # the card's roster ticker (the Cockpit's ?name= key), else the master's, else the id
    )
    name: str | None  # the company's name (today's master)
    also_armed: tuple[CardRef, ...]  # the OTHER cards on which this security is an armed row
    verdict: Verdict | None
    entry_grade: Grade | None
    confidence: float | None  # setup strength
    keys: tuple[KeyName, ...]
    triggers: tuple[TriggerOut, ...]
    risk_kinds: tuple[Kind, ...]  # one per risk signal on the card that names this security
    clocks: Clocks
    entry_window_closing: bool
    lapsing: bool
    since_arm: SinceArm
    tape: Tape
    cap: CapCell


@dataclass(frozen=True)
class HotBoard:
    asof: date
    clock: Literal["record", "public"]
    benchmark: str
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    cards_armed: int
    cards_warming: int
    cards_managing: int
    cards_incubating: int
    cards_no_call: int
    calendar_source: Literal["benchmarks", "companies", "none"]
    calendar_sessions: int
    calendar_first: date | None
    calendar_last: date | None
    price_window_start: (
        date | None
    )  # the earliest bar the point-in-time view reads (None = unbounded)
    no_ticker_rows: int  # rows whose name has no ticker anywhere: shown under their id
    rows_loaded: (
        RowsRead  # which candidate rows ``rows`` carries: the armed ones alone, or every kind
    )
    candidate_counts: (
        CandidateCounts  # every kind's rows on the recorded cards, whatever ``rows`` carries
    )
    rows: tuple[HotRow, ...]
    flagged: tuple[FlaggedCompany, ...]


UrlOf = Callable[[str, str | None], str | None]


def _trigger_out(t: TriggerRef, cik: str | None, url_of: UrlOf) -> TriggerOut:
    return TriggerOut(
        label=t.label,
        kind=t.kind,
        grade=t.grade,
        event_date=t.event_date,
        sources=tuple(SourceLink(p.source, p.ref, url_of(p.ref, cik)) for p in t.sources),
    )


def earliest_arm_night(arms: Mapping[tuple[UUID, UUID], ArmNight | None]) -> dict[UUID, date]:
    """Each security's earliest arm night over its rows: the date its closes are read from."""
    out: dict[UUID, date] = {}
    for (_, sid), arm in arms.items():
        if arm is not None and (sid not in out or arm.night < out[sid]):
            out[sid] = arm.night
    return out


def arm_nights(
    cands: Iterable[Candidate], runs: Mapping[UUID, Sequence[LogRow]]
) -> dict[tuple[UUID, UUID], ArmNight | None]:
    """The arm night of each armed or managing row, by ``(thesis_id, security_id)``. A watch or warming row has
    none: it is not armed."""
    out: dict[tuple[UUID, UUID], ArmNight | None] = {}
    for c in cands:
        if c.kind in ("armed", "managing"):
            out[(c.thesis_id, c.security_id)] = arm_night(
                runs.get(c.thesis_id, ()), c.security_id, run_states(c.card_state)
            )
    return out


def assemble(
    cands: Sequence[Candidate],
    *,
    asof: date,
    clock: Literal["record", "public"],
    arms: Mapping[tuple[UUID, UUID], ArmNight | None],
    tapes: Mapping[UUID, CompanyTape],
    roster_tickers: Mapping[tuple[UUID, UUID], str],
    master_tickers: Mapping[UUID, str | None],
    company_names: Mapping[UUID, str | None],
    ciks: Mapping[UUID, str | None],
    iwm_bars: Bars,
    calendar: Sequence[date],
    calendar_source: Literal["benchmarks", "companies", "none"],
    window_start: date | None,
    url_of: UrlOf,
    counts: Mapping[str, int],
    live_cards: int,
    cards_not_yet_created: int,
    roster_fallback_cards: int,
    cards_counted: int,
    rows_loaded: RowsRead = "all",
    candidate_counts: CandidateCounts | None = None,
    origin: date | None = None,
) -> HotBoard:
    """The whole payload from the candidates and everything read for them. ``cands`` are the rows this read carries
    (``read_candidates``); ``candidate_counts`` are every kind's rows on the recorded cards (counted from ``cands``
    when not given, which is right for the full read). A name whose security is not on the card's roster at the
    as-of still has its row (the master's ticker, else its id: counted in ``no_ticker_rows``); a company with no
    tape (not read) keeps its row with blank cells."""
    armed_on: dict[UUID, list[CardRef]] = {}
    for c in cands:
        if c.kind == "armed":
            armed_on.setdefault(c.security_id, []).append(CardRef(c.thesis_id, c.thesis_name))

    rows: list[HotRow] = []
    no_ticker = 0
    flagged: dict[UUID, tuple[str, str]] = {}
    for c in cands:
        sid = c.security_id
        ticker = roster_tickers.get((c.thesis_id, sid)) or master_tickers.get(sid)
        if not ticker:
            no_ticker += 1
            ticker = str(sid)
        got = tapes.get(sid)
        tape = got.tape if got else Tape()
        cap = got.cap if got else CapCell(gap="no_price")
        arm = arms.get((c.thesis_id, sid))
        if tape.flag is not None:
            flagged.setdefault(sid, (ticker, tape.flag))
        m = c.member
        rows.append(
            HotRow(
                key=f"{c.thesis_id}:{sid}",
                kind=c.kind,
                thesis_id=c.thesis_id,
                thesis_name=c.thesis_name,
                security_id=sid,
                ticker=ticker,
                name=company_names.get(sid),
                also_armed=tuple(x for x in armed_on.get(sid, ()) if x.thesis_id != c.thesis_id),
                verdict=m.verdict if m else None,
                entry_grade=m.entry_grade if m else None,
                confidence=m.confidence if m else None,
                keys=c.keys,
                triggers=tuple(
                    _trigger_out(t, ciks.get(t.security_id), url_of) for t in c.triggers
                ),
                risk_kinds=tuple(t.kind for t in c.risks),
                clocks=clocks(
                    arm,
                    c.arm_until,
                    c.exit_by,
                    calendar,
                    asof,
                    window_start=window_start,
                    origin=origin,
                ),
                entry_window_closing=bool(m and m.entry_window_closing),
                lapsing=bool(m and m.lapsing),
                since_arm=since_arm(
                    got.bars if got else (),
                    iwm_bars,
                    arm.night if arm else None,
                    flag=tape.flag,
                    window_start=window_start,
                ),
                tape=tape,
                cap=cap,
            )
        )
    return HotBoard(
        asof=asof,
        clock=clock,
        benchmark=BENCHMARK,
        live_cards=live_cards,
        cards_counted=cards_counted,
        cards_not_yet_created=cards_not_yet_created,
        roster_fallback_cards=roster_fallback_cards,
        cards_armed=counts["armed"],
        cards_warming=counts["warming"],
        cards_managing=counts["managing"],
        cards_incubating=counts["incubating"],
        cards_no_call=counts["no_call"],
        calendar_source=calendar_source,
        calendar_sessions=len(calendar),
        calendar_first=calendar[0] if calendar else None,
        calendar_last=calendar[-1] if calendar else None,
        price_window_start=window_start,
        no_ticker_rows=no_ticker,
        rows_loaded=rows_loaded,
        candidate_counts=(
            candidate_counts if candidate_counts is not None else count_candidates(cands)
        ),
        rows=tuple(rows),
        flagged=tuple(
            FlaggedCompany(sid, tk, reason)
            for sid, (tk, reason) in sorted(
                flagged.items(), key=lambda kv: (kv[1][0].casefold(), str(kv[0]))
            )
        ),
    )
