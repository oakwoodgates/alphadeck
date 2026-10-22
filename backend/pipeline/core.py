from __future__ import annotations

from datetime import date
from uuid import UUID

from calls.assembler import assemble_call
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.signal import SignalEvent
from domain.thesis import Thesis
from signals import laggard, registered_detectors, split_hold, theme_conviction
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

    **The split-night hold (S5, ``docs/CALL_LOGIC.md`` §2b)** lives HERE, at the one seam, so the nightly, the
    serve path and every history view apply it identically: a member whose newest bars carry an unconfirmed
    split-shaped step (``signals.split_hold.find_holds``) has its PRICE-PATH triggers (``Detector.
    price_trigger``, and the laggard rotation) read only the bars before the step (``HeldPriceView``). A
    trigger that reads across the step cannot fire, warm or arm; one wholly before it stands. Every open hold
    goes on the card's ``missing[]`` for as long as it holds, naming the triggers it withheld (held is never
    silent, #6). No hold = the stream and the card are exactly what they were before S5.
    """
    if pit.asof != asof:
        raise ValueError(f"pipeline asof {asof} does not match point-in-time view {pit.asof}")

    holds = split_hold.find_holds(pit, thesis, asof)
    held_view = split_hold.HeldPriceView(pit, holds) if holds else pit
    # security -> the price-path triggers the hold WITHHELD there (every held member has a line: [] reads
    # "Price triggers on X held"); a co-member appears only when its laggard confirmation was withheld
    held: dict[UUID, list[str]] = {sid: [] for sid in holds}
    same = split_hold.identity  # WHICH trigger, not its reading (score / label)

    events: list[SignalEvent] = []
    unheld: list[SignalEvent] = []  # the stream WITHOUT the hold — only to name what was held
    for member in thesis.basket:
        if member.security_id is None:
            continue
        sid = member.security_id
        for detector in registered_detectors():
            event = detector(pit, sid, asof, cfg)
            if event is not None:
                unheld.append(event)
            if sid in holds and detector.price_trigger is not None:
                kept = detector(held_view, sid, asof, cfg)
                if event is not None and same(event) != same(kept):
                    # withheld: this trigger read across the step
                    held[sid].append(detector.price_trigger)
                event = kept
            if event is not None:
                events.append(event)

    # M5b — an operator-ratified, thesis-level theme conviction supplies Key 1 as a FALLBACK: broadcast it
    # onto each eligible member (a live volume-backed confirmation, no own conviction) as a flip-capped
    # conviction event. Runs AFTER the per-member loop because eligibility reads the assembled member
    # stream; from here the assembler treats the broadcast events as ordinary convictions (no arming change).
    # It reads the HELD stream, so a held breakout makes no member eligible.
    theme_fact = theme_conviction.detect_fact(pit, thesis.id, asof, cfg)
    events += theme_conviction.broadcast(thesis, events, theme_fact, asof, cfg)

    # §1.2 ROTATION — a cross-member laggard sympathy CONFIRMATION (Key 2): while a basket leader has a
    # live breakout, a lagging-but-uptrend co-member gets a flip laggard confirmation, which the assembler
    # co-locates with an own conviction to ARM the name that hasn't run yet (R3/R4). Reads the assembled
    # member stream (the breakout leaders) like the theme broadcast; adds no ingest and no new fact. A
    # price-path reader: on a held night it reads the held view and the held stream (its leaders and the
    # basket's median return then come from one scale). A laggard it WITHHOLDS is named (on a held member's
    # line, or one line for co-members cued by a held member's price); a co-member's laggard merely re-read
    # (the basket median in its label) is not a held trigger and adds no line.
    lagging = laggard.detect(held_view, thesis, events, asof, cfg)
    if holds:
        lagging_unheld = laggard.detect(pit, thesis, unheld, asof, cfg)
        for sid in dict.fromkeys(e.security_id for e in lagging_unheld + lagging):
            mine = [e for e in lagging if e.security_id == sid]
            theirs = [e for e in lagging_unheld if e.security_id == sid]
            if {same(e) for e in theirs} - {same(e) for e in mine}:
                held.setdefault(sid, []).append(split_hold.LAGGARD_TRIGGER)
    events += lagging

    return assemble_call(
        thesis, events, asof, cfg, held=split_hold.notes(thesis, holds, held) if held else ()
    )
