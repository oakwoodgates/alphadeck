"""The request-scoped DB connection dependency, in its own module so ``app.auth`` and ``app.deps`` can both use it
without importing each other in a circle (``deps.get_workspace`` depends on ``auth.require_user``, and
``require_user`` reads the session through this connection). ``app.deps`` re-exports ``get_conn`` unchanged, so
every ``from app.deps import get_conn`` (and every ``dependency_overrides[get_conn]``) is the SAME object.
"""

from __future__ import annotations

from collections.abc import Iterator

import psycopg

from db.session import connect_request


def get_conn() -> Iterator[psycopg.Connection]:
    """Request-scoped DB connection, with the request timeouts (``db.session.connect_request``: connect, statement,
    lock and idle-in-transaction, each a setting). Overridden in tests to share the fixture's connection.
    """
    conn = connect_request()
    try:
        yield conn
    finally:
        conn.close()
