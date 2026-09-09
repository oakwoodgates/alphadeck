from __future__ import annotations

import math
from datetime import date, timedelta

from signals.display import price_path
from signals.display.base import DisplaySignal

_ASOF = date(2026, 7, 1)


def _bars(closes: list[float | None], end: date = _ASOF) -> list[dict]:
    start = end - timedelta(days=len(closes) - 1)
    return [{"d": start + timedelta(days=i), "close": c} for i, c in enumerate(closes)]


def _series(sig):
    assert sig is not None
    assert [s.key for s in sig.series] == ["close"]  # the ONE series the basket cell reads
    return sig.series[0]


def test_the_window_is_the_last_90_closes_ascending_newest_last():
    closes = [float(i) for i in range(1, 201)]  # 200 bars; the window is the LAST 90 = 111..200
    s = _series(price_path.compute(_bars(closes), _ASOF))
    assert len(s.values) == price_path.BARS == 90
    assert s.values == [float(i) for i in range(111, 201)]
    assert s.values[-1] == 200.0  # the last slot is "now" — the latest close knowable at asof
    assert s.unit == "price" and s.label == "close"


def test_the_window_matches_the_90d_return_bar_window():
    # the sparkline is the path BEHIND the 90d return cell — the same trading-bar window, by design
    from signals.display import trailing_returns

    assert price_path.BARS in trailing_returns.WINDOWS
    assert price_path.BARS == 90


def test_a_thin_tape_is_left_padded_never_stretched():
    # 34 bars: 56 honest None slots, then the 34 real closes right-aligned to "now" — the FE draws a
    # shorter path, never one stretched across the window to look like a full tape (#6/#9)
    closes = [10.0 + i for i in range(34)]
    sig = price_path.compute(_bars(closes), _ASOF)
    s = _series(sig)
    assert len(s.values) == 90  # the slot count NEVER shrinks — the FE's fixed-slot contract
    assert s.values[:56] == [None] * 56
    assert s.values[56:] == closes
    assert sig.basis.bars_used == 34
    assert sig.basis.note == "thin: 34/90 bars"  # the shortfall is named, never silent


def test_a_full_window_carries_no_thin_note_and_no_gaps():
    sig = price_path.compute(_bars([1.0] * 90), _ASOF)
    assert sig.basis.bars_used == 90 and sig.basis.note is None
    assert None not in _series(sig).values


def test_a_single_close_is_still_emitted_as_the_honest_tape():
    # one bar: 89 gaps + one value — the FE reads "a point, not a path" as "—"; nothing is invented
    sig = price_path.compute(_bars([42.0]), _ASOF)
    s = _series(sig)
    assert s.values[:-1] == [None] * 89 and s.values[-1] == 42.0
    assert sig.basis.note == "thin: 1/90 bars"


def test_null_closes_are_not_bars():
    sig = price_path.compute(_bars([None, 5.0, None, 6.0]), _ASOF)
    s = _series(sig)
    assert s.values[-2:] == [5.0, 6.0]  # a None close never occupies a slot
    assert s.values[:-2] == [None] * 88
    assert sig.basis.bars_used == 2 and sig.basis.note == "thin: 2/90 bars"


def test_no_bars_returns_none():
    assert price_path.compute([], _ASOF) is None
    assert price_path.compute(_bars([None, None]), _ASOF) is None


def test_pure_reads_only_the_bars_handed_to_it_no_lookahead():
    # the reading at an EARLIER asof must equal computing over ONLY the bars up to it — the bitemporal
    # PIT enforces the <=asof trim (tested at the API layer); this pins that the shape depends on
    # NOTHING past the last bar it is handed (the last slot is always "now")
    full = _bars([10.0, 11.0, 12.0, 13.0, 99.0])  # the 99.0 is the "future" bar
    early = full[:-1]
    assert _series(price_path.compute(early, _ASOF)).values[-1] == 13.0  # never the 99
    assert _series(price_path.compute(full, _ASOF)).values[-1] == 99.0


def test_basis_shows_the_work():
    bars = _bars([float(i) for i in range(1, 121)])  # 120 bars; the window is the last 90
    sig = price_path.compute(bars, _ASOF)
    assert sig.kind == "price_path" and sig.label == "Price path"
    assert sig.basis.source == "fact_price_eod"
    assert sig.basis.params == {"bars": 90, "lookback_days": 150}
    assert sig.basis.bars_used == 90
    # the window is the EXACT tape behind the slots, not the fetch
    assert sig.basis.window_start == bars[-90]["d"]
    assert sig.basis.window_end == _ASOF
    # a shape, never a scalar: nothing here for the sort, the panel strip, or the call to read (#4)
    assert sig.metrics == [] and sig.events == [] and sig.headline is None


def test_the_calendar_lookback_covers_the_bar_window_with_slack():
    # price_history trims by CALENDAR days; the fetch must comfortably hold BARS trading bars
    assert price_path.LOOKBACK_DAYS >= math.ceil(price_path.BARS * 7 / 5) + 10
    assert price_path.HORIZONS == {"fact_price_eod": price_path.LOOKBACK_DAYS}


def test_the_series_rides_the_generic_wire_and_carries_no_call_fields():
    sig = price_path.compute(_bars([1.0, 2.0]), _ASOF)
    dumped = sig.model_dump(mode="json")
    assert dumped["series"][0]["values"][-2:] == [1.0, 2.0]
    assert dumped["series"][0]["values"][0] is None  # a gap serializes as null, never a number
    assert set(dumped["series"][0]) == {"key", "label", "unit", "values"}
    # every OTHER member's signal still dumps with an EMPTY series list (a defaulted field — no member
    # had to change to admit the widening)
    assert "series" in DisplaySignal.model_fields
    assert DisplaySignal.model_fields["series"].default_factory is not None
