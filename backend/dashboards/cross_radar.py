"""Cross Radar (``docs/dashboards/cross-radar.md``): golden and death crosses (the 50-day SMA crossing the
200-day) by group across every live card, at the as-of dial. Four reads, loudest first: CLUSTERS (several
names of a group crossing the same way within 10 sessions), FIRST MOVERS (exactly one name of a group crossed
in that window), the NEAR-CROSS watch (names whose 50d is within 2% of the 200d and narrowing), and the cross
TIMELINE (every cross of the last 90 sessions, per group, beside the group's posture bar).

PURE: the router reads the cards (``dashboards/baskets.py``, shared with the Rotation Map), walks each company's
price history once and hands the readings in as ``RadarInputs`` (cached per as-of, ``dashboards/cache.py``);
``cross_radar(...)`` groups them per request. Display only: nothing here feeds a call, scores an idea or
writes anything (rule 1).

The rules, in one place:
- **A cross** is the 50d SMA crossing the 200d SMA: golden = the 50d moves above the 200d, death = it moves
  below. The SMAs are the Cockpit's own arithmetic (``signals/display/sma.py``'s ``_sma_series``) and the walk
  is its ``_last_flip``'s: exact zeros are skipped (a touch is not a cross) and a cross is stamped on the first
  bar on the far side. The member keeps only each name's most recent cross; this walk keeps EVERY cross of the
  last ``HISTORY_SESSIONS`` sessions.
- **Sessions** are each company's own trading bars up to the as-of: "N sessions ago" counts bars back from its
  last bar (0 = the as-of's own bar); "within 10 sessions" is its last 10 bars.
- **Priced** = enough bars for a 200d SMA at its last bar, and that bar no more than ``sma.STALE_GAP_DAYS``
  calendar days before the as-of (the member's own stale-tape tell). Anything else is counted as not priced
  (``short_tape`` / ``stale_tape``), never dropped and never read as "no cross".
- **A name's recent cross** is its LATEST cross within ``RECENT_SESSIONS`` (a name that crossed up then down
  inside the window counts once, as down).
- **Cluster:** at least ``CLUSTER_MIN_NAMES`` names AND at least ``CLUSTER_MIN_SHARE_PCT``% of the group's
  priced names have the same recent cross. No weighting.
- **First mover:** a group of at least ``FIRST_MOVER_MIN_PRICED`` priced names where EXACTLY ONE has a recent
  golden (or death) cross; the rest are counted as closing in / already above (below) / not yet.
- **Near cross:** a priced name with no cross within ``RECENT_SESSIONS``, its gap ``|SMA50/SMA200 - 1|`` at
  most ``NEAR_BAND_PCT``% and smaller than ``NEAR_LOOKBACK`` sessions before. It states the tape only, never a
  forecast.
- **Who counts / groups:** exactly the Rotation Map's (``dashboards/baskets.py``): Context and Bench left out
  unless ``include_silent``; in the Link view a company in two links counts in both, elsewhere once per group.
- **The price x 200d layer** (``price_200d``) adds price-crosses-the-200d ticks to the timeline only; it never
  counts toward a cluster, a first mover or the near-cross watch.
- **A flagged price history.** A company whose data flags reach its price readings at the as-of
  (``dashboards.baskets.price_flags``, the reader the Cockpit's grayed price cells come from) is left out of
  everything: a scale break makes fake crosses. It is never priced, never in a cluster, a first mover, the
  near-cross watch, a timeline count or the posture bar, and its bars never shape the timeline's axis. It
  still counts in its group's ``names`` (``flagged`` says how many; never in ``not_priced``), its timeline row
  carries no cross and its ``flag``, and the page names it (``flagged``).
"""

from __future__ import annotations

from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID

from dashboards.baskets import (
    FlaggedCompany,
    GroupBy,
    Membership,
    NameCard,
    buckets,
    companies_in,
    counted_rows,
    display_ticker,
    flagged_in_view,
)
from signals.display import sma
from signals.display.base import DisplaySignal

# Deliberately the SAME arithmetic as the Cockpit's sma_position member (its 50d / 200d lines and its posture
# chip), imported rather than copied so the radar's crosses can never drift from the member's. ``sma.py`` is
# not modified for this page.
from signals.display.sma import _sma_series

Direction = Literal["golden", "death"]
PriceDirection = Literal["up", "down"]
NotPriced = Literal["short_tape", "stale_tape"]

# every cross of the last this-many sessions is kept (the timeline's width)
HISTORY_SESSIONS = 90
# "recent": the cluster and first-mover window, and the near-cross watch's "no cross since"
RECENT_SESSIONS = 10
# a cluster needs at least this many names AND at least this share of the group's priced names
CLUSTER_MIN_NAMES = 2
CLUSTER_MIN_SHARE_PCT = 40
# a first mover leads a group of at least this many priced names (a one-name group has no one to lead)
FIRST_MOVER_MIN_PRICED = 2
# the near-cross band (|SMA50/SMA200 - 1|, in percent) and how far back "narrowing" looks
NEAR_BAND_PCT = 2.0
NEAR_LOOKBACK = 5
# the gap is rounded to this many decimals (of a percent) before the band test, so 2.0% reads as 2.0%, not
# 2.0000000000000018%
GAP_DECIMALS = 4

# the display members this dashboard reads (by registered name)
MEMBERS: tuple[str, ...] = ("sma_position", "relative_strength")

# sma_position's four posture keys; every other key (partial_*, level_*, *_flat, *_na) and no reading = "other"
POSTURES: tuple[str, ...] = ("above_rising", "above_falling", "below_rising", "below_falling")


# --- the walk -----------------------------------------------------------------------------------------------


def cross_history(
    dates: Sequence[date], diffs: Sequence[float | None], window: int = HISTORY_SESSIONS
) -> list[tuple[date, int, int]]:
    """Every sign flip of a diff series whose bar is within the last ``window`` bars, oldest first, as
    ``(date, sessions_ago, sign)`` (sign ``+1`` = moved above, ``-1`` = moved below; ``sessions_ago`` 0 = the
    last bar). ``sma._last_flip``'s walk, keeping every flip: exact zeros and ``None`` are skipped (a touch is
    not a cross; a cross THROUGH the line stamps the first bar on the far side), and the walk reads the whole
    series, so a flip on the window's first bar still sees the sign before it."""
    n = len(dates)
    prev = 0
    out: list[tuple[date, int, int]] = []
    for i, (d, diff) in enumerate(zip(dates, diffs)):
        if diff is None or diff == 0.0:
            continue
        sign = 1 if diff > 0.0 else -1
        if prev and sign != prev and n - 1 - i < window:
            out.append((d, n - 1 - i, sign))
        prev = sign
    return out


def _gap_pct(fast: float | None, slow: float | None) -> float | None:
    """``(SMA50 / SMA200 - 1) * 100``; ``None`` without both lines (or a non-positive 200d)."""
    if fast is None or slow is None or slow <= 0.0:
        return None
    return round((fast / slow - 1.0) * 100.0, GAP_DECIMALS)


@dataclass(frozen=True)
class CrossEvent:
    d: date
    direction: str  # golden / death for a 50d x 200d cross, up / down for a price x 200d cross
    sessions_ago: int  # bars back from the company's last bar (0 = the as-of's own bar)


@dataclass(frozen=True)
class CompanyCrosses:
    """One company's readings at the as-of, cached per as-of. ``crosses`` / ``price_200d`` oldest first."""

    bars: int  # bars with a close in the price window
    last_bar: date | None
    priced: bool
    not_priced: NotPriced | None = None
    crosses: tuple[
        CrossEvent, ...
    ] = ()  # every 50d x 200d cross of the last HISTORY_SESSIONS sessions
    price_200d: tuple[CrossEvent, ...] = ()  # every price x 200d cross of the same sessions
    # of the last HISTORY_SESSIONS sessions, how many a cross could be stamped on (a name with under
    # 200 + HISTORY_SESSIONS bars reads a shorter history)
    readable_sessions: int = 0
    gap_pct: float | None = None  # (SMA50 / SMA200 - 1) %, at the last bar
    gap_pct_then: float | None = None  # the same NEAR_LOOKBACK sessions before
    posture: str = "other"  # one of POSTURES, else "other"
    rs_leading: bool | None = None  # relative_strength headline == "leading"; None = no RS reading


def _rs_leading(rs: DisplaySignal | None) -> bool | None:
    return None if rs is None else (rs.headline is not None and rs.headline.key == "leading")


def recent_dates(bars: Iterable[Mapping[str, Any]]) -> list[date]:
    """The dates of a company's last ``HISTORY_SESSIONS`` bars with a close (the timeline's sessions)."""
    return [b["d"] for b in bars if b.get("close") is not None][-HISTORY_SESSIONS:]


def company_crosses(
    bars: Sequence[Mapping[str, Any]],
    asof: date,
    sma_sig: DisplaySignal | None,
    rs_sig: DisplaySignal | None,
) -> CompanyCrosses:
    """Read one company off its ascending EOD bars (``pit.price_history(sid, lookback_days=
    sma.LOOKBACK_DAYS)``) and its ``sma_position`` / ``relative_strength`` signals."""
    rows = [b for b in bars if b.get("close") is not None]
    closes = [float(b["close"]) for b in rows]
    dates = [b["d"] for b in rows]
    n = len(rows)
    rs = _rs_leading(rs_sig)
    if n == 0:
        return CompanyCrosses(
            bars=0, last_bar=None, priced=False, not_priced="short_tape", rs_leading=rs
        )
    fast = _sma_series(closes, sma.SMA_FAST)
    slow = _sma_series(closes, sma.SMA_SLOW)
    reason: NotPriced | None = None
    if slow[-1] is None:
        reason = "short_tape"
    elif (asof - dates[-1]).days > sma.STALE_GAP_DAYS:
        reason = "stale_tape"
    if reason is not None:
        return CompanyCrosses(
            bars=n, last_bar=dates[-1], priced=False, not_priced=reason, rs_leading=rs
        )

    fast_vs_slow = [f - s if f is not None and s is not None else None for f, s in zip(fast, slow)]
    price_vs_slow = [c - s if s is not None else None for c, s in zip(closes, slow)]
    crosses = tuple(
        CrossEvent(d, "golden" if sign > 0 else "death", ago)
        for d, ago, sign in cross_history(dates, fast_vs_slow)
    )
    price = tuple(
        CrossEvent(d, "up" if sign > 0 else "down", ago)
        for d, ago, sign in cross_history(dates, price_vs_slow)
    )
    then = n - 1 - NEAR_LOOKBACK
    headline = sma_sig.headline if sma_sig is not None else None
    return CompanyCrosses(
        bars=n,
        last_bar=dates[-1],
        priced=True,
        crosses=crosses,
        price_200d=price,
        readable_sessions=max(0, min(HISTORY_SESSIONS, n - sma.SMA_SLOW)),
        gap_pct=_gap_pct(fast[-1], slow[-1]),
        gap_pct_then=_gap_pct(fast[then], slow[then]) if then >= 0 else None,
        posture=headline.key if headline is not None and headline.key in POSTURES else "other",
        rs_leading=rs,
    )


NO_READING = CompanyCrosses(bars=0, last_bar=None, priced=False, not_priced="short_tape")
# a flagged company's reading: no bar read, never priced, and not "not priced" either (it is left out)
LEFT_OUT = CompanyCrosses(bars=0, last_bar=None, priced=False, not_priced=None)


def recent_cross(r: CompanyCrosses) -> CrossEvent | None:
    """The company's LATEST cross, when it printed within ``RECENT_SESSIONS`` sessions."""
    if not r.priced or not r.crosses:
        return None
    last = r.crosses[-1]
    return last if last.sessions_ago < RECENT_SESSIONS else None


def near_cross(r: CompanyCrosses) -> Direction | None:
    """Which cross the name is closing in on, or ``None``: no cross within ``RECENT_SESSIONS``, the gap at
    most ``NEAR_BAND_PCT``% and smaller than ``NEAR_LOOKBACK`` sessions before. 50d under the 200d =
    approaching golden, over = approaching death."""
    if not r.priced or r.gap_pct is None or r.gap_pct_then is None:
        return None
    # no recent cross (a 50d on the other side of the 200d than NEAR_LOOKBACK sessions ago crossed: implied
    # by the cross list on real bars, stated here so the rule holds on its own)
    if recent_cross(r) is not None or r.gap_pct * r.gap_pct_then < 0.0:
        return None
    if abs(r.gap_pct) > NEAR_BAND_PCT or not abs(r.gap_pct) < abs(r.gap_pct_then):
        return None
    # exactly on the line: the side it came from
    side = r.gap_pct if r.gap_pct != 0.0 else r.gap_pct_then
    return "golden" if side < 0 else "death"


def clusters(count: int, priced: int) -> bool:
    """The cluster rule: at least ``CLUSTER_MIN_NAMES`` names AND at least ``CLUSTER_MIN_SHARE_PCT``% of the
    priced names (integer arithmetic, so 2 of 5 is exactly 40% and in)."""
    return count >= CLUSTER_MIN_NAMES and count * 100 >= CLUSTER_MIN_SHARE_PCT * priced


def timeline_sessions(spy_dates: Iterable[date], company_dates: Iterable[date]) -> tuple[date, ...]:
    """The timeline's axis: the last ``HISTORY_SESSIONS`` sessions, SPY's plus any a company printed."""
    return tuple(sorted(set(spy_dates) | set(company_dates))[-HISTORY_SESSIONS:])


# --- the cached inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class RadarInputs:
    """Everything the heavy read produces: cached per as-of, shared by every grouping and toggle."""

    live_cards: int  # unarchived cards today
    cards_counted: int  # those that existed by the as-of
    cards_not_yet_created: int  # created after the as-of: left out
    roster_fallback_cards: (
        int  # counted cards with no roster snapshot by then: today's roster stands in
    )
    memberships: tuple[Membership, ...]
    readings: Mapping[
        UUID, CompanyCrosses
    ]  # every resolved company on the counted cards, silent included
    sessions: tuple[date, ...]  # the timeline's axis (timeline_sessions)
    # companies whose data flags reach their price readings at the as-of, each with its reason
    # (``dashboards.baskets.price_flags``): left out of everything
    flagged: Mapping[UUID, str] = field(default_factory=dict)


# --- the payload -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Group:
    key: str
    label: (
        str  # the sort's tie-break, as shown (the page words its own label from the fields below)
    )
    link: str | None = (
        None  # Link view: the link (None = Unsegmented; "Context" / "Bench" when silent)
    )
    thesis_id: UUID | None = None  # Link and Card views
    thesis_name: str | None = None
    business_type: str | None = None  # Business type view (None = Unclassified)
    supersector: str | None = None  # Sector view (None = Unclassified)
    names: int = 0  # companies in the group, each once (flagged ones included)
    priced: int = 0
    not_priced: int = 0  # short or stale tape (never a flagged one)
    flagged: int = 0  # left out of everything for a flagged price history


@dataclass(frozen=True)
class CrossName:
    """A name on a cluster or first-mover row: its recent cross."""

    security_id: UUID
    ticker: str
    cards: tuple[NameCard, ...]
    d: date
    sessions_ago: int
    rs_leading: bool | None


@dataclass(frozen=True)
class Cluster:
    group: Group
    direction: Direction
    count: int  # names whose recent cross is this direction
    priced: int  # the group's priced names
    share: float  # count / priced
    within: int  # the oldest of those crosses, in sessions (all of them printed within this many)
    rs_leaders: int  # of those names, RS at a 13-week high
    names: tuple[CrossName, ...]  # newest cross first


@dataclass(frozen=True)
class FirstMover:
    group: Group
    direction: Direction
    leader: CrossName
    rest: int  # the group's other priced names
    closing_in: int  # of those, near-crossing the same way
    already: int  # of those, already on the far side (golden: 50d over 200d; death: under) with no recent cross
    not_yet: int  # the rest of the rest


@dataclass(frozen=True)
class NearName:
    security_id: UUID
    ticker: str
    cards: tuple[NameCard, ...]
    toward: Direction
    gap_pct: float  # (SMA50 / SMA200 - 1) %, at the as-of
    gap_pct_then: float  # NEAR_LOOKBACK sessions before
    change: float  # gap_pct - gap_pct_then, in points


@dataclass(frozen=True)
class NearGroup:
    group: Group
    names: tuple[NearName, ...]  # smallest gap first


@dataclass(frozen=True)
class Mark:
    """One cross on the timeline."""

    d: date
    direction: str  # golden / death; up / down on the price x 200d layer
    sessions_ago: int
    pos: int  # its column on the axis (0 = the oldest session; a cross older than the axis pins to 0)


@dataclass(frozen=True)
class RadarName:
    security_id: UUID
    ticker: str
    cards: tuple[NameCard, ...]
    priced: bool
    not_priced: NotPriced | None
    bars: int
    last_bar: date | None
    readable_sessions: int
    crosses: tuple[Mark, ...]  # newest first
    price_200d: tuple[Mark, ...]  # newest first; empty unless the layer is on
    gap_pct: float | None
    posture: str
    rs_leading: bool | None
    flag: str | None = None  # why it is left out (a flagged price history); None = read


@dataclass(frozen=True)
class Posture:
    above_rising: int = 0
    above_falling: int = 0
    below_rising: int = 0
    below_falling: int = 0
    other: int = (
        0  # every other posture key, no reading, and every name not priced (never a flagged one)
    )


@dataclass(frozen=True)
class TimelineRow:
    group: Group
    golden: int  # golden crosses in the last HISTORY_SESSIONS sessions (every cross, not names)
    death: int
    price_up: int  # price x 200d crosses up (0 unless the layer is on)
    price_down: int
    posture: Posture
    members: tuple[RadarName, ...]  # most recent cross first, names without one after


@dataclass(frozen=True)
class CrossRadar:
    asof: date
    group_by: GroupBy
    include_silent: bool
    price_200d: bool
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int  # every row on the counted cards, before any filter
    silent_rows_left_out: int
    unresolved_rows: int  # rows in view with no resolved security: counted, never grouped
    companies: int  # distinct resolved companies in view
    priced_companies: int
    short_tape_companies: int  # not priced: too few bars for a 200d SMA
    stale_tape_companies: int  # not priced: the last bar is too old
    sessions: tuple[date, ...]
    clusters: tuple[Cluster, ...]
    first_movers: tuple[FirstMover, ...]
    near_cross: tuple[NearGroup, ...]
    timeline: tuple[TimelineRow, ...]
    # companies in view left out for a flagged price history, by ticker
    flagged: tuple[FlaggedCompany, ...] = ()


def _pos(sessions: Sequence[date], d: date) -> int:
    if not sessions:
        return 0
    return min(bisect_left(sessions, d), len(sessions) - 1)


def _marks(events: Iterable[CrossEvent], sessions: Sequence[date]) -> tuple[Mark, ...]:
    return tuple(
        Mark(e.d, e.direction, e.sessions_ago, _pos(sessions, e.d))
        for e in sorted(events, key=lambda e: e.sessions_ago)
    )


def _cross_name(
    sid: UUID, cards: tuple[NameCard, ...], r: CompanyCrosses, e: CrossEvent
) -> CrossName:
    return CrossName(sid, display_ticker(cards), cards, e.d, e.sessions_ago, r.rs_leading)


def _sort_names(names: Iterable[CrossName]) -> tuple[CrossName, ...]:
    return tuple(
        sorted(names, key=lambda c: (c.sessions_ago, c.ticker.casefold(), str(c.security_id)))
    )


def _group_view(
    group: Group,
    companies: Sequence[tuple[UUID, tuple[NameCard, ...]]],
    readings: Mapping[UUID, CompanyCrosses],
    sessions: Sequence[date],
    price_200d: bool,
    flagged: Mapping[UUID, str],
) -> tuple[list[Cluster], list[FirstMover], NearGroup | None, TimelineRow]:
    priced = [(sid, cards, readings[sid]) for sid, cards in companies if readings[sid].priced]

    found: list[Cluster] = []
    movers: list[FirstMover] = []
    for direction in ("golden", "death"):
        crossed = [
            (sid, cards, r, e)
            for sid, cards, r in priced
            if (e := recent_cross(r)) is not None and e.direction == direction
        ]
        if clusters(len(crossed), len(priced)):
            found.append(
                Cluster(
                    group=group,
                    direction=direction,
                    count=len(crossed),
                    priced=len(priced),
                    share=round(len(crossed) / len(priced), 4),
                    within=max(e.sessions_ago for *_, e in crossed) + 1,
                    rs_leaders=sum(1 for _, _, r, _ in crossed if r.rs_leading),
                    names=_sort_names(_cross_name(*c) for c in crossed),
                )
            )
        if len(crossed) == 1 and len(priced) >= FIRST_MOVER_MIN_PRICED:
            lead_sid = crossed[0][0]
            rest = [r for sid, _, r in priced if sid != lead_sid]
            closing = sum(1 for r in rest if near_cross(r) == direction)
            already = sum(
                1
                for r in rest
                if recent_cross(r) is None
                and r.gap_pct is not None
                and (r.gap_pct > 0.0 if direction == "golden" else r.gap_pct < 0.0)
            )
            movers.append(
                FirstMover(
                    group=group,
                    direction=direction,
                    leader=_cross_name(*crossed[0]),
                    rest=len(rest),
                    closing_in=closing,
                    already=already,
                    not_yet=len(rest) - closing - already,
                )
            )

    near: list[NearName] = []
    for sid, cards, r in priced:
        toward = near_cross(r)
        if toward is None:
            continue
        assert r.gap_pct is not None and r.gap_pct_then is not None
        near.append(
            NearName(
                security_id=sid,
                ticker=display_ticker(cards),
                cards=cards,
                toward=toward,
                gap_pct=round(r.gap_pct, 2),
                gap_pct_then=round(r.gap_pct_then, 2),
                change=round(r.gap_pct - r.gap_pct_then, 2),
            )
        )
    near.sort(key=lambda x: (abs(x.gap_pct), x.ticker.casefold(), str(x.security_id)))

    members: list[RadarName] = []
    posture = {k: 0 for k in (*POSTURES, "other")}
    for sid, cards in companies:
        r = readings[sid]
        flag = flagged.get(sid)
        if flag is None:  # a flagged name is in no posture count
            posture[r.posture if r.priced else "other"] += 1
        members.append(
            RadarName(
                security_id=sid,
                ticker=display_ticker(cards),
                cards=cards,
                priced=r.priced,
                not_priced=r.not_priced,
                bars=r.bars,
                last_bar=r.last_bar,
                readable_sessions=r.readable_sessions,
                crosses=_marks(r.crosses, sessions),
                price_200d=_marks(r.price_200d, sessions) if price_200d else (),
                gap_pct=None if r.gap_pct is None else round(r.gap_pct, 2),
                posture=r.posture if r.priced else "other",
                rs_leading=r.rs_leading,
                flag=flag,
            )
        )
    members.sort(
        key=lambda m: (
            not m.crosses,
            m.crosses[0].sessions_ago if m.crosses else 0,
            not m.priced,
            m.flag is not None,
            m.ticker.casefold(),
            str(m.security_id),
        )
    )
    every = [m for name in members for m in name.crosses]
    layer = [m for name in members for m in name.price_200d]
    row = TimelineRow(
        group=group,
        golden=sum(1 for m in every if m.direction == "golden"),
        death=sum(1 for m in every if m.direction == "death"),
        price_up=sum(1 for m in layer if m.direction == "up"),
        price_down=sum(1 for m in layer if m.direction == "down"),
        posture=Posture(**posture),
        members=tuple(members),
    )
    return found, movers, (NearGroup(group, tuple(near)) if near else None), row


def cross_radar(
    inputs: RadarInputs,
    identity: Mapping[UUID, Mapping[str, object]],
    *,
    asof: date,
    group_by: GroupBy = "link",
    include_silent: bool = False,
    price_200d: bool = False,
) -> CrossRadar:
    """The Cross Radar payload for one view. ``identity`` maps a security to its ``business_type`` and
    ``business_supersector`` (``securities.master.identity_for``); only the type and sector views read it.
    """
    counted = counted_rows(inputs.memberships, include_silent)
    flagged = inputs.flagged
    # a flagged company reads as LEFT_OUT: no cross, never priced, so no rule below can count it
    readings = {
        sid: LEFT_OUT if sid in flagged else inputs.readings.get(sid, NO_READING)
        for sid in {m.security_id for m in counted.rows}
    }
    found: list[Cluster] = []
    movers: list[FirstMover] = []
    near: list[NearGroup] = []
    timeline: list[TimelineRow] = []
    for key, (rows, label, meta) in buckets(counted.rows, group_by, identity).items():
        companies = companies_in(rows)
        n_priced = sum(1 for sid, _ in companies if readings[sid].priced)
        n_flagged = sum(1 for sid, _ in companies if sid in flagged)
        group = Group(
            key=key,
            label=label,
            names=len(companies),
            priced=n_priced,
            not_priced=len(companies) - n_priced - n_flagged,
            flagged=n_flagged,
            **meta,  # type: ignore[arg-type]
        )
        c, f, nr, row = _group_view(
            group, companies, readings, inputs.sessions, price_200d, flagged
        )
        found += c
        movers += f
        if nr is not None:
            near.append(nr)
        timeline.append(row)

    def label_key(g: Group) -> tuple[str, str]:
        return (g.label.casefold(), g.key)

    # the default orders (the page re-sorts): clusters by share, then count; first movers newest cross first;
    # near-cross by the smallest gap; the timeline by total crosses, then name
    found.sort(key=lambda c: (-c.share, -c.count, label_key(c.group), c.direction))
    movers.sort(
        key=lambda f: (
            -f.leader.d.toordinal(),
            f.leader.sessions_ago,
            label_key(f.group),
            f.direction,
        )
    )
    near.sort(key=lambda g: (abs(g.names[0].gap_pct), label_key(g.group)))
    timeline.sort(key=lambda t: (-(t.golden + t.death), label_key(t.group)))

    companies = set(readings)
    return CrossRadar(
        asof=asof,
        group_by=group_by,
        include_silent=include_silent,
        price_200d=price_200d,
        live_cards=inputs.live_cards,
        cards_counted=inputs.cards_counted,
        cards_not_yet_created=inputs.cards_not_yet_created,
        roster_fallback_cards=inputs.roster_fallback_cards,
        basket_rows=counted.basket_rows,
        silent_rows_left_out=counted.silent_rows_left_out,
        unresolved_rows=counted.unresolved_rows,
        companies=len(companies),
        priced_companies=sum(1 for sid in companies if readings[sid].priced),
        short_tape_companies=sum(
            1 for sid in companies if readings[sid].not_priced == "short_tape"
        ),
        stale_tape_companies=sum(
            1 for sid in companies if readings[sid].not_priced == "stale_tape"
        ),
        sessions=inputs.sessions,
        clusters=tuple(found),
        first_movers=tuple(movers),
        near_cross=tuple(near),
        timeline=tuple(timeline),
        flagged=flagged_in_view(counted.rows, flagged),
    )
