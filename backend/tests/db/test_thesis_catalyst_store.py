"""V2 W6a: the counting-catalyst store (``fact_thesis_catalyst``, migration 0212) — the table, its registrations
and the point-in-time read on both clocks, the night boundary included.

The night rule is the existing one (``domain.market_time.edgar_night`` / ``known_at_for_asof``): a version saved
at 10:00 ET on a weekday counts from that night; one saved before 06:00 belongs to the weekday before; a weekend
save to the Friday. A history view of night N (``known_at`` = 06:00 on the next weekday) sees exactly the versions
saved before that instant. These rows are written at FIXED instants by a test-only insert (the sole writer stamps
the database's ``now()``), the precedent of the other public-clock tests.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import psycopg
import pytest
from psycopg import errors

from db.bitemporal import _FACT_IDENTITY, DISPLAY_ONLY_FACT_TABLES
from db.public_clock import PUBLIC_TIME, SCOPE_COL, THESIS_SCOPED_FACT_TABLES, PublicTime
from db.session import DEFAULT_TENANT_ID
from domain.market_time import edgar_night, known_at_for_asof
from domain.scopes import SCOPE_OF, Scope
from repositories import thesis_catalyst_repo as repo
from tests.thesis_catalyst_fixtures import card, count, insert_version
from tests.workspaces import new_workspace

ET = ZoneInfo("America/New_York")
_PIN = datetime(2100, 1, 1, tzinfo=timezone.utc)

# the week of 2026-06-01 (Mon) .. 2026-06-05 (Fri)
WED_10 = datetime(2026, 6, 3, 10, 0, tzinfo=ET)
THU_03 = datetime(2026, 6, 4, 3, 0, tzinfo=ET)
THU_0559 = datetime(2026, 6, 4, 5, 59, 59, tzinfo=ET)
THU_0600 = datetime(2026, 6, 4, 6, 0, tzinfo=ET)
SAT_12 = datetime(2026, 6, 6, 12, 0, tzinfo=ET)
TUE, WED, THU, FRI = date(2026, 6, 2), date(2026, 6, 3), date(2026, 6, 4), date(2026, 6, 5)


def _refused(db: psycopg.Connection, exc: type[Exception], query: str, params: tuple) -> None:
    with pytest.raises(exc), db.transaction(), db.cursor() as cur:
        cur.execute(query, params)


def _read(db, thesis_id, night: date, clock: str, workspace=DEFAULT_TENANT_ID) -> list[dict]:
    known_at = known_at_for_asof(night, _PIN) if clock == "public" else _PIN
    return repo.versions_asof(
        db, thesis_id, asof=night, known_at=known_at, workspace=workspace, clock=clock
    )


# --- the registrations ----------------------------------------------------------------------------------------


def test_the_table_is_registered_as_a_workspace_thesis_scoped_dated_fact():
    t = "fact_thesis_catalyst"
    assert SCOPE_OF[t] is Scope.WORKSPACE
    assert _FACT_IDENTITY[t] == ["entry_id"]
    assert t not in DISPLAY_ONLY_FACT_TABLES  # the mirror carries it (W6c reads it in replay)
    assert PUBLIC_TIME[t] == PublicTime("dated", "entered_at", "valid_from")
    assert t in THESIS_SCOPED_FACT_TABLES and SCOPE_COL[t] == "thesis_id"


def test_the_night_mapping_is_edgar_night():
    assert [repo.entry_night(t) for t in (WED_10, THU_03, THU_0559, THU_0600, SAT_12)] == [
        WED,
        WED,
        WED,
        THU,
        FRI,
    ]
    assert repo.entry_night(WED_10) == edgar_night(WED_10)


# --- the table ------------------------------------------------------------------------------------------------


def test_versions_are_append_only(db):
    tid, _, entry = card(db)
    vid = insert_version(db, tid, entry, at=WED_10, valid_from=WED)
    _refused(
        db,
        errors.RaiseException,
        "UPDATE fact_thesis_catalyst SET grade = 'core' WHERE id = %s",
        (vid,),
    )


def test_the_checks(db):
    tid, sids, entry = card(db)
    base = (
        "INSERT INTO fact_thesis_catalyst (tenant_id, thesis_id, entry_id, counts, security_ids, grade, "
        "label, citation, source, entered_at, valid_from, recorded_at) VALUES "
    )
    at = WED_10
    ok = (
        DEFAULT_TENANT_ID,
        tid,
        entry,
        True,
        [sids[0]],
        "core",
        "x",
        "https://a",
        "thesis_catalyst",
    )
    cases = [
        # an unknown grade
        ((*ok[:5], "strong", *ok[6:]), at, at),
        # no citation (blank)
        ((*ok[:7], "  ", ok[8]), at, at),
        # a whole-card entry graded core (N2: flip only)
        ((*ok[:4], [], "core", *ok[6:]), at, at),
        # an unknown source
        ((*ok[:8], "llm"), at, at),
        # an operator version with no entry time
        (ok, None, at),
        # an entry time that is not its recorded_at
        (ok, datetime(2026, 5, 1, tzinfo=timezone.utc), at),
        # the converted theme row ('ratified') with an entry time
        ((*ok[:4], [], "flip", *ok[6:8], "ratified"), at, at),
        # a NULL member
        ((*ok[:4], [sids[0], None], *ok[5:]), at, at),
    ]
    for values, entered, recorded in cases:
        _refused(
            db,
            errors.CheckViolation,
            base + "(%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (*values, entered, WED, recorded),
        )
    # the shapes that ARE accepted: an operator version, and the converted row (no entry time, 'ratified')
    insert_version(db, tid, entry, at=at, valid_from=WED, security_ids=[sids[0]], grade="core")
    with db.cursor() as cur:
        cur.execute(
            base + "(%s, %s, %s, true, '{}', 'flip', 't', 'https://c', 'ratified', NULL, %s, %s)",
            (DEFAULT_TENANT_ID, tid, uuid.uuid4(), date(2026, 1, 15), at),
        )
    assert count(db) == 2


def test_the_0204_columns_are_gone_from_the_calendar_row(db):
    """N11 (a): 0204's four columns were never read and are dropped; the calendar row keeps its own fields."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'catalyst'"
        )
        cols = {r["column_name"] for r in cur.fetchall()}
    assert not cols & {"security_ids", "grade", "citation", "entered_at"}
    assert {"id", "label", "kind", "when_date", "when_label", "ordinal"} <= cols


# --- the point-in-time read -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("saved", "first_night"),
    [(WED_10, WED), (THU_03, WED), (THU_0559, WED), (THU_0600, THU), (SAT_12, FRI)],
)
def test_a_version_counts_from_its_save_night_never_the_night_before(db, saved, first_night):
    tid, _, entry = card(db)
    insert_version(db, tid, entry, at=saved, valid_from=repo.entry_night(saved))
    for clock in ("record", "public"):
        before = date.fromordinal(first_night.toordinal() - 1)
        assert _read(db, tid, before, clock) == [], (clock, before)
        if first_night == FRI:  # Thursday night too: the weekend save is Friday's
            assert _read(db, tid, THU, clock) == []
        rows = _read(db, tid, first_night, clock)
        assert [r["entry_id"] for r in rows] == [entry], clock
        assert [r["entry_id"] for r in _read(db, tid, date(2026, 6, 8), clock)] == [entry]


def test_the_public_clock_hides_a_version_saved_after_the_nights_end(db):
    """A version dated to an earlier night but SAVED after that night ended (a label edit carrying the entry's
    night, N5) is invisible to that night's history view on the public clock: the night shows what was saved by
    06:00 the next weekday."""
    tid, _, entry = card(db)
    insert_version(db, tid, entry, at=WED_10, valid_from=WED, label="first label")
    insert_version(db, tid, entry, at=THU_0600, valid_from=WED, label="later label")
    (wed,) = _read(db, tid, WED, "public")
    assert wed["label"] == "first label"
    (thu,) = _read(db, tid, THU, "public")
    assert thu["label"] == "later label"


def test_the_newest_visible_version_per_entry_wins_on_both_clocks(db):
    """A grade change on Thursday counts from Thursday; Wednesday's history keeps the old grade. A switch-off
    on Friday stops it from Friday; earlier nights still read a counting version."""
    tid, sids, entry = card(db)
    other = uuid.uuid4()
    insert_version(db, tid, entry, at=WED_10, valid_from=WED, security_ids=[sids[0]], grade="flip")
    insert_version(
        db,
        tid,
        entry,
        at=datetime(2026, 6, 4, 11, tzinfo=ET),
        valid_from=THU,
        grade="core",
        security_ids=[sids[0]],
    )
    insert_version(
        db,
        tid,
        entry,
        at=datetime(2026, 6, 5, 11, tzinfo=ET),
        valid_from=FRI,
        counts=False,
        grade="core",
        security_ids=[sids[0]],
    )
    insert_version(db, tid, other, at=WED_10, valid_from=WED)  # a second entry, independent
    for clock in ("record", "public"):
        by_night = {
            n: {r["entry_id"]: (r["grade"], r["counts"]) for r in _read(db, tid, n, clock)}
            for n in (TUE, WED, THU, FRI)
        }
        assert by_night[TUE] == {}
        assert by_night[WED] == {entry: ("flip", True), other: ("flip", True)}
        assert by_night[THU] == {entry: ("core", True), other: ("flip", True)}
        assert by_night[FRI] == {entry: ("core", False), other: ("flip", True)}
        assert [r["entry_id"] for r in repo.counting(_read(db, tid, FRI, clock))] == [other]
        members = {
            r["entry_id"]: r["security_ids"] for r in repo.counting(_read(db, tid, THU, clock))
        }
        assert members == {entry: frozenset({sids[0]}), other: frozenset()}


def test_another_workspace_reads_none_of_the_rows(db):
    w = uuid.UUID("00000000-0000-0000-0000-0000000000b1")
    v = uuid.UUID("00000000-0000-0000-0000-0000000000b2")
    new_workspace(db, "workspace W6a W", workspace_id=w)
    new_workspace(db, "workspace W6a V", workspace_id=v)
    tid, _, entry = card(db, workspace=w)
    insert_version(db, tid, entry, at=WED_10, valid_from=WED, tenant_id=w)
    insert_version(db, tid, uuid.uuid4(), at=WED_10, valid_from=WED, tenant_id=DEFAULT_TENANT_ID)
    for clock in ("record", "public"):
        assert [r["entry_id"] for r in _read(db, tid, WED, clock, workspace=w)] == [entry]
        assert _read(db, tid, WED, clock, workspace=v) == []
    assert [r["entry_id"] for r in repo.current(db, tid)] == [
        entry
    ]  # the card's own workspace only
