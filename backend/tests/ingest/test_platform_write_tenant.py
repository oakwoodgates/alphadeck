"""The platform-fact write guard (V2 W1a, ``domain.scopes.require_write_tenant``).

Share counts, cash/burn and company catalysts are platform data (``Scope.DATA``): every read names the data tenant
only, so a row written under a workspace would be read by nobody — a confirm that "succeeds" and changes nothing.
Each ingest writer therefore refuses a workspace tenant BEFORE it writes, and writes the data tenant as before.

DB-backed (the shared ``db`` fixture; skips when Postgres is unreachable). The workspace is a real one, not the
data tenant (``…0001``), so a missed check cannot pass by accident.
"""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from domain.enums import CatalystType, Grade
from domain.scopes import DATA_TENANT_ID
from ingest.cash_burn import ingest_cash_burn
from ingest.catalyst import ingest_catalyst
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding
from tests.workspaces import new_workspace

W = uuid.UUID("00000000-0000-0000-0000-0000000000b1")


def _write(table: str, db, sid, tenant):
    common = dict(source="ratified", source_ref=f"{table}-ref", event_date=date(2026, 5, 1))
    if table == "fact_shares_outstanding":
        return ingest_shares_outstanding(db, sid, shares=1_000, tenant_id=tenant, **common)
    if table == "fact_cash_burn":
        return ingest_cash_burn(
            db, sid, cash_usd=1_000, quarterly_burn_usd=10, tenant_id=tenant, **common
        )
    return ingest_catalyst(
        db,
        sid,
        catalyst_type=CatalystType.CONTRACT,
        grade=Grade.CORE,
        label="a contract",
        tenant_id=tenant,
        **common,
    )


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 — a test constant
        return cur.fetchone()["n"]


_PLATFORM = ("fact_shares_outstanding", "fact_cash_burn", "fact_catalyst")


@pytest.mark.parametrize("table", _PLATFORM)
def test_a_platform_writer_refuses_a_workspace_and_writes_nothing(db, security_id, table):
    new_workspace(db, "workspace W", workspace_id=W)
    before = _count(db, table)
    with pytest.raises(ValueError, match="data tenant"):
        _write(table, db, security_id, W)
    assert _count(db, table) == before


@pytest.mark.parametrize("table", _PLATFORM)
def test_a_platform_writer_writes_the_data_tenant(db, security_id, table):
    fid = _write(table, db, security_id, DATA_TENANT_ID)
    with db.cursor() as cur:
        cur.execute(f"SELECT tenant_id FROM {table} WHERE id = %s", (fid,))  # noqa: S608
        assert cur.fetchone()["tenant_id"] == DATA_TENANT_ID


def test_purity_is_written_in_the_workspace(db, security_id):
    """Purity is a WORKSPACE table: the guard never stands in its way."""
    new_workspace(db, "workspace W", workspace_id=W)
    fid = ingest_revenue_mix(
        db,
        security_id,
        segment_label="core",
        mix_pct=50,
        source="ratified",
        source_ref="mix-ref",
        event_date=date(2026, 1, 1),
        tenant_id=W,
    )
    with db.cursor() as cur:
        cur.execute("SELECT tenant_id FROM fact_revenue_mix WHERE id = %s", (fid,))
        assert cur.fetchone()["tenant_id"] == W


def test_the_guard_itself():
    from domain.scopes import require_write_tenant

    require_write_tenant("fact_shares_outstanding", DATA_TENANT_ID)  # no raise
    require_write_tenant("fact_revenue_mix", W)  # a WORKSPACE table: not checked
    with pytest.raises(ValueError, match="fact_catalyst"):
        require_write_tenant("fact_catalyst", W)
    with pytest.raises(KeyError):
        require_write_tenant("no_such_table", DATA_TENANT_ID)
