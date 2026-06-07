from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import CatalystType, Grade, Kind, Role
from domain.signal import Provenance, SignalEvent
from signals.base import PointInTimeData

# A binding (core) catalyst reads as strong conviction; a provisional (flip) one as moderate. Inline
# STARTING calibration — detectors may carry numbers (the magic-number guard is on the assembler).
_CORE_SCORE = 0.9
_FLIP_SCORE = 0.5


def _liveness(grade: Grade, cfg: CallConfig) -> int:
    if grade is Grade.CORE:
        return cfg.catalyst_core_alpha_liveness_days
    return cfg.catalyst_flip_alpha_liveness_days


def score(
    facts: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: the strongest LIVE catalyst on a security -> a Key-1 conviction SignalEvent (or None).

    A catalyst is a deterministic / operator-ratified, verifiable commitment (the theme analog of an
    insider buy). Its grade — core (binding: PPA / operating license / loan guarantee) vs flip
    (provisional: MOU / LOI / selection / attention) — is carried on the FACT, never decided here
    (invariant #3). The fire date is the catalyst's event date; it stays live for its GRADED
    alpha-liveness window (a hard window, like the insider clock), so a still-live conviction is
    re-derived from the facts on every read. Arming still needs a co-located confirmation.
    """
    live: list[tuple[dict[str, Any], Grade]] = []
    for f in facts:
        grade = Grade(f["grade"])
        if f["valid_from"] >= asof - timedelta(days=_liveness(grade, cfg)):
            live.append((f, grade))
    if not live:
        return None
    # strongest conviction: prefer a binding (core) catalyst, then the most recent
    fact, grade = max(live, key=lambda fg: (fg[1] is Grade.CORE, fg[0]["valid_from"]))
    ctype = CatalystType(fact["catalyst_type"]) if fact.get("catalyst_type") else None
    return SignalEvent(
        detector="catalyst_conviction",
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.CATALYST,
        type=ctype,
        grade=grade,
        score=_CORE_SCORE if grade is Grade.CORE else _FLIP_SCORE,
        fired=True,
        label=fact["label"],
        alpha_liveness_days=_liveness(grade, cfg),
        provenance=[Provenance(source=fact["source"], ref=fact["source_ref"])],
        asof=fact["valid_from"],
    )


def detect(
    pit: PointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — catalyst conviction (warms) for theme/catalyst theses. Reads ratified/parsed catalyst
    facts via the point-in-time view; arming still needs a co-located confirmation (the breakout).
    """
    return score(pit.catalyst_facts(security_id), security_id, asof, cfg)
