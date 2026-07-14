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
from domain.signal import SignalEvent
from ingest.edgar.converts import ConvertTerms
from signals.base import Detector, SignalPointInTimeData
from signals.common import fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_NAME = "dilution_clock"


def _live_converts(
    facts: list[dict[str, Any]], asof: date
) -> tuple[list[tuple[ConvertTerms, str]], float] | None:
    """The as-of-LIVE convertible-note terms (matured ones dropped) + the shares-outstanding basis, or
    ``None`` if there are no live converts or no shares. The single parse that BOTH the overhang number and
    the risk-signal label/provenance read from — one read of ``fact_dilution``, one source of truth.

    A name can have several live offerings, each carrying the shares basis known when that offering was
    recorded. Use the latest available basis by fact effective date (accession breaks same-day ties), never
    whichever row the storage engine happened to return last. The returned terms are ordered by that same
    deterministic key so live Postgres and replay DuckDB produce byte-identical labels/provenance.
    """
    parsed: list[tuple[date, str, ConvertTerms, float | None]] = []
    for f in facts:
        if f.get("instrument_kind") != "convertible_notes":
            continue
        t = ConvertTerms.model_validate(f["terms"])
        if t.maturity_date < asof:  # already matured -> no overhang
            continue
        accession = f["accession"]
        effective_date = f.get("valid_from") or t.issued_date or date.min
        raw_shares = f.get("shares_outstanding")
        shares = float(raw_shares) if raw_shares else None
        parsed.append((effective_date, accession, t, shares))
    if not parsed:
        return None
    parsed.sort(key=lambda row: (row[0], row[1]))
    shares_out = next((row[3] for row in reversed(parsed) if row[3] is not None), None)
    if shares_out is None:
        return None
    terms = [(t, accession) for _, accession, t, _ in parsed]
    return terms, shares_out


def _pct(terms: list[tuple[ConvertTerms, str]], shares_out: float) -> float | None:
    """The gross convert overhang as a % of shares outstanding (as-converted share count / shares)."""
    conv_shares = sum(t.principal_total_usd / 1000.0 * t.conversion_rate for t, _ in terms)
    if conv_shares <= 0:
        return None
    return 100.0 * conv_shares / shares_out


def overhang_pct(facts: list[dict[str, Any]], asof: date) -> float | None:
    """The RAW convert-overhang % — the SINGLE source of overhang, shared by the dilution risk-veto
    (``score`` below) and the Workbench dilution meter. The meter buckets on this real number, NEVER backed
    out of the clamped/normalized risk ``severity`` (which saturates at the severe threshold). ``None`` when
    there are no live converts / no shares — the meter renders that as "—", never a 0 (no fake zeros).
    """
    live = _live_converts(facts, asof)
    return _pct(*live) if live is not None else None


def score(
    facts: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: score a security's OUTSTANDING convertible-note overhang into a dilution RISK signal."""
    live = _live_converts(facts, asof)
    if live is None:
        return None
    terms, shares_out = live
    pct = _pct(terms, shares_out)
    if pct is None:
        return None

    severity = min(pct / cfg.dilution_overhang_severe_pct, 1.0) * cfg.risk_block_severity
    signal_score = round(min(severity, 0.95), 4)
    risk_read = (
        "severe overhang — withholds the Armed call on timing"
        if signal_score >= cfg.risk_block_severity
        else "structural overhang, below the Armed-call timing-veto threshold"
    )
    total_principal = sum(t.principal_total_usd for t, _ in terms)
    capped = any(t.capped_call_cost_usd is not None for t, _ in terms)
    cap_price = next((t.cap_price_usd for t, _ in reversed(terms) if t.cap_price_usd), None)
    coupon_zero = all(t.coupon_pct == 0.0 for t, _ in terms)
    due_year = min(t.maturity_date.year for t, _ in terms)
    issued = [t.issued_date for t, _ in terms if t.issued_date]

    offset = f", offset by a capped call (cap ~${cap_price:,.2f})" if capped and cap_price else ""
    label = (
        f"~${total_principal / 1e6:,.1f}M {'zero-coupon ' if coupon_zero else ''}convertible notes "
        f"due {due_year} — ~{pct:.1f}% potential share dilution{offset}; {risk_read}"
    )
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.RISK_SIGNAL,
        kind=Kind.DILUTION_RISK,
        score=signal_score,
        label=label,
        provenance=[
            source_provenance(
                "8-k",
                acc,
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
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Risk signal — convertible-note dilution overhang. Reads fact_dilution via point-in-time view."""
    return score(pit.dilution_facts(security_id), security_id, asof, cfg)


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
