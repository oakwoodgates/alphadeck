from __future__ import annotations

from datetime import date, datetime, timezone

from domain.config import DEFAULT_CONFIG
from domain.enums import State, Verdict
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
    seed_nuclear_theme_conviction,
)

_KNOWN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_ASOF = date(
    2026, 6, 5
)  # all four nuclear names have a live breakout here (see test_nuclear_thesis)


def test_theme_conviction_moves_a_confirmed_member_from_watch_to_armed(db):
    """M5b in isolation (no catalysts): the nuclear basket only WARMS (breakouts, no conviction) and SMR
    sits in the watch tier. Ratifying the theme conviction arms SMR — its 2026-06-02 volume-backed (CORE)
    breakout + the theme fallback -> a capped, theme-armed STARTER. NNE's momentum-only (flip) breakout is
    NOT enough (rule 4), so NNE stays in watch — the volume gate doing its job.
    """
    seed_nuclear(db)
    db.commit()
    before = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert before.state is State.WARMING  # breakouts but no conviction
    assert SMR_ID not in {m.security_id for m in before.armed_members}
    assert SMR_ID in {m.security_id for m in before.watch_members}  # confirmation-only -> watch

    seed_nuclear_theme_conviction(db)
    db.commit()
    after = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert after.state is State.ARMED
    armed = {m.security_id: m for m in after.armed_members}
    assert SMR_ID in armed  # SMR moved watch -> armed via the theme fallback
    smr = armed[SMR_ID]
    assert smr.theme_armed is True
    assert smr.verdict is Verdict.STARTER_ENTRY  # flip (capped) + long theme horizon -> starter
    assert smr.confidence is not None and smr.confidence <= DEFAULT_CONFIG.starter_confidence_cap
    # NNE's breakout is momentum-only -> rule 4 keeps it OUT of the armed set; it stays in watch
    assert NNE_ID not in armed
    assert NNE_ID in {m.security_id for m in after.watch_members}
    # the ratified theme conviction rides as provenance on the theme-armed member's triggers (show the work)
    refs = [p.ref for t in smr.triggers for p in t.sources]
    assert any("congress.gov" in r for r in refs)


def test_full_demo_theme_armed_smr_ranks_between_own_flip_and_lapsing_core(db):
    """The served demo (main): OKLO own-flip OTA (-> 2029, fresh) + LEU own-core contract (-> 2026-06-30,
    lapsing) + the theme conviction -> SMR a theme-armed starter. Freshness-primary ranking (Q1): OKLO
    headlines, SMR is #2 (a FRESH theme starter, above the LAPSING own core), LEU is #3; NNE stays watch.
    own-above-theme is only a within-band tiebreak — it does NOT lift the lapsing own core over a fresh
    theme starter (the M5a OKLO-over-lapsing-LEU doctrine, now with a theme name in the mix).
    """
    seed_nuclear(db)
    seed_nuclear_catalyst(db)  # OKLO flip OTA -> 2029-07-01 (fresh)
    seed_leu_catalyst(db)  # LEU core contract -> 2026-06-30 (lapsing at the 06-05 asof)
    seed_nuclear_theme_conviction(db)  # theme -> SMR theme-armed starter
    db.commit()
    card = call_for_thesis(db, NUCLEAR_THESIS_ID, _ASOF, known_at=_KNOWN, record=False)
    assert card.state is State.ARMED
    assert (
        card.armed_security_id == OKLO_ID
    )  # the fresh own flip headlines (Board / Decision Queue)
    assert [m.security_id for m in card.armed_members] == [OKLO_ID, SMR_ID, LEU_ID]
    by_id = {m.security_id: m for m in card.armed_members}
    assert (
        by_id[SMR_ID].theme_armed is True
    )  # the lone theme-armed name (NNE's breakout is momentum-only)
    assert by_id[OKLO_ID].theme_armed is False and by_id[LEU_ID].theme_armed is False  # own-armed
    assert by_id[SMR_ID].lapsing is False and by_id[LEU_ID].lapsing is True
    assert (
        by_id[LEU_ID].verdict is Verdict.CORE_ENTRY
    )  # the own core is preserved at #3 (demoted, not lost)
    assert NNE_ID in {m.security_id for m in card.watch_members}
