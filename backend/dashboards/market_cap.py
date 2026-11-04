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
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from domain.market_cap import CapTier, cap_tier
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
