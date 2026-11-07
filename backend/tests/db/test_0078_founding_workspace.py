"""Migration 0078 — the founding workspace (auth PR 2, docs/TENANT_SCOPES.md).

On the migrated test database: rows in the OLD shape (everything under the data tenant ``...0001``: a thesis, its
members, a roster snapshot, a call of record, an operator decision, a RATIFIED ``fact_revenue_mix`` row, a FEED
``fact_catalyst`` row and a user), then 0078's SQL executed directly (the runner skips an applied file). After it:

- every workspace row and the ratified row sit under the founding workspace ``...0002``;
- the feed row stays under ``...0001`` (DATA);
- the user sits on ``...0002``;
- a second execution changes nothing and its post-conditions still pass;
- the ``app_user`` trigger refuses a user on ``...0001``;
- the SQL's table list equals ``SCOPE_OF``'s WORKSPACE + MIXED tables, and every append-only trigger on those
  tables is disabled and re-enabled by name.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). The test's transaction is rolled back.
"""

from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path

import psycopg
import pytest

from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID, SCOPE_OF, Scope

_SQL_PATH = (
    Path(__file__).resolve().parents[2] / "db" / "migrations" / "0078_founding_workspace.sql"
)
_SQL = _SQL_PATH.read_text(encoding="utf-8")

_WORKSPACE_TABLES = sorted(t for t, s in SCOPE_OF.items() if s is Scope.WORKSPACE)
_MIXED_TABLES = sorted(t for t, s in SCOPE_OF.items() if s is Scope.MIXED)


def _count(
    cur, table: str, tenant: uuid.UUID, *, column: str = "tenant_id", extra: str = ""
) -> int:
    cur.execute(f"SELECT count(*) AS n FROM {table} WHERE {column} = %s {extra}", (tenant,))
    return cur.fetchone()["n"]


def _old_shape(db, security_id: uuid.UUID) -> dict[str, uuid.UUID]:
    """Write the pre-0078 shape: every row under the data tenant, the user too (the trigger off for that insert)."""
    ids = {"thesis": uuid.uuid4(), "user": uuid.uuid4()}
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (ids["thesis"], DATA_TENANT_ID, "Synthetic migration card", "a narrative"),
        )
        cur.execute(
            "INSERT INTO basket_member (tenant_id, thesis_id, ordinal, ticker, role, security_id) "
            "VALUES (%s, %s, 0, 'DEVCO', 'the name', %s)",
            (DATA_TENANT_ID, ids["thesis"], security_id),
        )
        cur.execute(
            "INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash) "
            "VALUES (gen_random_uuid(), %s, %s, now(), '[]'::jsonb, md5('[]'))",
            (DATA_TENANT_ID, ids["thesis"]),
        )
        cur.execute(
            "INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card) "
            "VALUES (%s, %s, %s, 'Incubating', 'none', '{}'::jsonb)",
            (DATA_TENANT_ID, ids["thesis"], date(2026, 6, 1)),
        )
        cur.execute(
            "INSERT INTO operator_decision (tenant_id, thesis_id, action, decision_date, reason) "
            "VALUES (%s, %s, 'pass', %s, 'not now')",
            (DATA_TENANT_ID, ids["thesis"], date(2026, 6, 1)),
        )
        cur.execute(
            "INSERT INTO fact_revenue_mix (tenant_id, security_id, segment_label, mix_pct, source, source_ref, "
            "ratified_by, valid_from) VALUES (%s, %s, 'core', 80, 'ratified', 'ref-ratified', 'operator', %s)",
            (DATA_TENANT_ID, security_id, date(2026, 1, 1)),
        )
        cur.execute(
            "INSERT INTO fact_catalyst (tenant_id, security_id, catalyst_type, grade, label, source, source_ref, "
            "ratified_by, valid_from) VALUES (%s, %s, 'contract', 'flip', 'a feed catalyst', '8-k', 'ref-feed', "
            "NULL, %s)",
            (DATA_TENANT_ID, security_id, date(2026, 1, 1)),
        )
        cur.execute("ALTER TABLE app_user DISABLE TRIGGER workspace_kind_check")
        cur.execute(
            "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id) "
            "VALUES (%s, %s, 'Old', 'x', 'super_admin', %s)",
            (ids["user"], f"old-{ids['user']}@example.test", DATA_TENANT_ID),
        )
        cur.execute("ALTER TABLE app_user ENABLE TRIGGER workspace_kind_check")
    return ids


def _run_0078(db) -> None:
    with db.cursor() as cur:
        cur.execute(_SQL)


def _snapshot(db) -> dict[str, tuple[int, int]]:
    """``table -> (rows under ...0001, rows under ...0002)`` for every table 0078 touches."""
    out: dict[str, tuple[int, int]] = {}
    with db.cursor() as cur:
        for t in _WORKSPACE_TABLES + _MIXED_TABLES:
            out[t] = (_count(cur, t, DATA_TENANT_ID), _count(cur, t, FOUNDING_WORKSPACE_ID))
        out["app_user"] = (
            _count(cur, "app_user", DATA_TENANT_ID, column="workspace_id"),
            _count(cur, "app_user", FOUNDING_WORKSPACE_ID, column="workspace_id"),
        )
    return out


def test_0078_moves_the_workspace_rows_and_the_ratified_row_and_keeps_the_feed_row(db, security_id):
    try:
        ids = _old_shape(db, security_id)
        _run_0078(db)
        with db.cursor() as cur:
            for table in (
                "thesis",
                "basket_member",
                "basket_snapshot",
                "calls",
                "operator_decision",
            ):
                assert _count(cur, table, DATA_TENANT_ID) == 0, table
                assert _count(cur, table, FOUNDING_WORKSPACE_ID) >= 1, table
            cur.execute("SELECT tenant_id FROM thesis WHERE id = %s", (ids["thesis"],))
            assert cur.fetchone()["tenant_id"] == FOUNDING_WORKSPACE_ID
            # the ratified row moved; the feed row stayed DATA
            cur.execute("SELECT tenant_id FROM fact_revenue_mix WHERE source_ref = 'ref-ratified'")
            assert cur.fetchone()["tenant_id"] == FOUNDING_WORKSPACE_ID
            cur.execute("SELECT tenant_id FROM fact_catalyst WHERE source_ref = 'ref-feed'")
            assert cur.fetchone()["tenant_id"] == DATA_TENANT_ID
            cur.execute("SELECT workspace_id FROM app_user WHERE id = %s", (ids["user"],))
            assert cur.fetchone()["workspace_id"] == FOUNDING_WORKSPACE_ID
            cur.execute("SELECT kind FROM tenant WHERE id = %s", (FOUNDING_WORKSPACE_ID,))
            assert cur.fetchone()["kind"] == "workspace"

        # a second run moves nothing and its post-conditions still pass
        before = _snapshot(db)
        _run_0078(db)
        assert _snapshot(db) == before

        # the append-only guard is back on after the move
        with db.cursor() as cur, pytest.raises(psycopg.errors.RaiseException):
            cur.execute("UPDATE calls SET state = 'Armed' WHERE thesis_id = %s", (ids["thesis"],))
    finally:
        db.rollback()


def test_the_app_user_trigger_refuses_a_user_on_the_data_tenant(db):
    try:
        with db.cursor() as cur:
            with pytest.raises(psycopg.errors.RaiseException, match="not a workspace tenant"):
                cur.execute(
                    "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id) "
                    "VALUES (%s, 'nope@example.test', 'Nope', 'x', 'user', %s)",
                    (uuid.uuid4(), DATA_TENANT_ID),
                )
        db.rollback()
        uid = uuid.uuid4()
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO app_user (id, email, display_name, password_hash, role, workspace_id) "
                "VALUES (%s, %s, 'Ok', 'x', 'user', %s)",
                (uid, f"ok-{uid}@example.test", FOUNDING_WORKSPACE_ID),
            )
            # an UPDATE onto the data tenant is refused too
            with pytest.raises(psycopg.errors.RaiseException, match="not a workspace tenant"):
                cur.execute(
                    "UPDATE app_user SET workspace_id = %s WHERE id = %s", (DATA_TENANT_ID, uid)
                )
    finally:
        db.rollback()


def test_the_sql_names_exactly_the_scope_maps_workspace_and_mixed_tables():
    moved_all = set(
        re.findall(
            r"^UPDATE (\w+) SET tenant_id = '00000000-0000-0000-0000-000000000002' "
            r"WHERE tenant_id = '00000000-0000-0000-0000-000000000001';$",
            _SQL,
            re.M,
        )
    )
    moved_ratified = set(
        re.findall(
            r"^UPDATE (\w+) SET tenant_id = '00000000-0000-0000-0000-000000000002' "
            r"WHERE tenant_id = '00000000-0000-0000-0000-000000000001' AND ratified_by IS NOT NULL;$",
            _SQL,
            re.M,
        )
    )
    assert moved_all == set(_WORKSPACE_TABLES)
    assert moved_ratified == set(_MIXED_TABLES)
    # the post-condition block checks the same lists
    post = _SQL[_SQL.index("-- 5. the post-conditions") :]
    for t in _WORKSPACE_TABLES + _MIXED_TABLES:
        assert f"'{t}'" in post, t


def test_every_update_trigger_on_the_moved_tables_is_disabled_and_reenabled_by_name(db):
    disabled = set(re.findall(r"^ALTER TABLE (\w+) DISABLE TRIGGER (\w+);$", _SQL, re.M))
    enabled = set(re.findall(r"^ALTER TABLE (\w+) ENABLE TRIGGER (\w+);$", _SQL, re.M))
    assert disabled == enabled
    with db.cursor() as cur:
        # tgtype bit 16 = UPDATE: every user trigger that would refuse the move's UPDATE
        cur.execute(
            "SELECT c.relname, t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE NOT t.tgisinternal AND (t.tgtype & 16) <> 0 AND c.relname = ANY(%s)",
            (_WORKSPACE_TABLES + _MIXED_TABLES,),
        )
        live = {(r["relname"], r["tgname"]) for r in cur.fetchall()}
    assert live == disabled
