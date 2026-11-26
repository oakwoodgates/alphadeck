"""Migration 0216 — Massive.com's split label on the split list (platform fixes W6-1).

On the migrated test database: ``polygon_adjustment_type`` exists (text, NULL), and it has NO check: a word Massive.com
starts serving tomorrow must never fail an insert (the reader maps an unknown word to NULL before it gets here, the
table does not police it). Then (the 0215 precedent: the runner applies each file ONCE, so the test re-executes the
FILE's SQL) over the pre-0216 shape, rebuilt inside the test's transaction: a stored row survives with NULL, and a
second run changes nothing (the table is COUNTED and checksummed). Everything is rolled back at the end (DDL is
transactional). DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import date

from db.migrate import MIGRATIONS_DIR
from domain.scopes import DATA_TENANT_ID

_FILE = "0216_security_split_polygon_label.sql"
_SQL = (MIGRATIONS_DIR / _FILE).read_text(encoding="utf-8")
KEY = date(2026, 11, 20)


def _security(cur) -> uuid.UUID:
    sid = uuid.uuid4()
    cur.execute(
        "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
        (sid, DATA_TENANT_ID, "SNDK", date(2025, 1, 1)),
    )
    return sid


def _split(cur, sid: uuid.UUID, **over) -> None:
    row = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "key_date": KEY,
        "effective_date": KEY,
        "ratio_from": 1,
        "ratio_to": 8,
        "status": "confirmed",
        "confirmed_via": "auto:sec+polygon",
        "value_source": "both",
        "action": "detect",
        "author": "system:detect_splits",
        "polygon_id": "E9e78f587e5015847f15ea87764e1cc37c555cae9c9008a5b9c5d5c8604c72bd3",
        "polygon_ticker": "SNDK",
        "polygon_execution_date": KEY,
        "polygon_split_from": 1,
        "polygon_split_to": 8,
        "valid_from": KEY,
        **over,
    }
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    cur.execute(f"INSERT INTO security_split ({cols}) VALUES ({marks})", tuple(row.values()))


def _checksum(cur) -> str:
    cur.execute(
        "SELECT count(*)::text || md5(coalesce(string_agg(s::text, '|' ORDER BY s::text), '')) AS h "
        "FROM security_split s"
    )
    return cur.fetchone()["h"]


def _columns(cur) -> list[dict]:
    cur.execute(
        "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
        "WHERE table_schema = current_schema() AND table_name = 'security_split' "
        "AND column_name = 'polygon_adjustment_type'"
    )
    return cur.fetchall()


def test_0216_is_applied_to_the_test_database(db):
    with db.cursor() as cur:
        assert _columns(cur) == [
            {"column_name": "polygon_adjustment_type", "data_type": "text", "is_nullable": "YES"}
        ]
        cur.execute(
            "SELECT count(*) AS n FROM pg_constraint WHERE conrelid = 'security_split'::regclass "
            "AND contype = 'c' AND pg_get_constraintdef(oid) LIKE '%%polygon_adjustment_type%%'"
        )
        assert cur.fetchone()["n"] == 0  # no CHECK: a new vendor word never fails an insert
        cur.execute("SELECT count(*) AS n FROM schema_migrations WHERE filename = %s", (_FILE,))
        assert cur.fetchone()["n"] == 1
    db.rollback()


def test_0216_takes_any_word_or_none(db):
    try:
        with db.cursor() as cur:
            for i, label in enumerate(
                ("forward_split", "stock_dividend", "a_word_never_served", None)
            ):
                _split(
                    cur,
                    _security(cur),
                    key_date=date(2026, 11, 20 - i),
                    polygon_adjustment_type=label,
                )
            cur.execute(
                "SELECT polygon_adjustment_type AS t FROM security_split ORDER BY key_date DESC"
            )
            assert [r["t"] for r in cur.fetchall()] == [
                "forward_split",
                "stock_dividend",
                "a_word_never_served",
                None,
            ]
    finally:
        db.rollback()


def test_0216_over_the_old_shape_keeps_every_row_and_reruns_as_a_no_op(db):
    try:
        with db.cursor() as cur:
            # the pre-0216 shape: no label column
            cur.execute("ALTER TABLE security_split DROP COLUMN polygon_adjustment_type")
            assert _columns(cur) == []
            _split(cur, _security(cur))

            cur.execute(_SQL)
            cur.execute("SELECT polygon_split_to, polygon_adjustment_type FROM security_split")
            assert cur.fetchall() == [{"polygon_split_to": 8, "polygon_adjustment_type": None}]
            before = _checksum(cur)
            cur.execute(
                _SQL
            )  # a second run: the runner never makes one, and it would change nothing
            assert _checksum(cur) == before and len(_columns(cur)) == 1
    finally:
        db.rollback()
