from __future__ import annotations

from domain.config import CallConfig
from domain.signal import SignalEvent


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def confidence(
    fired_entry_triggers: list[SignalEvent],
    active_risk_signals: list[SignalEvent],
    cfg: CallConfig,
) -> float:
    """Calibrated, not loud (CALL_LOGIC §7).

    A noisy-OR combine of fired entry-trigger scores (more agreeing detectors -> higher, saturating),
    capped so a single-detector call never reads "high", minus a penalty per active risk signal
    (scaled by the risk's severity score). All thresholds come from ``cfg``.
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

    penalty = sum(cfg.risk_penalty_per_signal * _clamp(r.score) for r in active_risk_signals)
    return round(_clamp(base - penalty), 4)
