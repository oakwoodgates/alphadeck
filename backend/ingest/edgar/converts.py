"""Deterministic convertible-note term extraction from EDGAR 8-Ks (the dilution overhang's facts).

The platform READS the filing — it never takes a number from a model (invariant #3 / CALL_LOGIC §8).
Convertible-note offering/closing 8-Ks and their pricing press releases use regular, boilerplate
phrasing ("$X aggregate principal amount of Y% convertible senior notes due ZZZZ", "initial conversion
rate of N shares per $1,000", "conversion price of approximately $P", ...), so the terms come out via
plain regex. This is the FIRST BRICK of the SEC filing-intelligence capability — tuned to this filing
class; generalizing across every dilution flavor/name is deferred.
"""

from __future__ import annotations

import html as _html
import re
from datetime import date, datetime
from uuid import UUID

import psycopg
from psycopg.types.json import Json
from pydantic import BaseModel

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.settings import get_settings


class ConvertTerms(BaseModel):
    """Convertible-note terms parsed from the filing — the structured facts behind a dilution signal."""

    principal_total_usd: float  # the definitive total (after any greenshoe)
    coupon_pct: float
    maturity_date: date
    conversion_rate: float  # shares per $1,000 principal
    conversion_price_usd: float
    base_principal_usd: float | None = None
    greenshoe_usd: float | None = None
    greenshoe_exercised_date: date | None = None
    issued_date: date | None = None
    max_conversion_shares: int | None = None  # ceiling at the make-whole max rate (the worst case)
    conversion_premium_pct: float | None = None  # over the reference price (pricing release only)
    reference_price_usd: float | None = None  # the pre-deal close (pricing release only)
    reference_date: date | None = None
    cap_price_usd: float | None = None
    cap_premium_pct: float | None = None
    capped_call_cost_usd: float | None = None
    redeemable_on_or_after: date | None = None


# Regular convert-8-K / pricing-release phrasing. From the issuance/closing 8-K unless noted (pricing).
_RE_TOTAL = r"Company issued \$([\d.,]+)\s*million aggregate principal"
_RE_COUPON = r"million principal amount of ([\d.]+)% Convertible Senior Notes"
_RE_MATURITY = r"Notes will mature on (\w+ \d{1,2}, \d{4})"
_RE_CONV_RATE = r"initial conversion rate is ([\d.]+) shares"
_RE_CONV_PRICE = r"initial conversion price of approximately \$([\d.]+)"
_RE_BASE = r"aggregate of \$([\d.,]+)\s*million principal amount of [\d.]+% Convertible"
_RE_GREENSHOE = r"additional \$([\d.,]+)\s*million aggregate principal"
_RE_GREENSHOE_DATE = r"exercised in full on (\w+ \d{1,2}, \d{4})"
_RE_ISSUED = r"On (\w+ \d{1,2}, \d{4}), the Company issued"
_RE_MAX_SHARES = r"([\d,]{6,}) shares[^.]*?may be issued upon conversion"
_RE_CAP_PRICE = r"cap price of the Capped Call Transactions is initially approximately \$([\d.]+)"
_RE_CAP_PREMIUM = r"premium of ([\d.]+)% over the last reported sale price"
_RE_CAP_COST = r"cost of the Capped Call Transactions was approximately \$([\d.,]+)\s*million"
_RE_REF_DATE = r"premium of [\d.]+% over the last reported sale price[^.]*?on (\w+ \d{1,2}, \d{4})"
_RE_REDEEM = r"redeem[^.]*?on or after (\w+ \d{1,2}, \d{4})"
_RE_CONV_PREMIUM = r"conversion price represents a premium of approximately ([\d.]+)%"  # pricing
_RE_REF_PRICE = r"last reported sale price of \$([\d.]+) per share"  # pricing


def clean_filing_text(html: str) -> str:
    """Strip an EDGAR HTML filing to flat, single-spaced text for regex extraction."""
    t = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    t = re.sub(r"<[^>]+>", " ", t)
    return re.sub(r"\s+", " ", _html.unescape(t)).strip()


def _one(text: str, pattern: str, field: str) -> str:
    m = re.search(pattern, text, re.IGNORECASE)
    if not m:
        raise ValueError(f"convert parse: could not extract {field}")
    return m.group(1)


def _opt(text: str, pattern: str) -> str | None:
    m = re.search(pattern, text, re.IGNORECASE)
    return m.group(1) if m else None


def _usd_m(s: str | None) -> float | None:
    return round(float(s.replace(",", "")) * 1_000_000, 2) if s else None


def _md(s: str | None) -> date | None:
    return datetime.strptime(s, "%B %d, %Y").date() if s else None  # "June 1, 2032"


def parse_convert_terms(issuance_text: str, pricing_text: str = "") -> ConvertTerms:
    """Parse the definitive deal from the issuance/closing 8-K, enriched by the pricing press release
    (the conversion premium + reference price live only there). Pure + deterministic; raises if the
    core terms aren't present (i.e. this isn't a recognized convert 8-K).
    """
    i, p = issuance_text, pricing_text
    return ConvertTerms(
        principal_total_usd=_usd_m(_one(i, _RE_TOTAL, "principal_total")),
        coupon_pct=float(_one(i, _RE_COUPON, "coupon")),
        maturity_date=_md(_one(i, _RE_MATURITY, "maturity")),
        conversion_rate=float(_one(i, _RE_CONV_RATE, "conversion_rate")),
        conversion_price_usd=float(_one(i, _RE_CONV_PRICE, "conversion_price")),
        base_principal_usd=_usd_m(_opt(i, _RE_BASE)),
        greenshoe_usd=_usd_m(_opt(i, _RE_GREENSHOE)),
        greenshoe_exercised_date=_md(_opt(i, _RE_GREENSHOE_DATE)),
        issued_date=_md(_opt(i, _RE_ISSUED)),
        max_conversion_shares=int(s.replace(",", "")) if (s := _opt(i, _RE_MAX_SHARES)) else None,
        cap_price_usd=float(s) if (s := _opt(i, _RE_CAP_PRICE)) else None,
        cap_premium_pct=float(s) if (s := _opt(i, _RE_CAP_PREMIUM)) else None,
        capped_call_cost_usd=_usd_m(_opt(i, _RE_CAP_COST)),
        reference_date=_md(_opt(i, _RE_REF_DATE)),
        redeemable_on_or_after=_md(_opt(i, _RE_REDEEM)),
        conversion_premium_pct=float(s) if (s := _opt(p, _RE_CONV_PREMIUM)) else None,
        reference_price_usd=float(s) if (s := _opt(p, _RE_REF_PRICE)) else None,
    )


def ingest_convert_terms(
    conn: psycopg.Connection,
    security_id: UUID,
    terms: ConvertTerms,
    accession: str,
    *,
    shares_outstanding: float | None = None,
    shares_outstanding_ref: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    recorded_at=None,
) -> UUID:
    """Append parsed convert terms to fact_dilution (append-only; the caller owns the transaction).

    ``shares_outstanding`` (with its ``_ref`` provenance) is the seeded basis for the % overhang.
    """
    if terms.issued_date is None:
        raise ValueError("convert ingest: terms.issued_date (the fact's valid_from) is required")
    values: dict = {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "instrument_kind": "convertible_notes",
        "accession": accession,
        "principal_total_usd": terms.principal_total_usd,
        "shares_outstanding": shares_outstanding,
        "shares_outstanding_ref": shares_outstanding_ref,
        "terms": Json(terms.model_dump(mode="json")),
        "valid_from": terms.issued_date,
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    return append_fact(conn, "fact_dilution", values)


def _filing_doc_url(cik: str | int, accession: str, doc: str) -> str:
    return f"{get_settings().sec_archives_base}/{int(cik)}/{accession.replace('-', '')}/{doc}"


def _is_convert_issuance(text: str) -> bool:
    """A convert ISSUANCE/closing 8-K (the definitive deal) — distinct from the pricing announcement,
    which says 'announced the pricing', not 'the Company issued'."""
    low = text.lower()
    return (
        "convertible senior notes" in low
        and "aggregate principal amount" in low
        and "the company issued" in low
    )


def discover_convert_issuance(
    client, cik: str | int, *, max_scan: int = 40
) -> tuple[ConvertTerms, str] | None:
    """Autonomously find a company's convertible-note ISSUANCE 8-K from just its CIK, and parse it.

    Scans recent 8-Ks (newest first) via the submissions API and returns (ConvertTerms, accession) for
    the first convert issuance found, else None. The platform finds the filing itself — no accession is
    handed in. Live/network (cache-first). The pricing-release enrichment (conversion premium +
    reference price) lives in a separate exhibit and is left to the seed/fixtures for now.
    """
    from ingest.edgar.submissions import fetch_submissions

    recent = fetch_submissions(client, cik).get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    accns = recent.get("accessionNumber", [])
    docs = recent.get("primaryDocument", [])
    scanned = 0
    for form, accession, doc in zip(forms, accns, docs):
        if form != "8-K" or not doc:
            continue
        scanned += 1
        if scanned > max_scan:
            break
        try:
            raw = client.get_text(_filing_doc_url(cik, accession, doc), f"forms/{accession}/{doc}")
        except Exception:
            continue  # a single unreadable filing shouldn't abort the scan
        if _is_convert_issuance(clean_filing_text(raw)):
            return parse_convert_terms(clean_filing_text(raw)), accession
    return None
