"""S7 — migration 0065, proven at the DB: the ``data_repair`` journal and the ``edition_cause`` set are
tenant-scoped and append-only (the ``no_update`` trigger), the journal takes only apply / revoke and a
non-negative count, a cause row is exactly one of a company-night or an episode and one per key, and the file
re-executes as a no-op. Postgres only (no engine), so it runs in a lean venv too."""

from __future__ import annotations

import uuid
from datetime import date, timedelta

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID
from repositories import data_repair_repo
from tests.editions.test_store_migration import _edition, _thesis

_SQL = (MIGRATIONS_DIR / "0065_data_repair_edition_cause.sql").read_text(encoding="utf-8")


def _cause(db, edition_id, thesis_id, sid, *, night=None, arm=None, cause="rule_change"):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO edition_cause (tenant_id, edition_id, thesis_id, security_id, night, arm_date, "
            "cause) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (DEFAULT_TENANT_ID, edition_id, thesis_id, sid, night, arm, cause),
        )
        return cur.fetchone()["id"]


def test_the_file_re_executes_as_a_no_op_and_carries_the_triggers(db):
    with db.cursor() as cur:
        cur.execute(_SQL)
        for table in ("data_repair", "edition_cause"):
            cur.execute(
                f"SELECT tgname FROM pg_trigger WHERE tgrelid = '{table}'::regclass AND NOT tgisinternal"
            )
            assert [r["tgname"] for r in cur.fetchall()] == ["no_update"], table
            cur.execute(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = %s AND column_name = 'tenant_id'",
                (table,),
            )
            assert cur.fetchone()["is_nullable"] == "NO", table
    db.commit()


def test_the_journal_is_append_only_and_checked(db):
    rid = data_repair_repo.journal(
        db, tool="repair_test", summary="appended 3 rows", rows_changed=3, counts={"a": 3}
    )
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with db.cursor() as cur:
            cur.execute("UPDATE data_repair SET summary = 'x' WHERE id = %s", (rid,))
    db.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO data_repair (tenant_id, tool, action, summary, rows_changed) "
                "VALUES (%s, 't', 'undo', 's', 0)",
                (DEFAULT_TENANT_ID,),
            )
    db.rollback()
    with pytest.raises(ValueError):
        data_repair_repo.journal(db, tool="t", summary="s", rows_changed=-1)
    with pytest.raises(ValueError):
        data_repair_repo.journal(db, tool="t", summary="s", rows_changed=0, action="undo")
    db.rollback()


def test_pending_reads_the_rows_after_a_mark_oldest_first(db):
    a = data_repair_repo.journal(db, tool="t1", summary="first", rows_changed=1)
    with db.cursor() as cur:
        cur.execute("SELECT applied_at FROM data_repair WHERE id = %s", (a,))
        mark = cur.fetchone()["applied_at"]
    db.commit()
    b = data_repair_repo.journal(db, tool="t2", summary="second", rows_changed=0, action="revoke")
    rows = data_repair_repo.pending(
        db, tenant_id=DEFAULT_TENANT_ID, after=mark - timedelta(seconds=1)
    )
    assert [r.id for r in rows] == [a, b]
    assert [
        r.id for r in data_repair_repo.pending(db, tenant_id=DEFAULT_TENANT_ID, after=mark)
    ] == [b]
    assert (
        data_repair_repo.pending(
            db, tenant_id=DEFAULT_TENANT_ID, after=mark - timedelta(seconds=1), through=mark
        )[0].id
        == a
    )
    other = uuid.uuid4()
    assert data_repair_repo.pending(db, tenant_id=other, after=mark - timedelta(days=1)) == []
    db.commit()


def test_a_cause_row_is_a_company_night_or_an_episode_and_one_per_key(db):
    tid, sid = _thesis(db), uuid.uuid4()
    eid = _edition(db)
    cid = _cause(db, eid, tid, sid, night=date(2026, 7, 10))
    _cause(db, eid, tid, sid, arm=date(2026, 7, 10))  # the same day as an episode is its own key
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with db.cursor() as cur:
            cur.execute("UPDATE edition_cause SET cause = 'unexplained' WHERE id = %s", (cid,))
    db.rollback()
    with pytest.raises(psycopg.errors.UniqueViolation):
        _cause(db, eid, tid, sid, night=date(2026, 7, 10))
    db.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):  # neither
        _cause(db, eid, tid, sid)
    db.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):  # both
        _cause(db, eid, tid, sid, night=date(2026, 7, 9), arm=date(2026, 7, 9))
    db.rollback()
    with pytest.raises(psycopg.errors.CheckViolation):  # a cause the trial never stores
        _cause(db, eid, tid, sid, night=date(2026, 7, 8), cause="late_data")
    db.rollback()


def test_a_thesis_delete_cascades_its_cause_rows(db):
    tid, sid = _thesis(db), uuid.uuid4()
    eid = _edition(db)
    _cause(db, eid, tid, sid, night=date(2026, 7, 10))
    db.commit()
    with db.cursor() as cur:
        cur.execute("DELETE FROM thesis WHERE id = %s", (tid,))
        cur.execute("SELECT count(*) AS n FROM edition_cause WHERE edition_id = %s", (eid,))
        assert cur.fetchone()["n"] == 0
    db.commit()
