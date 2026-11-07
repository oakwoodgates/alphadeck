"""A test helper: make a WORKSPACE tenant (docs/TENANT_SCOPES.md).

``pipeline.provision_tenant`` is retired (a workspace is born with its user, ``pipeline.users create``). A test that
needs the FK row a workspace's tables hang off, without a user, makes it here. The caller owns the transaction.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import psycopg


def new_workspace(
    conn: psycopg.Connection, name: str = "a workspace", *, workspace_id: UUID | None = None
) -> UUID:
    """Insert a ``kind='workspace'`` tenant and return its id (a fresh uuid4 unless ``workspace_id`` is given;
    re-making a given id is a no-op, so a fixed-id test can re-run). Never touches the data tenant.
    """
    wid = workspace_id or uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (id, name, kind) VALUES (%s, %s, 'workspace') ON CONFLICT (id) DO NOTHING",
            (wid, name),
        )
    return wid
