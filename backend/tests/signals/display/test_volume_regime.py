from __future__ import annotations

from datetime import date, timedelta

from signals.display import volume_regime

_ASOF = date(2026, 7, 1)


def _bars(rows: list[tuple[float | None, float | None]], end: date = _ASOF) -> list[dict]:
    start = end - timedelta(days=len(rows) - 1)
    return [
        {"d": start + timedelta(days=i), "close": c, "volume": v} for i, (c, v) in enumerate(rows)
    ]


def _by_key(sig) -> dict:
    return {m.key: m for m in sig.metrics}


def test_ratio_and_dollar_volume_math():
    rows = [(10.0, 100.0)] * 60 + [(10.0, 200.0)] * 20  # base 100, recent 200
    sig = volume_regime.compute(_bars(rows), _ASOF)
    m = _by_key(sig)
    assert m["vol_ratio"].value == 2.0
    assert m["adv_usd_20d"].value == 2000.0  # 10 close x 200 volume
    assert sig.basis.bars_used == 80
    assert sig.basis.note is None


def test_insufficient_bars_degrade_each_metric_honestly():
    rows = [(10.0, 100.0)] * 50  # enough for the 20d dollar figure, not the 80-bar ratio
    m = _by_key(volume_regime.compute(_bars(rows), _ASOF))
    assert m["vol_ratio"].value is None
    assert m["vol_ratio"].note == "n/a: 50/80 volume bars"
    assert m["adv_usd_20d"].value == 1000.0


def test_bars_without_volume_are_excluded_and_said():
    rows = [(10.0, None)] * 5 + [(10.0, 100.0)] * 60 + [(10.0, 300.0)] * 20
    sig = volume_regime.compute(_bars(rows), _ASOF)
    assert sig.basis.bars_used == 80  # only volume-bearing bars stand behind the ratio
    assert sig.basis.note == "5 bars without volume excluded"
    assert _by_key(sig)["vol_ratio"].value == 3.0


def test_zero_base_volume_is_a_gap_not_a_division():
    rows = [(10.0, 0.0)] * 60 + [(10.0, 100.0)] * 20
    m = _by_key(volume_regime.compute(_bars(rows), _ASOF))
    assert m["vol_ratio"].value is None
    assert m["vol_ratio"].note == "n/a: zero base volume"


def test_no_volume_bearing_bars_returns_none():
    assert volume_regime.compute(_bars([(10.0, None)] * 30), _ASOF) is None
    assert volume_regime.compute([], _ASOF) is None
