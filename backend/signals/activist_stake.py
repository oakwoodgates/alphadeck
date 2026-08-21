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
  the operator sees the full episode (#6). The LIVE exit path (``cfg.activist_exit_terminates``,
  DEFAULT ON since 2026-08-21 — Item 4) additionally reads a SAME-FILER PRESENT sub-5% /A filed AFTER
  the anchor as a real EXIT and TERMINATES the fire; still flag-guarded (set it False and behavior is
  byte-identical to pre-flip). NULL-safe — an unparsed pct never asserts exit, and a different/self-filed
  filer never terminates (the /A's ``pct_owned`` is a fire input, hence the measured flip).
- **13G-family rows fire NOTHING** — passive crossings are mostly index-fund plumbing (measured ~2
  originals/yr/name vs 1 13D per 6 years on the richest real subject); firing them would re-land
  the 1.01 flood. They stay on the tape (#9) and now power the **13G→13D SWITCH** enrichment
  (``_switch_from_13g``): a fired 13D whose filer held a PRIOR same-filer 13G (a passive holder going
  activist) has its label + provenance enriched — never a new fire, grade, or score.
- **Mis-attributed rows are screened from the FIRE** (data-quality, measured 2026-08-19; ``_is_misattributed``):
  a 13D-family original whose ``filer_cik`` equals the SUBJECT's own CIK (self-filed — the ingest fanned a
  filing onto the wrong subject) or whose ``pct_owned`` is PRESENT but ``< 5.0`` (statutorily impossible for
  a 13D) does NOT anchor a fire — ~9% of live originals (UEC self-filed 7.7%; GameStop's 0001193125-26-202465
  fanned onto GME/EBAY at 0.01%). NULL-safe (#9): a missing CIK / pct is KEPT and still fires. The screen is
  now belt-and-suspenders: the INGEST root-cause is fixed at source (``ingest/edgar/schedule13.py`` skips an
  outbound schedule whose true subject ≠ the feed owner), and ``pipeline/repair_activist_misattribution.py``
  deletes the pre-fix self-filed rows already on the tape.

Emitting a member of ``conviction_kinds`` inherits the existing composition: co-location arming
(a 13D WARMS; arming still needs a fresh breakout), the ``own_conviction_kinds`` ranking, and
``call_grade=max`` beside the other convictions — zero assembler edits (the through-line).

MASTER SWITCH (``activist_stake_enabled``) — **DEFAULT ON since the operator flip of 2026-08-20**,
on the clean re-measure (shipped OFF behind the S1/S3/S4 precedent). Measured on real prod data
before the flip: 29 clean warm fires -> 10 arms, <=4 per thesis (no flood), every surviving fire a
real 13D — the ``_is_misattributed`` self-filed screen is what made the flip safe. Unlike the risk
detectors this is a CONVICTION: it WARMS, and the two-key gate means a fire can never arm a name
alone. ``detect`` remains GATED: set the dial False and it no-ops (registered either way). The pure
``score`` is UNGATED (the math stays testable); ``replay.run``'s ``--activist-stake`` /
``ALPHADECK_ACTIVIST_STAKE`` set it explicitly for the backtest. See docs/ACTIVIST_STAKE.md.
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
# The 13G-family strings (both eras) — the PASSIVE side. Fires nothing on its own (Fork 3), but the
# 13G→13D SWITCH reads them off the SAME subject tape to detect a passive holder going activist. Same
# no-ingest-import literal convention + drift-pin test as ``_13D_FORMS``.
_13G_FORMS: frozenset[str] = frozenset({"SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"})


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


def _norm_cik(cik: Any) -> str:
    """Leading-zero-insensitive CIK for equality (the insider issuer-self precedent: EDGAR pads CIKs to
    10 digits inconsistently, so ``0001334933`` and ``1334933`` are the SAME filer). Empty when absent.
    """
    if not cik:
        return ""
    return str(cik).strip().lstrip("0")


def _is_misattributed(fact: dict[str, Any], subject_cik: str | None) -> bool:
    """A DATA-QUALITY screen (measured 2026-08-19: ~9% of live 13D-family originals are mis-attributed —
    the ingest fanned a filing onto the wrong SUBJECT security). A row must NOT anchor a fire when either
    self-evidently-wrong shape holds:

    - **self-filed** — ``filer_cik`` equals the SUBJECT security's own CIK: a basket company filed as its
      OWN 13D subject (measured: UEC ``SCHEDULE 13D`` 0001437749-26-024641, filer==subject 0001334933, 7.7%;
      GameStop 0001193125-26-202465 fanned onto GME itself). A 13D is filed ABOUT a company by an OUTSIDE
      party, never by the subject on itself — the ingest's subject-enumeration defect, not a real stake.
    - **statutorily impossible ownership** — ``pct_owned`` is PRESENT and ``< 5.0``: a 13D requires >5% by
      rule, so a sub-5% cover (measured: GME/EBAY 0.01%) is a mis-parse or a mis-fan, never a real crossing.

    NULL-SAFE / recall-sacred (#9): a NULL/missing ``filer_cik`` or ``pct_owned`` is KEPT and still fires —
    an unparsed value never asserts "invalid" (unparsed != invalid); only a PRESENT contradiction screens.
    The form-type fire policy (13D-family originals only) is unchanged (#3)."""
    fc, sc = _norm_cik(fact.get("filer_cik")), _norm_cik(subject_cik)
    if fc and sc and fc == sc:
        return True  # (a) self-filed — the filer IS the subject company
    pct = fact.get("pct_owned")
    if pct is not None and float(pct) < 5.0:
        return True  # (b) sub-5% — statutorily impossible for a 13D original
    return False


def _stake_exited(d_family: list[dict[str, Any]], anchor: dict[str, Any]) -> bool:
    """Item 4 (flag-gated) — has THE FIRED HOLDER's stake EXITED? A later 13D-family /A **by the SAME
    filer as the fire ``anchor``**, filed strictly AFTER it (both already asof-filtered by the caller, so
    an exit /A sits between the anchor and asof), whose ``pct_owned`` is PRESENT and ``< 5.0`` is a real
    sell-BELOW-5% exit: the reporting holder dropping under the 5% threshold files a direction-blind /A.

    SAME-FILER (mirrors ``_switch_from_13g``) — the load-bearing screen: an exit is THE ANCHOR's filer
    selling down. A DIFFERENT activist's sub-5% /A on the same (multi-filer) subject, or a self-filed
    mis-attributed /A (``filer_cik`` == subject, which never matches a legit non-subject anchor filer),
    must NOT terminate the anchor's fire. This same-filer match is the correct and SUFFICIENT screen — do
    NOT reuse ``_is_misattributed`` here: its sub-5% branch would wrongly screen out the LEGITIMATE exit
    /A, which is sub-5% by design. An UNRESOLVED anchor filer (NULL/absent) never terminates (recall-safe
    #9 — the switch's ``if not anchor_filer`` precedent: no fabricated exit from an absent CIK).

    NULL-SAFE / recall-sacred (#9): ONLY a PRESENT sub-5% pct terminates — a NULL/unparsed pct on an /A
    NEVER asserts exit (unparsed != exit). Direction-blindness is preserved on the fire/re-anchor side (an
    increase or an above-5% /A still never re-anchors AND never terminates); this adds ONLY the sub-5%
    same-filer terminate path. Because pct becomes a genuine fire input here (evidence-only elsewhere),
    the whole path stays flag-guarded (``cfg.activist_exit_terminates``, LIVE since the measured flip of
    2026-08-21 — 98.1% /A-cover parse-reliability, 0 verdict changes in the read-only prod measure).
    """
    anchor_filer = _norm_cik(anchor.get("filer_cik"))
    if not anchor_filer:
        return (
            False  # an unresolved anchor filer can't be matched to an exit /A (no fabricated exit)
        )
    for f in d_family:
        if not f["form"].endswith("/A"):
            continue
        if f["valid_from"] <= anchor["valid_from"]:
            continue  # must strictly POST-DATE the fire anchor (a give-back after the crossing)
        if _norm_cik(f.get("filer_cik")) != anchor_filer:
            continue  # only the FIRED HOLDER's OWN sell-down is an exit (same-filer, like the switch)
        pct = f.get("pct_owned")
        if pct is not None and float(pct) < 5.0:
            return True  # the anchor's filer dropped below 5% on a later /A = a real exit
    return False


def _switch_from_13g(
    facts: list[dict[str, Any]],
    anchor: dict[str, Any],
    asof: date,
    cfg: CallConfig,
    subject_cik: str | None,
) -> dict[str, Any] | None:
    """The 13G→13D SWITCH tell — a passive holder going activist (the loudest version of the signal;
    docs/ACTIVIST_STAKE.md). Given the firing 13D ``anchor``, find the EARLIEST prior 13G-family filing
    by the SAME filer (normalized CIK) on this subject's tape — the moment they first disclosed a
    PASSIVE stake — provided it predates the 13D by at least ``cfg.activist_switch_min_gap_days``.
    Returns that earliest 13G (the escalation evidence) or ``None`` (no switch).

    Guards:
    - **same filer, both resolved** — an UNRESOLVED filer on either side never asserts a match (a
      switch is never fabricated from an absent CIK; recall-safe #9).
    - **mis-attribution screen** — reuses ``_is_misattributed`` so a self-filed / sub-5% mis-fanned 13G
      can never fabricate a false switch.
    - **minimum gap** — a 13G and 13D filed ~a day apart is a RE-CLASSIFICATION / correction, not a
      passive→active escalation (measured: QNTM, Malone Wealth 13G 2025-06-11 → 13D 2025-06-12); the
      dial screens it out while keeping real escalations (measured: Gemini, Winklevoss ~185 days).

    KNOWN v1 BOUND — the affiliate edge: a control person / insider vehicle (e.g. Winklevoss Capital
    Fund → the Gemini exchange) passes ``filer≠subject`` and reads as a switch though it is a
    governance reshuffle, not an outside activist's escalation. v1 does not distinguish the two (both
    are a same-party 13G→13D). It only ever ENRICHES an already-firing CORE — never a new fire."""
    anchor_filer = _norm_cik(anchor.get("filer_cik"))
    if not anchor_filer:
        return (
            None  # an unresolved 13D filer can't be matched to a prior 13G (no fabricated switch)
        )
    priors = [
        g
        for g in facts
        if g["form"] in _13G_FORMS
        and g["valid_from"] <= asof
        and g["valid_from"] < anchor["valid_from"]
        and _norm_cik(g.get("filer_cik")) == anchor_filer
        and not _is_misattributed(g, subject_cik)
    ]
    if not priors:
        return None
    # the EARLIEST prior 13G = when they first went passive (the accession tail makes a same-day tie
    # deterministic, live/replay byte-parity).
    earliest = min(priors, key=lambda g: (g["valid_from"], g["accession"]))
    if (anchor["valid_from"] - earliest["valid_from"]).days < cfg.activist_switch_min_gap_days:
        return None  # too close to the 13D — a re-classification, not an escalation
    return earliest


def score(
    facts: list[dict[str, Any]],
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
    subject_cik: str | None = None,
) -> SignalEvent | None:
    """Pure: the most recent LIVE 13D-family ORIGINAL on a security -> a Key-1 CORE conviction
    SignalEvent (or None). The form type is the whole decision (#3); 13G rows and amendments
    contribute nothing to the fire (amendments inside the fired window ride the provenance). A
    DATA-QUALITY screen (``_is_misattributed``) additionally drops a self-filed (``filer_cik`` ==
    ``subject_cik``) or statutorily-impossible sub-5% original before it can anchor — ``subject_cik``
    is the SUBJECT security's own CIK, resolved by ``detect`` from the master (``None`` when it can't
    resolve → only the pure sub-5% half applies, recall-safe). A fired 13D is additionally ENRICHED
    when it is a 13G→13D SWITCH — a prior same-filer 13G on the tape (``_switch_from_13g``: a passive
    holder going activist) — label + provenance only, never the fire/grade/score. UNGATED by the
    master switch (``detect`` holds the gate)."""
    d_family = [f for f in facts if f["form"] in _13D_FORMS and f["valid_from"] <= asof]
    live_originals = [
        f
        for f in d_family
        if not f["form"].endswith("/A")
        and entry_signal_is_live(f["valid_from"], cfg.activist_13d_liveness_days, asof)
        and not _is_misattributed(f, subject_cik)
    ]
    if not live_originals:
        return None
    # the FIRE ANCHOR: the most recent live original; the accession tail makes a same-day tie
    # deterministic (live/replay byte-parity).
    anchor = max(live_originals, key=lambda f: (f["valid_from"], f["accession"]))
    # Item 4 — activist EXIT termination (flag-guarded; LIVE default ON since 2026-08-21, an explicit-off
    # config is byte-identical to pre-flip). Guarded EARLY and short-circuited on the flag: a same-filer
    # present sub-5% /A after the anchor means the holder sold BELOW 5% (a real exit), so the CORE fire
    # terminates rather than staying live the full liveness window. Returning None is the clean
    # termination — the exit /A already post-dates the anchor and predates asof, so any liveness-truncation
    # would read dead here too.
    if cfg.activist_exit_terminates and _stake_exited(d_family, anchor):
        return None
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
    provenance = [
        source_provenance("schedule13", f["accession"], detail=_provenance_detail(f))
        for f in episode
    ]
    # The 13G→13D SWITCH enrichment: a passive holder (a prior same-filer 13G) going activist is a
    # stronger tell. ENRICH only — the label + provenance; the fire, grade (CORE), and score are
    # untouched, so a switch can never flood or re-grade. The prior 13G rides AHEAD of the episode so
    # the chronology reads 13G → 13D → amendments (#6).
    switch = _switch_from_13g(facts, anchor, asof, cfg, subject_cik)
    if switch is not None:
        label += (
            " — ESCALATED from a prior 13G passive stake filed "
            f"{switch['valid_from'].isoformat()}"
        )
        provenance = [
            source_provenance("schedule13", switch["accession"], detail=_provenance_detail(switch))
        ] + provenance
    return fired_signal(
        detector=DETECTOR_NAME,
        security_id=security_id,
        role=Role.ENTRY_TRIGGER,
        kind=Kind.ACTIVIST_STAKE,
        grade=Grade.CORE,  # fixed: a 13D is structural, capital-committed intent (R6/R9 precedent)
        score=cfg.activist_13d_score,
        label=label,
        alpha_liveness_days=cfg.activist_13d_liveness_days,
        provenance=provenance,
        asof=anchor["valid_from"],
    )


def detect(
    pit: SignalPointInTimeData,
    security_id: UUID,
    asof: date,
    cfg: CallConfig = DEFAULT_CONFIG,
) -> SignalEvent | None:
    """Key 1 — SC 13D activist-stake conviction (warms). Reads ``fact_activist_stake`` via the
    point-in-time view; arming still needs a co-located confirmation (the breakout). Resolves the
    SUBJECT security's own CIK from the master (``security_name``'s identity-read sibling) to power the
    self-filed data-quality screen (``_is_misattributed``). MASTER SWITCH — **DEFAULT ON since
    2026-08-20**: gated on ``cfg.activist_stake_enabled``, so setting it False still no-ops the
    detector."""
    if not cfg.activist_stake_enabled:
        return None
    return score(
        pit.activist_stake_facts(security_id),
        security_id,
        asof,
        cfg,
        subject_cik=pit.security_cik(security_id),
    )


DETECTOR = register_detector(Detector(name=DETECTOR_NAME, detect=detect))
