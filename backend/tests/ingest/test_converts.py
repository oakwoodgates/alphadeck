from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.edgar.converts import clean_filing_text, parse_convert_terms

# The REAL HIMS convertible-notes filings (committed fixtures), same offline pattern as the Form 4.
_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data" / "edgar"


def _parse():
    issuance = clean_filing_text((_SEED / "hims_converts_8k.htm").read_text(encoding="utf-8"))
    pricing = clean_filing_text((_SEED / "hims_converts_pricing.htm").read_text(encoding="utf-8"))
    return parse_convert_terms(issuance, pricing)


def test_parses_the_real_hims_convert_terms_verified_oracle():
    """The platform reads the REAL filing AND reads it right: the deterministic parse must reproduce
    the operator-verified terms exactly (no model-sourced numbers).
    """
    t = _parse()
    # the definitive deal (issuance/closing 8-K)
    assert t.principal_total_usd == 402_500_000.0  # $350M base + $52.5M greenshoe exercised in full
    assert t.base_principal_usd == 350_000_000.0
    assert t.greenshoe_usd == 52_500_000.0
    assert t.greenshoe_exercised_date == date(2026, 5, 19)
    assert t.issued_date == date(2026, 5, 21)
    assert t.coupon_pct == 0.0  # zero-coupon
    assert t.maturity_date == date(2032, 6, 1)
    assert t.conversion_rate == 33.8590  # shares per $1,000
    assert t.conversion_price_usd == 29.53
    assert t.max_conversion_shares == 18_057_397  # make-whole ceiling
    assert t.cap_price_usd == 50.15
    assert t.cap_premium_pct == 125.0
    assert t.capped_call_cost_usd == 36_700_000.0
    assert t.reference_date == date(2026, 5, 18)
    assert t.redeemable_on_or_after == date(2029, 6, 6)
    # enriched from the pricing press release
    assert t.conversion_premium_pct == 32.5
    assert t.reference_price_usd == 22.29
