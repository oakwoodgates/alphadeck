from __future__ import annotations

import logging
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


def name_session(conn: psycopg.Connection, name: str) -> None:
    """Name the session in ``pg_stat_activity`` (``application_name``) — what a writer that finds a company
    lock held (``db/write_lock.py``) reports as the holder: "pid 123 (alphadeck-writer:daily-pass, …)".

    COSMETIC and FAIL-OPEN: a name only makes a holder easier to recognize, so a failure here is logged and
    swallowed — it never costs the caller its connection. Committed at once for the same reason as
    ``set_statement_timeout`` (a ``SET`` left in an open transaction is undone by the caller's first
    ``rollback()``); call it on a FRESH connection."""
    try:
        conn.execute("SELECT set_config('application_name', %s, false)", (name[:63],))
        if not conn.autocommit:
            conn.commit()
    except Exception:  # noqa: BLE001 — a name is a convenience, never a reason to fail
        logging.getLogger("alphadeck.db").debug("naming the session failed", exc_info=True)
        try:
            conn.rollback()  # never hand the caller an aborted transaction
        except Exception:  # noqa: BLE001
            pass


def set_statement_timeout(conn: psycopg.Connection, seconds: float) -> None:
    """Set the SESSION's ``statement_timeout`` (C2) — any single statement on ``conn`` running longer than
    ``seconds`` is canceled by the server (``QueryCanceled``) instead of hanging its caller. ``seconds <= 0``
    leaves the server default (no ceiling).

    SESSION-level and COMMITTED here, on purpose: ``SET`` is transactional in Postgres, so a ``SET`` left in
    an open transaction would be UNDONE by the caller's first ``rollback()`` (and the daily loop rolls back
    on every per-thesis error). Committing right after makes the ceiling stick for the life of the session.
    ``set_config`` rather than ``SET`` because psycopg binds parameters server-side and ``SET`` takes none.
    Call it on a FRESH connection (it commits whatever transaction is open)."""
    if seconds <= 0:
        return
    conn.execute("SELECT set_config('statement_timeout', %s, false)", (f"{int(seconds * 1000)}",))
    if not conn.autocommit:
        conn.commit()
