"""The V2 cutover dry run (``python -m pipeline.v2_cutover plan``, V2 PR 1 / W0a).

Read-only tooling: it lists every table the database holds, who owns it (``domain.scopes``), what the cutover will
do with it, and every row that sits where no plan expects it. It fails closed (exit 2) on a table the ownership
registry does not name, and on a mapped table the database lacks.

The pure tests build an ``Inventory`` by hand; the DB tests read the per-worktree test database (the shared ``db``
fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone

import domain.scopes as scopes
from db import session
from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID, SCOPE_OF, UNSCOPED_TABLES
from pipeline import v2_cutover as C
from tests.workspaces import new_workspace

W = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
V = uuid.UUID("00000000-0000-0000-0000-0000000000c2")
STEWARD = uuid.UUID("5d0c1a2b-0000-4000-8000-00000c3f3f2a")


# --- pure: the format, the action list, the rules ----------------------------------------------------------


def _table(name, rows, by_tenant=None, by_ratified=None):
    return C.TableCount(name=name, rows=rows, by_tenant=by_tenant, by_ratified=by_ratified)


def _inventory(tables, tenants=None):
    return C.Inventory(
        database="alphadeck_v2",
        read_at=datetime(2026, 11, 14, 15, 2, 11, 512000, tzinfo=timezone.utc),
        migrations_applied=85,
        newest_migration="0084_user_action_count.sql",
        tenants=tuple(
            tenants
            or (
                C.TenantRow(DATA_TENANT_ID, "data", "platform", 0),
                C.TenantRow(FOUNDING_WORKSPACE_ID, "workspace", "founding workspace", 1),
                C.TenantRow(STEWARD, "workspace", "steward", 1),
            )
        ),
        tables={t.name: t for t in tables},
    )


_EXPECTED = """\
V2 CUTOVER - DRY RUN (read-only; nothing written)
target DB : alphadeck_v2
read at   : 2026-11-14T15:02:11Z (database clock)
schema    : 85 files applied, newest 0084_user_action_count.sql
tenants   : 00000000-0000-0000-0000-000000000001  data       "platform"             users 0
            00000000-0000-0000-0000-000000000002  workspace  "founding workspace"   users 1
            5d0c1a2b-0000-4000-8000-00000c3f3f2a  workspace  "steward"              users 1

TABLE                     OWNER       PLAN              ROWS  BY TENANT                BY ratified_by
app_user                  user-keyed  keep                 3  -                        -
calls                     user        reset            41210  …0002:41210              -
fact_theme_conviction     user        convert (W6/W10)    12  …0002:9 …3f2a:3          null:2 operator:10
security_change           platform    prune            90210  …0001:90210              -
security_master           platform    keep              9021  …0001:9021               -

SURPRISES (1)
  1. fact_catalyst: 3 row(s) under workspace …0002 with ratified_by set - V2 converts these to thesis
     catalysts (W6/W10); none were expected
UNMAPPED TABLES: none
RESULT: REFUSE - 1 surprise(s). Exit 1.
"""


def test_render_is_byte_stable():
    """The plan fixes the text (docs/temp W0/W1 plan, PR 1): a hand-built inventory renders exactly this. Tables
    whose owner V2 PR 3 changes are left out on purpose, so that PR does not touch this test."""
    inv = _inventory(
        [
            _table("security_master", 9021, {DATA_TENANT_ID: 9021}),
            _table("calls", 41210, {FOUNDING_WORKSPACE_ID: 41210}),
            _table("app_user", 3),
            _table(
                "fact_theme_conviction",
                12,
                {FOUNDING_WORKSPACE_ID: 9, STEWARD: 3},
                {
                    (FOUNDING_WORKSPACE_ID, "operator"): 9,
                    (STEWARD, None): 2,
                    (STEWARD, "operator"): 1,
                },
            ),
            _table("security_change", 90210, {DATA_TENANT_ID: 90210}),
        ]
    )
    surprise = C.Surprise(
        rule=6,
        table="fact_catalyst",
        count=3,
        text="fact_catalyst: 3 row(s) under workspace …0002 with ratified_by set - V2 converts these to "
        "thesis catalysts (W6/W10); none were expected",
    )
    assert C.render(inv, [surprise]) == _EXPECTED
    # with no wide cell, the columns are exactly the plan's example (its lines, byte for byte)
    narrow = C.render(
        _inventory(
            [
                _table("calls", 41210, {FOUNDING_WORKSPACE_ID: 41210}),
                _table("security_master", 9021, {DATA_TENANT_ID: 9021}),
                _table("app_user", 3),
            ]
        ),
        [],
    ).splitlines()
    assert narrow[narrow.index("") + 1 : narrow.index("") + 5] == [
        "TABLE                     OWNER       PLAN          ROWS  BY TENANT                BY ratified_by",
        "app_user                  user-keyed  keep             3  -                        -",
        "calls                     user        reset        41210  …0002:41210              -",
        "security_master           platform    keep          9021  …0001:9021               -",
    ]
    # the JSON carries the same data, and is plain JSON
    doc = C.to_json(inv, [surprise])
    assert json.loads(json.dumps(doc)) == doc
    assert doc["exit_code"] == 1 and doc["surprises"][0]["rule"] == 6
    calls = next(t for t in doc["tables"] if t["name"] == "calls")
    assert calls["plan"] == "reset" and calls["by_tenant"] == {str(FOUNDING_WORKSPACE_ID): 41210}


def test_every_action_names_a_mapped_table():
    assert set(C.CUTOVER_ACTION) <= set(SCOPE_OF) | set(UNSCOPED_TABLES)
    assert C.CUTOVER_ACTION["fact_shares_outstanding"] == "move→data"
    assert C.CUTOVER_ACTION["fact_cash_burn"] == "move→data"
    assert C.CUTOVER_ACTION["fact_revenue_mix"] == "move→founding"
    for t in ("calls", "call_annotation") + tuple(t for t in SCOPE_OF if t.startswith("edition")):
        assert C.CUTOVER_ACTION[t] == "reset"
    assert len([t for t, a in C.CUTOVER_ACTION.items() if a == "reset"]) == 8
    assert C.plan_of("security_master") == "keep"  # the default


def test_the_rules_never_name_the_mixed_scope():
    """V2 PR 3 deletes ``Scope.MIXED``; the dry run's rules name tables, so that PR never edits this module."""
    from pathlib import Path

    src = Path(C.__file__).read_text(encoding="utf-8")
    assert "MIXED" not in src


def _full(tables):
    """Every mapped table present (empty unless given) — a database with nothing missing."""
    given = {t.name: t for t in tables}
    out = []
    for name in sorted(set(SCOPE_OF) | set(UNSCOPED_TABLES)):
        out.append(given.get(name) or _table(name, 0, {} if name in SCOPE_OF else None))
    return out


def test_a_clean_inventory_is_exit_0_and_founding_shares_are_the_plan():
    inv = _inventory(
        _full(
            [
                _table(
                    "fact_shares_outstanding",
                    2,
                    {FOUNDING_WORKSPACE_ID: 2},
                    {(FOUNDING_WORKSPACE_ID, "auto"): 2},
                ),
                _table(
                    "fact_revenue_mix",
                    1,
                    {DATA_TENANT_ID: 1},
                    {(DATA_TENANT_ID, "operator"): 1},  # D2: data-tenant ratified purity moves
                ),
            ]
        )
    )
    assert C.surprises(inv) == []
    assert C.exit_code(C.surprises(inv)) == 0
    assert C.render(inv, []).endswith(
        "SURPRISES (0)\nUNMAPPED TABLES: none\nRESULT: CLEAN - no surprises. Exit 0.\n"
    )


def test_a_user_on_the_data_tenant_is_a_surprise():
    """The database refuses this row (0077's ``app_user_workspace_kind_check``), so only a hand-built inventory
    can show the rule; it stays as a second line of defense on a database whose trigger is off."""
    inv = _inventory(_full([]), tenants=(C.TenantRow(DATA_TENANT_ID, "data", "platform", 2),))
    assert [(s.rule, s.table, s.count, s.text) for s in C.surprises(inv)] == [
        (
            9,
            "app_user",
            2,
            "app_user: 2 user(s) whose workspace is the data tenant - a user's workspace is never the data "
            "tenant",
        )
    ]


def test_unmapped_and_missing_tables_are_fatal():
    tables = [t for t in _full([]) if t.name != "fact_dilution"]
    tables.append(_table("mystery_table", 4))
    sur = C.surprises(_inventory(tables))
    assert [(s.rule, s.table) for s in sur] == [(1, "mystery_table"), (2, "fact_dilution")]
    assert C.exit_code(sur) == 2
    text = C.render(_inventory(tables), sur)
    assert "UNMAPPED TABLES: mystery_table\n" in text
    assert re.search(r"^mystery_table +UNMAPPED +\? +4  -", text, re.M)
    assert text.endswith("RESULT: REFUSE - 2 unmapped or missing table(s). Exit 2.\n")


# --- DB: the real database ---------------------------------------------------------------------------------


def _empty_every_table(db) -> None:
    """Start from an empty database INSIDE the test's transaction (the fixture rolls it back): every scoped table
    and the user tables, so rows other tests committed cannot reach the exact surprise list. ``tenant`` and
    ``schema_migrations`` stay."""
    names = sorted((set(SCOPE_OF) | set(UNSCOPED_TABLES)) - {"tenant", "schema_migrations"})
    with db.cursor() as cur:
        cur.execute(f"TRUNCATE {', '.join(names)} CASCADE")


def _security(db, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, "0000000099", date(2026, 1, 1)),
        )
    return sid


def _shares(db, sid, tenant, ref, by):
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_shares_outstanding (tenant_id, security_id, shares, source, source_ref, ratified_by, "
            "valid_from) VALUES (%s, %s, 1000, 'cover', %s, %s, '2026-03-31')",
            (tenant, sid, ref, by),
        )


def test_each_surprise_is_named(db):
    _empty_every_table(db)
    new_workspace(db, "workspace W", workspace_id=W)
    new_workspace(db, "workspace V", workspace_id=V)
    sid = _security(db, "CUTV2")
    # the clean case first: the founding workspace's share counts are the plan, not a surprise
    _shares(db, sid, FOUNDING_WORKSPACE_ID, "FOUNDING-1", "auto")
    inv = C.inventory(db)
    assert C.surprises(inv) == [] and C.exit_code([]) == 0
    assert inv.tables["fact_shares_outstanding"].by_tenant == {FOUNDING_WORKSPACE_ID: 1}

    with db.cursor() as cur:
        # 3. a platform table's row under a workspace
        cur.execute(
            "INSERT INTO fact_insider_txn (tenant_id, security_id, accession, valid_from) "
            "VALUES (%s, %s, 'W-STRAY', '2026-05-01')",
            (W, sid),
        )
        # 9. + 4. a thesis under the data tenant, and its kill criterion (a workspace table) there too
        tid = uuid.uuid4()
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, 'stray', 'n')",
            (tid, DATA_TENANT_ID),
        )
        cur.execute(
            "INSERT INTO kill_criterion (id, tenant_id, thesis_id, text, ordinal) VALUES (%s, %s, %s, 'k', 0)",
            (uuid.uuid4(), DATA_TENANT_ID, tid),
        )
        # 6. a ratified company catalyst under the founding workspace; 7. a data-tenant one with a ratifier
        for tenant, ref in ((FOUNDING_WORKSPACE_ID, "CAT-F"), (DATA_TENANT_ID, "CAT-D")):
            cur.execute(
                "INSERT INTO fact_catalyst (tenant_id, security_id, catalyst_type, grade, label, source, "
                "source_ref, ratified_by, valid_from) VALUES (%s, %s, 'contract', 'core', 'l', 'ratified', %s, "
                "'operator', '2026-05-01')",
                (tenant, sid, ref),
            )
        # 8. a data-tenant purity row with no ratifier
        cur.execute(
            "INSERT INTO fact_revenue_mix (tenant_id, security_id, segment_label, mix_pct, source, source_ref, "
            "valid_from) VALUES (%s, %s, 'core', 40, '10-k', 'MIX-D', '2026-01-01')",
            (DATA_TENANT_ID, sid),
        )
        # (9's other half, a user on the data tenant, cannot be stored: 0077's trigger refuses it; the pure
        # test below covers the rule)
    # 5. a share count in a workspace that is not the founding one
    _shares(db, sid, W, "W-1", "operator")

    sur = C.surprises(C.inventory(db))
    assert [(s.rule, s.table, s.count) for s in sur] == [
        (3, "fact_insider_txn", 1),
        (4, "kill_criterion", 1),
        (5, "fact_shares_outstanding", 1),
        (6, "fact_catalyst", 1),
        (7, "fact_catalyst", 1),
        (8, "fact_revenue_mix", 1),
        (9, "thesis", 1),
    ]
    assert [s.text for s in sur] == [
        "fact_insider_txn: 1 row(s) under workspace …00c1 - a platform table's rows belong to the data tenant; "
        "V2 reads none of these",
        "kill_criterion: 1 row(s) under the data tenant - a workspace table's rows belong to a user's "
        "workspace; V2 reads none of these",
        "fact_shares_outstanding: 1 row(s) under workspace …00c1, not the founding workspace - the move "
        "carries only the founding workspace's rows (decide: move, leave unread, or drop)",
        "fact_catalyst: 1 row(s) under workspace …0002 with ratified_by set - V2 converts these to thesis "
        "catalysts (W6/W10); none were expected",
        "fact_catalyst: 1 row(s) under the data tenant with ratified_by set - a feed row carries no "
        "ratifier; no plan covers these",
        "fact_revenue_mix: 1 row(s) under the data tenant with ratified_by NULL - purity is read from the "
        "workspace only in V2 and no plan moves an unratified row",
        "thesis: 1 row(s) under the data tenant - a thesis belongs to a workspace",
    ]
    assert C.exit_code(sur) == 1


def test_an_unmapped_table_fails_closed(db, monkeypatch, capsys):
    """No DDL: the registry forgets ``fact_dilution``, so the live database holds a table nobody owns."""
    monkeypatch.setattr(
        scopes, "SCOPE_OF", {k: v for k, v in SCOPE_OF.items() if k != "fact_dilution"}
    )
    assert C.main(["plan"]) == 2
    out = capsys.readouterr().out
    assert "UNMAPPED TABLES: fact_dilution\n" in out
    assert "RESULT: REFUSE" in out and out.rstrip().endswith("Exit 2.")


def test_the_dry_run_writes_nothing(db, monkeypatch, capsys, tmp_path):
    def counts() -> dict[str, int]:
        with db.cursor() as cur:
            cur.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
            )
            names = [r["table_name"] for r in cur.fetchall()]
            out = {}
            for n in names:
                cur.execute(f'SELECT count(*) AS n FROM "{n}"')
                out[n] = cur.fetchone()["n"]
        db.rollback()
        return out

    opened: list = []
    real_connect = session.connect

    def spy():
        conn = real_connect()
        opened.append(conn)
        return conn

    monkeypatch.setattr(session, "connect", spy)
    before = counts()
    code = C.main(["plan", "--json", str(tmp_path / "plan.json")])
    assert code in (
        0,
        1,
    )  # whatever other tests committed; never a fatal on the migrated test database
    assert counts() == before
    assert len(opened) == 1 and opened[0].read_only is True and opened[0].closed
    out = capsys.readouterr().out
    assert out.startswith(
        "V2 CUTOVER - DRY RUN (read-only; nothing written)\ntarget DB : alphadeck_test"
    )
    doc = json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))
    assert doc["database"].startswith("alphadeck_test") and doc["exit_code"] == code
    assert {t["name"] for t in doc["tables"]} >= set(SCOPE_OF) | set(UNSCOPED_TABLES)
