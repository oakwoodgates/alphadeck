"""Market Map (``docs/dashboards/market-map.md``): a nested treemap heat map of every company on the cards. This
module is the PURE side: one company's readings (six return windows, the 20-day dollar volume, the approximate market
cap with its source) and the page's payload (the readings, the memberships with card / link / silent, SPY's and IWM's
own returns, the companies left out). It ships READINGS, not a layout: the page groups, weighs, colors and lays the
tiles out itself, so every control switches without a refetch.

The router reads the counted cards and runs the point-in-time reads once per company (``app/routers/dashboards/
market_map.py``, cached per as-of in ``dashboards/cache.py``), and hands the result in as ``MapInputs``;
``market_map(...)`` builds the payload per request. Display only (rule 1): every number is a display member's own
reading or a subtraction over EOD bars on the request's one public-clock view; nothing here feeds a call, scores an
idea or writes anything.

The rules, in one place:
- **The windows.** The Cockpit's five trailing returns (``trailing_returns``: 1 / 7 / 30 / 90 / 252 trading BARS, not
  calendar days) plus ``ytd``: the last close against the last close on or before December 31 of the year before the
  as-of's year, from the same bars. No such bar (a company listed this year), or no session yet this year, is a stated
  gap with its reason, never a zero.
- **Dollar volume 20D.** The mean of close x volume over the last 20 sessions. Fewer than 20 sessions, or a volume
  missing on any of them, is a stated gap.
- **Approximate market cap.** The Peer Table's rule (``dashboards.peer_table.cap_cell``): the latest close times the
  latest Workbench share count (ADS ratio and split list applied). A company with NO Workbench count stands in with
  the latest share count the nightly stored from its filings' cover pages (``fact_fundamentals``, ``metric_key =
  'shares_out_cover_xbrl'``), only when the company has at least one QUARTERLY cover count (a 10-Q filer, so no
  ADS-ratio question); the split list scales it from the cover's own date. ``cap_source`` names which one made the
  cap (``workbench`` / ``cover``), ``None`` when there is none (the gap reason stays).
- **A flagged price history** (``dashboards.baskets.price_flags``) leaves the company off the map and out of every
  median; it is listed on the payload, never silently gone (rule 5 / invariant #9).
- **Context and Bench** (``is_silent``) memberships ride the payload marked ``silent``; the page filters them.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal
from uuid import UUID

from dashboards.baskets import Membership, companies_in, display_ticker
from dashboards.peer_table import CapCell, CompanyReads, cap_cell
from domain.splits import ListedSplit
from signals.display import trailing_returns
from signals.display.base import DisplaySignal

# the Cockpit's own windows (the trailing_returns member's keys) with YTD between the 90d and the 1Y
TRAILING: tuple[str, ...] = ("ret_1d", "ret_7d", "ret_30d", "ret_90d", "ret_1y")
WINDOWS: tuple[str, ...] = ("ret_1d", "ret_7d", "ret_30d", "ret_90d", "ytd", "ret_1y")

# sessions in the dollar-volume mean
DOLLAR_VOLUME_BARS = 20

# the metric_key the nightly stores the cover-page share count under (``ingest.fundamentals``), and the fiscal
# periods that make a company a 10-Q filer ("Q1" / "Q2" / "Q3"; a 10-K's cover reads "FY")
COVER_METRIC = "shares_out_cover_xbrl"
QUARTERLY: frozenset[str] = frozenset({"Q1", "Q2", "Q3"})

CapSource = Literal["workbench", "cover"]

NO_BARS = "no price bars at the as-of"


# --- one company's readings ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Reading:
    """A figure or the reason there is none."""

    value: float | None = None
    note: str | None = None


def gap_reason(note: str | None) -> str:
    """A display member's gap note as plain words: ``n/a: 61/91 bars`` -> ``61 of 91 bars``."""
    if not note:
        return "no reading"
    text = note[5:] if note.startswith("n/a: ") else note
    return re.sub(r"(\d+)/(\d+)", r"\1 of \2", text)


def _closes(bars: Sequence[Mapping[str, Any]]) -> list[tuple[date, float, Any]]:
    """(day, close, volume) ascending, bars with a close only."""
    return [
        (b["d"], float(b["close"]), b.get("volume"))
        for b in sorted(bars, key=lambda b: b["d"])
        if b.get("close") is not None
    ]


def ytd_return(bars: Sequence[Mapping[str, Any]], asof: date) -> Reading:
    """Year to date, in percent: the last close against the last close on or before December 31 of the year
    before the as-of's year, from ``bars`` (ascending or not; the as-of read already stops at the as-of). A gap
    with its reason when there is no bar, no bar on or before that date (a company listed this year), no session
    yet in the as-of's year, or a non-positive base close."""
    rows = _closes(bars)
    if not rows:
        return Reading(note=NO_BARS)
    year_end = date(asof.year - 1, 12, 31)
    base = next((c for d, c, _ in reversed(rows) if d <= year_end), None)
    if base is None:
        return Reading(
            note=f"no close on or before {year_end.isoformat()} (first bar {rows[0][0].isoformat()})"
        )
    last_day, last, _ = rows[-1]
    if last_day <= year_end:
        return Reading(note=f"no session yet in {asof.year} (last bar {last_day.isoformat()})")
    if base <= 0.0:
        return Reading(note="non-positive base close")
    return Reading(value=round((last / base - 1.0) * 100.0, 2))


def dollar_volume_20d(bars: Sequence[Mapping[str, Any]]) -> Reading:
    """The mean of close x volume over the last 20 sessions, in USD. A gap when the tape has fewer than 20
    sessions or a volume is missing on any of them (never a partial mean presented as the 20-day figure).
    """
    rows = _closes(bars)
    if not rows:
        return Reading(note=NO_BARS)
    if len(rows) < DOLLAR_VOLUME_BARS:
        return Reading(note=f"{len(rows)} of {DOLLAR_VOLUME_BARS} sessions")
    tail = rows[-DOLLAR_VOLUME_BARS:]
    missing = sum(1 for _, _, v in tail if v is None)
    if missing:
        return Reading(
            note=f"volume missing on {missing} of the last {DOLLAR_VOLUME_BARS} sessions"
        )
    return Reading(value=round(sum(c * float(v) for _, c, v in tail) / DOLLAR_VOLUME_BARS, 2))


def window_returns(
    sig: DisplaySignal | None, bars: Sequence[Mapping[str, Any]], asof: date
) -> tuple[dict[str, float | None], dict[str, str]]:
    """The six windows: the ``trailing_returns`` member's five (``sig``) and the year to date (``bars``). The
    second dict holds the reason for every window with no value."""
    returns: dict[str, float | None] = {w: None for w in WINDOWS}
    gaps: dict[str, str] = {}
    if sig is None:
        gaps.update({w: NO_BARS for w in TRAILING})
    else:
        for m in sig.metrics:
            if m.key in TRAILING:
                returns[m.key] = m.value
                if m.value is None:
                    gaps[m.key] = gap_reason(m.note)
    ytd = ytd_return(bars, asof)
    returns["ytd"] = ytd.value
    if ytd.value is None:
        gaps["ytd"] = ytd.note or "no reading"
    return returns, gaps


def benchmark_returns(
    bars: Sequence[Mapping[str, Any]], asof: date
) -> dict[str, float | None] | None:
    """A benchmark's own returns for all six windows, by the SAME rule as a company's (the member's pure
    ``compute`` over its bars). ``None`` when it has no tape at the as-of."""
    priced = [b for b in bars if b.get("close") is not None]
    sig = trailing_returns.compute(priced, asof)
    if sig is None:
        return None
    returns, _ = window_returns(sig, priced, asof)
    return returns


# --- the approximate market cap --------------------------------------------------------------------------------


def cover_shares(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The cover-page share count that stands in for a Workbench one, shaped like a ``fact_shares_outstanding``
    row (so ``cap_cell`` scales it by the same rule), or ``None``.

    Only for a company with at least one QUARTERLY cover count among the rows (``fiscal_period`` Q1 / Q2 / Q3: a
    10-Q filer). The latest cover row by its filing date (``valid_from``), then its period end, wins. Its date is the
    cover's own (``period_end``), never later than the filing's, so the split list scales it from the day the count
    was true. ADS fields are left unset: a 10-Q filer has no ADS-ratio question."""
    cover = [
        r
        for r in rows
        if r.get("metric_key") == COVER_METRIC
        and r.get("value") is not None
        and r.get("period_end") is not None
        and r.get("valid_from") is not None
    ]
    if not any(r.get("fiscal_period") in QUARTERLY for r in cover):
        return None
    best = max(cover, key=lambda r: (r["valid_from"], r["period_end"]))
    return {
        "shares": float(best["value"]),
        "valid_from": min(best["period_end"], best["valid_from"]),
        "ads_ratio": None,
        "ads_ratio_status": None,
    }


def pick_shares(
    workbench_facts: Sequence[Mapping[str, Any]], fundamentals_rows: Iterable[Mapping[str, Any]]
) -> tuple[dict[str, Any] | None, CapSource | None]:
    """The share count the cap rests on and where it came from: the latest Workbench fact (ratified; the Hot
    Board's pick), else the filing cover's (``cover_shares``), else none."""
    if workbench_facts:
        best = max(workbench_facts, key=lambda f: f["valid_from"])
        shares = {k: best.get(k) for k in ("shares", "valid_from", "ads_ratio", "ads_ratio_status")}
        return shares, "workbench"
    cover = cover_shares(fundamentals_rows)
    return (cover, "cover") if cover is not None else (None, None)


@dataclass(frozen=True)
class CompanyCap:
    """The cap cell and which share count made it (``None`` when there is no cap)."""

    cell: CapCell
    source: CapSource | None


def company_cap(
    close: tuple[date, float] | None,
    shares: Mapping[str, Any] | None,
    source: CapSource | None,
    splits: Sequence[ListedSplit] = (),
) -> CompanyCap:
    """The Peer Table's ``cap_cell`` over the picked share count; ``source`` is kept only when a cap came out."""
    cell = cap_cell(CompanyReads(close=close, shares=shares, splits=tuple(splits)))
    return CompanyCap(cell, source if cell.value is not None else None)


@dataclass(frozen=True)
class CompanyReading:
    """One company's readings at the as-of. ``None`` = an honest gap, with its reason in ``gaps`` /
    ``dollar_volume_note`` / ``cap.cell.gap``."""

    returns: Mapping[str, float | None]
    gaps: Mapping[str, str]
    priced: bool  # the company has a close at the as-of
    cap: CompanyCap
    dollar_volume: float | None = None
    dollar_volume_note: str | None = None


def company_reading(
    bars: Sequence[Mapping[str, Any]],
    sig: DisplaySignal | None,
    asof: date,
    cap: CompanyCap,
) -> CompanyReading:
    """One company's readings from its as-of bars, the ``trailing_returns`` signal and its cap."""
    returns, gaps = window_returns(sig, bars, asof)
    dv = dollar_volume_20d(bars)
    return CompanyReading(
        returns=returns,
        gaps=gaps,
        priced=sig is not None,
        cap=cap,
        dollar_volume=dv.value,
        dollar_volume_note=dv.note if dv.value is None else None,
    )


# --- the cached inputs and the payload ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MapInputs:
    """Everything the heavy read produces: cached per as-of, shared by every request."""

    live_cards: int  # unarchived cards today
    cards_counted: int  # those that existed by the as-of
    cards_not_yet_created: int  # created after the as-of: left out
    roster_fallback_cards: (
        int  # counted cards with no roster snapshot by then: today's roster stands in
    )
    memberships: tuple[
        Membership, ...
    ]  # every basket row of the counted cards, silent ones included
    readings: Mapping[UUID, CompanyReading]  # every resolved, unflagged company
    spy: Mapping[str, float | None] | None  # SPY's own returns; None = no SPY tape at the as-of
    iwm: Mapping[str, float | None] | None
    # companies whose data flags reach their price readings at the as-of, each with its reason
    flagged: Mapping[UUID, str] = field(default_factory=dict)


@dataclass(frozen=True)
class MapCard:
    thesis_id: UUID
    name: str


@dataclass(frozen=True)
class MapMembership:
    """One basket row of a drawn company: its card, the ticker it carries there (the Cockpit's ``?name=`` key),
    its link (``None`` = Unsegmented; ``Context`` / ``Bench`` when silent)."""

    security_id: UUID
    thesis_id: UUID
    ticker: str
    link: str | None
    silent: bool


@dataclass(frozen=True)
class MapCompany:
    security_id: UUID
    ticker: str
    name: str | None
    business_type: str | None  # the leaf (today's identity); None = Unclassified
    supersector: str | None
    silent_only: bool  # every membership is Context or Bench
    returns: Mapping[str, float | None]
    gaps: Mapping[str, str]
    priced: bool
    cap: float | None
    cap_tier: str | None
    cap_source: CapSource | None
    cap_gap: str | None
    cap_note: str | None
    dollar_volume: float | None
    dollar_volume_note: str | None


@dataclass(frozen=True)
class MapFlagged:
    security_id: UUID
    ticker: str
    reason: str
    silent_only: bool


@dataclass(frozen=True)
class MarketMap:
    asof: date
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int  # every row on the counted cards
    silent_rows: int  # of those, Context and Bench
    unresolved_rows: (
        int  # rows with no resolved security, not Context / Bench: counted, never drawn
    )
    unresolved_silent_rows: int
    companies_total: (
        int  # distinct resolved companies on the counted cards, flagged and silent ones included
    )
    cards: tuple[MapCard, ...]
    memberships: tuple[MapMembership, ...]
    companies: tuple[MapCompany, ...]
    flagged: tuple[MapFlagged, ...]
    spy: Mapping[str, float | None] | None
    iwm: Mapping[str, float | None] | None


def _link_order(m: MapMembership) -> tuple:
    return (
        str(m.thesis_id),
        m.link is None,
        (m.link or "").casefold(),
        m.ticker,
        str(m.security_id),
    )


def market_map(
    inputs: MapInputs, identity: Mapping[UUID, Mapping[str, object]], *, asof: date
) -> MarketMap:
    """The Market Map payload. ``identity`` maps a security to its ``name``, ``business_type`` and
    ``business_supersector`` (``securities.master.identity_for``): today's identity at any as-of."""
    rows = list(inputs.memberships)
    resolved = [m for m in rows if m.security_id is not None]
    flagged = inputs.flagged

    silent_by_company: dict[UUID, bool] = {}
    for m in resolved:
        assert m.security_id is not None
        silent_by_company[m.security_id] = silent_by_company.get(m.security_id, True) and m.silent

    def ident(sid: UUID, key: str) -> str | None:
        v = (identity.get(sid) or {}).get(key)
        return str(v) if v else None

    companies: list[MapCompany] = []
    for sid, cards in companies_in(resolved):
        if sid in flagged:
            continue
        r = inputs.readings.get(sid)
        if (
            r is None
        ):  # impossible off the router's read; a missing reading is a stated gap, never a drop
            r = CompanyReading(
                returns={w: None for w in WINDOWS},
                gaps={w: "not read" for w in WINDOWS},
                priced=False,
                cap=CompanyCap(CapCell(gap="no_price"), None),
            )
        cell = r.cap.cell
        companies.append(
            MapCompany(
                security_id=sid,
                ticker=display_ticker(cards),
                name=ident(sid, "name"),
                business_type=ident(sid, "business_type"),
                supersector=ident(sid, "business_supersector"),
                silent_only=silent_by_company[sid],
                returns=dict(r.returns),
                gaps=dict(r.gaps),
                priced=r.priced,
                cap=cell.value,
                cap_tier=cell.tier.value if cell.tier is not None else None,
                cap_source=r.cap.source,
                cap_gap=cell.gap if cell.value is None else None,
                cap_note=cell.note if cell.value is None else None,
                dollar_volume=r.dollar_volume,
                dollar_volume_note=r.dollar_volume_note,
            )
        )
    companies.sort(key=lambda c: (c.ticker.casefold(), str(c.security_id)))

    drawn = {c.security_id for c in companies}
    seen: set[tuple[UUID, UUID, str | None]] = set()
    memberships: list[MapMembership] = []
    for m in resolved:
        assert m.security_id is not None
        key = (m.thesis_id, m.security_id, m.segment)
        if m.security_id not in drawn or key in seen:
            continue
        seen.add(key)
        memberships.append(MapMembership(m.security_id, m.thesis_id, m.ticker, m.segment, m.silent))
    memberships.sort(key=_link_order)
    names = {m.thesis_id: m.thesis_name for m in rows}
    used = {m.thesis_id for m in memberships}
    cards = sorted(
        (MapCard(tid, names[tid]) for tid in used),
        key=lambda c: (c.name.casefold(), str(c.thesis_id)),
    )

    flagged_out = [
        MapFlagged(sid, display_ticker(cs), flagged[sid], silent_by_company[sid])
        for sid, cs in companies_in(resolved)
        if sid in flagged
    ]
    flagged_out.sort(key=lambda f: (f.ticker.casefold(), str(f.security_id)))

    return MarketMap(
        asof=asof,
        live_cards=inputs.live_cards,
        cards_counted=inputs.cards_counted,
        cards_not_yet_created=inputs.cards_not_yet_created,
        roster_fallback_cards=inputs.roster_fallback_cards,
        basket_rows=len(rows),
        silent_rows=sum(1 for m in rows if m.silent),
        unresolved_rows=sum(1 for m in rows if m.security_id is None and not m.silent),
        unresolved_silent_rows=sum(1 for m in rows if m.security_id is None and m.silent),
        companies_total=len(silent_by_company),
        cards=tuple(cards),
        memberships=tuple(memberships),
        companies=tuple(companies),
        flagged=tuple(flagged_out),
        spy=inputs.spy,
        iwm=inputs.iwm,
    )
