"""Dilution risk signal — convertible-note overhang (M4a-i).

A RISK signal (never an entry trigger): it feeds the counter-case and a confidence haircut, and a
*severe* overhang would withhold the Armed call on TIMING (the risk-veto) — but it never vetoes the
thesis. HIMS's converts are a mid-single-digit % overhang, largely offset by a capped call -> a LOW
score, non-blocking. Reads structured convert terms (deterministically parsed from the real 8-K) from
fact_dilution via the point-in-time view; the score scales with the gross overhang vs a config knob.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Kind, Role
from domain.signal import Provenance, SignalEvent
from ingest.edgar.converts import ConvertTerms
from signals.base import PointInTimeData


def score(
    facts: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: score a security's OUTSTANDING convertible-note overhang into a dilution RISK signal."""
    terms: list[tuple[ConvertTerms, str]] = []
    shares_out: float | None = None
    for f in facts:
        if f.get("instrument_kind") != "convertible_notes":
            continue
        t = ConvertTerms.model_validate(f["terms"])
        if t.maturity_date < asof:  # already matured -> no overhang
            continue
        terms.append((t, f["accession"]))
        if f.get("shares_outstanding"):
            shares_out = float(f["shares_outstanding"])

    conv_shares = sum(t.principal_total_usd / 1000.0 * t.conversion_rate for t, _ in terms)
    if not terms or not shares_out or conv_shares <= 0:
        return None

    overhang_pct = 100.0 * conv_shares / shares_out
    severity = min(overhang_pct / cfg.dilution_overhang_severe_pct, 1.0) * cfg.risk_block_severity
    total_principal = sum(t.principal_total_usd for t, _ in terms)
    capped = any(t.capped_call_cost_usd is not None for t, _ in terms)
    cap_price = next((t.cap_price_usd for t, _ in terms if t.cap_price_usd), None)
    coupon_zero = all(t.coupon_pct == 0.0 for t, _ in terms)
    due_year = min(t.maturity_date.year for t, _ in terms)
    issued = [t.issued_date for t, _ in terms if t.issued_date]

    offset = f", offset by a capped call (cap ~${cap_price:,.2f})" if capped and cap_price else ""
    label = (
        f"~${total_principal / 1e6:,.1f}M {'zero-coupon ' if coupon_zero else ''}convertible notes "
        f"due {due_year} — ~{overhang_pct:.1f}% potential share dilution{offset}; structural "
        f"overhang, not an entry blocker"
    )
    return SignalEvent(
        detector="dilution_clock",
        security_id=security_id,
        role=Role.RISK_SIGNAL,
        kind=Kind.DILUTION_RISK,
        grade=None,
        score=round(min(severity, 0.95), 4),
        fired=True,
        label=label,
        alpha_liveness_days=None,
        provenance=[
            Provenance(
                source="8-k",
                ref=acc,
                detail={
                    "principal_usd": t.principal_total_usd,
                    "conversion_rate": t.conversion_rate,
                    "cap_price_usd": t.cap_price_usd,
                    "shares_outstanding": shares_out,
                },
            )
            for t, acc in terms
        ],
        asof=max(issued) if issued else asof,
    )


def detect(
    pit: PointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Risk signal — convertible-note dilution overhang. Reads fact_dilution via point-in-time view."""
    return score(pit.dilution_facts(security_id), security_id, asof, cfg)
