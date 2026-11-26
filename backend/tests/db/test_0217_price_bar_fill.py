"""Migration 0217 — ``price_bar_fill``, the provenance of a price bar Massive.com filled (platform fixes W6-3b).

On the migrated test database: the table, its columns and the ``source`` CHECK (only ``massive``), the UNIQUE that
ties one row to one ``fact_price_eod`` version, and the cascade from the master. Then (the 0215 precedent: the runner
applies each file ONCE, so the test re-executes the FILE's SQL) a second run over a stored row changes nothing.
Rolled back at the end. DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import psycopg
import pytest

from db.migrate import MIGRATIONS_DIR
from domain.scopes import DATA_TENANT_ID

_FILE = "0217_price_bar_fill.sql"
_SQL = (MIGRATIONS_DIR / _FILE).read_text(encoding="utf-8")
AT = datetime(2026, 11, 21, 3, 31, tzinfo=timezone.utc)


def _security(cur) -> uuid.UUID:
    sid = uuid.uuid4()
    cur.execute(
        "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
        (sid, DATA_TENANT_ID, "SNDK", date(2025, 1, 1)),
    )
    return sid


def _fill(cur, sid: uuid.UUID, **over) -> None:
    row = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "d": date(2026, 11, 20),
        "bar_recorded_at": AT,
        "source": "massive",
        "vendor_symbol": "SNDK",
        "raw_close": "79.24",
        "raw_volume": "127860864.911014",
        "factor": 1,
        "reason": "split_window",
        **over,
    }
    cols = ", ".join(row)
    marks = ", ".join(["%s"] * len(row))
    cur.execute(f"INSERT INTO price_bar_fill ({cols}) VALUES ({marks})", tuple(row.values()))


def _checksum(cur) -> str:
    cur.execute(
        "SELECT count(*)::text || md5(coalesce(string_agg(f::text, '|' ORDER BY f::text), '')) AS h "
        "FROM price_bar_fill f"
    )
    return cur.fetchone()["h"]


def test_0217_is_applied_to_the_test_database(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT column_name, data_type, is_nullable FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'price_bar_fill' ORDER BY column_name"
        )
        cols = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in cur.fetchall()}
        assert cols == {
            "id": ("uuid", "NO"),
            "tenant_id": ("uuid", "NO"),
            "security_id": ("uuid", "NO"),
            "d": ("date", "NO"),
            "bar_recorded_at": ("timestamp with time zone", "NO"),
            "source": ("text", "NO"),
            "vendor_symbol": ("text", "NO"),
            "raw_close": ("numeric", "YES"),
            "raw_volume": ("numeric", "YES"),
            "factor": ("numeric", "NO"),
            "reason": ("text", "NO"),
            "created_at": ("timestamp with time zone", "NO"),
        }
        cur.execute("SELECT count(*) AS n FROM schema_migrations WHERE filename = %s", (_FILE,))
        assert cur.fetchone()["n"] == 1


@pytest.mark.parametrize(
    "over,error",
    [
        ({}, None),
        ({"source": "yahoo"}, psycopg.errors.CheckViolation),  # Massive.com is the only filler
        ({"factor": None}, psycopg.errors.NotNullViolation),
        ({"bar_recorded_at": None}, psycopg.errors.NotNullViolation),
        ({"reason": None}, psycopg.errors.NotNullViolation),
    ],
)
def test_0217_checks(db, over, error):
    try:
        with db.cursor() as cur:
            sid = _security(cur)
            if error is None:
                _fill(cur, sid, **over)
            else:
                with pytest.raises(error):
                    _fill(cur, sid, **over)
    finally:
        db.rollback()


def test_0217_one_row_per_bar_version_and_the_master_delete_cascades(db):
    try:
        with db.cursor() as cur:
            sid = _security(cur)
            _fill(cur, sid)
            _fill(cur, sid, bar_recorded_at=datetime(2026, 11, 24, 3, 31, tzinfo=timezone.utc))
            with pytest.raises(psycopg.errors.UniqueViolation):
                _fill(
                    cur, sid
                )  # the same (tenant, security, d, bar_recorded_at): one bar version, one row
    finally:
        db.rollback()
    try:
        with db.cursor() as cur:
            sid = _security(cur)
            _fill(cur, sid)
            cur.execute("DELETE FROM security_master WHERE id = %s", (sid,))
            cur.execute("SELECT count(*) AS n FROM price_bar_fill WHERE security_id = %s", (sid,))
            assert cur.fetchone()["n"] == 0
    finally:
        db.rollback()


def test_0217_reruns_as_a_no_op_over_a_stored_row(db):
    try:
        with db.cursor() as cur:
            sid = _security(cur)
            _fill(cur, sid)
            before = _checksum(cur)
            cur.execute(
                _SQL
            )  # a second run: the runner never makes one, and it would change nothing
            assert _checksum(cur) == before
    finally:
        db.rollback()
