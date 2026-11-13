"""The nightly prune of expired sign-in rows (``db/housekeeping.py``, security PR 5 step 8, plan D5 and D6).

For each table one row just past its cut and one just inside it; after ``prune`` the table is COUNTED: only the
old one is gone. What is never deleted is checked too: a decided consent at any age, a revoked grant and a revoked
API token, a self-registered client with a grant, every built-in (``static``) client. ``--dry-run`` deletes
nothing; a second run deletes nothing. DB-backed (the shared ``db`` fixture).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app import auth
from app.oauth import store
from db import housekeeping
from domain.scopes import FOUNDING_WORKSPACE_ID

DAY = timedelta(days=1)
HOUR = timedelta(hours=1)
TABLES = (
    "oauth_code",
    "oauth_access_token",
    "oauth_refresh_token",
    "oauth_consent",
    "oauth_client",
    "user_session",
    "user_action_count",
    "oauth_grant",
    "api_token",
)


@pytest.fixture
def conn(db):
    def wipe():
        with db.cursor() as cur:
            cur.execute("TRUNCATE app_user CASCADE")  # every sign-in table but oauth_client
            cur.execute(
                "DELETE FROM oauth_client WHERE registration = 'dcr' OR client_id = 'static-old'"
            )
        db.commit()

    wipe()
    yield db
    db.rollback()
    wipe()


def _count(conn, table: str) -> int:
    with conn.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 - a fixed table name
        return cur.fetchone()["n"]


def _counts(conn) -> dict[str, int]:
    return {t: _count(conn, t) for t in TABLES}


def _exec(conn, sql: str, *params) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)


def _user(conn, email: str = "keep@housekeeping.test") -> uuid.UUID:
    return auth.create_user(
        conn,
        email=email,
        display_name="Keeper",
        password="correct horse battery",
        workspace_id=FOUNDING_WORKSPACE_ID,
    )


def _client(conn, client_id: str, *, age: timedelta, registration: str = "dcr") -> None:
    _exec(
        conn,
        "INSERT INTO oauth_client (client_id, client_name, redirect_uris, scope, token_endpoint_auth_method, "
        "registration, grant_types, created_at) VALUES (%s, %s, %s, 'read', 'none', %s, %s, now() - %s)",
        client_id,
        client_id,
        ["https://claude.ai/cb"],
        registration,
        ["authorization_code", "refresh_token"],
        age,
    )


def _grant(conn, user_id, client_id: str, *, revoked: bool = False) -> uuid.UUID:
    gid = uuid.uuid4()
    _exec(
        conn,
        "INSERT INTO oauth_grant (id, user_id, client_id, scopes, revoked_at) VALUES (%s, %s, %s, %s, %s)",
        gid,
        user_id,
        client_id,
        ["read"],
        None if not revoked else _now(conn),
    )
    return gid


def _now(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT now() AS t")
        return cur.fetchone()["t"]


def _consent(conn, client_id: str, *, expired_for: timedelta, user_id=None) -> uuid.UUID:
    """A consent whose ``expires_at`` was ``expired_for`` ago (negative = still live); decided when a user is given."""
    cid = uuid.uuid4()
    _exec(
        conn,
        "INSERT INTO oauth_consent (id, client_id, redirect_uri, redirect_uri_explicit, scopes_requested, "
        "code_challenge, state, resource, expires_at, created_at, user_id, decision, decided_at) "
        "VALUES (%s, %s, 'https://claude.ai/cb', true, %s, %s, 's', 'http://x/mcp', now() - %s, "
        "now() - %s - interval '10 minutes', %s, %s, %s)",
        cid,
        client_id,
        ["read"],
        "c" * 43,
        expired_for,
        expired_for,
        user_id,
        "allow" if user_id else None,
        _now(conn) if user_id else None,
    )
    return cid


def _code(conn, grant_id, *, expired_for: timedelta) -> None:
    _exec(
        conn,
        "INSERT INTO oauth_code (code_hash, grant_id, client_id, scopes, redirect_uri, redirect_uri_explicit, "
        "code_challenge, resource, expires_at) SELECT %s, %s, client_id, %s, 'https://claude.ai/cb', true, "
        "%s, 'http://x/mcp', now() - %s FROM oauth_grant WHERE id = %s",
        store.hash_secret(uuid.uuid4().hex),
        grant_id,
        ["read"],
        "c" * 43,
        expired_for,
        grant_id,
    )


def _token(conn, table: str, grant_id, *, expired_for: timedelta) -> None:
    _exec(
        conn,
        f"INSERT INTO {table} (token_hash, grant_id, scopes, resource, expires_at) "  # noqa: S608
        "VALUES (%s, %s, %s, 'http://x/mcp', now() - %s)",
        store.hash_secret(uuid.uuid4().hex),
        grant_id,
        ["read"],
        expired_for,
    )


def _session(conn, user_id, *, expired_for=None, revoked_for=None) -> None:
    _exec(
        conn,
        "INSERT INTO user_session (id, user_id, token_hash, expires_at, revoked_at) "
        "VALUES (%s, %s, %s, now() - %s::interval, "
        "CASE WHEN %s::interval IS NULL THEN NULL ELSE now() - %s::interval END)",
        uuid.uuid4(),
        user_id,
        store.hash_secret(uuid.uuid4().hex),
        expired_for if expired_for is not None else -30 * DAY,
        revoked_for,
        revoked_for,
    )


def _allowance(conn, user_id, *, days_ago: int) -> None:
    _exec(
        conn,
        "INSERT INTO user_action_count (user_id, action, market_day, count) "
        "VALUES (%s, 'extract', (now() AT TIME ZONE %s)::date - %s, 3)",
        user_id,
        "America/New_York",
        days_ago,
    )


def _world(conn) -> dict:
    """One row just past each cut and one just inside it, plus the rows that are never deleted."""
    uid = _user(conn)
    _client(conn, "dcr-live", age=30 * DAY)
    grant = _grant(conn, uid, "dcr-live")
    for table in ("oauth_code", "oauth_access_token", "oauth_refresh_token"):
        if table == "oauth_code":
            _code(conn, grant, expired_for=DAY + HOUR)  # past the 1-day cut
            _code(conn, grant, expired_for=DAY - HOUR)  # inside it
        else:
            _token(conn, table, grant, expired_for=DAY + HOUR)
            _token(conn, table, grant, expired_for=DAY - HOUR)
    _consent(conn, "dcr-live", expired_for=DAY + HOUR)  # undecided, past the cut: gone
    _consent(conn, "dcr-live", expired_for=DAY - HOUR)  # undecided, inside it: kept
    _consent(conn, "dcr-live", expired_for=400 * DAY, user_id=uid)  # decided: kept at any age
    _client(conn, "dcr-old-unused", age=7 * DAY + HOUR)  # gone
    _client(conn, "dcr-young-unused", age=7 * DAY - HOUR)  # kept
    _client(conn, "dcr-old-only-a-stale-request", age=30 * DAY)  # its consent goes first, then it
    _consent(conn, "dcr-old-only-a-stale-request", expired_for=5 * DAY)
    _client(conn, "dcr-old-revoked-grant", age=30 * DAY)  # a revoked grant still keeps it
    _grant(conn, uid, "dcr-old-revoked-grant", revoked=True)
    _client(conn, "static-old", age=400 * DAY, registration="static")  # never deleted
    _session(conn, uid, expired_for=7 * DAY + HOUR)  # gone
    _session(conn, uid, expired_for=7 * DAY - HOUR)  # kept
    _session(conn, uid, revoked_for=7 * DAY + HOUR)  # gone (revoked long ago, not yet expired)
    _session(conn, uid, revoked_for=7 * DAY - HOUR)  # kept
    _session(conn, uid)  # live: kept
    _allowance(conn, uid, days_ago=8)  # gone
    _allowance(conn, uid, days_ago=7)  # kept
    _allowance(conn, uid, days_ago=0)  # kept
    tid, _ = auth.create_api_token(conn, uid, "old")
    auth.revoke_api_token(conn, tid)  # a revoked token is never deleted
    _exec(conn, "UPDATE api_token SET revoked_at = now() - interval '400 days'")
    conn.commit()
    return {"user": uid}


def _client_ids(conn) -> set[str]:
    with conn.cursor() as cur:
        cur.execute("SELECT client_id FROM oauth_client")
        return {r["client_id"] for r in cur.fetchall()}


EXPECTED = {
    "oauth_code": 1,
    "oauth_access_token": 1,
    "oauth_refresh_token": 1,
    "oauth_consent": 2,
    "oauth_client": 2,
    "user_session": 2,
    "user_action_count": 1,
}


def test_prune_deletes_only_the_rows_past_each_cut(conn):
    _world(conn)
    before = _counts(conn)
    deleted = housekeeping.prune(conn)
    conn.commit()
    after = _counts(conn)
    assert deleted == EXPECTED
    for table in TABLES:
        assert before[table] - after[table] == EXPECTED.get(table, 0), table
    ids = _client_ids(conn)
    assert {"dcr-live", "dcr-young-unused", "dcr-old-revoked-grant", "static-old"} <= ids
    assert {"dcr-old-unused", "dcr-old-only-a-stale-request"}.isdisjoint(ids)
    assert set(store.STATIC_CLIENTS) <= ids  # the two built-in clients
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM oauth_consent WHERE decided_at IS NOT NULL")
        assert cur.fetchone()["n"] == 1
        cur.execute("SELECT count(*) AS n FROM oauth_grant WHERE revoked_at IS NOT NULL")
        assert cur.fetchone()["n"] == 1
    assert _count(conn, "api_token") == 1


def test_a_second_run_deletes_nothing(conn):
    _world(conn)
    housekeeping.prune(conn)
    conn.commit()
    before = _counts(conn)
    assert sum(housekeeping.prune(conn).values()) == 0
    conn.commit()
    assert _counts(conn) == before


def test_the_retention_days_come_from_the_settings(conn, monkeypatch):
    from domain.settings import get_settings

    uid = _user(conn)
    _session(conn, uid, expired_for=3 * DAY)
    conn.commit()
    assert housekeeping.prune(conn)["user_session"] == 0  # 7 days by default
    conn.rollback()
    monkeypatch.setenv("ALPHADECK_PRUNE_SESSION_DAYS", "2")
    get_settings.cache_clear()
    try:
        assert housekeeping.prune(conn)["user_session"] == 1
        conn.rollback()
    finally:
        monkeypatch.delenv("ALPHADECK_PRUNE_SESSION_DAYS")
        get_settings.cache_clear()


def test_the_cli_dry_run_deletes_nothing_and_prints_the_counts(conn, capsys):
    _world(conn)
    before = _counts(conn)
    assert housekeeping.main(["--prune", "--dry-run"]) == 0
    line = capsys.readouterr().out.strip().splitlines()[-1]
    assert line.startswith("housekeeping (dry run): would delete")
    assert "user_session 2" in line and "oauth_client 2" in line
    assert _counts(conn) == before


def test_the_cli_prune_commits_and_prints_one_line(conn, capsys):
    _world(conn)
    before = _counts(conn)
    assert housekeeping.main(["--prune"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert len(out) == 1 and out[0].startswith("housekeeping: deleted")
    conn.rollback()  # read what the CLI's own connection committed
    after = _counts(conn)
    assert sum(before.values()) - sum(after.values()) == sum(EXPECTED.values())


def test_the_cli_needs_prune():
    with pytest.raises(SystemExit):
        housekeeping.main([])
