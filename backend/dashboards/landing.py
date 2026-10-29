"""Price & Volume Landing (``docs/dashboards/landing.md``): where price CLOSED in each window's range, and whether
volume backed the move, by group across every live card, at the as-of dial. Rows are GROUPS (a link of a card,
a business type, a sector, or a card); columns are four windows of trading bars (1D · 7D · 30D · 90D), each
with the group's median range position, its quadrant split and its median up/down-day volume.

PURE: the router reads the cards (``dashboards/baskets.py``, shared with the Rotation Map and the Cross Radar),
reads each company's price bars once on the public clock and hands the readings in as ``LandingInputs``
(cached per as-of, ``dashboards/cache.py``); ``landing(...)`` groups them per request. Display only: nothing
here feeds a call, scores an idea or writes anything (rule 1).

The rules, in one place (per name, per window of N trading bars, off its own bars up to the as-of):
- **The window** is the company's last N bars (the as-of's own bar is the first); the PRIOR window is the N
  bars before those (bars N+1..2N back). 1D = the as-of bar alone, its prior window the single bar before.
- **Range position** = (close - window low) / (window high - window low), the window's highest high and
  lowest low, as a percent: 0% = closed at the low, 100% = at the high. A close outside its bars' high / low
  (a vendor inconsistency) reads at the edge. Fewer than N bars, a bar without a high or a low, or a
  zero-width range = no reading ("—").
- **Return** = the close vs the close N bars back (the Cockpit's ``trailing_returns`` arithmetic); its sign
  is read off the two closes exactly, so only an unchanged close is flat.
- **Volume ratio** = the window's total volume / the prior window's total, rounded to 2 decimals; RISING =
  that ratio above ``RISING_MULT`` (so 1.20 is not rising, 1.21 is). Fewer than 2N bars, a bar without volume
  in either window, or a zero prior total = no reading.
- **Quadrant** = the return's sign x volume rising or not: up + rising = accumulation, up + not = thin rally,
  down + rising = distribution, down + not = rest; an unchanged close = flat (whatever the volume did); a
  missing return, or a move without a volume ratio = no reading.
- **Up/down-day volume** = the volume on the window's up-close days / the volume on its down-close days
  (each bar against the close before it; an unchanged close is neither). No down days (or no down-day
  volume), a bar without volume, or too few bars for the first day's prior close = no reading.
- **Priced** = a close at or before the as-of, the last one no more than ``sma.STALE_GAP_DAYS`` calendar days
  before it (the Cross Radar's stale-tape tell). Anything else is counted as not priced (``no_tape`` /
  ``stale_tape``), never dropped, and reads no window.
- **Per group, per window:** the MEDIAN range position over the names with one (fewer than ``MIN_PRICED`` =
  "—", the Rotation Map's rule), the quadrant SPLIT as counts (never one label), and the median up/down-day
  volume (same thin rule).
- **Who counts / groups:** exactly the Rotation Map's (``dashboards/baskets.py``): Context and Bench left out
  unless ``include_silent``; in the Link view a company in two links counts in both, elsewhere once per group.
- **Default order:** the 7D median range position, highest first, "—" last, ties by label (the page re-sorts
  by any window's position or accumulation count).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any, Literal
from uuid import UUID

from dashboards.baskets import (
    GroupBy,
    Membership,
    NameCard,
    buckets,
    companies_in,
    counted_rows,
    display_ticker,
)
from signals.display import sma

Quadrant = Literal["accumulation", "thin_rally", "distribution", "rest", "flat"]
NotPriced = Literal["no_tape", "stale_tape"]

# the windows, in TRADING bars: the Cockpit's own 1d / 7d / 30d / 90d ladder (``trailing_returns``)
WINDOWS: tuple[tuple[str, int], ...] = (("1d", 1), ("7d", 7), ("30d", 30), ("90d", 90))
WINDOW_KEYS: tuple[str, ...] = tuple(k for k, _ in WINDOWS)
DEFAULT_SORT = "7d"
QUADRANTS: tuple[Quadrant, ...] = ("accumulation", "thin_rally", "distribution", "rest", "flat")
# volume is "rising" when the window's total is MORE than this multiple of the prior window's
RISING_MULT = 1.2
# fewer names with a reading than this = no median (the Rotation Map's / Cockpit link header's rule)
MIN_PRICED = 3
# calendar days of bars read per company: the 90D volume ratio needs 180 trading bars (~262 calendar days with
# holidays), plus slack. Inside the display view's price bound (``display_bounds()``; a test holds it there).
LOOKBACK_DAYS = 300


# --- per-name arithmetic, over a company's ascending bars that carry a close ---------------------------------


def _num(v: Any) -> float | None:
    return None if v is None else float(v)


def range_position(bars: Sequence[Mapping[str, Any]], n: int) -> float | None:
    """Where the last close sits in the last ``n`` bars' high-low range, 0-100 (1 decimal)."""
    if n < 1 or len(bars) < n:
        return None
    window = bars[-n:]
    highs = [_num(b.get("high")) for b in window]
    lows = [_num(b.get("low")) for b in window]
    if any(v is None for v in highs) or any(v is None for v in lows):
        return None
    hi = max(v for v in highs if v is not None)
    lo = min(v for v in lows if v is not None)
    if hi <= lo:
        return None
    pos = (float(window[-1]["close"]) - lo) / (hi - lo)
    return round(min(1.0, max(0.0, pos)) * 100.0, 1)


def window_return(closes: Sequence[float], n: int) -> tuple[float | None, int | None]:
    """``(% return, sign)``: the last close vs the close ``n`` bars back. The sign (+1 / -1 / 0) is read off
    the two closes exactly; ``(None, None)`` when the tape is short or the base close is non-positive.
    """
    if len(closes) < n + 1:
        return None, None
    base, last = closes[-(n + 1)], closes[-1]
    if base <= 0.0:
        return None, None
    sign = 1 if last > base else -1 if last < base else 0
    return round((last / base - 1.0) * 100.0, 2), sign


def _volumes(bars: Sequence[Mapping[str, Any]]) -> list[float] | None:
    vols = [_num(b.get("volume")) for b in bars]
    if any(v is None for v in vols):
        return None
    return [v for v in vols if v is not None]


def volume_ratio(bars: Sequence[Mapping[str, Any]], n: int) -> float | None:
    """The last ``n`` bars' total volume / the ``n`` bars before them (2 decimals)."""
    if len(bars) < 2 * n:
        return None
    this = _volumes(bars[-n:])
    prior = _volumes(bars[-2 * n : -n])
    if this is None or prior is None or sum(prior) <= 0.0:
        return None
    return round(sum(this) / sum(prior), 2)


def rising(ratio: float | None) -> bool | None:
    return None if ratio is None else ratio > RISING_MULT


def quadrant(sign: int | None, ratio: float | None) -> Quadrant | None:
    """The return's sign x volume rising or not; an unchanged close is flat whatever the volume did."""
    if sign is None:
        return None
    if sign == 0:
        return "flat"
    up = rising(ratio)
    if up is None:
        return None
    if sign > 0:
        return "accumulation" if up else "thin_rally"
    return "distribution" if up else "rest"


def up_down_volume(bars: Sequence[Mapping[str, Any]], n: int) -> float | None:
    """Volume on the last ``n`` bars' up-close days / volume on their down-close days (2 decimals), each bar
    against the close before it."""
    if len(bars) < n + 1:
        return None
    up = down = 0.0
    down_days = 0
    for prev, bar in zip(bars[-(n + 1) : -1], bars[-n:]):
        vol = _num(bar.get("volume"))
        if vol is None:
            return None
        close, before = float(bar["close"]), float(prev["close"])
        if close > before:
            up += vol
        elif close < before:
            down += vol
            down_days += 1
    if down_days == 0 or down <= 0.0:
        return None
    return round(up / down, 2)


@dataclass(frozen=True)
class WindowReading:
    """One company's reading for one window; ``None`` = no reading (an honest gap)."""

    key: str
    ret: float | None = None  # % return over the window
    range_pos: float | None = None  # 0-100
    vol_ratio: float | None = None  # this window's volume / the prior window's
    quadrant: Quadrant | None = None
    up_down: float | None = None  # up-close-day volume / down-close-day volume


NO_WINDOWS: tuple[WindowReading, ...] = tuple(WindowReading(k) for k in WINDOW_KEYS)


@dataclass(frozen=True)
class CompanyLanding:
    """One company's readings at the as-of, cached per as-of."""

    bars: int  # bars with a close in the read
    last_bar: date | None
    priced: bool
    not_priced: NotPriced | None = None
    windows: tuple[WindowReading, ...] = NO_WINDOWS


NO_READING = CompanyLanding(bars=0, last_bar=None, priced=False, not_priced="no_tape")


def company_landing(bars: Sequence[Mapping[str, Any]], asof: date) -> CompanyLanding:
    """Read one company off its ascending EOD bars (``pit.price_history(sid, lookback_days=LOOKBACK_DAYS)``)."""
    rows = [b for b in bars if b.get("close") is not None and b["d"] <= asof]
    if not rows:
        return NO_READING
    last = rows[-1]["d"]
    if (asof - last).days > sma.STALE_GAP_DAYS:
        return CompanyLanding(bars=len(rows), last_bar=last, priced=False, not_priced="stale_tape")
    closes = [float(b["close"]) for b in rows]
    windows: list[WindowReading] = []
    for key, n in WINDOWS:
        ret, sign = window_return(closes, n)
        ratio = volume_ratio(rows, n)
        windows.append(
            WindowReading(
                key=key,
                ret=ret,
                range_pos=range_position(rows, n),
                vol_ratio=ratio,
                quadrant=quadrant(sign, ratio),
                up_down=up_down_volume(rows, n),
            )
        )
    return CompanyLanding(bars=len(rows), last_bar=last, priced=True, windows=tuple(windows))


# --- the cached inputs ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class LandingInputs:
    """Everything the heavy read produces: cached per as-of, shared by every grouping and toggle."""

    live_cards: int  # unarchived cards today
    cards_counted: int  # those that existed by the as-of
    cards_not_yet_created: int  # created after the as-of: left out
    roster_fallback_cards: (
        int  # counted cards with no roster snapshot by then: today's roster stands in
    )
    memberships: tuple[Membership, ...]
    readings: Mapping[
        UUID, CompanyLanding
    ]  # every resolved company on the counted cards, silent included


# --- the payload -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Split:
    """A window's quadrant split: how many of the group's names landed in each; ``unread`` = no reading."""

    accumulation: int = 0
    thin_rally: int = 0
    distribution: int = 0
    rest: int = 0
    flat: int = 0
    unread: int = 0


@dataclass(frozen=True)
class GroupWindow:
    key: str
    range_median: float | None  # None when fewer than MIN_PRICED names have a range position
    range_n: int
    split: Split
    up_down_median: float | None  # same thin rule
    up_down_n: int


@dataclass(frozen=True)
class LandingName:
    security_id: UUID
    ticker: str
    cards: tuple[NameCard, ...]
    priced: bool
    not_priced: NotPriced | None
    last_bar: date | None
    windows: tuple[WindowReading, ...]


@dataclass(frozen=True)
class LandingGroup:
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
    names: int = 0  # companies in the group, each once
    priced: int = 0
    windows: tuple[GroupWindow, ...] = ()
    members: tuple[LandingName, ...] = ()


@dataclass(frozen=True)
class Landing:
    asof: date
    group_by: GroupBy
    include_silent: bool
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int  # every row on the counted cards, before any filter
    silent_rows_left_out: int
    unresolved_rows: int  # rows in view with no resolved security: counted, never grouped
    companies: int  # distinct resolved companies in view
    priced_companies: int
    no_tape_companies: int  # not priced: no close at or before the as-of
    stale_tape_companies: int  # not priced: the last close is too old
    groups: tuple[LandingGroup, ...]


def _median(values: Sequence[float], ndigits: int) -> float | None:
    return round(median(values), ndigits) if len(values) >= MIN_PRICED else None


def split_of(quadrants: Sequence[Quadrant | None]) -> Split:
    counts = {q: 0 for q in QUADRANTS}
    for q in quadrants:
        if q is not None:
            counts[q] += 1
    return Split(**counts, unread=sum(1 for q in quadrants if q is None))


def _window(key: str, readings: Sequence[WindowReading]) -> GroupWindow:
    pos = [r.range_pos for r in readings if r.range_pos is not None]
    ud = [r.up_down for r in readings if r.up_down is not None]
    return GroupWindow(
        key=key,
        range_median=_median(pos, 1),
        range_n=len(pos),
        split=split_of([r.quadrant for r in readings]),
        up_down_median=_median(ud, 2),
        up_down_n=len(ud),
    )


def _window_of(windows: Sequence[Any], key: str) -> Any:
    return next(w for w in windows if w.key == key)


def _name_sort_key(m: LandingName) -> tuple:
    v = _window_of(m.windows, DEFAULT_SORT).range_pos
    return (v is None, -(v or 0.0), not m.priced, m.ticker.casefold(), str(m.security_id))


def group_sort_key(g: LandingGroup, key: str = DEFAULT_SORT) -> tuple:
    """Highest median range position first, groups without one last, ties by label: the default order."""
    v = _window_of(g.windows, key).range_median
    return (v is None, -(v or 0.0), g.label.casefold(), g.key)


def landing(
    inputs: LandingInputs,
    identity: Mapping[UUID, Mapping[str, object]],
    *,
    asof: date,
    group_by: GroupBy = "link",
    include_silent: bool = False,
) -> Landing:
    """The Landing payload for one view. ``identity`` maps a security to its ``business_type`` and
    ``business_supersector`` (``securities.master.identity_for``); only the type and sector views read it.
    """
    counted = counted_rows(inputs.memberships, include_silent)
    readings = {
        sid: inputs.readings.get(sid, NO_READING) for sid in {m.security_id for m in counted.rows}
    }
    groups: list[LandingGroup] = []
    for key, (rows, label, meta) in buckets(counted.rows, group_by, identity).items():
        members: list[LandingName] = []
        for sid, cards in companies_in(rows):
            r = readings[sid]
            members.append(
                LandingName(
                    security_id=sid,
                    ticker=display_ticker(cards),
                    cards=cards,
                    priced=r.priced,
                    not_priced=r.not_priced,
                    last_bar=r.last_bar,
                    windows=r.windows,
                )
            )
        members.sort(key=_name_sort_key)
        groups.append(
            LandingGroup(
                key=key,
                label=label,
                names=len(members),
                priced=sum(1 for m in members if m.priced),
                windows=tuple(
                    _window(k, [_window_of(m.windows, k) for m in members]) for k in WINDOW_KEYS
                ),
                members=tuple(members),
                **meta,  # type: ignore[arg-type]
            )
        )
    groups.sort(key=group_sort_key)

    def count(reason: NotPriced) -> int:
        return sum(1 for r in readings.values() if r.not_priced == reason)

    return Landing(
        asof=asof,
        group_by=group_by,
        include_silent=include_silent,
        live_cards=inputs.live_cards,
        cards_counted=inputs.cards_counted,
        cards_not_yet_created=inputs.cards_not_yet_created,
        roster_fallback_cards=inputs.roster_fallback_cards,
        basket_rows=counted.basket_rows,
        silent_rows_left_out=counted.silent_rows_left_out,
        unresolved_rows=counted.unresolved_rows,
        companies=len(readings),
        priced_companies=sum(1 for r in readings.values() if r.priced),
        no_tape_companies=count("no_tape"),
        stale_tape_companies=count("stale_tape"),
        groups=tuple(groups),
    )
