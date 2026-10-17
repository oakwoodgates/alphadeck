from __future__ import annotations

import uuid
from datetime import date, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

from psycopg.types.json import Json

from calls.assembler import assemble_call
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.signal import SignalEvent
from domain.thesis import BasketMember, Thesis
from repositories import thesis_repo
from repositories.mappers import call_to_row
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
                security_id=security_id,
            )
        ],
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis_repo.get(db, thesis.id)  # reload: tenant_id stamped by the repo


_NY = ZoneInfo("America/New_York")


def on_time(asof: date) -> datetime:
    """When the NIGHTLY record for ``asof`` really lands: 15 minutes after that night's 22:30 ET ``RUN_AT``
    (prod's rows land at ~22:4x) — inside its on-time window, so the L1 late-row reading scores it.
    """
    return datetime.combine(asof, time(22, 45), tzinfo=_NY)


def record_day(
    db,
    thesis: Thesis,
    events: list[SignalEvent],
    asof: date,
    *,
    ingest_fresh: bool | None = None,
    ingest_errors: int | None = None,
    reconstructed: bool = False,
    config_hash: str | None = None,
    code_sha: str | None = None,
    run_kind: str | None = None,
    recorded_at: datetime | None = None,
) -> UUID:
    """Assemble + append one call-of-record row, optionally stamped with the run's R2b ingest
    health (migration 0023) — the default (None, None) is the legacy/manual-append shape — and/or
    marked ``reconstructed`` (migration 0042: the row a backfill writes, which the record path
    reports but never scores) — and/or carrying the 0044 RUN IDENTITY (which policy / code / kind of
    run wrote it; all-None is the legacy shape here too). Returns the row's id.

    ``recorded_at`` defaults to ``on_time(asof)`` — the NIGHTLY row's real shape. Before L1 the helper let
    the DB stamp ``now()`` (test time, months after a 2026-06 as-of), harmless while nothing read the stamp;
    under L1 such a row is LATE (recorded after its night's next EDGAR open) and the default Scoreboard
    reading sets it aside, so a fixture modeling the nightly record has to be recorded on time. Pass an
    explicit stamp to model a late catch-up row. The row is otherwise exactly what ``calls_repo.append``
    writes (the repo's own ``call_to_row`` mapper); an explicit INSERT because the ``calls`` log is
    immutable (the ``no_update`` trigger), so the stamp must be supplied at insert time — the
    ``tests/repositories/test_calls_repo._insert_call_at`` precedent."""
    card = assemble_call(thesis, events, asof, DEFAULT_CONFIG)
    row = call_to_row(card, DEFAULT_TENANT_ID)
    with db.cursor() as cur:
        cur.execute(
            """INSERT INTO calls
                   (tenant_id, thesis_id, asof, state, verdict, card, ingest_fresh, ingest_errors,
                    reconstructed, config_hash, code_sha, run_kind, recorded_at)
               VALUES (%(tenant_id)s, %(thesis_id)s, %(asof)s, %(state)s, %(verdict)s, %(card)s,
                       %(ingest_fresh)s, %(ingest_errors)s, %(reconstructed)s,
                       %(config_hash)s, %(code_sha)s, %(run_kind)s, %(recorded_at)s)
               RETURNING id""",
            {
                **row,
                "card": Json(row["card"]),
                "ingest_fresh": ingest_fresh,
                "ingest_errors": ingest_errors,
                "reconstructed": reconstructed,
                "config_hash": config_hash,
                "code_sha": code_sha,
                "run_kind": run_kind,
                "recorded_at": recorded_at or on_time(asof),
            },
        )
        call_id = cur.fetchone()["id"]
    db.commit()
    return call_id


def bar(
    db,
    security_id: UUID,
    d: date,
    close: float | None,
    *,
    recorded_at: datetime | None = None,
    high: float | None = None,
    low: float | None = None,
) -> None:
    """One bitemporal price bar. ``high``/``low`` default to NULL — the honest close-only free-EOD
    shape, which is what the wick-excursion fields must degrade on (they never fall back to the
    close). Pass them to write a full-wick bar."""
    values = {
        "tenant_id": DEFAULT_TENANT_ID,
        "security_id": security_id,
        "d": d,
        "close": close,
        "high": high,
        "low": low,
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
