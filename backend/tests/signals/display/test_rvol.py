from __future__ import annotations

from datetime import date, timedelta

from domain.config import DEFAULT_CONFIG
from signals.display import rvol

_ASOF = date(2026, 7, 1)


def _bars(vols: list[float | None], end: date = _ASOF, close: float = 10.0) -> list[dict]:
    """Ascending EOD bars carrying a (constant) close + the given per-bar volume; a None volume is a
    halt/thin bar. The last bar is the as-of (anchor) bar."""
    start = end - timedelta(days=len(vols) - 1)
    return [
        {"d": start + timedelta(days=i), "close": close, "volume": v} for i, v in enumerate(vols)
    ]


def test_rvol_is_the_asof_bar_over_the_8_bar_base_average():
    m = rvol.compute(_bars([100.0] * 8 + [180.0]), _ASOF).metrics[0]  # 8 base @100, anchor 180
    assert m.key == "rvol" and m.label == "RVOL" and m.unit == "ratio"
    assert m.value == 1.8  # 180 / mean(eight 100s)
    # loudness is FE-derived (a warm 'hot' off params.loud_mult), NEVER a green pos/neg tone
    assert m.tone is None and m.note is None


def test_an_ordinary_day_is_a_real_ratio_below_the_loud_threshold():
    m = rvol.compute(_bars([100.0] * 8 + [90.0]), _ASOF).metrics[0]
    assert m.value == 0.9  # a real, quiet reading — the FE renders it muted, not hot


def test_base_is_exactly_the_8_bars_before_the_anchor_not_older_tape():
    # 20 older bars @1 (must be IGNORED), then the 8-bar base @100, then the anchor @200. This pins
    # the window to breakout_base_window (8) so the column and the trigger use the SAME denominator.
    m = rvol.compute(_bars([1.0] * 20 + [100.0] * 8 + [200.0]), _ASOF).metrics[0]
    assert m.value == 2.0  # 200 / mean(the last 8 = 100); the 20 older bars never enter the base


def test_the_loud_threshold_is_backed_volume_and_rides_the_basis_params():
    # >=1.5x is 'loud' (volume-backed); the FE tints it warm off params.loud_mult, so the threshold
    # lives in ONE place (this module) and the FE never hardcodes it. Below 1.5x is muted.
    loud = rvol.compute(_bars([100.0] * 8 + [150.0]), _ASOF)  # exactly 1.5x
    assert loud.metrics[0].value == 1.5 >= loud.basis.params["loud_mult"]
    quiet = rvol.compute(_bars([100.0] * 8 + [149.0]), _ASOF)  # just under
    assert quiet.metrics[0].value == 1.49 < quiet.basis.params["loud_mult"]


def test_no_volume_on_the_asof_bar_is_an_honest_gap_not_a_stale_ratio():
    # a halt / thin-OTC as-of bar: never show an EARLIER bar's ratio as if it were today's (#6)
    m = rvol.compute(_bars([100.0] * 8 + [None]), _ASOF).metrics[0]
    assert m.value is None and m.note == "n/a: no volume on the as-of bar"


def test_thin_tape_blanks_with_the_base_bar_count():
    # only 5 bars total -> 4 bars before the anchor, short of the 8-bar base -> honest "—" (#9)
    m = rvol.compute(_bars([100.0] * 4 + [150.0]), _ASOF).metrics[0]
    assert m.value is None and m.note == "n/a: 4/8 base bars"


def test_zero_base_volume_is_its_own_reason():
    m = rvol.compute(_bars([0.0] * 8 + [100.0]), _ASOF).metrics[0]
    assert (
        m.value is None and m.note == "n/a: zero base volume"
    )  # distinct from a bar-count gap (#6)


def test_a_volumeless_base_bar_is_dropped_and_named_in_the_basis():
    # a halt inside the base window drops out of the mean (mirroring the breakout detector); the
    # basis says how many were excluded (#6). base = mean of the seven present @100 = 100; anchor 150
    vols: list[float | None] = [100.0] * 8 + [150.0]
    vols[3] = None
    sig = rvol.compute(_bars(vols), _ASOF)
    assert sig.metrics[0].value == 1.5
    assert sig.basis.note == "1 base bars without volume excluded"


def test_pure_reads_only_the_bars_handed_to_it_no_lookahead():
    # the reading at an EARLIER asof must equal computing over ONLY the bars up to it — a later bar is
    # never touched. The bitemporal PIT enforces the <=asof trim (framework-tested); this pins that
    # the pure math depends on NOTHING past the last bar it is handed (the last bar is always "now").
    full = _bars([100.0] * 8 + [150.0, 999.0])  # the 999.0 is the "future" bar
    early = full[:-1]  # what the PIT hands at the earlier asof (the 999.0 bar is invisible)
    assert rvol.compute(early, _ASOF).metrics[0].value == 1.5  # anchor 150 over eight 100s
    # with the later bar handed in, IT becomes the anchor (base = the 8 bars before it: seven 100s + 150)
    assert rvol.compute(full, _ASOF).metrics[0].value == round(999.0 / (850.0 / 8.0), 2)  # 9.4


def test_no_bars_returns_none():
    assert rvol.compute([], _ASOF) is None
    assert rvol.compute([{"d": _ASOF, "close": None, "volume": 100.0}], _ASOF) is None


def test_basis_shows_the_work():
    sig = rvol.compute(_bars([100.0] * 8 + [150.0]), _ASOF)
    assert sig.kind == "rvol"
    assert sig.basis.source == "fact_price_eod"
    assert sig.basis.params == {"baseline_bars": 8, "loud_mult": 1.5, "lookback_days": 40}
    assert sig.basis.bars_used == 9
    assert sig.basis.window_end == _ASOF
    assert sig.basis.note is None  # a clean tape drops nothing


def test_dials_mirror_the_call_config_exactly():
    # R1 drift-guard: the display seam CANNOT import CallConfig (test_registry import-ban), so these
    # constants are hand-kept equal to the call's. If they drift, the RVOL column and the breakout
    # trigger would use different windows/thresholds and read as a contradiction — this catches it.
    assert rvol.BASELINE_BARS == DEFAULT_CONFIG.breakout_base_window
    assert rvol.LOUD_MULT == DEFAULT_CONFIG.breakout_volume_mult
