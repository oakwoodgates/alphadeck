from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import psycopg

from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, CallConfig
from pipeline.core import assemble_from_pit
from repositories import calls_repo, decisions_repo, thesis_repo
from signals.base import PointInTimeData


def call_for_thesis(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    *,
    known_at: datetime | None = None,
    cfg: CallConfig = DEFAULT_CONFIG,
    record: bool = True,
) -> CallCard:
    """Load the thesis, RE-DERIVE its dated signal stream from the bitemporal facts as-of (via the shared
    ``assemble_from_pit`` core), and (when ``record``) append the CallCard to the write-only ``calls``
    accountability log.

    There is no persisted firing layer: the stream is re-derived on every read, so a fact correction
    propagates automatically. The card is a pure function of (thesis, events, asof, cfg) — see
    ``pipeline.core.assemble_from_pit``, the seam the replay harness reuses with a Parquet-backed pit, so
    replay runs the identical pipeline (only the fact source differs). The API read path calls with
    ``record=False`` (a GET writes nothing); the batch ``pipeline.run`` is the writer of the call of record
    (``record=True``). When it writes, that append is the only write and is never read back to serve. The
    caller owns the transaction (commit/rollback). ``known_at`` defaults to now (live read); the replay
    harness pins it to a past transaction time.
    """
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise LookupError(f"thesis not found: {thesis_id}")

    # THE POSITION FEED (decision capture): the operator-decisions log is the source of truth for the
    # position, read as-of BOTH time axes — a past-asof call (or a pinned-known_at run) never sees a
    # later-logged fill (#1). Fed here, at the single assembly funnel, so the API read, pipeline.run,
    # and the daily cron all get it from one place and the assembler stays pure. A thesis with no
    # decision rows keeps its stored seed-era position (the precedence rule in decisions_repo).
    thesis.position = decisions_repo.effective_position(conn, thesis, asof=asof, known_at=known_at)

    # Derive the tenant from the THESIS (auth deferred): a thesis lives in exactly one tenant, so its
    # tenant_id scopes EVERY fact read for this call (threaded once into the pit) AND the call-of-record
    # write — making both the API read path and the batch pipeline tenant-correct from this one place.
    # Pass it explicitly: the column is NOT NULL, so a loaded thesis's tenant_id is non-None; never
    # `or DEFAULT` here, so a None would surface as a bug rather than silently read demo facts.
    pit = PointInTimeData(conn, asof=asof, known_at=known_at, tenant_id=thesis.tenant_id)
    card = assemble_from_pit(pit, thesis, asof, cfg)
    if record:
        calls_repo.append(conn, card, thesis.tenant_id)
    return card
