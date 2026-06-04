from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from domain.enums import CatalystType, Grade, Kind, Role


class Provenance(BaseModel):
    """A pointer to the source + the computation behind a signal.

    Invariant #6 (show the work): every trigger traces to a computation, never the model's say-so.
    """

    source: str  # "form4" | "price" | "xbrl" | ...
    ref: str  # EDGAR accession / "price:CCJ:2026-06-02"
    evidence_id: UUID | None = None  # FK to an evidence row once materialized
    detail: dict[str, Any] = Field(default_factory=dict)  # the computation inputs


class SignalEvent(BaseModel):
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
    alpha_half_life_days: int | None = None  # drives exit_by; None for risk signals
    provenance: list[Provenance] = Field(default_factory=list)
    asof: date
