"""``GET /admin/status`` -> ``platform_rows_in_workspaces`` (V2 PR 2 / W0b).

V2 reads share counts and cash/burn from the data tenant only, so a row of either left under a workspace is read by
nobody. The status counts them (fail-open, like its neighbors); the Admin page shows one loud line naming the move
(``python -m pipeline.v2_cutover move-platform-facts --apply``) when the count is above 0.
"""

from __future__ import annotations

import uuid
from datetime import date

from domain.scopes import DATA_TENANT_ID, FOUNDING_WORKSPACE_ID


def _security(db) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, "ADMV2", "0000000098", date(2026, 1, 1)),
        )
    return sid


def test_status_counts_share_and_cash_rows_left_in_a_workspace(client, db, cron_runs_dir):
    assert client.get("/admin/status").json()["platform_rows_in_workspaces"] == 0
    sid = _security(db)
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_shares_outstanding (tenant_id, security_id, shares, source, source_ref, ratified_by, "
            "valid_from) VALUES (%s, %s, 1000, 'cover', 'ADM-1', 'auto', '2026-03-31')",
            (FOUNDING_WORKSPACE_ID, sid),
        )
        # a data-tenant row is where V2 reads it: not counted
        cur.execute(
            "INSERT INTO fact_cash_burn (tenant_id, security_id, cash_usd, quarterly_burn_usd, source, source_ref, "
            "ratified_by, valid_from) VALUES (%s, %s, 10, 1, '10-q', 'ADM-2', 'operator', '2026-03-31')",
            (DATA_TENANT_ID, sid),
        )
    db.commit()
    assert client.get("/admin/status").json()["platform_rows_in_workspaces"] == 1
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_cash_burn (tenant_id, security_id, cash_usd, quarterly_burn_usd, source, source_ref, "
            "ratified_by, valid_from) VALUES (%s, %s, 10, 1, '10-q', 'ADM-3', 'operator', '2026-03-31')",
            (FOUNDING_WORKSPACE_ID, sid),
        )
    db.commit()
    assert client.get("/admin/status").json()["platform_rows_in_workspaces"] == 2
