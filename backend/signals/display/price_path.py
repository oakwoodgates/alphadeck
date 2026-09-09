"""The recent close PATH — the last N EOD closes, for the basket table's sparkline column.

Display-only SHAPE context beside the endpoint-return columns: ``trailing_returns`` states WHERE the
close ended up vs N bars back; this member shows HOW it got there — the same bar convention (N
trading BARS, never calendar days) over ONLY bars dated <= asof (the bitemporal read enforces
no-lookahead, #1). Read-only, structurally OFF the call path (#4/#6): no ``role``, no scalar, it
cannot fire/arm/veto/grade — or even be sorted on — a shape the operator reads, never a number the
system acts on.

The series is a FIXED-SLOT window (``BARS`` slots, ascending, one per trading bar, the last slot =
the latest close knowable at asof). A thinner tape is LEFT-padded with ``None`` — an honest gap the
FE draws as no line — so a young name draws a genuinely shorter path (right-aligned to "now"), never
a line stretched across the window to look like a full tape (#6/#9); the shortfall is named in the
basis note. A single close is still emitted (the honest tape) and the FE reads it as "—" (a point is
not a path). The window is deliberately the ``90d`` return window, so the sparkline IS the path
behind that cell's endpoint number.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayMember,
    DisplayPointInTimeData,
    DisplaySeries,
    DisplaySignal,
)
from signals.display.registry import register_display_member

MEMBER_NAME = "price_path"
LABEL = "Price path"
# The window in TRADING bars (not calendar) — matched to trailing_returns' 90-bar window on purpose:
# the sparkline is the shape whose endpoint the 90d return cell states. Retune here alone; the FE
# reads the slot count off the series itself (it hardcodes no window).
BARS = 90
# ``price_history`` trims by CALENDAR days; 90 trading bars ~= 126 calendar days, so 150 covers it
# with holiday slack. A thinner name is honestly left-padded (see the module docstring).
LOOKBACK_DAYS = 150

SERIES_KEY = "close"
SERIES_LABEL = "close"


def compute(bars: list[dict[str, Any]], asof: date) -> DisplaySignal | None:
    """Pure: the last ``BARS`` closes over ascending EOD bars (the last bar is the latest knowable at
    asof), LEFT-padded with ``None`` to exactly ``BARS`` slots. A null close is not a bar and drops
    out first; no real close at all -> ``None`` (nothing to draw — an honest absence)."""
    priced = [b for b in bars if b.get("close") is not None]
    if not priced:
        return None
    tail = priced[-BARS:]
    closes = [float(b["close"]) for b in tail]
    n = len(closes)
    values: list[float | None] = [None] * (BARS - n)
    values.extend(closes)
    series = DisplaySeries(key=SERIES_KEY, label=SERIES_LABEL, unit="price", values=values)
    basis = DisplayBasis(
        source="fact_price_eod",
        params={"bars": BARS, "lookback_days": LOOKBACK_DAYS},
        bars_used=n,
        window_start=tail[0]["d"],
        window_end=tail[-1]["d"],
        # the shortfall is NAMED (the panel's fine print reads it, the cell's hover carries it), so a
        # short path is never mistaken for a full window (#6)
        note=f"thin: {n}/{BARS} bars" if n < BARS else None,
    )
    return DisplaySignal(kind=MEMBER_NAME, label=LABEL, series=[series], basis=basis)


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read EOD bars via the point-in-time view; all shaping happens in the pure ``compute``."""
    return compute(pit.price_history(security_id, lookback_days=LOOKBACK_DAYS), asof)


# The READ-HORIZON declaration (``signals/horizons.py``): exactly the lookback ``display`` passes to
# ``price_history`` — the display PIT's price bound is derived from the max over every member.
HORIZONS: dict[str, int | None] = {"fact_price_eod": LOOKBACK_DAYS}


MEMBER = register_display_member(
    DisplayMember(name=MEMBER_NAME, compute=display, horizons=HORIZONS)
)
