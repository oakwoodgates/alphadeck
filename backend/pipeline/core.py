from __future__ import annotations

from datetime import date

from calls.assembler import assemble_call
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.signal import SignalEvent
from domain.thesis import Thesis
from signals import registered_detectors, theme_conviction
from signals.base import SignalPointInTimeData


def assemble_from_pit(
    pit: SignalPointInTimeData,
    thesis: Thesis,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> CallCard:
    """The pure pipeline core: re-derive the dated signal stream from a point-in-time view of the facts +
    assemble the CallCard. This is the ONE seam the live path and the replay harness share, so replay
    validates the REAL pipeline — the same detectors, the same M5b theme broadcast, the same
    ``assemble_call`` — with only the FACT SOURCE differing (``pit``). ``pit`` is any object with the
    ``PointInTimeData`` accessor interface (the live Postgres-backed one, or ``ReplayPointInTimeData`` over
    the Parquet mirror); it carries ``asof`` / ``known_at`` / ``tenant_id``, so this core takes no conn and
    does no recording. A pure function of ``(pit, thesis, asof, cfg)``.
    """
    if pit.asof != asof:
        raise ValueError(f"pipeline asof {asof} does not match point-in-time view {pit.asof}")

    events: list[SignalEvent] = []
    for member in thesis.basket:
        if member.security_id is None:
            continue
        for detector in registered_detectors():
            event = detector(pit, member.security_id, asof, cfg)
            if event is not None:
                events.append(event)

    # M5b — an operator-ratified, thesis-level theme conviction supplies Key 1 as a FALLBACK: broadcast it
    # onto each eligible member (a live volume-backed confirmation, no own conviction) as a flip-capped
    # conviction event. Runs AFTER the per-member loop because eligibility reads the assembled member
    # stream; from here the assembler treats the broadcast events as ordinary convictions (no arming change).
    theme_fact = theme_conviction.detect_fact(pit, thesis.id, asof, cfg)
    events += theme_conviction.broadcast(thesis, events, theme_fact, asof, cfg)

    return assemble_call(thesis, events, asof, cfg)
