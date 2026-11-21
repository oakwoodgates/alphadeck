"""V2 W6c: the thesis-catalyst rule through the ONE seam (``pipeline.core.assemble_from_pit``), on the seeded
small-scale-nuclear card and its real 2026-06-02 tape (SMR: a volume-backed CORE breakout; NNE: a momentum-only
FLIP breakout; OKLO: a platform FLIP catalyst; LEU: a platform CORE catalyst).

One test per signed rule (``docs/CALL_LOGIC.md`` §3c): R2 (no switch, nothing), R3 (this card only; it warms and
never arms alone), R4 / N1 (a) (a whole-card entry is exactly the theme conviction), R6 (relevant-until on the
card), R8 (the stronger wins, the other is shown muted), R10 (silent links and a meta index card), and the
recorded card gaining no key (R9 / N8 (a)).

Entries are written with fixed instants by the test-only insert (``tests/thesis_catalyst_fixtures``), each at
the night ``thesis_catalyst_repo.entry_night`` gives its instant, exactly as the sole writer would.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from db.session import DEFAULT_TENANT_ID
from domain.enums import Grade, Kind, State
from domain.thesis import CONTEXT_LABEL, BasketMember, Thesis
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
from repositories import thesis_repo
from repositories.calls_repo import _canonical
from repositories.mappers import call_to_row
from repositories.thesis_catalyst_repo import entry_night
from signals.catalyst_conviction import _CORE_SCORE
from tests.thesis_catalyst_fixtures import card, insert_version
from tests.workspaces import new_workspace

ET = ZoneInfo("America/New_York")
PIN = datetime(2100, 1, 1, tzinfo=timezone.utc)
NIGHT = date(2026, 6, 2)  # the breakouts are fresh and holding (tests/pipeline/test_theme_arm.py)
SAVED = datetime(2026, 6, 1, 10, 0, tzinfo=ET)  # Monday 10:00 ET: counts from Monday's night


def _entry(db, thesis_id=NUCLEAR_THESIS_ID, *, members=(SMR_ID,), grade="core", **kw) -> uuid.UUID:
    entry = uuid.uuid4()
    at = kw.pop("at", SAVED)
    insert_version(
        db,
        thesis_id,
        entry,
        at=at,
        valid_from=entry_night(at),
        security_ids=list(members),
        grade=grade,
        **kw,
    )
    db.commit()
    return entry


def _card(db, thesis_id=NUCLEAR_THESIS_ID, night=NIGHT, clock="record"):
    known_at = PIN
    card = call_for_thesis(db, thesis_id, night, known_at=known_at, clock=clock, record=False)
    db.commit()
    return card


def _catalyst_triggers(card, sid) -> list:
    """Every catalyst trigger on ``sid`` anywhere on the card (the headline, the armed and the watch menus)."""
    out = [t for t in card.triggers_fired if t.security_id == sid and t.kind is Kind.CATALYST]
    for m in [*card.armed_members, *card.watch_members]:
        out += [t for t in m.triggers if t.security_id == sid and t.kind is Kind.CATALYST]
    return out


def _armed(card) -> dict:
    return {m.security_id: m for m in card.armed_members}


def _seed(db, *, theme: bool = False, catalysts: bool = False) -> None:
    seed_nuclear(db)
    if catalysts:
        seed_nuclear_catalyst(db)
        seed_leu_catalyst(db)
    if theme:
        seed_nuclear_theme_conviction(db)
    db.commit()


def _second_card(db, *, workspace=DEFAULT_TENANT_ID, members=None) -> uuid.UUID:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=workspace,
        name="a second card holding SMR",
        narrative="n",
        basket=members
        or [
            BasketMember(ticker="SMR", role="r", security_id=SMR_ID),
            BasketMember(ticker="NNE", role="r", security_id=NNE_ID),
        ],
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis.id


# --- R2 ------------------------------------------------------------------------------------------------------


def test_an_entry_that_does_not_count_changes_nothing(db):
    _seed(db)
    before = _canonical(_card(db))
    _entry(db, counts=False)
    assert _canonical(_card(db)) == before


# --- R3 ------------------------------------------------------------------------------------------------------


def test_core_entry_and_breakout_arm_this_card_only(db):
    """The first test that failed before W6c: the entry was read by nothing."""
    _seed(db)
    other_ws = new_workspace(db, "V")
    second = _second_card(db)  # the same workspace, the same company
    elsewhere = _second_card(db, workspace=other_ws)  # another workspace, the same company
    before = _card(db)
    assert before.state is State.WARMING and SMR_ID not in _armed(before)
    second_before = _canonical(_card(db, second))
    elsewhere_before = _canonical(_card(db, elsewhere))

    entry = _entry(db, members=(SMR_ID,), grade="core")

    after = _card(db)
    assert after.state is State.ARMED
    smr = _armed(after)[SMR_ID]
    assert smr.theme_armed is False and smr.conviction_grade is Grade.CORE
    (trigger,) = _catalyst_triggers(after, SMR_ID)[:1]
    assert trigger.grade is Grade.CORE and trigger.event_date == date(2026, 6, 1)
    assert trigger.sources[0].source == "thesis_catalyst"
    assert trigger.sources[0].detail == {"entry_id": str(entry), "counts_from": "2026-06-01"}
    # NNE is not named: it keeps its watch place
    assert NNE_ID not in _armed(after)
    # the same company on a second card in the same workspace, and in another workspace: unchanged
    assert _canonical(_card(db, second)) == second_before
    assert _canonical(_card(db, elsewhere)) == elsewhere_before


def test_an_entry_on_another_workspaces_card_reaches_nothing_here(db):
    _seed(db)
    other_ws = new_workspace(db, "V")
    elsewhere = _second_card(db, workspace=other_ws)
    before = _canonical(_card(db))
    _entry(db, elsewhere, members=(SMR_ID,), grade="core", tenant_id=other_ws)
    assert _canonical(_card(db)) == before
    assert (
        _armed(_card(db, elsewhere))[SMR_ID].conviction_grade is Grade.CORE
    )  # it counts on its own card


def test_an_entry_alone_warms_and_never_arms(db):
    """A member with no confirmation at all (no price tape): a core entry on it warms the card, never arms it."""
    tid, sids, _ = card(db)
    db.commit()
    assert _card(db, tid).state is State.INCUBATING
    _entry(db, tid, members=(sids[0],), grade="core")
    after = _card(db, tid)
    assert after.state is State.WARMING and not after.armed_members
    (trigger,) = [t for t in after.triggers_fired if t.kind is Kind.CATALYST]
    assert trigger.security_id == sids[0] and trigger.grade is Grade.CORE


def test_an_entry_is_an_own_conviction_so_the_theme_skips_that_member(db):
    _seed(db, theme=True)
    _entry(db, members=(SMR_ID,), grade="flip")
    smr = _armed(_card(db))[SMR_ID]
    assert smr.theme_armed is False
    assert {t.kind for t in smr.triggers} == {Kind.CATALYST, Kind.TECHNICAL_BREAKOUT}


# --- R4 / N1 (a) ---------------------------------------------------------------------------------------------


def test_zero_member_entry_is_the_theme_conviction(db):
    """A whole-card entry does exactly what the theme conviction does: SMR (volume-backed breakout, no own
    conviction) becomes a theme-armed starter; NNE (momentum-only) stays in watch."""
    _seed(db)
    _entry(db, members=(), grade="flip", label="the operator's theme")
    card_ = _card(db)
    assert card_.state is State.ARMED
    smr = _armed(card_)[SMR_ID]
    assert smr.theme_armed is True and smr.conviction_grade is Grade.FLIP
    theme = [t for t in smr.triggers if t.kind is Kind.THEME_CONVICTION]
    assert [(t.label, t.sources[0].source) for t in theme] == [
        ("the operator's theme", "thesis_catalyst")
    ]
    assert NNE_ID not in _armed(card_)
    assert NNE_ID in {m.security_id for m in card_.watch_members}


def test_zero_member_entry_does_not_warm_a_card_on_its_own(db):
    """N1 (a): a whole-card entry reaches only a member with a live VOLUME-BACKED confirmation. A card whose names
    have no confirmation, and a card whose only breakout is momentum-only (NNE), are unchanged by it.
    """
    _seed(db)
    tid, _, _ = card(db)
    nne_only = _second_card(db, members=[BasketMember(ticker="NNE", role="r", security_id=NNE_ID)])
    db.commit()
    before = {t: _canonical(_card(db, t)) for t in (tid, nne_only)}
    _entry(db, tid, members=(), grade="flip")
    _entry(db, nne_only, members=(), grade="flip")
    assert {t: _canonical(_card(db, t)) for t in (tid, nne_only)} == before
    assert _card(db, tid).state is State.INCUBATING


# --- R6 ------------------------------------------------------------------------------------------------------


def test_relevant_until_ends_the_entry_on_the_card(db):
    _seed(db)
    _entry(db, members=(LEU_ID,), grade="core", relevant_until=date(2026, 6, 5))
    assert _catalyst_triggers(_card(db, night=date(2026, 6, 4)), LEU_ID)
    assert not _catalyst_triggers(_card(db, night=date(2026, 6, 9)), LEU_ID)


# --- R8 / N6 / N7 --------------------------------------------------------------------------------------------


def test_a_core_entry_outranks_the_platform_flip_catalyst_which_rides_muted(db):
    _seed(db, catalysts=True)
    _entry(db, members=(OKLO_ID,), grade="core")
    oklo = _armed(_card(db))[OKLO_ID]
    (trigger,) = [t for t in oklo.triggers if t.kind is Kind.CATALYST]
    assert trigger.grade is Grade.CORE and oklo.conviction_grade is Grade.CORE
    assert [p.source for p in trigger.sources] == ["thesis_catalyst", "ratified"]
    assert trigger.sources[1].detail["also_live"] is True
    assert trigger.sources[1].detail["counted"] is False


def test_a_flip_entry_under_a_platform_core_catalyst_changes_nothing_but_the_muted_source(db):
    _seed(db, catalysts=True)
    before = _armed(_card(db))[LEU_ID]
    _entry(db, members=(LEU_ID,), grade="flip", label="my LEU note")
    after = _armed(_card(db))[LEU_ID]
    strip = {"triggers"}
    assert after.model_dump(exclude=strip) == before.model_dump(exclude=strip)
    (b,) = [t for t in before.triggers if t.kind is Kind.CATALYST]
    (a,) = [t for t in after.triggers if t.kind is Kind.CATALYST]
    assert a.model_dump(exclude={"sources"}) == b.model_dump(exclude={"sources"})
    assert a.sources[0] == b.sources[0] and len(b.sources) == 1
    assert a.sources[1].source == "thesis_catalyst"
    assert a.sources[1].detail["label"] == "my LEU note"
    assert a.grade is Grade.CORE


# --- R10 -----------------------------------------------------------------------------------------------------


def _silent_card(db) -> uuid.UUID:
    """SMR in the Context link (on the card, no vote); NNE voting."""
    return _second_card(
        db,
        members=[
            BasketMember(ticker="SMR", role="r", security_id=SMR_ID, segment=CONTEXT_LABEL),
            BasketMember(ticker="NNE", role="r", security_id=NNE_ID),
        ],
    )


def test_entry_on_a_silent_link_does_nothing(db):
    """R10 through ``voting_thesis`` at the seam, no special case: an entry naming SMR (in Context) on a card
    adds nothing, though the SAME entry on a card where SMR votes arms it."""
    _seed(db)
    silent = _silent_card(db)
    before = _canonical(_card(db, silent))
    _entry(db, silent, members=(SMR_ID,), grade="core")
    assert _canonical(_card(db, silent)) == before
    # the control: SMR voting on its own card, the same entry arms it
    voting = _second_card(db)
    _entry(db, voting, members=(SMR_ID,), grade="core")
    assert _armed(_card(db, voting))[SMR_ID].conviction_grade is Grade.CORE


def test_an_entry_naming_a_silent_and_a_voting_name_counts_only_on_the_voting_one(db):
    _seed(db)
    silent = _silent_card(db)
    _entry(db, silent, members=(SMR_ID, NNE_ID), grade="flip")
    card = _card(db, silent)
    assert not _catalyst_triggers(card, SMR_ID)
    assert _catalyst_triggers(card, NNE_ID)


def test_meta_index_card_ignores_every_entry(db):
    _seed(db, theme=True)
    thesis_repo.set_meta_index(db, NUCLEAR_THESIS_ID, True)
    db.commit()
    before = _canonical(_card(db))
    _entry(db, members=(SMR_ID,), grade="core")
    _entry(db, members=(), grade="flip", at=datetime(2026, 6, 1, 11, tzinfo=ET))
    after = _card(db)
    assert _canonical(after) == before
    assert after.state is State.INCUBATING


# --- R9 / N8 (a): the recorded card gains no key ------------------------------------------------------------


def test_a_recorded_card_has_exactly_the_keys_it_had(db):
    """The stored JSON of a card with no operator entry (here the full demo: platform catalysts and the theme)
    has exactly the keys of the pre-W6 golden's card for the same night; an entry adds no KEY either (its
    origin rides in the provenance source, N8 (a))."""
    golden = json.loads(
        (Path(__file__).parent / "golden" / "w6_card_identity.json").read_text(encoding="utf-8")
    )
    _seed(db, theme=True, catalysts=True)
    card = call_to_row(_card(db))["card"]
    expected = golden["nuclear|2026-06-02|live|record"]
    assert set(card) == set(expected)
    _entry(db, members=(SMR_ID,), grade="core")
    with_entry = call_to_row(_card(db))["card"]
    assert set(with_entry) == set(expected)
    member_keys = {k for m in expected["armed_members"] for k in m}
    assert {k for m in with_entry["armed_members"] for k in m} == member_keys
    trigger_keys = {k for t in expected["triggers_fired"] for k in t}
    assert {k for t in with_entry["triggers_fired"] for k in t} == trigger_keys
    source_keys = {k for t in expected["triggers_fired"] for s in t["sources"] for k in s}
    assert {k for t in with_entry["triggers_fired"] for s in t["sources"] for k in s} == source_keys
    assert _CORE_SCORE == 0.9  # the reused constant (R3: core 0.9, flip 0.5)
