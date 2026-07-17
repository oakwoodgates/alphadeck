"""SMA position / flips — where a name sits vs its 50/200-day SMA, and when it last flipped.

Display-only tape context (deterministic arithmetic over EOD closes, never predictive TA): the
latest close vs each SMA, and the most recent crosses — price × 50d, price × 200d, and the 50 × 200
golden/death cross — each stamped with the bar date the tape actually printed, never the query asof.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayEvent,
    DisplayMember,
    DisplayMetric,
    DisplayPointInTimeData,
    DisplaySignal,
)
from signals.display.registry import register_display_member

MEMBER_NAME = "sma_position"
LABEL = "SMA position (50/200d)"
SMA_FAST = 50
SMA_SLOW = 200
# ``price_history`` trims by CALENDAR days: 600 ≈ 410 trading bars, leaving ~210 SMA200-computable
# bars — ~10 months of 50×200 cross search. A fresh name's initial 1y pull is honestly thinner; the
# basis (bars_used + the n/a notes) shows exactly how much tape the reading stands on.
LOOKBACK_DAYS = 600
# Basis note when the last bar lags asof by more than this (the stale/delisted-tape tell).
STALE_GAP_DAYS = 10


def _sma_series(closes: list[float], window: int) -> list[float | None]:
    """Rolling mean: out[i] = mean(closes[i-window+1 .. i]) once ``window`` bars exist, else None."""
    out: list[float | None] = [None] * len(closes)
    total = 0.0
    for i, close in enumerate(closes):
        total += close
        if i >= window:
            total -= closes[i - window]
        if i >= window - 1:
            out[i] = total / window
    return out


def _last_flip(dates: list[date], diffs: list[float | None]) -> tuple[date, str] | None:
    """The most recent sign flip in a diff series: the first bar whose nonzero sign opposes the
    previous nonzero sign. Exact zeros are skipped — a close ON the line is not a cross (touch-and-
    return flips nothing; a cross THROUGH the line stamps the first bar on the far side)."""
    prev = 0
    flip: tuple[date, str] | None = None
    for d, diff in zip(dates, diffs):
        if diff is None or diff == 0.0:
            continue
        sign = 1 if diff > 0.0 else -1
        if prev and sign != prev:
            flip = (d, "up" if sign > 0 else "down")
        prev = sign
    return flip


def _sma_metric(key: str, label: str, value: float | None, bars: int, window: int) -> DisplayMetric:
    note = None if value is not None else f"n/a: {bars}/{window} bars"
    return DisplayMetric(key=key, label=label, value=value, unit="price", note=note)


def _pct_metric(
    key: str, label: str, close: float, sma: float | None, bars: int, window: int
) -> DisplayMetric:
    if sma is None:
        return DisplayMetric(key=key, label=label, unit="pct", note=f"n/a: {bars}/{window} bars")
    if sma <= 0.0:
        return DisplayMetric(key=key, label=label, unit="pct", note="n/a: non-positive SMA")
    value = round((close / sma - 1.0) * 100.0, 2)
    return DisplayMetric(key=key, label=label, value=value, unit="pct")


def compute(bars: list[dict[str, Any]], asof: date) -> DisplaySignal | None:
    """Pure SMA position/flips over ascending EOD bars (last bar = the latest bar knowable at asof)."""
    bars = [b for b in bars if b.get("close") is not None]
    if not bars:
        return None
    closes = [float(b["close"]) for b in bars]
    dates = [b["d"] for b in bars]
    n = len(bars)

    fast = _sma_series(closes, SMA_FAST)
    slow = _sma_series(closes, SMA_SLOW)
    close = closes[-1]
    sma_fast = round(fast[-1], 4) if fast[-1] is not None else None
    sma_slow = round(slow[-1], 4) if slow[-1] is not None else None

    metrics = [
        DisplayMetric(key="close", label="close", value=round(close, 4), unit="price"),
        _sma_metric("sma50", "50d SMA", sma_fast, n, SMA_FAST),
        _sma_metric("sma200", "200d SMA", sma_slow, n, SMA_SLOW),
        _pct_metric("pct_vs_sma50", "vs 50d", close, sma_fast, n, SMA_FAST),
        _pct_metric("pct_vs_sma200", "vs 200d", close, sma_slow, n, SMA_SLOW),
    ]

    events: list[DisplayEvent] = []
    price_vs_fast = [c - f if f is not None else None for c, f in zip(closes, fast)]
    price_vs_slow = [c - s if s is not None else None for c, s in zip(closes, slow)]
    fast_vs_slow = [f - s if f is not None and s is not None else None for f, s in zip(fast, slow)]
    flip = _last_flip(dates, price_vs_fast)
    if flip is not None:
        d, direction = flip
        word = "above" if direction == "up" else "below"
        events.append(
            DisplayEvent(
                key="cross_sma50",
                label=f"price crossed {word} 50d SMA",
                date=d,
                direction=direction,
            )
        )
    flip = _last_flip(dates, price_vs_slow)
    if flip is not None:
        d, direction = flip
        word = "above" if direction == "up" else "below"
        events.append(
            DisplayEvent(
                key="cross_sma200",
                label=f"price crossed {word} 200d SMA",
                date=d,
                direction=direction,
            )
        )
    flip = _last_flip(dates, fast_vs_slow)
    if flip is not None:
        d, direction = flip
        if direction == "up":
            events.append(
                DisplayEvent(
                    key="golden_cross",
                    label="golden cross: 50d crossed above 200d",
                    date=d,
                    direction="up",
                )
            )
        else:
            events.append(
                DisplayEvent(
                    key="death_cross",
                    label="death cross: 50d crossed below 200d",
                    date=d,
                    direction="down",
                )
            )

    gap = (asof - dates[-1]).days
    basis = DisplayBasis(
        source="fact_price_eod",
        params={"fast": SMA_FAST, "slow": SMA_SLOW, "lookback_days": LOOKBACK_DAYS},
        bars_used=n,
        window_start=dates[0],
        window_end=dates[-1],
        note=f"stale: last bar {gap}d before asof" if gap > STALE_GAP_DAYS else None,
    )
    return DisplaySignal(kind=MEMBER_NAME, label=LABEL, metrics=metrics, events=events, basis=basis)


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read EOD bars via the point-in-time view; all arithmetic happens in the pure ``compute``."""
    return compute(pit.price_history(security_id, lookback_days=LOOKBACK_DAYS), asof)


MEMBER = register_display_member(DisplayMember(name=MEMBER_NAME, compute=display))
