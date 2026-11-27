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
    from db.testdb import ensure_test_db, test_db_url, with_connect_timeout

    # resolves + HARD GUARD (a bad name raises HERE, before os.environ), then bounds every test connect
    url = with_connect_timeout(test_db_url())
    os.environ["DATABASE_URL"] = url  # both the db fixture and the app-under-test read this late
    # X1 — the staged onboarding job's NIGHTLY WINDOW (21:30-23:30 ET by default) is a wall-clock rule: a
    # promote inside it queues instead of taking the fast lane. Off for the suite by default, so no test's
    # outcome depends on the hour it happens to run; the window's own tests set it (and a clock) explicitly.
    os.environ.setdefault("ALPHADECK_ONBOARD_WINDOW_BEFORE_MIN", "0")
    os.environ.setdefault("ALPHADECK_ONBOARD_WINDOW_AFTER_MIN", "0")
    # W6-4 — NO LIVE MASSIVE.COM KEY in the suite. A host with MASSIVE_API_KEY set (the one free-plan key prod's
    # jobs share) would otherwise let a test that runs the daily pass send live requests: the split detector,
    # the tape cross-check, the reference reads. Removed for the whole session — before any settings are built —
    # with every ALPHADECK_POLYGON_* override (the vendor host, the split path); a test that needs a key sets a
    # fake one itself (`monkeypatch.setenv`). Guarded by tests/test_suite_env.py.
    for name in [n for n in os.environ if n.upper() == "MASSIVE_API_KEY"]:
        del os.environ[name]
    for name in [n for n in os.environ if n.upper().startswith("ALPHADECK_POLYGON_")]:
        del os.environ[name]
    from domain.settings import get_settings

    get_settings.cache_clear()  # nothing built before this hook keeps the key
    # F5 — the suite-wide market-clock pin (``_market_clock_from_env``): a malformed, naive or past
    # ALPHADECK_TEST_MARKET_NOW stops the run here, before any test, instead of erroring every one.
    from tests.market_clock_probe import instant_from_env

    try:
        instant_from_env(os.environ)
    except ValueError as exc:
        raise pytest.UsageError(str(exc)) from exc
    try:
        ensure_test_db(url)  # create the per-worktree DB if absent (idempotent, race-tolerant)
    except psycopg.OperationalError:
        pass  # Postgres down -> the _migrated fixture SKIPs cleanly (unchanged)


@pytest.fixture(scope="session", autouse=True)
def _fast_argon2_for_tests():
    """Use a fast real Argon2id profile for the suite (1 MiB, 1 iteration vs RFC 9106's 64 MiB x 3).

    Every password hash/verify still runs real argon2id and produces real ``$argon2id$`` PHC strings, while
    avoiding hundreds of CPU-seconds spent hashing test passwords. Production's default ``PasswordHasher()``
    parameters are pinned in ``tests/app/test_auth_gate.py::test_prod_argon2_uses_rfc9106_defaults``.
    """
    from argon2 import PasswordHasher

    from app import auth

    orig_hasher = auth._HASHER
    auth._HASHER = PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)
    auth._DUMMY_HASH = None
    try:
        yield orig_hasher
    finally:
        auth._HASHER = orig_hasher
        auth._DUMMY_HASH = None


@pytest.fixture(scope="session", autouse=True)
def _market_clock_from_env():
    """F5: ``ALPHADECK_TEST_MARKET_NOW=<ISO instant with offset>`` runs the WHOLE suite at that market instant.

    ``market_now`` starts there and ticks, and so do ``market_today`` / ``market_clock`` / ``current_night`` /
    ``pinned_today`` and every route they serve; transaction time is not moved (``tests/market_clock_probe.py``).
    ``scripts/closure_probe.py`` runs the suite this way at NYSE closures. Unset, as in CI, it patches nothing.
    Set up at the first test, after collection imported every module, so the modules that imported
    ``market_now`` BY NAME are pinned too.
    """
    from tests.market_clock_probe import pin_from_env

    mp = pytest.MonkeyPatch()
    try:
        yield pin_from_env(os.environ, mp)
    finally:
        mp.undo()


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
        # thesis CASCADE clears the whole spine (basket/evidence/catalyst/kill_criterion/calls too);
        # `edition` (0063) references no thesis, so it is named — its event/entry/page/cause rows cascade;
        # `data_repair` (0065, the repair journal) references only the tenant, so it is named too, and so is
        # `security_change` (0081, the dashboards' change log: no foreign key to the master by design)
        cur.execute(
            "TRUNCATE thesis, fact_insider_txn, fact_price_eod, security_master, edition, "
            "data_repair, security_change CASCADE"
        )
    conn.commit()
    from app import call_cache

    call_cache.CACHE.clear()
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
    fakes when a fact was learned (INVARIANTS.md §1). A module that imported ``market_now`` BY NAME
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
def pin_market_clock(monkeypatch):
    """Pin the market clock at an EXACT instant for one test: ``pin_market_clock(instant)`` (an AWARE datetime).

    ``pinned_today`` freezes the clock at whatever instant the fixture happens to run, which is enough for a test
    that only needs the DAY to hold still. A test that depends on the TIME of day (F2: the current night, and so the
    action day a countdown counts from, change at the 22:30 pass) pins the instant itself. Same seam as
    ``pinned_today`` (``domain.market_time.market_now``), so ``market_today`` / ``market_clock`` / ``current_night``
    and every route they serve read the one pinned instant. The pass time and zone are the defaults the suite's
    environment sets (an unset ``ALPHADECK_CRON_AT`` / ``ALPHADECK_MARKET_TZ``); transaction time is not pinned.
    """
    from domain import market_time
    from domain.settings import get_settings

    monkeypatch.delenv("ALPHADECK_CRON_AT", raising=False)
    monkeypatch.delenv("ALPHADECK_MARKET_TZ", raising=False)
    get_settings.cache_clear()

    def pin(instant) -> None:
        monkeypatch.setattr(
            market_time,
            "market_now",
            lambda tz=None: instant.astimezone(tz or market_time.market_tz()),
        )

    yield pin
    get_settings.cache_clear()


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
