from __future__ import annotations

from datetime import date, timedelta

from signals.display import sma

_ASOF = date(2026, 7, 1)


def _bars(closes: list[float | None], end: date = _ASOF) -> list[dict]:
    """Ascending consecutive-day bars ending at ``end`` (so the default is never stale vs _ASOF)."""
    start = end - timedelta(days=len(closes) - 1)
    return [{"d": start + timedelta(days=i), "close": c} for i, c in enumerate(closes)]


def _by_key(sig) -> dict:
    return {m.key: m for m in sig.metrics}


def test_full_history_happy_path():
    closes = [float(i) for i in range(1, 261)]  # 1..260, monotonic: hand-computable, no flips
    sig = sma.compute(_bars(closes), _ASOF)
    assert sig is not None and sig.kind == "sma_position"
    m = _by_key(sig)
    assert m["close"].value == 260.0
    assert m["sma50"].value == 235.5  # mean(211..260)
    assert m["sma200"].value == 160.5  # mean(61..260)
    assert m["pct_vs_sma50"].value == round((260.0 / 235.5 - 1.0) * 100.0, 2)  # 10.4
    assert m["pct_vs_sma200"].value == round((260.0 / 160.5 - 1.0) * 100.0, 2)  # 61.99
    assert all(m[k].note is None for k in m)
    assert sig.events == []  # price never crossed a line it was always above
    assert sig.basis.source == "fact_price_eod"
    assert sig.basis.params == {"fast": 50, "slow": 200, "lookback_days": 600}
    assert sig.basis.bars_used == 260
    assert sig.basis.window_start == _ASOF - timedelta(days=259)
    assert sig.basis.window_end == _ASOF
    assert sig.basis.note is None


def test_thin_history_is_honest():
    closes = [float(i) for i in range(1, 141)]  # 140 bars: SMA50 real, SMA200 an honest gap
    sig = sma.compute(_bars(closes), _ASOF)
    m = _by_key(sig)
    assert m["sma50"].value == 115.5  # mean(91..140)
    assert m["sma200"].value is None
    assert m["sma200"].note == "n/a: 140/200 bars"
    assert m["pct_vs_sma200"].value is None
    assert m["pct_vs_sma200"].note == "n/a: 140/200 bars"
    assert not any(e.key in ("cross_sma200", "golden_cross", "death_cross") for e in sig.events)


def test_no_bars_returns_none():
    assert sma.compute([], _ASOF) is None
    assert sma.compute(_bars([None, None]), _ASOF) is None


def test_bars_with_none_closes_are_dropped_not_counted():
    closes: list[float | None] = [None] + [100.0] * 60 + [None]
    sig = sma.compute(_bars(closes), _ASOF)
    assert sig.basis.bars_used == 60  # only real closes stand behind the reading
    assert _by_key(sig)["sma50"].value == 100.0


def test_price_cross_up_is_stamped_on_the_exact_bar():
    # 50 flat bars (price ON the line — ties, not crosses), a 2-bar dip below, then the cross above.
    closes = [100.0] * 50 + [90.0, 90.0, 120.0]
    bars = _bars(closes)
    sig = sma.compute(bars, _ASOF)
    flips = {e.key: e for e in sig.events}
    assert flips["cross_sma50"].date == bars[52]["d"]  # the first bar back on the far side
    assert flips["cross_sma50"].direction == "up"
    assert flips["cross_sma50"].label == "price crossed above 50d SMA"


def test_tie_on_the_line_is_not_a_flip():
    days = [_ASOF + timedelta(days=i) for i in range(4)]
    # touch-and-return: above -> ON the line -> above again = no cross
    assert sma._last_flip(days, [1.0, 0.0, 1.0, 2.0]) is None
    # a cross THROUGH the line stamps the first bar on the far side
    assert sma._last_flip(days, [1.0, 0.0, -1.0, -2.0]) == (days[2], "down")
    # leading zeros/Nones only seed state, never flip
    assert sma._last_flip(days, [None, 0.0, -1.0, -2.0]) is None


def test_most_recent_flip_wins():
    days = [_ASOF + timedelta(days=i) for i in range(5)]
    assert sma._last_flip(days, [1.0, -1.0, -1.0, 1.0, 1.0]) == (days[3], "up")


def test_golden_cross_detected_after_a_recovery():
    # Flat base, a hard 30-bar decline (50d sinks below 200d, seeding the sign state), then a strong
    # 60-bar recovery that lifts the 50d back through the 200d: the most recent 50x200 flip is UP.
    recovery_start = 240
    closes = [100.0] * 210 + [50.0] * 30 + [150.0] * 60
    bars = _bars(closes)
    sig = sma.compute(bars, _ASOF)
    flips = {e.key: e for e in sig.events}
    assert "golden_cross" in flips and "death_cross" not in flips
    assert flips["golden_cross"].direction == "up"
    assert flips["golden_cross"].date > bars[recovery_start]["d"]
    # and the price itself crossed back above the 50d on the first recovery bar
    assert flips["cross_sma50"].date == bars[recovery_start]["d"]
    assert flips["cross_sma50"].direction == "up"


def test_stale_tape_notes_the_basis():
    closes = [float(i) for i in range(1, 61)]
    sig = sma.compute(_bars(closes, end=_ASOF - timedelta(days=15)), _ASOF)
    assert sig.basis.note == "stale: last bar 15d before asof"
    assert sig.basis.window_end == _ASOF - timedelta(days=15)


def test_compute_is_pure_of_now():
    bars = _bars([float(i) for i in range(1, 261)])
    a = sma.compute(bars, _ASOF)
    b = sma.compute(bars, _ASOF)
    assert a.model_dump() == b.model_dump()
