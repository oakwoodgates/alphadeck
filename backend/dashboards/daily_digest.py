"""Daily Digest (``docs/dashboards/daily-digest.md``): the morning page. Six sections, each a CUT of a page that
already exists, read by THAT page's own pure function on the digest's reads: what last night's pass changed, the
Managing cards' positions, what is due this week, the insider buys just filed, the armed names' last session and what
is forming. Follows the as-of dial.

PURE: the router reads the counted cards at the as-of (``dashboards/baskets.py``), the ``calls`` log (the two nights
and the card in effect on each), the thesis children (catalysts, kill criteria) and the operator's position, the
Form 4 filings through the shared reader, the closes of the names on a row and IWM's, and the data flags, on the
request's one ``serve_view(asof)`` pair; ``daily_digest(...)`` builds the whole payload. Display only: nothing here
feeds a call, scores an idea or writes anything (``docs/DASHBOARDS.md`` rule 1), and NOTHING here re-derives a rule a
source page owns (a source page's rule change changes the digest with it). It aggregates no outcome (rule 6): the
only numbers across rows are counts, and the five largest rises and falls are a list cut, never an average move.

The rules, in one place:
- **The night pair.** N = the newest recorded night across the counted cards with ``asof <= `` the as-of; P = the
  newest recorded night before N across the same cards (What Changed's "last night", at an as-of). The ``calls`` log
  appends only on change, so a card with no row on N is carried from its last row. The as-of can lie after N (a
  weekend, or the morning before the 22:30 pass): the payload says so (``Nights.days_after_night``).
- **Last night** is What Changed's own diff (``what_changed.what_changed``) over the card in effect at N and the one in
  effect at P, regrouped by card. A card whose change touches Armed or Managing (it is, or was, one of them) comes
  first, then a Warming card, then an Incubating one last (inverse loudness, #7). Inside a card, What Changed's own
  order. A card's state move (or "new card") is its headline; the other lines follow. With no P there is no baseline
  and no line (``no_baseline``): the record's first night has nothing to compare.
- **Loudness** is decided here, once: ``tone`` is ``accent`` on exactly three lines (a card that entered Armed, a name
  newly armed, an entry window closing within the Armed window of ``what_changed.ENTRY_WINDOW_DAYS`` days) and
  ``warm`` on two (a card that left Armed, an exit-by reached); every other line has none.
- **Positions** are the Managing cards (the recorded state in effect at the as-of), each with the operator's effective
  position as the as-of thesis read carries it. The since-fill return runs from the LOGGED price when there is one (and
  no listed split lies between the fill and the as-of: the bars are on the view's scale, the logged price is not), else
  from the first close on or after the fill date; the end is the last close on or before the as-of. IWM is read over
  the same two dates. Shares times price shows only when both were logged.
- **Due this week** is the Catalyst Calendar's ``this_week`` bucket (``catalyst_calendar.catalyst_calendar`` with
  ``today = `` the countdown origin and each card's latest call = the card in effect at the as-of), an item dated the
  origin marked ``today``, plus how many catalysts PASSED after P and are not yet pruned. The origin is the ACTION
  day on a live view (F2: Monday night's page reads a Tuesday catalyst as "today"), the as-of on a past one.
  ``days_after_night`` is an age of the record and stays on the as-of.
- **Insider buys** are Insider Buying's rows (``insider_tracker.purchase_of`` on the shared reader's Form 4 rows),
  cut to the FILING dates in (P, N]: the cluster rule (``insider_tracker.clusters``) sees the filings from P less
  ``CLUSTER_WINDOW_DAYS`` through N (the lead-in; nothing filed after N, so the page reads as of the night), and "became
  a trigger" is ``insider_tracker.trigger_hits`` over the recorded nights in (P, N].
- **The tape** is the Hot Board's ``armed`` rows (``hot_board.candidates``, ``arm_nights``, ``clocks``,
  ``since_arm``) on CLOSES only: no display member runs. The last session is the calendar's last two sessions
  (``series.session_calendar``): a name needs a close on both; anything else says why and keeps its row. The five
  largest rises and falls are cut over the distinct priced NAMES (a name on two cards is two rows, one name): rises are
  the positive returns, falls the negative ones, ties by ticker.
- **Forming** is counts only: the Warming cards, of which the ones whose key turned last night, and the Incubating cards.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Literal
from uuid import UUID

from dashboards.baskets import FlaggedCompany, Membership, companies_in, display_ticker
from dashboards.catalyst_calendar import CalendarItem, LatestCall, catalyst_calendar
from dashboards.filings import FilingRow, source_url
from dashboards.hot_board import (
    ArmNight,
    LogRow,
    Recorded,
    SinceArm,
    SourceLink,
    arm_nights,
    candidates,
    card_state_counts,
    clocks,
    sessions_left,
    since_arm,
    window_open,
)
from dashboards.insider_tracker import (
    CLUSTER_WINDOW_DAYS,
    Cluster,
    RoleClass,
    TriggerHit,
    accession_key,
    buyer_key,
    clusters,
    purchases_in,
    role_classes,
    row_key,
    trigger_hits,
)
from dashboards.series import SessionSource, session_calendar
from dashboards.trigger_log import CardNights, Scope, Series, copies, since_move
from dashboards.what_changed import (
    Change,
    ChangeItem,
    RecordedCall,
    Section,
    what_changed,
)
from domain.call import CallCard, TriggerRef
from domain.enums import Kind, State
from domain.splits import ListedSplit, is_applicable
from domain.thesis import Thesis

SLUG = "daily-digest"
BENCHMARK = "IWM"
TOP_N = 5  # the largest rises / falls the tape shows by default
# a calendar long enough that no price window outruns it (``series.session_calendar`` keeps the last N + 1 dates)
CALENDAR_SESSIONS = 1000

Tone = Literal["accent", "warm"]
Tier = Literal["armed", "warming", "incubating"]
Cut = Literal["rise", "fall"]
ReturnStatus = Literal["priced", "unpriced", "flagged"]
FillStatus = Literal["priced", "unpriced", "flagged", "no_name", "no_fill"]
FillBasis = Literal["logged_price", "first_close"]

Bars = Sequence[tuple[date, float]]  # ascending (date, close), positive closes only


# ---------------------------------------------------------------------------------------------------------
# the rows as read (the router builds these from its SELECTs)
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class InEffect:
    """One ``calls`` row in effect on a night: its own night (``asof``), its insertion order (``seq``), whether
    ``pipeline.backfill`` wrote it (``reconstructed``), whether the run that wrote it ingested every name cleanly
    (``ingest_fresh``: ``False`` = not fresh, ``None`` = unstamped, as a reconstruction is) and the card.
    """

    asof: date
    seq: int
    reconstructed: bool
    ingest_fresh: bool | None
    card: CallCard

    def recorded_call(self) -> RecordedCall:
        return RecordedCall(self.asof, self.seq, self.reconstructed, self.card)

    def recorded(self) -> Recorded:
        return Recorded(self.asof, self.seq, self.reconstructed, self.card)


@dataclass(frozen=True)
class HeldPosition:
    """The operator's effective position on one Managing card at the as-of (``decisions_repo.effective_position``):
    ``security_id`` = the held name (``None`` = unattributed), ``shares`` = the fill's logged shares (the position
    model carries none; ``None`` = not logged, and the seed-era columns never carry them)."""

    security_id: UUID | None
    opened_on: date | None
    entry_price: float | None
    shares: float | None


@dataclass(frozen=True)
class NameTape:
    """One company's closes (ascending, positive only) and its listed splits, as the point-in-time view reads
    them. A flagged price history reads no bar."""

    bars: Bars = ()
    splits: tuple[ListedSplit, ...] = ()


@dataclass(frozen=True)
class DigestInputs:
    """Everything the router read. ``now`` / ``baseline`` = each counted card's row in effect at N / P; ``positions``
    = the held position per Managing card (absent = none logged by the as-of); ``filings`` = each voting name's reader
    rows dated from ``P - CLUSTER_WINDOW_DAYS`` through the as-of; ``nights`` = each counted card's recorded nights in
    (P, N]; ``runs`` = the log rows the arm-night walk reads; ``tapes`` = the closes of the names on a row;
    ``iwm_bars`` = IWM's closes; ``flagged`` = ``security -> reason`` for the names on a row. ``origin`` = the day the
    COUNTDOWNS (an entry window closing, a catalyst coming due) count from: the action day on a live view, the as-of
    on a past one (``domain.market_time.countdown_origin``). The AGES (``days_after_night``) stay on the as-of.
    """

    asof: date
    clock: Literal["record", "public"]
    scope: Scope
    theses: Sequence[Thesis]
    latest: date | None
    previous: date | None
    now: Mapping[UUID, InEffect]
    baseline: Mapping[UUID, InEffect]
    tickers: Mapping[UUID, str | None]
    positions: Mapping[UUID, HeldPosition]
    roster_tickers: Mapping[tuple[UUID, UUID], str]
    memberships: Sequence[Membership]
    silent_rows_left_out: int
    unresolved_rows: int
    filings: Mapping[UUID, Sequence[FilingRow]]
    nights: Sequence[CardNights]
    runs: Mapping[UUID, Sequence[LogRow]]
    tapes: Mapping[UUID, NameTape]
    iwm_bars: Bars
    flagged: Mapping[UUID, str]
    window_start: date | None
    ciks: Mapping[UUID, str | None] = field(
        default_factory=dict
    )  # the issuer CIKs a source link is built from
    origin: date | None = None  # where the countdowns count from (F2); the as-of when None


# ---------------------------------------------------------------------------------------------------------
# the night pair
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Nights:
    """``latest`` = N and ``previous`` = P (``None`` = no recorded night / no earlier one). ``sessions_between`` =
    the Mon-Fri sessions strictly after P through N (the call path's count). ``days_after_night`` = the calendar days
    from N to the as-of (0 = the as-of IS the night; more = a weekend, a holiday or the morning before the pass).
    """

    asof: date
    latest: date | None
    previous: date | None
    sessions_between: int | None
    days_after_night: int | None


def night_pair(asof: date, latest: date | None, previous: date | None) -> Nights:
    return Nights(
        asof=asof,
        latest=latest,
        previous=previous,
        sessions_between=(
            sessions_left(previous, latest) if latest is not None and previous is not None else None
        ),
        days_after_night=(asof - latest).days if latest is not None else None,
    )


# ---------------------------------------------------------------------------------------------------------
# 1. last night: What Changed, regrouped by card
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DigestLine:
    """One line of a card: What Changed's own item (its card is the group's), plus the page's ``tone`` and, on a
    trigger or risk line, the trigger's first source (the Evidence Ledger's link rule, ``filings.source_url``).
    """

    section: Section
    change: Change
    security_id: UUID | None
    ticker: str | None
    from_state: State | None
    to_state: State | None
    label: str | None
    signal_kind: Kind | None
    on: date | None
    previous_on: date | None
    days: int | None
    tone: Tone | None
    source: SourceLink | None = None


@dataclass(frozen=True)
class DigestCard:
    """One card with a change. ``tier`` = the group it sits in (``armed`` holds an Armed or Managing card, now or at
    P; ``warming``; ``incubating`` last). ``headline`` = the state move (or "new card"); ``lines`` = the rest, in
    What Changed's order. ``state`` = the card's state at N, ``previous_state`` = at P (``None`` = new since P).
    """

    thesis_id: UUID
    thesis_name: str
    tier: Tier
    state: State
    previous_state: State | None
    reconstructed: bool
    ingest_fresh: bool | None
    headline: DigestLine | None
    lines: tuple[DigestLine, ...]


@dataclass(frozen=True)
class LastNight:
    no_baseline: bool  # no P: nothing to compare
    cards_with_change: int
    lines: int
    cards: tuple[DigestCard, ...]


def tone_of(item: ChangeItem) -> Tone | None:
    """The page's one loud rule (#7). ``accent`` (the Board's Armed color) on a card that entered Armed (a state move
    to Armed, or a new card born Armed), a name newly armed and an entry window closing within
    ``what_changed.ENTRY_WINDOW_DAYS``; ``warm`` on a card that LEFT Armed (a move into Managing is a position
    held, not a retreat) and an exit-by reached. Nothing else.
    """
    if item.change in ("state", "new_card"):
        if item.to_state == State.ARMED:
            return "accent"
        if (
            item.change == "state"
            and item.from_state == State.ARMED
            and item.to_state != State.MANAGING  # a position entered is not a card that left Armed
        ):
            return "warm"
        return None
    if item.change in ("armed", "entry_window"):
        return "accent"
    if item.change == "exit_by":
        return "warm"
    return None


def first_source(
    item: ChangeItem, now: CallCard, before: CallCard | None, ciks: Mapping[UUID, str | None]
) -> SourceLink | None:
    """A trigger or risk line's first source: the first ``Provenance`` of the trigger's copies on the card the line
    was read from (the baseline card for a trigger no longer firing, else the newest), found by the trigger's identity
    (role, security, kind, event date) and linked by the Evidence Ledger's rule. ``None`` when it states none.
    """
    if item.change not in ("trigger_new", "trigger_gone", "trigger_redated", "risk_new"):
        return None
    if item.security_id is None or item.signal_kind is None:
        return None
    role = "risk_signal" if item.change == "risk_new" else "entry_trigger"
    card = before if item.change == "trigger_gone" and before is not None else now
    for ref in copies(card).get((role, item.security_id, item.signal_kind, item.on), ()):
        for p in ref.sources:
            return SourceLink(p.source, p.ref, source_url(p.ref, ciks.get(item.security_id)))
    return None


def _line(
    item: ChangeItem,
    now: CallCard,
    before: CallCard | None,
    ciks: Mapping[UUID, str | None],
) -> DigestLine:
    return DigestLine(
        section=item.section,
        change=item.change,
        security_id=item.security_id,
        ticker=item.ticker,
        from_state=item.from_state,
        to_state=item.to_state,
        label=item.label,
        signal_kind=item.signal_kind,
        on=item.on,
        previous_on=item.previous_on,
        days=item.days,
        tone=tone_of(item),
        source=first_source(item, now, before, ciks),
    )


def _tier(now_state: State, before: State | None) -> Tier:
    if now_state in (State.ARMED, State.MANAGING) or before in (State.ARMED, State.MANAGING):
        return "armed"
    return "warming" if now_state == State.WARMING else "incubating"


_TIER_ORDER: dict[Tier, int] = {"armed": 0, "warming": 1, "incubating": 2}


def last_night(
    names: Mapping[UUID, str],
    now: Mapping[UUID, InEffect],
    baseline: Mapping[UUID, InEffect],
    tickers: Mapping[UUID, str | None],
    *,
    latest: date | None,
    previous: date | None,
    ciks: Mapping[UUID, str | None] | None = None,
    origin: date | None = None,
) -> LastNight:
    """Section 1: What Changed's diff over the two in-effect reads, grouped by card, Armed-touching cards first. With
    no P (the record's first night, or no recorded night at all) there is nothing to compare: no card, no line.
    """
    if latest is None or previous is None:
        return LastNight(no_baseline=True, cards_with_change=0, lines=0, cards=())
    result = what_changed(
        names,
        {tid: r.recorded_call() for tid, r in now.items()},
        {tid: r.recorded_call() for tid, r in baseline.items()},
        tickers,
        latest=latest,
        baseline_on=previous,
        origin=origin,
    )
    by_card: dict[UUID, list[ChangeItem]] = {}
    for item in result.items:
        by_card.setdefault(item.thesis_id, []).append(item)  # What Changed's order is kept
    cards: list[DigestCard] = []
    for tid, items in by_card.items():
        n, b = now[tid], baseline.get(tid)
        lines = [_line(i, n.card, b.card if b is not None else None, ciks or {}) for i in items]
        head = next((ln for ln in lines if ln.change in ("state", "new_card")), None)
        cards.append(
            DigestCard(
                thesis_id=tid,
                thesis_name=items[0].thesis_name,
                tier=_tier(n.card.state, b.card.state if b is not None else None),
                state=n.card.state,
                previous_state=b.card.state if b is not None else None,
                reconstructed=n.reconstructed,
                ingest_fresh=n.ingest_fresh,
                headline=head,
                lines=tuple(ln for ln in lines if ln is not head),
            )
        )
    cards.sort(key=lambda c: (_TIER_ORDER[c.tier], c.thesis_name.casefold(), str(c.thesis_id)))
    return LastNight(
        no_baseline=False,
        cards_with_change=len(cards),
        lines=len(result.items),
        cards=tuple(cards),
    )


# ---------------------------------------------------------------------------------------------------------
# the closes: a return over two dates, the last session
# ---------------------------------------------------------------------------------------------------------


def _pct(end: float, start: float) -> float:
    return round((end / start - 1.0) * 100.0, 2)


@dataclass(frozen=True)
class LastSession:
    """The name's as-traded return from the close before the last session to the last session's close, and IWM's over
    the same two sessions. ``status`` ``priced`` = both closes exist; ``unpriced`` says why in ``reason``; ``flagged``
    = the price history is on the flagged-price list (``reason``), never priced."""

    status: ReturnStatus
    reason: str | None = None
    session: date | None = None
    previous_session: date | None = None
    ret_pct: float | None = None
    bench_pct: float | None = None


def last_session(
    own: Series | None, bench: Series, calendar: Sequence[date], *, flag: str | None = None
) -> LastSession:
    """The last session's return: the calendar's last two sessions, the name needing a close on BOTH (a name missing
    one is not given a longer span under this label). IWM over the same two sessions, blank when it lacks one.
    """
    if flag is not None:
        return LastSession("flagged", reason=flag)
    if len(calendar) < 2:
        return LastSession("unpriced", reason="no session calendar")
    day, before = calendar[-1], calendar[-2]
    if own is None:
        return LastSession("unpriced", reason="no price bars", session=day, previous_session=before)
    c1, c0 = own.on(day), own.on(before)
    if c1 is None or c0 is None:
        missing = day if c1 is None else before
        return LastSession(
            "unpriced",
            reason=f"no close on {missing.isoformat()}",
            session=day,
            previous_session=before,
        )
    b1, b0 = bench.on(day), bench.on(before)
    return LastSession(
        "priced",
        session=day,
        previous_session=before,
        ret_pct=_pct(c1, c0),
        bench_pct=_pct(b1, b0) if b1 is not None and b0 is not None else None,
    )


# ---------------------------------------------------------------------------------------------------------
# 2. positions: the Managing cards
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SinceFill:
    """The held name's as-traded return from the fill to the last close on or before the as-of. ``basis`` says which
    start was used: ``logged_price`` (the operator's logged fill price) or ``first_close`` (the first close on or
    after the fill date, on the view's scale). ``note`` says why the logged price was not used when it was logged.
    IWM runs over the same dates. ``status``: ``priced``; ``unpriced`` (``reason``); ``flagged`` (a flagged price
    history, never priced); ``no_name`` (the position names no company); ``no_fill`` (no price and no date logged).
    """

    status: FillStatus
    basis: FillBasis | None = None
    basis_price: float | None = None
    basis_date: date | None = None
    last_date: date | None = None
    last_close: float | None = None
    ret_pct: float | None = None
    bench_pct: float | None = None
    reason: str | None = None
    note: str | None = None


def split_since_fill(splits: Sequence[ListedSplit], opened_on: date, asof: date) -> bool:
    """Is a split IN FORCE at the as-of with its first session after the fill? (The bars are on the view's scale; a
    logged price is on the scale of the day it was logged.)"""
    return any(
        opened_on < s.effective_date <= asof and is_applicable(s.status, s.effective_date, on=asof)
        for s in splits
    )


def since_fill(
    held: HeldPosition,
    tape: NameTape | None,
    bench: Series,
    asof: date,
    *,
    flag: str | None,
    window_start: date | None,
) -> SinceFill:
    if held.security_id is None:
        return SinceFill("no_name", reason="the position names no company")
    if flag is not None:
        return SinceFill("flagged", reason=flag)
    if held.entry_price is None and held.opened_on is None:
        return SinceFill("no_fill", reason="no fill price or date logged")
    series = Series.of(dict(tape.bars)) if tape is not None else None
    end = series.on_or_before(asof) if series is not None else None
    if end is None:
        return SinceFill("unpriced", reason="no close on or before the as-of")
    note: str | None = None
    use_logged = held.entry_price is not None and held.entry_price > 0
    if use_logged and held.opened_on is not None and tape is not None:
        if split_since_fill(tape.splits, held.opened_on, asof):
            use_logged = False
            note = (
                "a split lies between the fill and the as-of: the logged price is on the old scale, so the "
                "first close on or after the fill date stands in"
            )
    if use_logged:
        assert held.entry_price is not None
        if held.opened_on is not None and end[0] < held.opened_on:
            return SinceFill("unpriced", reason="no close on or after the fill date")
        b0 = bench.on_or_after(held.opened_on) if held.opened_on is not None else None
        b1 = bench.on(end[0])
        return SinceFill(
            "priced",
            basis="logged_price",
            basis_price=held.entry_price,
            basis_date=held.opened_on,
            last_date=end[0],
            last_close=end[1],
            ret_pct=_pct(end[1], held.entry_price),
            bench_pct=(
                _pct(b1, b0[1]) if b0 is not None and b1 is not None and b0[0] <= end[0] else None
            ),
        )
    if held.opened_on is None:
        return SinceFill("unpriced", reason="no fill date logged to find a first close", note=note)
    if window_start is not None and held.opened_on < window_start:
        return SinceFill("unpriced", reason="the fill date is before the price window", note=note)
    assert series is not None
    move = since_move(series, bench, held.opened_on, asof)
    if move.status != "priced" or move.ret is None or move.entry_date is None:
        return SinceFill("unpriced", reason="no close on or after the fill date", note=note)
    first = series.on(move.entry_date)
    return SinceFill(
        "priced",
        basis="first_close",
        basis_price=first,
        basis_date=move.entry_date,
        last_date=move.end_date,
        last_close=end[1],
        ret_pct=round(move.ret * 100.0, 2),
        bench_pct=round(move.bench_ret * 100.0, 2) if move.bench_ret is not None else None,
        note=note,
    )


@dataclass(frozen=True)
class HoldClock:
    """The card's exit-by (the signal-validity horizon, not an order) as sessions: ``sessions_left`` = the Mon-Fri
    sessions strictly after the as-of through ``exit_by`` while the horizon is open (the Hot Board's rule,
    ``window_open``: the first session after the as-of is on or before it, F3 — at least 1), or
    ``passed_sessions`` = the calendar's sessions strictly after ``exit_by`` through the as-of (``0`` until a
    session after it has printed)."""

    exit_by: date | None
    sessions_left: int | None
    passed_sessions: int | None


def hold_clock(exit_by: date | None, asof: date, calendar: Sequence[date]) -> HoldClock:
    if exit_by is None:
        return HoldClock(None, None, None)
    if window_open(asof, exit_by):
        return HoldClock(exit_by, sessions_left(asof, exit_by), None)
    return HoldClock(exit_by, None, sum(1 for d in calendar if exit_by < d <= asof))


@dataclass(frozen=True)
class PositionRow:
    """One Managing card. ``has_position`` is false when the as-of thesis read carries no position (a fill logged
    after the as-of is none then): the card keeps its line with its clocks and risks. ``fill_usd`` = shares times the
    logged price, only when BOTH were logged. ``risk_signals`` = the card's distinct risk signals (kind, security,
    event date), ``risk_new`` = how many of them section 1 carries as new last night. ``kill_criteria`` = the
    operator's own words, verbatim."""

    thesis_id: UUID
    thesis_name: str
    has_position: bool
    security_id: UUID | None
    ticker: str | None
    opened_on: date | None
    logged_price: float | None
    shares: float | None
    fill_usd: float | None
    since_fill: SinceFill
    last_session: LastSession
    hold: HoldClock
    risk_signals: int
    risk_new: int
    kill_criteria: tuple[str, ...]
    reconstructed: bool
    ingest_fresh: bool | None


def _risk_count(risks: Sequence[TriggerRef]) -> int:
    return len({(t.kind, t.security_id, t.event_date) for t in risks})


def position_rows(
    inp: DigestInputs, last: LastNight, bench: Series, calendar: Sequence[date]
) -> tuple[PositionRow, ...]:
    new_risk: dict[UUID, int] = {}
    for c in last.cards:
        new_risk[c.thesis_id] = sum(1 for ln in c.lines if ln.change == "risk_new")
    rows: list[PositionRow] = []
    for t in inp.theses:
        rec = inp.now.get(t.id)
        if rec is None or rec.card.state != State.MANAGING:
            continue
        held = inp.positions.get(t.id)
        sid = held.security_id if held is not None else None
        tape = inp.tapes.get(sid) if sid is not None else None
        flag = inp.flagged.get(sid) if sid is not None else None
        last_sess = (
            last_session(
                Series.of(dict(tape.bars)) if tape is not None else None,
                bench,
                calendar,
                flag=flag,
            )
            if sid is not None
            else LastSession("unpriced", reason="the position names no company")
        )
        fill = (
            since_fill(held, tape, bench, inp.asof, flag=flag, window_start=inp.window_start)
            if held is not None
            else SinceFill("no_fill", reason="no position logged as of this date")
        )
        rows.append(
            PositionRow(
                thesis_id=t.id,
                thesis_name=t.name,
                has_position=held is not None,
                security_id=sid,
                ticker=(
                    (inp.roster_tickers.get((t.id, sid)) or inp.tickers.get(sid))
                    if sid is not None
                    else None
                ),
                opened_on=held.opened_on if held is not None else None,
                logged_price=held.entry_price if held is not None else None,
                shares=held.shares if held is not None else None,
                fill_usd=(
                    held.shares * held.entry_price
                    if held is not None and held.shares is not None and held.entry_price is not None
                    else None
                ),
                since_fill=fill,
                last_session=last_sess,
                hold=hold_clock(rec.card.exit_by, inp.asof, calendar),
                risk_signals=_risk_count(rec.card.risk_signals),
                risk_new=new_risk.get(t.id, 0),
                kill_criteria=tuple(k.text for k in t.kill_criteria),
                reconstructed=rec.reconstructed,
                ingest_fresh=rec.ingest_fresh,
            )
        )
    rows.sort(key=lambda r: (r.thesis_name.casefold(), str(r.thesis_id)))
    return tuple(rows)


# ---------------------------------------------------------------------------------------------------------
# 3. due this week: the Catalyst Calendar's bucket
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DueItem:
    """One dated line of this week (a catalyst, or a card's entry window or exit-by). ``today`` = it is dated the
    countdown origin. ``days`` = calendar days from the origin (the action day on a live view, the as-of on a past
    one)."""

    item: Literal["catalyst", "entry_window", "exit_by"]
    on: date
    days: int
    today: bool
    label: str
    kind: str | None
    when_label: str | None
    thesis_id: UUID
    thesis_name: str
    state: str | None


@dataclass(frozen=True)
class DueThisWeek:
    """The Calendar's ``this_week`` bucket (``start`` to ``end`` inclusive: through the coming Sunday). ``passed_since``
    = the dated catalysts that passed AFTER P and are not yet pruned (``None`` = no P); ``passed_total`` = every
    passed catalyst the Calendar lists; ``cards_without_calls`` = counted cards with no recorded call.
    """

    start: date
    end: date
    items: tuple[DueItem, ...]
    catalysts: int
    deadlines: int
    passed_since: int | None
    passed_total: int
    cards_without_calls: int


def _due(i: CalendarItem) -> DueItem:
    return DueItem(
        item=i.item,
        on=i.on,
        days=i.days,
        today=i.days == 0,
        label=i.label,
        kind=i.kind,
        when_label=i.when_label,
        thesis_id=i.thesis_id,
        thesis_name=i.thesis_name,
        state=i.state,
    )


def due_this_week(inp: DigestInputs) -> DueThisWeek:
    latest = {
        tid: LatestCall(r.card.state.value, r.card.arm_until, r.card.exit_by)
        for tid, r in inp.now.items()
    }
    cal = catalyst_calendar(inp.theses, latest, today=inp.origin or inp.asof)
    bucket = cal.buckets[0]  # "this_week": today through the coming Sunday
    items = tuple(_due(i) for i in bucket.items)
    assert bucket.end is not None
    return DueThisWeek(
        start=bucket.start,
        end=bucket.end,
        items=items,
        catalysts=sum(1 for i in items if i.item == "catalyst"),
        deadlines=sum(1 for i in items if i.item != "catalyst"),
        passed_since=(
            sum(1 for p in cal.passed if p.on > inp.previous) if inp.previous is not None else None
        ),
        passed_total=cal.passed_catalysts,
        cards_without_calls=cal.cards_without_calls,
    )


# ---------------------------------------------------------------------------------------------------------
# 4. insider buys filed since P
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class BuyCard:
    """A card a name sits on (voting only): ``ticker`` = the ticker the Cockpit's ``?name=`` key wants there."""

    thesis_id: UUID
    thesis_name: str
    ticker: str


@dataclass(frozen=True)
class BuyRow:
    """One Form 4 (or applied 4/A) holding an open-market purchase filed in (P, N]. ``filed`` = the filing date (the
    public clock); ``filed_days_after`` = the days from the earliest purchase's trade date to the filing when the filing
    is later; ``filer`` / ``role`` = the first reporting owner and the filing's own role words (``None`` = not stated);
    ``shares`` / ``price`` / ``usd`` = the code-P lines' total, shares-weighted price and dollars (the reader's own
    summary, never re-parsed here); ``cluster`` = two or more distinct buyers within 30 days; ``triggers`` = the
    recorded insider triggers carrying this accession (empty = "not a trigger", which says nothing about why).
    """

    key: str
    security_id: UUID
    ticker: str
    accession: str | None
    form: str
    url: str | None
    filed: date
    trade_date: date | None
    filed_days_after: int | None
    cards: tuple[BuyCard, ...]
    filer: str | None
    role: str | None
    role_classes: tuple[RoleClass, ...]
    co_filers: int
    shares: float | None
    price: float | None
    usd: float | None
    tags: tuple[str, ...]
    headline: str
    cluster: Cluster | None
    triggers: tuple[TriggerHit, ...]


@dataclass(frozen=True)
class BuyCounts:
    """What the section rests on. ``filings_read`` = the Form 4s and 4/As filed in (P, N] = ``purchases`` +
    ``routine_folded`` + ``amendments_no_purchase``."""

    names_read: int
    filings_read: int
    purchases: int
    routine_folded: int
    amendments_no_purchase: int
    names_with_purchases: int
    filer_unresolved: int
    cluster_rows: int
    trigger_rows: int
    nights_read: int
    nights_unreadable: int
    silent_rows_left_out: int
    unresolved_rows: int


@dataclass(frozen=True)
class InsiderBuys:
    """The section: ``span_start`` is P and ``span_end`` is N (filing dates in (P, N]); both ``None`` with no P."""

    no_baseline: bool
    span_start: date | None
    span_end: date | None
    rows: tuple[BuyRow, ...]
    counts: BuyCounts


def _order(r: BuyRow) -> tuple:
    return (
        -r.filed.toordinal(),
        -(r.usd if r.usd is not None else float("-inf")),
        r.ticker.casefold(),
        r.accession or "",
    )


def insider_buys(inp: DigestInputs) -> InsiderBuys:
    """Insider Buying's purchases cut to the filing dates in (P, N]. The cluster rule sees the lead-in (P less 30 days)
    through N, never anything filed after N."""
    latest, previous = inp.latest, inp.previous
    empty = BuyCounts(
        0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, inp.silent_rows_left_out, inp.unresolved_rows
    )
    if latest is None or previous is None:
        return InsiderBuys(True, None, None, (), empty)

    chips: dict[UUID, tuple[BuyCard, ...]] = {}
    tickers: dict[UUID, str] = {}
    for sid, cards in companies_in(inp.memberships):
        tickers[sid] = display_ticker(cards)
        chips[sid] = tuple(BuyCard(c.thesis_id, c.thesis_name, c.ticker) for c in cards)

    # the filings read, as of the night: nothing filed after N, the lead-in kept for the clusters
    found = [(sid, row, p) for sid, row, p in purchases_in(inp.filings) if row.date <= latest]
    cl = clusters([(sid, row.date, buyer_key(p.filer), p.filer) for sid, row, p in found])
    hits = trigger_hits(inp.nights)

    rows: list[BuyRow] = []
    for (sid, fr, p), cluster in zip(found, cl, strict=True):
        if not previous < fr.date <= latest:
            continue
        rows.append(
            BuyRow(
                key=row_key(sid, fr.accession, fr.date),
                security_id=sid,
                ticker=tickers.get(sid) or fr.ticker,
                accession=fr.accession,
                form=fr.form,
                url=fr.url,
                filed=fr.date,
                trade_date=p.trade_date,
                filed_days_after=(
                    (fr.date - p.trade_date).days
                    if p.trade_date is not None and p.trade_date < fr.date
                    else None
                ),
                cards=chips.get(sid, ()),
                filer=p.filer,
                role=p.role,
                role_classes=role_classes(p.role),
                co_filers=p.co_filers,
                shares=p.shares,
                price=p.price,
                usd=p.usd,
                tags=fr.tags,
                headline=fr.headline,
                cluster=cluster,
                triggers=hits.get((sid, accession_key(fr.accession) or ""), ()),
            )
        )
    rows.sort(key=_order)

    in_span = [
        fr
        for rs in inp.filings.values()
        for fr in rs
        if fr.kind in ("form4", "form4a") and previous < fr.date <= latest
    ]
    listed = {(r.security_id, r.accession) for r in rows}
    with_purchase = sum(1 for fr in in_span if (fr.security_id, fr.accession) in listed)
    routine = sum(1 for fr in in_span if fr.kind == "form4" and fr.routine)
    window_nights = [n for cn in inp.nights for n in cn.nights]
    cluster_rows = [r for r in rows if r.cluster is not None]
    return InsiderBuys(
        no_baseline=False,
        span_start=previous,
        span_end=latest,
        rows=tuple(rows),
        counts=BuyCounts(
            names_read=len(inp.filings),
            filings_read=len(in_span),
            purchases=len(rows),
            routine_folded=routine,
            amendments_no_purchase=len(in_span) - with_purchase - routine,
            names_with_purchases=len({r.security_id for r in rows}),
            filer_unresolved=sum(1 for r in rows if r.filer is None),
            cluster_rows=len(cluster_rows),
            trigger_rows=sum(1 for r in rows if r.triggers),
            nights_read=len(window_nights),
            nights_unreadable=sum(1 for n in window_nights if n.card is None),
            silent_rows_left_out=inp.silent_rows_left_out,
            unresolved_rows=inp.unresolved_rows,
        ),
    )


# ---------------------------------------------------------------------------------------------------------
# 5. the tape: the armed names' last session
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TapeRow:
    """One (card, armed name). ``ticker`` = the card's roster ticker (the Cockpit's ``?name=`` key). The two clocks are
    the Hot Board's (``sessions_since`` the arm night, ``sessions_left`` in an open entry window, ``closed_ago`` since a
    closed one). ``cut`` = the name is among the largest rises / falls of the last session."""

    key: str
    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str
    arm_night: date | None
    arm_reconstructed: bool
    sessions_since: int | None
    arm_until: date | None
    sessions_left: int | None
    closed_ago: int | None
    last_session: LastSession
    since_arm: SinceArm
    cut: Cut | None


@dataclass(frozen=True)
class TapeCounts:
    """What the tape rests on, in rows and in distinct names. ``names_priced`` = names with a last-session return;
    ``cut_n`` = the priced names the cut ranks."""

    rows: int
    names: int
    names_priced: int
    names_unpriced: int
    names_flagged: int
    since_arm_priced: int
    since_arm_blank: int
    rises_shown: int
    falls_shown: int


@dataclass(frozen=True)
class Tape:
    """The armed rows, in order: the last session's return largest to smallest, then the rows with no return (by
    ticker). ``session`` / ``previous_session`` = the calendar's last two sessions; ``iwm_pct`` = IWM over them.
    """

    session: date | None
    previous_session: date | None
    iwm_pct: float | None
    calendar_source: SessionSource
    calendar_sessions: int
    rows: tuple[TapeRow, ...]
    counts: TapeCounts


def _cuts(rows: Sequence[tuple[UUID, str, LastSession]]) -> dict[UUID, Cut]:
    """The names among the ``TOP_N`` largest rises (positive returns) and ``TOP_N`` largest falls (negative ones) of
    the last session, over distinct priced names; ties by ticker."""
    best: dict[UUID, tuple[str, float]] = {}
    for sid, ticker, ls in rows:
        if ls.status == "priced" and ls.ret_pct is not None:
            best[sid] = (ticker, ls.ret_pct)
    rises = sorted(
        (kv for kv in best.items() if kv[1][1] > 0),
        key=lambda kv: (-kv[1][1], kv[1][0].casefold(), str(kv[0])),
    )[:TOP_N]
    falls = sorted(
        (kv for kv in best.items() if kv[1][1] < 0),
        key=lambda kv: (kv[1][1], kv[1][0].casefold(), str(kv[0])),
    )[:TOP_N]
    out: dict[UUID, Cut] = {sid: "rise" for sid, _ in rises}
    out.update({sid: "fall" for sid, _ in falls})
    return out


def build_tape(
    inp: DigestInputs, bench: Series, calendar: Sequence[date], source: SessionSource
) -> Tape:
    recorded = {tid: r.recorded() for tid, r in inp.now.items()}
    names = {t.id: t.name for t in inp.theses}
    armed = [c for c in candidates(names, recorded) if c.kind == "armed"]
    arms: dict[tuple[UUID, UUID], ArmNight | None] = arm_nights(armed, inp.runs)
    iwm = list(inp.iwm_bars)
    bars_of = {sid: Series.of(dict(t.bars)) for sid, t in inp.tapes.items()}

    built: list[tuple[TapeRow, str]] = []
    for c in armed:
        sid = c.security_id
        ticker = inp.roster_tickers.get((c.thesis_id, sid)) or inp.tickers.get(sid) or str(sid)
        flag = inp.flagged.get(sid)
        arm = arms.get((c.thesis_id, sid))
        clk = clocks(arm, c.arm_until, c.exit_by, calendar, inp.asof, window_start=inp.window_start)
        tp = inp.tapes.get(sid)
        built.append(
            (
                TapeRow(
                    key=f"{c.thesis_id}:{sid}",
                    thesis_id=c.thesis_id,
                    thesis_name=c.thesis_name,
                    security_id=sid,
                    ticker=ticker,
                    arm_night=clk.arm_night,
                    arm_reconstructed=clk.arm_reconstructed,
                    sessions_since=clk.sessions_since,
                    arm_until=clk.arm_until,
                    sessions_left=clk.sessions_left,
                    closed_ago=clk.closed_ago,
                    last_session=last_session(bars_of.get(sid), bench, calendar, flag=flag),
                    since_arm=since_arm(
                        tp.bars if tp is not None else (),
                        iwm,
                        arm.night if arm is not None else None,
                        flag=flag,
                        window_start=inp.window_start,
                    ),
                    cut=None,
                ),
                ticker,
            )
        )
    cuts = _cuts([(r.security_id, tk, r.last_session) for r, tk in built])

    def order(pair: tuple[TapeRow, str]) -> tuple:
        r, tk = pair
        ls = r.last_session
        has = ls.status == "priced" and ls.ret_pct is not None
        return (0 if has else 1, -(ls.ret_pct or 0.0), tk.casefold(), r.thesis_name.casefold())

    rows = [replace(r, cut=cuts.get(r.security_id)) for r, _ in sorted(built, key=order)]
    distinct = {r.security_id: r for r in rows}
    priced = [r for r in distinct.values() if r.last_session.status == "priced"]
    return Tape(
        session=calendar[-1] if calendar else None,
        previous_session=calendar[-2] if len(calendar) >= 2 else None,
        iwm_pct=(
            _pct(b1, b0)
            if len(calendar) >= 2
            and (b1 := bench.on(calendar[-1])) is not None
            and (b0 := bench.on(calendar[-2])) is not None
            else None
        ),
        calendar_source=source,
        calendar_sessions=len(calendar),
        rows=tuple(rows),
        counts=TapeCounts(
            rows=len(rows),
            names=len(distinct),
            names_priced=len(priced),
            names_unpriced=sum(1 for r in distinct.values() if r.last_session.status == "unpriced"),
            names_flagged=sum(1 for r in distinct.values() if r.last_session.status == "flagged"),
            since_arm_priced=sum(1 for r in rows if r.since_arm.ret_pct is not None),
            since_arm_blank=sum(1 for r in rows if r.since_arm.ret_pct is None),
            rises_shown=sum(1 for sid in cuts if cuts[sid] == "rise"),
            falls_shown=sum(1 for sid in cuts if cuts[sid] == "fall"),
        ),
    )


# ---------------------------------------------------------------------------------------------------------
# 6. forming: counts only
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class KeyTurned:
    """A Warming card whose key turned last night: the key's own recorded label(s) from section 1."""

    thesis_id: UUID
    thesis_name: str
    labels: tuple[str, ...]


@dataclass(frozen=True)
class Forming:
    warming: int
    incubating: int
    key_turned: tuple[KeyTurned, ...]


def forming(counts: Mapping[str, int], last: LastNight) -> Forming:
    turned: list[KeyTurned] = []
    for c in last.cards:
        if c.state != State.WARMING:
            continue
        labels = tuple(ln.label for ln in c.lines if ln.change == "key_on" and ln.label)
        if labels:
            turned.append(KeyTurned(c.thesis_id, c.thesis_name, labels))
    turned.sort(key=lambda k: (k.thesis_name.casefold(), str(k.thesis_id)))
    return Forming(counts["warming"], counts["incubating"], tuple(turned))


# ---------------------------------------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Counts:
    """The work the page shows on its quiet line: counts of what was read and listed, never a statistic over returns
    (rule 6). ``cards_recorded_on_night`` + ``cards_carried`` + ``cards_no_call`` = ``cards_counted``; the four states
    sum to the cards with a call."""

    cards_counted: int
    live_cards: int
    not_yet_created: int
    roster_fallback: int
    cards_recorded_on_night: int
    cards_carried: int
    cards_no_call: int
    cards_armed: int
    cards_warming: int
    cards_managing: int
    cards_incubating: int
    cards_ingest_not_fresh: int
    cards_reconstructed: int


@dataclass(frozen=True)
class DailyDigest:
    asof: date
    clock: Literal["record", "public"]
    nights: Nights
    last_night: LastNight
    positions: tuple[PositionRow, ...]
    due: DueThisWeek
    insider: InsiderBuys
    tape: Tape
    forming: Forming
    counts: Counts
    flagged: tuple[FlaggedCompany, ...]


def daily_digest(inp: DigestInputs) -> DailyDigest:
    """The whole page from everything read. Each section is the source page's own function over the digest's reads."""
    names = {t.id: t.name for t in inp.theses}
    recorded = {tid: r.recorded() for tid, r in inp.now.items()}
    state_counts = card_state_counts(names, recorded)

    bench = Series.of(dict(inp.iwm_bars))
    calendar, source = session_calendar(
        (d for d, _ in inp.iwm_bars),
        (d for t in inp.tapes.values() for d, _ in t.bars),
        inp.asof,
        CALENDAR_SESSIONS,
    )

    last = last_night(
        names,
        inp.now,
        inp.baseline,
        inp.tickers,
        latest=inp.latest,
        previous=inp.previous,
        ciks=inp.ciks,
        origin=inp.origin,
    )
    positions = position_rows(inp, last, bench, calendar)
    tp = build_tape(inp, bench, calendar, source)

    # the flagged names among the tape rows and the held names, each once (left out of every figure, kept on screen)
    on_page: dict[UUID, str] = {r.security_id: r.ticker for r in tp.rows}
    for p in positions:
        if p.security_id is not None and p.ticker is not None:
            on_page.setdefault(p.security_id, p.ticker)
    flagged = tuple(
        sorted(
            (
                FlaggedCompany(sid, tk, inp.flagged[sid])
                for sid, tk in on_page.items()
                if sid in inp.flagged
            ),
            key=lambda f: (f.ticker.casefold(), str(f.security_id)),
        )
    )

    on_night = sum(1 for r in inp.now.values() if r.asof == inp.latest)
    return DailyDigest(
        asof=inp.asof,
        clock=inp.clock,
        nights=night_pair(inp.asof, inp.latest, inp.previous),
        last_night=last,
        positions=positions,
        due=due_this_week(inp),
        insider=insider_buys(inp),
        tape=tp,
        forming=forming(state_counts, last),
        counts=Counts(
            cards_counted=len(names),
            live_cards=inp.scope.live_cards,
            not_yet_created=inp.scope.not_yet_created,
            roster_fallback=inp.scope.roster_fallback,
            cards_recorded_on_night=on_night,
            cards_carried=len(inp.now) - on_night,
            cards_no_call=state_counts["no_call"],
            cards_armed=state_counts["armed"],
            cards_warming=state_counts["warming"],
            cards_managing=state_counts["managing"],
            cards_incubating=state_counts["incubating"],
            cards_ingest_not_fresh=sum(1 for r in inp.now.values() if r.ingest_fresh is False),
            cards_reconstructed=sum(1 for r in inp.now.values() if r.reconstructed),
        ),
        flagged=flagged,
    )


def read_start(previous: date) -> date:
    """The first filing date the router reads: P less the cluster window, so a row just after P counts the buyers just
    before it."""
    return previous - timedelta(days=CLUSTER_WINDOW_DAYS)
