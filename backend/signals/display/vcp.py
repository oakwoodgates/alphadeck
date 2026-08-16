"""§3.2 — base-tightening / VCP (volatility contraction) — WARMING-stage context, DISPLAY-first (R14).

The coil BEFORE a breakout: successive pullback ranges contract while price holds above a rising trend
line. Read-only tape context (like ``rvol`` / ``sma_position``), structurally OFF the call path (#4/#7):
no ``role``, it cannot fire/arm/veto/grade — it NEVER warms or arms a call, it only SURFACES the setup so
the operator sees a name coiling. The loud "coiling" headline is reserved for the exception — a name that
is ACTUALLY coiling (all conditions met); every other name shows the quiet contraction metrics with no
headline (inverse-loudness, #7).

The rule (module dials below — the display seam is barred from importing ``CallConfig``):
  * split the last ``VCP_WINDOW`` bars into ``VCP_SEGMENTS`` consecutive segments (oldest -> newest);
  * each segment's DEPTH = ``(max(high) - min(low)) / max(high)`` — the peak-to-trough drawdown as a
    fraction of the segment's OWN peak (normalized per-segment, so a steady uptrend's growing price does
    NOT fake a contraction — only genuinely tighter ranges shrink the depth);
  * COILING when the depths strictly contract (T1 > T2 > T3), the newest depth is <= ``VCP_MAX_LAST_RATIO``
    of the oldest (a MEANINGFUL contraction, not a trivial one), AND the close holds above a RISING
    ``SMA_WINDOW``-day SMA.

Honesty (#6/#9): high/low fall back to the close when absent (an EOD-close-only tape still reads a coil off
close range); a degenerate/flat base yields no contraction ratio (an honest gap), never a fabricated one.
Pure bar math, no new data — reads the same ``fact_price_eod`` the other price members read.
"""

from __future__ import annotations

from datetime import date
from statistics import fmean
from typing import Any
from uuid import UUID

from signals.display.base import (
    DisplayBasis,
    DisplayHeadline,
    DisplayMember,
    DisplayMetric,
    DisplayPointInTimeData,
    DisplaySignal,
)
from signals.display.registry import register_display_member

MEMBER_NAME = "vcp"
LABEL = "Base tightening (VCP)"
VCP_WINDOW = 60  # bars of base examined for the contraction
VCP_SEGMENTS = 3  # consecutive contraction segments (Minervini-style T1 > T2 > T3)
VCP_MAX_LAST_RATIO = 0.65  # newest depth must be <= this x the oldest -> a MEANINGFUL contraction
SMA_WINDOW = 50  # the trend line the coil must hold above
SLOPE_BARS = 5  # "rising" = the SMA now vs this many bars back (~one trading week)
# calendar pull: covers the 60-bar base + the 50d SMA + the 5-bar slope with weekend/holiday slack
LOOKBACK_DAYS = 120


def _segments(bars: list[dict[str, Any]], window: int, segments: int) -> list[list[dict[str, Any]]]:
    """The last ``window`` bars split into ``segments`` consecutive, near-even chunks (oldest -> newest);
    the final chunk absorbs any remainder. Empty when there aren't enough bars for one bar per segment.
    """
    tail = bars[-window:]
    n = len(tail)
    size = n // segments
    if size == 0:
        return []
    out: list[list[dict[str, Any]]] = []
    for k in range(segments):
        start = k * size
        end = n if k == segments - 1 else (k + 1) * size
        out.append(tail[start:end])
    return out


def _hl(bar: dict[str, Any]) -> tuple[float, float]:
    """(high, low) for a bar, falling back to the close when either is absent (an EOD-close-only tape)."""
    close = float(bar["close"])
    hi = float(bar["high"]) if bar.get("high") is not None else close
    lo = float(bar["low"]) if bar.get("low") is not None else close
    return hi, lo


def _depth(seg: list[dict[str, Any]]) -> float | None:
    """Segment DEPTH = (max high - min low) / max high — the peak-to-trough drawdown as a fraction of the
    segment's own peak. None on a non-positive peak (degenerate)."""
    his, los = zip(*(_hl(b) for b in seg))
    hi = max(his)
    if hi <= 0.0:
        return None
    return (hi - min(los)) / hi


def _sma_at(closes: list[float], end_idx: int, window: int) -> float | None:
    """The ``window``-bar simple moving average ending (inclusive) at ``end_idx``, or None when there
    aren't ``window`` bars up to it."""
    if end_idx + 1 < window or end_idx < 0:
        return None
    return fmean(closes[end_idx - window + 1 : end_idx + 1])


def compute(bars: list[dict[str, Any]], asof: date) -> DisplaySignal | None:
    """Pure VCP over ascending EOD bars: the contraction of successive segment depths + whether the close
    holds above a rising SMA. Always emits the (quiet) contraction metrics when computable; the loud
    "coiling" headline appears ONLY when every coil condition is met (#7). None when there isn't enough
    tape to examine a base at all."""
    bars = [b for b in bars if b.get("close") is not None]
    if len(bars) < VCP_WINDOW:
        return None
    closes = [float(b["close"]) for b in bars]
    n = len(closes)

    segs = _segments(bars, VCP_WINDOW, VCP_SEGMENTS)
    depths = [_depth(s) for s in segs]
    contraction: float | None = None
    strictly = False
    meaningful = False
    if len(depths) == VCP_SEGMENTS and all(d is not None and d > 0.0 for d in depths):
        d = [x for x in depths if x is not None]
        strictly = all(d[k] > d[k + 1] for k in range(len(d) - 1))
        meaningful = d[-1] <= d[0] * VCP_MAX_LAST_RATIO
        contraction = round(d[-1] / d[0], 3)

    sma_now = _sma_at(closes, n - 1, SMA_WINDOW)
    sma_prev = _sma_at(closes, n - 1 - SLOPE_BARS, SMA_WINDOW)
    rising = sma_now is not None and sma_prev is not None and sma_now > sma_prev
    above = sma_now is not None and closes[-1] >= sma_now
    coiling = bool(strictly and meaningful and above and rising)

    base_depth = _depth(bars[-VCP_WINDOW:])
    metrics = [
        DisplayMetric(
            key="contraction",
            label="contraction",
            value=contraction,
            unit="ratio",
            note=None if contraction is not None else "n/a: flat/degenerate base",
        ),
        DisplayMetric(
            key="base_depth",
            label="base depth",
            value=round(base_depth * 100.0, 2) if base_depth is not None else None,
            unit="pct",
            note=None if base_depth is not None else "n/a: flat/degenerate base",
        ),
        _vs_sma_metric(closes[-1], sma_now, n),
    ]

    headline = (
        DisplayHeadline(
            key="coiling",
            label=f"Base coiling — {VCP_SEGMENTS} tightening contractions above a rising {SMA_WINDOW}d",
            glyph="flat",  # a coil is sideways; direction is unknown until it breaks (never forecast, #4)
            detail=(
                f"range narrowed to {round(contraction * 100)}% of the base start"
                if contraction is not None
                else None
            ),
        )
        if coiling
        else None
    )

    basis = DisplayBasis(
        source="fact_price_eod",
        params={
            "window": VCP_WINDOW,
            "segments": VCP_SEGMENTS,
            "max_last_ratio": VCP_MAX_LAST_RATIO,
            "sma_window": SMA_WINDOW,
            "slope_bars": SLOPE_BARS,
            "lookback_days": LOOKBACK_DAYS,
        },
        bars_used=n,
        window_start=bars[-VCP_WINDOW:][0]["d"],
        window_end=bars[-1]["d"],
        note=None if sma_now is not None else f"n/a: {n}/{SMA_WINDOW} bars for the trend line",
    )
    return DisplaySignal(
        kind=MEMBER_NAME, label=LABEL, headline=headline, metrics=metrics, basis=basis
    )


def _vs_sma_metric(close: float, sma: float | None, n: int) -> DisplayMetric:
    """Price vs the trend line (context for "holding above"): an honest gap when the SMA isn't computable
    or is non-positive, never a fabricated percentage."""
    if sma is None:
        return DisplayMetric(
            key="vs_sma", label=f"vs {SMA_WINDOW}d", unit="pct", note=f"n/a: {n}/{SMA_WINDOW} bars"
        )
    if sma <= 0.0:
        return DisplayMetric(
            key="vs_sma", label=f"vs {SMA_WINDOW}d", unit="pct", note="n/a: non-positive SMA"
        )
    return DisplayMetric(
        key="vs_sma",
        label=f"vs {SMA_WINDOW}d",
        value=round((close / sma - 1.0) * 100.0, 2),
        unit="pct",
    )


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read EOD bars via the point-in-time view; all arithmetic happens in the pure ``compute``."""
    return compute(pit.price_history(security_id, lookback_days=LOOKBACK_DAYS), asof)


MEMBER = register_display_member(DisplayMember(name=MEMBER_NAME, compute=display))
