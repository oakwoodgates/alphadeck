"""Trigger Log (``docs/dashboards/trigger-log.md``): every entry trigger and risk signal the RECORDED calls show
firing across the cards in a window, as events over time: its recorded night, fire date, source, the card's
state that night beside the night before, whether it is still live, and what the name's tape did since. Follows
the as-of dial.

PURE: ``collect(cards, ...)`` walks each card's recorded nights and returns the trigger events; ``trigger_log(...)``
adds the ticker, the source links and the tape (the since-return, the sessions since) to each and counts the
work. The router reads the ``calls`` log, the master, the bars and the data flags, on the request's one
``serve_view(asof)`` pair, and hands them in. Display only: nothing here feeds a call, scores an idea or writes
anything (``docs/DASHBOARDS.md`` rule 1), and NOTHING here aggregates the rows (rule 6: an outcome statistic
"after insider triggers" is the Track Record page's job, sliced by market-cap tier; this page lists).

The rules, in one place:
- **The nights.** A card's recorded call in effect on a night is the row of the ``calls`` log with the highest
  ``seq`` that night. The window is the nights in ``[as-of - days, as-of]``, plus the ONE row in effect before
  the window opens (so the first night's state change and the carried-in triggers can be read). The log appends
  only on change, so a night without a row is one where nothing changed. A reconstructed night
  (``pipeline.backfill``) counts as a night and is marked. A row whose card no longer validates is counted
  (``nights_unreadable``), never a 500 and never silently skipped.
- **A trigger's identity** is ``(security, kind, event date, label)``, within its role, across a card's nights:
  the same trigger on twelve nights is ONE row. It is read from three places on each night's card:
  ``triggers_fired`` (an entry trigger), ``risk_signals`` (a risk signal), and each ``armed_members[]`` /
  ``watch_members[]`` entry's ``triggers`` (member-level entry triggers; the same identity merges with the
  card-level one, the first copy in that order standing for it).
- **Its recorded night** is the first night in the window the record shows it. An identity the row before the
  window already shows was first recorded BEFORE the window: it is not a row (the Hot Board holds it while it
  is live) and the page counts those still live (``carried_live``).
- **Its fire date** is ``event_date`` (``None`` = undated). A fire date before the recorded night is "recorded N
  days after" (a Form 4 filed days after its trade: the system knew it only when it was public, invariant #1).
- **Its role** is the list it came from; **its key** is conviction or confirmation by kind against
  ``DEFAULT_CONFIG.conviction_kinds`` / ``confirmation_kinds``, with the one exception the assembler keeps (a
  revenue-acceleration screen turns ``revenue_accel_key``); an entry trigger whose kind is in neither set turns
  no key (``None``); a risk signal turns none.
- **The state pair** is the card's recorded state on the recorded night beside the previous recorded night's
  (the row before the window opens, for the first night). No earlier row at all is "new card".
- **Live** is whether the same identity is on the card in effect at the as-of (the newest row on or before it).
- **Since** is the name's as-traded return from the first close on or after the recorded night (the
  Scoreboard's entry rule) to the last close on or before the as-of, with IWM over the same two dates; **sessions
  since** counts the sessions between the first of those dates and the last on IWM's bar calendar
  (``series.session_calendar``). A name with no bar since reads "unpriced"; one on the flagged-price list
  reads "flagged" and is never priced; either keeps its row (rule 5 / invariant #9).
- **Order** is neutral and computed: the recorded night newest first, then ticker.
"""

from __future__ import annotations

import hashlib
from bisect import bisect_left, bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from dashboards.filings import source_url
from dashboards.series import SessionSource, session_calendar
from domain.call import CallCard, TriggerRef
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, State
from domain.signal import Provenance

WINDOWS: tuple[int, ...] = (30, 90, 180)  # the window choices, in days
DEFAULT_WINDOW = 90
BENCHMARK = "IWM"  # the comparison and the session calendar

# The metric the revenue-acceleration screen stamps on its provenance (``signals.revenue_acceleration``). The
# assembler keys its one key exception on the DETECTOR, which never reaches the wire (``TriggerRef`` has no
# detector field), so a recorded trigger is recognized by the screen's own provenance instead. A test pins this
# value against the detector's.
REVENUE_METRIC = "revenue"
REVENUE_SOURCE = "xbrl"

Role = Literal["entry_trigger", "risk_signal"]
KeyName = Literal["conviction", "confirmation"]
SinceStatus = Literal["priced", "unpriced", "flagged"]

# the identity of one trigger on a card (the role is part of it, so an entry trigger and a risk signal that
# somehow shared the other four fields would never merge and hide one)
Identity = tuple[Role, UUID, Kind, date | None, str]


# ---------------------------------------------------------------------------------------------------------
# the rows as read (the router builds these from its SELECTs)
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Night:
    """One recorded night of one card: its winning row (the highest ``seq``). ``card`` is ``None`` when the
    stored card no longer validates against the call model (counted, never read)."""

    asof: date
    seq: int
    reconstructed: bool
    card: CallCard | None


@dataclass(frozen=True)
class CardNights:
    """One counted card's recorded nights: ``before`` = the row in effect before the window opens (``None`` =
    the card has no earlier row), ``nights`` = the window's nights, oldest first."""

    thesis_id: UUID
    thesis_name: str
    before: Night | None
    nights: tuple[Night, ...]


@dataclass(frozen=True)
class Scope:
    """How many cards the as-of's counted set left out, for the quiet line (``baskets.CardsAsOf``)."""

    live_cards: int  # unarchived cards today
    not_yet_created: int  # created after the as-of: left out
    roster_fallback: int  # counted cards with no roster snapshot by then: today's roster stands in


# ---------------------------------------------------------------------------------------------------------
# the events
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TriggerEvent:
    """One trigger on one card, dated by the first night in the window the record shows it."""

    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    role: Role
    kind: Kind
    event_date: date | None
    label: str
    grade: Grade | None
    key: KeyName | None
    recorded_on: date
    reconstructed: bool
    from_state: (
        State | None
    )  # the previous recorded night's state (None = new card, or that row unreadable)
    to_state: State
    new_card: bool  # no earlier row of any kind on this card
    live: bool | None  # on the card in effect at the as-of (None = that card could not be read)
    sources: tuple[Provenance, ...]


@dataclass(frozen=True)
class Collected:
    """The events of every counted card and what was read to find them."""

    events: tuple[TriggerEvent, ...]
    cards_counted: int
    cards_with_nights: int  # at least one night in the window
    cards_no_call: int  # no row at or before the as-of at all
    nights_read: int  # window nights
    nights_reconstructed: int
    nights_unreadable: (
        int  # window nights and the row before the window, whose card did not validate
    )
    carried_live: int  # live triggers first recorded before the window (not listed)


def is_revenue_acceleration(ref: TriggerRef) -> bool:
    """The revenue-acceleration screen, by the one tell that reaches the wire: a catalyst whose provenance is
    an XBRL source naming the revenue metric."""
    return ref.kind is Kind.CATALYST and any(
        p.source == REVENUE_SOURCE and p.detail.get("metric") == REVENUE_METRIC for p in ref.sources
    )


def key_of(ref: TriggerRef, role: Role, cfg: CallConfig = DEFAULT_CONFIG) -> KeyName | None:
    """Which key an ENTRY trigger turns, as ``calls.assembler`` reads it: the revenue-acceleration screen turns
    ``revenue_accel_key``, any other trigger by its kind's membership in the two kind sets. A risk signal turns
    none, and a kind in neither set (an entry trigger the assembler does not partition) turns none.
    """
    if role == "risk_signal":
        return None
    if is_revenue_acceleration(ref):
        return cfg.revenue_accel_key
    if ref.kind in cfg.conviction_kinds:
        return "conviction"
    if ref.kind in cfg.confirmation_kinds:
        return "confirmation"
    return None


def identity_of(role: Role, ref: TriggerRef) -> Identity:
    return (role, ref.security_id, ref.kind, ref.event_date, ref.label)


def _refs(card: CallCard) -> Iterable[tuple[Role, TriggerRef]]:
    """Every trigger a card shows, in the order the first copy of an identity is taken: the card's fired
    triggers, then each armed member's and each watch member's own, then the risk signals."""
    for t in card.triggers_fired:
        yield "entry_trigger", t
    for m in (*card.armed_members, *card.watch_members):
        for t in m.triggers:
            yield "entry_trigger", t
    for t in card.risk_signals:
        yield "risk_signal", t


def identities(card: CallCard) -> dict[Identity, tuple[Role, TriggerRef]]:
    """The card's triggers by identity; a re-listed identity (a member-level copy of a card-level trigger)
    keeps its first copy."""
    out: dict[Identity, tuple[Role, TriggerRef]] = {}
    for role, ref in _refs(card):
        out.setdefault(identity_of(role, ref), (role, ref))
    return out


def card_events(cn: CardNights, cfg: CallConfig = DEFAULT_CONFIG) -> tuple[list[TriggerEvent], int]:
    """One card's events and how many live triggers were first recorded before the window.

    Walks the nights oldest first, remembering every identity already shown (the row before the window seeds
    it): an identity seen for the first time on a night is an event dated that night. The previous night is
    the one just before it in the walk (the row before the window, for the first)."""
    seen: set[Identity] = set()
    carried: set[Identity] = set()
    if cn.before is not None and cn.before.card is not None:
        carried = set(identities(cn.before.card))
        seen = set(carried)

    # the card in effect at the as-of: the newest night, else the row before the window
    in_effect = cn.nights[-1] if cn.nights else cn.before
    live_ids: set[Identity] | None = (
        set(identities(in_effect.card)) if in_effect is not None and in_effect.card else None
    )

    events: list[TriggerEvent] = []
    prior = cn.before
    for night in cn.nights:
        if night.card is None:
            prior = night
            continue
        for ident, (role, ref) in identities(night.card).items():
            if ident in seen:
                continue
            seen.add(ident)
            events.append(
                TriggerEvent(
                    thesis_id=cn.thesis_id,
                    thesis_name=cn.thesis_name,
                    security_id=ref.security_id,
                    role=role,
                    kind=ref.kind,
                    event_date=ref.event_date,
                    label=ref.label,
                    grade=ref.grade,
                    key=key_of(ref, role, cfg),
                    recorded_on=night.asof,
                    reconstructed=night.reconstructed,
                    from_state=prior.card.state if prior is not None and prior.card else None,
                    to_state=night.card.state,
                    new_card=prior is None,
                    live=None if live_ids is None else ident in live_ids,
                    sources=tuple(ref.sources),
                )
            )
        prior = night
    carried_live = len(carried & live_ids) if live_ids is not None else 0
    return events, carried_live


def collect(cards: Sequence[CardNights], cfg: CallConfig = DEFAULT_CONFIG) -> Collected:
    """Every counted card's events, in card order, with the counts of what was read."""
    events: list[TriggerEvent] = []
    carried_live = 0
    for cn in cards:
        got, carried = card_events(cn, cfg)
        events += got
        carried_live += carried
    window_nights = [n for cn in cards for n in cn.nights]
    before_unreadable = sum(1 for cn in cards if cn.before is not None and cn.before.card is None)
    return Collected(
        events=tuple(events),
        cards_counted=len(cards),
        cards_with_nights=sum(1 for cn in cards if cn.nights),
        cards_no_call=sum(1 for cn in cards if cn.before is None and not cn.nights),
        nights_read=len(window_nights),
        nights_reconstructed=sum(1 for n in window_nights if n.reconstructed),
        nights_unreadable=sum(1 for n in window_nights if n.card is None) + before_unreadable,
        carried_live=carried_live,
    )


# ---------------------------------------------------------------------------------------------------------
# the tape: sessions since and the since-return
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Series:
    """A company's positive closes, by date ascending, with the two lookups the entry rule needs."""

    dates: tuple[date, ...]
    closes: tuple[float, ...]

    @classmethod
    def of(cls, closes: Mapping[date, float]) -> Series:
        """A non-positive close is not a bar (the shared line math's rule, ``series.aligned``)."""
        pairs = sorted((d, c) for d, c in closes.items() if c is not None and c > 0)
        return cls(tuple(d for d, _ in pairs), tuple(c for _, c in pairs))

    def on_or_after(self, d: date) -> tuple[date, float] | None:
        i = bisect_left(self.dates, d)
        return (self.dates[i], self.closes[i]) if i < len(self.dates) else None

    def on_or_before(self, d: date) -> tuple[date, float] | None:
        i = bisect_right(self.dates, d) - 1
        return (self.dates[i], self.closes[i]) if i >= 0 else None

    def on(self, d: date) -> float | None:
        """The close dated exactly ``d`` (``None`` = no bar that day)."""
        i = bisect_left(self.dates, d)
        return self.closes[i] if i < len(self.dates) and self.dates[i] == d else None


@dataclass(frozen=True)
class SinceMove:
    """The name's as-traded return from the entry bar to the end bar, with IWM on the same two dates. ``status``
    ``priced`` = both bars exist; ``unpriced`` = no bar from the recorded night to the as-of; ``flagged`` = the
    name's price history is on the flagged-price list (``reason``), never priced. ``bench_ret`` is ``None``
    when IWM has no bar on one of those two dates."""

    status: SinceStatus
    reason: str | None = None
    entry_date: date | None = None
    end_date: date | None = None
    ret: float | None = None
    bench_ret: float | None = None


def sessions_since(sessions: Sequence[date], night: date, asof: date) -> int | None:
    """The sessions between the first one on or after ``night`` (the entry bar's) and the last one on or
    before ``asof``, on the shared session calendar: the number of daily steps the since-return spans. ``None``
    = no session in ``[night, asof]``."""
    first = bisect_left(sessions, night)
    last = bisect_right(sessions, asof) - 1
    if first >= len(sessions) or last < first:
        return None
    return last - first


def since_move(own: Series | None, bench: Series, night: date, asof: date) -> SinceMove:
    """The since-return of one name over the span from its first close on or after ``night`` to its last close
    on or before ``asof``; IWM's closes on those same two dates (``None`` when IWM has no bar on either, never
    a different span)."""
    start = own.on_or_after(night) if own is not None else None
    end = own.on_or_before(asof) if own is not None else None
    if start is None or end is None or start[0] > end[0]:
        return SinceMove("unpriced")
    b0, b1 = bench.on(start[0]), bench.on(end[0])
    return SinceMove(
        "priced",
        entry_date=start[0],
        end_date=end[0],
        ret=end[1] / start[1] - 1.0,
        bench_ret=b1 / b0 - 1.0 if b0 is not None and b1 is not None else None,
    )


def week_start(d: date) -> date:
    """The Monday of ``d``'s week: the page's default grouping key."""
    return d - timedelta(days=d.weekday())


# ---------------------------------------------------------------------------------------------------------
# the rows
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Source:
    """One place a trigger points at: ``source`` = the kind as stored ("form4", "price", "xbrl"), ``ref`` = its
    reference, ``url`` = a link (an accession through the EDGAR index with the issuer CIK, or a stored URL;
    ``None`` = none known), ``bar_date`` = the bar a ``price:`` reference names (shown as text, never linked),
    ``detail`` = the computation's inputs, one ``key: value`` line each."""

    source: str
    ref: str | None
    url: str | None
    bar_date: date | None
    detail: tuple[str, ...]


@dataclass(frozen=True)
class LogRow:
    """One trigger, ready to show. ``week`` = the Monday of the recorded night's week; ``recorded_days_after`` =
    the days between the fire date and the recorded night when the fire date is earlier; ``newest`` = the
    recorded night IS the as-of's own night (the page's one accent); ``sessions_since`` = see
    ``sessions_since``."""

    key: str
    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str | None
    role: Role
    kind: Kind
    grade: Grade | None
    turns: KeyName | None
    label: str
    event_date: date | None
    recorded_on: date
    week: date
    recorded_days_after: int | None
    reconstructed: bool
    from_state: State | None
    to_state: State
    new_card: bool
    live: bool | None
    newest: bool
    sessions_since: int | None
    since: SinceMove
    sources: tuple[Source, ...]


@dataclass(frozen=True)
class NoteName:
    """A name the page set aside from the tape, listed on its quiet line: ``reason`` = the flags' labels for a
    flagged one, ``None`` for an unpriced one."""

    security_id: UUID
    ticker: str | None
    reason: str | None


@dataclass(frozen=True)
class SessionsUsed:
    """The session calendar the "sessions since" counts on: IWM's bar dates (``source`` says what stood in when
    IWM has none), ending at the as-of."""

    source: SessionSource
    count: int
    first: date | None
    last: date | None


@dataclass(frozen=True)
class Counts:
    """The work the page shows on its quiet line. Counts of what was read and listed, never a statistic over
    the rows' returns (rule 6)."""

    cards_counted: int
    cards_with_nights: int
    cards_quiet: int  # a call by the as-of, but no night in the window
    cards_no_call: int
    live_cards: int
    not_yet_created: int
    roster_fallback: int
    nights_read: int
    nights_reconstructed: int
    nights_unreadable: int
    entry_rows: int
    risk_rows: int
    carried_live: int
    names_unpriced: int
    names_flagged: int
    rows_no_ticker: int


@dataclass(frozen=True)
class TriggerLog:
    asof: date
    window_days: int
    window_start: date
    rows: tuple[LogRow, ...]
    counts: Counts
    sessions: SessionsUsed
    unpriced: tuple[NoteName, ...]
    flagged: tuple[NoteName, ...]


def window_start(asof: date, window_days: int) -> date:
    """The first night the window reads: ``window_days`` calendar days back from the as-of, inclusive."""
    return asof - timedelta(days=window_days)


def _detail_lines(detail: Mapping[str, Any]) -> tuple[str, ...]:
    """A computation's inputs as ``key: value`` lines, keys as stored and sorted; a value the detector did not
    state (``None``) is left out, since a missing field asserts nothing."""

    def words(v: Any) -> str:
        if isinstance(v, bool):
            return "yes" if v else "no"
        if isinstance(v, (list, tuple)):
            return ", ".join(str(x) for x in v)
        return str(v)

    return tuple(f"{k}: {words(v)}" for k, v in sorted(detail.items()) if v is not None)


def _bar_date(p: Provenance) -> date | None:
    """The bar date of a ``price:<id>:<YYYY-MM-DD>`` reference (``None`` for any other source)."""
    if p.source.casefold() != "price" and not p.ref.startswith("price:"):
        return None
    try:
        return date.fromisoformat(p.ref.rsplit(":", 1)[-1])
    except ValueError:
        return None


def source_of(p: Provenance, cik: str | None) -> Source:
    """One provenance as the page shows it: an accession links to its EDGAR index through the issuer's CIK
    (``filings.source_url``), a ``price:`` reference shows its bar date as text, anything else no link.
    """
    bar = _bar_date(p)
    return Source(
        source=p.source,
        ref=p.ref,
        url=None if bar is not None else source_url(p.ref, cik),
        bar_date=bar,
        detail=_detail_lines(p.detail),
    )


def row_key(e: TriggerEvent) -> str:
    """A short stable id for one trigger on one card (unique within a response)."""
    raw = f"{e.thesis_id}|{e.security_id}|{e.role}|{e.kind.value}|{e.event_date}|{e.label}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def _order(r: LogRow) -> tuple:
    return (
        -r.recorded_on.toordinal(),
        (r.ticker or "￿").casefold(),
        r.thesis_name.casefold(),
        r.role,
        r.kind.value,
        r.label,
        r.event_date or date.min,
    )


def trigger_log(
    collected: Collected,
    *,
    asof: date,
    window_days: int,
    scope: Scope,
    roster_tickers: Mapping[UUID, Mapping[UUID, str]],
    master_tickers: Mapping[UUID, str | None],
    ciks: Mapping[UUID, str | None],
    closes: Mapping[UUID, Mapping[date, float]],
    bench: Mapping[date, float],
    flagged: Mapping[UUID, str],
) -> TriggerLog:
    """The page's payload. ``roster_tickers`` = each counted card's roster as of the as-of (``thesis -> security
    -> ticker``, the ticker the Cockpit's ``?name=`` key wants), ``master_tickers`` the fallback; ``closes`` =
    each unflagged name's ``{day: close}`` up to the as-of, ``bench`` IWM's, ``flagged`` = ``security -> reason``
    for the names whose price history is flagged."""
    bench_series = Series.of(bench)
    series = {sid: Series.of(c) for sid, c in closes.items()}
    calendar, source = session_calendar(
        bench_series.dates, (d for s in series.values() for d in s.dates), asof
    )

    rows: list[LogRow] = []
    unpriced: dict[UUID, NoteName] = {}
    flagged_names: dict[UUID, NoteName] = {}
    for e in collected.events:
        ticker = roster_tickers.get(e.thesis_id, {}).get(e.security_id) or master_tickers.get(
            e.security_id
        )
        if e.security_id in flagged:
            move = SinceMove("flagged", reason=flagged[e.security_id])
            flagged_names.setdefault(
                e.security_id, NoteName(e.security_id, ticker, flagged[e.security_id])
            )
        else:
            move = since_move(series.get(e.security_id), bench_series, e.recorded_on, asof)
            if move.status == "unpriced":
                unpriced.setdefault(e.security_id, NoteName(e.security_id, ticker, None))
        cik = ciks.get(e.security_id)
        lag = (
            (e.recorded_on - e.event_date).days
            if e.event_date and e.event_date < e.recorded_on
            else None
        )
        rows.append(
            LogRow(
                key=row_key(e),
                thesis_id=e.thesis_id,
                thesis_name=e.thesis_name,
                security_id=e.security_id,
                ticker=ticker,
                role=e.role,
                kind=e.kind,
                grade=e.grade,
                turns=e.key,
                label=e.label,
                event_date=e.event_date,
                recorded_on=e.recorded_on,
                week=week_start(e.recorded_on),
                recorded_days_after=lag,
                reconstructed=e.reconstructed,
                from_state=e.from_state,
                to_state=e.to_state,
                new_card=e.new_card,
                live=e.live,
                newest=e.recorded_on == asof,
                sessions_since=sessions_since(calendar, e.recorded_on, asof),
                since=move,
                sources=tuple(source_of(p, cik) for p in e.sources),
            )
        )
    rows.sort(key=_order)

    def by_ticker(n: NoteName) -> tuple[str, str]:
        return ((n.ticker or "").casefold(), str(n.security_id))

    return TriggerLog(
        asof=asof,
        window_days=window_days,
        window_start=window_start(asof, window_days),
        rows=tuple(rows),
        counts=Counts(
            cards_counted=collected.cards_counted,
            cards_with_nights=collected.cards_with_nights,
            cards_quiet=collected.cards_counted
            - collected.cards_with_nights
            - collected.cards_no_call,
            cards_no_call=collected.cards_no_call,
            live_cards=scope.live_cards,
            not_yet_created=scope.not_yet_created,
            roster_fallback=scope.roster_fallback,
            nights_read=collected.nights_read,
            nights_reconstructed=collected.nights_reconstructed,
            nights_unreadable=collected.nights_unreadable,
            entry_rows=sum(1 for r in rows if r.role == "entry_trigger"),
            risk_rows=sum(1 for r in rows if r.role == "risk_signal"),
            carried_live=collected.carried_live,
            names_unpriced=len(unpriced),
            names_flagged=len(flagged_names),
            rows_no_ticker=sum(1 for r in rows if r.ticker is None),
        ),
        sessions=SessionsUsed(
            source=source,
            count=len(calendar),
            first=calendar[0] if calendar else None,
            last=calendar[-1] if calendar else None,
        ),
        unpriced=tuple(sorted(unpriced.values(), key=by_ticker)),
        flagged=tuple(sorted(flagged_names.values(), key=by_ticker)),
    )
