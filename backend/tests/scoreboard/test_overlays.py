from __future__ import annotations

from datetime import date, datetime, time, timezone

from scoreboard.overlays import annotate_sma, known_at_for_asof

# Pure overlay helpers (no DB): the SMA rolling mean + its honest left-edge gap, and the insider read's
# transaction-axis cap (min(now, asof-EOD)). The DB-backed insider path (no-lookahead event twin,
# superseded no-double-count, the open-market screen) is exercised through the API in
# tests/app/test_scoreboard_price_window_api.py.


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


def test_known_at_caps_at_asof_end_of_day_for_a_past_view():
    """A scrubbed-back as-of caps the transaction axis at that day's end — a buy disclosed the next day
    is beyond known_at (hidden), the whole no-lookahead-on-disclosure point."""
    asof = date(2026, 6, 10)
    now = datetime(
        2026, 7, 24, 12, 0, tzinfo=timezone.utc
    )  # real 'now' is well after the past asof
    assert known_at_for_asof(asof, now=now) == datetime.combine(asof, time.max, tzinfo=timezone.utc)


def test_known_at_is_now_for_a_live_view():
    """A live/future as-of reads at now (everything disclosed by this moment), never a future EOD."""
    now = datetime(2026, 7, 24, 12, 0, tzinfo=timezone.utc)
    assert (
        known_at_for_asof(date(2026, 8, 1), now=now) == now
    )  # asof-EOD is in the future → now wins
    assert (
        known_at_for_asof(date(2026, 7, 24), now=now) == now
    )  # same day, now is before EOD → now wins
