"""Share-count creep / ATM detection (Band 03, Slice S4) — the slow-motion dilution risk signal.

An **at-the-market program quietly draining into the tape** shows up as shares outstanding rising
quarter-over-quarter with no loud raise event to explain it: ATM sales are registered (S-3 shelf +
424B5) and file NO per-sale 8-K — that is exactly what makes the drip quiet. This detector reads the
quarterly XBRL shares series (``fact_fundamentals``, the S4 metric-key extension of the §2.2 revenue
ingest) and fires a RISK signal on a **sustained trailing drip**: every quarter-over-quarter step in
the window strictly positive, cumulative rise at/above the configured floor. A deterministic
companyfacts parse end to end — the LLM never fires (#3).

``kind=DILUTION_RISK`` — REUSED, not a new Kind (operator-confirmed 2026-08-17): share-count creep
and ``dilution_clock``'s convert overhang are two lenses on ONE phenomenon — REALIZED issuance vs
POTENTIAL conversion — where the per-risk-family kind splits (INSIDER_SELL, CORPORATE_RISK) separated
genuinely different phenomena. Grade-blind like dilution (``grade=None``, no ``dearm_grade`` — this
is not a de-arm); no contract regen (the Kind enum is in the OpenAPI schema; reuse leaves it be).

**The concept ladder (fork 2, operator-confirmed).** Three XBRL share concepts are STORED (evidence
is complete, #9 — measured on the real 475-name basket, no single concept covers everyone); the
detector walks a fixed AVAILABILITY ladder — balance-sheet ``shares_out_xbrl`` (period-end aligned,
the cleanest QoQ cadence) → cover-page ``shares_out_cover_xbrl`` (dei; broadest presence, irregular
"latest practicable date" spacing) → ``shares_issued_xbrl`` — and takes the FIRST concept with a
fresh trailing chain of consecutive quarters. The verdict (ceiling / positivity / floor) then comes
from THAT series alone — **never concept-shopping past a computable window's honest "no creep"**, and
never mixing concepts inside one computation (issued vs outstanding differ by treasury stock).
Accepted consequence, documented: a concept-specific artifact in the preferred series can mask a
clean fallback — visible in the lab, re-orderable in code with zero re-ingest.

**The pair ceiling (measurement-driven).** The real basket's top single-quarter "rises" are not ATMs:
forward splits, recaps, and XBRL scale artifacts (a literal 1-share row; thousands-vs-units errors
reading +100,000%). A step at/above ``share_creep_pair_ceiling_pct`` makes the window NOT-creep — the
detector declines rather than mislabeling a structural event as ATM drainage. A one-off jump with
flat neighbors (the discrete explained-raise shape) is likewise declined by the strict-positivity
drip rule: a flat or shrinking quarter breaks the run.

**Named recall holes (#9 — honest, not silent).** Measured on the real basket: FPIs/ADRs on 20-F
annual cadence, fresh IPOs (series too short), and dual-class filers whose per-class dei dimensions
companyfacts DROPS (HIMS: no usable series at all — its convertible notes remain ``dilution_clock``'s
territory, potential-not-realized dilution) have no quarterly series; the detector DECLINES on them,
never fabricates. The annual-YoY and cover-parse extensions are deferred, named in the S4 plan.

Freshness is DETECTOR-ENFORCED (the assembler never ages risk signals): the chain must be anchored on
a point whose ``filed`` is within ``share_creep_liveness_days`` of asof, so a gone-dark series drops
out of the re-derived stream and replay stays honest. Fire-date-anchored at the newest point's
``filed`` (the honest knowability, #1); provenance carries every window point's accession + count and
the computation summary (#6).

MASTER SWITCH (``share_creep_enabled``) — **DEFAULT ON since the operator flip of 2026-08-19**, on the
sig-lab pass (shipped OFF behind the insider_sell / corporate-pair precedent). Measured safe on real
prod data before the flip: 61 fires across 5 theses with ZERO arm-withholdings — and sub-veto BY
CONSTRUCTION (``share_creep_score`` 0.50 sits strictly below ``risk_block_severity`` 0.70). ``detect``
remains GATED: set the dial False and it no-ops (registered either way). The pure ``score`` is UNGATED;
``replay.run``'s ``--share-creep`` / ``ALPHADECK_SHARE_CREEP`` set it explicitly for the backtest.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_NAME = "share_creep"

# The fact_fundamentals metric_keys the S4 ingest writes (ingest.fundamentals.SHARES_*_METRIC) — kept
# literals here so the pure detector needs no ingest import (the _REVENUE_METRIC convention), in the
# LADDER order the availability walk tries them.
_CONCEPT_LADDER: tuple[str, ...] = (
    "shares_out_xbrl",
    "shares_out_cover_xbrl",
    "shares_issued_xbrl",
)
_CONCEPT_LABELS: dict[str, str] = {
    "shares_out_xbrl": "balance-sheet series",
    "shares_out_cover_xbrl": "cover-page series",
    "shares_issued_xbrl": "issued-shares series",
}

# Consecutive-quarter matching windows (period_end proximity) — the SAME rationale as
# revenue_acceleration's: ~1 quarter apart within tolerance; a wider gap is NOT a consecutive quarter
# (the chain declines across a hole rather than comparing non-adjacent periods — honest, #9). The
# walk skips sub-quarter extras (irregular dei cover dates), mirroring ``_prior_quarter``.
_QUARTER_MIN_GAP_DAYS = 60
_QUARTER_MAX_GAP_DAYS = 130


@dataclass(frozen=True)
class _P:
    """One as-of share-count instant (already the latest version per period_end via the bitemporal
    read — a restatement supersedes). ``filed`` = the knowability / freshness-anchor date."""

    period_end: date
    value: float
    filed: date
    accession: str


def _points(rows: list[dict[str, Any]], metric_key: str) -> list[_P]:
    """The share-count points for ONE concept from the as-of fundamentals rows, sorted by period_end.
    Other metric keys (revenue, the sibling share concepts) are skipped; a row missing
    value/period_end/valid_from — or a non-positive count (a literal 0-share XBRL row: garbage that
    can never be an honest ratio base, #3) — is skipped. The INGEST stores such rows regardless (the
    tape is honest); this screen is the detector's read-side cut."""
    out: list[_P] = []
    for r in rows:
        if r.get("metric_key") != metric_key:
            continue
        val, pe, vf = r.get("value"), r.get("period_end"), r.get("valid_from")
        if val is None or pe is None or vf is None:
            continue
        v = float(val)
        if v <= 0:
            continue
        out.append(_P(period_end=pe, value=v, filed=vf, accession=r.get("accession") or ""))
    out.sort(key=lambda p: p.period_end)
    return out


def _prior_point(points: list[_P], e: date) -> _P | None:
    """The immediately-preceding CONSECUTIVE quarterly point before period_end ``e`` (gap within one
    quarter, latest such wins) — revenue_acceleration's ``_prior_quarter`` shape, so sub-quarter
    extras (irregular dei covers) are skipped and a genuine hole declines the chain."""
    best: _P | None = None
    for p in points:
        d = (e - p.period_end).days
        if _QUARTER_MIN_GAP_DAYS <= d <= _QUARTER_MAX_GAP_DAYS and (
            best is None or p.period_end > best.period_end
        ):
            best = p
    return best


def _trailing_chain(points: list[_P], n_pairs: int) -> list[_P] | None:
    """The trailing chain of ``n_pairs`` consecutive QoQ pairs ENDING at the newest point (creep is
    about NOW), or None when the cadence breaks — ascending ``n_pairs + 1`` points."""
    if not points:
        return None
    chain = [points[-1]]
    while len(chain) < n_pairs + 1:
        prev = _prior_point(points, chain[0].period_end)
        if prev is None:
            return None
        chain.insert(0, prev)
    return chain


def score(
    rows: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: the as-of fundamentals rows -> ONE share-creep RISK SignalEvent (or None).

    The ladder walk is an AVAILABILITY gate only (enough points, consecutive cadence, fresh anchor);
    the first concept passing it is the name's series, and the VERDICT — pair ceiling, strict
    positivity, cumulative floor — evaluates on that series alone (no concept shopping). UNGATED by
    the master switch (``detect`` holds the gate)."""
    chain: list[_P] | None = None
    metric_key = ""
    for key in _CONCEPT_LADDER:
        candidate = _trailing_chain(_points(rows, key), cfg.share_creep_window_quarters)
        if candidate is None:
            continue
        if not entry_signal_is_live(candidate[-1].filed, cfg.share_creep_liveness_days, asof):
            continue  # the series has gone dark — a stale concept asserts nothing about today
        chain, metric_key = candidate, key
        break
    if chain is None:
        return None

    pair_pcts = [(b.value / a.value - 1.0) * 100.0 for a, b in zip(chain, chain[1:])]
    if any(p >= cfg.share_creep_pair_ceiling_pct for p in pair_pcts):
        return None  # a split / recap / XBRL scale artifact — a structural event, not ATM creep
    if any(p <= 0.0 for p in pair_pcts):
        return None  # not a persistent drip: a flat or shrinking quarter breaks the run
    cum_pct = (chain[-1].value / chain[0].value - 1.0) * 100.0
    if cum_pct < cfg.share_creep_cum_min_pct:
        return None
    max_pct = max(pair_pcts)

    risk_read = (
        "severe — withholds the Armed call on timing"
        if cfg.share_creep_score >= cfg.risk_block_severity
        else "counter-case input, below the timing-veto threshold"
    )
    n = len(chain) - 1
    label = (
        f"Share count creeping — +{cum_pct:.1f}% over {n} quarters "
        f"({chain[0].value:,.0f} → {chain[-1].value:,.0f} by {chain[-1].period_end}, "
        f"{_CONCEPT_LABELS[metric_key]}); largest single-quarter step +{max_pct:.1f}% — the "
        f"quiet-issuance (ATM-style) dilution shape; {risk_read}"
    )
    provenance = []
    for i, p in enumerate(chain):
        detail: dict[str, Any] = {
            "metric": metric_key,
            "period_end": str(p.period_end),
            "shares": p.value,
            "filed": p.filed.isoformat(),
        }
        if i == len(chain) - 1:  # the anchor carries the computation summary — the work shown (#6)
            detail.update(
                {
                    "cum_pct": round(cum_pct, 4),
                    "max_pair_pct": round(max_pct, 4),
                    "window_quarters": n,
                }
            )
        provenance.append(source_provenance("xbrl", p.accession, detail=detail))
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.RISK_SIGNAL,
        kind=Kind.DILUTION_RISK,
        grade=None,  # a risk is ungraded; and no dearm_grade — this is not a de-arm
        score=cfg.share_creep_score,
        label=label,
        alpha_liveness_days=None,  # matching dilution/insider_sell: freshness is enforced ABOVE
        provenance=provenance,
        asof=chain[-1].filed,
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Risk signal — quarterly share-count creep (ATM detection). Reads ``fact_fundamentals`` via the
    point-in-time view (the SAME accessor the revenue detector uses — zero protocol changes). MASTER
    SWITCH — **DEFAULT ON since 2026-08-19**: gated on ``cfg.share_creep_enabled``, so setting it
    False still no-ops the detector."""
    if not cfg.share_creep_enabled:
        return None
    return score(pit.fundamentals_facts(security_id), security_id, asof, cfg)


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
