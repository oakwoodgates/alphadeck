"""Rotation Quadrant (``docs/dashboards/rotation-quadrant.md``): each group's strength vs SPY and that strength's
momentum, with an 8-week tail, so a group turning up shows before it leads. Rows are GROUPS (a link of a card, a
business type, a sector, or a card), plotted as one dot each in four quadrants: leading, weakening, lagging,
improving. Follows the as-of dial.

PURE: the router reads the counted cards' rosters as of the as-of (``dashboards/baskets.py``), every company's
closes and SPY's through the point-in-time view, the master's business types and sectors and the price flags,
and hands them to ``quadrant_inputs(...)``, which builds every group's line and measures for all four groupings
ONCE (cached per as-of, ``dashboards/cache.py``). ``rotation_quadrant(...)`` then picks one grouping per
request. Display only: nothing here feeds a call, scores an idea or writes anything (rule 1).

The rules, in one place. These are our own simple measures, stated here and on the page; not a published
methodology.
- **The line.** Each group's equal-weighted line over the shared sessions ending at the as-of, through the
  shared line math (``dashboards/series.py``: the Narrative Index's calendar and chain), over a year of
  sessions (``READ_SESSIONS``; the measures need ``MEASURE_SPAN``). SPY's line runs through the same chain (one
  member). Who counts and the groups are ``dashboards/baskets.py``'s: Context and Bench left out unless
  ``include_silent``; in the Link view a company in two links of a card counts in both; in the Business type,
  Sector and Card views each company counts ONCE per group.
- **RS ratio** = the group's line ÷ SPY's line, from the later of the two lines' starts (the session before the
  first one any member contributed a daily return to): before that the group's line is only carried flat.
- **x = RS level** = 100 × today's RS ratio ÷ its own trailing ``LEVEL_SESSIONS``-session average (the
  ``LEVEL_SESSIONS`` sessions ending today, today included). 100 = at its 13-week average.
- **y = RS momentum** = 100 × today's x ÷ x ``MOMENTUM_SESSIONS`` sessions ago. 100 = flat.
- **Quadrants** (on the values as shown, rounded to 2 decimals): leading (x > 100, y > 100), weakening
  (x > 100, y <= 100), lagging (x <= 100, y <= 100), improving (x <= 100, y > 100).
- **The tail** = x / y every ``TAIL_STEP`` sessions back, ``TAIL_POINTS`` points (8 weeks), oldest first. "4 weeks
  ago" = ``AGO_SESSIONS`` sessions back (a tail point).
- **Priced** = a name with a daily return into one of the last ``PRICED_SESSIONS`` sessions (the 13 weeks x
  averages over). A group needs ``MIN_PRICED`` priced names to be measured; fewer = "too small", listed with
  "—", never dropped. A group without the history for today's x or y = "not enough history" (counted).
- **A flagged price history.** A company whose data flags reach its price readings at the as-of
  (``dashboards.baskets.price_flags``) is left out of every group's line: its bars are never read (neither the
  line nor the session calendar). It still counts in ``names``, never in ``priced``, and each group lists it
  (``flagged``: its ticker and the flags' labels). The Narrative Index's rule.
- **Default order:** quadrant (improving, leading, weakening, lagging), then x highest first; groups without a
  quadrant last, ties by label.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from typing import Literal
from uuid import UUID

from dashboards.baskets import (
    GROUP_BYS,
    GroupBy,
    Membership,
    NameCard,
    buckets,
    companies_in,
    counted_rows,
    display_ticker,
)
from dashboards.series import YEAR_SESSIONS, Chain, SessionSource, aligned, chain, session_calendar

# the line's start: moved to the shared line math (``dashboards/series.py``) with the Chain Wave, re-exported here
# under its old name
from dashboards.series import line_start as line_start

NEUTRAL = 100.0
# x: the RS ratio vs its own trailing average over this many sessions (13 weeks)
LEVEL_SESSIONS = 65
# y: x vs x this many sessions ago (4 weeks)
MOMENTUM_SESSIONS = 20
# the tail: a point every TAIL_STEP sessions back, TAIL_POINTS of them (8 weeks), oldest first
TAIL_STEP = 5
TAIL_POINTS = 9
# "the quadrant 4 weeks ago": this many sessions back (a tail point)
AGO_SESSIONS = 20
# a name is priced with a daily return into one of the last PRICED_SESSIONS sessions; a group needs MIN_PRICED
PRICED_SESSIONS = LEVEL_SESSIONS
MIN_PRICED = 3
# the sessions the oldest tail point's y reads: its x 20 sessions before it, that x's 65-session average
MEASURE_SPAN = TAIL_STEP * (TAIL_POINTS - 1) + MOMENTUM_SESSIONS + LEVEL_SESSIONS
# the sessions read (daily returns): a year, the Narrative Index's own 1Y read, comfortably past MEASURE_SPAN
READ_SESSIONS = YEAR_SESSIONS
BENCHMARK = "SPY"

Quadrant = Literal["leading", "weakening", "lagging", "improving"]
# the default order: a group turning up first
QUADRANT_ORDER: tuple[Quadrant, ...] = ("improving", "leading", "weakening", "lagging")
Status = Literal["plotted", "too_small", "no_history"]


# --- the measures ------------------------------------------------------------------------------------------


def quadrant(x: float | None, y: float | None) -> Quadrant | None:
    """The quadrant of ``(x, y)``; exactly 100 is never above it."""
    if x is None or y is None:
        return None
    if x > NEUTRAL:
        return "leading" if y > NEUTRAL else "weakening"
    return "improving" if y > NEUTRAL else "lagging"


def rs_ratio(group: Chain, spy: Chain | None) -> tuple[float | None, ...]:
    """The group's line ÷ SPY's on each session, from the later of the two lines' starts (``None`` before it,
    and everywhere when either line never steps)."""
    gs, ss = line_start(group), line_start(spy)
    if gs is None or ss is None or spy is None:
        return (None,) * len(group.levels)
    start = max(gs, ss)
    return tuple(
        g / s if t >= start else None for t, (g, s) in enumerate(zip(group.levels, spy.levels))
    )


def rs_level(rs: Sequence[float | None], t: int) -> float | None:
    """x on session ``t``: 100 × the RS ratio ÷ its mean over the ``LEVEL_SESSIONS`` sessions ending at ``t``
    (``t`` included); ``None`` without all of them."""
    lo = t - (LEVEL_SESSIONS - 1)
    if lo < 0 or t >= len(rs):
        return None
    window = rs[lo : t + 1]
    if any(v is None for v in window):
        return None
    now = rs[t]
    assert now is not None
    return NEUTRAL * now / (sum(v for v in window if v is not None) / LEVEL_SESSIONS)


def rs_momentum(rs: Sequence[float | None], t: int) -> float | None:
    """y on session ``t``: 100 × x on ``t`` ÷ x ``MOMENTUM_SESSIONS`` sessions before it."""
    now, then = rs_level(rs, t), rs_level(rs, t - MOMENTUM_SESSIONS)
    if now is None or then is None:
        return None
    return NEUTRAL * now / then


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


@dataclass(frozen=True)
class TailPoint:
    """x / y on one session (``session`` None = before the first session read; ``None`` = not measurable)."""

    session: date | None
    x: float | None
    y: float | None
    quadrant: Quadrant | None


def point(rs: Sequence[float | None], sessions: Sequence[date], t: int) -> TailPoint:
    """x, y and the quadrant on session ``t``, rounded as shown; the quadrant is read off the rounded values."""
    if t < 0 or t >= len(sessions):
        return TailPoint(None, None, None, None)
    x, y = _round(rs_level(rs, t)), _round(rs_momentum(rs, t))
    return TailPoint(sessions[t], x, y, quadrant(x, y))


def tail(rs: Sequence[float | None], sessions: Sequence[date]) -> tuple[TailPoint, ...]:
    """``TAIL_POINTS`` points, every ``TAIL_STEP`` sessions back from the last one, oldest first."""
    end = len(sessions) - 1
    return tuple(point(rs, sessions, end - TAIL_STEP * k) for k in range(TAIL_POINTS - 1, -1, -1))


def priced(closes: Sequence[float | None], end: int) -> bool:
    """A daily return into one of the last ``PRICED_SESSIONS`` sessions (a close on both ``t-1`` and ``t``)."""
    lo = max(1, end - PRICED_SESSIONS + 1)
    return any(closes[t - 1] is not None and closes[t] is not None for t in range(lo, end + 1))


def _flat_in_span(ch: Chain, start: int | None) -> int:
    """Sessions in the measures' span, after the line's start, that no member could contribute to."""
    if start is None:
        return 0
    end = len(ch.contributors) - 1
    lo = max(start + 1, end - MEASURE_SPAN + 1)
    return sum(1 for t in range(lo, end + 1) if ch.contributors[t] == 0)


# --- the groups --------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class QuadrantName:
    security_id: UUID
    ticker: str
    cards: tuple[NameCard, ...]
    priced: bool
    flag: str | None = None  # why its price history is left out (``price_flags``); None = read


@dataclass(frozen=True)
class FlaggedName:
    """A company in a group left out of its line: its price history is flagged at the as-of."""

    ticker: str
    reason: str  # the flags' labels ("mixed-scale price history")


@dataclass(frozen=True)
class QuadrantGroup:
    key: str
    label: (
        str  # the sort's tie-break, as grouped (the page words its own label from the fields below)
    )
    link: str | None = (
        None  # Link view: the link (None = Unsegmented; "Context" / "Bench" when silent)
    )
    thesis_id: UUID | None = None  # Link and Card views
    thesis_name: str | None = None
    business_type: str | None = None  # Business type view (None = Unclassified)
    supersector: str | None = None  # Sector view (None = Unclassified)
    names: int = 0  # companies in the group, each once; flagged ones included
    priced: int = 0  # of those, names priced (never a flagged one)
    status: Status = "too_small"
    x: float | None = None  # today's RS level (shown for a group with too little history for y)
    y: float | None = None
    quadrant: Quadrant | None = None
    quadrant_ago: Quadrant | None = None  # AGO_SESSIONS sessions back
    tail: tuple[
        TailPoint, ...
    ] = ()  # TAIL_POINTS, oldest first; empty for a group too small to measure
    sessions_flat: int = 0  # sessions in the measures' span its line carried flat
    members: tuple[QuadrantName, ...] = ()
    flagged: tuple[
        FlaggedName, ...
    ] = ()  # names left out for a flagged price history, member order


def _name_sort_key(m: QuadrantName) -> tuple:
    return (m.flag is not None, not m.priced, m.ticker.casefold(), str(m.security_id))


def measure_group(
    key: str,
    label: str,
    rows: Sequence[Membership],
    closes: Mapping[UUID, Sequence[float | None]],
    priced_ids: frozenset[UUID] | set[UUID],
    flags: Mapping[UUID, str],
    spy: Chain | None,
    sessions: Sequence[date],
    **meta: object,
) -> QuadrantGroup:
    """One group over its rows: its line (each company once), its measures and its names. ``closes`` = each
    company's closes on the sessions (a flagged company's never read); ``priced_ids`` = the priced companies.
    """
    companies = companies_in(rows)
    n = len(sessions)
    no_bars: tuple[float | None, ...] = (None,) * n
    members = tuple(
        sorted(
            (
                QuadrantName(
                    security_id=sid,
                    ticker=display_ticker(cards),
                    cards=cards,
                    priced=sid in priced_ids,
                    flag=flags.get(sid),
                )
                for sid, cards in companies
            ),
            key=_name_sort_key,
        )
    )
    flagged = tuple(FlaggedName(m.ticker, m.flag) for m in members if m.flag is not None)
    n_priced = sum(1 for m in members if m.priced)
    base = QuadrantGroup(
        key=key,
        label=label,
        names=len(members),
        priced=n_priced,
        members=members,
        flagged=flagged,
        **meta,  # type: ignore[arg-type]
    )
    if n_priced < MIN_PRICED:
        return base
    ch = chain(
        [closes.get(sid, no_bars) if sid not in flags else no_bars for sid, _ in companies], n
    )
    rs = rs_ratio(ch, spy)
    now = point(rs, sessions, n - 1)
    ago = point(rs, sessions, n - 1 - AGO_SESSIONS)
    return replace(
        base,
        status="plotted" if now.x is not None and now.y is not None else "no_history",
        x=now.x,
        y=now.y,
        quadrant=now.quadrant,
        quadrant_ago=ago.quadrant,
        tail=tail(rs, sessions),
        sessions_flat=_flat_in_span(ch, line_start(ch)),
    )


def group_sort_key(g: QuadrantGroup) -> tuple:
    """Quadrant (improving, leading, weakening, lagging), then x highest first; no quadrant last; ties by
    label: the default order."""
    if g.quadrant is None:
        return (1, 0, 0.0, g.label.casefold(), g.key)
    return (0, QUADRANT_ORDER.index(g.quadrant), -(g.x or 0.0), g.label.casefold(), g.key)


# --- the cached read: every grouping's groups ------------------------------------------------------------------


@dataclass(frozen=True)
class QuadrantInputs:
    """Everything the heavy read produces: cached per (clock, include_silent, as-of), every grouping built."""

    live_cards: int  # unarchived cards today
    cards_counted: int  # those that existed by the as-of
    cards_not_yet_created: int  # created after the as-of: left out
    roster_fallback_cards: (
        int  # counted cards with no roster snapshot by then: today's roster stands in
    )
    include_silent: bool
    basket_rows: int  # every row on the counted cards, before any filter
    silent_rows_left_out: int
    unresolved_rows: int  # rows in view with no resolved security: counted, never grouped
    sessions: tuple[date, ...]  # the sessions read, oldest first
    session_source: SessionSource
    spy_available: bool  # SPY has a bar in the sessions read (no SPY = nothing can be measured)
    companies: int  # distinct resolved companies in view
    priced_companies: (
        int  # of those, priced (a daily return into one of the last PRICED_SESSIONS sessions)
    )
    flagged_companies: int  # of those, left out of every line for a flagged price history
    groupings: Mapping[GroupBy, tuple[QuadrantGroup, ...]]  # each in the default order


def quadrant_inputs(
    *,
    memberships: Sequence[Membership],
    closes: Mapping[UUID, Mapping[date, float]],
    spy: Mapping[date, float],
    identity: Mapping[UUID, Mapping[str, object]],
    asof: date,
    include_silent: bool,
    live_cards: int,
    cards_counted: int,
    cards_not_yet_created: int,
    roster_fallback_cards: int,
    flagged: Mapping[UUID, str] | None = None,
) -> QuadrantInputs:
    """Every grouping's groups over one shared calendar. ``closes`` = each company's ``{day: close}`` up to the
    as-of; ``spy`` = SPY's (empty = no tape); ``identity`` = each company's ``business_type`` and
    ``business_supersector`` (``securities.master.identity_for``); ``flagged`` = the companies whose data
    flags reach their price readings, each with its reason (``dashboards.baskets.price_flags``): their closes
    are never read."""
    flags = dict(flagged or {})
    counted = counted_rows(memberships, include_silent)
    in_view = {m.security_id for m in counted.rows if m.security_id is not None}
    read = {sid for sid in in_view if sid not in flags}  # a flagged company's bars are never read

    sessions, source = session_calendar(
        spy, (d for sid in read for d in closes.get(sid, {})), asof, READ_SESSIONS
    )
    n = len(sessions)
    spy_closes = aligned(spy, sessions)
    spy_chain = chain([spy_closes], n) if any(c is not None for c in spy_closes) else None
    aligned_closes = {sid: aligned(closes.get(sid, {}), sessions) for sid in read}
    priced_ids = {sid for sid, cl in aligned_closes.items() if priced(cl, n - 1)}

    groupings: dict[GroupBy, tuple[QuadrantGroup, ...]] = {}
    for group_by in GROUP_BYS:
        groups = [
            measure_group(
                key, label, rows, aligned_closes, priced_ids, flags, spy_chain, sessions, **meta
            )
            for key, (rows, label, meta) in buckets(counted.rows, group_by, identity).items()
        ]
        groups.sort(key=group_sort_key)
        groupings[group_by] = tuple(groups)

    return QuadrantInputs(
        live_cards=live_cards,
        cards_counted=cards_counted,
        cards_not_yet_created=cards_not_yet_created,
        roster_fallback_cards=roster_fallback_cards,
        include_silent=include_silent,
        basket_rows=counted.basket_rows,
        silent_rows_left_out=counted.silent_rows_left_out,
        unresolved_rows=counted.unresolved_rows,
        sessions=sessions,
        session_source=source,
        spy_available=spy_chain is not None,
        companies=len(in_view),
        priced_companies=len(priced_ids),
        flagged_companies=len(in_view) - len(read),
        groupings=groupings,
    )


# --- one view -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RotationQuadrant:
    asof: date
    group_by: GroupBy
    include_silent: bool
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int
    sessions: int  # daily returns read
    first_session: date | None
    last_session: date | None
    session_source: SessionSource
    spy_available: bool
    companies: int
    priced_companies: int
    flagged_companies: int
    groups_plotted: int
    groups_too_small: int
    groups_no_history: int
    groups: tuple[QuadrantGroup, ...]


def rotation_quadrant(inputs: QuadrantInputs, *, asof: date, group_by: GroupBy) -> RotationQuadrant:
    """The page for one grouping, groups in the default order, with the counts its quiet line shows."""
    groups = inputs.groupings[group_by]

    def count(status: Status) -> int:
        return sum(1 for g in groups if g.status == status)

    return RotationQuadrant(
        asof=asof,
        group_by=group_by,
        include_silent=inputs.include_silent,
        live_cards=inputs.live_cards,
        cards_counted=inputs.cards_counted,
        cards_not_yet_created=inputs.cards_not_yet_created,
        roster_fallback_cards=inputs.roster_fallback_cards,
        basket_rows=inputs.basket_rows,
        silent_rows_left_out=inputs.silent_rows_left_out,
        unresolved_rows=inputs.unresolved_rows,
        sessions=max(0, len(inputs.sessions) - 1),
        first_session=inputs.sessions[0] if inputs.sessions else None,
        last_session=inputs.sessions[-1] if inputs.sessions else None,
        session_source=inputs.session_source,
        spy_available=inputs.spy_available,
        companies=inputs.companies,
        priced_companies=inputs.priced_companies,
        flagged_companies=inputs.flagged_companies,
        groups_plotted=count("plotted"),
        groups_too_small=count("too_small"),
        groups_no_history=count("no_history"),
        groups=groups,
    )
