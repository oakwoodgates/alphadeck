"""52-week range position — how far off the high, how far above the low, and when each printed.

The drawdown/run-up context that frames both a laggard and breakout proximity: % off the 52-week
closing high, % above the 52-week closing low, with the high/low levels and their bar dates.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayMember,
    DisplayMetric,
    DisplayPointInTimeData,
    DisplaySignal,
)
from signals.display.registry import register_display_member

MEMBER_NAME = "range_52w"
LABEL = "52-week range"
LOOKBACK_DAYS = 380  # calendar — a full trading year of bars (~252) plus holiday slack
FULL_YEAR_BARS = 245  # below this the window is honestly less than a year (the basis says so)


def _pct_vs(close: float, level: float) -> float | None:
    if level <= 0.0:
        return None
    return round((close / level - 1.0) * 100.0, 2)


def compute(bars: list[dict[str, Any]], asof: date) -> DisplaySignal | None:
    """Pure 52w range over ascending EOD bars; on a tied high/low the most recent bar is stamped."""
    bars = [b for b in bars if b.get("close") is not None]
    if not bars:
        return None
    closes = [float(b["close"]) for b in bars]
    dates = [b["d"] for b in bars]
    n = len(bars)

    hi_i = max(range(n), key=lambda i: (closes[i], i))
    lo_i = min(range(n), key=lambda i: (closes[i], -i))
    close, high, low = closes[-1], closes[hi_i], closes[lo_i]

    metrics = [
        DisplayMetric(
            key="pct_off_52w_high", label="off 52w high", value=_pct_vs(close, high), unit="pct"
        ),
        DisplayMetric(
            key="pct_above_52w_low", label="above 52w low", value=_pct_vs(close, low), unit="pct"
        ),
        # level chips carry their print date as the note (a quiet hover detail, inline only on a gap)
        DisplayMetric(
            key="high_52w",
            label="52w high",
            value=round(high, 4),
            unit="price",
            note=f"on {dates[hi_i].isoformat()}",
        ),
        DisplayMetric(
            key="low_52w",
            label="52w low",
            value=round(low, 4),
            unit="price",
            note=f"on {dates[lo_i].isoformat()}",
        ),
    ]
    basis = DisplayBasis(
        source="fact_price_eod",
        params={"lookback_days": LOOKBACK_DAYS},
        bars_used=n,
        window_start=dates[0],
        window_end=dates[-1],
        note=f"range over {n} bars, not a full year" if n < FULL_YEAR_BARS else None,
    )
    return DisplaySignal(kind=MEMBER_NAME, label=LABEL, metrics=metrics, basis=basis)


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read EOD bars via the point-in-time view; all arithmetic happens in the pure ``compute``."""
    return compute(pit.price_history(security_id, lookback_days=LOOKBACK_DAYS), asof)


MEMBER = register_display_member(DisplayMember(name=MEMBER_NAME, compute=display))
