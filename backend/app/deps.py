from __future__ import annotations

from collections.abc import Iterator

import psycopg

from db.session import connect


def get_conn() -> Iterator[psycopg.Connection]:
    """Request-scoped DB connection. Overridden in tests to share the fixture's connection."""
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
