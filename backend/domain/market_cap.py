"""The market-cap bands, defined ONCE (operator rule, 2026-10-20; ``CLAUDE.md`` "Event-outcome reports are sliced by
MARKET-CAP TIER", ``docs/BACKTEST.md`` "Sliced by market cap", ``docs/DASHBOARDS.md`` rule 6).

The bands (fixed; this table mirrors ``CLAUDE.md`` verbatim):

======  ======================
tier    market cap
======  ======================
nano    < $50M
micro   $50M - $300M
small   $300M - $2B
mid     $2B - $10B
large   >= $10B
======  ======================

A cap sitting exactly ON a boundary belongs to the HIGHER band ($50M is micro, $300M is small, $2B is mid, $10B is
large), the table's own reading ("$50M-$300M" starts at $50M, "< $50M" excludes it).

**A tier is a slicing dimension for analysis and display ONLY.** It is never a call input, never sizing, and never a
revival of the retired size-tier "archetype" (invariant #4). Nothing in ``calls/``, ``signals/`` or ``pipeline/``
imports this module; it exists so every report and view that slices by size draws the same lines.

**This module bands a number, it does not measure one.** How a cap is measured belongs to the reader: for an event
report it is the as-traded close before the event times the last share count reported before it, on the same split
basis, divided by the ADS ratio for an ADR (``docs/BACKTEST.md``); for a live view it is the Workbench's
``_market_cap`` rule. A cap that cannot be pinned down is passed in as ``None`` and gets no tier: leave it blank and
count the blank, never guess.
"""

from __future__ import annotations

import math
from decimal import Decimal
from enum import StrEnum


class CapTier(StrEnum):
    """The five fixed market-cap bands, smallest first (the enum's order is the band order)."""

    NANO = "nano"
    MICRO = "micro"
    SMALL = "small"
    MID = "mid"
    LARGE = "large"


# Each band's lower bound in USD, ascending (nano has none: it is everything below micro's floor). A cap on a
# bound is in the band that STARTS there.
CAP_TIER_FLOORS: tuple[tuple[CapTier, float], ...] = (
    (CapTier.MICRO, 50_000_000.0),
    (CapTier.SMALL, 300_000_000.0),
    (CapTier.MID, 2_000_000_000.0),
    (CapTier.LARGE, 10_000_000_000.0),
)


def cap_tier(usd: float | int | Decimal | None) -> CapTier | None:
    """The band a market cap (USD) falls in, or ``None`` when there is no cap to band: ``None``, not a finite
    number, or not positive (a zero or negative cap is bad input, not a measurement; it is never "nano").
    """
    if usd is None:
        return None
    try:
        value = float(usd)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    tier = CapTier.NANO
    for band, floor in CAP_TIER_FLOORS:
        if value >= floor:
            tier = band
    return tier
