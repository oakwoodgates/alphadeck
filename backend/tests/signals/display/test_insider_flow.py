from __future__ import annotations

from datetime import date, timedelta

from signals.display import insider_flow

_ASOF = date(2026, 7, 1)


def _txn(d: date, code: str = "P", usd: float | None = None, name: str = "A") -> dict:
    return {"valid_from": d, "txn_code": code, "usd": usd, "insider_name": name}


def _by_key(sig) -> dict:
    return {m.key: m for m in sig.metrics}


def test_net_math_window_boundaries_and_code_filter():
    rows = [
        _txn(_ASOF, usd=1_000_000.0, name="A"),  # buy, at the asof edge (included)
        _txn(_ASOF - timedelta(days=89), usd=500_000.0, name="B"),  # oldest included day
        _txn(_ASOF - timedelta(days=1), code="S", usd=400_000.0, name="C"),  # sell
        _txn(_ASOF - timedelta(days=90), usd=99_000_000.0, name="D"),  # 91st day back: OUT
        _txn(_ASOF, code="A", usd=2_000_000.0, name="E"),  # an award is not open-market flow
        _txn(_ASOF, usd=None, name="F"),  # a buy with no $ value still counts
    ]
    sig = insider_flow.compute(rows, _ASOF)
    m = _by_key(sig)
    assert m["buy_count"].value == 3.0
    assert m["sell_count"].value == 1.0
    assert m["distinct_buyers"].value == 3.0  # A, B, F
    assert m["buy_usd"].value == 1_500_000.0
    assert m["buy_usd"].note == "1 txns without $ value"  # the unpriced buy is SAID, not hidden
    assert m["sell_usd"].value == 400_000.0
    assert m["net_usd"].value == 1_100_000.0
    flips = {e.key: e for e in sig.events}
    assert flips["last_buy"].date == _ASOF and flips["last_buy"].direction == "up"
    assert flips["last_sell"].date == _ASOF - timedelta(days=1)
    assert "zero ingested" in sig.basis.note  # the epistemics ride every payload
    assert sig.basis.window_start == _ASOF - timedelta(days=89)
    assert sig.basis.window_end == _ASOF
    # the at-a-glance flow state: net +1.1M -> buying, magnitude compact, counts in the detail
    assert sig.headline.key == "net_buying"
    assert sig.headline.glyph == "up"
    assert sig.headline.label == "net buying $1.1M (90d)"
    assert sig.headline.detail == "3 buys · 1 sell · 1 unpriced"


def test_net_selling_headline():
    rows = [
        _txn(_ASOF, usd=500_000.0, name="A"),
        _txn(_ASOF - timedelta(days=3), code="S", usd=3_900_000.0, name="B"),
    ]
    sig = insider_flow.compute(rows, _ASOF)
    assert sig.headline.key == "net_selling"
    assert sig.headline.glyph == "down"
    assert sig.headline.label == "net selling $3.4M (90d)"  # the word carries the sign
    assert sig.headline.detail == "1 buy · 1 sell"


def test_rows_outside_the_window_read_as_a_quiet_zero_not_absence():
    rows = [_txn(_ASOF - timedelta(days=200), usd=1_000_000.0)]
    sig = insider_flow.compute(rows, _ASOF)  # the name IS ingested — zero activity is information
    m = _by_key(sig)
    assert m["buy_count"].value == 0.0
    assert m["net_usd"].value == 0.0
    assert sig.events == []
    # ...but the top strip stays quiet: no flow line on a no-flow name (honest loudness — the
    # headline marks the exception; the section's zeros carry the quiet-is-information read)
    assert sig.headline is None


def test_nothing_ingested_returns_none():
    assert insider_flow.compute([], _ASOF) is None
