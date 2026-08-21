from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import ValidationError

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from ingest.edgar.form4 import parse_form4
from signals import insider_conviction
from signals.insider_conviction import _is_foreign_ordinary

ASOF = date(2026, 6, 4)
SID = uuid4()

_TSM_MIXED = (
    Path(__file__).resolve().parent.parent / "fixtures" / "edgar" / "form4_tsm_mixed.xml"
).read_text(encoding="utf-8")


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

    def security_name(self, security_id):
        # this fake exercises the PRICE screen (PBLS day-lows); no issuer-self identity here -> None
        return None


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


# --- issuer-self screen (§3): the ISSUER filing a Form 4 on ITSELF is a buyback/treasury/ADR mechanic,
# never personal insider conviction — it does NOT feed Key-1, even though it prices AT the market. The
# excluded row STAYS in the txn stream (the display tape); only the conviction total skips it. Recall-safe:
# only a self-filing has filer==issuer, and a missing CIK / name mismatch just KEEPS the row (#9). ---


def _self_buy(name, *, issuer_name=None, owner_cik=None, issuer_cik=None, usd=690_000_000):
    # a large AT-MARKET code-P block (the class the price screen does NOT catch — KYOCERA-on-KYOCERA)
    t = _buy(name, "10% owner", usd)
    if issuer_name is not None:
        t["issuer_name"] = issuer_name
    if owner_cik is not None:
        t["rpt_owner_cik"] = owner_cik
    if issuer_cik is not None:
        t["issuer_cik"] = issuer_cik
    return t


def test_excludes_issuer_self_by_cik():
    # rpt_owner_cik == issuer_cik → the company filed on itself (canonical match; no name needed)
    txn = _self_buy("KYOCERA CORP", owner_cik="0000054321", issuer_cik="0000054321")
    assert insider_conviction.score([txn], SID, ASOF) is None


def test_excludes_issuer_self_by_cik_ignoring_zero_padding():
    # the CIKs compare equal despite different zero-padding (normalized both sides)
    txn = _self_buy("Roivant Sciences Ltd.", owner_cik="1479290", issuer_cik="0001479290")
    assert insider_conviction.score([txn], SID, ASOF) is None


def test_excludes_issuer_self_by_name_via_row_issuer_name():
    # no CIKs on the row (a pre-capture row), but the filer name == the row's captured issuer name
    txn = _self_buy("Roivant Sciences Ltd.", issuer_name="Roivant Sciences Ltd.")
    assert insider_conviction.score([txn], SID, ASOF) is None


def test_excludes_issuer_self_by_name_via_issuer_name_param():
    # the already-ingested path: no CIKs, no row issuer_name — the security-master name is passed in
    txn = _self_buy("KYOCERA CORP")  # no identity fields on the row at all
    assert (
        insider_conviction.score([txn], SID, ASOF, issuer_name="Kyocera Corp") is None
    )  # casefold


def test_keeps_genuine_activist_buy_not_self():
    # BHC/Paulson: a large at-market director buy whose filer != issuer — a REAL signal, must NOT be screened
    txn = _self_buy("Paulson John", issuer_name="Bausch Health Companies Inc.", usd=312_500_000)
    ev = insider_conviction.score([txn], SID, ASOF, issuer_name="Bausch Health Companies Inc.")
    assert ev is not None and ev.fired  # kept (recall-safe): the screen isolates self-filings only


def test_recall_safe_when_no_identity_present():
    # a plain buy with no CIKs, no issuer_name, no issuer_name param → kept (the screen never over-excludes)
    assert insider_conviction.score([_buy("Jane Doe", "CEO", 700_000)], SID, ASOF) is not None


def test_self_filing_does_not_drop_the_real_buys_beside_it():
    # a mixed stream: the issuer's self-buy is screened out, the real senior cluster still fires on its own
    txns = [
        _self_buy("Devco Inc", owner_cik="111", issuer_cik="111", usd=500_000_000),
        _buy("Jane Doe", "Chief Executive Officer", 150_000),
        _buy("John Roe", "Chief Financial Officer", 120_000),
    ]
    ev = insider_conviction.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None and ev.grade is Grade.CORE
    # the $500M self-block is NOT in the total (2 real insiders, $270k), and not in provenance
    assert "270,000" in ev.label and len(ev.provenance) == 2


# --- S2c: the DORMANT 10b5-1 planned-buy weight (`insider_10b5_1_buy_weight`, default 1.0 = today) ---
# The load-bearing guarantee is BEHAVIOR PRESERVATION: at the default every golden above passes
# byte-unchanged, and the flag-vs-no-flag golden below pins the full SignalEvent identical. The
# non-default tests prove the seam works so a future flip is a config decision, not a code change.
# NB every `aff_10b5_1=True` BUY here is a CONSTRUCTED row, clearly so: the population query for a
# real planned buy on dev data is owed on the dev stack (see the S2c PR) — never fabricated as "real".


def _planned(name, role, usd, d=date(2026, 5, 20)):
    t = _buy(name, role, usd, d=d)
    t["aff_10b5_1"] = True
    return t


def test_planned_flag_changes_nothing_at_the_default_weight():
    # the byte-stability golden: the SAME cluster with and without the plan flag produces an
    # IDENTICAL full SignalEvent under DEFAULT_CONFIG (weight 1.0) — label, grade, score, asof,
    # liveness, provenance, everything (model_dump covers every field).
    plain = [
        _buy("Jane Doe", "Chief Executive Officer", 150_000),
        _buy("John Roe", "Chief Financial Officer", 120_000),
    ]
    flagged = [
        _planned("Jane Doe", "Chief Executive Officer", 150_000),
        _planned("John Roe", "Chief Financial Officer", 120_000),
    ]
    a = insider_conviction.score(plain, SID, ASOF, DEFAULT_CONFIG)
    b = insider_conviction.score(flagged, SID, ASOF, DEFAULT_CONFIG)
    assert a is not None and b is not None
    assert a.model_dump() == b.model_dump()


def test_weight_zero_fully_screens_planned_buys_and_moves_the_anchor():
    # w == 0.0 is a FULL screen: the planned buy leaves the survivor set before the anchor is chosen,
    # so the cluster re-anchors on the unplanned buy (the event's asof/fire date moves) and the
    # planned accession leaves the provenance entirely. ($600k CEO buy = strong-single CORE, so the
    # re-anchored cluster is still live at ASOF under the 180d core window.)
    cfg = DEFAULT_CONFIG.model_copy(update={"insider_10b5_1_buy_weight": 0.0})
    txns = [
        _buy("Jane Doe", "Chief Executive Officer", 600_000, d=date(2026, 5, 10)),
        _planned("Plan Buyer", "Director", 300_000, d=date(2026, 5, 20)),  # the latest buy
    ]
    ev = insider_conviction.score(txns, SID, ASOF, cfg)
    assert ev is not None
    assert ev.asof == date(2026, 5, 10)  # the anchor MOVED off the screened planned buy
    assert "1 insider" in ev.label and "$600,000" in ev.label
    assert [p.ref for p in ev.provenance] == ["acc-Jane Doe"]
    # ...and at the 1.0 default the same stream anchors on the planned buy (today's behavior)
    ev_default = insider_conviction.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev_default is not None and ev_default.asof == date(2026, 5, 20)
    assert "$900,000" in ev_default.label


def test_weight_half_scales_planned_dollars_but_keeps_presence():
    # 0 < w < 1: the planned buy's $ HALVES but the buy stays PRESENT — it still counts as a distinct
    # insider, still carries seniority, and its accession stays in the provenance.
    cfg = DEFAULT_CONFIG.model_copy(update={"insider_10b5_1_buy_weight": 0.5})
    txns = [
        _buy("Jane Doe", "Chief Executive Officer", 100_000),
        _planned("Plan Buyer", "Director", 100_000),
    ]
    ev = insider_conviction.score(txns, SID, ASOF, cfg)
    assert ev is not None
    assert "2 insiders" in ev.label  # presence retained (distinct count)
    assert "$150,000" in ev.label  # 100k + 0.5 × 100k
    assert ev.grade is Grade.CORE  # 2 distinct seniors, weighted total still ≥ the 100k core floor
    assert len(ev.provenance) == 2  # the planned accession still shows its work (#6)


def test_weight_below_one_demotes_a_core_that_stood_on_planned_dollars():
    # a cluster CORE only because of the planned buy's $ demotes to FLIP when the weighted total
    # crosses under `insider_core_min_usd` (60k + 0.5 × 70k = 95k < 100k) — the seam reaches grade.
    txns = [
        _buy("Jane Doe", "Chief Executive Officer", 60_000),
        _planned("Plan Buyer", "Director", 70_000),
    ]
    at_default = insider_conviction.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert at_default is not None and at_default.grade is Grade.CORE  # 130k ≥ 100k
    cfg = DEFAULT_CONFIG.model_copy(update={"insider_10b5_1_buy_weight": 0.5})
    halved = insider_conviction.score(txns, SID, ASOF, cfg)
    assert halved is not None and halved.grade is Grade.FLIP  # 95k < 100k, still ≥ the 10k floor
    assert "$95,000" in halved.label


def test_weight_tri_state_none_and_false_are_never_weighted():
    # #9: only an explicit True weighs — a pre-Dec-2022 `None` (unknown) or an explicit False buy
    # keeps its full $ even at weight 0 (unknown is never asserted "planned").
    cfg = DEFAULT_CONFIG.model_copy(update={"insider_10b5_1_buy_weight": 0.0})
    unknown = _buy("Jane Doe", "Chief Executive Officer", 200_000)  # aff_10b5_1 absent (None)
    explicit_false = _buy("John Roe", "Chief Financial Officer", 150_000)
    explicit_false["aff_10b5_1"] = False
    ev = insider_conviction.score([unknown, explicit_false], SID, ASOF, cfg)
    assert ev is not None
    assert "$350,000" in ev.label  # both fully counted


# --- the ANCHOR WALK regression: a late small buy must not SHADOW an older still-live CORE ---------
# Measured before the fix (score() anchored unconditionally on the most-recent kept buy, with no
# fallback to an earlier anchor): a $600k senior Director buy alone fires CORE 0.9 at asof 2026-06-01
# (the 180d core window runs to ~Jul 9). Add ONE $15k non-senior employee buy on 2026-04-01 and the
# $600k falls outside the new anchor's 30d window, so 2026-04-10 read "FLIP 0.5075 on $15,000" and
# 2026-06-01 read None — the flip's 18d liveness expired and the still-live CORE was simply gone.
# More insider buying read LESS bullish, contradicting score()'s own "the lookback never drops a
# still-live conviction". The walk evaluates EVERY distinct buy date as a candidate anchor and picks
# prefer-CORE-then-most-recent (catalyst_conviction's selection precedent).

_CORE_BUY = _buy("David Wells", "Director", 600_000, d=date(2026, 1, 10))
_LATE_SMALL_BUY = _buy("Temp Staffer", "Employee", 15_000, d=date(2026, 4, 1))


def test_late_small_buy_does_not_shadow_a_still_live_core():
    """The measured scenario. With the late $15k non-senior buy present, the January CORE still
    fires — same grade, same anchor, same total as when it stands alone."""
    alone = insider_conviction.score([_CORE_BUY], SID, date(2026, 6, 1), DEFAULT_CONFIG)
    assert alone is not None and alone.grade is Grade.CORE and alone.asof == date(2026, 1, 10)

    ev = insider_conviction.score(
        [_CORE_BUY, _LATE_SMALL_BUY], SID, date(2026, 6, 1), DEFAULT_CONFIG
    )
    assert ev is not None  # was None before the walk — the whole conviction vanished
    assert ev.grade is Grade.CORE
    assert ev.asof == date(2026, 1, 10)  # the CHOSEN cluster's anchor, not the late buy's date
    assert "$600,000" in ev.label  # the $15k is outside this cluster's window, not fused in
    assert [p.ref for p in ev.provenance] == [_CORE_BUY["accession"]]
    assert ev.model_dump() == alone.model_dump()  # the extra buy changes nothing about the call


def test_core_outranks_a_fresher_flip_while_both_are_live():
    """Same stream, read while the late flip cluster is ALSO live (asof 2026-04-10, 9d after it):
    the selection prefers CORE over recency, so the January conviction still headlines. Before the
    walk this read 'FLIP 0.5075 on $15,000'."""
    ev = insider_conviction.score(
        [_CORE_BUY, _LATE_SMALL_BUY], SID, date(2026, 4, 10), DEFAULT_CONFIG
    )
    assert ev is not None and ev.grade is Grade.CORE
    assert ev.asof == date(2026, 1, 10) and "$600,000" in ev.label
    # ...and the late cluster genuinely WOULD have qualified on its own (so this is a preference,
    # not the candidate silently failing a floor)
    solo = insider_conviction.score([_LATE_SMALL_BUY], SID, date(2026, 4, 10), DEFAULT_CONFIG)
    assert solo is not None and solo.grade is Grade.FLIP and "$15,000" in solo.label


def test_fresh_flip_still_fires_when_no_live_core_exists():
    """The walk must not OVER-reach: a stale (365d-old) core in the history is not resurrected, and
    the genuinely fresh flip cluster fires exactly as it did before."""
    stale_core = _buy("Old Director", "Director", 600_000, d=date(2025, 6, 1))
    fresh = _buy("Jane Doe", "Chief Executive Officer", 50_000, d=date(2026, 5, 25))
    ev = insider_conviction.score([stale_core, fresh], SID, date(2026, 6, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.grade is Grade.FLIP
    assert ev.asof == date(2026, 5, 25) and "$50,000" in ev.label
    assert [p.ref for p in ev.provenance] == [fresh["accession"]]  # the stale core is not fused in


def test_nothing_is_resurrected_when_every_candidate_is_stale():
    """A core past its 180d window plus a flip past its 18d window -> None. The walk adds candidate
    anchors, never a longer memory: each candidate still faces its OWN graded liveness."""
    stale_core = _buy("Old Director", "Director", 600_000, d=date(2025, 11, 1))  # 215d before asof
    stale_flip = _buy("Jane Doe", "Chief Executive Officer", 50_000, d=date(2026, 5, 1))  # 34d
    assert (
        insider_conviction.score([stale_core, stale_flip], SID, date(2026, 6, 4), DEFAULT_CONFIG)
        is None
    )


def test_weight_dial_is_bounded_zero_to_one():
    # the dial's contract: a weight outside [0, 1] fails loud at construction, never silently clamps
    with pytest.raises(ValidationError):
        CallConfig(insider_10b5_1_buy_weight=1.5)
    with pytest.raises(ValidationError):
        CallConfig(insider_10b5_1_buy_weight=-0.1)


# --- the ADR / dual-listed foreign-ordinary screen (S2c) — a home-market row mis-filed on the ADR tape ---


def _adr_row(
    title, fsym, *, usd=600_000, code="P", name="Tien Bor-Zen", role="VP", d=date(2026, 5, 20)
):
    """A txn row carrying the S2c screen inputs (real TSM title strings by default)."""
    return {
        "txn_code": code,
        "usd": usd,
        "insider_name": name,
        "insider_role": role,
        "valid_from": d,
        "accession": "acc-x",
        "security_title": title,
        "issuer_foreign_symbol": fsym,
    }


def test_foreign_ordinary_buy_screened_out_of_conviction():
    """A big "Common Shares (2330.TW)" buy would fire (FLIP) without the screen; the screen drops it —
    it is the WRONG instrument (the home-market ordinary line), not conviction in the ADR we hold.
    """
    ord_row = _adr_row("Common Shares (2330.TW)", "2330.TW", usd=600_000)
    assert insider_conviction.score([ord_row], SID, ASOF) is None


def test_genuine_adr_buy_still_fires():
    """The ADR row ("American Depositary Shares (TSM)") does NOT name the foreign symbol -> KEPT + fires."""
    ev = insider_conviction.score(
        [_adr_row("American Depositary Shares (TSM)", "2330.TW")], SID, ASOF
    )
    assert ev is not None and ev.fired


def test_null_title_is_kept_keep_when_ambiguous():
    """A row ingested before the capture has a NULL title — KEPT (never silently dropped, #9)."""
    ev = insider_conviction.score([_adr_row(None, "2330.TW")], SID, ASOF)
    assert ev is not None and ev.fired


def test_us_name_without_foreign_symbol_never_screened():
    """A US issuer declares no foreign symbol, so a plain "Common Stock" buy is never screened."""
    ev = insider_conviction.score([_adr_row("Common Stock", None)], SID, ASOF)
    assert ev is not None and ev.fired


def test_pbr_bare_foreign_symbol_title_is_screened():
    """Petrobras titles the row with the bare symbol "PETR4"; the containment predicate catches it."""
    assert insider_conviction.score([_adr_row("PETR4", "PETR4", usd=600_000)], SID, ASOF) is None


def test_predicate_case_insensitive_and_adr_token_guard():
    """Direct contract of ``_is_foreign_ordinary``: the foreign-symbol containment is case-insensitive, and
    a title that positively names a DEPOSITARY instrument is KEPT even if it also cites the ordinary symbol
    (the belt-and-suspenders guard — recall-safe #9)."""
    assert _is_foreign_ordinary(
        {"security_title": "common shares (2330.tw)", "issuer_foreign_symbol": "2330.TW"}
    )
    assert not _is_foreign_ordinary(
        {"security_title": "American Depositary Shares (TSM)", "issuer_foreign_symbol": "2330.TW"}
    )
    # a hypothetical ADS title that ALSO cites the ordinary symbol — the depositary token wins -> KEPT
    assert not _is_foreign_ordinary(
        {
            "security_title": "American Depositary Shares representing Common Shares (2330.TW)",
            "issuer_foreign_symbol": "2330.TW",
        }
    )
    # no declared foreign symbol / no title -> KEPT
    assert not _is_foreign_ordinary(
        {"security_title": "Common Shares (2330.TW)", "issuer_foreign_symbol": None}
    )
    assert not _is_foreign_ordinary({"security_title": None, "issuer_foreign_symbol": "2330.TW"})


def test_real_mixed_tsm_filing_before_and_after_the_screen():
    """THE #291 interaction, on a REAL TSM filing (0001046179-26-000461): a $67,970 home-market ordinary
    buy ("Common Shares (2330.TW)") sits beside two tiny genuine ADR buys ($3,900 + $1,950). BEFORE the
    S2c capture the ordinary buy makes the name FIRE; AFTER, only the ADR rows remain — $5,850, below the
    $10k floor -> the conviction correctly drops (no ordinary row anchors a fire)."""
    # the stored-row shape the detector reads (ingest maps txn_date -> valid_from + stamps the accession)
    txns = [
        {**t, "valid_from": t["txn_date"], "accession": "0001046179-26-000461"}
        for t in parse_form4(_TSM_MIXED)  # 2 ADR + 1 ordinary, all code P, 2026-07-28
    ]
    asof = date(2026, 8, 1)
    # BEFORE: the pre-migration state (columns NULL) — the mis-attributed ordinary buy is included + fires
    pre = [{**t, "security_title": None, "issuer_foreign_symbol": None} for t in txns]
    before = insider_conviction.score(pre, SID, asof)
    assert before is not None and before.fired
    # AFTER: the captured title screens the ordinary row; the genuine ADR rows miss the $10k floor
    after = insider_conviction.score(txns, SID, asof)
    assert after is None
