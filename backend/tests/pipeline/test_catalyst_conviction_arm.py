from __future__ import annotations

from datetime import date, datetime, timezone

from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, State, Verdict
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import (
    LEU_ID,
    NUCLEAR_THESIS_ID,
    OKLO_ID,
    seed_leu_catalyst,
    seed_nuclear,
    seed_nuclear_catalyst,
)

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_ASOF = date(
    2026, 6, 5
)  # all four nuclear names have a live breakout here (see test_nuclear_thesis)


def test_a_ratified_catalyst_arms_the_theme_as_a_disciplined_starter(db):
    """The #10 unlock, on REAL nuclear data + the real OKLO DOE OTA: the basket broke out sector-wide but
    had no conviction key, so it could only WARM. The operator-ratified DOE Reactor Pilot Program OTA on
    OKLO (flip grade — provisional, but a multi-year horizon) co-locates with OKLO's live breakout ->
    OKLO ARMS as a disciplined STARTER (not a do-not-hold flip, because the conviction's HORIZON is long
    — the option-A fix). The discipline holds: the catalyst alone wouldn't arm without confirmation.
    """
    seed_nuclear(db)
    db.commit()

    # before ratification: confirmation (breakouts) but no conviction -> Warming (the honest theme state)
    before = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert before.state is State.WARMING
    assert before.key_confirmation.turned and not before.key_conviction.turned

    seed_nuclear_catalyst(db)  # the operator ratifies the OKLO DOE OTA via the bridge
    db.commit()

    after = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert after.state is State.ARMED
    assert (
        after.armed_security_id == OKLO_ID
    )  # the catalyst armed the name it fired on (co-location)
    assert after.key_conviction.turned and after.key_confirmation.turned
    # flip (provisional) conviction + long horizon -> a disciplined STARTER, NOT flip_only / do-not-hold
    assert after.verdict is Verdict.STARTER_ENTRY
    assert after.conviction_grade is Grade.FLIP
    assert "do not hold" not in after.expression.lower()
    # the inverse-loudness fix: OKLO's provisional starter no longer reads loud (was ~0.95, the strong
    # breakout floating it up despite the weak conviction) — it's now capped at the starter ceiling
    assert after.confidence is not None
    assert after.confidence <= DEFAULT_CONFIG.starter_confidence_cap
    # the specific DOE OTA (DENE0009589) rides the conviction trigger as provenance (show the work)
    refs = [p.ref for t in after.triggers_fired for p in t.sources]
    assert any("DENE0009589" in r for r in refs)


def test_a_binding_contract_arms_core_and_outranks_the_provisional_starter(db):
    """LEU's DOE HALEU production contract (CORE — a binding, multi-year federal contract) co-locates with
    LEU's live core breakout and arms a real core_entry. With BOTH catalysts ratified, the theme headlines
    the BINDING name (LEU core_entry), not the PROVISIONAL one (OKLO starter): the thesis surfaces its
    strongest member, so a binding revenue contract correctly outranks an authorization pathway. (OKLO's
    starter is still computed beneath; true per-member side-by-side is the M5 group view.) This is the
    catalyst core_entry path — nothing else in the seed exercises it.
    """
    seed_nuclear(db)
    seed_nuclear_catalyst(db)  # OKLO flip -> would arm a starter
    seed_leu_catalyst(db)  # LEU core  -> a binding contract
    db.commit()

    card = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert card.state is State.ARMED
    assert (
        card.armed_security_id == LEU_ID
    )  # the binding name wins the headline (strongest entry grade)
    assert card.verdict is Verdict.CORE_ENTRY
    assert card.conviction_grade is Grade.CORE and card.entry_grade is Grade.CORE
    assert "do not hold" not in card.expression.lower()
    # both catalysts ride as live conviction-trigger provenance (OKLO's OTA + LEU's contract)
    refs = [p.ref for t in card.triggers_fired for p in t.sources]
    assert any("DENE0009589" in r for r in refs)  # OKLO OTA still present
    assert any("89243223CNE000030" in r for r in refs)  # LEU contract
    # a CORE entry (both keys strong) is NOT a starter -> confidence is NOT capped at the starter ceiling
    assert card.confidence is not None and card.confidence > DEFAULT_CONFIG.starter_confidence_cap
    # the hold clock lands on the contract's base-term end (near-the-edge, not open-ended)
    assert card.exit_by == date(2026, 6, 30)
