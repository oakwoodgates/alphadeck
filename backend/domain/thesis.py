from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field

from domain.base import DomainModel
from domain.enums import Archetype


class BasketMember(DomainModel):
    ticker: str
    role: str  # the name's role in the thesis (operator/decomposition prose)
    archetype: Archetype
    security_id: UUID | None = None
    detail: str | None = None  # the board/cockpit "met" cell (e.g. "mkt $1.2B")


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
    evidence: list[Evidence] = Field(default_factory=list)
    catalysts: list[Catalyst] = Field(default_factory=list)
    kill_criteria: list[KillCriterion] = Field(default_factory=list)
    position: Position | None = None
