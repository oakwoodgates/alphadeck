from __future__ import annotations

from datetime import date, datetime, timezone

from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, State, Verdict
from pipeline.call_for_thesis import call_for_thesis
from pipeline.seed import (
    LEU_ID,
    NNE_ID,
    NUCLEAR_THESIS_ID,
    OKLO_ID,
    SMR_ID,
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


def test_per_member_menu_ranks_a_fresh_starter_above_a_lapsing_core(db):
    """M5 Part A: with BOTH catalysts ratified the theme has two armed members, and the menu RANKS them on
    the freshness band (liveness runway) primary, grade within — so OKLO (a starter with years of runway →
    2029) HEADLINES over LEU (a core arm about to lapse → 2026-06-30), instead of collapsing to the strongest
    grade. LEU's binding-contract call isn't lost — it's #2, still core, with its 06-30 cliff. SMR + NNE
    (breakout, no conviction) sit in the confirmation-only watch tier. The headline drives the Board / Queue.
    """
    seed_nuclear(db)
    seed_nuclear_catalyst(db)  # OKLO flip OTA      -> 2029-07-01 (fresh runway)
    seed_leu_catalyst(db)  # LEU core contract  -> 2026-06-30 (lapsing at the 06-05 asof)
    db.commit()

    card = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert card.state is State.ARMED

    # headline = top-ranked ACTIONABLE member = OKLO (fresh), NOT LEU (lapsing core): runway over grade
    assert card.armed_security_id == OKLO_ID
    assert card.verdict is Verdict.STARTER_ENTRY and card.exit_by == date(2029, 7, 1)

    # the ranked armed menu: OKLO (#1 fresh starter) then LEU (#2 core, lapsing) — both visible, not collapsed
    assert [m.security_id for m in card.armed_members] == [OKLO_ID, LEU_ID]
    assert card.armed_members[0].entry_grade is Grade.FLIP  # OKLO: the fresh starter
    leu = card.armed_members[1]
    assert leu.conviction_grade is Grade.CORE and leu.verdict is Verdict.CORE_ENTRY
    assert leu.exit_by == date(2026, 6, 30)  # LEU still core + its 06-30 cliff — demoted, not lost
    assert (
        leu.lapsing is True and card.armed_members[0].lapsing is False
    )  # LEU flagged lapsing; OKLO fresh

    # SMR + NNE broke out with no conviction -> the confirmation-only "watch" tier (visible, not actionable)
    watch_ids = {m.security_id for m in card.watch_members}
    assert {SMR_ID, NNE_ID} <= watch_ids
    assert all(m.verdict is None and m.conviction_grade is None for m in card.watch_members)

    # both catalysts still ride as provenance on the thesis card
    refs = [p.ref for t in card.triggers_fired for p in t.sources]
    assert any("DENE0009589" in r for r in refs) and any("89243223CNE000030" in r for r in refs)
