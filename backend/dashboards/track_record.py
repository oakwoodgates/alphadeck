"""Track Record by Cap Tier (``docs/dashboards/track-record.md``): what the tape did after each Armed call, by the
name's market-cap tier AT THE EVENT, at fixed horizons, against IWM and SPY, with n and how often it held in every
cell. Follows the as-of dial.

PURE: the router reads the Scoreboard's own arm episodes (``scoreboard.record.scoreboard_records``, the honesty
filters unchanged) and, per company, the point-in-time bars, share facts and split list through the request's one
``serve_view(asof)`` pair; it hands them in as ``EpisodeIn`` / ``CompanyData`` / ``Benchmarks`` / ``Market`` and
``assemble(...)`` builds the page's whole read ONCE per (edition, honesty policies, clock, as-of) in the shared
dashboard cache (``dashboards/cache.py``). Tiering, slicing, filtering and the entry toggle are the page's, from
that one read. Display only: nothing here feeds a call, scores an idea or writes anything (rule 1). A cap tier is a
slicing dimension, never a call input or sizing (#4); every figure is a close, a fact or a count the system already
holds, and the model never writes one (#3). Nothing here, or on the page, says a signal "works": it states what
happened, with the four things the rule asks for (horizon, comparison, n, how often it held).

The rules, in one place. This is our own simple table, stated here and on the page.
- **The events** are the Scoreboard's arm episodes, unchanged (``replay.episodes.derive_episodes`` over the honest
  timeline): keyed (card, name, arm date), a re-arm is a new episode. An episode the Scoreboard leaves out of its
  metrics is left out of every cell here and COUNTED, never dropped: a ``censored_start`` one (armed on the card's
  first recorded night), an ``ingest_flagged`` one, and one whose company is on the flagged-price list
  (``baskets.price_flags``).
- **Horizons count SESSIONS of the name's own bars**, never calendar days: the Nth bar after the entry bar (5, 10,
  20 and 60) plus "to exit_by" (the last bar on or before the episode's own ``exit_by``, the Scoreboard's
  ``forward_return`` window). A horizon whose bar is not there is not counted. It reads **maturing** when the market
  has not yet had that many sessions since the entry (or the name's tape is current and simply has not reached it),
  and **path ended** when the market HAS had them and the name's tape stops more than ``stale_days`` calendar days
  short of the market's last bar (a delisting, a halt, a stale tape: the recency monitor's own threshold).
  "The market" is SPY's bar dates, IWM's when SPY has none (the Narrative Index's session calendar). With no market
  tape at all nothing can read path ended.
- **Entry.** The page's default (and the ``opening`` table's) is the NEXT SESSION's close, the first close strictly
  AFTER the arm date: the first session the operator could have acted on a call recorded at 22:30 ET (operator
  decision 2026-10-30). The toggle's other entry is the Scoreboard's own entry close, the first close on or after
  the arm date (the arm night's own), so "to exit_by" there reconciles with its ``forward_return`` to the cent. A
  row carries both.
- **Return** = horizon close / entry close - 1, as traded (no dividends). **Comparison** = the same span on IWM and
  on SPY, each benchmark's close on the entry bar's DATE and the horizon bar's DATE (an exact date, never a stale
  stand-in; a missing benchmark bar leaves that comparison blank, counted). **Excess** = the name's return minus the
  benchmark's.
- **Market cap AT THE EVENT** (``CLAUDE.md``, ``docs/BACKTEST.md`` "Sliced by market cap"): the close of the last
  session BEFORE the arm date, put back to the price as traded that day, times the last share count (by cover date)
  that was PUBLIC before the arm date (F9: its filing's acceptance, else its ratification), divided by the ADS
  ratio when known, with a split between the count and the close
  putting the count on the close's scale (``domain.splits.scale_after``): the Peer Table's own pure ``cap_cell``
  with the inputs chosen at the event, so the two pages cannot disagree on the arithmetic. A cap that cannot be pinned
  (no close before the event, no share count before it, an unread ADS ratio) is **unpinned**: counted, never
  dropped, never guessed. Every cap is approximate.
- **Liquidity at the event** = the mean close times volume over the last 20 volume-bearing sessions before the arm
  date (``signals.display.volume_regime`` run at the event date): under $1M, $1M to $10M, $10M and over (PROPOSED
  bands); unpinned when fewer than 20 volume-bearing bars precede the event.
- **Cells.** A cell holds, for the episodes of one row that have a counted span at that horizon (and a comparison, when
  one is picked): n, the median excess (the median raw return under "raw"), the hit rate (the share above zero), the
  interquartile range (linear interpolation between order statistics) and the medians of the name's and the
  benchmark's raw returns. **A cell with n under 10 is too small to read** (``MIN_N``): the page prints its n only.
  The pooled row is the whole counted set and is always last.
"""

from __future__ import annotations

import math
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Literal, NamedTuple
from uuid import UUID

from dashboards.baskets import FlaggedCompany
from dashboards.peer_table import CapCell, CompanyReads, cap_cell
from domain.market_cap import CapTier
from domain.market_time import market_tz
from domain.splits import ListedSplit, scale_after
from signals.display import volume_regime

SLUG = "track-record"

# The horizons: sessions of the name's own bars after the entry bar, then the episode's own exit_by.
SESSION_HORIZONS: dict[str, int] = {"h5": 5, "h10": 10, "h20": 20, "h60": 60}
EXIT_BY = "exit_by"
HORIZONS: tuple[str, ...] = (*SESSION_HORIZONS, EXIT_BY)

# The two entry rules a row carries: the Scoreboard's (the first close on or after the arm date) and the next
# session's (the first close strictly after it, the page's default).
ENTRY_RULES: tuple[str, ...] = ("arm_night", "next_session")
BENCHMARKS: tuple[str, ...] = ("IWM", "SPY")
COMPARISONS: tuple[str, ...] = ("iwm", "spy", "raw")

# A cell under this many episodes is too small to read: the page prints its n only.
MIN_N = 10

# Calendar days a name's tape may sit behind the market's last bar before a horizon it has not reached reads "path
# ended" rather than "maturing". The router passes ``Settings.tape_stale_days``, the recency monitor's threshold.
STALE_DAYS = 5

# Liquidity bands (PROPOSED, the plan's): each lower bound in dollars of 20-day average volume at the event; a
# value on a bound belongs to the higher band (the cap tiers' rule).
LIQUIDITY_BANDS: tuple[tuple[str, float], ...] = (
    ("under_1m", 0.0),
    ("1m_10m", 1_000_000.0),
    ("10m_plus", 10_000_000.0),
)
UNPINNED = "unpinned"
POOLED = "pooled"
TIER_KEYS: tuple[str, ...] = tuple(t.value for t in CapTier)  # nano .. large, smallest first
LIQUIDITY_KEYS: tuple[str, ...] = tuple(k for k, _ in LIQUIDITY_BANDS)
SLICES: tuple[str, ...] = ("tier", "liquidity")

SpanStatus = Literal["counted", "maturing", "path_ended", "none"]
LeftOut = Literal["flagged", "censored_start", "ingest_flagged"]


class Bar(NamedTuple):
    """One bar of a name's own tape, as the point-in-time view reads it (ascending, a positive close)."""

    d: date
    close: float
    volume: float | None = None


# --- the bars ------------------------------------------------------------------------------------------------


def bars_of(rows: Iterable[Mapping[str, Any]]) -> list[Bar]:
    """The point-in-time price rows as ascending bars. A bar with no close or a non-positive one is not a bar (the
    Narrative Index's rule); a later row for the same date wins."""
    by_day: dict[date, Bar] = {}
    for r in rows:
        close = r.get("close")
        if close is None or float(close) <= 0:
            continue
        vol = r.get("volume")
        by_day[r["d"]] = Bar(r["d"], float(close), float(vol) if vol is not None else None)
    return [by_day[d] for d in sorted(by_day)]


def entry_index(bars: Sequence[Bar], arm_date: date, rule: str) -> int | None:
    """The entry bar's index: the first bar on or after the arm date (``arm_night``, the Scoreboard's own) or
    strictly after it (``next_session``); ``None`` when the name has no such bar."""
    if rule == "arm_night":
        i = bisect_left(bars, arm_date, key=lambda b: b.d)
    elif rule == "next_session":
        i = bisect_right(bars, arm_date, key=lambda b: b.d)
    else:
        raise ValueError(f"unknown entry rule {rule!r}")
    return i if i < len(bars) else None


def horizon_close(bars: Sequence[Bar], entry_i: int, sessions: int) -> Bar | None:
    """The bar ``sessions`` bars after the entry bar, in the name's OWN bars (a weekend or a holiday never shifts
    it); ``None`` when the tape does not reach that far."""
    j = entry_i + sessions
    return bars[j] if 0 <= j < len(bars) else None


# --- the market's clock and the benchmarks -------------------------------------------------------------------


@dataclass(frozen=True)
class Market:
    """The market's session dates up to the as-of (SPY's bars, IWM's when SPY has none) and the staleness
    threshold. It only decides "maturing" against "path ended"; it never supplies a number."""

    dates: tuple[date, ...] = ()
    source: str | None = None
    stale_days: int = STALE_DAYS

    @property
    def last(self) -> date | None:
        return self.dates[-1] if self.dates else None

    def sessions_after(self, d: date) -> int:
        """Market sessions strictly after ``d`` (up to the as-of)."""
        return len(self.dates) - bisect_right(self.dates, d)

    def behind(self, name_last: date | None) -> bool:
        """Does the name's tape stop more than ``stale_days`` calendar days short of the market's last bar? A
        name with no bar at all is behind; with no market tape nothing is."""
        if name_last is None:
            return True
        if self.last is None:
            return False
        return (self.last - name_last).days > self.stale_days


def market_of(spy: Sequence[Bar], iwm: Sequence[Bar], *, stale_days: int = STALE_DAYS) -> Market:
    """SPY's session dates, IWM's when SPY has none, none when neither has a bar."""
    if spy:
        return Market(tuple(b.d for b in spy), "SPY", stale_days)
    if iwm:
        return Market(tuple(b.d for b in iwm), "IWM", stale_days)
    return Market((), None, stale_days)


@dataclass(frozen=True)
class Benchmarks:
    """Each benchmark's close by date (``IWM``, ``SPY``)."""

    closes: Mapping[str, Mapping[date, float]]

    def ret(self, symbol: str, start: date, end: date) -> float | None:
        """The benchmark's return between two DATES; ``None`` when it has no bar on either (never a nearby
        stand-in)."""
        series = self.closes.get(symbol)
        if not series:
            return None
        a, b = series.get(start), series.get(end)
        if a is None or b is None or a <= 0:
            return None
        return b / a - 1.0


def benchmarks_of(spy: Sequence[Bar], iwm: Sequence[Bar]) -> Benchmarks:
    return Benchmarks({"SPY": {b.d: b.close for b in spy}, "IWM": {b.d: b.close for b in iwm}})


# --- the spans -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Span:
    """One horizon of one entry rule. ``counted``: the name's return (and each benchmark's over the same dates, blank
    when it has no bar on either date). ``maturing`` / ``path_ended`` / ``none`` carry no number: ``none`` is a
    horizon there is nothing to measure for (no exit_by, or no session inside the window for this entry).
    """

    status: SpanStatus
    end_date: date | None = None
    ret: float | None = None
    iwm: float | None = None
    spy: float | None = None


def _counted(entry: Bar, end: Bar, bench: Benchmarks) -> Span:
    return Span(
        "counted",
        end_date=end.d,
        ret=end.close / entry.close - 1.0,
        iwm=bench.ret("IWM", entry.d, end.d),
        spy=bench.ret("SPY", entry.d, end.d),
    )


def session_span(
    bars: Sequence[Bar], entry_i: int | None, sessions: int, bench: Benchmarks, market: Market
) -> Span:
    """The return over ``sessions`` of the name's own bars after the entry bar. A bar that is not there reads
    ``path_ended`` when the market has had that many sessions since the entry and the name's tape is behind it, else
    ``maturing``; with no entry bar at all, ``path_ended`` when the tape is behind the market, else ``maturing``
    (tonight's bar is not in yet)."""
    name_last = bars[-1].d if bars else None
    if entry_i is None:
        return Span("path_ended" if market.behind(name_last) else "maturing")
    end = horizon_close(bars, entry_i, sessions)
    if end is None:
        reached = market.sessions_after(bars[entry_i].d) >= sessions
        return Span("path_ended" if reached and market.behind(name_last) else "maturing")
    return _counted(bars[entry_i], end, bench)


def exit_by_span(
    bars: Sequence[Bar],
    entry_i: int | None,
    exit_by: date | None,
    asof: date,
    bench: Benchmarks,
    market: Market,
) -> Span:
    """The return to the episode's own ``exit_by``: the last bar on or before it (the Scoreboard's window). Not
    counted before ``exit_by`` has arrived (``maturing``), with no ``exit_by`` or no session between the entry and
    it (``none``), or when the tape stops short of ``exit_by`` and sits behind the market (``path_ended``).
    """
    if exit_by is None:
        return Span("none")
    if exit_by > asof:
        return Span("maturing")
    if entry_i is None:
        return Span("path_ended")
    j = bisect_right(bars, exit_by, key=lambda b: b.d) - 1
    if j < entry_i:
        return Span("none")
    name_last = bars[-1].d
    if name_last < exit_by and market.behind(name_last):
        return Span("path_ended")
    return _counted(bars[entry_i], bars[j], bench)


@dataclass(frozen=True)
class EntryRead:
    """One entry rule's read of an episode: the entry bar and a ``Span`` per horizon."""

    entry_date: date | None
    entry_close: float | None
    spans: Mapping[str, Span]


def entry_read(
    bars: Sequence[Bar],
    arm_date: date,
    exit_by: date | None,
    rule: str,
    asof: date,
    bench: Benchmarks,
    market: Market,
) -> EntryRead:
    i = entry_index(bars, arm_date, rule)
    spans = {h: session_span(bars, i, n, bench, market) for h, n in SESSION_HORIZONS.items()}
    spans[EXIT_BY] = exit_by_span(bars, i, exit_by, asof, bench, market)
    entry = bars[i] if i is not None else None
    return EntryRead(
        entry_date=entry.d if entry else None,
        entry_close=entry.close if entry else None,
        spans=spans,
    )


# --- the cap and the liquidity at the event ------------------------------------------------------------------


def share_public_at(fact: Mapping[str, Any]) -> datetime | None:
    """When a share fact became PUBLIC (F9): the acceptance of the filing whose cover carries its count
    (``filing_accepted``), else its ``recorded_at`` — the ratification instant on a live read, the public instant
    itself on a history read (``db.public_clock``). ``None`` when neither is on the row (never guessed).
    """
    for key in ("filing_accepted", "recorded_at"):
        v = fact.get(key)
        if isinstance(v, datetime):
            return v
    return None


def event_cap(
    bars: Sequence[Bar],
    shares_facts: Sequence[Mapping[str, Any]],
    splits: Sequence[ListedSplit],
    arm_date: date,
    view_day: date,
) -> CapCell:
    """The approximate market cap AT THE EVENT, by the Peer Table's rule (``cap_cell``) with the inputs chosen at
    the arm date: the close of the last session BEFORE it, and the newest share fact (by cover date, ``valid_from``)
    that was PUBLIC before it (F9, ``share_public_at``: its filing's acceptance, else the instant it was ratified,
    before the start of the arm date in market time) — a count filed on or after the arm date is never used, even
    when its cover date is earlier (the lookahead the cover date alone allowed).

    The bars are on the VIEW's price scale (``view_day``: the splits in force by the view's day applied), so the close
    is put back to the price AS TRADED that session (divided by ``scale_after`` from that session through the view's
    day) before ``cap_cell`` puts the share count on the same footing with the splits between the count's date and the
    close's: both sides on one basis, never an adjusted price times a pre-split count. ``gap`` names why a cap cannot
    be pinned (``no_price`` | ``no_shares`` | ``ads_unread`` | ``no_cap``)."""
    i = (
        bisect_left(bars, arm_date, key=lambda b: b.d) - 1
    )  # the last bar strictly before the arm date
    if i < 0:
        return CapCell(gap="no_price")
    prior = bars[i]
    traded = prior.close / float(scale_after(splits, prior.d, through=view_day))
    start = datetime.combine(
        arm_date, time(0), tzinfo=market_tz()
    )  # the arm date's start, market time
    known = [
        f
        for f in shares_facts
        if isinstance(f.get("valid_from"), date)
        and f["valid_from"] < arm_date
        and (public := share_public_at(f)) is not None
        and public < start
    ]
    newest = max(known, key=lambda f: f["valid_from"]) if known else None
    shares = (
        {k: newest.get(k) for k in ("shares", "valid_from", "ads_ratio", "ads_ratio_status")}
        if newest
        else None
    )
    return cap_cell(
        CompanyReads(close=(prior.d, traded), shares=shares, splits=tuple(splits)),
    )


def event_adv(bars: Sequence[Bar], arm_date: date) -> float | None:
    """The 20-day average dollar volume at the event: the Volume Regime member's own arithmetic
    (``volume_regime.compute``, mean close x volume) over the last 20 volume-bearing bars strictly BEFORE the arm
    date. ``None`` when fewer than 20 precede it."""
    prior: list[dict[str, Any]] = []
    i = bisect_left(bars, arm_date, key=lambda b: b.d) - 1
    while i >= 0 and len(prior) < volume_regime.RECENT_BARS:
        b = bars[i]
        if b.volume is not None:
            prior.append({"d": b.d, "close": b.close, "volume": b.volume})
        i -= 1
    prior.reverse()
    signal = volume_regime.compute(prior, arm_date)
    metric = next((m for m in signal.metrics if m.key == "adv_usd_20d"), None) if signal else None
    return metric.value if metric else None


def liquidity_band(adv: float | None) -> str | None:
    """The liquidity band of a 20-day average dollar volume, or ``None`` when there is no figure."""
    if adv is None or not math.isfinite(adv) or adv < 0:
        return None
    band = LIQUIDITY_BANDS[0][0]
    for key, floor in LIQUIDITY_BANDS:
        if adv >= floor:
            band = key
    return band


# --- the rows ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class EpisodeIn:
    """One Scoreboard arm episode as the page reads it: who, when, the entry attributes the filter chips use and
    the honesty flags. ``price_flag`` = the flags' labels when the company's data flags reach its price readings.
    """

    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str
    arm_date: date
    exit_by: date | None
    arm_until: date | None
    close_reason: str
    is_headline: bool
    key1_source: str | None
    entry_grade: str | None
    confirmation_grade: str | None
    co_arm_bucket: str
    censored_start: bool = False
    ingest_flagged: bool = False
    ingest_note: str | None = None
    price_flag: str | None = None


@dataclass(frozen=True)
class CompanyData:
    """One company's point-in-time reads: its bars, its share facts, its split list (as the view knows it) and the
    view's market day (the scale the bars are on)."""

    bars: tuple[Bar, ...]
    shares: tuple[Mapping[str, Any], ...]
    splits: tuple[ListedSplit, ...]
    view_day: date


@dataclass(frozen=True)
class RecordRow:
    """One episode: identity, the entry attributes, the cap and liquidity at the event, and both entry rules' reads.
    A left-out episode (``left_out``) carries identity only. Both entry reads are carried so the page's entry
    toggle is client side."""

    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str
    arm_date: date
    exit_by: date | None
    arm_until: date | None
    close_reason: str
    is_headline: bool
    key1_source: str | None
    entry_grade: str | None
    confirmation_grade: str | None
    co_arm_bucket: str
    left_out: LeftOut | None = None
    left_out_note: str | None = None
    cap: CapCell | None = None
    adv_usd: float | None = None
    liquidity: str | None = None
    arm_night: EntryRead | None = None
    next_session: EntryRead | None = None

    @property
    def counted(self) -> bool:
        return self.left_out is None

    def entry(self, rule: str) -> EntryRead | None:
        return self.arm_night if rule == "arm_night" else self.next_session


def left_out_of(ep: EpisodeIn) -> tuple[LeftOut | None, str | None]:
    """Why an episode is left out of every cell, if it is, with the reader's own words: a flagged price history
    first (the price data itself is wrong), then a censored start (the arm date is unknowable), then an ingest
    flag."""
    if ep.price_flag is not None:
        return "flagged", ep.price_flag
    if ep.censored_start:
        return (
            "censored_start",
            "armed on the card's first recorded night, so the true arm date is unknown",
        )
    if ep.ingest_flagged:
        return "ingest_flagged", ep.ingest_note
    return None, None


def build_row(
    ep: EpisodeIn,
    company: CompanyData | None,
    bench: Benchmarks,
    market: Market,
    asof: date,
) -> RecordRow:
    """One episode's row. A left-out episode keeps identity only (nothing is read for it). A counted one gets its cap
    and liquidity at the event and both entry rules' spans; a company with no point-in-time read at all is a row
    with no bars (its spans read path ended or maturing, its cap ``no_price``)."""
    identity: dict[str, Any] = {
        "thesis_id": ep.thesis_id,
        "thesis_name": ep.thesis_name,
        "security_id": ep.security_id,
        "ticker": ep.ticker,
        "arm_date": ep.arm_date,
        "exit_by": ep.exit_by,
        "arm_until": ep.arm_until,
        "close_reason": ep.close_reason,
        "is_headline": ep.is_headline,
        "key1_source": ep.key1_source,
        "entry_grade": ep.entry_grade,
        "confirmation_grade": ep.confirmation_grade,
        "co_arm_bucket": ep.co_arm_bucket,
    }
    reason, note = left_out_of(ep)
    if reason is not None:
        return RecordRow(**identity, left_out=reason, left_out_note=note)
    bars = company.bars if company else ()
    adv = event_adv(bars, ep.arm_date)
    return RecordRow(
        **identity,
        cap=event_cap(
            bars,
            company.shares if company else (),
            company.splits if company else (),
            ep.arm_date,
            company.view_day if company else ep.arm_date,
        ),
        adv_usd=adv,
        liquidity=liquidity_band(adv),
        arm_night=entry_read(bars, ep.arm_date, ep.exit_by, "arm_night", asof, bench, market),
        next_session=entry_read(bars, ep.arm_date, ep.exit_by, "next_session", asof, bench, market),
    )


# --- the cell statistics -------------------------------------------------------------------------------------


def quantile(ordered: Sequence[float], p: float) -> float:
    """The ``p`` quantile of ascending values by linear interpolation between order statistics (the median at 0.5,
    the mean of the two middle values for an even count). The page mirrors this rule exactly."""
    n = len(ordered)
    if n == 0:
        raise ValueError("quantile of nothing")
    h = (n - 1) * p
    lo = math.floor(h)
    hi = min(lo + 1, n - 1)
    return ordered[lo] + (h - lo) * (ordered[hi] - ordered[lo])


def excess(ret: float, bench: float | None) -> float | None:
    """The name's return minus the benchmark's; ``None`` when the benchmark has no return for the span."""
    return None if bench is None else ret - bench


def hit(value: float) -> bool:
    """Did it hold? Strictly above zero (excess under a comparison, the raw return under "raw")."""
    return value > 0


class Obs(NamedTuple):
    """One counted episode's span, as a cell sees it: the name's return and the comparison's (``None`` under
    "raw", or when the benchmark had no bar)."""

    ret: float
    bench: float | None


@dataclass(frozen=True)
class CellStats:
    """One table cell. ``n`` = the episodes it rests on; ``held`` = how many were above zero; ``no_comparison`` =
    counted spans left out because the comparison had no bar on one of the dates. ``too_small`` = 0 < n < MIN_N: the
    page prints n only. ``name_median`` / ``bench_median`` = the medians of the raw returns of the same episodes.
    """

    n: int
    held: int = 0
    hit_rate: float | None = None
    median: float | None = None
    q1: float | None = None
    q3: float | None = None
    name_median: float | None = None
    bench_median: float | None = None
    no_comparison: int = 0
    too_small: bool = False


def cell_stats(obs: Sequence[Obs], comparison: str, *, no_comparison: int = 0) -> CellStats:
    """The cell for the observations that have a counted span at one horizon. Under "raw" the value is the raw
    return; under IWM or SPY it is the excess, and an observation without that benchmark's return is not in the cell
    (it is counted in ``no_comparison`` instead)."""
    if comparison not in COMPARISONS:
        raise ValueError(f"unknown comparison {comparison!r}")
    if comparison == "raw":
        used = [(o.ret, None, o.ret) for o in obs]
    else:
        used = [(o.ret, o.bench, e) for o in obs if (e := excess(o.ret, o.bench)) is not None]
        no_comparison += len(obs) - len(used)
    n = len(used)
    if n == 0:
        return CellStats(n=0, no_comparison=no_comparison)
    values = sorted(v for _, _, v in used)
    held = sum(1 for v in values if hit(v))
    benches = sorted(b for _, b, _ in used if b is not None)
    return CellStats(
        n=n,
        held=held,
        hit_rate=held / n,
        median=quantile(values, 0.5),
        q1=quantile(values, 0.25),
        q3=quantile(values, 0.75),
        name_median=quantile(sorted(r for r, _, _ in used), 0.5),
        bench_median=quantile(benches, 0.5) if benches else None,
        no_comparison=no_comparison,
        too_small=n < MIN_N,
    )


def span_obs(rows: Iterable[RecordRow], entry: str, horizon: str, comparison: str) -> list[Obs]:
    """The counted spans of ``rows`` at one horizon of one entry rule, with the comparison's return alongside."""
    out: list[Obs] = []
    for r in rows:
        read = r.entry(entry)
        span = read.spans.get(horizon) if read else None
        if span is None or span.status != "counted" or span.ret is None:
            continue
        bench = {"iwm": span.iwm, "spy": span.spy}.get(comparison)
        out.append(Obs(span.ret, bench))
    return out


# --- the opening table ---------------------------------------------------------------------------------------


def slice_key(row: RecordRow, slice_by: str) -> str:
    """The row's place on the slice dimension: its cap tier or its liquidity band, ``unpinned`` when it has none."""
    if slice_by == "tier":
        return row.cap.tier.value if row.cap is not None and row.cap.tier is not None else UNPINNED
    if slice_by == "liquidity":
        return row.liquidity or UNPINNED
    raise ValueError(f"unknown slice {slice_by!r}")


@dataclass(frozen=True)
class TableRow:
    """One row of the table: the slice key, how many counted episodes it holds and a cell per horizon."""

    key: str
    episodes: int
    cells: Mapping[str, CellStats]


def table_rows(
    rows: Iterable[RecordRow],
    *,
    slice_by: str = "tier",
    entry: str = "next_session",
    comparison: str = "iwm",
) -> tuple[TableRow, ...]:
    """The table for the COUNTED rows: the slice's rows in size order, then ``unpinned``, then ``pooled`` LAST (the
    whole counted set, never first). Every row is present, a row with no episode too (n = 0)."""
    counted = [r for r in rows if r.counted]
    keys = TIER_KEYS if slice_by == "tier" else LIQUIDITY_KEYS
    groups: dict[str, list[RecordRow]] = {k: [] for k in (*keys, UNPINNED)}
    for r in counted:
        groups[slice_key(r, slice_by)].append(r)
    groups[POOLED] = counted
    return tuple(
        TableRow(
            key=k,
            episodes=len(g),
            cells={h: cell_stats(span_obs(g, entry, h, comparison), comparison) for h in HORIZONS},
        )
        for k, g in groups.items()
    )


# --- the counts ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class HorizonCount:
    """How the counted episodes read at one horizon of one entry rule."""

    counted: int = 0
    maturing: int = 0
    path_ended: int = 0
    none: int = 0


@dataclass(frozen=True)
class Counts:
    """What the page rests on, for the quiet work line. ``episodes`` = every Scoreboard episode in the record at the
    as-of; ``counted`` = those in the cells; the three ``left_out_*`` = those set aside (each episode once, in the
    order flagged price history, censored start, ingest flag); ``unpinned_cap`` / ``unpinned_liquidity`` = counted
    episodes with no cap tier / no liquidity band; ``arm_night`` / ``next_session`` = per horizon, how the counted
    episodes read."""

    episodes: int
    counted: int
    left_out_flagged: int
    left_out_censored: int
    left_out_ingest: int
    unpinned_cap: int
    unpinned_liquidity: int
    arm_night: Mapping[str, HorizonCount]
    next_session: Mapping[str, HorizonCount]


def horizon_counts(rows: Iterable[RecordRow], entry: str) -> dict[str, HorizonCount]:
    per: dict[str, Counter[str]] = {h: Counter() for h in HORIZONS}
    for r in rows:
        read = r.entry(entry)
        if read is None:
            continue
        for h in HORIZONS:
            per[h][read.spans[h].status] += 1
    return {
        h: HorizonCount(
            counted=c["counted"], maturing=c["maturing"], path_ended=c["path_ended"], none=c["none"]
        )
        for h, c in per.items()
    }


def count_rows(rows: Sequence[RecordRow]) -> Counts:
    counted = [r for r in rows if r.counted]
    left = Counter(r.left_out for r in rows if r.left_out)
    return Counts(
        episodes=len(rows),
        counted=len(counted),
        left_out_flagged=left["flagged"],
        left_out_censored=left["censored_start"],
        left_out_ingest=left["ingest_flagged"],
        unpinned_cap=sum(1 for r in counted if slice_key(r, "tier") == UNPINNED),
        unpinned_liquidity=sum(1 for r in counted if slice_key(r, "liquidity") == UNPINNED),
        arm_night=horizon_counts(counted, "arm_night"),
        next_session=horizon_counts(counted, "next_session"),
    )


# --- the page ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TrackRecord:
    """The page's whole read: every episode's row (newest arm first), the counts, the companies whose flagged price
    history leaves them out, the opening table (IWM, the next-session entry, by cap tier, every armed member: the
    reference the page's own client-side arithmetic is tested against) and the market clock in force.
    """

    rows: tuple[RecordRow, ...]
    counts: Counts
    flagged: tuple[FlaggedCompany, ...]
    opening: tuple[TableRow, ...]
    market_source: str | None
    benchmarks_missing: tuple[str, ...]
    stale_days: int
    cards: int = 0
    cards_unreadable: int = 0


def assemble(
    rows: Iterable[RecordRow],
    *,
    flagged: Sequence[FlaggedCompany],
    market: Market,
    bench: Benchmarks,
    cards: int = 0,
    cards_unreadable: int = 0,
) -> TrackRecord:
    """The page from its rows: sorted newest arm first (then ticker, card), counted, tabled at the opening reading.
    ``benchmarks_missing`` = a benchmark with no bar at all at the as-of (its comparison reads blank everywhere);
    ``cards`` = the cards with a recorded call, ``cards_unreadable`` = cards whose record could not be read (counted,
    never dropped)."""
    ordered = tuple(
        sorted(
            rows,
            key=lambda r: (-r.arm_date.toordinal(), r.ticker, r.thesis_name, str(r.security_id)),
        )
    )
    return TrackRecord(
        rows=ordered,
        counts=count_rows(ordered),
        flagged=tuple(sorted(flagged, key=lambda f: (f.ticker.casefold(), str(f.security_id)))),
        opening=table_rows(ordered),
        market_source=market.source,
        benchmarks_missing=tuple(s for s in BENCHMARKS if not bench.closes.get(s)),
        stale_days=market.stale_days,
        cards=cards,
        cards_unreadable=cards_unreadable,
    )
