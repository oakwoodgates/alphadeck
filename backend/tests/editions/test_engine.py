"""S2 — the editions ENGINE: the roster by join date, the position from the decisions log at each night's cap,
the public clock end to end, the consistent cut, and determinism across workers."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

import editions.engine as engine
from db.session import DEFAULT_TENANT_ID
from domain.enums import State
from editions.engine import (
    ThesisJob,
    build_job,
    night_cap,
    night_members,
    open_mirror,
    prepare,
    run_job,
    run_jobs,
)
from editions.entry import Entry, Snapshot, ThesisRoster
from replay.export import export_snapshot
from repositories.calls_repo import _canonical
from tests.editions.fixtures import member_rows, seed_big_buy, seed_tape
from tests.scoreboard.helpers import persist_thesis

A, B = uuid.UUID(int=0xB001), uuid.UUID(int=0xB002)
KB = datetime(2026, 10, 20, tzinfo=timezone.utc)


def _entry(tid, sid, d: date, estimated=True) -> Entry:
    return Entry(tid, sid, d, estimated, "first_pull" if estimated else "snapshot")


def _seeded_roster(
    tid, *sids, created=datetime(2026, 5, 1, tzinfo=timezone.utc), seed_at=None, extra=()
):
    seed = Snapshot(
        seed_at or datetime(2026, 9, 15, 12, 36, tzinfo=timezone.utc), member_rows(*sids)
    )
    return ThesisRoster(
        thesis_id=tid,
        tenant_id=DEFAULT_TENANT_ID,
        created_at=created,
        archived_at=None,
        snapshots=(seed, *extra),
        live_security_ids=frozenset(sids),
        seed=seed,
    )


def real_roster(db, thesis_id) -> ThesisRoster:
    """The thesis's REAL roster rows (its ``basket_snapshot``, written by the promote) treated as a 0043 seed,
    so nights before the snapshot read its members by join date — exactly the members ``get_asof`` returns.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT t.tenant_id, t.created_at, s.taken_at, s.members FROM thesis t "
            "JOIN basket_snapshot s ON s.thesis_id = t.id WHERE t.id = %s "
            "ORDER BY s.taken_at DESC LIMIT 1",
            (thesis_id,),
        )
        r = cur.fetchone()
    db.commit()
    seed = Snapshot(r["taken_at"], tuple(r["members"]))
    return ThesisRoster(
        thesis_id=thesis_id,
        tenant_id=r["tenant_id"],
        created_at=datetime(2026, 5, 1, tzinfo=timezone.utc),
        archived_at=None,
        snapshots=(seed,),
        live_security_ids=seed.security_ids,
        seed=seed,
    )


# --- the roster on a night ---------------------------------------------------------------------------


def test_before_the_seed_a_member_is_in_the_basket_only_from_its_join_date():
    tid = uuid.uuid4()
    roster = _seeded_roster(tid, A, B)
    entries = {A: _entry(tid, A, date(2026, 6, 1)), B: _entry(tid, B, date(2026, 7, 1))}

    def ids(night):
        return [m["security_id"] for m in night_members(roster, entries, night, known_by=KB)]

    assert ids(date(2026, 6, 30)) == [str(A)]  # B's entry is after this night: absent
    assert ids(date(2026, 7, 1)) == [str(A), str(B)]
    assert ids(date(2026, 9, 16)) == [str(A), str(B)]  # the seed snapshot itself, once taken


def test_a_later_snapshot_is_read_exactly_and_never_before_it_was_taken():
    tid = uuid.uuid4()
    later = Snapshot(datetime(2026, 9, 29, 18, 0, tzinfo=timezone.utc), member_rows(A))  # B removed
    roster = _seeded_roster(tid, A, B, extra=(later,))
    entries = {A: _entry(tid, A, date(2026, 6, 1)), B: _entry(tid, B, date(2026, 6, 1))}

    def names(night, kb=KB):
        return {m["security_id"] for m in night_members(roster, entries, night, known_by=kb)}

    assert names(date(2026, 9, 28)) == {str(A), str(B)}
    assert names(date(2026, 9, 29)) == {str(A)}
    # a cut before the later snapshot never sees it, even on a later night
    assert names(date(2026, 10, 1), datetime(2026, 9, 20, tzinfo=timezone.utc)) == {str(A), str(B)}


def test_a_thesis_born_after_the_snapshot_table_has_no_roster_before_its_first_snapshot():
    tid = uuid.uuid4()
    first = Snapshot(datetime(2026, 10, 1, 15, 0, tzinfo=timezone.utc), member_rows(A))
    roster = ThesisRoster(
        tid, DEFAULT_TENANT_ID, first.taken_at, None, (first,), frozenset({A}), None
    )
    assert night_members(roster, {}, date(2026, 9, 30), known_by=KB) == []
    assert [
        m["security_id"] for m in night_members(roster, {}, date(2026, 10, 1), known_by=KB)
    ] == [str(A)]


def test_no_snapshot_at_all_means_the_caller_uses_the_live_basket():
    tid = uuid.uuid4()
    roster = ThesisRoster(tid, DEFAULT_TENANT_ID, KB, None, (), frozenset({A}), None)
    assert night_members(roster, {}, date(2026, 10, 1), known_by=KB) is None


# --- end to end on the public clock ---------------------------------------------------------------------


def _cut(db, path):
    """The mirror and its cut, the way ``prepare`` takes it (one consistent snapshot)."""
    with engine.consistent_snapshot(db) as at:
        export_snapshot(db, path, clock="public", known_by=at, identity=True)
    return at


def _setup(db, security_id, tmp_path, *, join=date(2026, 6, 8)):
    thesis = persist_thesis(db, security_id)
    seed_tape(db, security_id)
    seed_big_buy(db, security_id)  # accepted 06-17, stored 07-19
    db.commit()
    roster = _seeded_roster(thesis.id, security_id)
    entries = {security_id: _entry(thesis.id, security_id, join)}
    kb = _cut(db, tmp_path / "mirror")
    return thesis, roster, entries, kb


def _cards(db, roster, entries, kb, path, *, end=date(2026, 7, 10)):
    con = open_mirror(path)
    try:
        job = build_job(db, con, roster, entries, end=end, known_by=kb)
        db.commit()
        return job, run_job(con, job)
    finally:
        con.close()


def test_pages_start_at_the_join_date_and_the_buy_counts_from_its_acceptance(
    db, security_id, tmp_path
):
    thesis, roster, entries, kb = _setup(db, security_id, tmp_path)
    _, cards = _cards(db, roster, entries, kb, tmp_path / "mirror")
    by = {c.asof: c for c in cards}
    assert min(by) == date(2026, 6, 8)  # nothing before the company joined (the tape starts 06-01)
    assert by[date(2026, 6, 16)].conviction_grade is None  # traded 06-15, not yet public
    assert (
        by[date(2026, 6, 17)].conviction_grade is not None
    )  # public that night — stored only 07-19
    assert by[date(2026, 6, 17)].state is State.WARMING


def test_a_member_whose_entry_is_after_T_is_absent_at_T(db, security_id, tmp_path):
    thesis = persist_thesis(db, security_id)
    other = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (other, DEFAULT_TENANT_ID, "LATECO", "0007654321", "2026-01-01"),
        )
    seed_tape(db, security_id)
    seed_tape(db, other)
    db.commit()
    roster = _seeded_roster(thesis.id, security_id, other)
    entries = {
        security_id: _entry(thesis.id, security_id, date(2026, 6, 1)),
        other: _entry(thesis.id, other, date(2026, 6, 22)),
    }
    kb = _cut(db, tmp_path / "mirror")
    job, _ = _cards(db, roster, entries, kb, tmp_path / "mirror")
    by = {n.asof: {m.security_id for m in n.basket} for n in job.nights}
    assert by[date(2026, 6, 19)] == {security_id}  # LATECO joins 06-22: absent before it
    assert by[date(2026, 6, 22)] == {security_id, other}


def test_the_position_is_read_at_each_nights_cap(db, security_id, tmp_path, monkeypatch):
    thesis, roster, entries, kb = _setup(db, security_id, tmp_path)
    seen: list[tuple[date, datetime]] = []
    real = engine.decisions_repo.effective_position

    def spy(conn, th, *, asof, known_at):
        seen.append((asof, known_at))
        return real(conn, th, asof=asof, known_at=known_at)

    monkeypatch.setattr(engine.decisions_repo, "effective_position", spy)
    _cards(db, roster, entries, kb, tmp_path / "mirror", end=date(2026, 6, 12))
    assert seen and all(k == night_cap(a, kb) for a, k in seen)


def test_a_job_round_trips_exactly(db, security_id, tmp_path):
    thesis, roster, entries, kb = _setup(db, security_id, tmp_path)
    job, _ = _cards(db, roster, entries, kb, tmp_path / "mirror")
    again = ThesisJob.from_json(job.to_json())
    assert again == job and again.to_json() == job.to_json()


def test_a_cut_cannot_be_later_than_its_snapshot(db, tmp_path):
    later = datetime.now(timezone.utc) + timedelta(days=1)
    with pytest.raises(ValueError, match="cannot be later"):
        prepare(db, tmp_path / "m", end=date(2026, 7, 10), known_by=later)


def test_prepare_reads_one_snapshot_and_restores_the_connection(db, security_id, tmp_path):
    persist_thesis(db, security_id)
    seed_tape(db, security_id)
    db.commit()
    iso, ro = db.isolation_level, db.read_only
    prep = prepare(db, tmp_path / "m", end=date(2026, 7, 10))
    assert prep.cut.known_by == prep.cut.snapshot_at
    assert prep.cut.manifest["known_by"] == prep.cut.known_by.isoformat()
    assert prep.cut.manifest["identity_table"]["rows"] >= 1
    assert (db.isolation_level, db.read_only) == (iso, ro)


@pytest.mark.slow  # spawns six worker processes
@pytest.mark.timeout(300)
def test_workers_1_and_6_are_byte_identical(db, security_id, tmp_path):
    thesis, roster, entries, kb = _setup(db, security_id, tmp_path)
    con = open_mirror(tmp_path / "mirror")
    try:
        jobs = [build_job(db, con, roster, entries, end=date(2026, 7, 10), known_by=kb)]
        # more theses on the same company, each joining later, so all six workers have work
        for k in range(1, 7):
            other = persist_thesis(db, security_id)
            r = _seeded_roster(other.id, security_id)
            e = {
                security_id: _entry(other.id, security_id, date(2026, 6, 1) + timedelta(days=3 * k))
            }
            jobs.append(build_job(db, con, r, e, end=date(2026, 7, 10), known_by=kb))
        db.commit()
    finally:
        con.close()
    one = run_jobs(tmp_path / "mirror", jobs, workers=1)
    six = run_jobs(tmp_path / "mirror", jobs, workers=6)
    assert list(one) == list(six) == [j.thesis_id for j in jobs]
    for tid in one:
        assert [c.model_dump_json() for c in one[tid]] == [c.model_dump_json() for c in six[tid]]
        assert [_canonical(c) for c in one[tid]] == [_canonical(c) for c in six[tid]]
    assert min(c.asof for c in one[jobs[-1].thesis_id]) == date(2026, 6, 19)
