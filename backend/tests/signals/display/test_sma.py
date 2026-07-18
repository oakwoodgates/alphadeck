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
    assert m["ma_fast"].value == 235.5  # mean(211..260)
    assert m["ma_slow"].value == 160.5  # mean(61..260)
    assert m["ma_fast"].label == "50d SMA"  # label derives from the params, never hardcoded
    assert m["pct_vs_fast"].value == round((260.0 / 235.5 - 1.0) * 100.0, 2)  # 10.4
    assert m["pct_vs_slow"].value == round((260.0 / 160.5 - 1.0) * 100.0, 2)  # 61.99
    assert all(m[k].note is None for k in m)
    assert sig.events == []  # price never crossed a line it was always above
    assert sig.basis.source == "fact_price_eod"
    assert sig.basis.params == {"fast": 50, "slow": 200, "lookback_days": 600, "slope_bars": 5}
    assert sig.basis.bars_used == 260
    assert sig.basis.window_start == _ASOF - timedelta(days=259)
    assert sig.basis.window_end == _ASOF
    assert sig.basis.note is None
    # the posture chip: monotonic up = fast over slow, rising — the strongest quadrant
    assert sig.headline is not None
    assert sig.headline.key == "above_rising"
    assert sig.headline.glyph == "up"
    assert sig.headline.label == "50d over 200d · rising"
    assert sig.headline.detail == "price above both · rising"


def test_thin_history_is_honest():
    closes = [float(i) for i in range(1, 141)]  # 140 bars: SMA50 real, SMA200 an honest gap
    sig = sma.compute(_bars(closes), _ASOF)
    m = _by_key(sig)
    assert m["ma_fast"].value == 115.5  # mean(91..140)
    assert m["ma_slow"].value is None
    assert m["ma_slow"].note == "n/a: 140/200 bars"
    assert m["pct_vs_slow"].value is None
    assert m["pct_vs_slow"].note == "n/a: 140/200 bars"
    assert not any(e.key in ("cross_sma200", "golden_cross", "death_cross") for e in sig.events)
    # the posture degrades to the half it can say — never a fake quadrant
    assert sig.headline is not None
    assert sig.headline.key == "partial_rising"
    assert sig.headline.label == "50d rising · 200d n/a"
    assert sig.headline.glyph == "up"
    assert sig.headline.detail == "price above 50d · rising"


def test_no_bars_returns_none():
    assert sma.compute([], _ASOF) is None
    assert sma.compute(_bars([None, None]), _ASOF) is None


def test_bars_with_none_closes_are_dropped_not_counted():
    closes: list[float | None] = [None] + [100.0] * 60 + [None]
    sig = sma.compute(_bars(closes), _ASOF)
    assert sig.basis.bars_used == 60  # only real closes stand behind the reading
    assert _by_key(sig)["ma_fast"].value == 100.0


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


def test_posture_rolling_over_still_above_but_fading():
    # a long low base, a 55-bar rally, then a 5-bar fade: the 50d is far above the 200d but has
    # started falling — the "over · falling" quadrant, price back below the fast line
    closes = [10.0] * 200 + [100.0] * 55 + [90.0] * 5
    sig = sma.compute(_bars(closes), _ASOF)
    assert sig.headline.key == "above_falling"
    assert sig.headline.glyph == "turn_down"
    assert sig.headline.label == "50d over 200d · falling"
    assert sig.headline.detail == "price below 50d, above 200d · falling"


def test_posture_repairing_below_but_turning_up():
    # decline then a fresh 5-bar recovery: the 50d turns up while still under the 200d — the
    # golden-cross-pending quadrant
    closes = [100.0] * 210 + [50.0] * 30 + [150.0] * 5
    sig = sma.compute(_bars(closes), _ASOF)
    assert sig.headline.key == "below_rising"
    assert sig.headline.glyph == "turn_up"
    assert sig.headline.label == "50d under 200d · rising"
    assert sig.headline.detail == "price above both · rising"


def test_posture_flat_tape_reads_level_and_flat():
    sig = sma.compute(_bars([100.0] * 260), _ASOF)
    assert sig.headline.key == "level_flat"
    assert sig.headline.glyph == "flat"
    assert sig.headline.label == "50d level with 200d · flat"
    assert sig.headline.detail == "price at both · flat"


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
