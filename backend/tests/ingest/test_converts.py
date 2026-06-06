from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.edgar.converts import (
    _is_convert_issuance,
    clean_filing_text,
    discover_convert_issuance,
    parse_convert_terms,
)

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


def test_is_convert_issuance_distinguishes_issuance_from_pricing():
    issuance = clean_filing_text((_SEED / "hims_converts_8k.htm").read_text(encoding="utf-8"))
    pricing = clean_filing_text((_SEED / "hims_converts_pricing.htm").read_text(encoding="utf-8"))
    assert _is_convert_issuance(issuance) is True
    assert _is_convert_issuance(pricing) is False  # the pricing announcement, not the issuance


class _StubClient:
    """Offline EDGAR stand-in: canned submissions + raw doc text keyed by accession."""

    def __init__(self, submissions: dict, docs: dict[str, str]):
        self._submissions = submissions
        self._docs = docs

    def get_json(self, url: str, cache_key: str) -> dict:
        return self._submissions

    def get_text(self, url: str, cache_key: str) -> str:
        for accession, text in self._docs.items():
            if accession.replace("-", "") in url:
                return text
        raise KeyError(url)


def test_discover_convert_issuance_finds_and_parses_from_just_the_cik():
    """The platform finds the convert 8-K itself (no accession handed in): scan 8-Ks -> issuance."""
    issuance_html = (_SEED / "hims_converts_8k.htm").read_text(encoding="utf-8")
    submissions = {
        "filings": {
            "recent": {
                "form": ["8-K", "8-K"],
                "accessionNumber": ["0001773751-26-000091", "0001193125-26-234847"],
                "primaryDocument": ["hims-20260529.htm", "d264371d8k.htm"],
            }
        }
    }
    docs = {
        "0001773751-26-000091": "<html><body>Results of Operations — quarterly earnings.</body></html>",
        "0001193125-26-234847": issuance_html,
    }
    found = discover_convert_issuance(_StubClient(submissions, docs), 1773751)
    assert found is not None
    terms, accession = found
    assert accession == "0001193125-26-234847"  # discovered, not handed in
    assert terms.principal_total_usd == 402_500_000.0 and terms.coupon_pct == 0.0
