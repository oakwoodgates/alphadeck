from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from domain.base import DomainModel
from domain.enums import CatalystType, Grade, Kind, Role


class Provenance(DomainModel):
    """A pointer to the source + the computation behind a signal.

    Invariant #6 (show the work): every trigger traces to a computation, never the model's say-so.
    """

    source: str  # "form4" | "price" | "xbrl" | ...
    ref: str  # EDGAR accession / "price:CCJ:2026-06-02"
    evidence_id: UUID | None = None  # FK to an evidence row once materialized
    detail: dict[str, Any] = Field(default_factory=dict)  # the computation inputs


class SignalEvent(DomainModel):
    """A detector's output: f(point_in_time_data, security_id, asof, cfg) -> SignalEvent (CALL_LOGIC §1).

    Detectors are pure: no implicit "now" — time is always the `asof` parameter.
    """

    detector: str
    security_id: UUID
    role: Role
    kind: Kind
    type: CatalystType | None = None
    grade: Grade | None = None  # None when role=risk_signal
    # A DE-ARM targeting grade — set ONLY on a ``breakdown`` risk signal (M4a, CALL_LOGIC §2). A
    # structural break de-arms an armed entry of THIS grade only (a core-breakdown de-arms a core hold,
    # a flip-breakdown a flip entry); the assembler reads this PROPERTY to make its risk-veto grade-aware
    # without a per-kind branch (the through-line). Distinct from ``grade`` (a risk carries no
    # call-strength grade): this is a targeting parameter, not the signal's own strength class.
    dearm_grade: Grade | None = None
    score: float = Field(ge=0.0, le=1.0)
    fired: bool
    label: str
    alpha_liveness_days: int | None = Field(default=None, ge=1)  # drives exit_by; positive when set
    provenance: list[Provenance] = Field(default_factory=list)
    asof: date

    @model_validator(mode="after")
    def _event_contract(self) -> "SignalEvent":
        # Taxonomy + trust contract (§1/§3 and invariants #3/#6): risks are ungraded; a fired entry
        # trigger is graded and bounded by a positive alpha horizon; every fired output shows its source.
        if self.role == Role.RISK_SIGNAL and self.grade is not None:
            raise ValueError("a risk_signal must not carry a grade")
        if self.role == Role.ENTRY_TRIGGER and self.fired and self.grade is None:
            raise ValueError("a fired entry_trigger must carry a grade (flip|core)")
        if self.role == Role.ENTRY_TRIGGER and self.fired and self.alpha_liveness_days is None:
            raise ValueError("a fired entry_trigger must carry alpha_liveness_days")
        # dearm_grade is a de-arm TARGET, meaningful only on a risk signal (an entry trigger never
        # de-arms). Kept off entry triggers so it can never be confused with the entry's own grade.
        if self.dearm_grade is not None and self.role != Role.RISK_SIGNAL:
            raise ValueError(
                "dearm_grade is a risk-signal de-arm target, not valid on an entry trigger"
            )
        if self.fired and not self.provenance:
            raise ValueError("a fired signal must carry provenance")
        return self
