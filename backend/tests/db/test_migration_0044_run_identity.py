"""Migration 0044 — the ``calls`` run-identity columns (``config_hash`` / ``code_sha`` / ``run_kind``).

The runner applies each migration ONCE (``schema_migrations``), so the suite's schema already carries 0044.
These tests re-execute the FILE's SQL directly — it is idempotent by construction (``ADD COLUMN IF NOT
EXISTS`` + ``DROP CONSTRAINT IF EXISTS`` before the ``ADD CONSTRAINT``) — and assert the properties that
make this migration *safe*, which are all about what it does NOT do:

- NOTHING RETROACTIVE. Unlike 0042 there is no derived stamp — we cannot know what config or code produced
  a legacy row, and that is precisely the finding this column exists to stop repeating. Legacy rows stay
  NULL and every other column is byte-identical afterwards.
- THE ``no_update`` TRIGGER STAYS ARMED THROUGHOUT. 0042 had to disable it for its one UPDATE; 0044 has no
  UPDATE at all, so a disabled-and-not-re-enabled guard would be a pure regression. Proven by trying to
  UPDATE after the file runs.
- THE SHAPE IS READ FROM THE DATABASE, not from the file: nullability from ``information_schema`` and the
  value set from ``pg_constraint``. Asserting the DDL text would only prove the file says what the file
  says; asserting the catalog proves the database agrees.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID

_SQL = (MIGRATIONS_DIR / "0044_calls_run_identity.sql").read_text(encoding="utf-8")
_ASOF = date(2026, 8, 7)
_IDENTITY_COLUMNS = ("config_hash", "code_sha", "run_kind")


def _thesis(db) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "legacy", "x"),
        )
    return tid


def _legacy_row(db, tid: uuid.UUID) -> uuid.UUID:
    """A calls row shaped exactly like one written before 0044 existed: the identity columns untouched,
    so they take whatever default the migration gave them."""
    cid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO calls (id, tenant_id, thesis_id, asof, state, verdict, card, recorded_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (
                cid,
                DEFAULT_TENANT_ID,
                tid,
                _ASOF,
                "warming",
                "watching",
                "{}",
                datetime(2026, 8, 8, 2, 35, tzinfo=timezone.utc),
            ),
        )
    return cid


def _snapshot(db, cid: uuid.UUID) -> dict:
    with db.cursor() as cur:
        cur.execute(
            "SELECT asof, state, verdict, card, recorded_at, ingest_fresh, ingest_errors, "
            "       reconstructed, config_hash, code_sha, run_kind "
            "FROM calls WHERE id = %s",
            (cid,),
        )
        return dict(cur.fetchone())


def test_legacy_rows_are_NULL_and_nothing_else_changes(db):
    """The honest value for "we do not know which policy wrote this" is NULL — never a backfilled guess,
    never a coerced default. And the migration must not touch what the row SAYS."""
    tid = _thesis(db)
    cid = _legacy_row(db, tid)
    db.commit()
    before = _snapshot(db, cid)

    with db.cursor() as cur:
        cur.execute(_SQL)
    db.commit()
    after = _snapshot(db, cid)

    assert all(after[c] is None for c in _IDENTITY_COLUMNS)
    assert after == before  # card / state / verdict / asof / recorded_at all byte-identical


def test_the_no_update_trigger_is_still_armed_afterwards(db):
    """0042 disabled this guard for its one legacy stamp; 0044 has no UPDATE and must leave it alone. If a
    future edit here ever adds a stamp, this is the test that refuses to let the guard stay off."""
    tid = _thesis(db)
    _legacy_row(db, tid)
    db.commit()
    with db.cursor() as cur:
        cur.execute(_SQL)
    db.commit()

    with pytest.raises(psycopg.errors.RaiseException):
        with db.cursor() as cur:
            cur.execute("UPDATE calls SET verdict = 'tampered' WHERE thesis_id = %s", (tid,))
    db.rollback()


def test_the_columns_are_NULLABLE_per_the_catalog(db):
    """Read from ``information_schema``, not from the file: a NOT NULL here would have made the migration
    fail closed on every legacy row, so the database's own answer is the one that matters."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name, is_nullable, data_type FROM information_schema.columns "
            "WHERE table_name = 'calls' AND column_name = ANY(%s)",
            (list(_IDENTITY_COLUMNS),),
        )
        cols = {r["column_name"]: r for r in cur.fetchall()}

    assert set(cols) == set(_IDENTITY_COLUMNS)
    for name, row in cols.items():
        assert (
            row["is_nullable"] == "YES"
        ), f"{name} must be nullable — legacy rows carry no identity"
        assert row["data_type"] == "text"


def test_the_run_kind_CHECK_exists_and_pins_the_value_set(db):
    """The three kinds are enforced by the DATABASE, so a typo cannot reach the record from any writer —
    including a future one nobody has written yet."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT conname FROM pg_constraint WHERE conrelid = 'calls'::regclass "
            "AND contype = 'c' AND conname = 'calls_run_kind_check'"
        )
        assert cur.fetchone() is not None

    tid = _thesis(db)
    db.commit()
    for kind in ("cron", "manual", "backfill"):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, run_kind) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (DEFAULT_TENANT_ID, tid, _ASOF, "warming", "watching", "{}", kind),
            )
        db.commit()

    with pytest.raises(psycopg.errors.CheckViolation):
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card, run_kind) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (DEFAULT_TENANT_ID, tid, _ASOF, "warming", "watching", "{}", "weekend"),
            )
    db.rollback()


def test_re_executing_the_file_is_a_clean_no_op(db):
    """Belt-and-braces beside the runner's own once-only tracking: the CHECK is dropped before it is added,
    so a second execution neither errors nor duplicates the constraint."""
    tid = _thesis(db)
    _legacy_row(db, tid)
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls")
        before = cur.fetchone()["n"]

    with db.cursor() as cur:
        cur.execute(_SQL)
        cur.execute(_SQL)
    db.commit()

    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls")
        assert cur.fetchone()["n"] == before
        cur.execute(
            "SELECT count(*) AS n FROM pg_constraint WHERE conrelid = 'calls'::regclass "
            "AND conname = 'calls_run_kind_check'"
        )
        assert cur.fetchone()["n"] == 1
