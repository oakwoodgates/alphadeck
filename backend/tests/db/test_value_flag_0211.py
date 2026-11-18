"""V2 W5c: migration 0211 records WHO reopened a user's flag (``value_flag.reopened_by``; ``docs/PLATFORM_VALUES.md``
§Flags, the steward's side).

- one nullable text column: the steward's email on a reopen row (``reopens`` set); when = that row's ``opened_at``;
- a CHECK: only a reopen row names a reopener (an ordinary flag never does);
- 0210's reopen trigger and 0202's closed-once trigger still hold (a reopen row with its reopener is accepted, a
  closed row is never edited, its reopener included);
- the file re-runs cleanly (twice) and changes nothing.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). Every write is rolled back.
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest
from psycopg import errors

from db.migrate import MIGRATIONS_DIR
from domain.scopes import DATA_TENANT_ID
from tests.workspaces import new_workspace

_MIGRATION = MIGRATIONS_DIR / "0211_v2_value_flag_reopened_by.sql"
STEWARD = "steward@example.test"


def _security(db: psycopg.Connection) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, "RBY", "0000000099", date(2026, 1, 1)),
        )
    return sid


def _user(db: psycopg.Connection) -> uuid.UUID:
    uid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id) "
            "VALUES (%s, %s, 'u', 'x', 'user', %s)",
            (uid, f"{uid.hex}@example.test", new_workspace(db, "a flagger's workspace")),
        )
    return uid


def _flag(db: psycopg.Connection, uid, sid, **over) -> uuid.UUID:
    row = {"user_id": uid, "security_id": sid, "value_type": "shares_outstanding", **over}
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO value_flag ({cols}) VALUES ({marks}) RETURNING id", tuple(row.values())
        )
        return cur.fetchone()["id"]


def _close(db: psycopg.Connection, fid) -> None:
    with db.cursor() as cur:
        cur.execute(
            "UPDATE value_flag SET closed_at = now(), closed_by = %s, outcome = 'kept', "
            "outcome_note = 'right' WHERE id = %s",
            (STEWARD, fid),
        )


def _refused(db: psycopg.Connection, exc: type[BaseException], query: str, params=()) -> None:
    with pytest.raises(exc):
        with db.transaction():
            with db.cursor() as cur:
                cur.execute(query, params)


def test_the_reopener_column_exists_and_is_nullable(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'value_flag' "
            "AND column_name = 'reopened_by'"
        )
        assert tuple(cur.fetchone().values()) == ("text", "YES")


def test_a_reopen_row_records_its_reopener(db):
    sid, uid = _security(db), _user(db)
    first = _flag(db, uid, sid)
    _close(db, first)
    again = _flag(db, uid, sid, reopens=first, reopened_by=STEWARD)
    with db.cursor() as cur:
        cur.execute("SELECT reopened_by, reopens FROM value_flag WHERE id = %s", (again,))
        assert tuple(cur.fetchone().values()) == (STEWARD, first)


def test_only_a_reopen_row_names_a_reopener(db):
    sid, uid = _security(db), _user(db)
    _refused(
        db,
        errors.CheckViolation,
        "INSERT INTO value_flag (user_id, security_id, value_type, reopened_by) "
        "VALUES (%s, %s, 'shares_outstanding', %s)",
        (uid, sid, STEWARD),
    )


def test_the_older_triggers_still_hold(db):
    sid, uid = _security(db), _user(db)
    first = _flag(db, uid, sid)
    # 0210: an open flag is never reopened, reopener or not
    _refused(
        db,
        errors.RaiseException,
        "INSERT INTO value_flag (user_id, security_id, value_type, reopens, reopened_by) "
        "VALUES (%s, %s, 'shares_outstanding', %s, %s)",
        (uid, sid, first, STEWARD),
    )
    _close(db, first)
    again = _flag(db, uid, sid, reopens=first, reopened_by=STEWARD)
    _close(db, again)
    # 0202: a closed row is never edited, its reopener included
    _refused(
        db,
        errors.RaiseException,
        "UPDATE value_flag SET reopened_by = 'other@example.test' WHERE id = %s",
        (again,),
    )


def test_the_migration_re_runs_cleanly(db):
    sid = _security(db)
    _flag(db, _user(db), sid, note="kept across a re-run")

    def shape() -> tuple:
        with db.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM value_flag) AS rows, "
                "(SELECT count(*) FROM pg_indexes WHERE tablename = 'value_flag') AS ix, "
                "(SELECT count(*) FROM pg_trigger WHERE tgrelid = 'value_flag'::regclass "
                "AND NOT tgisinternal) AS trg, "
                "(SELECT count(*) FROM pg_constraint WHERE conrelid = 'value_flag'::regclass) AS con"
            )
            return tuple(cur.fetchone().values())

    before = shape()
    for _ in range(2):
        with db.cursor() as cur:
            cur.execute(_MIGRATION.read_text(encoding="utf-8"))
    assert shape() == before
