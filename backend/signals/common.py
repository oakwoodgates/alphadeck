from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from domain.enums import CatalystType, Grade, Kind, Role
from domain.signal import Provenance, SignalEvent


def entry_signal_is_live(fire_date: date, alpha_liveness_days: int, asof: date) -> bool:
    """Inclusive entry-signal liveness shared by detectors and the call assembler."""
    return asof <= fire_date + timedelta(days=alpha_liveness_days)


def source_provenance(
    source: str,
    ref: str,
    *,
    detail: dict[str, Any] | None = None,
) -> Provenance:
    """Stamp one source/computation pointer in the common signal provenance shape."""
    return Provenance(source=source, ref=ref, detail={} if detail is None else detail)


def fired_signal(
    *,
    detector: str,
    security_id: UUID,
    role: Role,
    kind: Kind,
    score: float,
    label: str,
    asof: date,
    provenance: Iterable[Provenance],
    grade: Grade | None = None,
    catalyst_type: CatalystType | None = None,
    alpha_liveness_days: int | None = None,
) -> SignalEvent:
    """Construct the one output shape used by every current fired signal producer."""
    return SignalEvent(
        detector=detector,
        security_id=security_id,
        role=role,
        kind=kind,
        type=catalyst_type,
        grade=grade,
        score=score,
        fired=True,
        label=label,
        alpha_liveness_days=alpha_liveness_days,
        provenance=list(provenance),
        asof=asof,
    )
