"""Band 03 S5 — the SC 13D activist-stake CONVICTION trigger (pure score + the master switch + the
full-pipeline standing guard). No DB: the pure ``score`` takes fact rows directly; ``detect`` is
exercised through a stub pit; the two-key gate runs the REAL pipeline (``assemble_from_pit`` ->
every registered detector) over a COMPLETE in-memory point-in-time fake.

The REAL instances (test-honesty — cited filings, fixed timestamps, no fitting):

- the GOLDEN: atai's ORIGINAL SC 13D about COMPASS Pathways (CMPS, an answer-key basket member) —
  accession 0001193125-21-171001, filed 2021-05-24, ATAI Life Sciences B.V. (CIK 0001840904);
- the fork-4 proof: the real SCHEDULE 13D/A accession 0001140361-26-005810 (filed 2026-02-17,
  AtaiBeckley Inc., 4.96% — a sell-down BELOW 5%) must NOT fire a fresh CORE at a 2026 asof;
- the negative: MindMed's real tape holds ONLY 13G-family rows — the detector fires nothing.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role, State
from ingest.edgar.submissions import SCHEDULE13_D_FORMS, SCHEDULE13_G_FORMS
from pipeline.core import assemble_from_pit
from signals import activist_stake
from signals.base import window_prices
from tests.calls.factories import ASOF, SID, make_thesis

_ON = DEFAULT_CONFIG.model_copy(update={"activist_stake_enabled": True})
_LIVENESS = DEFAULT_CONFIG.activist_13d_liveness_days
_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)

# The real CMPS × atai episode (cited; see the module docstring).
_CMPS_13D_ACC = "0001193125-21-171001"
_CMPS_13DA_ACC = "0001140361-26-005810"


def _fact(
    accession="ACC-13D",
    form="SC 13D",
    filed=ASOF,
    filer_cik="0001111111",
    filer_name="Activist Fund LP",
    pct=None,
):
    return {
        "accession": accession,
        "form": form,
        "filer_cik": filer_cik,
        "filer_name": filer_name,
        "pct_owned": pct,
        "filed": filed,
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
        "valid_from": filed,
    }


def _real_cmps_original(filed=date(2021, 5, 24)):
    return _fact(
        accession=_CMPS_13D_ACC,
        form="SC 13D",
        filed=filed,
        filer_cik="0001840904",
        filer_name="ATAI Life Sciences B.V.",
    )


def _real_cmps_exit_amendment():
    return _fact(
        accession=_CMPS_13DA_ACC,
        form="SCHEDULE 13D/A",
        filed=date(2026, 2, 17),
        filer_cik="0002081043",
        filer_name="AtaiBeckley Inc.",
        pct=4.96,
    )


# --- the pure score ---------------------------------------------------------------------------------


def test_13d_original_fires_a_core_conviction_from_the_dials():
    e = activist_stake.score([_fact(pct=5.6)], SID, ASOF)
    assert e is not None and e.fired
    assert e.role is Role.ENTRY_TRIGGER and e.kind is Kind.ACTIVIST_STAKE
    assert e.grade is Grade.CORE  # fixed in the detector — structural, capital-committed intent
    assert e.score == DEFAULT_CONFIG.activist_13d_score  # the [PROPOSED] dial, never hardcoded
    assert e.alpha_liveness_days == _LIVENESS
    assert e.asof == ASOF  # fire date = the ORIGINAL's filing date
    assert "SC 13D" in e.label and "Activist Fund LP" in e.label and "5.6% of class" in e.label
    (p,) = e.provenance
    assert p.source == "schedule13" and p.ref == "ACC-13D"
    assert p.detail["form"] == "SC 13D" and p.detail["filer_name"] == "Activist Fund LP"
    assert p.detail["index_url"].endswith("-index.htm")  # checkable source (#6)


def test_renamed_era_schedule_13d_fires_identically():
    """The rename trap's detector half: a post-2024-12 "SCHEDULE 13D" original is the same event."""
    e = activist_stake.score([_fact(form="SCHEDULE 13D")], SID, ASOF)
    assert e is not None and e.grade is Grade.CORE and "SCHEDULE 13D" in e.label


def test_13g_family_never_fires():
    """FORK 3 (operator-confirmed): 13G = passive — mostly index-fund plumbing (measured ~2
    originals/yr/name). It stays ON the tape (#9) but fires NOTHING, either naming era."""
    for form in ("SC 13G", "SC 13G/A", "SCHEDULE 13G", "SCHEDULE 13G/A"):
        assert activist_stake.score([_fact(form=form)], SID, ASOF) is None, form


def test_amendment_never_fires_or_reanchors():
    """FORK 4 (operator-confirmed): a 13D/A is direction-blind (increase, sell-down, and exit file
    identically) — an amendment alone fires nothing, and a fresh /A must NOT re-anchor a stale
    original back to life."""
    for form in ("SC 13D/A", "SCHEDULE 13D/A"):
        assert activist_stake.score([_fact(form=form)], SID, ASOF) is None, form
    stale_original = _fact(filed=ASOF - timedelta(days=_LIVENESS + 40))
    fresh_amendment = _fact(accession="ACC-A", form="SC 13D/A", filed=ASOF)
    assert activist_stake.score([stale_original, fresh_amendment], SID, ASOF) is None


def test_real_cmps_atai_golden_fires_on_the_original():
    """THE GOLDEN: the real atai SC 13D about CMPS, scored inside its window (asof 2021-06-15) —
    a CORE Key-1 conviction with the real accession + filer in the provenance."""
    e = activist_stake.score([_real_cmps_original()], SID, date(2021, 6, 15))
    assert e is not None and e.fired and e.grade is Grade.CORE
    assert e.asof == date(2021, 5, 24)
    assert "ATAI Life Sciences B.V." in e.label
    assert [p.ref for p in e.provenance] == [_CMPS_13D_ACC]


def test_real_cmps_exit_amendment_must_not_fire_today():
    """FORK 4 ON THE REAL DATA: CMPS's tape today = the 2021 original (long stale) + the real
    2026-02-17 SCHEDULE 13D/A reporting 4.96% — a sell-down BELOW 5%. Latest-wins-including-/A
    would fire a fresh CORE on an exit filing; the original-anchor rule fires NOTHING."""
    tape = [_real_cmps_original(), _real_cmps_exit_amendment()]
    assert activist_stake.score(tape, SID, date(2026, 8, 18)) is None


def test_real_mindmed_13g_tape_fires_nothing():
    """The negative instance: MindMed (CIK 1813814) has NO 13D in its entire submissions history —
    only 13G-family rows (verified live 2026-08-18; the famous FCM MM Holdings campaign was DFAN14A
    proxy material, never a 13D). Two real rows from that tape fire nothing."""
    # real accessions + forms + dates from the live tape; the filers are irrelevant to the fire
    # decision (form type is the whole rule) and unasserted here, so they stay None — no fabricated
    # identity on a real accession.
    tape = [
        _fact(
            accession="0000938206-26-000012",
            form="SCHEDULE 13G",
            filed=date(2026, 2, 17),
            filer_cik=None,
            filer_name=None,
        ),
        _fact(
            accession="0002012383-24-003562",
            form="SC 13G",
            filed=date(2024, 11, 8),
            filer_cik=None,
            filer_name=None,
        ),
    ]
    assert activist_stake.score(tape, SID, date(2026, 8, 18)) is None


def test_liveness_is_inclusive_at_the_horizon_and_expires():
    at_horizon = _fact(filed=ASOF - timedelta(days=_LIVENESS))
    assert activist_stake.score([at_horizon], SID, ASOF) is not None
    stale = _fact(filed=ASOF - timedelta(days=_LIVENESS + 1))
    assert activist_stake.score([stale], SID, ASOF) is None


def test_future_filed_fact_is_invisible_no_lookahead():
    """A pure-score honesty re-check (the as-of read already guarantees it live): a filing dated
    after asof contributes nothing."""
    assert activist_stake.score([_fact(filed=ASOF + timedelta(days=1))], SID, ASOF) is None


def test_most_recent_live_original_wins_and_the_episode_rides_provenance():
    """Two live originals: the most recent anchors the fire (accession tiebreak deterministic);
    the fired EPISODE — the anchor + the amendments from it forward — rides the provenance (#6),
    while the older separate episode does not."""
    old = _fact(accession="ACC-OLD", filed=ASOF - timedelta(days=100))
    new = _fact(accession="ACC-NEW", filed=ASOF - timedelta(days=10))
    amendment = _fact(accession="ACC-NEW-A", form="SC 13D/A", filed=ASOF - timedelta(days=5))
    e = activist_stake.score([amendment, old, new], SID, ASOF)
    assert e is not None and e.asof == ASOF - timedelta(days=10)
    assert [p.ref for p in e.provenance] == ["ACC-NEW", "ACC-NEW-A"]  # sorted, episode-scoped


# --- the data-quality screen (measured 2026-08-19: ~9% of live originals are mis-attributed) ---------
# Real, cited instances (test-honesty — no fabricated shapes to force an outcome):
#  - UEC: a SCHEDULE 13D (acc 0001437749-26-024641, filed 2026-07-28) whose filer_cik == URANIUM ENERGY
#    CORP's OWN cik 0001334933 (7.7%) — the ingest fanned a filing onto the wrong subject (self-filed).
#  - GameStop's SCHEDULE 13D (acc 0001193125-26-202465, filer 0001326380, filed 2026-05-04) fanned onto
#    both GME (self) and EBAY at 0.01% of class — statutorily impossible for a 13D (requires >5%).


def test_self_filed_13d_is_screened_from_the_fire():
    """RULE (a) self-filed: the real UEC SCHEDULE 13D whose filer_cik equals the SUBJECT's own cik
    (0001334933) does NOT anchor a fire — a company is never its own 13D subject. pct 7.7 (≥5) is fine,
    so this isolates the self-filed half; and it FIRES when the subject is a different company, proving
    the screen is targeted, not a blanket veto."""
    uec_self = _fact(
        accession="0001437749-26-024641",
        form="SCHEDULE 13D",
        filed=date(2026, 7, 28),
        filer_cik="0001334933",
        filer_name="URANIUM ENERGY CORP",
        pct=7.7,
    )
    asof = date(2026, 8, 18)
    assert activist_stake.score([uec_self], SID, asof, subject_cik="0001334933") is None
    assert activist_stake.score([uec_self], SID, asof, subject_cik="0009999999") is not None


def test_self_filed_screen_is_leading_zero_insensitive():
    """EDGAR pads CIKs to 10 digits inconsistently: a filer '1334933' and a subject '0001334933' are the
    SAME company. The screen normalizes both (the insider issuer-self precedent) so the pad can't smuggle
    a self-filing through."""
    row = _fact(filer_cik="1334933", pct=8.0)
    assert activist_stake.score([row], SID, ASOF, subject_cik="0001334933") is None


def test_sub_five_pct_13d_is_screened_from_the_fire():
    """RULE (b) statutorily-impossible ownership: GameStop's real 13D fanned onto EBAY at 0.01% of class
    — a 13D requires >5%, so a 0.01% cover is a mis-fan, never a real crossing. Screened whether or not a
    (different) subject cik resolves, since rule (b) reads only the fact row."""
    ebay_misfan = _fact(
        accession="0001193125-26-202465",
        form="SCHEDULE 13D",
        filed=date(2026, 5, 4),
        filer_cik="0001326380",
        filer_name="GameStop Corp.",
        pct=0.01,
    )
    asof = date(2026, 6, 1)
    # eBay's cik (≠ the GameStop filer) → rule (a) can't apply; rule (b) screens it.
    assert activist_stake.score([ebay_misfan], SID, asof, subject_cik="0001065088") is None
    # and with NO subject cik resolved (the replay-mirror path), the pure sub-5% half still screens it.
    assert activist_stake.score([ebay_misfan], SID, asof, subject_cik=None) is None


def test_exactly_five_pct_is_kept_the_threshold_is_below_not_at():
    """The screen is ``< 5.0``, not ``<= 5.0``: a 13D reporting EXACTLY 5% (the real LRHC 2026-06-23 at
    5%) is a legitimate crossing and FIRES. Off-by-one honesty on the statutory threshold."""
    e = activist_stake.score([_fact(pct=5.0)], SID, ASOF, subject_cik="0009999999")
    assert e is not None and e.grade is Grade.CORE


def test_null_pct_valid_shape_original_still_fires_recall():
    """RECALL-SACRED (#9): a valid-shape 13D with an UNPARSED pct (NULL), filer ≠ subject, STILL fires a
    CORE. An unparsed value is not an invalid one — the screen never drops a real crossing on absence.
    """
    e = activist_stake.score(
        [_fact(filer_cik="0001111111", pct=None)], SID, ASOF, subject_cik="0009999999"
    )
    assert e is not None and e.grade is Grade.CORE


def test_normal_13d_filer_differs_from_subject_fires_core():
    """The happy path with a subject resolved: filer ≠ subject, pct ≥ 5 → the screen passes it and it
    fires a CORE (the real RLBY shape: 7.01%). Proves the screen doesn't over-reach a clean original.
    """
    e = activist_stake.score(
        [_fact(filer_cik="0001111111", pct=7.01)], SID, ASOF, subject_cik="0009999999"
    )
    assert e is not None and e.grade is Grade.CORE and "7.01% of class" in e.label


def test_detect_resolves_subject_cik_from_the_master_and_screens_self_filed():
    """THE WIRING: detect() resolves the subject's own cik via pit.security_cik (the master read) and
    passes it to the screen. A self-filed tape (filer == the resolved subject cik) fires NOTHING through
    the full detect path with the switch ON; the SAME tape under a different subject cik fires."""
    self_filed = [_fact(filer_cik="0001334933", pct=7.7)]
    pit_self = SimpleNamespace(
        activist_stake_facts=lambda sid: [dict(f) for f in self_filed],
        security_cik=lambda sid: "0001334933",
    )
    assert activist_stake.detect(pit_self, SID, ASOF, _ON) is None

    pit_other = SimpleNamespace(
        activist_stake_facts=lambda sid: [dict(f) for f in self_filed],
        security_cik=lambda sid: "0009999999",
    )
    assert activist_stake.detect(pit_other, SID, ASOF, _ON) is not None


def test_master_switch_now_defaults_on_but_still_gates_detect():
    """The switch is now LIVE by default (activist_stake_enabled=True, ratified on the 2026-08-20
    clean re-measure: 29 clean warm fires -> 10 arms, <=4/thesis, every survivor a real 13D) —
    ``detect`` FIRES under DEFAULT_CONFIG. Explicitly OFF it still no-ops WITHOUT reading the pit
    (the throwing accessor proves it never runs); the pure ``score`` above ignores the switch
    entirely — it takes no cfg (the insider_sell / share_creep precedent)."""
    assert (
        DEFAULT_CONFIG.activist_stake_enabled is True
    )  # the ratified LIVE default (re-measure 2026-08-20)

    off = DEFAULT_CONFIG.model_copy(update={"activist_stake_enabled": False})
    pit_off = SimpleNamespace(
        activist_stake_facts=lambda sid: (_ for _ in ()).throw(AssertionError("pit read while OFF"))
    )
    assert (
        activist_stake.detect(pit_off, SID, ASOF, off) is None
    )  # OFF -> no-op, never reads the pit

    assert activist_stake.score([_fact()], SID, ASOF) is not None  # score is ungated (takes no cfg)

    pit_on = SimpleNamespace(
        activist_stake_facts=lambda sid: [_fact()],
        security_cik=lambda sid: None,  # detect now also resolves the subject cik for the screen
    )
    e = activist_stake.detect(pit_on, SID, ASOF, DEFAULT_CONFIG)  # LIVE default -> fires
    assert e is not None and e.kind is Kind.ACTIVIST_STAKE


def test_activist_kind_inherits_conviction_membership():
    """The wiring claim: Kind.ACTIVIST_STAKE ∈ conviction_kinds (co-location arming) AND ∈
    own_conviction_kinds (the is_own ranking) — the derived property inherits it automatically,
    with zero assembler edits."""
    assert Kind.ACTIVIST_STAKE in DEFAULT_CONFIG.conviction_kinds
    assert Kind.ACTIVIST_STAKE in DEFAULT_CONFIG.own_conviction_kinds


def test_detector_13d_forms_match_the_ingest_constant():
    """The drift pin: the detector keeps the 13D-family strings as a LOCAL literal (the share_creep
    no-ingest-import convention) — this asserts it can never drift from the ingest's set."""
    assert activist_stake._13D_FORMS == SCHEDULE13_D_FORMS


def test_detector_13g_forms_match_the_ingest_constant():
    """The 13G drift pin (the switch reads the passive side off the same tape): the detector's local
    ``_13G_FORMS`` literal can never drift from the ingest's ``SCHEDULE13_G_FORMS``."""
    assert activist_stake._13G_FORMS == SCHEDULE13_G_FORMS


# --- the 13G→13D switch enrichment (real, cited: Gemini escalation + QNTM re-classification) ---------
# Gemini (subject CIK 0002055592): Winklevoss Capital Fund LLC (0002085726) filed a passive SCHEDULE
# 13G (0001104659-25-112696, 2025-11-14) then a SCHEDULE 13D (0001193125-26-229103, 2026-05-18, 65.1%)
# — ~185 days later (verified live 2026-08-20). QNTM (subject 0001771885): Malone Wealth Ventures LLC
# (0002072045) filed a SCHEDULE 13G (0002072045-25-000001, 2025-06-11) then a SCHEDULE 13D
# (0002072045-25-000002, 2025-06-12, 14.6%) ONE DAY later — a re-classification, not an escalation.
_GEMI_SUBJECT, _GEMI_FILER = "0002055592", "0002085726"
_QNTM_SUBJECT, _QNTM_FILER = "0001771885", "0002072045"


def _gemi_13g():
    return _fact(
        accession="0001104659-25-112696",
        form="SCHEDULE 13G",
        filed=date(2025, 11, 14),
        filer_cik=_GEMI_FILER,
        filer_name="Winklevoss Capital Fund, LLC",
        pct=None,
    )


def _gemi_13d():
    return _fact(
        accession="0001193125-26-229103",
        form="SCHEDULE 13D",
        filed=date(2026, 5, 18),
        filer_cik=_GEMI_FILER,
        filer_name="Winklevoss Capital Fund, LLC",
        pct=65.1,
    )


def test_13g_to_13d_switch_enriches_the_fire_real_gemi():
    """THE SWITCH on real data: the Winklevoss 13G → 13D escalation enriches the fired CORE — the label
    names the prior 13G's date and the 13G rides AHEAD of the 13D in the provenance (#6). Also the
    AFFILIATE EDGE (a Winklevoss vehicle → the Winklevoss exchange): it reads as a switch though it is a
    governance reshuffle — a known v1 bound (a same-party 13G→13D is indistinguishable from an outside
    activist's escalation in v1)."""
    e = activist_stake.score(
        [_gemi_13g(), _gemi_13d()], SID, date(2026, 6, 1), subject_cik=_GEMI_SUBJECT
    )
    assert e is not None and e.grade is Grade.CORE
    assert e.asof == date(2026, 5, 18)  # the 13D anchors the fire
    assert "65.1% of class" in e.label  # the base fire label is intact
    assert "ESCALATED from a prior 13G passive stake filed 2025-11-14" in e.label
    assert [p.ref for p in e.provenance] == [
        "0001104659-25-112696",  # the prior 13G, first
        "0001193125-26-229103",  # then the firing 13D
    ]


def test_one_day_reclassification_is_not_a_switch_real_qntm():
    """THE MIN-GAP GUARD on real data: Malone's 13G → 13D ONE DAY apart is a re-classification, not an
    escalation. The 13D still fires CORE (a real >5% stake) but is NOT enriched (gap 1d < min-gap 30),
    and only the 13D rides the provenance."""
    g13 = _fact(
        accession="0002072045-25-000001",
        form="SCHEDULE 13G",
        filed=date(2025, 6, 11),
        filer_cik=_QNTM_FILER,
        filer_name="Malone Wealth Ventures LLC",
        pct=None,
    )
    d13 = _fact(
        accession="0002072045-25-000002",
        form="SCHEDULE 13D",
        filed=date(2025, 6, 12),
        filer_cik=_QNTM_FILER,
        filer_name="Malone Wealth Ventures LLC",
        pct=14.6,
    )
    e = activist_stake.score([g13, d13], SID, date(2025, 7, 1), subject_cik=_QNTM_SUBJECT)
    assert e is not None and e.grade is Grade.CORE  # the 13D still fires normally
    assert "ESCALATED" not in e.label
    assert [p.ref for p in e.provenance] == ["0002072045-25-000002"]  # only the 13D episode


def test_switch_needs_the_same_filer_a_different_filers_13g_does_not_escalate():
    """The escalation must be the SAME party: a prior 13G by a DIFFERENT filer (Gemini's tape also
    carries a Morgan Creek 13G near the Winklevoss 13D) never creates a switch."""
    other_g = _fact(
        accession="0001213900-25-090099",
        form="SCHEDULE 13G",
        filed=date(2025, 9, 22),
        filer_cik="0001878908",
        filer_name="Morgan Creek",
        pct=None,
    )
    e = activist_stake.score(
        [other_g, _gemi_13d()], SID, date(2026, 6, 1), subject_cik=_GEMI_SUBJECT
    )
    assert e is not None and "ESCALATED" not in e.label
    assert [p.ref for p in e.provenance] == ["0001193125-26-229103"]


def test_switch_min_gap_is_a_dial_not_a_magic_number():
    """The min-gap is a ``CallConfig`` dial, never hardcoded: raise it above the real Gemini gap and the
    SAME escalation stops counting as a switch (config-driven, the no-magic-number discipline)."""
    wide = DEFAULT_CONFIG.model_copy(update={"activist_switch_min_gap_days": 400})
    e = activist_stake.score(
        [_gemi_13g(), _gemi_13d()], SID, date(2026, 6, 1), wide, subject_cik=_GEMI_SUBJECT
    )
    assert e is not None and e.grade is Grade.CORE and "ESCALATED" not in e.label


def test_switch_ignores_a_mis_attributed_prior_13g():
    """The switch reuses ``_is_misattributed`` so a mis-fanned prior 13G can't fabricate a false
    escalation. A SAME-filer 13G/A reporting a sub-5% stake (a sell-down BELOW the >5% threshold — a
    stake-death shape, like the real CMPS 13D/A at 4.96%) is screened, so the clean outside 13D fires
    WITHOUT the switch enrichment even though the filer CIKs match and the gap is wide."""
    sub5_g = _fact(
        accession="ACC-SUB5-G",
        form="SCHEDULE 13G/A",
        filed=date(2025, 11, 14),
        filer_cik=_GEMI_FILER,
        filer_name="Winklevoss Capital Fund, LLC",
        pct=4.0,
    )
    e = activist_stake.score(
        [sub5_g, _gemi_13d()], SID, date(2026, 6, 1), subject_cik=_GEMI_SUBJECT
    )
    assert e is not None and e.grade is Grade.CORE and "ESCALATED" not in e.label


# --- the activist EXIT termination (Item 4, flag-gated; default OFF = byte-identical to today) --------
# THE FIRED HOLDER selling BELOW 5% files a direction-blind /A; when cfg.activist_exit_terminates is ON,
# a SAME-FILER (the anchor's filer) PRESENT sub-5% /A filed AFTER the anchor terminates the CORE fire.
# Screens (mirroring the switch): a DIFFERENT activist's /A, a self-filed (filer==subject) /A, and an
# UNRESOLVED anchor filer never terminate. NULL-safe (#9): only a PRESENT sub-5% pct terminates; an
# unparsed pct or an above-5% /A never does (direction-blindness preserved).

_EXIT_ON = DEFAULT_CONFIG.model_copy(update={"activist_exit_terminates": True})
_HOLDER = "0001111111"  # the fired holder's filer CIK (the _fact default)
_SUBJECT = "0009999999"  # the subject security's own CIK (a real 13D has filer != subject)


def _live_original(pct=7.0, filer_cik=_HOLDER):
    """A live 13D ORIGINAL (filer ≠ subject, pct ≥ 5) filed 10d before ASOF — inside its liveness window."""
    return _fact(
        accession="ACC-ORIG",
        form="SC 13D",
        filed=ASOF - timedelta(days=10),
        pct=pct,
        filer_cik=filer_cik,
    )


def _amendment(pct, filed=ASOF - timedelta(days=2), filer_cik=_HOLDER):
    return _fact(accession="ACC-A", form="SC 13D/A", filed=filed, pct=pct, filer_cik=filer_cik)


def test_exit_flag_off_is_byte_identical_a_sub5_amendment_does_not_terminate():
    """Item 4 flag OFF (the DEFAULT): a same-filer present sub-5% /A after the anchor changes NOTHING —
    the CORE fires exactly as today, and the /A merely rides the provenance (an ordinary episode
    amendment). The guard short-circuits on the flag, so the disabled path is byte-identical to
    pre-Item-4 behavior."""
    tape = [_live_original(), _amendment(pct=4.0)]
    off = activist_stake.score(tape, SID, ASOF, DEFAULT_CONFIG, subject_cik=_SUBJECT)
    assert off is not None and off.grade is Grade.CORE  # still fires with the flag off
    assert off.asof == ASOF - timedelta(days=10)  # anchored on the original, unchanged
    assert [p.ref for p in off.provenance] == ["ACC-ORIG", "ACC-A"]  # /A rides provenance as today


def test_exit_flag_on_same_filer_sub5_amendment_terminates_the_fire():
    """Item 4 flag ON (a): a SAME-FILER (the anchor's filer) PRESENT sub-5% /A filed after the anchor is a
    real sell-below-5% exit — the CORE fire TERMINATES (returns None) instead of staying live the full
    180d liveness window."""
    tape = [_live_original(filer_cik=_HOLDER), _amendment(pct=4.0, filer_cik=_HOLDER)]
    assert activist_stake.score(tape, SID, ASOF, _EXIT_ON, subject_cik=_SUBJECT) is None


def test_exit_flag_on_a_different_filer_sub5_amendment_does_not_terminate():
    """Item 4 flag ON (b): a DIFFERENT activist's sub-5% /A on the same (multi-filer) subject is NOT the
    fired holder selling down — it must not terminate the anchor's fire (the same-filer screen, mirroring
    the 13G→13D switch)."""
    tape = [_live_original(filer_cik=_HOLDER), _amendment(pct=4.0, filer_cik="0002222222")]
    e = activist_stake.score(tape, SID, ASOF, _EXIT_ON, subject_cik=_SUBJECT)
    assert e is not None and e.grade is Grade.CORE  # a different filer's /A never terminates


def test_exit_flag_on_a_self_filed_sub5_amendment_does_not_terminate():
    """Item 4 flag ON (c): a self-filed (filer == subject) sub-5% /A is a mis-attributed row, not the
    holder's exit — the SAME-FILER screen handles it (subject ≠ the legit non-subject anchor filer), so
    it never terminates WITHOUT reusing _is_misattributed (whose sub-5% branch would wrongly screen a
    LEGITIMATE sub-5% exit /A)."""
    tape = [_live_original(filer_cik=_HOLDER), _amendment(pct=4.0, filer_cik=_SUBJECT)]
    e = activist_stake.score(tape, SID, ASOF, _EXIT_ON, subject_cik=_SUBJECT)
    assert e is not None and e.grade is Grade.CORE  # self-filed /A ≠ the anchor's filer -> no exit


def test_exit_flag_on_a_null_filer_anchor_never_terminates_recall():
    """Item 4 flag ON (d): an UNRESOLVED (NULL) anchor filer can't be matched to an exit /A, so the fire
    never terminates (recall-safe #9 — no fabricated exit from an absent CIK, the switch's precedent).
    The original still fires (a NULL filer with pct ≥ 5 is not mis-attributed)."""
    tape = [_live_original(filer_cik=None), _amendment(pct=4.0, filer_cik=_HOLDER)]
    e = activist_stake.score(tape, SID, ASOF, _EXIT_ON, subject_cik=_SUBJECT)
    assert e is not None and e.grade is Grade.CORE  # NULL anchor filer -> can't assert an exit


def test_exit_flag_on_a_null_pct_amendment_never_terminates_recall():
    """Item 4 NULL-SAFE (#9): with the flag ON, a same-filer /A with an UNPARSED (NULL) pct NEVER asserts
    exit — the CORE still fires. Unparsed != exit; the terminate path never drops a real stake on absence.
    """
    tape = [_live_original(), _amendment(pct=None)]
    e = activist_stake.score(tape, SID, ASOF, _EXIT_ON, subject_cik=_SUBJECT)
    assert e is not None and e.grade is Grade.CORE


def test_exit_flag_on_an_above5_amendment_never_terminates_direction_blind():
    """Item 4 direction-blindness preserved on the fire side: with the flag ON, a same-filer /A reporting
    an INCREASE (above 5%) never terminates — only a sub-5% exit does. An add is not an exit."""
    tape = [_live_original(), _amendment(pct=8.0)]
    e = activist_stake.score(tape, SID, ASOF, _EXIT_ON, subject_cik=_SUBJECT)
    assert e is not None and e.grade is Grade.CORE


def test_exit_flag_on_ignores_a_sub5_amendment_that_predates_the_anchor():
    """Item 4 post-date guard: a same-filer sub-5% /A that PRE-dates the fire anchor (amending an older,
    superseded original) never terminates the fresh anchor's fire — only an exit AFTER the crossing does.
    """
    old_amend = _fact(
        accession="ACC-OLD-A",
        form="SC 13D/A",
        filed=ASOF - timedelta(days=40),
        pct=3.0,
        filer_cik=_HOLDER,
    )
    tape = [_live_original(pct=7.0), old_amend]  # the /A (ASOF-40) predates the ASOF-10 anchor
    e = activist_stake.score(tape, SID, ASOF, _EXIT_ON, subject_cik=_SUBJECT)
    assert e is not None and e.grade is Grade.CORE


# --- the two-key gate + the standing guard (the REAL pipeline over a COMPLETE fake) -----------------

# A flat 50.0 base long enough for the 52-week detectors, then one 65.0 close on 3x volume — the
# breakout confirmation bar (the dearm-replay arc's shape). Without the spike the tape is flat and
# NO confirmation exists.
_FLAT = [50.0] * 260
_VOLS = [1000.0] * 260


def _bars(spike: bool):
    closes = _FLAT + ([65.0] if spike else [])
    vols = _VOLS + ([3000.0] if spike else [])
    n = len(closes)
    return [
        {"d": ASOF - timedelta(days=(n - 1 - i)), "close": c, "high": c, "low": c, "volume": v}
        for i, (c, v) in enumerate(zip(closes, vols))
    ]


class _PIT:
    """A COMPLETE ``SignalPointInTimeData`` fake — every protocol accessor implemented. THE
    STANDING GUARD (the corporate_risk-flip lesson): ``assemble_from_pit`` with the switch ON runs
    EVERY registered detector against this fake, so a protocol accessor missing from a test double
    AttributeErrors HERE in CI today, not at the future flip."""

    def __init__(self, asof: date, *, stakes=(), bars=(), subject_cik=None) -> None:
        self.asof = asof
        self.known_at = _KNOWN
        self.tenant_id = DEFAULT_TENANT_ID
        self._stakes = list(stakes)
        self._bars = list(bars)
        self._subject_cik = subject_cik

    def insider_txns(self, security_id: UUID) -> list[dict]:
        return []

    def price_history(self, security_id: UUID, lookback_days: int | None = None) -> list[dict]:
        rows = [dict(b) for b in self._bars if b["d"] <= self.asof]
        return window_prices(rows, self.asof, lookback_days)

    def dilution_facts(self, security_id: UUID) -> list[dict]:
        return []

    def catalyst_facts(self, security_id: UUID) -> list[dict]:
        return []

    def fundamentals_facts(self, security_id: UUID) -> list[dict]:
        return []

    def corporate_event_facts(self, security_id: UUID) -> list[dict]:
        return []

    def activist_stake_facts(self, security_id: UUID) -> list[dict]:
        return [dict(f) for f in self._stakes if f["valid_from"] <= self.asof]

    def theme_conviction_facts(self, thesis_id: UUID) -> list[dict]:
        return []

    def security_name(self, security_id: UUID) -> str | None:
        return None

    def security_cik(self, security_id: UUID) -> str | None:
        return self._subject_cik


def test_two_key_gate_a_13d_alone_warms_and_arms_only_with_a_colocated_breakout():
    """The two-key gate through the REAL pipeline: a live 13D conviction alone WARMS (never arms);
    with a co-located volume-backed breakout it ARMS (``_ON`` now equals the live DEFAULT_CONFIG —
    the switch defaults ON since the 2026-08-20 flip). And the INERT proof, re-pointed at an
    EXPLICITLY-OFF config now that OFF is no longer the default: the same stake tape contributes
    nothing — flat tape reads INCUBATING, breakout tape reads exactly what a no-stake basket reads.
    """
    thesis = make_thesis()
    stakes = [_fact(filed=ASOF - timedelta(days=10))]

    # Key 1 alone — a live 13D on a flat tape: WARMING, never armed.
    warm = assemble_from_pit(_PIT(ASOF, stakes=stakes, bars=_bars(spike=False)), thesis, ASOF, _ON)
    assert warm.state is State.WARMING
    assert Kind.ACTIVIST_STAKE in {t.kind for t in warm.triggers_fired}

    # Key 1 + a co-located Key 2 (the breakout bar): ARMED.
    armed = assemble_from_pit(_PIT(ASOF, stakes=stakes, bars=_bars(spike=True)), thesis, ASOF, _ON)
    assert armed.state is State.ARMED
    assert Kind.ACTIVIST_STAKE in {t.kind for t in armed.triggers_fired}

    # INERT when EXPLICITLY OFF: with the detector disabled the stake tape changes NOTHING (the
    # switch now defaults ON, so the disabled proof runs against an explicit-off config) — the flat
    # card reads INCUBATING (no trigger at all), and the breakout card equals the card a stake-free
    # basket produces (state AND fired-trigger kinds identical; the detector is genuinely gated).
    off = DEFAULT_CONFIG.model_copy(update={"activist_stake_enabled": False})
    off_flat = assemble_from_pit(
        _PIT(ASOF, stakes=stakes, bars=_bars(spike=False)), thesis, ASOF, off
    )
    assert off_flat.state is State.INCUBATING
    off_spike = assemble_from_pit(
        _PIT(ASOF, stakes=stakes, bars=_bars(spike=True)), thesis, ASOF, off
    )
    no_stakes = assemble_from_pit(_PIT(ASOF, stakes=(), bars=_bars(spike=True)), thesis, ASOF, off)
    assert off_spike.state is no_stakes.state
    assert {t.kind for t in off_spike.triggers_fired} == {t.kind for t in no_stakes.triggers_fired}
    assert Kind.ACTIVIST_STAKE not in {t.kind for t in off_spike.triggers_fired}
