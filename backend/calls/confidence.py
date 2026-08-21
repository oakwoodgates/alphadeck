from __future__ import annotations

from domain.config import CallConfig
from domain.enums import Kind
from domain.signal import SignalEvent


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _max_score_by_kind(events: list[SignalEvent]) -> dict[Kind, float]:
    """Collapse events to ONE representative score per ``kind`` — its STRONGEST. Correlated reads of the
    same phenomenon share a kind (volume_breakout + breakout_52w both emit Kind.TECHNICAL_BREAKOUT — two
    lenses on one price move), so keeping the family's single best score stops the noisy-OR from treating
    them as independent evidence; genuinely different kinds still contribute separately. MIRRORS the grade
    path (grading.call_grade = ``max(graded, key=grade_rank)`` over a kind's fired triggers)."""
    by_kind: dict[Kind, float] = {}
    for e in events:
        s = _clamp(e.score)
        if e.kind not in by_kind or s > by_kind[e.kind]:
            by_kind[e.kind] = s
    return by_kind


def confidence(
    fired_entry_triggers: list[SignalEvent],
    active_risk_signals: list[SignalEvent],
    cfg: CallConfig,
    is_starter: bool = False,
) -> float:
    """Calibrated, not loud (CALL_LOGIC §7).

    A noisy-OR combine of fired entry-trigger scores (more agreeing detectors -> higher, saturating),
    capped so a single-detector call never reads "high", and capped lower still for a STARTER — a call
    whose entry grade is flip because EITHER key is weak (an unconfirmed breakout OR a provisional
    conviction). The starter cap is essential: noisy-OR would otherwise let the ONE strong key float an
    enter-small call to a loud number, ignoring the weak key. Minus a penalty per active risk signal.

    Correlated confirmations are collapsed to ONE contribution BEFORE the noisy-OR: two entry triggers
    sharing a ``kind`` are reads of the SAME move (volume_breakout + breakout_52w both fire
    TECHNICAL_BREAKOUT), and the noisy-OR's independence assumption would otherwise double-count them and
    saturate confidence toward ~0.99. Each kind contributes once, at its strongest — so the
    "single-detector" cap keys on the number of distinct detector-FAMILIES (kinds), not raw trigger count.
    """
    by_kind = _max_score_by_kind(fired_entry_triggers)
    if not by_kind:
        base = 0.0
    else:
        prod = 1.0
        for s in by_kind.values():
            prod *= 1.0 - s
        base = 1.0 - prod

    if len(by_kind) <= 1:
        base = min(base, cfg.single_detector_cap)
    if is_starter:
        base = min(base, cfg.starter_confidence_cap)

    penalty = sum(cfg.risk_penalty_per_signal * _clamp(r.score) for r in active_risk_signals)
    return round(_clamp(base - penalty), 4)
