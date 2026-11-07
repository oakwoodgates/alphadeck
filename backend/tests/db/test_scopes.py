"""The two tenant scopes (``domain.scopes``, docs/TENANT_SCOPES.md) — the map, the migration, the boot assert and
the union / ignore rules every read follows.

The rules, each proven against REAL rows with two workspaces that are NOT the data tenant (``…0001``): with the
data tenant equal to a user's workspace a missed scope filter would read the right rows by accident, so no
workspace here is ``…0001``.

- a MIXED-table read with ``(DATA, W)`` returns the data-scope row AND W's row, latest-wins across them;
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

from db.bitemporal import append_fact, as_of, as_of_many, fact_exists
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
from ingest.catalyst import ingest_catalyst
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


def test_the_map_assignment():
    """The plan's assignment (auth-multiuser plan §3.1): the four scoring-fact tables are MIXED; the thesis spine
    and the record are WORKSPACE; the master and the ingested facts are DATA."""
    mixed = {t for t, s in SCOPE_OF.items() if s is Scope.MIXED}
    assert mixed == {
        "fact_revenue_mix",
        "fact_cash_burn",
        "fact_shares_outstanding",
        "fact_catalyst",
    }
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
    assert tenants_for("fact_revenue_mix", data=DATA_TENANT_ID, workspace=W) == (DATA_TENANT_ID, W)
    # equal scopes (today's state, before PR 2) -> ONE id, so the read is byte-identical
    assert tenants_for("fact_revenue_mix", data=DATA_TENANT_ID, workspace=DATA_TENANT_ID) == (
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


def test_the_demo_default_is_the_data_tenant():
    """``DEFAULT_TENANT_ID`` (the demo/test default, retired in PR 2) is the same row as the data tenant."""
    assert DEFAULT_TENANT_ID == DATA_TENANT_ID


# --- the migration + the boot assert -----------------------------------------------------------------------


def test_migration_0076_marks_the_one_data_tenant(db):
    with db.cursor() as cur:
        cur.execute(
            "SELECT kind, name, notify_webhook FROM tenant WHERE id = %s", (DATA_TENANT_ID,)
        )
        row = cur.fetchone()
    assert row["kind"] == "data" and row["name"] == "platform" and row["notify_webhook"] is None
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
    ingest_catalyst(
        db,
        sid,
        catalyst_type=CatalystType.CONTRACT,
        grade=Grade.CORE,
        label=label,
        source="ratified" if tenant != DATA_TENANT_ID else "8-k",
        source_ref=ref,
        event_date=date(2026, 5, 1),
        ratified_by="operator" if tenant != DATA_TENANT_ID else None,
        tenant_id=tenant,
        recorded_at=_t(day),
    )


def _cat_by_ref(rows):
    return {r["source_ref"]: r["label"] for r in rows}


def _pit(db, workspace, **kw):
    return PointInTimeData(
        db, asof=_ASOF, known_at=_KNOWN, data_tenant=DATA_TENANT_ID, workspace=workspace, **kw
    )


def _mix_by_ref(rows):
    return {r["source_ref"]: float(r["mix_pct"]) for r in rows}


def test_a_mixed_read_unions_data_and_the_workspace_latest_wins(db, two_workspaces):
    sid, _ = two_workspaces
    _mix(db, sid, DATA_TENANT_ID, "FEED-1", 40, 1)  # a feed row (DATA)
    _mix(db, sid, W, "FEED-1", 60, 2)  # W ratifies the SAME fact later: overrides it for W only
    _mix(db, sid, W, "W-ONLY", 10, 3)  # a fact only W ratified
    for basket in (None, [sid]):  # the per-security read and the basket prefetch
        assert _mix_by_ref(_pit(db, W, basket=basket).revenue_mix_facts(sid)) == {
            "FEED-1": 60.0,
            "W-ONLY": 10.0,
        }
        # V reads the data row as the feed wrote it, and none of W's
        assert _mix_by_ref(_pit(db, V, basket=basket).revenue_mix_facts(sid)) == {"FEED-1": 40.0}
    # the accessor itself, one tenant or a list
    rows = as_of(
        db,
        "fact_revenue_mix",
        security_id=sid,
        asof=_ASOF,
        known_at=_KNOWN,
        tenant_id=[DATA_TENANT_ID, W],
    )
    assert _mix_by_ref(rows) == {"FEED-1": 60.0, "W-ONLY": 10.0}
    assert _mix_by_ref(
        as_of(
            db,
            "fact_revenue_mix",
            security_id=sid,
            asof=_ASOF,
            known_at=_KNOWN,
            tenant_id=DATA_TENANT_ID,
        )
    ) == {"FEED-1": 40.0}
    many = as_of_many(
        db,
        "fact_revenue_mix",
        security_ids=[sid],
        asof=_ASOF,
        known_at=_KNOWN,
        tenant_id=[DATA_TENANT_ID, V],
    )
    assert _mix_by_ref(many[sid]) == {"FEED-1": 40.0}
    # the public clock (a history view) resolves the union the same way
    assert _mix_by_ref(_pit(db, W, clock="public").revenue_mix_facts(sid)) == {
        "FEED-1": 60.0,
        "W-ONLY": 10.0,
    }


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
    """One PIT reads a MIXED table for (DATA, W); the memo key carries that tenant tuple, so a later read of
    the SAME security on another table (DATA only) is its own entry — never a reuse of the union."""
    sid, _ = two_workspaces
    _mix(db, sid, W, "W-ONLY", 10, 3)
    pit = _pit(db, W)
    assert _mix_by_ref(pit.revenue_mix_facts(sid)) == {"W-ONLY": 10.0}
    keys = set(pit._memo)
    assert ("fact_revenue_mix", (DATA_TENANT_ID, W), sid) in keys
    pit.insider_txns(sid)
    assert ("fact_insider_txn", (DATA_TENANT_ID,), sid) in set(pit._memo)


def test_the_replay_mirror_applies_the_same_map(db, two_workspaces, tmp_path):
    """The export writes each table for ``tenants_for(table, …)`` and the mirror reader picks per table — so
    W's mirror reads exactly what W's live PIT reads, and V's mirror carries none of W's ratified rows.
    """
    pytest.importorskip("duckdb")
    from replay.export import export_snapshot
    from replay.pit import ReplayPointInTimeData, connect_mirror

    sid, _ = two_workspaces
    # fact_catalyst is the MIXED table the mirror reader carries an accessor for (the call path reads it)
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
    want_w = {"CAT-FEED": "as W ratified it", "CAT-W": "W only"}
    want_v = {"CAT-FEED": "as the feed wrote it"}
    for ws, want in ((W, want_w), (V, want_v)):
        # the live PIT reads the same union the mirror must reproduce
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
