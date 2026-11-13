"""Timeouts on request and job connections (security hardening PR 2, item 3; docs/DEPLOY.md §Connections).

``db.session.connect_request`` (every API request through ``app.db_conn.get_conn``, and the OAuth provider) opens its
connection with ``connect_timeout`` and the session's ``statement_timeout``, ``lock_timeout`` and
``idle_in_transaction_session_timeout`` set as connection OPTIONS: nothing is executed and nothing committed, so it
can never end a request's transaction. ``connect_job`` (the draft job, the on-promote ingest job) gets a lock
timeout and a longer statement timeout. Each is a setting; 0 = off. The nightly and the CLIs keep ``connect()`` and
their own settings.
"""

from __future__ import annotations

import pytest
from psycopg.pq import TransactionStatus

from db import session
from domain.settings import get_settings


def _show(conn, name: str) -> str:
    with conn.cursor() as cur:
        cur.execute(f"SHOW {name}")
        value = next(iter(cur.fetchone().values()))
    conn.rollback()
    return value


@pytest.fixture
def opened():
    conns = []

    def open_(factory):
        c = factory()
        conns.append(c)
        return c

    yield open_
    for c in conns:
        c.close()


def test_request_connections_carry_the_default_timeouts(_migrated, opened):
    conn = opened(session.connect_request)
    assert conn.info.transaction_status == TransactionStatus.IDLE  # nothing ran, nothing committed
    assert _show(conn, "statement_timeout") == "5min"
    assert _show(conn, "lock_timeout") == "5s"
    assert _show(conn, "idle_in_transaction_session_timeout") == "2min"
    assert conn.info.get_parameters().get("connect_timeout") == "5"


def test_a_zero_setting_turns_that_timeout_off(_migrated, opened, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "db_statement_timeout_s", 0)
    monkeypatch.setattr(s, "db_lock_timeout_s", 0)
    monkeypatch.setattr(s, "db_idle_in_transaction_timeout_s", 0)
    monkeypatch.setattr(s, "db_connect_timeout_s", 0)
    conn = opened(session.connect_request)
    assert _show(conn, "statement_timeout") == "0"
    assert _show(conn, "lock_timeout") == "0"
    assert _show(conn, "idle_in_transaction_session_timeout") == "0"
    assert "connect_timeout" not in conn.info.get_parameters()


def test_the_settings_are_read_at_connect_time(_migrated, opened, monkeypatch):
    monkeypatch.setattr(get_settings(), "db_statement_timeout_s", 1.5)
    conn = opened(session.connect_request)
    assert _show(conn, "statement_timeout") == "1500ms"


def test_a_rollback_keeps_the_timeouts(_migrated, opened):
    """Options are session settings from the start, not a ``SET`` inside a transaction a rollback would undo."""
    conn = opened(session.connect_request)
    conn.execute("SELECT 1")
    conn.rollback()
    assert _show(conn, "statement_timeout") == "5min"


def test_job_connections_get_a_lock_timeout_and_a_longer_statement_timeout(_migrated, opened):
    conn = opened(session.connect_job)
    assert _show(conn, "statement_timeout") == "15min"
    assert _show(conn, "lock_timeout") == "30s"
    assert _show(conn, "idle_in_transaction_session_timeout") == "0"


def test_nightly_and_cli_connections_are_unchanged(_migrated, opened):
    conn = opened(session.connect)
    assert _show(conn, "statement_timeout") == "0"
    assert _show(conn, "lock_timeout") == "0"


def test_get_conn_opens_a_request_connection(_migrated):
    from app.db_conn import get_conn

    gen = get_conn()
    conn = next(gen)
    try:
        assert _show(conn, "statement_timeout") == "5min"
    finally:
        gen.close()
    assert conn.closed


def test_the_oauth_provider_opens_request_connections(_migrated, monkeypatch):
    pytest.importorskip("mcp")
    from app.oauth import server

    class _App:
        class router:  # noqa: N801 — mimics FastAPI's app.router
            routes: list = []

        class state:  # noqa: N801
            pass

    class _Settings:
        public_origin = "https://deck.example.com"

    built = server.mount_oauth(_App, _Settings)
    with built.provider.connection() as conn:
        assert _show(conn, "lock_timeout") == "5s"


def test_a_canceled_statement_is_a_query_canceled(_migrated, opened, monkeypatch):
    import psycopg

    monkeypatch.setattr(get_settings(), "db_statement_timeout_s", 0.2)
    conn = opened(session.connect_request)
    with pytest.raises(psycopg.errors.QueryCanceled):
        conn.execute("SELECT pg_sleep(2)")
    conn.rollback()
