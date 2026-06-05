from __future__ import annotations

from enum import StrEnum


class State(StrEnum):
    """Thesis lifecycle (a loop, not a ratchet)."""

    INCUBATING = "incubating"
    WARMING = "warming"
    ARMED = "armed"
    MANAGING = "managing"


class Verdict(StrEnum):
    WATCHING = "watching"
    NOT_YET = "not_yet"
    FLIP_ONLY = "flip_only"  # a flip thesis: small, short-dated, do-not-hold
    STARTER_ENTRY = (
        "starter_entry"  # core thesis, starter-sized entry (confirmation not volume-backed)
    )
    CORE_ENTRY = "core_entry"  # core thesis, volume-confirmed: build to core size
    MANAGING = "managing"


class Grade(StrEnum):
    FLIP = "flip"  # fast, sentiment-driven; trade small/short-dated; do not hold
    CORE = "core"  # structural; build the position


class Role(StrEnum):
    """Only entry triggers can turn the two keys; risk signals feed counter-case/confidence."""

    ENTRY_TRIGGER = "entry_trigger"
    RISK_SIGNAL = "risk_signal"


class Kind(StrEnum):
    """What produced the signal (extensible)."""

    INSIDER = "insider"
    TECHNICAL_BREAKOUT = "technical_breakout"
    LAGGARD = "laggard"
    SQUEEZE = "squeeze"
    ETF_LAUNCH = "etf_launch"
    ETF_FLOW = "etf_flow"
    DILUTION_RISK = "dilution_risk"


class CatalystType(StrEnum):
    """The catalyst nature, where one applies (optional on a signal; extensible)."""

    REGULATORY = "regulatory"
    PROMOTER_ATTENTION = "promoter_attention"
    CLINICAL_READOUT = "clinical_readout"
    PERSONNEL = "personnel"
    EARNINGS = "earnings"
    CONTRACT = "contract"


class Archetype(StrEnum):
    """A basket member's role in expressing the thesis."""

    LEADER = "leader"
    HIGH_BETA = "high_beta"
    LOTTO = "lotto"
    SHOVEL = "shovel"
