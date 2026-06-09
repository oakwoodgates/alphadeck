from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import psycopg

from calls.assembler import assemble_call
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.signal import SignalEvent
from repositories import calls_repo, thesis_repo
from signals import (
    catalyst_conviction,
    dilution_clock,
    insider_conviction,
    theme_conviction,
    volume_breakout,
)
from signals.base import PointInTimeData

# The per-security detectors the pipeline runs over each basket member: the Key-1 conviction triggers
# (insider buys + catalysts, for single-name and theme theses respectively), the Key-2 volume breakout,
# and the dilution risk signal. (The breakdown risk signal joins in M4a-ii.)
_DETECTORS = (
    insider_conviction.detect,
    catalyst_conviction.detect,
    volume_breakout.detect,
    dilution_clock.detect,
)


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
    CallCard, and (when ``record``) append it to the write-only ``calls`` accountability log.

    There is no persisted firing layer: the stream is re-derived on every read, so a fact correction
    propagates automatically. The card itself is a pure function of (thesis, events, asof, cfg). The
    API read path calls with ``record=False`` (a GET writes nothing); the batch ``pipeline.run`` is
    the writer of the call of record (``record=True``). When it writes, that append is the only write
    and is never read back to serve. The caller owns the transaction (commit/rollback). ``known_at``
    defaults to now (live read); the replay harness (M5) pins it to a past transaction time.
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

    # M5b — an operator-ratified, thesis-level theme conviction supplies Key 1 as a FALLBACK: broadcast it
    # onto each eligible member (a live volume-backed confirmation, no own conviction) as a flip-capped
    # conviction event. Runs AFTER the per-member loop because eligibility reads the assembled member
    # stream; from here the assembler treats the broadcast events as ordinary convictions (no arming change).
    theme_fact = theme_conviction.detect_fact(pit, thesis_id, asof, cfg)
    events += theme_conviction.broadcast(thesis, events, theme_fact, asof, cfg)

    card = assemble_call(thesis, events, asof, cfg)
    if record:
        calls_repo.append(conn, card)
    return card
