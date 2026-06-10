"""Shared helper for the operator-ratify CLIs (the #10 bridge family).

The Workbench scoring-fact bridges (revenue-mix / shares / cash-burn) all resolve a ticker to its
security in the target tenant before ratifying a sourced fact onto it — the same first step. Factored here
so the three new bridges share one resolver rather than each re-implementing it.
"""

from __future__ import annotations

from uuid import UUID

import psycopg


def resolve_security(conn: psycopg.Connection, ticker: str, tenant_id: UUID) -> UUID:
    """The security_master id for ``ticker`` in ``tenant_id`` (latest row), or exit with a clear error.

    Per-tenant (security_master is per-tenant), so a production ratify targets production's own security.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM security_master WHERE tenant_id = %s AND ticker = %s "
            "ORDER BY recorded_at DESC LIMIT 1",
            (tenant_id, ticker.upper()),
        )
        row = cur.fetchone()
    if row is None:
        raise SystemExit(f"no security_master row for {ticker!r} — seed the security first")
    return row["id"]
