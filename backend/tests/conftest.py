from __future__ import annotations

import uuid

import psycopg
import pytest

from db.migrate import apply_migrations
from db.session import DEFAULT_TENANT_ID, connect


@pytest.fixture(scope="session")
def _migrated():
    """Apply migrations once; skip every DB-backed test if Postgres isn't reachable."""
    try:
        conn = connect()
    except psycopg.OperationalError as exc:
        pytest.skip(
            f"Postgres not reachable ({exc.__class__.__name__}); "
            "start it with `docker compose -f infra/docker-compose.yml up -d`"
        )
    try:
        apply_migrations(conn)
    finally:
        conn.close()


@pytest.fixture
def db(_migrated):
    """A clean connection per test: truncate the facts + security master, keep the seeded tenant."""
    conn = connect()
    with conn.cursor() as cur:
        cur.execute("TRUNCATE fact_insider_txn, fact_price_eod, security_master CASCADE")
    conn.commit()
    try:
        yield conn
    finally:
        conn.rollback()
        conn.close()


@pytest.fixture
def security_id(db) -> uuid.UUID:
    """Insert one security_master row so facts have something to reference."""
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) "
            "VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "DEVCO", "2026-01-01"),
        )
    db.commit()
    return sid
