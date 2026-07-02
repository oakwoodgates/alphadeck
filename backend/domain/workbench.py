"""Workbench scoring domain models (Slice 3) — the per-name scored read.

Scores RE-DERIVE on read (Option B); nothing here is persisted. A ``ScoredFigure`` carries the 0-4 pip
(``None`` = no data, rendered "—"), the raw value (for the "behind the scores" chip), and the provenance
behind it. ``ScoredMember`` bundles a basket member's four meters + the market-cap figure + a deterministic
fit label.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from domain.base import DomainModel
from domain.enums import Archetype
from domain.signal import Provenance


class ScoredFigure(DomainModel):
    """One meter (or figure) for a name. ``pips`` is the 0-4 bucket; ``None`` means no data ("—", never a
    fake 0). ``value`` is the raw figure (mix %, runway months, overhang %, market-cap USD) for the chip.
    ``market_cap`` is a FIGURE, not a meter — it carries ``value`` only, ``pips`` stays ``None``."""

    pips: int | None = None
    value: float | None = None
    provenance: list[Provenance] = Field(default_factory=list)


class ScoredMember(DomainModel):
    """A basket member scored for the Workbench — the four data meters + the market-cap figure + a
    deterministic fit label. Re-derived on read; never persisted. Only members with a resolved
    ``security_id`` are scored (no facts to read otherwise)."""

    security_id: UUID
    archetype: Archetype
    # A DERIVED-DEFAULT archetype recommendation (Slice 4, INVARIANT #10): deterministic, from market cap +
    # purity. Display-only — the operator confirms/overrides; it is NEVER auto-applied to ``archetype`` and
    # never promoted onto a ``BasketMember``. ``None`` = abstain (no facts yet, or a relational role the rule
    # won't guess — shovel / fund). The LLM does not touch this (no model, no number — #1/#3).
    archetype_hint: Archetype | None = None
    segment: str | None = None
    purity: ScoredFigure
    runway: ScoredFigure
    catalysts: ScoredFigure
    dilution: ScoredFigure
    market_cap: ScoredFigure
    fit: str  # deterministic label derived from the pips (the auto-drafted fit PROSE is the LLM's job, Slice 5)
    # HONEST CONFIDENCE (SURFACE Slice 1a): how many of the fact-backed meters (purity / runway / market cap)
    # have NO operator-confirmed value yet — each has an on-demand estimate the operator can confirm/override.
    # A readiness signal ("rests on N unconfirmed"), NEVER a scoring input: the meters themselves stay
    # confirmed-only (blank until ratified); this just counts the blanks so the surface can flag them honestly.
    unconfirmed_estimates: int = 0
