from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import psycopg

from calls.assembler import assemble_call
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.signal import SignalEvent
from repositories import calls_repo, thesis_repo
from signals import insider_conviction, volume_breakout
from signals.base import PointInTimeData

# The per-security entry detectors the pipeline runs over each basket member. Risk-signal detectors
# (dilution, breakdown) join here in M4a.
_DETECTORS = (insider_conviction.detect, volume_breakout.detect)


def call_for_thesis(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    *,
    known_at: datetime | None = None,
    cfg: CallConfig = DEFAULT_CONFIG,
    record: bool = True,
) -> CallCard:
    """Load the thesis, RE-DERIVE its dated signal stream from the bitemporal facts as-of, assemble the
    CallCard, and (by default) append it to the write-only ``calls`` accountability log.

    There is no persisted firing layer: the stream is re-derived on every read, so a fact correction
    propagates automatically. The card itself is a pure function of (thesis, events, asof, cfg); the
    ``calls`` append is the only write and is never read back to serve. The caller owns the
    transaction (commit/rollback). ``known_at`` defaults to now (live read); the replay harness (M5)
    pins it to a past transaction time.
    """
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise LookupError(f"thesis not found: {thesis_id}")

    pit = PointInTimeData(conn, asof=asof, known_at=known_at)
    events: list[SignalEvent] = []
    for member in thesis.basket:
        if member.security_id is None:
            continue
        for detect in _DETECTORS:
            event = detect(pit, member.security_id, asof, cfg)
            if event is not None:
                events.append(event)

    card = assemble_call(thesis, events, asof, cfg)
    if record:
        calls_repo.append(conn, card)
    return card
