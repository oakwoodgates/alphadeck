from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from db.migrate import apply_migrations
from db.session import DEFAULT_TENANT_ID, connect


def pytest_configure(config):
    """Point the WHOLE suite at a per-worktree, HARD-GUARDED test DB before any test/fixture/``connect()``.

    Fires at session startup (the configure phase, before collection imports any test module). Resolving
    the name (``test_db_url``) applies the fail-closed guard: a name that doesn't start with
    ``alphadeck_test`` raises HERE — before ``os.environ`` is touched — so a forgotten or stale
    ``DATABASE_URL`` can never truncate the demo. Then it overrides ``DATABASE_URL`` (which BOTH the ``db``
    fixture AND the app-under-test read late) and creates the per-worktree DB if absent. A Postgres-down
    ``OperationalError`` is swallowed so the existing ``_migrated`` SKIP still fires (suite runs offline).
    """
    from db.testdb import ensure_test_db, test_db_url

    url = test_db_url()  # resolves + HARD GUARD; a bad name raises HERE, before os.environ
    os.environ["DATABASE_URL"] = url  # both the db fixture and the app-under-test read this late
    try:
        ensure_test_db(url)  # create the per-worktree DB if absent (idempotent, race-tolerant)
    except psycopg.OperationalError:
        pass  # Postgres down -> the _migrated fixture SKIPs cleanly (unchanged)


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
        # thesis CASCADE clears the whole spine (basket/evidence/catalyst/kill_criterion/calls too)
        cur.execute("TRUNCATE thesis, fact_insider_txn, fact_price_eod, security_master CASCADE")
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
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, "DEVCO", "0001234567", "2026-01-01"),
        )
    db.commit()
    return sid
