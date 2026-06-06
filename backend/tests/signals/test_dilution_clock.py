from __future__ import annotations

from datetime import date
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.enums import Kind, Role
from ingest.edgar.converts import ConvertTerms
from signals import dilution_clock

SID = uuid4()


def _fact(maturity: date = date(2032, 6, 1)) -> dict:
    # the real HIMS convert terms (as fact_dilution would store them) + the seeded shares basis
    terms = ConvertTerms(
        principal_total_usd=402_500_000.0,
        coupon_pct=0.0,
        maturity_date=maturity,
        conversion_rate=33.8590,
        conversion_price_usd=29.53,
        cap_price_usd=50.15,
        capped_call_cost_usd=36_700_000.0,
        issued_date=date(2026, 5, 21),
    )
    return {
        "instrument_kind": "convertible_notes",
        "accession": "0001193125-26-234847",
        "shares_outstanding": 228_357_303,
        "terms": terms.model_dump(mode="json"),
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
