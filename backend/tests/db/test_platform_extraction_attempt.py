"""V2 W3a: ``platform_extraction_attempt`` (migration 0207), the append-only record of every extraction attempt.

One row per attempt to extract a company's platform values (applied, candidate, nothing found, skipped or failed),
keyed on the filings the extractor read (``inputs_key``). The gate (``platform_values.extraction.due``) reads it;
the runner writes it. These tests pin the shapes the table refuses, that it is never updated, that it is platform
data, and that the migration is idempotent.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). Every write is rolled back.
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest
from psycopg import errors

from db.migrate import MIGRATIONS_DIR
from domain.scopes import DATA_TENANT_ID, SCOPE_OF, Scope, owner_of

_MIGRATION = "0207_v2_platform_extraction_attempt.sql"
_KEY = "1234567|10-Q:0001-26-000002|10-K:|20-F:|40-F:"


def _security(db: psycopg.Connection, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, "0001234567", date(2026, 1, 1)),
        )
    return sid


def _insert(db: psycopg.Connection, sid: uuid.UUID, **cols) -> uuid.UUID:
    row = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "run_kind": "nightly",
        "inputs_key": _KEY,
        "status": "done",
        **cols,
    }
    with db.cursor() as cur:
        cur.execute(
            f"INSERT INTO platform_extraction_attempt ({', '.join(row)}) "  # noqa: S608
            f"VALUES ({', '.join(['%s'] * len(row))}) RETURNING id",
            tuple(row.values()),
        )
        return cur.fetchone()["id"]


def _refused(db: psycopg.Connection, exc: type[BaseException], fn, *args, **kwargs) -> None:
    """``fn`` raises ``exc`` inside a savepoint, so the test's transaction stays usable."""
    with pytest.raises(exc):
        with db.transaction():
            fn(*args, **kwargs)


def test_every_good_shape_inserts(db):
    sid = _security(db, "GOOD")
    for kind in ("nightly", "onboarding", "backfill", "steward", "get_data"):
        _insert(db, sid, run_kind=kind)
    _insert(db, sid, status="done", shares_reason="applied", cash_reason="nothing_found")
    _insert(db, sid, status="error", error="SEC unreachable")
    _insert(db, sid, status="busy", detail="company busy")
    _insert(db, sid, status="skipped", detail="no CIK", inputs_key="|10-Q:|10-K:|20-F:|40-F:")
    _insert(db, sid, live_fetches=2)
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) AS n FROM platform_extraction_attempt WHERE security_id = %s", (sid,)
        )
        assert cur.fetchone()["n"] == 10
    db.rollback()


def test_an_error_without_text_is_refused(db):
    sid = _security(db, "ERR")
    _refused(db, errors.CheckViolation, _insert, db, sid, status="error")
    db.rollback()


@pytest.mark.parametrize(
    "cols",
    [{"status": "failed"}, {"run_kind": "cron"}, {"live_fetches": -1}],
    ids=["unknown status", "unknown run kind", "negative fetches"],
)
def test_an_unknown_status_or_run_kind_is_refused(db, cols):
    sid = _security(db, "BAD")
    _refused(db, errors.CheckViolation, _insert, db, sid, **cols)
    db.rollback()


def test_an_error_is_one_bounded_line(db):
    sid = _security(db, "LONG")
    _refused(db, errors.CheckViolation, _insert, db, sid, status="error", error="x" * 201)
    _refused(db, errors.CheckViolation, _insert, db, sid, status="error", error="a\nb")
    db.rollback()


def test_an_attempt_is_never_updated(db):
    sid = _security(db, "UPD")
    aid = _insert(db, sid)

    def update() -> None:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE platform_extraction_attempt SET status = 'error', error = 'x' WHERE id = %s",
                (aid,),
            )

    _refused(db, errors.RaiseException, update)
    db.rollback()


def test_the_table_is_platform_data():
    assert SCOPE_OF["platform_extraction_attempt"] is Scope.DATA
    assert owner_of("platform_extraction_attempt") == "platform"


def test_the_migration_is_idempotent(db):
    """Re-running the file (twice) changes nothing: the table, its index and trigger are created once, and a
    stored attempt survives."""
    sid = _security(db, "IDM")
    _insert(db, sid)

    def shape() -> tuple:
        with db.cursor() as cur:
            cur.execute(
                "SELECT (SELECT count(*) FROM platform_extraction_attempt) AS rows, "
                "(SELECT count(*) FROM pg_indexes WHERE tablename = 'platform_extraction_attempt') AS ix, "
                "(SELECT count(*) FROM pg_trigger WHERE tgrelid = 'platform_extraction_attempt'::regclass "
                "AND NOT tgisinternal) AS trg, "
                "(SELECT count(*) FROM pg_constraint "
                " WHERE conrelid = 'platform_extraction_attempt'::regclass) AS con"
            )
            return tuple(cur.fetchone().values())

    before = shape()
    assert before[:3] == (1, 2, 1)  # the primary key and the company index; the no_update trigger
    try:
        for _ in range(2):
            with db.cursor() as cur:
                cur.execute((MIGRATIONS_DIR / _MIGRATION).read_text(encoding="utf-8"))
        assert shape() == before
    finally:
        db.rollback()
