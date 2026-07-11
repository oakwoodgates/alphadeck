from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.signal import SignalEvent
from tests.calls.factories import breakout_event, insider_event

# Shared seeding helpers for the Scoreboard tests: controlled price bars (bitemporal, explicit
# recorded_at when versioning matters) and the two-key event pair with a CHOSEN fire date (the
# factories fix asof; an episode test needs exit_by/arm_until anchored where the test says).


def bar(
    db,
    security_id: UUID,
    d: date,
    close: float | None,
    *,
    recorded_at: datetime | None = None,
) -> None:
    values = {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "d": d,
        "close": close,
        "valid_from": d,
    }
    if recorded_at is not None:
        values["recorded_at"] = recorded_at
    append_fact(db, "fact_price_eod", values)
    db.commit()


def keys_fired(
    security_id: UUID,
    fired_on: date,
    *,
    conv_liveness: int,
    conf_liveness: int,
) -> tuple[SignalEvent, SignalEvent]:
    """(conviction, confirmation) both fired on ``fired_on`` — armed while both live, so
    exit_by = fired_on + conv_liveness and arm_until = fired_on + conf_liveness, by construction."""
    conv = insider_event(security_id=security_id, liveness=conv_liveness).model_copy(
        update={"asof": fired_on}
    )
    conf = breakout_event(security_id=security_id, liveness=conf_liveness).model_copy(
        update={"asof": fired_on}
    )
    return conv, conf
