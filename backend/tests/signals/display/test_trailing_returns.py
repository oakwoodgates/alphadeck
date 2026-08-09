from __future__ import annotations

from datetime import date, timedelta

from signals.display import trailing_returns

_ASOF = date(2026, 7, 1)
_KEYS = ("ret_1d", "ret_7d", "ret_30d", "ret_90d")


def _bars(closes: list[float | None], end: date = _ASOF) -> list[dict]:
    start = end - timedelta(days=len(closes) - 1)
    return [{"d": start + timedelta(days=i), "close": c} for i, c in enumerate(closes)]


def _by_key(sig) -> dict:
    return {m.key: m for m in sig.metrics}


def test_return_math_over_the_four_windows():
    # a 91-bar tape (indices 0..90); only the endpoints of each window are load-bearing, the rest is
    # filler. now=110; 1d back=100 (+10%), 7d back=88 (+25%), 30d back=137.5 (-20%), 90d back=200 (-45%)
    closes = [50.0] * 91
    closes[0] = 200.0  # 90 bars back  (index -91)
    closes[60] = 137.5  # 30 bars back (index -31)
    closes[83] = 88.0  # 7 bars back   (index -8)
    closes[89] = 100.0  # 1 bar back   (index -2, the prior close)
    closes[90] = 110.0  # now          (index -1)
    m = _by_key(trailing_returns.compute(_bars(closes), _ASOF))
    assert m["ret_1d"].value == 10.0
    assert m["ret_7d"].value == 25.0
    assert m["ret_30d"].value == -20.0
    assert m["ret_90d"].value == -45.0
    # tone drives the FE's green/red — up windows pos, down windows neg
    assert m["ret_1d"].tone == "pos" and m["ret_7d"].tone == "pos"
    assert m["ret_30d"].tone == "neg" and m["ret_90d"].tone == "neg"
    assert all(m[k].unit == "pct" and m[k].note is None for k in _KEYS)


def test_1y_window_is_252_trading_bars_with_a_stable_key():
    # 1Y = 252 trading BARS (needs 253 bars), the same bar-count convention as the shorter windows —
    # NOT a calendar-365d lookup. The key is bar-count-agnostic ("ret_1y") and the label is "1Y".
    closes = [120.0] * 253
    closes[0] = 100.0  # 252 bars back (index -253) — the 1Y base
    closes[-1] = 150.0  # now
    m = _by_key(trailing_returns.compute(_bars(closes), _ASOF))["ret_1y"]
    assert m.value == 50.0  # 150 / 100 - 1
    assert m.label == "1Y" and m.unit == "pct" and m.tone == "pos" and m.note is None
    # one bar short of the year is an honest gap, never a fabricated number (#6/#9)
    short = _by_key(trailing_returns.compute(_bars([120.0] * 252), _ASOF))["ret_1y"]
    assert short.value is None and short.note == "n/a: 252/253 bars"


def test_1d_is_the_prior_close_not_intraday():
    # the shortest window is one trading day: the last close vs the PRIOR close (EOD, never a 24h move)
    m = _by_key(trailing_returns.compute(_bars([100.0, 102.5]), _ASOF))
    assert m["ret_1d"].value == 2.5
    assert m["ret_1d"].tone == "pos"


def test_pure_reads_only_the_bars_handed_to_it_no_lookahead():
    # the reading at an EARLIER asof must equal computing over ONLY the bars up to it — a later bar is
    # never touched. The bitemporal PIT enforces the <=asof trim (tested at the API layer); this pins
    # that the pure math depends on NOTHING past the last bar it is handed (the last bar is always "now").
    full = _bars([10.0, 11.0, 12.0, 13.0, 99.0])  # the 99.0 is the "future" bar
    early = full[:-1]  # what the PIT hands at the earlier asof (the 99.0 bar is invisible)
    assert _by_key(trailing_returns.compute(early, _ASOF))["ret_1d"].value == round(
        (13.0 / 12.0 - 1) * 100, 2
    )  # 13 vs 12, NOT 99
    assert _by_key(trailing_returns.compute(full, _ASOF))["ret_1d"].value == round(
        (99.0 / 13.0 - 1) * 100, 2
    )  # the last handed bar is "now"


def test_thin_history_blanks_only_the_windows_it_cannot_reach():
    # a 40-bar name (common for new/thin OTC): 1d/7d/30d compute; 90d needs 91 bars -> an honest blank,
    # never a fabricated or zero-filled number (#9). The note says exactly how much tape is missing.
    m = _by_key(trailing_returns.compute(_bars([float(i) for i in range(1, 41)]), _ASOF))
    assert m["ret_1d"].value is not None
    assert m["ret_7d"].value is not None
    assert m["ret_30d"].value is not None  # 40 >= 31
    assert m["ret_90d"].value is None  # needs 91 bars
    assert m["ret_90d"].note == "n/a: 40/91 bars"
    assert m["ret_90d"].tone is None
    assert m["ret_1y"].value is None  # needs 253 bars — young names blank the 1Y cell (honest #9)
    assert m["ret_1y"].note == "n/a: 40/253 bars"


def test_a_flat_window_is_a_real_zero_neutral_not_a_gap():
    m = _by_key(trailing_returns.compute(_bars([100.0, 100.0]), _ASOF))
    assert m["ret_1d"].value == 0.0  # AT the prior close is a real 0, never a fake gap
    assert m["ret_1d"].tone is None  # neither green nor red
    assert m["ret_1d"].note is None
    assert m["ret_7d"].value is None and m["ret_7d"].note == "n/a: 2/8 bars"


def test_non_positive_base_is_an_honest_gap_with_its_own_reason():
    m = _by_key(trailing_returns.compute(_bars([0.0, 50.0]), _ASOF))
    assert m["ret_1d"].value is None  # base close 0 -> gap, never a divide-by-zero or fake number
    assert m["ret_1d"].note == "n/a: non-positive base close"  # distinct from a bar-count gap (#6)


def test_null_closes_are_dropped_before_the_window_count():
    # a None close is not a real bar — it's excluded, and the window count reflects the real tape
    m = _by_key(trailing_returns.compute(_bars([None, 100.0, 110.0]), _ASOF))
    assert m["ret_1d"].value == 10.0  # 110 vs 100, the None never counts as a bar
    assert m["ret_7d"].note == "n/a: 2/8 bars"  # 2 real bars, not 3


def test_no_bars_returns_none():
    assert trailing_returns.compute([], _ASOF) is None
    assert trailing_returns.compute(_bars([None, None]), _ASOF) is None


def test_basis_shows_the_work():
    sig = trailing_returns.compute(_bars([10.0, 11.0, 12.0]), _ASOF)
    assert sig.kind == "trailing_returns"
    assert sig.basis.source == "fact_price_eod"
    assert sig.basis.params == {"windows_trading_days": [1, 7, 30, 90, 252], "lookback_days": 420}
    assert sig.basis.bars_used == 3
    assert sig.basis.window_end == _ASOF
