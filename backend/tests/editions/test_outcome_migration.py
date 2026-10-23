"""S5 — migration 0064, the INK table (``edition_outcome``), proven at the DB: tenant-scoped, append-only (the
``no_update`` trigger), one row per episode (edition, thesis, company, arm date), only the two ink kinds, and the
file re-executes as a no-op. Postgres only (no engine), so it runs in a lean venv too."""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest
from psycopg.types.json import Json

from db.migrate import MIGRATIONS_DIR
from db.session import DEFAULT_TENANT_ID
from tests.editions.test_store_migration import CUT, _edition, _thesis

_SQL = (MIGRATIONS_DIR / "0064_edition_outcome.sql").read_text(encoding="utf-8")
ARM = date(2026, 7, 1)


def _ink(db, edition_id, thesis_id, security_id, *, kind="matured", arm=ARM):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO edition_outcome (tenant_id, edition_id, thesis_id, security_id, arm_date, "
            "exit_by, kind, outcome, night, prices_known_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                DEFAULT_TENANT_ID,
                edition_id,
                thesis_id,
                security_id,
                arm,
                date(2026, 7, 20),
                kind,
                Json({"forward_return": 0.1}),
                date(2026, 7, 21),
                CUT,
            ),
        )
        return cur.fetchone()["id"]


def test_the_file_re_executes_as_a_no_op_and_carries_the_trigger(db):
    with db.cursor() as cur:
        cur.execute(_SQL)
        cur.execute(
            "SELECT tgname FROM pg_trigger WHERE tgrelid = 'edition_outcome'::regclass "
            "AND NOT tgisinternal"
        )
        assert [r["tgname"] for r in cur.fetchall()] == ["no_update"]
        cur.execute(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'edition_outcome' AND column_name = 'tenant_id'"
        )
        assert cur.fetchone()["is_nullable"] == "NO"
    db.commit()


def test_an_ink_is_never_updated_and_never_duplicated(db):
    tid, sid = _thesis(db), uuid.uuid4()
    eid = _edition(db)
    oid = _ink(db, eid, tid, sid)
    db.commit()
    with pytest.raises(psycopg.errors.RaiseException, match="append-only"):
        with db.cursor() as cur:
            cur.execute(
                "UPDATE edition_outcome SET kind = 'running_at_close' WHERE id = %s", (oid,)
            )
    db.rollback()
    with pytest.raises(psycopg.errors.UniqueViolation):
        _ink(db, eid, tid, sid)  # the same episode again
    db.rollback()
    _ink(
        db, eid, tid, sid, arm=date(2026, 7, 2)
    )  # another arm of the same company is its own episode
    db.commit()


def test_only_the_two_ink_kinds(db):
    tid = _thesis(db)
    eid = _edition(db)
    with pytest.raises(psycopg.errors.CheckViolation):
        _ink(db, eid, tid, uuid.uuid4(), kind="dearmed")
    db.rollback()
