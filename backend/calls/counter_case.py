from __future__ import annotations

from domain.signal import SignalEvent
from domain.thesis import Thesis


def deterministic_counter_case(
    thesis: Thesis,
    active_risk_signals: list[SignalEvent],
    missing: list[str],
    caveats: list[str] | tuple[str, ...] = (),
) -> str:
    """Deterministic fallback counter-case (CALL_LOGIC §8).

    Assembled from assembler caveats (e.g. a momentum-only confirmation), active risk signals, the
    thesis kill criteria, and the missing triggers. The LLM path (M4b) replaces this via the
    assembler's ``counter_case_fn`` hook; this is the guaranteed fallback and never alters
    state/verdict/grade/triggers.
    """
    parts: list[str] = list(caveats)
    if active_risk_signals:
        parts.append(
            "Active risk signals: " + "; ".join(r.label for r in active_risk_signals) + "."
        )
    if thesis.kill_criteria:
        parts.append("Kill criteria: " + "; ".join(k.text for k in thesis.kill_criteria) + ".")
    if missing:
        parts.append("Still missing: " + "; ".join(missing) + ".")
    if not parts:
        return "No documented counter-case: no active risk signals, kill criteria, or missing triggers."
    return " ".join(parts)
