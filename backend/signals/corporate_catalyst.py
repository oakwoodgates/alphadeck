"""8-K item-code catalyst trigger (Band 03 S3) — the deterministic corporate-event conviction.

An ENTRY trigger extending the catalyst family (``kind=CATALYST``): a basket member filing an 8-K
whose item code is on the trigger side of the policy map fires a Key-1 conviction — the SEC's own
item taxonomy IS the classification (#3: no NLP, no LLM, no located-passage judgment on the fire
path; the provenance links the filing for the operator to read). The live trigger map is now
**5.02-ONLY** (5.02 officer/director change -> ``personnel``/FLIP): the original cut's 1.01 material
definitive agreement -> ``contract``/CORE was DEMOTED out of the catalyst on 2026-08-20 (the "1.01
decision", option A) — measured ~60% of Item 1.01s are FINANCING, not deals, so firing a bullish
conviction on them flooded ~80 false-bullish arms; 1.01 stays STORED on the tape (#9) and fires
nothing, while a 3.02 co-item now routes to the RISK side as dilution. The prefer-CORE half of the
selection below therefore has no live item to exercise (it is pinned against the function's
contract in the tests, with a purpose-built policy map).

A SECOND catalyst detector, deliberately NOT a ``fact_catalyst`` feed: the item→(grade/type/score/
liveness) POLICY lives in ``CallConfig.corporate_event_items`` and is applied on READ, so retuning
re-derives every call with zero data repair (the evidence/policy seam) — writing pre-graded
``fact_catalyst`` rows would bake policy into evidence AND feed the live ``catalyst_conviction``
the moment facts land (no inert-first possible). Emitting ``kind=CATALYST`` inherits the existing
``conviction_kinds`` membership: co-location arming, ``own_conviction_kinds`` ranking, and
``call_grade=max`` composition beside ratified catalysts — zero assembler edits (the through-line).

Selection mirrors ``catalyst_conviction``: the STRONGEST live item fires (prefer a CORE-graded
item, then the most recent filing, then a deterministic accession/item tiebreak); every OTHER live
trigger item rides the provenance so nothing surfaced is hidden. Liveness is the ITEM's configured
window anchored on the filing date (an 8-K item code carries no agreement term, so no per-fact
``horizon_end``).

MASTER SWITCH (``corporate_catalyst_enabled``) — **STILL DEFAULT OFF, and the detector stays PARKED**
(its S3 sibling ``corporate_risk`` flipped ON 2026-08-17; this side touches the LIVE catalyst family
and was the flood source the 1.01 decision addressed). Registered but ``detect`` no-ops, so with the
live DEFAULT_CONFIG no corporate-catalyst event enters the stream and every existing golden is
byte-for-byte unchanged. The pure ``score`` is UNGATED (the math stays testable); ``replay.run``'s
``--corporate-catalyst`` / ``ALPHADECK_CORPORATE_CATALYST`` force it on for the sig-lab pass.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import fired_signal, source_provenance
from signals.corporate_events import item_label, live_policy_items
from signals.registry import register_detector

DETECTOR_NAME = "corporate_catalyst"


def score(
    facts: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: the strongest LIVE trigger-cut 8-K item on a security -> a Key-1 conviction
    SignalEvent (or None). Grade/type/score/liveness come from the item's POLICY row, never decided
    here (#3 — the deterministic rule; a policy edit re-derives, no data repair). UNGATED by the
    master switch (``detect`` holds the gate)."""
    live = live_policy_items(facts, asof, cfg, Role.ENTRY_TRIGGER)
    if not live:
        return None
    # strongest conviction: prefer a CORE-graded item, then the most recent filing; the
    # (accession, item) tail makes a same-day tie deterministic (live/replay byte-parity).
    fact, item, policy = max(
        live, key=lambda x: (x[2].grade is Grade.CORE, x[0]["valid_from"], x[0]["accession"], x[1])
    )
    label = (
        f"8-K Item {item} — {item_label(item)} (filed {fact['valid_from'].isoformat()}, "
        f"{fact['form']})"
    )
    # every LIVE trigger item rides the provenance (one ref per (filing, item), the firing one
    # included) — the work shown beside the call (#6), nothing surfaced is hidden.
    provenance = [
        source_provenance(
            "8-k",
            f["accession"],
            detail={
                "item": it,
                "form": f["form"],
                "filed": f["valid_from"].isoformat(),
                "index_url": f["source_ref"],
            },
        )
        for f, it, _ in sorted(live, key=lambda x: (x[0]["valid_from"], x[0]["accession"], x[1]))
    ]
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.CATALYST,
        catalyst_type=policy.catalyst_type,
        grade=policy.grade,
        score=policy.score,
        label=label,
        alpha_liveness_days=policy.liveness_days,
        provenance=provenance,
        asof=fact["valid_from"],
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — corporate-event catalyst conviction off the 8-K item-code tape. Reads
    ``fact_corporate_event`` via the point-in-time view; arming still needs a co-located
    confirmation (the breakout). MASTER SWITCH (default OFF): no-ops until
    ``cfg.corporate_catalyst_enabled`` — nothing reaches live cards unmeasured."""
    if not cfg.corporate_catalyst_enabled:
        return None
    return score(pit.corporate_event_facts(security_id), security_id, asof, cfg)


def horizons(cfg: CallConfig) -> dict[str, int | None]:
    """The READ-HORIZON declaration (``signals/horizons.py``): the 8-K item tape is read UNBOUNDED
    (``None``) — the detector's own liveness/asof filters select from the full as-of tape, so no event-
    time floor may ever be applied to it."""
    return {"fact_corporate_event": None}


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect, horizons=horizons))
