"""Insider-selling cluster risk (Band 03, Slice S1) — the risk-side mirror of ``insider_conviction``.

A RISK signal (never an entry trigger): clustered DISCRETIONARY open-market sales (Form 4 code ``S``)
by insiders feed the counter-case and a setup-strength haircut — and nothing else. Per the
literature's asymmetry (people sell for many reasons — diversification, taxes, a house — while they
buy for one), sales are weak predictors: the score is CAPPED at ``insider_sell_max_score``, strictly
below ``risk_block_severity``, so a sell cluster can NEVER withhold an arm in v1 (operator decision 1,
2026-08-16). Grade-blind like dilution (``grade=None``, no ``dearm_grade`` — this is not a de-arm).

Screens (each one recall-safe on the CALL only — every row STAYS in ``fact_insider_txn`` and the raw
display tape, ``signals/display/insider_flow.py``, which shows sells unscreened; #9):

- **10b5-1 planned sales** (``aff_10b5_1 is True``) are near-noise and are screened OUT of the
  cluster, with the excluded count + $ NAMED in the label/provenance (#6). ``None`` (unknown — the
  pre-Dec-2022 norm) is KEPT: absence must never assert "planned" *or* "discretionary", and on the
  risk side keeping unknowns errs cautious (a louder counter-case). This is the first reader of the
  tri-state flag migration 0022 captured; the BUY detector still does not read it.
- **Issuer self-filings** (the company transacting its own stock — buyback/treasury/ADR mechanics)
  are never personal insider supply; same identity screen as the buy side.
- **Below-day's-low sales** are discounted registered secondaries — real supply, but a *different*
  risk family (the dilution tape's future job), not open-market selling pressure; set aside, named.
  No above-high screen (the buy side's forward-split rationale, mirrored).
- **Implausible rows** (> ``insider_max_plausible_txn_usd``) are bad source data (#3).

Freshness is DETECTOR-ENFORCED: the assembler never ages risk signals (``active_risk`` has no
liveness filter), and a sell cluster is the first event-shaped risk needing an explicit window — a
cluster whose anchor is older than ``insider_sell_liveness_days`` drops out of the re-derived stream
(``entry_signal_is_live``, the shared inclusive helper), so replay stays honest.

MASTER SWITCH (``insider_sell_enabled``, default OFF — operator decision 2): registered but
``detect`` no-ops until enabled, so with the live DEFAULT_CONFIG no sell event enters the stream /
counter-case / setup strength and every existing golden is byte-for-byte unchanged (the seed Form 4s
carry no code-S rows — doubly guaranteed). The pure ``score`` is UNGATED (the math stays testable);
``replay.run``'s ``--insider-sell`` / ``ALPHADECK_INSIDER_SELL`` force it on for the sig-lab pass
(the breakdown precedent), which finalizes the ``[PROPOSED]`` dials before the operator flips the
default.

The ``_is_senior`` / ``_norm_entity`` / ``_is_issuer_self`` helpers DUPLICATE the buy side
(``signals/insider_conviction.py``) with this pointer rather than extracting a shared module: this
slice explicitly does not touch the buy detector (its 10b5-1 screen is a separate, later decision) —
the display seam's documented duplicate-with-pointer pattern. If the buy side's screens recalibrate,
re-sync these by hand.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_NAME = "insider_sell"

# The screen buckets a code-S row can land in (exactly one each — deterministic attribution). Only
# KEPT rows form the cluster; the others are counted and named (label / provenance detail), never
# silently dropped (#6/#9).
_KEPT = "kept"
_PLANNED = "planned"  # aff_10b5_1 is True — a pre-planned 10b5-1 sale (near-noise)
_SELF = "self"  # the issuer filing on itself — never personal insider supply
_BELOW_LOW = "below_low"  # below the day's tape — a discounted secondary, a different risk family
_IMPLAUSIBLE = "implausible"  # physically-impossible $ — bad source data (#3)


def _is_senior(role: str | None, keywords: frozenset[str]) -> bool:
    # duplicated from insider_conviction._is_senior (see module docstring) — ONE definition of
    # "senior" in substance: both read cfg.insider_senior_role_keywords.
    if not role:
        return False
    r = role.lower()
    return any(k in r for k in keywords)


def _norm_entity(name: str | None) -> str:
    # duplicated from insider_conviction._norm_entity (see module docstring): casefold + collapse
    # whitespace + drop a trailing period; deliberately no corporate-suffix stripping (a self-filing
    # has the SAME name on both sides; stripping would widen the match and risk excluding a real,
    # non-self sale — recall-safe, #9).
    if not name:
        return ""
    return " ".join(name.strip().casefold().rstrip(".").split())


def _is_issuer_self(txn: dict[str, Any], issuer_name: str | None) -> bool:
    # duplicated from insider_conviction._is_issuer_self (see module docstring): CIK equality is
    # canonical (migration 0024); the name fallback covers rows ingested before the CIK capture. A
    # missing CIK / name mismatch KEEPS the row (the one-directional failure mode, #9).
    oc, ic = txn.get("rpt_owner_cik"), txn.get("issuer_cik")
    if oc and ic and str(oc).strip().lstrip("0") == str(ic).strip().lstrip("0"):
        return True
    filer = _norm_entity(txn.get("insider_name"))
    issuer = _norm_entity(txn.get("issuer_name") or issuer_name)
    return bool(filer) and filer == issuer


def _screen(
    txn: dict[str, Any],
    day_lows: dict[date, float],
    issuer_name: str | None,
    cfg: CallConfig,
) -> str:
    """Which bucket does this code-S row land in? Ordered most-structural first (implausible data,
    then identity, then price, then the plan flag) so a row tripping several screens has ONE
    deterministic attribution. Absent price context only disables the below-low screen — a sale with
    no day low is KEPT (recall-safe, #9: we cannot prove it was off-market). Note the direction of
    error is inverted vs the buy side: over-screening SELLS makes the platform MORE bullish, which is
    exactly why every set-aside is counted and named (#6)."""
    if float(txn.get("usd") or 0.0) > cfg.insider_max_plausible_txn_usd:
        return _IMPLAUSIBLE
    if _is_issuer_self(txn, issuer_name):
        return _SELF
    price = txn.get("price")
    low = day_lows.get(txn.get("valid_from"))
    if price is not None and low is not None:
        if float(price) < low * (1.0 - cfg.insider_offmarket_below_low_frac):
            return _BELOW_LOW
    if txn.get("aff_10b5_1") is True:  # only an EXPLICIT True screens; None (unknown) is kept
        return _PLANNED
    return _KEPT


def _score(n_distinct: int, total_usd: float, senior: bool, cfg: CallConfig) -> float:
    # Conservative, bounded — the buy-side _score idiom with the RISK CEILING as the clamp: a base
    # plus breadth (distinct sellers), a seniority bump, and dollar-total scaling, clamped to
    # insider_sell_max_score (< risk_block_severity — operator decision 1: a sell cluster haircuts
    # setup strength and feeds the counter-case; it can never withhold an arm in v1). The uncapped
    # sum tops out above the ceiling on purpose, so the clamp genuinely BINDS under an extreme
    # cluster (the ceiling test proves it) and lifting the ceiling later is a visible config diff.
    s = 0.25
    s += min(n_distinct, 4) * 0.05
    s += 0.10 if senior else 0.0
    s += min(total_usd / (cfg.insider_sell_min_usd * 8.0), 1.0) * 0.15
    return round(min(s, cfg.insider_sell_max_score), 4)


def score(
    txns: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
    day_lows: dict[date, float] | None = None,
    issuer_name: str | None = None,
) -> SignalEvent | None:
    """Pure: score a clustered discretionary open-market sell episode into a RISK SignalEvent (or None).

    Reads only sales (code 'S'); never fires on buys. The screens (see ``_screen``) are supplied by
    ``detect`` from the same point-in-time view the buy side uses; absent ``day_lows``/``issuer_name``
    only disables that one screen (nothing over-excluded). Cluster mechanics mirror the buy side:
    anchor on the most recent QUALIFYING (kept) sale — the event's ``asof``, so the card's event_date
    and the freshness clock agree — gather kept sales within the cohesion window before it, apply the
    floors (total $, distinct sellers, senior-required), and emit only while the anchor is inside the
    freshness window (a stale cluster drops out of the re-derived stream; the assembler never ages
    risks). The screened counts named in the label/provenance are framed on the SAME episode window
    (planned/self/below-low/implausible rows falling inside [anchor - window, anchor]) — the work
    behind THIS cluster, not all history. UNGATED by the master switch (``detect`` holds the gate).
    """
    lows = day_lows or {}
    rows = [
        t
        for t in txns
        if t.get("txn_code") == "S" and t.get("valid_from") is not None and t["valid_from"] <= asof
    ]
    if not rows:
        return None
    buckets: dict[str, list[dict[str, Any]]] = {
        _KEPT: [],
        _PLANNED: [],
        _SELF: [],
        _BELOW_LOW: [],
        _IMPLAUSIBLE: [],
    }
    for t in rows:
        buckets[_screen(t, lows, issuer_name, cfg)].append(t)
    kept = buckets[_KEPT]
    if not kept:
        return None

    # FIRE date = the most recent kept sale (the anchor); the cluster = the kept sales within the
    # cohesion window before it — one episode of selling (the buy side's convention, so the card's
    # event_date reads the cluster's latest sale). Freshness floor on the anchor: past the liveness
    # window the cluster drops out entirely (detector-enforced; the assembler never ages risks).
    anchor = max(t["valid_from"] for t in kept)
    if not entry_signal_is_live(anchor, cfg.insider_sell_liveness_days, asof):
        return None
    floor = anchor - timedelta(days=cfg.insider_sell_cluster_window_days)

    def _in_window(t: dict[str, Any]) -> bool:
        return floor <= t["valid_from"] <= anchor

    cluster = [t for t in kept if _in_window(t)]
    total_usd = float(sum(float(t.get("usd") or 0) for t in cluster))
    if total_usd < cfg.insider_sell_min_usd:
        return None
    distinct = {t.get("insider_name") for t in cluster if t.get("insider_name")}
    if len(distinct) < cfg.insider_sell_min_distinct:
        return None  # "clustered" is the load-bearing word — one big sale is the many-reasons case
    senior = any(
        _is_senior(t.get("insider_role"), cfg.insider_senior_role_keywords) for t in cluster
    )
    if cfg.insider_sell_require_senior and not senior:
        return None

    # the named screens, framed on the SAME episode window — the work shown beside the number (#6)
    planned_w = [t for t in buckets[_PLANNED] if _in_window(t)]
    planned_usd = float(sum(float(t.get("usd") or 0) for t in planned_w))
    below_low_w = [t for t in buckets[_BELOW_LOW] if _in_window(t)]
    self_w = [t for t in buckets[_SELF] if _in_window(t)]
    implausible_w = [t for t in buckets[_IMPLAUSIBLE] if _in_window(t)]
    unknown_plan = [t for t in cluster if t.get("aff_10b5_1") is None]

    label = (
        f"{len(distinct)} insider{'s' if len(distinct) != 1 else ''}"
        f"{' incl. senior officer' if senior else ''} sold "
        f"${total_usd:,.0f} open-market (code S) across {len(cluster)} txns"
    )
    if planned_w:
        label += (
            f"; {len(planned_w)} planned-sale (10b5-1) txn{'s' if len(planned_w) != 1 else ''} "
            f"(${planned_usd:,.0f}) screened"
        )
    if below_low_w:
        label += (
            f"; {len(below_low_w)} below-day-low txn{'s' if len(below_low_w) != 1 else ''} "
            f"set aside (not open-market)"
        )

    # the work behind the number, stamped on every accession ref (the dilution shared-detail idiom)
    detail = {
        "total_usd": round(total_usd, 2),
        "distinct_sellers": len(distinct),
        "txn_count": len(cluster),
        "senior_in_cluster": senior,
        "anchor": anchor.isoformat(),
        "cluster_window_days": cfg.insider_sell_cluster_window_days,
        "planned_screened": len(planned_w),
        "planned_screened_usd": round(planned_usd, 2),
        "unknown_plan_kept": len(unknown_plan),
        "self_filings_screened": len(self_w),
        "below_low_set_aside": len(below_low_w),
        "implausible_dropped": len(implausible_w),
    }
    by_accession = {t["accession"] for t in cluster if t.get("accession")}
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.RISK_SIGNAL,
        kind=Kind.INSIDER_SELL,
        grade=None,  # a risk is ungraded; and no dearm_grade — this is not a de-arm
        score=_score(len(distinct), total_usd, senior, cfg),
        label=label,
        alpha_liveness_days=None,  # matching dilution/breakdown: freshness is enforced ABOVE
        provenance=[source_provenance("form4", acc, detail=detail) for acc in sorted(by_accession)],
        asof=anchor,
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Risk signal — clustered discretionary insider selling. Reads Form 4 sales via the
    point-in-time view; from the SAME as-of view (no lookahead) it builds the below-low price screen
    and the issuer-self identity screen, exactly like the buy side's ``detect``.

    MASTER SWITCH (default OFF — operator decision 2): no-ops when ``cfg.insider_sell_enabled`` is
    off, so with the live DEFAULT_CONFIG NO sell event enters the stream / counter-case / setup
    strength and every existing golden is byte-for-byte unchanged. Stays REGISTERED (the
    registry-list test expects it) — it just emits nothing until the sig-lab pass measures the
    ``[PROPOSED]`` dials and the operator flips the default. The pure ``score`` is UNGATED."""
    if not cfg.insider_sell_enabled:
        return None
    day_lows = {
        b["d"]: float(b["low"]) for b in pit.price_history(security_id) if b.get("low") is not None
    }
    return score(
        pit.insider_txns(security_id),
        security_id,
        asof,
        cfg,
        day_lows=day_lows,
        issuer_name=pit.security_name(security_id),
    )


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
