"""The ``tenant`` table's reads (docs/TENANT_SCOPES.md): which tenants are WORKSPACES.

A system job that runs per workspace (the editions nightly and cut) loops ``workspaces(conn)`` instead of assuming
one default tenant. The data tenant (``kind='data'``) is never a workspace and is never listed here.
"""

from __future__ import annotations

from uuid import UUID

import psycopg


def workspaces(conn: psycopg.Connection) -> list[UUID]:
    """Every ``kind='workspace'`` tenant id, in a stable order (by id). Read-only."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM tenant WHERE kind = 'workspace' ORDER BY id")
        return [r["id"] for r in cur.fetchall()]
