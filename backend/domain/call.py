from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field

from domain.base import DomainModel
from domain.enums import Grade, Kind, State, Verdict
from domain.signal import Provenance
from domain.thesis import Catalyst


class KeyState(DomainModel):
    """One of the two keys (Conviction / Confirmation) rendered on the call card."""

    turned: bool
    label: str
    detail: str | None = None


class TriggerRef(DomainModel):
    label: str
    kind: Kind
    grade: Grade | None = None
    security_id: UUID  # the security this trigger fired on — drives issuer-CIK URL resolution
    sources: list[Provenance] = Field(default_factory=list)


class CallCard(DomainModel):
    """The opinionated, auditable call — a pure function of (thesis, events, asof), recomputed on read.

    This is the domain shape. The API response contract (CallCardResponse, M3) is kept separate so the
    wire shape can resolve provenance to URLs without coupling the frontend to the domain schema.
    """

    thesis_id: UUID
    asof: date
    state: State
    verdict: Verdict
    conviction_grade: Grade | None = None  # the thesis quality (the conviction key)
    entry_grade: Grade | None = None  # the action/size grade (the weaker key) — drives the verdict
    armed_security_id: UUID | None = None  # the co-located security that armed (None unless Armed)
    expression: str
    exit_by: date | None = None  # the HOLD horizon (conviction key); drives the catalyst surface
    arm_until: date | None = None  # the ENTRY window (confirmation key); the arm lapses past this
    catalyst_surface: list[Catalyst] = Field(default_factory=list)
    confidence: float = Field(ge=0.0, le=1.0)
    key_conviction: KeyState
    key_confirmation: KeyState
    triggers_fired: list[TriggerRef] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    counter_case: str = ""
    safe_sleeve: str | None = None
