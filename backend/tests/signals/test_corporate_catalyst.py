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


def _fact(accession="ACC-1", filed=ASOF, items=("5.02",), form="8-K"):
    return {
        "accession": accession,
        "form": form,
        "items": list(items) if items is not None else None,
        "filed": filed,
        "source_ref": f"https://www.sec.gov/Archives/edgar/data/1/{accession}-index.htm",
        "valid_from": filed,
    }


def test_101_is_demoted_out_of_the_catalyst_and_fires_nothing():
    """The "1.01 decision" (option A, 2026-08-20): Item 1.01 is ~60% financing not deals and grade
    does not gate arming, so it was REMOVED from the trigger cut — even with corporate_catalyst forced
    on, a 1.01-only 8-K now fires NOTHING on the trigger side. It stays on the tape (#9); its dilution
    tell 3.02 fires on the RISK side instead (see test_corporate_risk)."""
    assert "1.01" not in DEFAULT_CONFIG.corporate_event_items  # removed from the policy map
    assert corporate_catalyst.score([_fact(items=("1.01", "9.01"))], SID, ASOF) is None


def test_502_fires_a_flip_personnel_catalyst_the_only_remaining_trigger():
    """5.02 is now the ONLY trigger item (corporate_catalyst is 5.02-only after the 1.01 demotion): an
    officer/director-change 8-K fires a FLIP personnel Key-1 conviction, with grade/type/score/liveness
    from the policy row (#3 — not hardcoded) and a checkable provenance link (#6)."""
    e = corporate_catalyst.score([_fact(items=("5.02", "9.01"))], SID, ASOF)
    assert e is not None and e.fired
    assert e.role is Role.ENTRY_TRIGGER and e.kind is Kind.CATALYST
    assert e.type is CatalystType.PERSONNEL and e.grade is Grade.FLIP
    p = DEFAULT_CONFIG.corporate_event_items["5.02"]
    assert e.score == p.score and e.alpha_liveness_days == p.liveness_days  # policy, not hardcoded
    assert e.asof == ASOF  # fire date = the filing date
    assert "Item 5.02" in e.label and "officer/director" in e.label
    assert e.provenance[0].ref == "ACC-1" and e.provenance[0].detail["item"] == "5.02"
    assert e.provenance[0].detail["index_url"].endswith("-index.htm")  # checkable source (#6)


def test_unmapped_null_and_risk_cut_items_fire_nothing():
    """Items outside the trigger cut stay ON the tape (#9) but fire no catalyst: an unmapped 2.02, an
    unresolved NULL, and the RISK-side 4.02 / the newly-added 3.02 all contribute nothing here."""
    assert corporate_catalyst.score([_fact(items=("2.02", "9.01"))], SID, ASOF) is None
    assert corporate_catalyst.score([_fact(items=None)], SID, ASOF) is None
    assert corporate_catalyst.score([_fact(items=("4.02",))], SID, ASOF) is None
    assert corporate_catalyst.score([_fact(items=("3.02",))], SID, ASOF) is None


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
            [_fact(filed=ASOF + timedelta(days=1), items=("5.02",))], SID, ASOF
        )
        is None
    )


def test_most_recent_live_item_headlines_and_the_rest_ride_provenance():
    """With the shipped 5.02-only cut, two live 5.02s resolve by the most-recent filing (the prefer-CORE
    branch is unreachable in the shipped map — the contract test below covers it); the older one still
    rides the provenance so nothing surfaced is hidden (#6)."""
    old = _fact(accession="ACC-OLD", filed=ASOF - timedelta(days=30), items=("5.02",))
    new = _fact(accession="ACC-NEW", filed=ASOF, items=("5.02",))
    e = corporate_catalyst.score([old, new], SID, ASOF)
    assert e is not None and e.grade is Grade.FLIP
    assert e.asof == ASOF  # the most-recent filing headlines
    assert {(p.ref, p.detail["item"]) for p in e.provenance} == {
        ("ACC-OLD", "5.02"),
        ("ACC-NEW", "5.02"),
    }


def test_score_selection_prefers_core_over_a_more_recent_flip():
    """The score selection CONTRACT (prefer CORE, then most recent): the shipped v1 cut is 5.02-only
    (1.01 demoted 2026-08-20), so the prefer-core branch is exercised against the function's contract
    with a purpose-built policy map — a CORE item (older but live) headlines over a more-recent FLIP
    5.02, and BOTH ride the provenance (#6)."""
    cfg = DEFAULT_CONFIG.model_copy(
        update={
            "corporate_event_items": {
                **DEFAULT_CONFIG.corporate_event_items,
                "9.99": CorporateEventItemPolicy(
                    role=Role.ENTRY_TRIGGER,
                    grade=Grade.CORE,
                    catalyst_type=CatalystType.CONTRACT,
                    score=0.9,
                    liveness_days=365,
                ),
            }
        }
    )
    core = _fact(accession="ACC-CORE", filed=ASOF - timedelta(days=30), items=("9.99",))
    flip = _fact(accession="ACC-FLIP", filed=ASOF, items=("5.02",))
    e = corporate_catalyst.score([flip, core], SID, ASOF, cfg)
    assert e is not None and e.grade is Grade.CORE and "Item 9.99" in e.label
    assert e.asof == ASOF - timedelta(days=30)  # the CORE item, older but preferred
    assert {(p.ref, p.detail["item"]) for p in e.provenance} == {
        ("ACC-CORE", "9.99"),
        ("ACC-FLIP", "5.02"),
    }


def test_8ka_amendment_fires_like_the_original_form_named():
    e = corporate_catalyst.score([_fact(form="8-K/A", items=("5.02",))], SID, ASOF)
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
    pit_on = SimpleNamespace(corporate_event_facts=lambda sid: [_fact(items=("5.02",))])
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
