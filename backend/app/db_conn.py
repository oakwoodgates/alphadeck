"""The request-scoped DB connection dependency, in its own module so ``app.auth`` and ``app.deps`` can both use it
without importing each other in a circle (``deps.get_workspace`` depends on ``auth.require_user``, and
``require_user`` reads the session through this connection). ``app.deps`` re-exports ``get_conn`` unchanged, so
every ``from app.deps import get_conn`` (and every ``dependency_overrides[get_conn]``) is the SAME object.
"""

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
