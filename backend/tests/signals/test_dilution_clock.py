from __future__ import annotations

from datetime import date
from uuid import uuid4

import pytest

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import Kind, Role, State
from ingest.edgar.converts import ConvertTerms
from signals import dilution_clock
from tests.calls.factories import ASOF, breakout_event, insider_event, make_thesis
from tests.calls.factories import SID as CALL_SID

SID = uuid4()


def _fact(
    maturity: date = date(2032, 6, 1),
    *,
    issued: date = date(2026, 5, 21),
    accession: str = "0001193125-26-234847",
    principal: float = 402_500_000.0,
    conversion_rate: float = 33.8590,
    shares_outstanding: float = 228_357_303,
    cap_price: float = 50.15,
) -> dict:
    # the real HIMS convert terms (as fact_dilution would store them) + the seeded shares basis
    terms = ConvertTerms(
        principal_total_usd=principal,
        coupon_pct=0.0,
        maturity_date=maturity,
        conversion_rate=conversion_rate,
        conversion_price_usd=29.53,
        cap_price_usd=cap_price,
        capped_call_cost_usd=36_700_000.0,
        issued_date=issued,
    )
    return {
        "instrument_kind": "convertible_notes",
        "accession": accession,
        "shares_outstanding": shares_outstanding,
        "terms": terms.model_dump(mode="json"),
        "valid_from": issued,
    }


def test_dilution_clock_emits_a_low_nonblocking_risk():
    ev = dilution_clock.score([_fact()], SID, date(2026, 6, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.role is Role.RISK_SIGNAL and ev.kind is Kind.DILUTION_RISK and ev.grade is None
    assert ev.score < DEFAULT_CONFIG.risk_block_severity  # NON-blocking — a few % overhang
    assert 0.10 < ev.score < 0.30  # ~6% gross overhang scaled against the severe-overhang knob
    assert "convertible notes" in ev.label.lower() and "dilution" in ev.label.lower()
    assert "402.5M" in ev.label and "capped call" in ev.label  # the operator-facing texture
    assert ev.provenance[0].source == "8-k" and ev.provenance[0].ref == "0001193125-26-234847"
    assert ev.asof == date(2026, 5, 21)  # stamped with the issuance (event) date


def test_dilution_clock_silent_when_absent_or_matured():
    assert dilution_clock.score([], SID, date(2026, 6, 1), DEFAULT_CONFIG) is None
    matured = _fact(maturity=date(2025, 1, 1))  # already matured -> no live overhang
    assert dilution_clock.score([matured], SID, date(2026, 6, 1), DEFAULT_CONFIG) is None


def test_multi_offering_uses_latest_shares_basis_independent_of_row_order():
    older = _fact(
        issued=date(2025, 1, 15),
        accession="0000000001-25-000001",
        principal=100_000_000,
        conversion_rate=100.0,  # 10M as-converted shares
        shares_outstanding=80_000_000,
        cap_price=40.0,
    )
    newer = _fact(
        issued=date(2026, 2, 20),
        accession="0000000001-26-000002",
        principal=50_000_000,
        conversion_rate=100.0,  # 5M as-converted shares
        shares_outstanding=200_000_000,
        cap_price=60.0,
    )

    # Aggregate overhang = (10M + 5M) / the LATEST 200M shares basis = 7.5%.
    forward_pct = dilution_clock.overhang_pct([older, newer], ASOF)
    reverse_pct = dilution_clock.overhang_pct([newer, older], ASOF)
    assert forward_pct == pytest.approx(7.5)
    assert reverse_pct == pytest.approx(forward_pct)

    forward = dilution_clock.score([older, newer], SID, ASOF, DEFAULT_CONFIG)
    reverse = dilution_clock.score([newer, older], SID, ASOF, DEFAULT_CONFIG)
    assert forward is not None and reverse is not None
    assert forward.model_dump() == reverse.model_dump()
    assert [p.ref for p in forward.provenance] == [
        "0000000001-25-000001",
        "0000000001-26-000002",
    ]
    assert all(p.detail["shares_outstanding"] == 200_000_000 for p in forward.provenance)
    assert "cap ~$60.00" in forward.label


def test_severe_dilution_label_matches_veto_and_flows_to_call_surfaces():
    risk = dilution_clock.score(
        [_fact(shares_outstanding=40_000_000)],
        CALL_SID,
        ASOF,
        DEFAULT_CONFIG,
    )
    assert risk is not None and risk.score >= DEFAULT_CONFIG.risk_block_severity
    assert "withholds the Armed call on timing" in risk.label
    assert "not an entry blocker" not in risk.label

    card = assemble_call(
        make_thesis(),
        [insider_event(), breakout_event(), risk],
        ASOF,
        DEFAULT_CONFIG,
    )
    assert card.state is State.WARMING
    assert card.risk_signals[0].label == risk.label
    assert risk.label in card.counter_case
    assert any(risk.label in item for item in card.missing)


def test_severe_overhang_scores_the_dedicated_dial_and_clears_the_gate_with_margin():
    """Item 1: a >= severe overhang scores to ``dilution_severe_score`` (the dedicated severity dial),
    clearing the veto gate (``risk_block_severity``) WITH MARGIN — no longer landing exactly on the >=
    boundary the way the old ``* risk_block_severity`` math did (a severe overhang == 0.70 == the gate).
    """
    risk = dilution_clock.score([_fact(shares_outstanding=40_000_000)], SID, ASOF, DEFAULT_CONFIG)
    assert risk is not None
    # a >= severe overhang saturates the overhang ratio to 1.0, so the score IS the dedicated dial
    assert risk.score == DEFAULT_CONFIG.dilution_severe_score  # 0.80, not 0.70
    assert risk.score > DEFAULT_CONFIG.risk_block_severity  # clears the gate WITH MARGIN (not ==)
    assert risk.score - DEFAULT_CONFIG.risk_block_severity >= 0.09  # a real margin, not zero


def test_dilution_score_is_decoupled_from_the_veto_dial():
    """Item 1: retuning ``risk_block_severity`` (the UNIVERSAL veto threshold) no longer rescales the
    dilution score — the score reads the dedicated ``dilution_severe_score`` dial, so the two knobs move
    independently. Before the decoupling, ``* risk_block_severity`` meant every veto-dial change silently
    re-scored every dilution overhang."""
    fact = _fact(shares_outstanding=40_000_000)  # a severe overhang (>= severe_pct)
    base = dilution_clock.score([fact], SID, ASOF, DEFAULT_CONFIG)
    assert base is not None
    # move ONLY the veto dial, down AND up — the dilution score must not budge
    for veto in (0.50, 0.95):
        moved = DEFAULT_CONFIG.model_copy(update={"risk_block_severity": veto})
        assert dilution_clock.score([fact], SID, ASOF, moved).score == base.score
    # and moving the DEDICATED dial DOES move the score (it now owns "how loud is a severe dilution")
    louder = DEFAULT_CONFIG.model_copy(update={"dilution_severe_score": 0.90})
    assert dilution_clock.score([fact], SID, ASOF, louder).score == 0.90
