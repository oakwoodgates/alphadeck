"""Band 03 S1 — the insider-selling cluster RISK detector (the risk-side mirror of insider_conviction).

Pure, fixed-timestamp tests on ``score`` (the master switch gates only the pit-reading ``detect``,
proven last). The three ratified decisions each get a pinning test: (1) the severity CEILING —
``score <= insider_sell_max_score < risk_block_severity`` under an EXTREME synthetic cluster, so a
sell cluster can never withhold an arm in v1 and lifting the ceiling later is a visible diff; (2) the
MASTER SWITCH — ``detect`` no-ops disabled, stays registered (the registry-list test), fires enabled;
(3) the distinct ``Kind.INSIDER_SELL`` on every fired event. Screens are proven in EACH direction
(excluded AND named; the recall-safe keep), the label/provenance discipline mirrors
``test_dilution_clock``, and the assembler flow-through mirrors
``test_severe_dilution_label_matches_veto_and_flows_to_call_surfaces`` — inverted: this risk is
sub-veto by construction, so the card stays ARMED with a setup-strength haircut and NOTHING in
``missing[]``. The seed guard pins the structural reason existing goldens cannot change: the
committed seed Form 4s carry no code-S rows.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from calls.assembler import assemble_call
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Kind, Role, State
from ingest.edgar.form4 import parse_form4
from signals import insider_sell
from tests.calls.factories import ASOF as CALL_ASOF
from tests.calls.factories import SID as CALL_SID
from tests.calls.factories import (
    breakout_event,
    insider_event,
    insider_sell_event,
    make_thesis,
)

ASOF = date(2026, 6, 4)
SID = uuid4()

_ON = DEFAULT_CONFIG.model_copy(update={"insider_sell_enabled": True})  # the master switch ON


def _sell(name, role, usd, d=date(2026, 5, 20), code="S", aff=False, **extra):
    """A Form 4 sell row as fact_insider_txn hands it back. ``aff`` defaults to False — the checkbox
    PRESENT and CLEAR (an explicitly discretionary sale); pass True (planned) / None (unknown,
    the pre-Dec-2022 norm) to exercise the tri-state screen."""
    t = {
        "txn_code": code,
        "usd": usd,
        "insider_name": name,
        "insider_role": role,
        "valid_from": d,
        "accession": f"acc-{name}",
        "aff_10b5_1": aff,
    }
    t.update(extra)
    return t


# --- fires on a qualifying cluster: contract, label texture, provenance, anchor ---------------------


def test_fires_on_a_qualifying_discretionary_cluster():
    """Two senior insiders sell $750k discretionary inside one window (a planned $5M rides beside
    them) -> a RISK signal: ungraded, no dearm target, no wire liveness (freshness is
    detector-enforced), the full label texture (sellers/senior/$/txns + the planned-screened note),
    sorted per-accession provenance carrying the work, and ``asof`` = the cluster anchor."""
    txns = [
        _sell("Jane Doe", "Chief Executive Officer", 400_000, d=date(2026, 5, 18)),
        _sell("John Roe", "Chief Financial Officer", 350_000, d=date(2026, 5, 20)),
        _sell("Plan Peddler", "Director", 5_000_000, d=date(2026, 5, 19), aff=True),
    ]
    ev = insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.role is Role.RISK_SIGNAL and ev.kind is Kind.INSIDER_SELL
    assert ev.grade is None  # a risk is ungraded...
    assert ev.dearm_grade is None  # ...and this one is NOT a de-arm (unlike breakdown)
    assert ev.alpha_liveness_days is None  # matching dilution/breakdown: no wire liveness
    assert ev.asof == date(2026, 5, 20)  # the anchor = the most recent QUALIFYING (kept) sale
    assert ev.score <= DEFAULT_CONFIG.insider_sell_max_score
    assert ev.label == (
        "2 insiders incl. senior officer sold $750,000 open-market (code S) across 2 txns; "
        "1 planned-sale (10b5-1) txn ($5,000,000) screened"
    )
    # provenance: one form4 ref per KEPT accession, sorted; the screened filing is NOT a source
    assert [p.ref for p in ev.provenance] == ["acc-Jane Doe", "acc-John Roe"]
    d = ev.provenance[0].detail  # the work, shown (#6)
    assert d["total_usd"] == 750_000.0 and d["distinct_sellers"] == 2 and d["txn_count"] == 2
    assert d["senior_in_cluster"] is True
    assert d["planned_screened"] == 1 and d["planned_screened_usd"] == 5_000_000.0
    assert d["unknown_plan_kept"] == 0


def test_never_fires_on_buys():
    """The mirror of the buy side's sales-only test: a code-P stream is not selling pressure."""
    buys = [
        _sell("Jane Doe", "Chief Executive Officer", 500_000, code="P"),
        _sell("John Roe", "Chief Financial Officer", 500_000, code="P"),
    ]
    assert insider_sell.score(buys, SID, ASOF, DEFAULT_CONFIG) is None


def test_deterministic_regardless_of_input_row_order():
    """The dilution row-order precedent: same event either way, provenance sorted by accession."""
    alpha = _sell("Alpha Seller", "Chief Executive Officer", 400_000)
    zulu = _sell("Zulu Seller", "Chief Financial Officer", 350_000)
    forward = insider_sell.score([zulu, alpha], SID, ASOF, DEFAULT_CONFIG)
    reverse = insider_sell.score([alpha, zulu], SID, ASOF, DEFAULT_CONFIG)
    assert forward is not None and reverse is not None
    assert forward.model_dump() == reverse.model_dump()
    assert [p.ref for p in forward.provenance] == ["acc-Alpha Seller", "acc-Zulu Seller"]


# --- silent when: the floors + freshness ------------------------------------------------------------


def test_silent_below_min_usd():
    """Selling is routine — a $200k cluster sits below the $250k floor (honest loudness)."""
    txns = [
        _sell("Jane Doe", "Chief Executive Officer", 100_000),
        _sell("John Roe", "Chief Financial Officer", 100_000),
    ]
    assert insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG) is None


def test_silent_on_a_single_seller():
    """ "Clustered" is the load-bearing word: one big sale is the many-reasons case — NO single-seller
    path in v1 (the deliberate asymmetry with the buy side's insider_strong_single_usd)."""
    assert (
        insider_sell.score(
            [_sell("Jane Doe", "Chief Executive Officer", 5_000_000)], SID, ASOF, DEFAULT_CONFIG
        )
        is None
    )


def test_silent_without_a_senior_seller_when_required():
    """Officer/director sales are what the (weak) literature measures; two 10%-owner fund blocks
    alone don't fire under the default. The dial is real: require_senior=False fires the same rows
    (the config-driven behavioral guard, not a hardcoded gate)."""
    txns = [
        _sell("Fund A LP", "10% owner", 300_000),
        _sell("Fund B LP", "10% owner", 300_000),
    ]
    assert insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG) is None
    lenient = DEFAULT_CONFIG.model_copy(update={"insider_sell_require_senior": False})
    ev = insider_sell.score(txns, SID, ASOF, lenient)
    assert ev is not None and "incl. senior officer" not in ev.label


def test_silent_when_every_sale_is_planned():
    """An all-10b5-1 stream is near-noise: nothing discretionary remains -> no cluster at all."""
    txns = [
        _sell("Jane Doe", "Chief Executive Officer", 400_000, aff=True),
        _sell("John Roe", "Chief Financial Officer", 350_000, aff=True),
    ]
    assert insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG) is None


def test_stale_cluster_drops_out_with_the_inclusive_boundary():
    """Freshness is DETECTOR-enforced (the assembler never ages risks): the cluster emits AT the
    liveness horizon (the entry_signal_is_live inclusive idiom) and drops out one day past it."""
    anchor = date(2026, 3, 6)
    txns = [
        _sell("Jane Doe", "Chief Executive Officer", 400_000, d=anchor),
        _sell("John Roe", "Chief Financial Officer", 350_000, d=anchor),
    ]
    at_horizon = anchor + timedelta(days=DEFAULT_CONFIG.insider_sell_liveness_days)
    assert insider_sell.score(txns, SID, at_horizon, DEFAULT_CONFIG) is not None  # inclusive
    past = at_horizon + timedelta(days=1)
    assert insider_sell.score(txns, SID, past, DEFAULT_CONFIG) is None  # stale -> out of the stream


def test_cohesion_window_scopes_the_cluster_to_one_episode():
    """Sales months before the anchor are NOT fused in (the buy side's cohesion logic): the total,
    txn count, and floors read the [anchor - window, anchor] episode only."""
    txns = [
        _sell("Old Seller", "Director", 9_000_000, d=date(2026, 3, 1)),  # months earlier
        _sell("Jane Doe", "Chief Executive Officer", 150_000, d=date(2026, 5, 20)),
        _sell("John Roe", "Chief Financial Officer", 120_000, d=date(2026, 5, 20)),
    ]
    ev = insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None
    assert ev.label.startswith(
        "2 insiders incl. senior officer sold $270,000 open-market (code S) across 2 txns"
    )
    # and when the WINDOWED episode alone fails the floors, there is no signal — an out-of-window
    # whale can't carry a thin recent cluster over the line
    thin = [
        _sell("Old Seller", "Director", 9_000_000, d=date(2026, 3, 1)),
        _sell("Jane Doe", "Chief Executive Officer", 100_000, d=date(2026, 5, 20)),
    ]
    assert insider_sell.score(thin, SID, ASOF, DEFAULT_CONFIG) is None


# --- the ANCHOR WALK regression: a lone late sale must not SILENCE a live episode -------------------
# Measured before the fix (score() anchored unconditionally on the most-recent kept sale, with no
# fallback to an earlier anchor): a 2-seller senior episode ($300k Director 2026-05-01 + $200k CFO
# 2026-05-02) inside its 90d liveness fires 0.4875 at asof 2026-07-25. Add ONE lone $30k employee
# sale on 2026-07-20 and the whole risk returned None — the lone sale re-anchored the episode onto
# itself and failed insider_sell_min_distinct=2. More insider selling read MORE bullish. The walk is
# the buy side's, mirrored: each distinct kept-sale date is a candidate anchor, newest -> oldest, and
# the most recent QUALIFYING episode fires (no grade axis on a risk to prefer over recency).

_EPISODE = [
    _sell("Dir Seller", "Director", 300_000, d=date(2026, 5, 1)),
    _sell("CFO Seller", "Chief Financial Officer", 200_000, d=date(2026, 5, 2)),
]
_LONE_LATE_SALE = _sell("Lone Staffer", "Employee", 30_000, d=date(2026, 7, 20))
_WALK_ASOF = date(2026, 7, 25)


def test_lone_late_sale_does_not_silence_a_live_episode():
    """The measured scenario. The lone $30k sale cannot qualify on its own (one seller), so the walk
    falls back to the still-live 2-seller episode — identical event to the episode standing alone.
    """
    alone = insider_sell.score(_EPISODE, SID, _WALK_ASOF, DEFAULT_CONFIG)
    assert alone is not None and alone.asof == date(2026, 5, 2)

    ev = insider_sell.score([*_EPISODE, _LONE_LATE_SALE], SID, _WALK_ASOF, DEFAULT_CONFIG)
    assert ev is not None  # was None before the walk — the risk disappeared entirely
    assert ev.asof == date(2026, 5, 2)  # the CHOSEN episode's anchor, not the lone sale's date
    assert "$500,000" in ev.label and "2 insiders" in ev.label
    assert [p.ref for p in ev.provenance] == ["acc-CFO Seller", "acc-Dir Seller"]
    assert ev.model_dump() == alone.model_dump()  # the extra sale changes nothing about the risk


def test_walk_still_enforces_anchor_freshness():
    """The walk adds candidate anchors, never a longer memory: past the 90d liveness the episode is
    out of the re-derived stream, and no OLDER candidate can rescue it (freshness is
    grade-independent on this side, so the walk stops at the first stale anchor)."""
    past = date(2026, 5, 2) + timedelta(days=DEFAULT_CONFIG.insider_sell_liveness_days + 1)
    assert insider_sell.score(_EPISODE, SID, past, DEFAULT_CONFIG) is None
    assert insider_sell.score([*_EPISODE, _LONE_LATE_SALE], SID, past, DEFAULT_CONFIG) is None


def test_walk_prefers_the_most_recent_qualifying_episode():
    """Two qualifying episodes in the window -> the MOST RECENT fires (recency is the only axis on a
    risk); the older one is not fused in, its dollars stay out of the total."""
    older = [
        _sell("A Seller", "Director", 4_000_000, d=date(2026, 5, 1)),
        _sell("B Seller", "Chief Financial Officer", 4_000_000, d=date(2026, 5, 1)),
    ]
    newer = [
        _sell("C Seller", "Director", 300_000, d=date(2026, 7, 20)),
        _sell("D Seller", "Chief Executive Officer", 200_000, d=date(2026, 7, 21)),
    ]
    ev = insider_sell.score([*older, *newer], SID, _WALK_ASOF, DEFAULT_CONFIG)
    assert ev is not None
    assert ev.asof == date(2026, 7, 21) and "$500,000" in ev.label


# --- the screens, each direction --------------------------------------------------------------------


def test_unknown_plan_status_is_kept_and_counted():
    """aff_10b5_1 = None (the pre-Dec-2022 norm) must never be coerced to "planned" OR
    "discretionary": the row is KEPT (erring cautious on the risk side) and the unknown count is
    named in the work."""
    txns = [
        _sell("Jane Doe", "Chief Executive Officer", 400_000, aff=None),
        _sell("John Roe", "Chief Financial Officer", 350_000, aff=None),
    ]
    ev = insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None
    assert "$750,000" in ev.label  # both unknowns feed the total
    assert ev.provenance[0].detail["unknown_plan_kept"] == 2


def _self_sell(name, *, issuer_name=None, owner_cik=None, issuer_cik=None, usd=690_000_000):
    # a large AT-MARKET code-S block by the issuer itself (treasury/ADR mechanics — never personal
    # insider supply); the price screen does not catch it, the identity screen must
    t = _sell(name, "10% owner", usd)
    if issuer_name is not None:
        t["issuer_name"] = issuer_name
    if owner_cik is not None:
        t["rpt_owner_cik"] = owner_cik
    if issuer_cik is not None:
        t["issuer_cik"] = issuer_cik
    return t


def test_excludes_issuer_self_by_cik():
    txn = _self_sell("KYOCERA CORP", owner_cik="0000054321", issuer_cik="0000054321")
    assert insider_sell.score([txn], SID, ASOF, DEFAULT_CONFIG) is None


def test_excludes_issuer_self_by_cik_ignoring_zero_padding():
    txn = _self_sell("Roivant Sciences Ltd.", owner_cik="1479290", issuer_cik="0001479290")
    assert insider_sell.score([txn], SID, ASOF, DEFAULT_CONFIG) is None


def test_excludes_issuer_self_by_name_fallback_via_param():
    """The already-ingested path: no CIKs, no row issuer_name — the security-master name passed in
    (casefolded) still recognises the self-filing."""
    txn = _self_sell("KYOCERA CORP")  # no identity fields on the row at all
    assert insider_sell.score([txn], SID, ASOF, issuer_name="Kyocera Corp") is None


def test_self_filing_screened_beside_a_real_cluster_and_counted():
    """The screen isolates the self-block only: the real senior cluster beside it still fires on its
    own numbers, and the set-aside is counted in the work (#6/#9 — screened, never vanished)."""
    txns = [
        _self_sell("Devco Inc", owner_cik="111", issuer_cik="111", usd=500_000_000),
        _sell("Jane Doe", "Chief Executive Officer", 150_000),
        _sell("John Roe", "Chief Financial Officer", 120_000),
    ]
    ev = insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None
    assert "$270,000" in ev.label and len(ev.provenance) == 2  # the $500M block is not in the total
    assert ev.provenance[0].detail["self_filings_screened"] == 1


def test_below_day_low_sale_set_aside_and_named():
    """A code-S priced well below the day's own tape is a discounted registered secondary — a
    DIFFERENT risk family, not open-market selling pressure: set aside, NAMED in the label (an
    over-aggressive sell screen makes the platform MORE bullish, so set-asides must show — #6)."""
    d = date(2026, 5, 20)
    txns = [
        _sell("Jane Doe", "Chief Executive Officer", 200_000, d=d),
        _sell("John Roe", "Chief Financial Officer", 150_000, d=d),
        _sell("Block Seller", "Director", 9_000_000, d=d, price=10.0),  # $10 vs a $30 low
    ]
    ev = insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG, day_lows={d: 30.0})
    assert ev is not None
    assert ev.label == (
        "2 insiders incl. senior officer sold $350,000 open-market (code S) across 2 txns; "
        "1 below-day-low txn set aside (not open-market)"
    )
    assert ev.provenance[0].detail["below_low_set_aside"] == 1
    assert all("Block Seller" not in p.ref for p in ev.provenance)


def test_missing_price_bar_keeps_the_row_recall_safe():
    """No day low -> we cannot prove a sale was off-market -> KEEP (#9, the recall-safe direction —
    the buy side's convention, mirrored)."""
    txns = [
        _sell("Jane Doe", "Chief Executive Officer", 200_000, price=1.0),
        _sell("John Roe", "Chief Financial Officer", 150_000, price=1.0),
    ]
    assert insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG, day_lows={}) is not None


def test_implausible_row_dropped_even_without_price_context():
    """A $2T "sale" is bad source data, never real supply (#3): the absolute ceiling (the SAME reused
    dial as the buy side) drops it with no day low needed; the real cluster beside it is untouched.
    """
    garbage = _sell("MILLS THOMAS E", "10% owner", 2_000_000_000_000)  # $2T
    assert insider_sell.score([garbage], SID, ASOF, DEFAULT_CONFIG) is None
    txns = [
        garbage,
        _sell("Jane Doe", "Chief Executive Officer", 150_000),
        _sell("John Roe", "Chief Financial Officer", 120_000),
    ]
    ev = insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None
    assert "$270,000" in ev.label
    assert ev.provenance[0].detail["implausible_dropped"] == 1


# --- the CEILING (ratified decision 1): a sell cluster can never block in v1 ------------------------


def test_ceiling_holds_under_an_extreme_synthetic_cluster():
    """The "cannot block in v1" guarantee AS A TEST: ten senior insiders dumping $500M — the most
    extreme cluster constructible — still scores exactly at insider_sell_max_score (the clamp BINDS),
    which is pinned at 0.60, strictly below risk_block_severity (0.70). Lifting the ceiling is
    therefore a VISIBLE diff here, to be made only with the lab's 0.70-crossing count measured."""
    txns = [
        _sell(f"Senior Seller {i}", "Chief Executive Officer", 50_000_000, d=date(2026, 5, 20))
        for i in range(10)
    ]
    ev = insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG)
    assert ev is not None
    assert ev.score == DEFAULT_CONFIG.insider_sell_max_score  # the clamp genuinely binds
    assert DEFAULT_CONFIG.insider_sell_max_score == 0.60  # the ratified ceiling (2026-08-16)
    assert DEFAULT_CONFIG.insider_sell_max_score < DEFAULT_CONFIG.risk_block_severity  # strictly


# --- assembler flow-through: rides the card, haircuts setup strength, never withholds ---------------


def _flow_risk():
    """A real detector event on the CALL factories' security/date, at the score CEILING (the maximum
    possible live effect), for the assembler flow-through."""
    sells = [
        _sell(
            f"Senior Seller {i}",
            "Chief Executive Officer",
            50_000_000,
            d=CALL_ASOF - timedelta(days=2),
        )
        for i in range(10)
    ]
    return insider_sell.score(sells, CALL_SID, CALL_ASOF, DEFAULT_CONFIG)


def test_flow_through_stays_armed_with_a_haircut_and_counter_case():
    """The mirror of the severe-dilution flow-through, INVERTED by design: even the ceiling-score
    sell cluster is sub-veto, so the card stays ARMED — the label rides risk_signals[] and the
    counter-case, confidence takes the setup-strength haircut, and NOTHING lands in missing[]
    (missing names blockers; this can never be one)."""
    risk = _flow_risk()
    assert risk is not None and risk.score < DEFAULT_CONFIG.risk_block_severity

    thesis = make_thesis()
    no_risk = assemble_call(thesis, [insider_event(), breakout_event()], CALL_ASOF, DEFAULT_CONFIG)
    card = assemble_call(
        thesis, [insider_event(), breakout_event(), risk], CALL_ASOF, DEFAULT_CONFIG
    )
    assert card.state is State.ARMED  # the ceiling cannot withhold the arm (decision 1)
    assert card.risk_signals[0].label == risk.label  # rides the card with its provenance
    assert card.risk_signals[0].kind is Kind.INSIDER_SELL
    assert card.risk_signals[0].event_date == risk.asof  # the cluster anchor, not the query asof
    assert risk.label in card.counter_case  # feeds the counter-case prose
    assert card.missing == []  # NOT a blocker -> nothing lands in missing[]
    assert no_risk.confidence is not None and card.confidence is not None
    assert card.confidence < no_risk.confidence  # the setup-strength haircut


def test_factory_event_is_contract_valid_and_rides_an_armed_card():
    """The shared factory (tests/calls/factories.insider_sell_event) matches the detector's contract
    — sub-veto RISK, ungraded, no dearm target — and rides an Armed card without blocking it."""
    ev = insider_sell_event()
    assert ev.role is Role.RISK_SIGNAL and ev.kind is Kind.INSIDER_SELL
    assert ev.grade is None and ev.dearm_grade is None
    assert ev.score <= DEFAULT_CONFIG.insider_sell_max_score < DEFAULT_CONFIG.risk_block_severity
    card = assemble_call(
        make_thesis(), [insider_event(), breakout_event(), ev], CALL_ASOF, DEFAULT_CONFIG
    )
    assert card.state is State.ARMED
    assert card.risk_signals[0].kind is Kind.INSIDER_SELL


# --- the MASTER SWITCH (ratified decision 2): registered, but detect no-ops until enabled -----------


class _FakePit:
    """A minimal SignalPointInTimeData stand-in: canned txns + bars + the master name, so the gated
    detect() wiring (switch -> day_lows -> issuer_name -> score) is exercised without a DB."""

    def __init__(self, txns, bars=None, name="Devco Inc"):
        self._txns, self._bars, self._name = txns, bars or [], name
        self.asof = ASOF
        self.known_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
        self.tenant_id = DEFAULT_TENANT_ID

    def insider_txns(self, security_id):
        return self._txns

    def price_history(self, security_id, lookback_days=None):
        return self._bars

    def security_name(self, security_id):
        return self._name


def test_the_master_switch_now_defaults_on_but_still_gates_detect():
    """The switch is now LIVE by default (insider_sell_enabled=True, ratified on the 2026-08-19
    sig-lab pass: 58 fires / 5 theses / 0 withholds / 0 de-arms) — detect() FIRES on a genuine
    cluster under DEFAULT_CONFIG. Explicitly OFF it still no-ops (the switch continues to gate), and
    the pure score() stays UNGATED either way (the testable math, the breakdown precedent)."""
    txns = [
        _sell("Jane Doe", "Chief Executive Officer", 400_000),
        _sell("John Roe", "Chief Financial Officer", 350_000),
    ]
    pit = _FakePit(txns)
    assert (
        DEFAULT_CONFIG.insider_sell_enabled is True
    )  # the ratified LIVE default (sig-lab 2026-08-19)
    off = DEFAULT_CONFIG.model_copy(update={"insider_sell_enabled": False})
    assert insider_sell.detect(pit, SID, ASOF, off) is None  # explicitly OFF -> no-op
    assert insider_sell.detect(pit, SID, ASOF, DEFAULT_CONFIG) is not None  # LIVE default -> fires
    assert insider_sell.detect(pit, SID, ASOF, _ON) is not None  # switch ON -> fires
    assert insider_sell.score(txns, SID, ASOF, DEFAULT_CONFIG) is not None  # score is ungated


def test_detect_builds_the_price_and_identity_screens_from_the_pit():
    """End-to-end through detect() (switch ON): the price bars feed the day-low map (the below-low
    block is set aside) and the master name feeds the issuer-self screen — the same one as-of view
    the buy side's detect uses, no lookahead."""
    d = date(2026, 5, 20)
    txns = [
        _sell("Jane Doe", "Chief Executive Officer", 200_000, d=d),
        _sell("John Roe", "Chief Financial Officer", 150_000, d=d),
        _sell("Block Seller", "Director", 9_000_000, d=d, price=10.0),  # below the $30 low
        _self_sell("Devco Inc"),  # matches the pit's security_name -> screened
    ]
    bars = [{"d": d, "low": 30.0, "high": 36.0, "close": 32.0}]
    ev = insider_sell.detect(_FakePit(txns, bars), SID, ASOF, _ON)
    assert ev is not None
    assert "$350,000" in ev.label and "set aside" in ev.label
    detail = ev.provenance[0].detail
    assert detail["below_low_set_aside"] == 1 and detail["self_filings_screened"] == 1


# --- the structural golden guarantee: the seed carries no sells -------------------------------------

_SEED_EDGAR = Path(__file__).resolve().parents[2] / "seed_data" / "edgar"


def test_seed_form4s_carry_no_code_s_sells():
    """WHY the seeded UNH/HIMS goldens cannot change from this slice (even with the switch ON): the
    committed seed Form 4s contain no code-S rows at all. Pinned here so a future seed change that
    introduces sells flips this test visibly instead of silently re-shaping the demo goldens."""
    xmls = sorted(_SEED_EDGAR.glob("*form4*.xml"))
    assert xmls, "the seed Form 4s must exist for this guard to mean anything"
    for path in xmls:
        rows = parse_form4(path.read_text(encoding="utf-8"))
        assert rows, path.name
        assert all(t["txn_code"] != "S" for t in rows), path.name
