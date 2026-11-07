"""Provision a WORKSPACE tenant — the FK row a workspace's tables need (docs/TENANT_SCOPES.md).

A new row is ``kind='workspace'`` (migration 0076's default): it holds a user's theses, calls and ratified
facts, and it reads the ONE shared data tenant (``domain.scopes.DATA_TENANT_ID``) for the master and every
ingested fact — the feed never writes a workspace. Never a wipe: it only inserts the row. PR 2 creates a
workspace with its user (``pipeline.users``) and retires this CLI.

    python -m pipeline.provision_tenant --name "a workspace"
    # -> prints the new tenant id; upsert a thesis with that tenant_id and call it (it reads the DATA facts
    #    plus this workspace's own ratified rows).
"""

from __future__ import annotations

import argparse
from uuid import UUID, uuid4

import psycopg

from db.session import connect


def provision_tenant(conn: psycopg.Connection, name: str, *, tenant_id: UUID | None = None) -> UUID:
    """Insert a tenant row (idempotent) and return its id. The caller owns the txn (no commit here).

    ``tenant_id`` defaults to a fresh ``uuid4`` (a new workspace). Pass an explicit id for an idempotent
    fixed-id provision (re-runs + tests): ``ON CONFLICT (id) DO NOTHING`` makes re-provisioning a no-op. This
    ONLY creates the FK row (``kind='workspace'`` by default) — it never wipes or touches any other tenant, and
    never the data tenant (``…0001``, ``kind='data'``).
    """
    tid = tenant_id or uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO tenant (id, name) VALUES (%s, %s) ON CONFLICT (id) DO NOTHING",
            (tid, name),
        )
    return tid


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(description="Provision a tenant (the Phase-1 production cut).")
    p.add_argument("--name", required=True, help="human label for the tenant (e.g. 'production')")
    p.add_argument(
        "--id",
        default=None,
        help="optional fixed tenant UUID (idempotent re-provision); else a fresh UUID is generated",
    )
    a = p.parse_args(argv)

    conn = connect()
    try:
        tid = provision_tenant(conn, a.name, tenant_id=UUID(a.id) if a.id else None)
        conn.commit()
        print(f"provisioned tenant {tid} ({a.name!r})")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
