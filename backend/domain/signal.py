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
    """A detector's output: f(point_in_time_data, asof) -> SignalEvent (CALL_LOGIC §1).

    Detectors are pure: no implicit "now" — time is always the `asof` parameter.
    """

    detector: str
    security_id: UUID
    role: Role
    kind: Kind
    type: CatalystType | None = None
    grade: Grade | None = None  # None when role=risk_signal
    score: float = Field(ge=0.0, le=1.0)
    fired: bool
    label: str
    alpha_liveness_days: int | None = Field(default=None, ge=1)  # drives exit_by; positive when set
    provenance: list[Provenance] = Field(default_factory=list)
    asof: date

    @model_validator(mode="after")
    def _grade_matches_role(self) -> "SignalEvent":
        # taxonomy contract (§1/§3): risk signals are ungraded; a fired entry trigger is graded flip|core
        if self.role == Role.RISK_SIGNAL and self.grade is not None:
            raise ValueError("a risk_signal must not carry a grade")
        if self.role == Role.ENTRY_TRIGGER and self.fired and self.grade is None:
            raise ValueError("a fired entry_trigger must carry a grade (flip|core)")
        return self
