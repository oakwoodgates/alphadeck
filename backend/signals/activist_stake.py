"""SC 13D activist-stake trigger (Band 03 S5) — the deterministic outside-conviction Key 1.

An ENTRY trigger with its own kind (``Kind.ACTIVIST_STAKE``): a new SC 13D filed ABOUT a basket
member means an outside party crossed 5% WITH INTENT to influence — a rare, deliberate,
capital-committed act by an informed party (the Brav/Jiang activist event-study literature measures
persistent post-FILING abnormal returns). The FORM TYPE is the entire fire decision (#3 — the SEC
only requires a 13D when intent exists; no NLP, no cover parse on the fire path; the filer identity
and %-owned the tape carries are EVIDENCE shown beside the call, never inputs to the fire).

The v1 fire policy (operator-confirmed 2026-08-18, the S3 1.01-flood lessons applied):

- **13D-family ORIGINALS fire** (``SC 13D`` / ``SCHEDULE 13D``, both naming eras), grade CORE fixed
  here in the detector (the R6/R9 structural-grade precedent — "core = a rare, deliberate capital
  commitment", the line item 1.01 failed). The literature anchors the edge at the ORIGINAL filing.
- **Amendments (/A) never re-anchor a fire** — a 13D/A is direction-blind (an increase, a
  sell-down, and an exit all file identically; the measured CMPS 13D/A reporting 4.96%, a sell-down
  BELOW 5%, must not fire a fresh CORE). Amendments inside the fired window ride the PROVENANCE so
  the operator sees the full episode (#6).
- **13G-family rows fire NOTHING** — passive crossings are mostly index-fund plumbing (measured ~2
  originals/yr/name vs 1 13D per 6 years on the richest real subject); firing them would re-land
  the 1.01 flood. They stay on the tape (#9) for the deferred 13G→13D-switch refinement.

Emitting a member of ``conviction_kinds`` inherits the existing composition: co-location arming
(a 13D WARMS; arming still needs a fresh breakout), the ``own_conviction_kinds`` ranking, and
``call_grade=max`` beside the other convictions — zero assembler edits (the through-line).

MASTER SWITCH (``activist_stake_enabled``, default OFF — the S1/S3/S4 precedent): registered but
``detect`` no-ops until enabled, so with the live DEFAULT_CONFIG no activist-stake event enters the
stream and every existing golden is byte-for-byte unchanged. The pure ``score`` is UNGATED (the
math stays testable); ``replay.run``'s ``--activist-stake`` / ``ALPHADECK_ACTIVIST_STAKE`` force it
on for the sig-lab pass, which finalizes the ``[PROPOSED]`` dials before the operator flips the
default. See docs/ACTIVIST_STAKE.md.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role
from domain.signal import SignalEvent
from signals.base import Detector, SignalPointInTimeData
from signals.common import entry_signal_is_live, fired_signal, source_provenance
from signals.registry import register_detector

DETECTOR_NAME = "activist_stake"

# The 13D-family form strings, BOTH naming eras (the rename trap: EDGAR moved from "SC 13D" to
# "SCHEDULE 13D" at the ~2024-12 structured-XML cutover). Kept a LITERAL here so the pure detector
# needs no ingest import (the share_creep metric-key convention); a test pins equality with the
# ingest's ``submissions.SCHEDULE13_D_FORMS`` so the two can never drift.
_13D_FORMS: frozenset[str] = frozenset({"SC 13D", "SC 13D/A", "SCHEDULE 13D", "SCHEDULE 13D/A"})


def _provenance_detail(fact: dict[str, Any]) -> dict[str, Any]:
    """One filing's evidence surface (#6): the form, dates, the activist's identity, and the
    structured %-owned where the tape has them — shown beside the call, never fire inputs."""
    return {
        "form": fact["form"],
        "filed": fact["valid_from"].isoformat(),
        "filer_cik": fact.get("filer_cik"),
        "filer_name": fact.get("filer_name"),
        "pct_owned": None if fact.get("pct_owned") is None else float(fact["pct_owned"]),
        "index_url": fact["source_ref"],
    }


def score(
    facts: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Pure: the most recent LIVE 13D-family ORIGINAL on a security -> a Key-1 CORE conviction
    SignalEvent (or None). The form type is the whole decision (#3); 13G rows and amendments
    contribute nothing to the fire (amendments inside the fired window ride the provenance).
    UNGATED by the master switch (``detect`` holds the gate)."""
    d_family = [f for f in facts if f["form"] in _13D_FORMS and f["valid_from"] <= asof]
    live_originals = [
        f
        for f in d_family
        if not f["form"].endswith("/A")
        and entry_signal_is_live(f["valid_from"], cfg.activist_13d_liveness_days, asof)
    ]
    if not live_originals:
        return None
    # the FIRE ANCHOR: the most recent live original; the accession tail makes a same-day tie
    # deterministic (live/replay byte-parity).
    anchor = max(live_originals, key=lambda f: (f["valid_from"], f["accession"]))
    # the fired episode's evidence: the anchor + every 13D-family filing (amendments included)
    # from the anchor to asof, sorted — the operator sees the whole episode, nothing hidden (#6).
    episode = sorted(
        (f for f in d_family if f["valid_from"] >= anchor["valid_from"]),
        key=lambda f: (f["valid_from"], f["accession"]),
    )
    filer = anchor.get("filer_name") or "filer unresolved"
    pct = anchor.get("pct_owned")
    pct_note = f", {float(pct):g}% of class" if pct is not None else ""
    label = (
        f"{anchor['form']} — {filer} disclosed a >5% activist stake"
        f"{pct_note} (filed {anchor['valid_from'].isoformat()})"
    )
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.ACTIVIST_STAKE,
        grade=Grade.CORE,  # fixed: a 13D is structural, capital-committed intent (R6/R9 precedent)
        score=cfg.activist_13d_score,
        label=label,
        alpha_liveness_days=cfg.activist_13d_liveness_days,
        provenance=[
            source_provenance("schedule13", f["accession"], detail=_provenance_detail(f))
            for f in episode
        ],
        asof=anchor["valid_from"],
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — SC 13D activist-stake conviction (warms). Reads ``fact_activist_stake`` via the
    point-in-time view; arming still needs a co-located confirmation (the breakout). MASTER SWITCH
    (default OFF): no-ops until ``cfg.activist_stake_enabled`` — nothing reaches live cards
    unmeasured."""
    if not cfg.activist_stake_enabled:
        return None
    return score(pit.activist_stake_facts(security_id), security_id, asof, cfg)


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
