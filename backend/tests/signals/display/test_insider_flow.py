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


# --- PBLS regression: the "open-market" block must agree with the fixed call, not sum raw code-P ---
# Same shape as the call-path test (backend/tests/signals/test_insider_conviction.py): Parabilis (PBLS)
# IPO'd ~2026-06-10 at a $20 offer. On the 6/11 closing, RA Capital (a pre-IPO 10%-owner crossover fund),
# Levy Guy, and Sebulsky each filed code-P "purchases" at the $20 OFFER price — well below the $29.65-34.47
# public tape that day. Code P = "open market OR PRIVATE purchase", so those primary-market subscriptions
# rode straight into the display total: a fake "net buying ~$434M" beside the call's honest ~$473k FLIP.
# The one real signal is Sebulsky's genuine open-market buys on 6/12 & 6/15 at $26-28 (inside the tape),
# ~$473k — which the screen must PRESERVE (recall is sacred, #9).
_PBLS_ASOF = date(2026, 6, 16)
_PBLS_DAY_LOWS = {date(2026, 6, 11): 29.65, date(2026, 6, 12): 26.88, date(2026, 6, 15): 24.51}


def _priced(name: str, shares: float, price: float, d: date, code: str = "P") -> dict:
    return {
        "valid_from": d,
        "txn_code": code,
        "price": price,
        "usd": shares * price,
        "insider_name": name,
    }


def _pbls_rows() -> list[dict]:
    d611 = date(2026, 6, 11)  # the IPO closing — all five $20 offer-price subscriptions land here
    return [
        # the 6/11 IPO subscription @ the $20 offer (below the day's $29.65 low) — primary-market, NOT open
        _priced("RA CAPITAL MANAGEMENT, L.P.", 19_728_353, 20.0, d611),
        _priced("RA CAPITAL MANAGEMENT, L.P.", 1_460_397, 20.0, d611),
        _priced("Levy Guy", 375_000, 20.0, d611),
        _priced("Levy Guy", 125_000, 20.0, d611),
        _priced("SEBULSKY ALAN", 12_500, 20.0, d611),
        # Sebulsky's genuine post-IPO open-market buys @ $26-28 (inside the tape) — the real signal to keep
        _priced("SEBULSKY ALAN", 8_435, 27.6696, date(2026, 6, 12)),
        _priced("SEBULSKY ALAN", 5_000, 25.9978, date(2026, 6, 15)),
        _priced("SEBULSKY ALAN", 4_065, 27.0963, date(2026, 6, 15)),
    ]


def test_pbls_subscription_inflates_flow_without_price_context():
    # WITHOUT day lows (no price context) NOTHING is screened on price — the raw code-P tape, recall-safe:
    # this reproduces the reported ~$434M contradiction and proves the screen is opt-in on price context.
    sig = insider_flow.compute(_pbls_rows(), _PBLS_ASOF)
    m = _by_key(sig)
    assert m["buy_count"].value == 8.0
    assert m["distinct_buyers"].value == 3.0  # RA Capital, Levy Guy, Sebulsky
    assert round(m["buy_usd"].value) == 434_498_529  # matches the call's pre-fix inflated label
    assert sig.headline.label == "net buying $434.5M (90d)"
    assert "screened" not in sig.basis.note  # no price context → nothing set aside


def test_pbls_subscription_screened_by_day_low_matches_the_call():
    # WITH the day lows, every $20 offer-price subscription (below the $29.65 tape) drops out; only
    # Sebulsky's genuine $26-28 post-IPO open-market buys remain — the panel now agrees with the call.
    sig = insider_flow.compute(_pbls_rows(), _PBLS_ASOF, day_lows=_PBLS_DAY_LOWS)
    m = _by_key(sig)
    assert m["buy_count"].value == 3.0  # 8 raw − 5 offer-price subscriptions
    assert m["distinct_buyers"].value == 1.0  # Sebulsky only
    assert round(m["buy_usd"].value) == 473_529  # the honest ~$473k, matching the fixed CallCard
    assert m["net_usd"].value == m["buy_usd"].value  # no sells
    assert sig.headline.key == "net_buying"
    assert sig.headline.label == "net buying $473.5K (90d)"
    # the set-aside subscription is NAMED, never silently dropped (#9 / #6 show-the-work)
    assert "screened 5 off-market code-P buys (~$434M)" in sig.basis.note
    # last_buy is Sebulsky's real 6/15 print, not the 6/11 subscription
    last_buy = {e.key: e for e in sig.events}["last_buy"]
    assert last_buy.date == date(2026, 6, 15)


def test_below_market_buy_kept_when_no_day_low_for_that_date():
    # recall-safe (#9): a suspiciously-cheap buy whose date has NO price bar is KEPT — we cannot prove it
    # was off-market, and a silently-dropped real name is a system failure. Only price CONTEXT can exclude.
    rows = [_priced("Jane Doe", 100_000, 1.0, _ASOF)]
    sig = insider_flow.compute(rows, _ASOF, day_lows={})  # empty lows → no screen
    assert _by_key(sig)["buy_count"].value == 1.0
    assert "screened" not in sig.basis.note
    # ...and WITH a day low that the $1 price sits far below, it drops out and is named
    sig2 = insider_flow.compute(rows, _ASOF, day_lows={_ASOF: 40.0})
    assert _by_key(sig2)["buy_count"].value == 0.0
    assert "screened 1 off-market code-P buy" in sig2.basis.note


def test_absolute_ceiling_excludes_a_physically_impossible_row_without_price_context():
    # CNBX-shape: a $100,000/share price → a $2T row is bad source data, never a personal buy (#3). The
    # absolute ceiling drops it even with NO day low, leaving a real buy beside it untouched.
    rows = [
        _priced("MILLS THOMAS E", 20_000_000, 100_000.0, _ASOF),  # $2T garbage
        _priced("Jane Doe", 10_000, 25.0, _ASOF - timedelta(days=1)),  # a real $250k buy
    ]
    sig = insider_flow.compute(rows, _ASOF)  # no day_lows at all
    m = _by_key(sig)
    assert m["buy_count"].value == 1.0  # only the real buy survives
    assert m["buy_usd"].value == 250_000.0
    assert "screened 1 off-market code-P buy" in sig.basis.note
