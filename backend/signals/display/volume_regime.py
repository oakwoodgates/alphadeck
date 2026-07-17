"""Volume regime — is attention arriving, and how liquid is the name in dollars.

Recent participation vs the prior base (mean volume, last 20 bars ÷ the 60 before them) and the
20-day average dollar volume. Pairs with the breakout detector's base-window idea, display-only.
"""

from __future__ import annotations

from datetime import date
from statistics import fmean
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

MEMBER_NAME = "volume_regime"
LABEL = "Volume regime"
RECENT_BARS = 20
BASE_BARS = 60
LOOKBACK_DAYS = 150  # calendar — comfortably covers the 80 trading bars the ratio needs


def compute(bars: list[dict[str, Any]], asof: date) -> DisplaySignal | None:
    """Pure volume regime over ascending EOD bars; bars without a volume are excluded (and said)."""
    priced = [b for b in bars if b.get("close") is not None]
    vbars = [b for b in priced if b.get("volume") is not None]
    if not vbars:
        return None
    vols = [float(b["volume"]) for b in vbars]
    closes = [float(b["close"]) for b in vbars]
    dates = [b["d"] for b in vbars]
    n = len(vbars)
    need = RECENT_BARS + BASE_BARS

    if n < need:
        ratio = DisplayMetric(
            key="vol_ratio", label="vol 20d/60d", unit="ratio", note=f"n/a: {n}/{need} volume bars"
        )
    else:
        base = fmean(vols[-need:-RECENT_BARS])
        if base <= 0.0:
            ratio = DisplayMetric(
                key="vol_ratio", label="vol 20d/60d", unit="ratio", note="n/a: zero base volume"
            )
        else:
            ratio = DisplayMetric(
                key="vol_ratio",
                label="vol 20d/60d",
                value=round(fmean(vols[-RECENT_BARS:]) / base, 2),
                unit="ratio",
            )

    if n < RECENT_BARS:
        adv = DisplayMetric(
            key="adv_usd_20d",
            label="$vol 20d",
            unit="usd",
            note=f"n/a: {n}/{RECENT_BARS} volume bars",
        )
    else:
        adv = DisplayMetric(
            key="adv_usd_20d",
            label="$vol 20d",
            value=round(
                fmean(c * v for c, v in zip(closes[-RECENT_BARS:], vols[-RECENT_BARS:])), 2
            ),
            unit="usd",
        )

    dropped = len(priced) - n
    basis = DisplayBasis(
        source="fact_price_eod",
        params={"recent_bars": RECENT_BARS, "base_bars": BASE_BARS, "lookback_days": LOOKBACK_DAYS},
        bars_used=n,
        window_start=dates[0],
        window_end=dates[-1],
        note=f"{dropped} bars without volume excluded" if dropped else None,
    )
    return DisplaySignal(kind=MEMBER_NAME, label=LABEL, metrics=[ratio, adv], basis=basis)


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read EOD bars via the point-in-time view; all arithmetic happens in the pure ``compute``."""
    return compute(pit.price_history(security_id, lookback_days=LOOKBACK_DAYS), asof)


MEMBER = register_display_member(DisplayMember(name=MEMBER_NAME, compute=display))
