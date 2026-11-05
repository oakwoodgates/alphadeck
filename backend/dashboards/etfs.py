"""ETFs (``docs/dashboards/etfs.md``): the funds on the cards from five angles: each fund against its card's
companies, money in and out, size and trading, price trend, and which cards have a fund. Follows the as-of dial.

PURE: the router reads the cards and their rosters as of the as-of (``dashboards/baskets.py``), the funds' and the
companies' closes, the funds' display readings and shares samples through the point-in-time view, and hands them to
``etf_inputs(...)``, which aligns everything on ONE session calendar (the longest window, 63 daily returns), cached
per as-of (``dashboards/cache.py``). ``etfs(...)`` then slices that to the chosen window per request. The line math
lives in ``dashboards/series.py`` (shared). Nothing here reads the network, writes, calls the LLM or feeds a call
(rule 1); the module imports no ingest client, EDGAR client, fund source or OpenFIGI client.

The rules, in one place:
- **A fund** is a roster row, as of the as-of, whose security is an ETF (``security_master.instrument_kind``), in ANY
  link. Since the ETFs link every fund sits there; reading the kind also finds a fund at a past as-of, when funds
  sat in voting links.
- **A card's companies** are its voting rows (every link except Context, ETFs and Bench), with every fund left out
  whatever its link, each company once (by ``security_id``). A voting row with no resolved security counts as a
  company on its card and is never priced.
- **A flagged price history is left out** (rule 5): a fund or company whose data flags reach its price readings at
  the as-of is out of every number that uses its price (panels 1 to 4, flow dollars included). Each is listed. A
  flagged fund stays in the coverage panel.
- **Panel 1: fund vs its companies.** Windows 1W / 1M / 3M = 5 / 21 / 63 daily returns on the shared session
  calendar (``series.session_calendar``, IWM's bars), ending at the as-of. The fund's line is its closes rebased to
  100 at the window start (``series.chain`` with one member); the companies' line is equal weight, rebalanced
  daily, from 100 (the Narrative Index's rule, the same ``series.chain``). A fund with a bar missing from the
  window's sessions has no line ("n/a: 12/22 bars"): a gap would drop its steps. A card whose companies are all
  unpriced reads no return and no difference, never a flat 0%. No lead or lag reading: only the two moves and
  their difference.
- **Panel 2: money in and out.** The ``etf_flow`` display member's 1W and 1M net flow, as it reads it.
- **Panel 3: size and trading.** Approximate size = the latest shares sample on or before the as-of times the close
  on or before the sample's date (not the filed net assets); the 20-day average dollar volume is the
  ``volume_regime`` member's ``adv_usd_20d``.
- **Panel 4: price trend.** The Cockpit's own readings for any name: the 30-day return, the distance from the 50-
  and 200-day averages, relative volume (20-day) and the position in the 52-week range.
- **Panel 5: coverage.** Card grain; flagged funds count.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Literal
from uuid import UUID

from dashboards.baskets import FlaggedCompany, Membership
from dashboards.series import BASE, SessionSource, aligned, chain, session_calendar
from signals.display import etf_flow
from signals.display.base import DisplaySignal

Window = Literal["1w", "1m", "3m"]
# daily returns per window: a week, a month and a quarter of sessions
WINDOWS: dict[Window, int] = {"1w": 5, "1m": 21, "3m": 63}
DEFAULT_WINDOW: Window = "1m"
MAX_SESSIONS = WINDOWS["3m"]
# calendar days of company closes read: the 64 bars of the longest window plus holiday slack
LOOKBACK_DAYS = 130
BENCHMARK = "IWM"  # panel 1's comparison, in the header

# the display members this dashboard reads (by registered name)
MEMBERS: tuple[str, ...] = (
    "etf_flow",
    "volume_regime",
    "trailing_returns",
    "sma_position",
    "rvol",
    "range_52w",
)


# --- readings pulled out of the display members' own output ---------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """One number with the reason it is missing: ``value`` None carries the member's own note."""

    value: float | None = None
    note: str | None = None


def reading_of(sig: DisplaySignal | None, key: str) -> Reading:
    """A member metric as a ``Reading`` (``Reading(None, "no data")`` when the member returned nothing)."""
    if sig is None:
        return Reading(None, "no data")
    for m in sig.metrics:
        if m.key == key:
            return Reading(m.value, m.note)
    return Reading(None, "no data")


def range_position(sig: DisplaySignal | None) -> Reading:
    """Where the close sits in its 52-week range, 0 (at the low) to 100 (at the high), from the member's two
    percentages (the close cancels out of the ratio)."""
    off = reading_of(sig, "pct_off_52w_high")
    above = reading_of(sig, "pct_above_52w_low")
    if off.value is None or above.value is None:
        return Reading(None, off.note or above.note or "no data")
    inv_low = 1.0 / (1.0 + above.value / 100.0)
    inv_high = 1.0 / (1.0 + off.value / 100.0)
    width = inv_high - inv_low
    if width <= 0.0:
        return Reading(None, "n/a: flat range")
    return Reading(round((1.0 - inv_low) / width * 100.0, 1))


@dataclass(frozen=True)
class FundReadings:
    """One fund's readings at the as-of, everything the panels 2 to 4 show. ``None`` = no reading."""

    # panel 2: the etf_flow member
    flow_1w_usd: Reading = field(default_factory=Reading)
    flow_1w_pct: Reading = field(default_factory=Reading)
    flow_1m_usd: Reading = field(default_factory=Reading)
    flow_1m_pct: Reading = field(default_factory=Reading)
    filling_1w: bool = False  # the series is younger than the 7-day window
    filling_1m: bool = False  # ... than the 30-day window
    samples: int = 0
    first_sample: date | None = None
    latest_sample: date | None = None
    source: str | None = None
    # panel 3
    shares: float | None = None
    size_usd: float | None = None
    size_note: str | None = None
    adv_usd_20d: Reading = field(default_factory=Reading)
    # panel 4
    ret_30d: Reading = field(default_factory=Reading)
    vs_50d: Reading = field(default_factory=Reading)
    vs_200d: Reading = field(default_factory=Reading)
    rvol20: Reading = field(default_factory=Reading)
    range_pos: Reading = field(default_factory=Reading)


def _close_on_or_before(bars: Sequence[tuple[date, float]], d: date) -> float | None:
    i = bisect_right([b[0] for b in bars], d)
    return bars[i - 1][1] if i else None


def fund_readings(
    signals: Mapping[str, DisplaySignal | None],
    shares: Sequence[Mapping[str, Any]],
    bars: Sequence[Mapping[str, Any]],
    asof: date,
) -> FundReadings:
    """One fund's readings off the six members' signals (keyed by member name), its shares samples and its bars
    (``pit.fund_shares`` / ``pit.price_history``, both as of the public clock). Nothing dated after ``asof`` is
    read."""
    samples = sorted((s for s in shares if s["d"] <= asof), key=lambda s: s["d"])
    flow = signals.get("etf_flow")
    priced = sorted(
        (
            (b["d"], float(b["close"]))
            for b in bars
            if b.get("close") is not None and float(b["close"]) > 0.0
        ),
        key=lambda t: t[0],
    )
    latest = samples[-1] if samples else None
    shares_out: float | None = None
    size_usd: float | None = None
    size_note: str | None = "no shares sample yet"
    if latest is not None:
        shares_out = float(latest["shares_out"])
        close = _close_on_or_before(priced, latest["d"])
        if close is None:
            size_note = "no close on file at the sample's date"
        else:
            size_usd = shares_out * close
            size_note = None

    def filling(days: int) -> bool:
        if not samples:
            return False
        return not any(s["d"] <= asof - timedelta(days=days) for s in samples)

    return FundReadings(
        flow_1w_usd=reading_of(flow, "flow_1w_usd"),
        flow_1w_pct=reading_of(flow, "flow_1w_pct_of_shares"),
        flow_1m_usd=reading_of(flow, "flow_1m_usd"),
        flow_1m_pct=reading_of(flow, "flow_1m_pct_of_shares"),
        filling_1w=filling(etf_flow.WINDOW_1W_DAYS),
        filling_1m=filling(etf_flow.WINDOW_1M_DAYS),
        samples=len(samples),
        first_sample=samples[0]["d"] if samples else None,
        latest_sample=latest["d"] if latest else None,
        source=str(latest["source"]) if latest and latest.get("source") else None,
        shares=shares_out,
        size_usd=size_usd,
        size_note=size_note,
        adv_usd_20d=reading_of(signals.get("volume_regime"), "adv_usd_20d"),
        ret_30d=reading_of(signals.get("trailing_returns"), "ret_30d"),
        vs_50d=reading_of(signals.get("sma_position"), "pct_vs_fast"),
        vs_200d=reading_of(signals.get("sma_position"), "pct_vs_slow"),
        rvol20=reading_of(signals.get("rvol"), "rvol20"),
        range_pos=range_position(signals.get("range_52w")),
    )


# --- the cached read ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class CardRef:
    """A card a fund sits on, with the fund's ticker there (the Cockpit's ``?name=`` key)."""

    thesis_id: UUID
    thesis_name: str
    ticker: str


@dataclass(frozen=True)
class FundBundle:
    """What the router reads per fund: its closes (the long tape) and its readings."""

    closes: Mapping[date, float]
    readings: FundReadings


@dataclass(frozen=True)
class CardIn:
    thesis_id: UUID
    thesis_name: str
    company_ids: tuple[UUID, ...]  # the card's companies, each once, first-seen order
    unresolved: int  # voting rows with no resolved security (counted as companies, never priced)
    fund_ids: tuple[UUID, ...]


@dataclass(frozen=True)
class FundIn:
    security_id: UUID
    ticker: str
    name: str | None
    cards: tuple[CardRef, ...]  # A→Z by card name
    aligned: tuple[float | None, ...]  # closes on the shared sessions
    readings: FundReadings
    flag: str | None  # why its price history is left out (``price_flags``); None = read


@dataclass(frozen=True)
class EtfInputs:
    """Everything the heavy read produces: cached per (clock, as-of); every window is derived from it."""

    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    sessions: tuple[date, ...]  # the longest window's sessions, oldest first
    session_source: SessionSource
    iwm: tuple[float | None, ...] | None  # IWM on the sessions; None = no bar
    cards: tuple[CardIn, ...]  # A→Z
    funds: tuple[FundIn, ...]  # by ticker
    company_aligned: Mapping[UUID, tuple[float | None, ...]]  # flagged companies hold no bars
    company_flags: Mapping[UUID, str]
    company_tickers: Mapping[UUID, str]
    companies: int  # distinct resolved companies on the cards that have a fund


def display_ticker(refs: Sequence[CardRef]) -> str:
    """The ticker a fund shows under: the one most of its cards use, ties by ticker."""
    counts: dict[str, int] = {}
    for r in refs:
        counts[r.ticker] = counts.get(r.ticker, 0) + 1
    return min(counts, key=lambda t: (-counts[t], t))


def etf_inputs(
    *,
    cards: Sequence[tuple[UUID, str]],
    memberships: Sequence[Membership],
    fund_ids: set[UUID],
    funds: Mapping[UUID, FundBundle],
    names: Mapping[UUID, str | None],
    company_closes: Mapping[UUID, Mapping[date, float]],
    iwm: Mapping[date, float],
    flagged: Mapping[UUID, str],
    asof: date,
    live_cards: int,
    cards_not_yet_created: int,
    roster_fallback_cards: int,
) -> EtfInputs:
    """The shared calendar and every card's and fund's alignment on it. ``fund_ids`` = the securities on the
    rosters whose master row is an ETF; ``funds`` = each one's closes and readings; ``company_closes`` = the
    voting companies' closes of the cards that have a fund; ``flagged`` = the securities whose data flags reach
    their price readings (``dashboards.baskets.price_flags``): their closes are never read."""
    rows_by_card: dict[UUID, list[Membership]] = {tid: [] for tid, _ in cards}
    for m in memberships:
        if m.thesis_id in rows_by_card:
            rows_by_card[m.thesis_id].append(m)

    card_ins: list[CardIn] = []
    refs: dict[UUID, dict[UUID, CardRef]] = {}  # fund -> card -> ref
    company_tickers: dict[UUID, str] = {}
    for tid, name in cards:
        rows = rows_by_card[tid]
        on_card: list[UUID] = []
        for m in rows:
            sid = m.security_id
            if sid is not None and sid in fund_ids:
                refs.setdefault(sid, {}).setdefault(tid, CardRef(tid, name, m.ticker))
                if sid not in on_card:
                    on_card.append(sid)
        companies: list[UUID] = []
        unresolved: list[str] = []
        if on_card:
            for m in rows:
                if m.silent:
                    continue
                if m.security_id is None:
                    if m.ticker not in unresolved:
                        unresolved.append(m.ticker)
                elif m.security_id not in fund_ids and m.security_id not in companies:
                    companies.append(m.security_id)
                    company_tickers.setdefault(m.security_id, m.ticker)
        card_ins.append(CardIn(tid, name, tuple(companies), len(unresolved), tuple(on_card)))
    card_ins.sort(key=lambda c: (c.thesis_name.casefold(), str(c.thesis_id)))

    in_view = {sid for c in card_ins for sid in c.company_ids}
    read_companies = {sid for sid in in_view if sid not in flagged}
    iwm_dates = list(iwm)
    sessions, source = session_calendar(
        iwm_dates,
        [
            d
            for sid in (*(f for f in refs), *read_companies)
            for d in (
                funds[sid].closes
                if sid in funds and sid not in flagged
                else company_closes.get(sid, {})
            )
        ],
        asof,
        MAX_SESSIONS,
    )
    n = len(sessions)
    no_bars: tuple[float | None, ...] = (None,) * n
    company_aligned = {
        sid: aligned(company_closes.get(sid, {}), sessions) if sid in read_companies else no_bars
        for sid in in_view
    }
    iwm_aligned = aligned(iwm, sessions)

    fund_ins: list[FundIn] = []
    for sid, per_card in refs.items():
        bundle = funds[sid]
        cs = tuple(
            sorted(per_card.values(), key=lambda r: (r.thesis_name.casefold(), str(r.thesis_id)))
        )
        flag = flagged.get(sid)
        fund_ins.append(
            FundIn(
                security_id=sid,
                ticker=display_ticker(cs),
                name=names.get(sid),
                cards=cs,
                aligned=no_bars if flag else aligned(bundle.closes, sessions),
                readings=bundle.readings,
                flag=flag,
            )
        )
    fund_ins.sort(key=lambda f: (f.ticker.casefold(), str(f.security_id)))

    return EtfInputs(
        live_cards=live_cards,
        cards_counted=len(cards),
        cards_not_yet_created=cards_not_yet_created,
        roster_fallback_cards=roster_fallback_cards,
        sessions=sessions,
        session_source=source,
        iwm=iwm_aligned if any(c is not None for c in iwm_aligned) else None,
        cards=tuple(card_ins),
        funds=tuple(fund_ins),
        company_aligned=company_aligned,
        company_flags={sid: flagged[sid] for sid in in_view if sid in flagged},
        company_tickers=company_tickers,
        companies=len(in_view),
    )


# --- the payload ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class IwmView:
    available: bool
    reason: str | None
    ret: float | None


@dataclass(frozen=True)
class CompareRow:
    """One (card, fund) pair. ``ret`` / ``companies_ret`` are the window returns (%), ``diff`` = fund minus
    companies in points; ``bars`` / ``bars_needed`` explain a fund with no line; ``fund_points`` /
    ``company_points`` are the two lines from 100 (one per window session), None when absent."""

    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str
    fund_name: str | None
    ret: float | None
    companies_ret: float | None
    diff: float | None
    companies: int
    priced: int
    unresolved: int
    sessions_flat: int
    bars: int
    bars_needed: int
    note: str | None
    fund_points: tuple[float, ...] | None
    company_points: tuple[float, ...] | None


@dataclass(frozen=True)
class FlowRow:
    security_id: UUID
    ticker: str
    name: str | None
    cards: tuple[CardRef, ...]
    flow_1w_usd: Reading
    flow_1w_pct: Reading
    flow_1m_usd: Reading
    flow_1m_pct: Reading
    filling_1w: bool
    filling_1m: bool
    samples: int
    first_sample: date | None
    latest_sample: date | None
    source: str | None


@dataclass(frozen=True)
class SizeRow:
    security_id: UUID
    ticker: str
    name: str | None
    cards: tuple[CardRef, ...]
    shares: float | None
    sample_date: date | None
    size_usd: float | None
    size_note: str | None
    adv_usd_20d: Reading


@dataclass(frozen=True)
class TrendRow:
    security_id: UUID
    ticker: str
    name: str | None
    cards: tuple[CardRef, ...]
    ret_30d: Reading
    vs_50d: Reading
    vs_200d: Reading
    rvol20: Reading
    range_pos: Reading


@dataclass(frozen=True)
class CardNoFund:
    thesis_id: UUID
    thesis_name: str


@dataclass(frozen=True)
class MultiCardFund:
    security_id: UUID
    ticker: str
    name: str | None
    cards: tuple[CardRef, ...]


@dataclass(frozen=True)
class Coverage:
    cards_counted: int
    cards_with_fund: int
    funds: int
    without: tuple[CardNoFund, ...]  # A→Z
    multi: tuple[MultiCardFund, ...]


@dataclass(frozen=True)
class Etfs:
    asof: date
    window: Window
    sessions: int  # daily returns in the window (fewer than nominal when the tape is short)
    dates: tuple[date, ...]
    session_source: SessionSource
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    funds: int
    companies: int
    iwm: IwmView
    compare: tuple[CompareRow, ...]
    flows: tuple[FlowRow, ...]
    sizes: tuple[SizeRow, ...]
    trends: tuple[TrendRow, ...]
    coverage: Coverage
    flagged_funds: tuple[FlaggedCompany, ...]
    flagged_companies: tuple[FlaggedCompany, ...]


def _pct(end: float, start: float) -> float:
    return round((end / start - 1.0) * 100.0, 2)


def window_start(n_sessions: int, window: Window) -> int:
    """The index of the window's first session in the shared sessions (0 when the tape is shorter)."""
    return max(0, n_sessions - 1 - WINDOWS[window])


def _iwm_view(iwm: tuple[float | None, ...] | None, start: int) -> IwmView:
    if iwm is None:
        return IwmView(False, f"no {BENCHMARK} tape at this as-of", None)
    ch = chain([iwm[start:]], len(iwm) - start)
    if not any(ch.contributors):
        return IwmView(False, f"no {BENCHMARK} bars in this window", None)
    return IwmView(True, None, _pct(ch.levels[-1], ch.levels[0]))


def _desc_none_last(v: float | None) -> tuple[bool, float]:
    return (v is None, -(v or 0.0))


def _compare_rows(inputs: EtfInputs, start: int, window: Window) -> list[CompareRow]:
    n = len(inputs.sessions) - start
    need = WINDOWS[window] + 1
    funds = {f.security_id: f for f in inputs.funds}
    rows: list[CompareRow] = []
    for card in inputs.cards:
        usable = [funds[fid] for fid in card.fund_ids if funds[fid].flag is None]
        if not usable:
            continue
        members = [inputs.company_aligned[sid][start:] for sid in card.company_ids]
        ch = chain(members, n)
        priced = sum(1 for c in ch.contributed if c)
        comp_ret = _pct(ch.levels[-1], ch.levels[0]) if priced and ch.levels else None
        comp_pts = tuple(ch.levels) if comp_ret is not None else None
        flat = sum(1 for k in ch.contributors[1:] if k == 0)
        for f in usable:
            closes = f.aligned[start:]
            bars = sum(1 for c in closes if c is not None)
            line = None
            fund_ret: float | None = None
            note: str | None = None
            if bars < need or len(closes) < need:
                note = f"n/a: {bars}/{need} bars"
            else:
                line = chain([closes], n)
                fund_ret = _pct(line.levels[-1], line.levels[0])
            rows.append(
                CompareRow(
                    thesis_id=card.thesis_id,
                    thesis_name=card.thesis_name,
                    security_id=f.security_id,
                    ticker=next(
                        (r.ticker for r in f.cards if r.thesis_id == card.thesis_id), f.ticker
                    ),
                    fund_name=f.name,
                    ret=fund_ret,
                    companies_ret=comp_ret,
                    diff=(
                        round(fund_ret - comp_ret, 2)
                        if fund_ret is not None and comp_ret is not None
                        else None
                    ),
                    companies=len(card.company_ids) + card.unresolved,
                    priced=priced,
                    unresolved=card.unresolved,
                    sessions_flat=flat,
                    bars=bars,
                    bars_needed=need,
                    note=note,
                    fund_points=tuple(line.levels) if line is not None else None,
                    company_points=comp_pts,
                )
            )
    rows.sort(key=lambda r: (*_desc_none_last(r.diff), r.thesis_name.casefold(), r.ticker))
    return rows


def _flow_rows(funds: Sequence[FundIn]) -> list[FlowRow]:
    rows = [
        FlowRow(
            security_id=f.security_id,
            ticker=f.ticker,
            name=f.name,
            cards=f.cards,
            flow_1w_usd=f.readings.flow_1w_usd,
            flow_1w_pct=f.readings.flow_1w_pct,
            flow_1m_usd=f.readings.flow_1m_usd,
            flow_1m_pct=f.readings.flow_1m_pct,
            filling_1w=f.readings.filling_1w,
            filling_1m=f.readings.filling_1m,
            samples=f.readings.samples,
            first_sample=f.readings.first_sample,
            latest_sample=f.readings.latest_sample,
            source=f.readings.source,
        )
        for f in funds
    ]
    # 1M dollars, highest first; a fund with no 1M number goes last, A→Z
    rows.sort(key=lambda r: (*_desc_none_last(r.flow_1m_usd.value), r.ticker.casefold()))
    return rows


def _size_rows(funds: Sequence[FundIn]) -> list[SizeRow]:
    rows = [
        SizeRow(
            security_id=f.security_id,
            ticker=f.ticker,
            name=f.name,
            cards=f.cards,
            shares=f.readings.shares,
            sample_date=f.readings.latest_sample,
            size_usd=f.readings.size_usd,
            size_note=f.readings.size_note,
            adv_usd_20d=f.readings.adv_usd_20d,
        )
        for f in funds
    ]
    rows.sort(
        key=lambda r: (
            *_desc_none_last(r.size_usd),
            *_desc_none_last(r.adv_usd_20d.value),
            r.ticker.casefold(),
        )
    )
    return rows


def _trend_rows(funds: Sequence[FundIn]) -> list[TrendRow]:
    return [
        TrendRow(
            security_id=f.security_id,
            ticker=f.ticker,
            name=f.name,
            cards=f.cards,
            ret_30d=f.readings.ret_30d,
            vs_50d=f.readings.vs_50d,
            vs_200d=f.readings.vs_200d,
            rvol20=f.readings.rvol20,
            range_pos=f.readings.range_pos,
        )
        for f in funds  # inputs.funds is already A→Z by ticker
    ]


def _coverage(inputs: EtfInputs) -> Coverage:
    without = tuple(CardNoFund(c.thesis_id, c.thesis_name) for c in inputs.cards if not c.fund_ids)
    multi = tuple(
        MultiCardFund(f.security_id, f.ticker, f.name, f.cards)
        for f in inputs.funds
        if len(f.cards) > 1
    )
    return Coverage(
        cards_counted=len(inputs.cards),
        cards_with_fund=sum(1 for c in inputs.cards if c.fund_ids),
        funds=len(inputs.funds),
        without=without,
        multi=multi,
    )


def etfs(inputs: EtfInputs, *, asof: date, window: Window) -> Etfs:
    """The page for one window: panel 1 sliced from the cached sessions, the other four as the cache holds
    them (they do not depend on the window)."""
    n = len(inputs.sessions)
    start = window_start(n, window)
    read = [f for f in inputs.funds if f.flag is None]
    flagged_funds = tuple(
        sorted(
            (FlaggedCompany(f.security_id, f.ticker, f.flag) for f in inputs.funds if f.flag),
            key=lambda x: (x.ticker.casefold(), str(x.security_id)),
        )
    )
    flagged_companies = tuple(
        sorted(
            (
                FlaggedCompany(sid, inputs.company_tickers.get(sid, "?"), reason)
                for sid, reason in inputs.company_flags.items()
            ),
            key=lambda x: (x.ticker.casefold(), str(x.security_id)),
        )
    )
    return Etfs(
        asof=asof,
        window=window,
        sessions=max(0, n - 1 - start),
        dates=inputs.sessions[start:],
        session_source=inputs.session_source,
        live_cards=inputs.live_cards,
        cards_counted=inputs.cards_counted,
        cards_not_yet_created=inputs.cards_not_yet_created,
        roster_fallback_cards=inputs.roster_fallback_cards,
        funds=len(inputs.funds),
        companies=inputs.companies,
        iwm=_iwm_view(inputs.iwm, start) if n else IwmView(False, "no session calendar", None),
        compare=tuple(_compare_rows(inputs, start, window)) if n else (),
        flows=tuple(_flow_rows(read)),
        sizes=tuple(_size_rows(read)),
        trends=tuple(_trend_rows(read)),
        coverage=_coverage(inputs),
        flagged_funds=flagged_funds,
        flagged_companies=flagged_companies,
    )


__all__ = [
    "BASE",
    "BENCHMARK",
    "DEFAULT_WINDOW",
    "LOOKBACK_DAYS",
    "MEMBERS",
    "WINDOWS",
    "EtfInputs",
    "Etfs",
    "FundBundle",
    "FundReadings",
    "Reading",
    "Window",
    "etf_inputs",
    "etfs",
    "fund_readings",
]
