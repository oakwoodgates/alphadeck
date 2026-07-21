from __future__ import annotations

import uuid
from datetime import date, datetime
from uuid import UUID

from calls.assembler import assemble_call
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Archetype
from domain.signal import SignalEvent
from domain.thesis import BasketMember, Thesis
from repositories import calls_repo, thesis_repo
from tests.calls.factories import breakout_event, insider_event, make_thesis

# Shared seeding helpers for the Scoreboard tests: a persisted single-name thesis, controlled
# call-of-record rows (assemble at a chosen as-of, append to the log — the live shape), controlled
# price bars (bitemporal, explicit recorded_at when versioning matters), and the two-key event pair
# with a CHOSEN fire date (the factories fix asof; an episode test needs exit_by/arm_until anchored
# where the test says).


def persist_thesis(db, security_id: UUID, thesis_id: UUID | None = None) -> Thesis:
    thesis = make_thesis(
        id=thesis_id or uuid.uuid4(),
        basket=[
            BasketMember(
                ticker="DEVCO",
                role="Lead developer",
                archetype=Archetype.LEADER,
                security_id=security_id,
            )
        ],
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis_repo.get(db, thesis.id)  # reload: tenant_id stamped by the repo


def record_day(
    db,
    thesis: Thesis,
    events: list[SignalEvent],
    asof: date,
    *,
    ingest_fresh: bool | None = None,
    ingest_errors: int | None = None,
) -> None:
    """Assemble + append one call-of-record row, optionally stamped with the run's R2b ingest
    health (migration 0023) — the default (None, None) is the legacy/manual-append shape."""
    calls_repo.append(
        db,
        assemble_call(thesis, events, asof, DEFAULT_CONFIG),
        ingest_fresh=ingest_fresh,
        ingest_errors=ingest_errors,
    )
    db.commit()


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
