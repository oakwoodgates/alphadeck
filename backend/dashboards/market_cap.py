"""The shared approximate-market-cap cell and the pieces the dashboards read it through (``docs/DASHBOARDS.md``
rule 7): ``CompanyReads`` (one company's raw readings), ``CapCell`` + ``cap_cell`` (the Workbench's market-cap
rule, copied here, not imported: ``workbench/scoring`` pulls the call config), ``Gap`` (why a cell is blank) and
``metric_of``. Built with the Peer Table, shared by the Hot Board, Ticker Lookup, Market Map, Theme Leaderboard,
Track Record and Insider Tracker. The cap BANDS stay in ``domain/market_cap.py`` (``CapTier``, ``cap_tier``). A
cap tier is a slicing dimension, never a call input or sizing (#4); display only (rule 1).

**Approximate market cap** = the latest close times the latest shares fact: an ``ads_ratio_status`` of ``known``
divides the ordinary count by the ratio, ``unread`` WITHHOLDS the cap, NULL means 1:1 (a domestic name); a split
in force between the count's date and the close's puts the count on the close's scale
(``domain.splits.scale_after``, "never mix a split-adjusted price with a pre-split count"). Labeled approximate
everywhere.

**Which share count** (moved here from ``dashboards/market_map.py`` on 2026-11-04, unchanged, so the pages that use
it cannot drift apart): the latest Workbench count (ratified), else the latest count the nightly stored from the
company's filing covers (``cover_shares``: a 10-Q filer only, filed within ``COVER_MAX_AGE_DAYS`` and at least
``COVER_MIN_SHARES``), else none. ``pick_shares`` judges it at an as-of (the Market Map); ``shares_at_event``
judges it at an event's date, from the counts PUBLIC before it (Track Record). ``CapSource`` names which one made
the cap.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Literal, NamedTuple

from domain.market_cap import CapTier, cap_tier
from domain.market_time import EDGAR_OPEN, edgar_night, market_tz
from domain.splits import ListedSplit, scale_after
from signals.display.base import DisplayMetric, DisplaySignal

# Why a cell is blank. A stated cell has no gap.
Gap = Literal[
    "flagged",  # data flags reach the price readings: withheld, the reason in ``note``
    "no_price",  # no price bars at the as-of
    "no_reading",  # the display member gave no value (too few bars, no volume); ``note`` says why
    "no_shares",  # a price but no shares fact
    "ads_unread",  # an ADR whose ordinary-shares-per-ADS ratio could not be read: the cap is withheld
    "no_cap",  # a share count or close that is not positive
    "no_revenue",  # no revenue facts
    "no_prior_year",  # a revenue print but no row for the same period a year earlier in the read
    "no_burn",  # no cash/burn fact
    "no_convert",  # no live convertible-note overhang on file
    "no_mix",  # no revenue-mix fact
    "no_form4",  # no Form 4 ingested for the name
    "no_spy",  # SPY has no bars at the as-of
    "ends_differ",  # the company's last close is not SPY's last close
]


@dataclass(frozen=True)
class CompanyReads:
    """One company's raw readings, as the router's heavy read produced them. ``flag`` = why its price readings are
    withheld (``baskets.price_flags``); when set the price-derived fields are left ``None`` (never read).
    """

    flag: str | None = None
    close: tuple[date, float] | None = None  # the latest bar: (date, close)
    shares: Mapping[str, Any] | None = (
        None  # the latest shares fact: shares, valid_from, ads_ratio(_status)
    )
    splits: tuple[ListedSplit, ...] = ()
    revenue: tuple[
        Mapping[str, Any], ...
    ] = ()  # metric_key == "revenue" rows: period_end, value, valid_from
    cash_burn: Mapping[str, Any] | None = None  # the latest cash/burn fact
    dilution: tuple[Mapping[str, Any], ...] = ()  # fact_dilution rows (the clock's own input)
    mix: tuple[Mapping[str, Any], ...] = ()  # revenue-mix facts
    range_52w: DisplaySignal | None = None
    sma: DisplaySignal | None = None
    rvol: DisplaySignal | None = None
    volume: DisplaySignal | None = None
    returns: DisplaySignal | None = None
    insider: DisplaySignal | None = None


@dataclass(frozen=True)
class CapCell:
    """The approximate market cap and its tier, with the inputs that made it (the hover shows the product)."""

    value: float | None = None
    tier: CapTier | None = None
    gap: Gap | None = None
    note: str | None = None
    close: float | None = None
    close_date: date | None = None
    shares: float | None = (
        None  # the ordinary count on file (before the ADS ratio and any split scaling)
    )
    shares_date: date | None = None
    ads_ratio: int | None = None  # applied when the status is "known"
    split_factor: float | None = None  # the multiplier put on the count to match the close's scale


def day_of(v: Any) -> date | None:
    return v if isinstance(v, date) else None


def float_of(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def metric_of(sig: DisplaySignal | None, key: str) -> DisplayMetric | None:
    """The named metric of a display member's output, or ``None``."""
    if sig is None:
        return None
    return next((m for m in sig.metrics if m.key == key), None)


def cap_cell(reads: CompanyReads) -> CapCell:
    """The approximate market cap, by ``workbench/scoring._market_cap``'s rule (copied; see the module note).

    A flagged price history withholds it. No price bars: ``no_price`` ("awaiting price"). A price but no shares
    fact: ``no_shares`` ("awaiting shares"). ``ads_ratio_status`` ``unread``, or ``known`` with a missing or
    absurd ratio (a corrupt pairing is treated as unread: fail closed), withholds the cap; ``known`` divides the
    ordinary count by the ratio; NULL is not applicable and counts 1:1. A split in force with a first session
    after the count's date and on or before the close's puts the count on the close's scale.
    """
    if reads.flag is not None:
        return CapCell(gap="flagged", note=reads.flag)
    sh, px = reads.shares, reads.close
    if px is None:
        return CapCell(gap="no_price", shares=float_of(sh["shares"]) if sh else None)
    close_date, close = px
    if sh is None:
        return CapCell(gap="no_shares", close=close, close_date=close_date)
    count, shares_date = float(sh["shares"]), day_of(sh.get("valid_from"))
    status, ratio = sh.get("ads_ratio_status"), sh.get("ads_ratio")
    base = dict(close=close, close_date=close_date, shares=count, shares_date=shares_date)
    if status == "unread" or (status == "known" and not (ratio and float(ratio) >= 1)):
        return CapCell(gap="ads_unread", **base)
    divisor = float(ratio) if status == "known" else 1.0
    k = scale_after(list(reads.splits), shares_date, through=close_date) if shares_date else 1
    cap = count / divisor / float(k) * close
    if cap <= 0:
        return CapCell(gap="no_cap", note="the share count or the close is not positive", **base)
    return CapCell(
        value=float(round(cap)),
        tier=cap_tier(cap),
        ads_ratio=int(ratio) if status == "known" and ratio else None,
        split_factor=float(1 / k) if k != 1 else None,
        **base,
    )


# --- which share count: the Workbench's, else the filing cover's ------------------------------------------------

# the metric_key the nightly stores the cover-page share count under (``ingest.fundamentals``), and the fiscal
# periods that make a company a 10-Q filer ("Q1" / "Q2" / "Q3"; a 10-K's cover reads "FY")
COVER_METRIC = "shares_out_cover_xbrl"
QUARTERLY: frozenset[str] = frozenset({"Q1", "Q2", "Q3"})
# the cover count stands in only when it was filed within this many calendar days on or before the AS-OF (a
# company that stopped tagging the dei cover count keeps its last, old value: Visa's and Mastercard's are from
# 2010), and only when it is at least this many shares (a few tag a placeholder of 1). MEASURED on a prod copy
# (2026-10-30): with both rules the cover count is within 5% of the Workbench count for 807 of 855 companies
# that have both, within 25% for 34 more, further off for 14 (a split between the two dates or new shares).
COVER_MAX_AGE_DAYS = 200
COVER_MIN_SHARES = 10_000

CapSource = Literal["workbench", "cover"]


class CoverPick(NamedTuple):
    """The cover-page stand-in: the share count (shaped like a ``fact_shares_outstanding`` row, so ``cap_cell``
    scales it by the same rule), or ``None`` with, when a cover count exists but was refused, the reason.
    """

    shares: dict[str, Any] | None
    refused: str | None = None


def cover_shares(rows: Iterable[Mapping[str, Any]], asof: date) -> CoverPick:
    """The cover-page share count that stands in for a Workbench one, or none.

    Only for a company with at least one QUARTERLY cover count among the rows (``fiscal_period`` Q1 / Q2 / Q3: a
    10-Q filer). The latest cover row by its filing date (``valid_from``), then its period end, is the only
    candidate, and it stands in only when it is (a) recent: filed within ``COVER_MAX_AGE_DAYS`` calendar days on or
    before the AS-OF (so a past as-of judges the same rule against its own day, never today), and (b) a share count:
    at least ``COVER_MIN_SHARES`` (a company that stopped tagging the cover count keeps its last old value, and
    some tag a placeholder of 1). A refused row yields no stand-in and a short reason for the info box, never an
    older row (older is staler). Its date is the cover's own (``period_end``), never later than the filing's, so
    the split list scales it from the day the count was true. ADS fields are left unset: a 10-Q filer has no
    ADS-ratio question."""
    cover = [
        r
        for r in rows
        if r.get("metric_key") == COVER_METRIC
        and r.get("value") is not None
        and r.get("period_end") is not None
        and r.get("valid_from") is not None
    ]
    if not any(r.get("fiscal_period") in QUARTERLY for r in cover):
        return CoverPick(None)
    best = max(cover, key=lambda r: (r["valid_from"], r["period_end"]))
    value = float(best["value"])
    if value < COVER_MIN_SHARES:
        return CoverPick(None, f"the filing cover's count ({value:,.0f}) is not a share count")
    if (asof - best["valid_from"]).days > COVER_MAX_AGE_DAYS:
        return CoverPick(
            None,
            f"the filing cover's count is from {best['valid_from'].isoformat()}, too old to stand in",
        )
    return CoverPick(
        {
            "shares": value,
            "valid_from": min(best["period_end"], best["valid_from"]),
            "ads_ratio": None,
            "ads_ratio_status": None,
        }
    )


class SharesPick(NamedTuple):
    """The share count the cap rests on, where it came from, and (when the cover's count was refused) why."""

    shares: dict[str, Any] | None
    source: CapSource | None
    refused: str | None = None


def pick_shares(
    workbench_facts: Sequence[Mapping[str, Any]],
    fundamentals_rows: Iterable[Mapping[str, Any]],
    asof: date,
) -> SharesPick:
    """The share count the cap rests on and where it came from: the latest Workbench fact (ratified; the Hot
    Board's pick), else the filing cover's (``cover_shares``, judged against ``asof``), else none.
    """
    if workbench_facts:
        best = max(workbench_facts, key=lambda f: f["valid_from"])
        shares = {k: best.get(k) for k in ("shares", "valid_from", "ads_ratio", "ads_ratio_status")}
        return SharesPick(shares, "workbench")
    cover = cover_shares(fundamentals_rows, asof)
    if cover.shares is None:
        return SharesPick(None, None, cover.refused)
    return SharesPick(cover.shares, "cover")


# --- the same pick judged AT AN EVENT (Track Record) ------------------------------------------------------------

# Why an event's cap has no cover count when the cover rows public before it are all 10-K covers: the company is
# not a 10-Q filer, so its cover count is never used (a foreign filer's raises the ADS-ratio question). The Market
# Map leaves this case silent; Track Record states it.
NOT_A_10Q_FILER = "the filing cover's count is not used: not a 10-Q filer"


def share_public_at(fact: Mapping[str, Any]) -> datetime | None:
    """When a Workbench share fact became PUBLIC (F9): the acceptance of the filing whose cover carries its count
    (``filing_accepted``), else its ``recorded_at`` — the ratification instant on a live read, the public instant
    itself on a history read (``db.public_clock``). ``None`` when neither is on the row (never guessed).
    """
    for key in ("filing_accepted", "recorded_at"):
        v = fact.get(key)
        if isinstance(v, datetime):
            return v
    return None


def cover_public_at(row: Mapping[str, Any]) -> datetime | None:
    """When a filing-cover row (a ``fact_fundamentals`` version) became PUBLIC: its own SEC acceptance
    (``accepted``), else 06:00 market time on its filing date (``valid_from``), the first instant of that date's
    night. That is ``db.public_clock``'s rule for ``fact_fundamentals`` (``PUBLIC_TIME``: kind ``dated``,
    ``accepted`` else ``_start_of_day(valid_from)``), which exists only as SQL; this is its Python twin on the
    same constants (``EDGAR_OPEN``, the market zone), and a test pins the registry entry it mirrors. It reads the
    row itself, so a live read and a history read give the same instant. ``None`` when the row carries neither
    (never guessed)."""
    accepted = row.get("accepted")
    if isinstance(accepted, datetime):
        return accepted
    filed = row.get("valid_from")
    if isinstance(filed, date) and not isinstance(filed, datetime):
        return datetime.combine(filed, EDGAR_OPEN, tzinfo=market_tz())
    return None


def shares_at_event(
    workbench_facts: Sequence[Mapping[str, Any]],
    cover_rows: Iterable[Mapping[str, Any]],
    event_date: date,
) -> SharesPick:
    """The share count a cap AT AN EVENT rests on, from counts PUBLIC before ``event_date`` only.

    (a) The newest Workbench fact (by cover date, ``valid_from``) dated before the event and public before it (F9,
        ``share_public_at``) on a night before the event (``edgar_night``: an instant between midnight and EDGAR's
        06:00 open belongs to the night before, a weekend one to Friday's). Source ``workbench``.
    (b) Else the filing cover's count by the Market Map's rule (``cover_shares``, unchanged) over the cover rows
        public on a night before the event (``cover_public_at``), judged AT the event's date: the 200-day limit
        counts back from the event, never from today. Source ``cover``.
    (c) Else none, with the reason when a cover count was refused: ``cover_shares``' own words, or
        ``NOT_A_10Q_FILER`` when cover rows were public before the event but none is a 10-Q's.

    A count filed on or after the event is never used, even when its cover date is earlier."""
    known = [
        f
        for f in workbench_facts
        if isinstance(f.get("valid_from"), date)
        and f["valid_from"] < event_date
        and (public := share_public_at(f)) is not None
        and edgar_night(public) < event_date
    ]
    if known:
        newest = max(known, key=lambda f: f["valid_from"])
        shares = {
            k: newest.get(k) for k in ("shares", "valid_from", "ads_ratio", "ads_ratio_status")
        }
        return SharesPick(shares, "workbench")
    public = [
        r
        for r in cover_rows
        if r.get("metric_key") == COVER_METRIC
        and (at := cover_public_at(r)) is not None
        and edgar_night(at) < event_date
    ]
    cover = cover_shares(public, event_date)
    if cover.shares is not None:
        return SharesPick(cover.shares, "cover")
    if cover.refused is None and public:
        return SharesPick(None, None, NOT_A_10Q_FILER)
    return SharesPick(None, None, cover.refused)
