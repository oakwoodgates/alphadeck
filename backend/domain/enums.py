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
    CATALYST = (
        "catalyst"  # Key-1 conviction for theme theses (deterministic/ratified commitment, #10)
    )
    # Key-1 FALLBACK: an operator-ratified, thesis-level theme conviction that arms an otherwise-
    # confirmed basket member as a disciplined STARTER (M5b). The weaker key — capped at flip.
    THEME_CONVICTION = "theme_conviction"
    TECHNICAL_BREAKOUT = "technical_breakout"
    LAGGARD = "laggard"
    SQUEEZE = "squeeze"
    ETF_LAUNCH = "etf_launch"
    ETF_FLOW = "etf_flow"
    DILUTION_RISK = "dilution_risk"


class CatalystType(StrEnum):
    """The catalyst nature, where one applies (optional on a signal; extensible)."""

    REGULATORY = "regulatory"  # e.g. an NRC license action
    PROMOTER_ATTENTION = "promoter_attention"
    CLINICAL_READOUT = "clinical_readout"
    PERSONNEL = "personnel"
    EARNINGS = "earnings"
    CONTRACT = "contract"  # e.g. a power-purchase / offtake agreement
    GOV_FUNDING = "gov_funding"  # e.g. a DOE loan guarantee / grant award


class Archetype(StrEnum):
    """A basket member's role in expressing the thesis."""

    LEADER = "leader"
    HIGH_BETA = "high_beta"
    LOTTO = "lotto"
    SHOVEL = "shovel"
    ADJACENT = "adjacent"  # off-thesis / impure exposure — surfaced and FLAGGED, never hidden (Workbench hero)
    FUND = "fund"  # an ETF: the low-torque safe-exposure sleeve expression of the thesis


class Authorship(StrEnum):
    """Who placed a basket member in its value-chain segment (the Workbench authorship seam).

    The MVP writes only the operator values; ``SYSTEM_DRAFTED`` is present now so the seam already
    supports the bounded LLM drafter (the next slice) without a later schema change.
    """

    SYSTEM_DRAFTED = "system_drafted"  # LLM-drafted placement (post-MVP); the operator ratifies it
    OPERATOR_SET = "operator_set"  # the operator placed it
    OPERATOR_EDITED = "operator_edited"  # the operator edited a drafted placement
