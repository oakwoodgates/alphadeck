from __future__ import annotations

from datetime import date, timedelta

from signals.display import vcp

_ASOF = date(2026, 7, 1)


def _bars(closes: list[float], amps: list[float], end: date = _ASOF) -> list[dict]:
    """Ascending EOD bars: each close bracketed by ``amp`` into a high/low, so the per-segment range
    (and thus the contraction) is fully controlled. Last bar = the as-of bar."""
    start = end - timedelta(days=len(closes) - 1)
    return [
        {"d": start + timedelta(days=i), "close": c, "high": c + a, "low": c - a}
        for i, (c, a) in enumerate(zip(closes, amps))
    ]


# A gentle uptrend (a rising 50d SMA with the close above it) over 80 bars; the amplitude only matters
# inside the last VCP_WINDOW (60) bars, split 20/20/20 across the three contraction segments.
_UPTREND = [100.0 + i * 0.05 for i in range(80)]
_PRE = [0.5] * 20  # outside the 60-bar window


def _by_key(sig) -> dict:
    return {m.key: m for m in sig.metrics}


def test_a_contracting_base_above_a_rising_sma_reads_coiling():
    """The coil: three strictly-tightening contractions (amp 3.0 -> 1.5 -> 0.6) while the close holds
    above a rising 50d SMA -> the loud 'coiling' headline, with a meaningful contraction ratio."""
    amps = _PRE + [3.0] * 20 + [1.5] * 20 + [0.6] * 20
    sig = vcp.compute(_bars(_UPTREND, amps), _ASOF)
    assert sig.headline is not None and sig.headline.key == "coiling"
    assert sig.headline.glyph == "flat"  # a coil is sideways — direction is never forecast (#4)
    m = _by_key(sig)
    assert m["contraction"].value is not None and m["contraction"].value <= vcp.VCP_MAX_LAST_RATIO
    assert (
        m["vs_sma"].value is not None and m["vs_sma"].value >= 0.0
    )  # holding above the trend line


def test_a_loose_constant_range_base_does_not_read_coiling():
    """Same uptrend but a CONSTANT range (no contraction) -> not a coil: the quiet metrics still ride
    the panel, but there is no loud headline (#7 — loudness marks the exception)."""
    sig = vcp.compute(_bars(_UPTREND, [1.5] * 80), _ASOF)
    assert sig.headline is None
    assert (
        _by_key(sig)["contraction"].value > vcp.VCP_MAX_LAST_RATIO
    )  # ~1.0, no meaningful tightening


def test_a_shallow_but_strictly_decreasing_base_is_not_meaningful_enough():
    """Strictly decreasing widths alone are not a coil: a shallow contraction whose newest depth is more
    than VCP_MAX_LAST_RATIO of the oldest stays quiet (the meaningfulness gate, not just monotonicity).
    """
    amps = _PRE + [2.0] * 20 + [1.8] * 20 + [1.6] * 20
    sig = vcp.compute(_bars(_UPTREND, amps), _ASOF)
    assert sig.headline is None
    assert _by_key(sig)["contraction"].value > vcp.VCP_MAX_LAST_RATIO


def test_a_declining_base_below_a_falling_sma_is_not_coiling():
    """A tightening range in a DOWNtrend (close below a falling SMA) is not the pre-breakout coil — the
    rising-SMA / hold-above gate blocks it."""
    downtrend = [120.0 - i * 0.1 for i in range(80)]
    amps = _PRE + [3.0] * 20 + [1.5] * 20 + [0.6] * 20
    sig = vcp.compute(_bars(downtrend, amps), _ASOF)
    assert sig.headline is None


def test_metrics_and_basis_show_the_work():
    amps = _PRE + [3.0] * 20 + [1.5] * 20 + [0.6] * 20
    sig = vcp.compute(_bars(_UPTREND, amps), _ASOF)
    assert sig.kind == "vcp"
    m = _by_key(sig)
    assert [mt.key for mt in sig.metrics] == ["contraction", "base_depth", "vs_sma"]
    assert m["contraction"].unit == "ratio" and m["base_depth"].unit == "pct"
    assert sig.basis.source == "fact_price_eod"
    assert sig.basis.params == {
        "window": 60,
        "segments": 3,
        "max_last_ratio": 0.65,
        "sma_window": 50,
        "slope_bars": 5,
        "lookback_days": 120,
    }
    assert sig.basis.bars_used == 80 and sig.basis.window_end == _ASOF


def test_thin_tape_returns_none():
    """Below the VCP window there is no base to assess -> None (honest, not a fabricated coil)."""
    assert vcp.compute(_bars([100.0] * 40, [1.0] * 40), _ASOF) is None
    assert vcp.compute([], _ASOF) is None


def test_pure_reads_only_the_bars_handed_to_it_no_lookahead():
    """The reading depends only on the bars handed in — the last bar is always 'now'. Appending a wild
    future bar makes IT the newest tape (its blow-out range breaks the tight final segment), proving the
    coil verdict never peeks past the last bar it is given (#1)."""
    amps = _PRE + [3.0] * 20 + [1.5] * 20 + [0.6] * 20
    coil = _bars(_UPTREND, amps)
    assert vcp.compute(coil, _ASOF).headline is not None  # coiling on the handed bars
    spiked = coil + [{"d": _ASOF + timedelta(days=1), "close": 150.0, "high": 180.0, "low": 120.0}]
    assert (
        vcp.compute(spiked, _ASOF).headline is None
    )  # the new last bar blows the final segment open
