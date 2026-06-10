from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field, model_validator

from domain.base import DomainModel
from domain.enums import Archetype, Authorship


class Segment(DomainModel):
    """A link in the thesis's value chain (the Workbench decomposition) — STRUCTURE, not a score.

    The per-segment candidate count is DERIVED on read (``len`` of members in the link), never stored.
    """

    label: str  # the link, e.g. "Reactor developers"
    descriptor: str | None = None  # the operator's per-segment tag, e.g. "catalyst-rich"


class BasketMember(DomainModel):
    ticker: str
    role: str  # the name's role in the thesis (operator/decomposition prose)
    archetype: Archetype
    security_id: UUID | None = None
    detail: str | None = None  # the board/cockpit "met" cell (e.g. "mkt $1.2B")
    segment: str | None = None  # the value-chain link this name sits in (a Thesis.segments label)
    authored_by: Authorship = (
        Authorship.OPERATOR_SET
    )  # who placed it (the Workbench authorship seam)


class Evidence(DomainModel):
    """Immutable reference to a filing / data point."""

    id: UUID
    kind: str  # display label e.g. "8-K", "FORM 4", "DATA", "EO"
    label: str
    ref: str  # URL / EDGAR accession
    date_label: str | None = None


class Catalyst(DomainModel):
    id: UUID
    label: str
    kind: str | None = None  # display kind e.g. "earnings", "regulatory"
    when_date: date | None = None  # drives the catalyst_surface filter; None = fuzzy/undated
    when_label: str | None = None  # display string e.g. "~3wk", "Q3"


class KillCriterion(DomainModel):
    id: UUID
    text: str


class Position(DomainModel):
    """Populated once the operator logs a fill — its presence drives the Managing state."""

    entry_price: float | None = None
    current_price: float | None = None
    opened_on: date | None = None


class Thesis(DomainModel):
    """First-class object (invariant #2): narrative, basket, evidence, catalysts, kill criteria, expression."""

    id: UUID
    name: str
    narrative: str  # the operator's words, preserved
    tenant_id: UUID | None = None
    parent_id: UUID | None = None  # nullable now; the umbrella/segment hierarchy lands in M5
    ticker: str | None = None
    basket: list[BasketMember] = Field(default_factory=list)
    segments: list[Segment] = Field(
        default_factory=list
    )  # the value-chain links (Workbench structure)
    evidence: list[Evidence] = Field(default_factory=list)
    catalysts: list[Catalyst] = Field(default_factory=list)
    kill_criteria: list[KillCriterion] = Field(default_factory=list)
    position: Position | None = None

    @model_validator(mode="after")
    def _segments_consistent(self) -> "Thesis":
        """Every placed ``member.segment`` must name one of the thesis's segments — the chain stays
        internally consistent (a name can't sit in a link that isn't in the chain)."""
        labels = {s.label for s in self.segments}
        for m in self.basket:
            if m.segment is not None and m.segment not in labels:
                raise ValueError(
                    f"basket member {m.ticker!r} placed in unknown segment {m.segment!r}"
                )
        return self
