from __future__ import annotations

from datetime import date, timedelta
from typing import get_args

from app.schemas_api import InsiderBuyOut
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


def test_30d_subwindow_is_a_tighter_slice_of_the_screened_90d_buys():
    # the 30d metrics are the subset of the SAME screened open-market buys within 30 days of asof.
    # Its boundary mirrors the 90d convention (asof-30, asof]: day 29 is in, day 30 is out — exactly
    # as the 90d window includes day 89 and excludes day 90. A buy at day 45 counts in 90d, NOT 30d.
    rows = [
        _txn(_ASOF, name="A"),  # day 0 — in BOTH windows
        _txn(_ASOF - timedelta(days=29), name="B"),  # day 29 — the oldest 30d day (in)
        _txn(_ASOF - timedelta(days=30), name="C"),  # day 30 — OUT of 30d, in 90d
        _txn(_ASOF - timedelta(days=45), name="D"),  # day 45 — in 90d, NOT 30d
        _txn(_ASOF - timedelta(days=89), name="E"),  # day 89 — the oldest 90d day, NOT 30d
    ]
    sig = insider_flow.compute(rows, _ASOF)
    m = _by_key(sig)
    assert m["buy_count"].value == 5.0  # all five fall inside the 90d window
    assert m["distinct_buyers"].value == 5.0
    assert m["buy_count_30d"].value == 2.0  # only A (day 0) + B (day 29)
    assert m["distinct_buyers_30d"].value == 2.0  # A, B
    assert m["buy_count_30d"].unit == "count"
    # the tighter window is shown-the-work in the basis params, beside the 90d one (#6)
    assert sig.basis.params["window_days"] == 90
    assert sig.basis.params["window_days_short"] == 30


def test_30d_distinct_buyers_dedup_and_no_lookahead():
    # a pure no-lookahead assertion at the compute grain: a FUTURE buy (valid_from > asof) is
    # invisible in BOTH windows — the filter is `valid_from <= asof`, never the wall clock (#1). And
    # the 30d distinct-buyer count de-dups a repeat filer exactly as the 90d count does.
    rows = [
        _txn(_ASOF - timedelta(days=2), name="A"),  # two buys, same insider, both in 30d
        _txn(_ASOF - timedelta(days=5), name="A"),
        _txn(_ASOF - timedelta(days=10), name="B"),  # a second insider in 30d
        _txn(_ASOF + timedelta(days=1), name="Z"),  # a FUTURE buy — no lookahead, out of both
    ]
    m = _by_key(insider_flow.compute(rows, _ASOF))
    assert m["buy_count_30d"].value == 3.0  # A, A, B — the future Z is excluded
    assert m["distinct_buyers_30d"].value == 2.0  # A counted once despite two buys; Z invisible
    assert m["buy_count"].value == 3.0  # the 90d window likewise never sees the future buy
    assert m["distinct_buyers"].value == 2.0


def test_30d_subwindow_rides_the_price_screen_not_a_re_screen():
    # the 30d slice is taken from the ALREADY-screened open-market `buys`, so an offer-price
    # subscription dropped from the 90d total is absent from the 30d total too (no double-screen, no
    # leak): a $1 buy far below the day's $40 low is set aside, and neither window counts it.
    rows = [
        _priced("Jane Doe", 100_000, 1.0, _ASOF - timedelta(days=3)),  # off-market, screened out
        _priced("Ray Real", 10_000, 25.0, _ASOF - timedelta(days=4)),  # genuine open-market buy
    ]
    m = _by_key(insider_flow.compute(rows, _ASOF, day_lows={_ASOF - timedelta(days=3): 40.0}))
    assert m["buy_count"].value == 1.0  # only the genuine buy survives the screen
    assert m["buy_count_30d"].value == 1.0  # the 30d slice inherits the screen, never re-adds it
    assert m["distinct_buyers_30d"].value == 1.0


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


# --- S2c: the buy-CHARACTER screen (`_screen`) — the display-side attribution behind the Scoreboard's
# per-buy chips / event-ledger labels. Pure field predicates only (#3); ordered most-structural first
# (implausible → self → below-low, mirroring `insider_sell._screen`) so a multi-screen row has ONE
# deterministic attribution. Real cited shapes where they exist: KYOCERA/Roivant (self-filing CIK
# equality — the call-path suite's own fixtures), PBLS accessions 0001231919-26-000638 /
# 0001213900-26-072928 / 0001193125-26-271324 (the $20-offer below-low subscriptions), CNBX (the $2T
# implausible row). ---


def test_buy_character_set_covers_the_wire_literal():
    """Drift pin (the buy mirror of ``test_overlays``'s sell pin): the full set of characters
    ``_screen`` can return is EXACTLY ``InsiderBuyOut.character``'s Literal. The buy side ships its
    screen values on the wire VERBATIM (no translation map), so a new character added to
    ``BUY_SCREEN_CHARACTERS`` without a matching Literal value — or an orphan Literal value with no
    character — fails HERE, loudly, instead of as a runtime response-validation 500 on the price-window
    endpoint. ``BUY_SCREEN_CHARACTERS`` is the authoritative vocabulary declared BESIDE ``_screen``'s
    constants (not re-listed here), so adding a character there is what this pin keys on."""
    literal = set(get_args(InsiderBuyOut.model_fields["character"].annotation))
    assert insider_flow.BUY_SCREEN_CHARACTERS == literal


def test_screen_open_market_is_the_default_character():
    t = _priced("Ray Real", 10_000, 25.0, _ASOF)
    assert insider_flow._screen(t, {_ASOF: 24.0}, None) == insider_flow.OPEN_MARKET


def test_screen_primary_market_on_a_below_low_offer_price_buy():
    # the PBLS shape: RA Capital's $20 IPO subscription vs the day's 29.65 low → primary-market
    t = _priced("RA CAPITAL MANAGEMENT, L.P.", 19_728_353, 20.0, date(2026, 6, 11))
    assert insider_flow._screen(t, _PBLS_DAY_LOWS, None) == insider_flow.PRIMARY_MARKET


def test_screen_implausible_needs_no_price_context():
    # the CNBX shape: a $2T "purchase" is bad source data even with NO day low (#3)
    t = _priced("MILLS THOMAS E", 20_000_000, 100_000.0, _ASOF)
    assert insider_flow._screen(t, {}, None) == insider_flow.IMPLAUSIBLE


def test_screen_self_filing_by_cik_equality_and_zero_padding():
    # KYOCERA-on-KYOCERA / Roivant-on-Roivant: rpt_owner_cik == issuer_cik is the canonical match,
    # zero-padding normalized — priced AT the market, so only identity (never price) catches it
    t = _priced("Roivant Sciences Ltd.", 16_666_666, 21.0, _ASOF)
    t["rpt_owner_cik"], t["issuer_cik"] = "1479290", "0001479290"
    assert insider_flow._screen(t, {}, None) == insider_flow.SELF_FILING


def test_screen_self_filing_by_name_fallback_row_and_param():
    # the pre-CIK-capture rows: the row's own issuer_name, else the passed security-master name
    via_row = _priced("Roivant Sciences Ltd.", 100_000, 21.0, _ASOF)
    via_row["issuer_name"] = "Roivant Sciences Ltd."
    assert insider_flow._screen(via_row, {}, None) == insider_flow.SELF_FILING
    via_param = _priced("KYOCERA CORP", 100_000, 21.75, _ASOF)  # casefolded match
    assert insider_flow._screen(via_param, {}, "Kyocera Corp") == insider_flow.SELF_FILING


def test_screen_missing_identity_is_never_self():
    # recall-safe (#9): no CIKs, no issuer_name anywhere → the identity screen KEEPS the row
    t = _priced("Paulson John", 12_500_000, 25.0, _ASOF)
    assert insider_flow._screen(t, {}, None) == insider_flow.OPEN_MARKET
    # ...and a filer that merely differs from the issuer name stays open-market (BHC/Paulson shape)
    assert insider_flow._screen(t, {}, "Bausch Health Companies Inc.") == insider_flow.OPEN_MARKET


def test_screen_no_day_low_stays_open_market_never_proven_discretionary():
    # `open_market` means "passed the AVAILABLE screens": a suspiciously-cheap buy with no price bar
    # cannot be attributed primary-market (#9 — absent context only disables that one screen)
    t = _priced("Jane Doe", 100_000, 1.0, _ASOF)
    assert insider_flow._screen(t, {}, None) == insider_flow.OPEN_MARKET
    assert insider_flow._screen(t, {_ASOF: 40.0}, None) == insider_flow.PRIMARY_MARKET


def test_screen_never_reads_the_plan_flag_tri_state():
    # the 10b5-1 flag rides BESIDE the character (rendered only on explicit True by the FE); the
    # character itself is identical across the tri-state — a planned buy is still an open-market buy
    for flag in (True, False, None):
        t = _priced("Jane Doe", 10_000, 25.0, _ASOF)
        t["aff_10b5_1"] = flag
        assert insider_flow._screen(t, {}, None) == insider_flow.OPEN_MARKET


def test_screen_precedence_is_deterministic_most_structural_first():
    # self + planned → SELF_FILING, and the plan flag is NOT consumed (still on the row for the wire)
    both = _priced("Devco Inc", 10_000, 25.0, _ASOF)
    both["rpt_owner_cik"] = both["issuer_cik"] = "111"
    both["aff_10b5_1"] = True
    assert insider_flow._screen(both, {}, None) == insider_flow.SELF_FILING
    assert both["aff_10b5_1"] is True
    # implausible + self → IMPLAUSIBLE (bad data outranks identity)
    garbage = _priced("Devco Inc", 30_000_000, 100_000.0, _ASOF)
    garbage["rpt_owner_cik"] = garbage["issuer_cik"] = "111"
    assert insider_flow._screen(garbage, {}, None) == insider_flow.IMPLAUSIBLE
    # self + below-low → SELF_FILING (identity is the more explanatory label; the plan's order)
    cheap_self = _priced("Devco Inc", 10_000, 1.0, _ASOF)
    cheap_self["rpt_owner_cik"] = cheap_self["issuer_cik"] = "111"
    assert insider_flow._screen(cheap_self, {_ASOF: 40.0}, None) == insider_flow.SELF_FILING


def test_screen_never_mutates_the_input_row():
    # #9: classification is a READ — the fact row (and its dict image) is never altered
    t = _priced("Jane Doe", 10_000, 25.0, _ASOF)
    t["rpt_owner_cik"], t["issuer_cik"], t["aff_10b5_1"] = "1", "2", True
    snapshot = dict(t)
    insider_flow._screen(t, {_ASOF: 24.0}, "Some Issuer")
    assert t == snapshot


def test_is_open_market_buy_is_the_same_predicates_minus_identity():
    # the panel's net-flow screen composes the SAME price/data predicates `_screen` orders, but is
    # DELIBERATELY identity-blind (operator decision 3: the net-flow re-base is deferred — a
    # self-filing still counts in the 90d figure). Matrix:
    lows = {_ASOF: 40.0}
    plain = _priced("Ray Real", 1_000, 41.0, _ASOF)
    below = _priced("Jane Doe", 1_000, 20.0, _ASOF)
    garbage = _priced("MILLS THOMAS E", 30_000_000, 100_000.0, _ASOF)
    self_at_market = _priced("Devco Inc", 1_000, 41.0, _ASOF)
    self_at_market["rpt_owner_cik"] = self_at_market["issuer_cik"] = "111"
    self_below = _priced("Devco Inc", 1_000, 20.0, _ASOF)
    self_below["rpt_owner_cik"] = self_below["issuer_cik"] = "111"

    assert insider_flow._is_open_market_buy(plain, lows) is True
    assert insider_flow._is_open_market_buy(below, lows) is False
    assert insider_flow._is_open_market_buy(garbage, lows) is False
    # identity-blind: the self-filing COUNTS in the panel figure (labeled, not re-based — deferred)
    assert insider_flow._is_open_market_buy(self_at_market, lows) is True
    # the one corner where character and net-flow differ: self + below-low reads SELF_FILING as its
    # character but stays OUT of the net-flow (below-low) — the same deferred tape-vs-call seam
    assert insider_flow._is_open_market_buy(self_below, lows) is False
    assert insider_flow._screen(self_below, lows, None) == insider_flow.SELF_FILING
