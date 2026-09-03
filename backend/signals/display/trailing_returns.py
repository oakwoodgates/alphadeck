"""Trailing price returns — the name's EOD close return over 1/7/30/90/252 trading days (1Y).

Display-only tape context: a single subtraction over two closes, never predictive. For each window
the latest close vs the close N trading BARS back, over ONLY bars dated <= asof (the bitemporal read
enforces no-lookahead, #1). A window with fewer than N+1 real closes reads an HONEST gap ("—" + the
why), never a fabricated or zero-filled number (#6/#9).

END-OF-DAY, not intraday: the shortest window is one trading day (the last close vs the PRIOR
close) — deliberately labeled ``1d``, never ``24h``. This platform has no intraday tape
(docs/DATA_SOURCES.md), so every window is a trading-day count of EOD closes.
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

MEMBER_NAME = "trailing_returns"
LABEL = "Trailing returns"
# Windows in TRADING days (bars), NOT calendar: 1d = last close vs the prior close. A window of N
# needs N+1 bars (the close now + the close N bars back). 252 ~= one trading year.
WINDOWS = (1, 7, 30, 90, 252)
# Friendly overrides for a window whose mechanical "{n}d" would read poorly. The 1Y key is
# bar-count-AGNOSTIC ("ret_1y", not "ret_252d"), so retuning 252 never churns the FE contract.
_KEY = {252: "ret_1y"}
_LABEL = {252: "1Y"}
# ``price_history`` trims by CALENDAR days; the longest window (1Y = 252 trading bars ~= 353 calendar
# days) needs 253 bars, so 420 calendar days (~300 trading bars) covers it with holiday slack. A
# thinner name reads honest gaps ("—" + the why); a name under ~1y of tape simply blanks the 1Y cell.
LOOKBACK_DAYS = 420


def _tone(value: float) -> str | None:
    """Green (pos) / red (neg) opt-in for the FE; a flat 0.0% stays neutral (neither up nor down)."""
    if value > 0.0:
        return "pos"
    if value < 0.0:
        return "neg"
    return None


def _metric(closes: list[float], n: int, window: int) -> DisplayMetric:
    """One window's return = ``close_now / close_(window bars back) - 1`` (percent). An HONEST gap
    (value=None + the why) — never a fabricated or zero-filled number — when the tape is shorter than
    ``window + 1`` bars or the base close is non-positive; the note distinguishes the two reasons (#6).
    """
    key, label = _KEY.get(window, f"ret_{window}d"), _LABEL.get(window, f"{window}d")
    if n < window + 1:
        return DisplayMetric(key=key, label=label, unit="pct", note=f"n/a: {n}/{window + 1} bars")
    base = closes[-(window + 1)]
    if base <= 0.0:
        return DisplayMetric(key=key, label=label, unit="pct", note="n/a: non-positive base close")
    value = round((closes[-1] / base - 1.0) * 100.0, 2)
    return DisplayMetric(key=key, label=label, value=value, unit="pct", tone=_tone(value))


def compute(bars: list[dict[str, Any]], asof: date) -> DisplaySignal | None:
    """Pure trailing returns over ascending EOD bars (the last bar is the latest knowable at asof)."""
    bars = [b for b in bars if b.get("close") is not None]
    if not bars:
        return None
    closes = [float(b["close"]) for b in bars]
    dates = [b["d"] for b in bars]
    n = len(bars)

    metrics = [_metric(closes, n, w) for w in WINDOWS]
    basis = DisplayBasis(
        source="fact_price_eod",
        params={"windows_trading_days": list(WINDOWS), "lookback_days": LOOKBACK_DAYS},
        bars_used=n,
        window_start=dates[0],
        window_end=dates[-1],
    )
    return DisplaySignal(kind=MEMBER_NAME, label=LABEL, metrics=metrics, basis=basis)


def display(pit: DisplayPointInTimeData, security_id: UUID, asof: date) -> DisplaySignal | None:
    """Read EOD bars via the point-in-time view; all arithmetic happens in the pure ``compute``."""
    return compute(pit.price_history(security_id, lookback_days=LOOKBACK_DAYS), asof)


# The READ-HORIZON declaration (``signals/horizons.py``): exactly the lookback ``display`` passes to
# ``price_history`` — the display PIT's price bound is derived from the max over every member.
HORIZONS: dict[str, int | None] = {"fact_price_eod": LOOKBACK_DAYS}


MEMBER = register_display_member(
    DisplayMember(name=MEMBER_NAME, compute=display, horizons=HORIZONS)
)
