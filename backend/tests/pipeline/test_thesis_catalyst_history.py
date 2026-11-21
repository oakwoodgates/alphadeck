"""V2 W6c: R5, NO LOOKAHEAD for a thesis catalyst entry, on the live path (both clocks) and the replay mirror.

An entry counts from the night it was SAVED (its public instant, ``entry_night`` = ``domain.market_time.
edgar_night``), never from the date typed on the calendar. An edit of the grade or the members counts from the
edit, and earlier nights still read the earlier version; switching it off stops it from that night, and earlier
nights still count it. Fixed instants (the test-only insert); the 06:00 ET boundary is pinned.

The seeded nuclear card: SMR's volume-backed breakout is fresh on Tuesday 2026-06-02 (the arming night).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, State
from domain.market_time import known_at_for_asof
from domain.thesis import Catalyst
from pipeline.call_for_thesis import call_for_thesis, load_call_inputs
from pipeline.core import assemble_from_pit
from pipeline.seed import NUCLEAR_THESIS_ID, SMR_ID, seed_nuclear
from replay.export import export_snapshot
from replay.pit import ReplayPointInTimeData, connect_mirror
from repositories import thesis_catalyst_repo, thesis_repo
from repositories.thesis_catalyst_repo import entry_night
from tests.thesis_catalyst_fixtures import insert_version

ET = ZoneInfo("America/New_York")
PIN = datetime(2100, 1, 1, tzinfo=timezone.utc)
MON, TUE, WED = date(2026, 6, 1), date(2026, 6, 2), date(2026, 6, 3)


def _known(clock: str, night: date) -> datetime:
    return known_at_for_asof(night, PIN) if clock == "public" else PIN


def _card(db, night: date, clock: str):
    card = call_for_thesis(
        db, NUCLEAR_THESIS_ID, night, known_at=_known(clock, night), clock=clock, record=False
    )
    db.commit()
    return card


def _smr_catalyst(card) -> list:
    out = [t for t in card.triggers_fired if t.security_id == SMR_ID and t.kind is Kind.CATALYST]
    for m in [*card.armed_members, *card.watch_members]:
        out += [t for t in m.triggers if t.security_id == SMR_ID and t.kind is Kind.CATALYST]
    return out


def _grade_on(card) -> Grade | None:
    found = _smr_catalyst(card)
    return found[0].grade if found else None


def _save(db, at: datetime, entry: uuid.UUID, **kw) -> None:
    insert_version(
        db,
        NUCLEAR_THESIS_ID,
        entry,
        at=at,
        valid_from=entry_night(at),
        security_ids=kw.pop("security_ids", [SMR_ID]),
        **kw,
    )
    db.commit()


@pytest.fixture
def nuclear(db):
    seed_nuclear(db)
    db.commit()


def test_the_entry_night_mapping():
    assert entry_night(datetime(2026, 6, 3, 10, tzinfo=ET)) == WED  # Wednesday 10:00
    assert entry_night(datetime(2026, 6, 4, 3, tzinfo=ET)) == WED  # Thursday 03:00
    assert entry_night(datetime(2026, 6, 6, 12, tzinfo=ET)) == date(
        2026, 6, 5
    )  # Saturday -> Friday


@pytest.mark.parametrize("clock", ["record", "public"])
def test_history_view_before_entry_night_is_unarmed(db, nuclear, clock):
    """Saved Tuesday 10:00 ET: absent on Monday's night (whatever the calendar says), armed on Tuesday's."""
    _save(db, datetime(2026, 6, 2, 10, tzinfo=ET), uuid.uuid4(), grade="core")
    for night in (date(2026, 5, 22), date(2026, 5, 29), MON):
        assert _smr_catalyst(_card(db, night, clock)) == [], night
    tue = _card(db, TUE, clock)
    assert tue.state is State.ARMED and _grade_on(tue) is Grade.CORE


@pytest.mark.parametrize(
    ("saved", "counts_monday"),
    [
        (datetime(2026, 6, 2, 5, 59, tzinfo=ET), True),  # before EDGAR's open: Monday's night
        (datetime(2026, 6, 2, 6, 0, tzinfo=ET), False),  # at the open: Tuesday's night
    ],
)
def test_the_0600_et_boundary(db, nuclear, saved, counts_monday):
    _save(db, saved, uuid.uuid4(), grade="flip")
    for clock in ("record", "public"):
        assert bool(_smr_catalyst(_card(db, MON, clock))) is counts_monday, clock
        assert _grade_on(_card(db, TUE, clock)) is Grade.FLIP, clock


def test_the_live_board_during_the_session_does_not_see_todays_entry(db, nuclear):
    """Tuesday 10:30 ET, the serve path still reads Monday's night (record clock, known_at = now): an entry
    saved at 10:00 that morning counts from Tuesday's night, so Monday's live card does not have it.
    """
    _save(db, datetime(2026, 6, 2, 10, tzinfo=ET), uuid.uuid4(), grade="core")
    card = call_for_thesis(
        db, NUCLEAR_THESIS_ID, MON, known_at=datetime(2026, 6, 2, 10, 30, tzinfo=ET), record=False
    )
    assert _smr_catalyst(card) == []


@pytest.mark.parametrize("clock", ["record", "public"])
def test_edit_counts_from_the_edit(db, nuclear, clock):
    entry = uuid.uuid4()
    _save(db, datetime(2026, 5, 29, 10, tzinfo=ET), entry, grade="flip")  # Friday
    _save(db, datetime(2026, 6, 2, 10, tzinfo=ET), entry, grade="core")  # Tuesday: grade -> core
    assert (
        _grade_on(_card(db, MON, clock)) is Grade.FLIP
    )  # the night before the edit: the old version
    assert _grade_on(_card(db, TUE, clock)) is Grade.CORE


@pytest.mark.parametrize("clock", ["record", "public"])
def test_a_members_edit_counts_from_the_edit(db, nuclear, clock):
    from pipeline.seed import LEU_ID

    entry = uuid.uuid4()
    _save(db, datetime(2026, 5, 29, 10, tzinfo=ET), entry, grade="flip", security_ids=[LEU_ID])
    _save(db, datetime(2026, 6, 2, 10, tzinfo=ET), entry, grade="flip", security_ids=[SMR_ID])
    assert _smr_catalyst(_card(db, MON, clock)) == []
    assert _grade_on(_card(db, TUE, clock)) is Grade.FLIP


@pytest.mark.parametrize("clock", ["record", "public"])
def test_switch_off_leaves_history(db, nuclear, clock):
    entry = uuid.uuid4()
    _save(db, datetime(2026, 6, 1, 10, tzinfo=ET), entry, grade="core")  # Monday
    _save(db, datetime(2026, 6, 3, 10, tzinfo=ET), entry, grade="core", counts=False)  # Wednesday
    assert _grade_on(_card(db, MON, clock)) is Grade.CORE
    assert (
        _card(db, TUE, clock).state is State.ARMED
    )  # still counted the night before the switch-off
    assert _smr_catalyst(_card(db, WED, clock)) == []
    assert _smr_catalyst(_card(db, date(2026, 6, 4), clock)) == []


def test_typed_date_never_reaches_the_call(db, nuclear):
    """The REAL writer: a calendar row typed 2026-03-01 switched on today counts from today's night, so no
    history night of the fixture tape (whose dates are all before today) sees it, on either clock.
    """
    entry = uuid.uuid4()
    thesis_repo.set_catalysts(
        db,
        NUCLEAR_THESIS_ID,
        [
            *thesis_repo.get(db, NUCLEAR_THESIS_ID, workspace=DEFAULT_TENANT_ID).catalysts,
            Catalyst(
                id=entry, label="NRC design approval", kind="regulatory", when_date=date(2026, 3, 1)
            ),
        ],
        tenant_id=DEFAULT_TENANT_ID,
    )
    thesis_catalyst_repo.append_version(
        db,
        NUCLEAR_THESIS_ID,
        entry,
        counts=True,
        grade="core",
        security_ids=[SMR_ID],
        citation="https://www.nrc.gov/",
        relevant_until=None,
        label="NRC design approval",
        user_id=None,
    )
    db.commit()
    (row,) = [
        r for r in thesis_catalyst_repo.current(db, NUCLEAR_THESIS_ID) if r["entry_id"] == entry
    ]
    assert row["valid_from"] > date(2026, 6, 2) and row["valid_from"] != date(2026, 3, 1)
    for clock in ("record", "public"):
        for night in (date(2026, 3, 2), TUE):
            assert _smr_catalyst(_card(db, night, clock)) == [], (clock, night)


@pytest.mark.parametrize("clock", ["record", "public"])
def test_the_mirror_reads_the_same_history(db, nuclear, clock, tmp_path):
    """The replay mirror (both clocks) judges each night exactly as the live path does."""
    entry = uuid.uuid4()
    _save(db, datetime(2026, 5, 29, 10, tzinfo=ET), entry, grade="flip")
    _save(db, datetime(2026, 6, 2, 5, 59, tzinfo=ET), entry, grade="core")  # Monday's night
    _save(db, datetime(2026, 6, 3, 10, tzinfo=ET), entry, grade="core", counts=False)
    export_snapshot(db, tmp_path / "m", clock=clock, workspace=DEFAULT_TENANT_ID)
    con = connect_mirror(tmp_path / "m")
    try:
        for night in (date(2026, 5, 28), date(2026, 5, 29), MON, TUE, WED):
            known_at = _known(clock, night)
            thesis, pit = load_call_inputs(
                db, NUCLEAR_THESIS_ID, night, known_at=known_at, clock=clock
            )
            live = assemble_from_pit(pit, thesis, night, DEFAULT_CONFIG)
            rep = ReplayPointInTimeData(
                con, asof=night, known_at=known_at, workspace=DEFAULT_TENANT_ID
            )
            mirror = assemble_from_pit(rep, thesis, night, DEFAULT_CONFIG)
            assert mirror.model_dump(mode="json") == live.model_dump(mode="json"), night
        db.commit()
    finally:
        con.close()
    # not vacuous: the nights differ the way the versions say
    assert _smr_catalyst(_card(db, date(2026, 5, 28), clock)) == []
    assert _grade_on(_card(db, date(2026, 5, 29), clock)) is Grade.FLIP
    assert _grade_on(_card(db, MON, clock)) is Grade.CORE
    assert _smr_catalyst(_card(db, WED, clock)) == []
