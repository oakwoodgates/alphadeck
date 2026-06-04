from __future__ import annotations

from domain.base import DomainModel
from domain.enums import Kind


class CallConfig(DomainModel):
    """Every ``TODO(operator)`` threshold lives here — never hardcoded in the assembler.

    Start conservative. These are starting defaults to calibrate against real calls
    (CALL_LOGIC §2/§3/§7), not claims of precision. The block-vs-penalize behavior of the
    risk-veto is fixed; only its severity threshold is calibrated.
    """

    # --- state transitions (§2) ---
    warming_min_entry_triggers: int = 1
    arming_requires_confirmation: bool = True  # two-key gate: conviction warms, confirmation arms
    conviction_kinds: frozenset[Kind] = frozenset({Kind.INSIDER})
    confirmation_kinds: frozenset[Kind] = frozenset({Kind.TECHNICAL_BREAKOUT, Kind.LAGGARD})

    # --- risk-veto (§2) ---
    risk_block_severity: float = (
        0.7  # a fired risk signal with score >= this withholds the Armed call
    )

    # --- confidence (§7) ---
    single_detector_cap: float = 0.80  # a one-detector call never reads "high"
    risk_penalty_per_signal: float = 0.10  # confidence cut per active risk signal (scaled by score)

    # --- dilution severity (used by the dilution detector in M4a) ---
    dilution_block_runway_months: float = 6.0


DEFAULT_CONFIG = CallConfig()
