"""Band 03 S3 — the 8-K item-code CATALYST trigger (pure score + the master switch). No DB: the
pure ``score`` takes fact rows directly; ``detect`` is exercised through a stub pit."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace

import pytest

from domain.config import DEFAULT_CONFIG, CorporateEventItemPolicy
from domain.enums import CatalystType, Grade, Kind, Role
from signals import corporate_catalyst
from tests.calls.factories import ASOF, SID


def _fact(accession="ACC-1", filed=ASOF, items=("1.01",), form="8-K"):
    return {
        "accession": accession,
        "form": form,
        "items": list(items) if items is not None else None,
        "filed": filed,
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
        "valid_from": filed,
    }


def test_101_fires_a_core_contract_catalyst_from_the_policy_map():
    e = corporate_catalyst.score([_fact(items=("1.01", "9.01"))], SID, ASOF)
    assert e is not None and e.fired
    assert e.role is Role.ENTRY_TRIGGER and e.kind is Kind.CATALYST
    assert e.type is CatalystType.CONTRACT and e.grade is Grade.CORE
    p = DEFAULT_CONFIG.corporate_event_items["1.01"]
    assert e.score == p.score and e.alpha_liveness_days == p.liveness_days  # policy, not hardcoded
    assert e.asof == ASOF  # fire date = the filing date
    assert "Item 1.01" in e.label and "material definitive agreement" in e.label
    assert e.provenance[0].ref == "ACC-1" and e.provenance[0].detail["item"] == "1.01"
    assert e.provenance[0].detail["index_url"].endswith("-index.htm")  # checkable source (#6)


def test_502_fires_a_flip_personnel_catalyst():
    e = corporate_catalyst.score([_fact(items=("5.02",))], SID, ASOF)
    assert e is not None
    assert e.type is CatalystType.PERSONNEL and e.grade is Grade.FLIP
    assert e.score == DEFAULT_CONFIG.corporate_event_items["5.02"].score


def test_unmapped_null_and_risk_cut_items_fire_nothing():
    """Items outside the trigger cut stay ON the tape (#9) but fire no catalyst: an unmapped 2.02,
    an unresolved NULL, and a RISK-side 4.02 all contribute nothing here."""
    assert corporate_catalyst.score([_fact(items=("2.02", "9.01"))], SID, ASOF) is None
    assert corporate_catalyst.score([_fact(items=None)], SID, ASOF) is None
    assert corporate_catalyst.score([_fact(items=("4.02",))], SID, ASOF) is None


def test_liveness_is_per_item_inclusive_and_expires():
    """The 90d [PROPOSED] 5.02 window, anchored on the filing date: inclusive AT the horizon,
    expired one day past it — the same ``entry_signal_is_live`` arithmetic every trigger uses."""
    days = DEFAULT_CONFIG.corporate_event_items["5.02"].liveness_days
    filed = ASOF - timedelta(days=days)
    assert corporate_catalyst.score([_fact(filed=filed, items=("5.02",))], SID, ASOF) is not None
    stale = ASOF - timedelta(days=days + 1)
    assert corporate_catalyst.score([_fact(filed=stale, items=("5.02",))], SID, ASOF) is None


def test_future_filed_fact_is_invisible_no_lookahead():
    """A pure-score honesty re-check (the as-of read already guarantees it live): a filing dated
    after asof contributes nothing."""
    assert (
        corporate_catalyst.score(
            [_fact(filed=ASOF + timedelta(days=1), items=("1.01",))], SID, ASOF
        )
        is None
    )


def test_strongest_live_item_wins_and_the_rest_ride_provenance():
    """A CORE 1.01 (older but live) headlines over a more recent FLIP 5.02 — catalyst_conviction's
    prefer-core-then-recent selection — and BOTH live trigger items ride the provenance (#6)."""
    core = _fact(accession="ACC-CORE", filed=ASOF - timedelta(days=30), items=("1.01",))
    flip = _fact(accession="ACC-FLIP", filed=ASOF, items=("5.02",))
    e = corporate_catalyst.score([flip, core], SID, ASOF)
    assert e is not None and e.grade is Grade.CORE and "Item 1.01" in e.label
    assert e.asof == ASOF - timedelta(days=30)
    assert {(p.ref, p.detail["item"]) for p in e.provenance} == {
        ("ACC-CORE", "1.01"),
        ("ACC-FLIP", "5.02"),
    }


def test_8ka_amendment_fires_like_the_original_form_named():
    e = corporate_catalyst.score([_fact(form="8-K/A", items=("1.01",))], SID, ASOF)
    assert e is not None and "8-K/A" in e.label


def test_master_switch_off_detect_emits_nothing_score_stays_ungated():
    """INERT-FIRST: with the live DEFAULT_CONFIG (switch OFF) ``detect`` no-ops — it never even
    reads the pit — so no corporate-catalyst event can reach a live card; the pure ``score`` above
    stays fully testable. Flipping the switch (model_copy — the replay force-on path) fires."""
    pit = SimpleNamespace(
        corporate_event_facts=lambda sid: (_ for _ in ()).throw(
            AssertionError("pit read while OFF")
        )
    )
    assert corporate_catalyst.detect(pit, SID, ASOF, DEFAULT_CONFIG) is None

    on = DEFAULT_CONFIG.model_copy(update={"corporate_catalyst_enabled": True})
    pit_on = SimpleNamespace(corporate_event_facts=lambda sid: [_fact(items=("1.01",))])
    e = corporate_catalyst.detect(pit_on, SID, ASOF, on)
    assert e is not None and e.kind is Kind.CATALYST


def test_catalyst_kind_inherits_conviction_membership():
    """The wiring claim: emitting kind=CATALYST rides the EXISTING conviction_kinds (co-location
    arming) and own_conviction_kinds (the is_own ranking) with zero config-set changes."""
    assert Kind.CATALYST in DEFAULT_CONFIG.conviction_kinds
    assert Kind.CATALYST in DEFAULT_CONFIG.own_conviction_kinds


def test_item_policy_contract_fails_loud_on_a_bad_config_edit():
    """The config-time mirror of the SignalEvent taxonomy contract: a trigger row must carry grade +
    type; a risk row must not carry a grade — a bad policy edit fails at import, not at first fire.
    """
    with pytest.raises(ValueError, match="entry_trigger item policy"):
        CorporateEventItemPolicy(role=Role.ENTRY_TRIGGER, score=0.5, liveness_days=30)
    with pytest.raises(ValueError, match="must not carry a grade"):
        CorporateEventItemPolicy(
            role=Role.RISK_SIGNAL, grade=Grade.CORE, score=0.5, liveness_days=30
        )
