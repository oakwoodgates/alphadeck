from __future__ import annotations

from datetime import date
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role
from signals import insider_conviction

ASOF = date(2026, 6, 4)
SID = uuid4()


def _buy(name, role, usd, d=date(2026, 5, 20), code="P"):
    return {
        "txn_code": code,
        "usd": usd,
        "insider_name": name,
        "insider_role": role,
        "valid_from": d,
        "accession": f"acc-{name}",
    }


def test_core_when_two_senior_insiders_buy_big():
    txns = [
        _buy("Jane Doe", "Chief Executive Officer", 150_000),
        _buy("John Roe", "Chief Financial Officer", 120_000),
    ]
    ev = insider_conviction.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.role is Role.ENTRY_TRIGGER and ev.kind is Kind.INSIDER
    assert ev.grade is Grade.CORE
    # a CORE cluster carries the multi-month conviction horizon (the hold clock), not the flip window
    assert ev.alpha_liveness_days == DEFAULT_CONFIG.insider_core_alpha_liveness_days
    assert len(ev.provenance) == 2  # one per accession


def test_provenance_accessions_are_sorted_independent_of_row_order():
    alpha = _buy("Alpha Buyer", "Chief Executive Officer", 150_000)
    zulu = _buy("Zulu Buyer", "Chief Financial Officer", 120_000)

    forward = insider_conviction.score([zulu, alpha], SID, ASOF, DEFAULT_CONFIG)
    reverse = insider_conviction.score([alpha, zulu], SID, ASOF, DEFAULT_CONFIG)

    assert forward is not None and reverse is not None
    assert forward.model_dump() == reverse.model_dump()
    assert [p.ref for p in forward.provenance] == ["acc-Alpha Buyer", "acc-Zulu Buyer"]


def test_flip_when_single_insider():
    ev = insider_conviction.score([_buy("Jane Doe", "Chief Executive Officer", 50_000)], SID, ASOF)
    assert ev is not None and ev.grade is Grade.FLIP  # one insider, below the strong-single floor
    # a flip buy is short-horizon (fast/sentiment), not the multi-month core hold window
    assert ev.alpha_liveness_days == DEFAULT_CONFIG.insider_flip_alpha_liveness_days


def test_core_on_strong_single_senior_buy():
    # STARTING calibration (HIMS): one senior insider buying above the high floor warms as CORE
    ev = insider_conviction.score([_buy("David Wells", "Director", 1_200_000)], SID, ASOF)
    assert ev is not None and ev.grade is Grade.CORE


def test_not_fired_on_sales_only():
    assert insider_conviction.score([_buy("Jane Doe", "CEO", 500_000, code="S")], SID, ASOF) is None


def test_not_fired_below_min_usd():
    assert insider_conviction.score([_buy("Jane Doe", "CEO", 5_000)], SID, ASOF) is None


def test_drops_a_cluster_past_its_alpha_horizon():
    # a single small senior buy is FLIP (short horizon); 154d old -> decayed out of the live stream
    old = [_buy("Jane Doe", "CEO", 200_000, d=date(2026, 1, 1))]
    assert insider_conviction.score(old, SID, ASOF) is None  # ASOF = 2026-06-04


def test_core_cluster_stays_live_for_months():
    # a CORE cluster carries a multi-month horizon, so it is still re-derived ~100d after the buys
    # (the UNH case: conviction in May, breakout confirms in August) — a flip window would have dropped it
    txns = [
        _buy("Jane Doe", "Chief Executive Officer", 200_000, d=date(2026, 2, 24)),
        _buy("John Roe", "Chief Financial Officer", 150_000, d=date(2026, 2, 24)),
    ]
    ev = insider_conviction.score(
        txns, SID, ASOF, DEFAULT_CONFIG
    )  # ASOF = 2026-06-04 (~100d later)
    assert ev is not None and ev.grade is Grade.CORE
    assert ev.asof == date(2026, 2, 24)  # dated at the cluster's fire, not the query asof
    assert ev.alpha_liveness_days == DEFAULT_CONFIG.insider_core_alpha_liveness_days


def test_event_dated_at_latest_buy_not_query_asof():
    # the cluster's fire date is the most recent buy, not the query asof (ASOF = 2026-06-04)
    txns = [
        _buy("Jane Doe", "Chief Executive Officer", 120_000, d=date(2026, 5, 18)),
        _buy("John Roe", "Chief Financial Officer", 120_000, d=date(2026, 5, 22)),
    ]
    ev = insider_conviction.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None
    assert ev.asof == date(2026, 5, 22)


# --- PBLS regression (#3-adjacent): an IPO subscription must not inflate open-market conviction ---
# Parabilis Medicines (PBLS) IPO'd ~2026-06-10 at a $20 offer. On the 6/11 closing, RA Capital (a pre-IPO
# 10%-owner crossover fund), Levy Guy, and Sebulsky each filed code-P "purchases" at exactly $20 — the
# OFFER price, well below the $29.65-$34.47 public tape that day. Code P = "open market OR PRIVATE
# purchase", so these primary-market subscriptions rode straight into the Key-1 total: a fake
# "3 insiders incl. senior officer bought $434,498,529 open-market (code P) across 8 txns" CORE. The one
# real signal is director Sebulsky's genuine open-market buys on 6/12 & 6/15 at $26-28 (inside the tape),
# ~$473k — which the fix must PRESERVE (recall is sacred, #9). Shape taken from the actual filings
# (accessions 0001231919-26-000638, 0001213900-26-072928, 0001193125-26-271324 / -273013).

_PBLS_ASOF = date(2026, 6, 16)
_PBLS_DAY_LOWS = {date(2026, 6, 11): 29.65, date(2026, 6, 12): 26.88, date(2026, 6, 15): 24.51}


def _priced_buy(name, role, shares, price, d, code="P"):
    return {
        "txn_code": code,
        "shares": shares,
        "price": price,
        "usd": shares * price,
        "insider_name": name,
        "insider_role": role,
        "valid_from": d,
        "accession": f"acc-{name}-{d.isoformat()}",
    }


def _pbls_txns():
    d611 = date(2026, 6, 11)  # the IPO closing — all five $20 offer-price subscriptions land here
    return [
        # the 6/11 IPO subscription @ the $20 offer (below the day's $29.65 low) — primary-market, NOT open
        _priced_buy("RA CAPITAL MANAGEMENT, L.P.", "Director, 10% owner", 19_728_353, 20.0, d611),
        _priced_buy("RA CAPITAL MANAGEMENT, L.P.", "Director, 10% owner", 1_460_397, 20.0, d611),
        _priced_buy("Levy Guy", "10% owner", 375_000, 20.0, d611),
        _priced_buy("Levy Guy", "10% owner", 125_000, 20.0, d611),
        _priced_buy("SEBULSKY ALAN", "Director", 12_500, 20.0, d611),
        # Sebulsky's genuine post-IPO open-market buys @ $26-28 (inside the tape) — the real signal to keep
        _priced_buy("SEBULSKY ALAN", "Director", 8_435, 27.6696, date(2026, 6, 12)),
        _priced_buy("SEBULSKY ALAN", "Director", 5_000, 25.9978, date(2026, 6, 15)),
        _priced_buy("SEBULSKY ALAN", "Director", 4_065, 27.0963, date(2026, 6, 15)),
    ]


def test_pbls_ipo_subscription_inflates_the_call_without_price_context():
    # WITHOUT the day-low cross-check (the reported bug) the $434M IPO subscription arms a fake CORE — this
    # asserts the exact string the operator saw, so the fix below is measured against the real defect.
    ev = insider_conviction.score(_pbls_txns(), SID, _PBLS_ASOF, DEFAULT_CONFIG)
    assert ev is not None and ev.grade is Grade.CORE
    assert ev.label == (
        "3 insiders incl. senior officer bought $434,498,529 open-market (code P) across 8 txns"
    )


def test_pbls_ipo_subscription_excluded_by_day_low_cross_check():
    # WITH the day lows, every $20 offer-price subscription (below the $29.65 tape) drops out of the total;
    # only Sebulsky's genuine $26-28 post-IPO open-market buys remain -> an honest single-insider FLIP.
    ev = insider_conviction.score(
        _pbls_txns(), SID, _PBLS_ASOF, DEFAULT_CONFIG, day_lows=_PBLS_DAY_LOWS
    )
    assert ev is not None
    assert ev.grade is Grade.FLIP  # 1 insider, ~$473k < the $500k strong-single CORE floor
    assert ev.label == (
        "1 insider incl. senior officer bought $473,529 open-market (code P) across 3 txns"
    )
    # the excluded RA Capital / Levy subscriptions are gone from the provenance too (Sebulsky-only)
    refs = {p.ref for p in ev.provenance}
    assert refs and all("SEBULSKY" in r for r in refs)


def test_absolute_ceiling_excludes_a_physically_impossible_row():
    # CNBX-shape: a $100,000/share price -> a $2 TRILLION row is bad source data, never a personal buy (#3).
    # The absolute ceiling drops it even with NO price context (no day low needed) -> the only buy is gone.
    txns = [_priced_buy("MILLS THOMAS E", "10% owner", 20_000_000, 100_000.0, date(2026, 5, 20))]
    assert insider_conviction.score(txns, SID, ASOF, DEFAULT_CONFIG) is None


def test_ceiling_drops_the_garbage_row_but_keeps_a_real_buy_beside_it():
    txns = [
        _priced_buy(
            "MILLS THOMAS E", "10% owner", 20_000_000, 100_000.0, date(2026, 5, 20)
        ),  # $2T garbage
        _buy(
            "Jane Doe", "Chief Executive Officer", 200_000, d=date(2026, 5, 21)
        ),  # a real $200k senior buy
    ]
    ev = insider_conviction.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None
    assert (
        ev.label
        == "1 insider incl. senior officer bought $200,000 open-market (code P) across 1 txns"
    )


def test_below_market_buy_kept_when_no_price_context_recall_safe():
    # a suspiciously-low-priced buy with NO day low available is KEPT — we cannot prove it was off-market,
    # and a silently-dropped real name is a system failure (#9). Only price CONTEXT can exclude it.
    txns = [_priced_buy("Jane Doe", "Chief Executive Officer", 100_000, 1.0, date(2026, 5, 20))]
    assert insider_conviction.score(txns, SID, ASOF, DEFAULT_CONFIG, day_lows={}) is not None


class _FakePit:
    """A minimal SignalPointInTimeData stand-in: hands back canned insider txns + price bars so the
    detect() wiring (price_history -> day_lows -> score) can be exercised without a DB."""

    def __init__(self, txns, bars, asof):
        self._txns, self._bars, self.asof = txns, bars, asof

    def insider_txns(self, security_id):
        return self._txns

    def price_history(self, security_id, lookback_days=None):
        return self._bars


def test_detect_builds_day_lows_from_price_history_and_filters():
    # end-to-end through detect(): the price bars feed the day-low map, so PBLS de-inflates to the FLIP.
    bars = [
        {"d": d, "low": low, "high": low + 6.0, "close": low + 2.0}
        for d, low in _PBLS_DAY_LOWS.items()
    ]
    ev = insider_conviction.detect(
        _FakePit(_pbls_txns(), bars, _PBLS_ASOF), SID, _PBLS_ASOF, DEFAULT_CONFIG
    )
    assert ev is not None and ev.grade is Grade.FLIP
    assert ev.label == (
        "1 insider incl. senior officer bought $473,529 open-market (code P) across 3 txns"
    )
