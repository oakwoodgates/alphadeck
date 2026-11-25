"""Migration 0215 — the split list's steward-cited values (platform fixes W2-1, decision D3).

On the migrated test database: ``value_source`` admits ``steward``, the two nullable columns (``cited_url``,
``tape_override``) exist, and the steward check refuses a steward row with no cited URL while a system row (both
NULL) is unchanged. Then (the 0079 / 0214 precedent: the runner applies each file ONCE, so the test re-executes the
FILE's SQL) over the pre-0215 shape, rebuilt inside the test's transaction: a stored row survives with NULLs, and a
second run changes nothing (the table is COUNTED and checksummed, the constraints counted). Everything is rolled
back at the end (DDL is transactional). DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR
from domain.scopes import DATA_TENANT_ID

_FILE = "0215_security_split_steward_values.sql"
_SQL = (MIGRATIONS_DIR / _FILE).read_text(encoding="utf-8")
KEY = date(2026, 10, 21)


def _security(cur) -> uuid.UUID:
    sid = uuid.uuid4()
    cur.execute(
        "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
        (sid, DATA_TENANT_ID, "POM", date(2025, 1, 1)),
    )
    return sid


def _split(cur, sid: uuid.UUID, **over) -> None:
    row = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "key_date": KEY,
        "effective_date": KEY,
        "ratio_from": 10,
        "ratio_to": 3,
        "status": "confirmed",
        "confirmed_via": "operator",
        "value_source": "sec",
        "action": "confirm",
        "author": "operator",
        "valid_from": KEY,
        **over,
    }
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    cur.execute(f"INSERT INTO security_split ({cols}) VALUES ({marks})", tuple(row.values()))


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


def test_0215_is_applied_to_the_test_database(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'security_split' "
            "AND column_name IN ('cited_url', 'tape_override') ORDER BY column_name"
        )
        assert cur.fetchall() == [
            {"column_name": "cited_url", "data_type": "text", "is_nullable": "YES"},
            {"column_name": "tape_override", "data_type": "boolean", "is_nullable": "YES"},
        ]
        checks = _constraints(cur)
        assert "'steward'" in checks["security_split_value_source_check"]
        assert "cited_url IS NOT NULL" in checks["security_split_steward_cites"]
        cur.execute("SELECT count(*) AS n FROM schema_migrations WHERE filename = %s", (_FILE,))
        assert cur.fetchone()["n"] == 1


@pytest.mark.parametrize(
    "over,allowed",
    [
        (
            {
                "value_source": "steward",
                "cited_url": "https://example.test/6-k",
                "tape_override": False,
            },
            True,
        ),
        (
            {
                "value_source": "steward",
                "cited_url": "https://example.test/6-k",
                "tape_override": True,
            },
            True,
        ),
        ({"value_source": "steward"}, False),  # a steward's values always cite a document
        ({"value_source": "sec"}, True),  # a system / one-source row: both columns NULL, as before
        ({"value_source": "both", "status": "needs_review", "confirmed_via": None}, True),
        ({"value_source": "tape"}, False),
    ],
)
def test_0215_checks_keep_a_steward_row_to_a_cited_document(db, over, allowed):
    try:
        with db.cursor() as cur:
            sid = _security(cur)
            if allowed:
                _split(cur, sid, **over)
            else:
                with pytest.raises(psycopg.errors.CheckViolation):
                    _split(cur, sid, **over)
    finally:
        db.rollback()


def test_0215_over_the_old_shape_keeps_every_row_and_reruns_as_a_no_op(db):
    try:
        with db.cursor() as cur:
            # the pre-0215 shape: 0052's value_source set, no steward columns
            cur.execute("ALTER TABLE security_split DROP CONSTRAINT security_split_steward_cites")
            cur.execute("ALTER TABLE security_split DROP COLUMN cited_url")
            cur.execute("ALTER TABLE security_split DROP COLUMN tape_override")
            cur.execute(
                "ALTER TABLE security_split DROP CONSTRAINT security_split_value_source_check"
            )
            cur.execute(
                "ALTER TABLE security_split ADD CONSTRAINT security_split_value_source_check "
                "CHECK (value_source IN ('sec', 'polygon', 'both'))"
            )
            sid = _security(cur)
            _split(cur, sid, value_source="polygon")

            cur.execute(_SQL)
            cur.execute("SELECT value_source, cited_url, tape_override FROM security_split")
            assert cur.fetchall() == [
                {"value_source": "polygon", "cited_url": None, "tape_override": None}
            ]
            before, checks = _checksum(cur), _constraints(cur)
            cur.execute(
                _SQL
            )  # a second run: the runner never makes one, and it would change nothing
            assert _checksum(cur) == before and _constraints(cur) == checks
            assert "'steward'" in checks["security_split_value_source_check"]
    finally:
        db.rollback()
