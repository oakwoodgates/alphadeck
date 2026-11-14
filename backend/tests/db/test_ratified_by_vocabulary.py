"""V2 W2a: the ``ratified_by`` vocabulary (migration 0206) on ``fact_shares_outstanding`` and ``fact_cash_burn``.

Who set a platform value is one of: ``auto`` (the machine applied a clean filing value), ``operator`` (a V1 row
carried over), a steward's email, or NULL (a legacy row). Any other text is refused on every NEW row. The check is
added NOT VALID, so the rows already stored are not judged: a database holding V1 rows (``auto`` / ``operator``,
or anything a shell once typed) migrates cleanly, and W10 validates the constraint after the cutover dry run.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). Every write is rolled back.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest
from psycopg import errors

from db.bitemporal import append_fact
from db.migrate import MIGRATIONS_DIR
from domain.scopes import DATA_TENANT_ID

_TABLES = ("fact_shares_outstanding", "fact_cash_burn")
_MIGRATION = MIGRATIONS_DIR / "0206_v2_ratified_by_vocabulary.sql"


def _security(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, "VOC", "0000000099", date(2026, 1, 1)),
        )
    return sid


def _fact(db, table: str, sid: uuid.UUID, ratified_by: str | None) -> uuid.UUID:
    values = {
        "tenant_id": DATA_TENANT_ID,
        "security_id": sid,
        "source": "cover",
        "source_ref": f"https://www.sec.gov/x/{uuid.uuid4().hex}.htm",
        "valid_from": date(2026, 3, 31),
        "ratified_by": ratified_by,
    }
    if table == "fact_shares_outstanding":
        values["shares"] = 1_000_000
    else:
        values |= {"cash_usd": 5_000_000, "quarterly_burn_usd": 1_000_000}
    return append_fact(db, table, values)


@pytest.mark.parametrize("table", _TABLES)
@pytest.mark.parametrize("who", ["auto", "operator", "s@x.test", None])
def test_the_vocabulary_inserts(db, table, who):
    _fact(db, table, _security(db), who)


@pytest.mark.parametrize("table", _TABLES)
@pytest.mark.parametrize("who", ["jane", "", "Operator", "@x.test", "a b@x.test", "s@x@y"])
def test_anything_else_is_refused(db, table, who):
    sid = _security(db)
    with pytest.raises(errors.CheckViolation):
        with db.transaction():
            _fact(db, table, sid, who)


def test_a_database_holding_v1_rows_migrates_cleanly(db):
    """Drop the two constraints, store the V1 shapes (``auto``, ``operator`` and one odd text a shell once typed),
    then apply 0206: it succeeds, leaves every stored row as it was, and refuses the odd text on a NEW row.
    """
    sid = _security(db)
    with db.cursor() as cur:
        for table in _TABLES:
            cur.execute(
                f"ALTER TABLE {table} DROP CONSTRAINT {table}_ratified_by_vocabulary"
            )  # noqa: S608
    stored = [_fact(db, t, sid, who) for t in _TABLES for who in ("auto", "operator", "jane")]
    with db.cursor() as cur:
        cur.execute(_MIGRATION.read_text(encoding="utf-8"))
        cur.execute(_MIGRATION.read_text(encoding="utf-8"))  # and again: a no-op
        cur.execute(
            "SELECT conrelid::regclass::text AS t, convalidated FROM pg_constraint "
            "WHERE conname LIKE '%%ratified_by_vocabulary' ORDER BY 1"
        )
        assert [(r["t"], r["convalidated"]) for r in cur.fetchall()] == [
            ("fact_cash_burn", False),
            ("fact_shares_outstanding", False),
        ]
        cur.execute(
            "SELECT count(*) AS n FROM (SELECT id FROM fact_shares_outstanding UNION ALL "
            "SELECT id FROM fact_cash_burn) f WHERE id = ANY(%s)",
            (stored,),
        )
        assert cur.fetchone()["n"] == 6
    for table in _TABLES:
        with pytest.raises(errors.CheckViolation):
            with db.transaction():
                _fact(db, table, sid, "jane")
