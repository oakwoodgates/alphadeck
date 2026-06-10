from __future__ import annotations

from collections.abc import Iterator
from uuid import UUID

import psycopg

from db.session import connect, current_tenant_id


def get_conn() -> Iterator[psycopg.Connection]:
    """Request-scoped DB connection. Overridden in tests to share the fixture's connection."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def get_current_tenant() -> UUID:
    """The current deployment tenant (env-config, NOT auth). Overridable in tests, like ``get_conn``."""
    return current_tenant_id()
