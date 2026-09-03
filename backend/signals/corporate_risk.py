"""8-K item-code corporate RISK (Band 03 S3) — the counter-case's deterministic teeth.

A RISK signal (never an entry trigger) off the same ``fact_corporate_event`` tape as the trigger
side: an 8-K whose item code is on the risk side of the policy map fires ``kind=CORPORATE_RISK``.
The v1 cut: 3.01 listing-deficiency + 4.01 auditor change (moderate — counter-case + a confidence
haircut, sub-veto) and 4.02 non-reliance/restatement + 1.03 bankruptcy (SEVERE — score >=
``risk_block_severity``, so the assembler's existing grade-blind role+score composition withholds
the NAME on timing; zero assembler edits, zero kind branches). Grade-blind like dilution
(``grade=None``, no ``dearm_grade`` — this is not a de-arm).

ONE event per name per read (the detector contract): the MAX-severity live item is the headline —
its policy score is the event's score and its filing date the event's ``asof`` — and EVERY other
live risk item is enumerated in the label and carried in the provenance, so two simultaneous flags
(an auditor change + a listing notice) are both visible while the composition stays the max. The
accepted consequence: co-live moderates cost ONE confidence haircut, not one each.

Freshness is DETECTOR-ENFORCED (the assembler never ages risk signals): each item stays live for
its configured ``liveness_days`` anchored on its filing date, then drops out of the re-derived
stream — replay stays honest. An 8-K/A is its own tape row; these existence/latest-shaped reads
can't double-count an amendment (the deferred cadence slice, which COUNTS, must dedupe — noted in
docs/CORPORATE_EVENTS.md).

MASTER SWITCH (``corporate_risk_enabled``) — **DEFAULT ON since the operator flip of 2026-08-17**
(shipped OFF behind the insider_sell precedent; validated on real prod data BEFORE the flip — zero
spurious arm-withholdings, zero recorded calls changed, so every existing golden stayed
byte-for-byte unchanged). ``detect`` is still GATED: set the dial False and it no-ops, and the
detector stays registered either way. The pure ``score`` is UNGATED; ``replay.run``'s
``--corporate-risk`` / ``ALPHADECK_CORPORATE_RISK`` set it explicitly for the sig-lab pass.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import fired_signal, source_provenance
from signals.corporate_events import item_label, live_policy_items
from signals.registry import register_detector

DETECTOR_NAME = "corporate_risk"


def score(
    facts: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: the LIVE risk-cut 8-K items on a security -> ONE risk SignalEvent (or None), scored at
    the max-severity live item. Severity/liveness come from the item's POLICY row, never decided
    here (#3). UNGATED by the master switch (``detect`` holds the gate)."""
    live = live_policy_items(facts, asof, cfg, Role.RISK_SIGNAL)
    if not live:
        return None
    # the headline = the max-severity live item; most recent filing breaks a severity tie, then the
    # deterministic (accession, item) tail (live/replay byte-parity).
    ordered = sorted(
        live,
        key=lambda x: (x[2].score, x[0]["valid_from"], x[0]["accession"], x[1]),
        reverse=True,
    )
    fact, item, policy = ordered[0]
    severe = policy.score >= cfg.risk_block_severity
    risk_read = (
        "severe — withholds the Armed call on timing"
        if severe
        else "counter-case input, below the timing-veto threshold"
    )
    label = (
        f"8-K Item {item} — {item_label(item)} (filed {fact['valid_from'].isoformat()}, "
        f"{fact['form']}); {risk_read}"
    )
    others = ordered[1:]
    if others:
        also = ", ".join(
            f"Item {it} ({item_label(it)}, filed {f['valid_from'].isoformat()})"
            for f, it, _ in others
        )
        label += f"; also live: {also}"
    # every LIVE risk item rides the provenance (the headline included) — the work shown (#6).
    provenance = [
        source_provenance(
            "8-k",
            f["accession"],
            detail={
                "item": it,
                "form": f["form"],
                "filed": f["valid_from"].isoformat(),
                "index_url": f["source_ref"],
                "score": p.score,
            },
        )
        for f, it, p in ordered
    ]
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.RISK_SIGNAL,
        kind=Kind.CORPORATE_RISK,
        grade=None,  # a risk is ungraded; and no dearm_grade — this is not a de-arm
        score=policy.score,
        label=label,
        alpha_liveness_days=None,  # matching dilution/insider_sell: freshness is enforced ABOVE
        provenance=provenance,
        asof=fact["valid_from"],
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Risk signal — 8-K item-code corporate events. Reads ``fact_corporate_event`` via the
    point-in-time view. MASTER SWITCH — **DEFAULT ON since 2026-08-17**: gated on
    ``cfg.corporate_risk_enabled``, so setting it False still no-ops the detector."""
    if not cfg.corporate_risk_enabled:
        return None
    return score(pit.corporate_event_facts(security_id), security_id, asof, cfg)


def horizons(cfg: CallConfig) -> dict[str, int | None]:
    """The READ-HORIZON declaration (``signals/horizons.py``): the 8-K item tape is read UNBOUNDED
    (``None``) — the detector's own liveness/asof filters select from the full as-of tape, so no event-
    time floor may ever be applied to it."""
    return {"fact_corporate_event": None}


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect, horizons=horizons))
