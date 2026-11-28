"""Migration 0219 — the split list's ``add`` verb (framework completion F2-1: the steward adds a split no source lists).

On the migrated test database: ``action`` admits ``add`` beside the six verbs before it and refuses anything else.
Then (the 0215 precedent: the runner applies each file ONCE, so the test re-executes the FILE's SQL) over the
pre-0219 shape, rebuilt inside the test's transaction: a stored row survives unchanged, and a second run changes
nothing (the table is COUNTED and checksummed, the constraints compared). Everything is rolled back at the end (DDL
is transactional). DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR
from domain.scopes import DATA_TENANT_ID

_FILE = "0219_security_split_add.sql"
_SQL = (MIGRATIONS_DIR / _FILE).read_text(encoding="utf-8")
KEY = date(2025, 1, 15)
_BEFORE = "('backfill', 'detect', 'confirm', 'reject', 'revoke', 'restore')"


def _security(cur) -> uuid.UUID:
    sid = uuid.uuid4()
    cur.execute(
        "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
        (sid, DATA_TENANT_ID, "VRM", date(2020, 1, 1)),
    )
    return sid


def _split(cur, sid: uuid.UUID, **over) -> None:
    row = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "key_date": KEY,
        "effective_date": KEY,
        "ratio_from": 5,
        "ratio_to": 1,
        "status": "confirmed",
        "confirmed_via": "operator",
        "value_source": "steward",
        "cited_url": "https://example.test/vrm/8-k",
        "tape_override": True,
        "action": "add",
        "author": "operator",
        "valid_from": KEY,
        **over,
    }
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    cur.execute(f"INSERT INTO security_split ({cols}) VALUES ({marks})", tuple(row.values()))


def _action_check(cur) -> str:
    cur.execute(
        "SELECT pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = 'security_split'::regclass AND conname = 'security_split_action_check'"
    )
    return cur.fetchone()["def"]


def _constraints(cur) -> dict[str, str]:
    cur.execute(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = 'security_split'::regclass AND contype = 'c'"
    )
    return {r["conname"]: r["def"] for r in cur.fetchall()}


def _checksum(cur) -> str:
    cur.execute(
        "SELECT count(*)::text || md5(coalesce(string_agg(s::text, '|' ORDER BY s::text), '')) AS h "
        "FROM security_split s"
    )
    return cur.fetchone()["h"]


def test_0219_is_applied_to_the_test_database(db):
    with db.cursor() as cur:
        check = _action_check(cur)
        for verb in ("backfill", "detect", "confirm", "reject", "revoke", "restore", "add"):
            assert f"'{verb}'" in check, verb
        cur.execute("SELECT count(*) AS n FROM schema_migrations WHERE filename = %s", (_FILE,))
        assert cur.fetchone()["n"] == 1
    db.rollback()


@pytest.mark.parametrize(
    "action,allowed",
    [
        ("add", True),  # the steward's add (F2-1)
        ("confirm", True),  # the verbs before it are unchanged
        ("detect", True),
        ("restore", True),
        ("bogus", False),
        ("ADD", False),
    ],
)
def test_0219_admits_add_and_refuses_anything_else(db, action, allowed):
    try:
        with db.cursor() as cur:
            sid = _security(cur)
            if allowed:
                _split(cur, sid, action=action)
            else:
                with pytest.raises(psycopg.errors.CheckViolation):
                    _split(cur, sid, action=action)
    finally:
        db.rollback()


def test_0219_over_the_old_shape_keeps_every_row_and_reruns_as_a_no_op(db):
    try:
        with db.cursor() as cur:
            # the pre-0219 shape: 0061's six verbs
            cur.execute("ALTER TABLE security_split DROP CONSTRAINT security_split_action_check")
            cur.execute(
                "ALTER TABLE security_split ADD CONSTRAINT security_split_action_check "
                f"CHECK (action IN {_BEFORE})"
            )
            sid = _security(cur)
            _split(cur, sid, action="confirm")
            cur.execute("SAVEPOINT before_add")
            with pytest.raises(psycopg.errors.CheckViolation):
                _split(cur, sid, action="add", key_date=date(2025, 2, 3))
            cur.execute("ROLLBACK TO SAVEPOINT before_add")
            held = _checksum(cur)

            cur.execute(_SQL)
            assert _checksum(cur) == held  # the stored row is untouched
            assert "'add'" in _action_check(cur)
            _split(cur, sid, action="add", key_date=date(2025, 2, 3))
            before, checks = _checksum(cur), _constraints(cur)
            cur.execute(
                _SQL
            )  # a second run: the runner never makes one, and it would change nothing
            assert _checksum(cur) == before and _constraints(cur) == checks
    finally:
        db.rollback()
