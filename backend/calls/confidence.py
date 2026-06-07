from __future__ import annotations

from domain.config import CallConfig
from domain.signal import SignalEvent


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


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
    """
    if not fired_entry_triggers:
        base = 0.0
    else:
        prod = 1.0
        for e in fired_entry_triggers:
            prod *= 1.0 - _clamp(e.score)
        base = 1.0 - prod

    if len(fired_entry_triggers) <= 1:
        base = min(base, cfg.single_detector_cap)
    if is_starter:
        base = min(base, cfg.starter_confidence_cap)

    penalty = sum(cfg.risk_penalty_per_signal * _clamp(r.score) for r in active_risk_signals)
    return round(_clamp(base - penalty), 4)
