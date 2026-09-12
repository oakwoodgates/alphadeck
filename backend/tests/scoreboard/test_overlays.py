from __future__ import annotations

from datetime import date
from typing import get_args

from app.schemas_api import InsiderSellOut
from scoreboard.overlays import annotate_sma, sell_character_wire
from signals.insider_sell import _FOREIGN, _KEPT, _PLANNED, _SELF, SELL_SCREEN_BUCKETS

# Pure overlay helpers (no DB): the SMA rolling mean + its honest left-edge gap, and the Slice B
# sell-character wire map's drift pin. The transaction-axis cap the drawer's event reads thread
# (``known_at_for_asof`` — min(now, end of the asof MARKET day)) moved to ``domain.market_time`` when the
# serve-path recomputes started sharing it; its tests live in tests/domain/test_market_time.py. The
# DB-backed event paths (no-lookahead event twins, superseded no-double-count, the screens) are
# exercised through the API in tests/app/test_scoreboard_price_window_api.py.


def _bars(closes: list[float]) -> list[dict]:
    return [{"d": date(2026, 1, 1), "close": c} for c in closes]  # only close/order matter here


def test_annotate_sma_rolling_mean_and_honest_left_edge_gap():
    """A known series → the trailing mean matches a hand computation, and is None until enough closes
    precede the bar (the honest gap — never back-padded)."""
    out = annotate_sma(_bars([10, 20, 30, 40, 50]), windows=(3,))
    # sma3: None, None, mean(10,20,30)=20, mean(20,30,40)=30, mean(30,40,50)=40
    assert [b["sma3"] for b in out] == [None, None, 20.0, 30.0, 40.0]


def test_annotate_sma_writes_the_two_default_windows_as_sma50_sma200():
    """The default windows produce exactly the wire's sma50/sma200 keys; both are None until their
    window fills, and sma200 can be absent-valued for a whole short-history series."""
    out = annotate_sma([{"d": date(2026, 1, 1), "close": float(i)} for i in range(120)])
    assert all("sma50" in b and "sma200" in b for b in out)
    # sma50: None for the first 49 bars, then the trailing-50 mean; sma200: None throughout (only 120 bars)
    assert out[48]["sma50"] is None and out[49]["sma50"] is not None
    assert all(b["sma200"] is None for b in out)  # 120 < 200 — the whole line is an honest gap
    # bar index 49 = closes 0..49 → mean = 24.5
    assert out[49]["sma50"] == 24.5


def test_annotate_sma_does_not_mutate_the_input():
    bars = _bars([1.0, 2.0, 3.0])
    annotate_sma(bars, windows=(2,))
    assert all("sma2" not in b for b in bars)  # new dicts returned; input untouched


def test_sell_character_wire_map_covers_every_screen_bucket():
    """The Slice B drift pin: every ``insider_sell._screen`` bucket, pushed through the wire map,
    lands EXACTLY on ``InsiderSellOut.character``'s Literal — so a future new screen bucket (or a
    renamed one) fails HERE, loudly, instead of as a runtime response-validation 500 on the price-
    window endpoint.

    The bucket set is read from ``SELL_SCREEN_BUCKETS`` — the authoritative vocabulary declared BESIDE
    ``_screen``'s constants (not re-listed here), so a 7th bucket added there without a matching wire-map
    entry + ``InsiderSellOut.character`` Literal value drops through ``sell_character_wire``'s identity
    fallback as a token the Literal doesn't contain and BREAKS this equality. (Were the set re-typed
    here, that same new bucket would sail past — which is exactly the gap this rewrite closes.)"""
    wire = {sell_character_wire(b) for b in SELL_SCREEN_BUCKETS}
    literal = set(get_args(InsiderSellOut.model_fields["character"].annotation))
    assert wire == literal
    # the two deliberate renames (cryptic short names -> the contract vocabulary), identity otherwise
    assert sell_character_wire(_SELF) == "self_filing"
    assert sell_character_wire(_FOREIGN) == "foreign_ordinary"
    assert sell_character_wire(_KEPT) == _KEPT and sell_character_wire(_PLANNED) == _PLANNED
