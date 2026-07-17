from __future__ import annotations

from datetime import date, timedelta

from signals.display import range52w

_ASOF = date(2026, 7, 1)


def _bars(closes: list[float | None], end: date = _ASOF) -> list[dict]:
    start = end - timedelta(days=len(closes) - 1)
    return [{"d": start + timedelta(days=i), "close": c} for i, c in enumerate(closes)]


def _by_key(sig) -> dict:
    return {m.key: m for m in sig.metrics}


def test_range_math_and_print_dates():
    bars = _bars([60.0, 100.0, 30.0, 60.0, 75.0])
    sig = range52w.compute(bars, _ASOF)
    m = _by_key(sig)
    assert m["pct_off_52w_high"].value == -25.0  # 75 vs the 100 high
    assert m["pct_above_52w_low"].value == 150.0  # 75 vs the 30 low
    assert m["high_52w"].value == 100.0
    assert m["high_52w"].note == f"on {bars[1]['d'].isoformat()}"
    assert m["low_52w"].note == f"on {bars[2]['d'].isoformat()}"
    # 5 bars is honestly not a year — the basis says so
    assert sig.basis.note == "range over 5 bars, not a full year"
    assert sig.basis.params == {"lookback_days": 380}


def test_at_the_high_reads_zero_not_missing_and_ties_stamp_the_recent_bar():
    bars = _bars([50.0, 50.0, 60.0])
    sig = range52w.compute(bars, _ASOF)
    m = _by_key(sig)
    assert m["pct_off_52w_high"].value == 0.0  # AT the high is a real 0, never a fake gap
    assert m["high_52w"].note == f"on {bars[2]['d'].isoformat()}"
    assert m["low_52w"].note == f"on {bars[1]['d'].isoformat()}"  # tied lows -> the most recent


def test_full_year_window_carries_no_thin_note():
    sig = range52w.compute(_bars([50.0] * 250 + [60.0]), _ASOF)
    assert sig.basis.bars_used == 251
    assert sig.basis.note is None


def test_no_bars_returns_none():
    assert range52w.compute([], _ASOF) is None
    assert range52w.compute(_bars([None]), _ASOF) is None
