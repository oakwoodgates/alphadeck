from __future__ import annotations

import os
import uuid
from datetime import date

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
    # X1 — the staged onboarding job's NIGHTLY WINDOW (21:30-23:30 ET by default) is a wall-clock rule: a
    # promote inside it queues instead of taking the fast lane. Off for the suite by default, so no test's
    # outcome depends on the hour it happens to run; the window's own tests set it (and a clock) explicitly.
    os.environ.setdefault("ALPHADECK_ONBOARD_WINDOW_BEFORE_MIN", "0")
    os.environ.setdefault("ALPHADECK_ONBOARD_WINDOW_AFTER_MIN", "0")
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
def pinned_today(monkeypatch) -> date:
    """The market day, PINNED for one test, so the test's "today" and the app's are the SAME read.

    The code under test reads ``market_today()`` at REQUEST time. A module-level
    ``TODAY = market_today()`` reads it at IMPORT time instead, when the module is collected, which
    under ``-n 6`` can be minutes before the test runs. A run that crossed midnight ET therefore
    compared yesterday's date with today's: started 2026-10-16 23:59:11 ET, 8 failures in
    ``test_decisions_api`` / ``test_thesis_lists_api``, and every one was a CORRECT app answer (a fill
    dated "yesterday" but recorded after yesterday ended is invisible to yesterday's as-of: no
    lookahead on the transaction axis, invariant #1). Reading ``market_today()`` inside the test only
    narrows that window to the test's own runtime; pinning closes it.

    Pins ``domain.market_time.market_now`` at the instant this fixture runs. Every ``market_today()``
    goes through that module attribute, so the test and every route it calls agree on the day by
    construction, even across a real midnight. NOT pinned: transaction time (``recorded_at`` from the
    DB clock, ``datetime.now(timezone.utc)``, the ``known_at`` cap's ``now``), so a pinned DAY never
    fakes when a fact was learned (INVARIANTS.md §4). A module that imported ``market_now`` BY NAME
    (``pipeline/daily.py``) keeps the live clock; a test that needs it pinned patches that name too.
    """
    # Imported here, not at the top: this conftest is imported before its ``pytest_configure`` test-DB
    # guard runs, so its import surface stays exactly what it was.
    from domain import market_time

    frozen = market_time.market_now()
    monkeypatch.setattr(
        market_time, "market_now", lambda tz=None: frozen.astimezone(tz or market_time.market_tz())
    )
    return frozen.date()


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
