from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import CatalystType, Grade, Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

# A binding (core) catalyst reads as strong conviction; a provisional (flip) one as moderate. Inline
# STARTING calibration — detectors may carry numbers (the magic-number guard is on the assembler).
DETECTOR_NAME = "catalyst_conviction"
_CORE_SCORE = 0.9
_FLIP_SCORE = 0.5


def liveness(fact: dict[str, Any], cfg: CallConfig) -> int:
    """The catalyst's relevance HORIZON in days — DECOUPLED from grade (unlike insider). When the
    structured record carries an agreement term (``horizon_end``, e.g. a DOE OTA's period of
    performance), liveness runs to that term; otherwise the configured default. So a provisional (flip)
    but long-horizon catalyst stays live for its term, while grade still sets entry size.

    Public so the Workbench catalyst-density meter reuses the SAME live-window the back half uses (one
    source of liveness — a name's catalyst is 'live' in the same sense for both)."""
    horizon_end = fact.get("horizon_end")
    if horizon_end is not None:
        return max((horizon_end - fact["valid_from"]).days, 1)
    return cfg.catalyst_default_horizon_days


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
    (invariant #3), and sets entry SIZE. Liveness is the catalyst's relevance HORIZON (the agreement
    term), DECOUPLED from grade (§7 / docs/CATALYST_CONVICTION.md option A). The fire date is the
    event date; it stays live to its horizon, re-derived from the facts on every read. Arming still
    needs a co-located confirmation (a fresh breakout), so a still-on-the-books catalyst arms nothing
    on its own.
    """
    live: list[tuple[dict[str, Any], Grade, int]] = []
    for f in facts:
        lv = liveness(f, cfg)
        if entry_signal_is_live(f["valid_from"], lv, asof):
            live.append((f, Grade(f["grade"]), lv))
    if not live:
        return None
    # strongest conviction: prefer a binding (core) catalyst, then the most recent
    fact, grade, lv = max(live, key=lambda x: (x[1] is Grade.CORE, x[0]["valid_from"]))
    ctype = CatalystType(fact["catalyst_type"]) if fact.get("catalyst_type") else None
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.CATALYST,
        catalyst_type=ctype,
        grade=grade,
        score=_CORE_SCORE if grade is Grade.CORE else _FLIP_SCORE,
        label=fact["label"],
        alpha_liveness_days=lv,
        provenance=[source_provenance(fact["source"], fact["source_ref"])],
        asof=fact["valid_from"],
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — catalyst conviction (warms) for theme/catalyst theses. Reads ratified/parsed catalyst
    facts via the point-in-time view; arming still needs a co-located confirmation (the breakout).
    """
    return score(pit.catalyst_facts(security_id), security_id, asof, cfg)


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
