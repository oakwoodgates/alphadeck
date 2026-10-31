from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta
from typing import Any
from uuid import UUID

from domain.enums import CatalystType, Grade, Kind, Role
from domain.signal import Provenance, SignalEvent
from domain.trading_days import next_scheduled_day


def entry_signal_is_live(fire_date: date, alpha_liveness_days: int, asof: date) -> bool:
    """Is a trigger live on night ``asof``? Shared by every detector and the call assembler (CALL_LOGIC §2/§6).

    F3 (operator rule 2026-10-30, rule log ``2026-11-entry-window-next-session``): a trigger counts on night N
    only while the FIRST SESSION AFTER N (``domain.trading_days.next_scheduled_day``) is on or before its
    window end. The call is recorded after N's close, so the operator can act no earlier than that session; a
    night whose next session falls outside the window would show an arm (or a warm-up, or a risk) nobody can
    act on. Before F3 the test was ``asof <= window end``, which kept a trigger live through the window's last
    night with no session of it left. No window is stretched: the end date is unchanged, only the last
    unusable night (or, for a window ending on a weekend or holiday, the nights after its last session) drops.
    """
    return next_scheduled_day(asof) <= fire_date + timedelta(days=alpha_liveness_days)


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
    dearm_grade: Grade | None = None,
    catalyst_type: CatalystType | None = None,
    alpha_liveness_days: int | None = None,
) -> SignalEvent:
    """Construct the one output shape used by every current fired signal producer.

    ``dearm_grade`` is the de-arm TARGET a ``breakdown`` risk signal carries (the grade of armed entry it
    de-arms) — left ``None`` by every entry trigger and every other risk (see ``SignalEvent``).
    """
    return SignalEvent(
        detector=detector,
        security_id=security_id,
        role=role,
        kind=kind,
        type=catalyst_type,
        grade=grade,
        dearm_grade=dearm_grade,
        score=score,
        fired=True,
        label=label,
        alpha_liveness_days=alpha_liveness_days,
        provenance=list(provenance),
        asof=asof,
    )
