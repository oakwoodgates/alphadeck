from __future__ import annotations

import os
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

DEFAULT_DATABASE_URL = "postgresql://alphadeck:alphadeck@localhost:5544/alphadeck"

# One seeded tenant (auth deferred, but tenant_id is on every table from day one). Matches 0001_init.sql.
DEFAULT_TENANT_ID = UUID("00000000-0000-0000-0000-000000000001")


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)


def current_tenant_id() -> UUID:
    """The tenant this DEPLOYMENT serves — a deployment-config setting (env ``ALPHADECK_TENANT_ID``), NOT
    auth (auth is deferred). The Workbench works IN one tenant, so it resolves the current tenant here
    rather than listing all tenants mixed (the Board's known limitation). Defaults to the demo tenant.
    Per-thesis reads still take their tenant from the thesis (intrinsic); this scopes the write/promote
    path and any future tenant-scoped listing. See docs/PRODUCTION_TENANT.md."""
    raw = os.environ.get("ALPHADECK_TENANT_ID")
    return UUID(raw) if raw else DEFAULT_TENANT_ID


def connect() -> psycopg.Connection:
    """Open a psycopg connection with dict rows. The caller owns the transaction (commit/rollback)."""
    return psycopg.connect(database_url(), row_factory=dict_row)
