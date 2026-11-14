"""The two tenant scopes (``domain.scopes``, docs/TENANT_SCOPES.md) — the map, the migration, the boot assert and
the union / ignore rules every read follows.

The rules, each proven against REAL rows with two workspaces that are NOT the data tenant (``…0001``): with the
data tenant equal to a user's workspace a missed scope filter would read the right rows by accident, so no
workspace here is ``…0001``.

- a platform table (share counts, cash/burn, company catalysts: DATA since V2) reads the data tenant only, never
  a row stored under a workspace; purity (``fact_revenue_mix``: WORKSPACE since V2) reads the workspace only;
- a DATA-table read ignores a row stored under a workspace;
- a WORKSPACE-table read ignores a row stored under the data tenant;
- workspace V sees none of W's rows;
- ``fact_exists`` honors the same lists;
- the replay mirror (export + reader) applies the same map.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable).
"""

from __future__ import annotations

import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from db.bitemporal import append_fact, as_of_many, fact_exists
from db.session import DEFAULT_TENANT_ID
from domain.enums import CatalystType, Grade
from domain.scopes import (
    DATA_TENANT_ID,
    SCOPE_OF,
    DataTenantMissing,
    Scope,
    as_tenants,
    assert_data_tenant,
    resolve_scopes,
    tenants_for,
)
from domain.thesis import BasketMember, Thesis
from ingest.revenue_mix import ingest_revenue_mix
from repositories import thesis_repo
from signals.base import PointInTimeData
from tests.workspaces import new_workspace

_MIGRATIONS = Path(__file__).resolve().parents[2] / "db" / "migrations"
_KNOWN = datetime(2100, 1, 1, tzinfo=timezone.utc)
_ASOF = date(2026, 6, 1)

# two user WORKSPACES, neither of them the data tenant (fixed ids: the `db` fixture keeps `tenant` rows, and
# new_workspace is ON CONFLICT DO NOTHING, so re-runs never accrete rows)
W = uuid.UUID("00000000-0000-0000-0000-0000000000a1")
V = uuid.UUID("00000000-0000-0000-0000-0000000000a2")


def _t(day: int) -> datetime:
    """An explicit, ordered ``recorded_at`` (one test transaction would otherwise stamp every row alike)."""
    return datetime(2026, 5, day, 12, tzinfo=timezone.utc)


# --- the map ----------------------------------------------------------------------------------------------


def _tenant_bearing_tables() -> set[str]:
    """Every table a migration creates WITH a ``tenant_id`` column, or adds one to."""
    out: set[str] = set()
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
        for m in re.finditer(
            r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)\s*\((.*?)\n\s*\)\s*;",
            sql,
            re.S | re.I,
        ):
            if re.search(r"\btenant_id\b", m.group(2)):
                out.add(m.group(1).lower())
        for m in re.finditer(
            r"ALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?(\w+)\s+ADD\s+COLUMN\s+"
            r"(?:IF\s+NOT\s+EXISTS\s+)?tenant_id\b",
            sql,
            re.I,
        ):
            out.add(m.group(1).lower())
    return out


def test_every_tenant_bearing_table_has_a_scope():
    """The map grows with the schema: a new table with a ``tenant_id`` column must be named in ``SCOPE_OF`` (and
    the map names no table the schema lacks)."""
    tables = _tenant_bearing_tables()
    assert len(tables) >= 50  # the parse found the schema (50 tables at 0076)
    assert tables - set(SCOPE_OF) == set(), "add these to domain.scopes.SCOPE_OF"
    assert set(SCOPE_OF) - tables == set(), "SCOPE_OF names tables no migration creates"


def test_the_v2_assignment():
    """V2 (W1a): two scopes, no MIXED. Share counts, cash/burn and company catalysts are platform data (DATA);
    purity is the workspace's own (WORKSPACE); the thesis spine and the record are WORKSPACE; the master and the
    ingested facts are DATA."""
    assert {s.name for s in Scope} == {"DATA", "WORKSPACE"}
    for t in ("fact_shares_outstanding", "fact_cash_burn", "fact_catalyst"):
        assert SCOPE_OF[t] is Scope.DATA, t
    assert SCOPE_OF["fact_revenue_mix"] is Scope.WORKSPACE
    for t in (
        "thesis",
        "basket_member",
        "calls",
        "operator_decision",
        "edition",
        "fact_term_mentions",
    ):
        assert SCOPE_OF[t] is Scope.WORKSPACE
    for t in ("security_master", "fact_price_eod", "fact_insider_txn", "security_onboarding"):
        assert SCOPE_OF[t] is Scope.DATA


def test_tenants_for_and_the_normalizer():
    assert tenants_for("fact_price_eod", data=DATA_TENANT_ID, workspace=W) == (DATA_TENANT_ID,)
    assert tenants_for("fact_theme_conviction", data=DATA_TENANT_ID, workspace=W) == (W,)
    assert tenants_for("fact_revenue_mix", data=DATA_TENANT_ID, workspace=W) == (W,)
    assert tenants_for("fact_shares_outstanding", data=DATA_TENANT_ID, workspace=W) == (
        DATA_TENANT_ID,
    )
    with pytest.raises(KeyError):
        tenants_for("no_such_table", data=DATA_TENANT_ID, workspace=W)
    assert as_tenants(W) == (W,)
    assert as_tenants([W, DATA_TENANT_ID, W]) == (W, DATA_TENANT_ID)
    with pytest.raises(ValueError):
        as_tenants([])
    # the workspace is REQUIRED (PR 2 removed the PR 1 `tenant_id=` transition alias); the data tenant defaults
    assert resolve_scopes(data_tenant=None, workspace=W) == (DATA_TENANT_ID, W)
    assert resolve_scopes(data_tenant=V, workspace=W) == (V, W)
    with pytest.raises(TypeError):
        resolve_scopes(data_tenant=None, workspace=None)  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        resolve_scopes(data_tenant=None, workspace=W, tenant_id=V)  # type: ignore[call-arg]


def test_tenants_for_names_one_tenant():
    """Every mapped table, read for a workspace that is not the data tenant, names exactly ONE tenant: no read
    unions two tenants any more (V2: MIXED is gone)."""
    for table in SCOPE_OF:
        got = tenants_for(table, data=DATA_TENANT_ID, workspace=W)
        assert len(got) == 1, (table, got)
        assert got == ((DATA_TENANT_ID,) if SCOPE_OF[table] is Scope.DATA else (W,)), table


def test_the_demo_default_is_the_data_tenant():
    """``DEFAULT_TENANT_ID`` (the demo/test default, retired in PR 2) is the same row as the data tenant."""
    assert DEFAULT_TENANT_ID == DATA_TENANT_ID


# --- the migration + the boot assert -----------------------------------------------------------------------


def test_migration_0076_marks_the_one_data_tenant(db):
    with db.cursor() as cur:
        # 0076's notify_webhook column is gone since 0079 (the destination moved to user_meta)
        cur.execute("SELECT kind, name FROM tenant WHERE id = %s", (DATA_TENANT_ID,))
        row = cur.fetchone()
    assert row["kind"] == "data" and row["name"] == "platform"
    new_workspace(db, "a workspace", workspace_id=W)
    with db.cursor() as cur:
        cur.execute("SELECT kind FROM tenant WHERE id = %s", (W,))
        assert cur.fetchone()["kind"] == "workspace"  # the default: a new tenant is a workspace
        # exactly one data tenant: a second kind='data' row is refused by the partial unique index
        with pytest.raises(Exception, match="tenant_one_data_kind"):
            with db.transaction():
                cur.execute(
                    "INSERT INTO tenant (id, name, kind) VALUES (%s, 'x', 'data')", (uuid.uuid4(),)
                )
        with pytest.raises(Exception, match="tenant_kind_check"):
            with db.transaction():
                cur.execute(
                    "INSERT INTO tenant (id, name, kind) VALUES (%s, 'x', 'other')", (uuid.uuid4(),)
                )
    assert_data_tenant(db)  # the migrated database passes


def test_the_boot_assert_refuses_without_a_data_tenant(db):
    """``assert_data_tenant`` (the API lifespan + the nightly) raises a NAMED error — never a silent default —
    when the data tenant is not ``kind='data'``."""
    with db.cursor() as cur:
        cur.execute("UPDATE tenant SET kind = 'workspace' WHERE id = %s", (DATA_TENANT_ID,))
    with pytest.raises(DataTenantMissing, match="not 'data'"):
        assert_data_tenant(db)
    db.rollback()  # the fixture's TRUNCATE committed already; this undoes only the kind flip
    assert_data_tenant(db)


# --- the read rules ----------------------------------------------------------------------------------------


@pytest.fixture
def two_workspaces(db):
    """W and V provisioned; one company in the DATA master; a W thesis on it."""
    new_workspace(db, "workspace W", workspace_id=W)
    new_workspace(db, "workspace V", workspace_id=V)
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, 'SCOPE', '0000000077', %s)",
            (sid, DATA_TENANT_ID, date(2026, 1, 1)),
        )
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=W,
        name="W's card",
        narrative="n",
        basket=[BasketMember(ticker="SCOPE", role="r", security_id=sid)],
    )
    thesis_repo.upsert(db, thesis)
    return sid, thesis


def _mix(db, sid, tenant, ref, pct, day):
    ingest_revenue_mix(
        db,
        sid,
        segment_label="core",
        mix_pct=pct,
        source="ratified" if tenant != DATA_TENANT_ID else "10-k-segment",
        source_ref=ref,
        event_date=date(2026, 1, 1),
        ratified_by="operator" if tenant != DATA_TENANT_ID else None,
        tenant_id=tenant,
        recorded_at=_t(day),
    )


def _cat(db, sid, tenant, ref, label, day):
    """A company catalyst row. Raw ``append_fact``: a row under a workspace is a POISON row (no V2 writer puts
    one there; ``ingest_catalyst`` refuses it), planted to prove no read reaches it."""
    append_fact(
        db,
        "fact_catalyst",
        {
            "tenant_id": tenant,
            "security_id": sid,
            "catalyst_type": CatalystType.CONTRACT.value,
            "grade": Grade.CORE.value,
            "label": label,
            "source": "ratified" if tenant != DATA_TENANT_ID else "8-k",
            "source_ref": ref,
            "ratified_by": "operator" if tenant != DATA_TENANT_ID else None,
            "valid_from": date(2026, 5, 1),
            "recorded_at": _t(day),
        },
    )


def _shares(db, sid, tenant, ref, shares, day):
    """A share-count row (raw ``append_fact``; under a workspace it is a poison row, as ``_cat``)."""
    append_fact(
        db,
        "fact_shares_outstanding",
        {
            "tenant_id": tenant,
            "security_id": sid,
            "shares": shares,
            "source": "10-q-cover",
            "source_ref": ref,
            "ratified_by": "operator" if tenant != DATA_TENANT_ID else "auto",
            "valid_from": date(2026, 5, 1),
            "recorded_at": _t(day),
        },
    )


def _cash(db, sid, tenant, ref, cash, day):
    """A cash/burn row (raw ``append_fact``; under a workspace it is a poison row, as ``_cat``)."""
    append_fact(
        db,
        "fact_cash_burn",
        {
            "tenant_id": tenant,
            "security_id": sid,
            "cash_usd": cash,
            "quarterly_burn_usd": 1_000_000,
            "source": "10-q",
            "source_ref": ref,
            "ratified_by": "operator",
            "valid_from": date(2026, 3, 31),
            "recorded_at": _t(day),
        },
    )


def _cat_by_ref(rows):
    return {r["source_ref"]: r["label"] for r in rows}


def _pit(db, workspace, **kw):
    return PointInTimeData(
        db, asof=_ASOF, known_at=_KNOWN, data_tenant=DATA_TENANT_ID, workspace=workspace, **kw
    )


def _mix_by_ref(rows):
    return {r["source_ref"]: float(r["mix_pct"]) for r in rows}


_PLATFORM = (
    ("fact_shares_outstanding", "shares_outstanding_facts", "shares"),
    ("fact_cash_burn", "cash_burn_facts", "cash_usd"),
    ("fact_catalyst", "catalyst_facts", "label"),
)


def _platform_rows(db, sid):
    """The SAME fact (one ``source_ref``) under the data tenant AND, later, under W (a poison row a V1 ratify
    would have written), plus a fact only W holds. Returns what the data tenant's read must give."""
    _shares(db, sid, DATA_TENANT_ID, "SH-1", 100, 1)
    _shares(db, sid, W, "SH-1", 999, 2)
    _shares(db, sid, W, "SH-W", 7, 3)
    _cash(db, sid, DATA_TENANT_ID, "CB-1", 500, 1)
    _cash(db, sid, W, "CB-1", 999, 2)
    _cash(db, sid, W, "CB-W", 7, 3)
    _cat(db, sid, DATA_TENANT_ID, "CAT-1", "as the platform holds it", 1)
    _cat(db, sid, W, "CAT-1", "as W once ratified it", 2)
    _cat(db, sid, W, "CAT-W", "W only", 3)
    return {
        "fact_shares_outstanding": {"SH-1": 100.0},
        "fact_cash_burn": {"CB-1": 500.0},
        "fact_catalyst": {"CAT-1": "as the platform holds it"},
    }


def _by_ref(rows, col):
    return {r["source_ref"]: (r[col] if col == "label" else float(r[col])) for r in rows}


def test_platform_facts_read_only_the_data_tenant(db, two_workspaces):
    """Share counts, cash/burn and company catalysts are platform data: every workspace reads the data tenant's
    rows and NOTHING stored under a workspace, not even its own (no fallback, no union) — on the per-security
    read, the basket prefetch, the public clock and ``as_of_many`` over the map's tenants."""
    sid, _ = two_workspaces
    want = _platform_rows(db, sid)
    for ws in (W, V):
        for table, accessor, col in _PLATFORM:
            for kw in ({}, {"basket": [sid]}, {"clock": "public"}):
                got = _by_ref(getattr(_pit(db, ws, **kw), accessor)(sid), col)
                assert got == want[table], (ws, table, kw)
            many = as_of_many(
                db,
                table,
                security_ids=[sid],
                asof=_ASOF,
                known_at=_KNOWN,
                tenant_id=tenants_for(table, data=DATA_TENANT_ID, workspace=ws),
            )
            assert _by_ref(many[sid], col) == want[table], (ws, table)


def test_purity_reads_only_the_workspace(db, two_workspaces):
    """Purity is the workspace's own: a purity row under the data tenant is invisible to every workspace, and
    W's purity is W's only."""
    sid, _ = two_workspaces
    _mix(db, sid, DATA_TENANT_ID, "FEED-1", 40, 1)  # a data-tenant purity row (a V1 CLI default)
    _mix(db, sid, W, "W-ONLY", 10, 3)
    for kw in ({}, {"basket": [sid]}, {"clock": "public"}):
        assert _mix_by_ref(_pit(db, W, **kw).revenue_mix_facts(sid)) == {"W-ONLY": 10.0}
        assert _mix_by_ref(_pit(db, V, **kw).revenue_mix_facts(sid)) == {}
    many = as_of_many(
        db,
        "fact_revenue_mix",
        security_ids=[sid],
        asof=_ASOF,
        known_at=_KNOWN,
        tenant_id=tenants_for("fact_revenue_mix", data=DATA_TENANT_ID, workspace=V),
    )
    assert _mix_by_ref(many.get(sid, [])) == {}


def test_a_data_read_ignores_a_row_stored_under_a_workspace(db, two_workspaces):
    sid, _ = two_workspaces
    for tenant, acc in ((DATA_TENANT_ID, "DATA-F4"), (W, "W-STRAY-F4")):
        append_fact(
            db,
            "fact_insider_txn",
            {
                "tenant_id": tenant,
                "security_id": sid,
                "accession": acc,
                "insider_name": "An Insider",
                "txn_code": "P",
                "valid_from": date(2026, 5, 20),
            },
        )
    for ws in (W, V):
        for basket in (None, [sid]):
            assert [r["accession"] for r in _pit(db, ws, basket=basket).insider_txns(sid)] == [
                "DATA-F4"
            ]


def test_a_workspace_read_ignores_a_row_stored_under_the_data_tenant(db, two_workspaces):
    _, thesis = two_workspaces
    for tenant, ref in ((DATA_TENANT_ID, "DATA-STRAY-THEME"), (W, "W-THEME")):
        append_fact(
            db,
            "fact_theme_conviction",
            {
                "tenant_id": tenant,
                "thesis_id": thesis.id,
                "grade": "flip",
                "label": "a theme conviction",
                "source": "ratified",
                "source_ref": ref,
                "valid_from": date(2026, 5, 1),
            },
        )
    assert [r["source_ref"] for r in _pit(db, W).theme_conviction_facts(thesis.id)] == ["W-THEME"]
    assert _pit(db, V).theme_conviction_facts(thesis.id) == []  # V sees none of W's rows


def test_fact_exists_honors_the_same_lists(db, two_workspaces):
    sid, _ = two_workspaces
    _mix(db, sid, W, "W-ONLY", 10, 3)
    assert fact_exists(
        db, "fact_revenue_mix", security_id=sid, tenant_id=[DATA_TENANT_ID, W], source_ref="W-ONLY"
    )
    assert not fact_exists(
        db, "fact_revenue_mix", security_id=sid, tenant_id=[DATA_TENANT_ID, V], source_ref="W-ONLY"
    )
    assert not fact_exists(
        db, "fact_revenue_mix", security_id=sid, tenant_id=DATA_TENANT_ID, source_ref="W-ONLY"
    )


def test_the_memo_never_crosses_a_scope(db, two_workspaces):
    """One PIT reads a WORKSPACE table for ``(W,)``; the memo key carries that one-tenant tuple, so a later read
    of the SAME security on a DATA table is its own entry under ``(DATA,)`` — never a reuse."""
    sid, _ = two_workspaces
    _mix(db, sid, W, "W-ONLY", 10, 3)
    pit = _pit(db, W)
    assert _mix_by_ref(pit.revenue_mix_facts(sid)) == {"W-ONLY": 10.0}
    keys = set(pit._memo)
    assert ("fact_revenue_mix", (W,), sid) in keys
    pit.insider_txns(sid)
    assert ("fact_insider_txn", (DATA_TENANT_ID,), sid) in set(pit._memo)


def test_the_replay_mirror_applies_the_same_map(db, two_workspaces, tmp_path):
    """The export writes each table for ``tenants_for(table, …)`` and the mirror reader picks per table — so
    W's mirror reads exactly what W's live PIT reads: a company catalyst stored under W reaches NO mirror (not
    even W's), and the data tenant's catalyst reaches both, on the record and the public clock.
    """
    pytest.importorskip("duckdb")
    from replay.export import export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror

    sid, _ = two_workspaces
    # fact_catalyst is the platform table the call path reads; W's rows are poison rows (no V2 writer)
    _cat(db, sid, DATA_TENANT_ID, "CAT-FEED", "as the feed wrote it", 1)
    _cat(db, sid, W, "CAT-FEED", "as W ratified it", 2)
    _cat(db, sid, W, "CAT-W", "W only", 3)
    append_fact(
        db,
        "fact_insider_txn",
        {
            "tenant_id": W,  # a stray under a workspace: never exported for a DATA table
            "security_id": sid,
            "accession": "W-STRAY-F4",
            "insider_name": "An Insider",
            "txn_code": "P",
            "valid_from": date(2026, 5, 20),
        },
    )
    db.commit()
    want = {"CAT-FEED": "as the feed wrote it"}
    for ws in (W, V):
        # the live PIT reads what the mirror must reproduce
        assert _cat_by_ref(_pit(db, ws).catalyst_facts(sid)) == want
        for clock in ("record", "public"):
            out = tmp_path / f"{ws}-{clock}"
            manifest = export_snapshot(
                db, out, data_tenant=DATA_TENANT_ID, workspace=ws, clock=clock
            )
            assert manifest["tenant_id"] == str(ws) and manifest["data_tenant"] == str(
                DATA_TENANT_ID
            )
            con = connect_mirror(out)
            try:
                for basket in (None, [sid]):
                    pit = ReplayPointInTimeData(
                        con,
                        asof=_ASOF,
                        known_at=_KNOWN,
                        data_tenant=DATA_TENANT_ID,
                        workspace=ws,
                        basket=basket,
                    )
                    assert _cat_by_ref(pit.catalyst_facts(sid)) == want
                    assert pit.insider_txns(sid) == []
            finally:
                con.close()


# --- V2 W0a: one owner per table (domain.scopes.UNSCOPED_TABLES + owner_of) ---------------------------------
# Additive block (V2 PR 1). The cutover dry run (pipeline.v2_cutover) fails closed on any table this map does not
# name, so the map must name EVERY table, not only the tenant-bearing ones.


def _every_created_table() -> set[str]:
    """Every table a migration creates (temporary tables aside), plus ``schema_migrations`` (``db/migrate.py``
    creates it, not a migration)."""
    out = {"schema_migrations"}
    for path in sorted(_MIGRATIONS.glob("*.sql")):
        sql = re.sub(r"--[^\n]*", "", path.read_text(encoding="utf-8"))
        for m in re.finditer(
            r"CREATE\s+(TEMP\s+|TEMPORARY\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", sql, re.I
        ):
            if not m.group(1):
                out.add(m.group(2).lower())
    return out


def test_every_table_has_one_owner():
    """Each table is in exactly one of ``SCOPE_OF`` (it carries ``tenant_id``) or ``UNSCOPED_TABLES`` (it does
    not), and together they name every table the schema has."""
    from domain.scopes import UNSCOPED_TABLES

    tables = _every_created_table()
    # the parse found the schema (66 tables at 0084, plus schema_migrations)
    assert len(tables) >= 67
    assert set(SCOPE_OF) & set(UNSCOPED_TABLES) == set(), "a table has two owners"
    named = set(SCOPE_OF) | set(UNSCOPED_TABLES)
    assert tables - named == set(), "name these in domain.scopes (SCOPE_OF or UNSCOPED_TABLES)"
    assert named - tables == set(), "domain.scopes names tables no migration creates"
    assert set(UNSCOPED_TABLES.values()) <= {"platform", "user-keyed"}


def test_owner_of_names_the_file_to_edit():
    from domain.scopes import owner_of

    with pytest.raises(KeyError, match=r"domain\.scopes"):
        owner_of("no_such")
    assert owner_of("fact_price_eod") == "platform"
    assert owner_of("thesis") == "user"
    assert owner_of("tenant") == "platform"
    assert owner_of("app_user") == "user-keyed"


# --- end V2 W0a block ------------------------------------------------------------------------------------
