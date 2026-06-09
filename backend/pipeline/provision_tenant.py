"""Provision a tenant — the FK row every table needs before any ingest can target it.

The Phase-1 production cut adds a tenant ALONGSIDE the demo (never a wipe): provision a fresh tenant,
ingest the operator's real data under it, then every call for a thesis in that tenant reads ONLY that
tenant's facts (tenant isolation — see docs/PRODUCTION_TENANT.md). Auth is deferred, so the tenant is
DATA isolation, not authentication: there is no login; a thesis carries its ``tenant_id`` and the call
path threads it into every fact read.

    python -m pipeline.provision_tenant --name production
    # -> prints the new tenant id; ingest under it via the ingest fns' tenant_id= param, then upsert a
    #    thesis with that tenant_id and call it (it re-derives from only this tenant's facts).
"""

from __future__ import annotations

import argparse
from uuid import UUID, uuid4

import psycopg

from db.session import connect


def provision_tenant(conn: psycopg.Connection, name: str, *, tenant_id: UUID | None = None) -> UUID:
    """Insert a tenant row (idempotent) and return its id. The caller owns the txn (no commit here).

    ``tenant_id`` defaults to a fresh ``uuid4`` (a new production tenant). Pass an explicit id for an
    idempotent fixed-id provision (re-runs + tests): ``ON CONFLICT (id) DO NOTHING`` makes re-provisioning
    a no-op. This ONLY creates the FK row — it never wipes or touches any other tenant (the demo is
    untouched). NEVER provision production data into ``DEFAULT_TENANT_ID`` — that is the demo tenant.
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
