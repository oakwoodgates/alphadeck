"""Band 03 S3 — the 8-K item-code corporate RISK detector: pure score, the one-event merge, the
master switch, and the assembler composition (severe withholds the arm through the EXISTING
role+score path — zero assembler edits; moderate feeds counter-case + the confidence haircut)."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import Kind, Role, State
from signals import corporate_risk
from tests.calls.factories import ASOF, SID, breakout_event, insider_event, make_thesis


def _fact(accession="ACC-R1", filed=ASOF, items=("4.02",), form="8-K"):
    return {
        "accession": accession,
        "form": form,
        "items": list(items) if items is not None else None,
        "filed": filed,
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
        "valid_from": filed,
    }


def test_402_fires_severe_grade_blind_and_not_a_dearm():
    e = corporate_risk.score([_fact(items=("4.02",))], SID, ASOF)
    assert e is not None and e.fired
    assert e.role is Role.RISK_SIGNAL and e.kind is Kind.CORPORATE_RISK
    assert e.grade is None and e.dearm_grade is None  # grade-blind like dilution; NOT a de-arm
    p = DEFAULT_CONFIG.corporate_event_items["4.02"]
    assert e.score == p.score and e.score >= DEFAULT_CONFIG.risk_block_severity  # severe
    assert "Item 4.02" in e.label and "severe — withholds the Armed call on timing" in e.label
    assert e.asof == ASOF  # anchored on the filing date
    assert e.provenance[0].ref == "ACC-R1" and e.provenance[0].detail["item"] == "4.02"


def test_301_fires_moderate_sub_veto():
    e = corporate_risk.score([_fact(items=("3.01",))], SID, ASOF)
    assert e is not None
    assert e.score == DEFAULT_CONFIG.corporate_event_items["3.01"].score
    assert e.score < DEFAULT_CONFIG.risk_block_severity
    assert "counter-case input, below the timing-veto threshold" in e.label


def test_two_live_items_merge_into_one_event_at_max_severity():
    """The one-event-per-detector contract: a live 4.01 AND a live 3.01 emit ONE event scored at
    the max (never summed), with the other item ENUMERATED in the label and BOTH in provenance —
    two flags visible, one composition."""
    a = _fact(accession="ACC-A", filed=ASOF - timedelta(days=10), items=("4.01",))
    b = _fact(accession="ACC-B", filed=ASOF, items=("3.01",))
    e = corporate_risk.score([a, b], SID, ASOF)
    assert e is not None
    assert e.score == DEFAULT_CONFIG.corporate_event_items["3.01"].score  # max of two equals
    assert "also live:" in e.label
    assert {(p.ref, p.detail["item"]) for p in e.provenance} == {
        ("ACC-A", "4.01"),
        ("ACC-B", "3.01"),
    }


def test_amendment_does_not_double_count_severity():
    """An 8-K/A repeating the original's item is its own tape row, but the existence/latest-shaped
    read can't inflate: the merged event still scores the ITEM's policy value, never a sum."""
    orig = _fact(accession="ACC-1", filed=ASOF - timedelta(days=5), items=("4.02",))
    amend = _fact(accession="ACC-1A", filed=ASOF, items=("4.02",), form="8-K/A")
    e = corporate_risk.score([orig, amend], SID, ASOF)
    assert e is not None and e.score == DEFAULT_CONFIG.corporate_event_items["4.02"].score


def test_freshness_is_detector_enforced_and_expires_per_item():
    """The assembler never ages risk signals, so the detector must: a 3.01 outside its 180d window
    drops out of the re-derived stream entirely (inclusive at the horizon, gone one day past)."""
    days = DEFAULT_CONFIG.corporate_event_items["3.01"].liveness_days
    live = _fact(filed=ASOF - timedelta(days=days), items=("3.01",))
    assert corporate_risk.score([live], SID, ASOF) is not None
    stale = _fact(filed=ASOF - timedelta(days=days + 1), items=("3.01",))
    assert corporate_risk.score([stale], SID, ASOF) is None


def test_trigger_cut_and_unmapped_items_fire_no_risk():
    assert corporate_risk.score([_fact(items=("1.01", "5.02"))], SID, ASOF) is None
    assert corporate_risk.score([_fact(items=("2.02",))], SID, ASOF) is None
    assert corporate_risk.score([_fact(items=None)], SID, ASOF) is None


def test_master_switch_off_detect_emits_nothing_score_stays_ungated():
    """INERT-FIRST (the insider_sell precedent): switch OFF -> detect no-ops without reading the
    pit; switch ON (the live default since the 2026-08-17 flip) -> fires. Both sides pin an
    EXPLICIT switch state so the test outlives the default in either direction."""
    off = DEFAULT_CONFIG.model_copy(update={"corporate_risk_enabled": False})
    pit = SimpleNamespace(
        corporate_event_facts=lambda sid: (_ for _ in ()).throw(
            AssertionError("pit read while OFF")
        )
    )
    assert corporate_risk.detect(pit, SID, ASOF, off) is None

    on = DEFAULT_CONFIG.model_copy(update={"corporate_risk_enabled": True})
    pit_on = SimpleNamespace(corporate_event_facts=lambda sid: [_fact(items=("1.03",))])
    e = corporate_risk.detect(pit_on, SID, ASOF, on)
    assert e is not None and e.kind is Kind.CORPORATE_RISK


# --- assembler composition: zero kind branches, the existing role+score path --------------------------


def test_severe_corporate_risk_withholds_the_arm_on_timing():
    """A severe item (4.02) on an otherwise-armed name withholds the Armed call through the
    assembler's EXISTING grade-blind block — the risk-veto holds timing, never the thesis, and the
    counter-case names the risk in ``missing``."""
    risk = corporate_risk.score([_fact(items=("4.02",))], SID, ASOF)
    card = assemble_call(
        make_thesis(), [insider_event(), breakout_event(), risk], ASOF, DEFAULT_CONFIG
    )
    assert card.state is State.WARMING  # keys are in, the severe risk withholds the arm
    assert any("Risk must clear before arming" in m for m in card.missing)
    assert any(r.kind is Kind.CORPORATE_RISK for r in card.risk_signals)


def test_moderate_corporate_risk_rides_the_armed_call_with_a_haircut():
    """A moderate item (3.01) never blocks: the call still ARMS, the risk rides the card
    (counter-case surface), and setup strength wears the per-risk haircut."""
    risk = corporate_risk.score([_fact(items=("3.01",))], SID, ASOF)
    clean = assemble_call(make_thesis(), [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG)
    carded = assemble_call(
        make_thesis(), [insider_event(), breakout_event(), risk], ASOF, DEFAULT_CONFIG
    )
    assert carded.state is State.ARMED
    assert any(r.kind is Kind.CORPORATE_RISK for r in carded.risk_signals)
    assert carded.confidence is not None and carded.confidence < clean.confidence
