"""Read-only personal access tokens (``api_token.scopes``, migration 0214; docs/AUTH.md, docs/CONNECTORS.md).

A token whose stored ``scopes`` are set is RESTRICTED: the gate holds it to the OAuth token's two rules
(``auth.restricted_may``): any method but GET / HEAD / OPTIONS is 403 "this token can only read", and it never
reaches ``/account`` or ``/auth/change-password``. Its role is ``super_admin`` only when the user is one AND the
token carries ``steward`` (``auth.capped_role``). A token whose scopes are NULL is a full token, exactly as before.

Real users and real bearer tokens (no signed-in override): a plain user A, a steward S and a second steward K (so S
can be demoted). The first test mints its token through the CLI's default, which is read-only.

DB-backed (the shared ``db`` fixture; SKIPS when Postgres is unreachable, which is not a pass).
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app import auth
from app.deps import get_conn
from app.main import app
from domain.settings import get_settings
from pipeline import users
from tests.app.test_auth_gate import _KICKS_A_JOB, OPEN, _fill, _operations
from tests.auth_fixtures import sign_out
from tests.workspaces import new_workspace

PASSWORD = "correct horse battery"
READ_ONLY = "this token can only read"
NO_ACCOUNT = "a read-only token cannot manage your account"
ACCOUNT_READS = (
    "/account/tokens",
    "/account/connections",
    "/account/agents",
    "/account/notifications/slack-webhook",
)


@pytest.fixture
def ro(db, monkeypatch):
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")  # the auth tables are not in the shared truncate
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_PUBLIC_ORIGIN", raising=False)
    get_settings.cache_clear()
    ids = {}
    for key, role in (("a", "user"), ("s", "super_admin"), ("k", "super_admin")):
        ids[key] = auth.create_user(
            db,
            email=f"{key}@read-only.test",
            display_name=key.upper(),
            password=PASSWORD,
            role=role,
            workspace_id=new_workspace(db, f"read-only {key}"),
        )
    db.commit()
    yield ids
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()


def _client() -> TestClient:
    return TestClient(app, base_url="https://testserver", raise_server_exceptions=False)


def _call(method: str, path: str, token: str, **kwargs):
    return _client().request(method, path, headers={"authorization": f"Bearer {token}"}, **kwargs)


def _cli_token(db, key: str, *extra: str) -> tuple[uuid.UUID, str]:
    """A token minted the way an operator does it: ``python -m pipeline.users token create``."""
    out: list[str] = []
    argv = ["token", "create", "--email", f"{key}@read-only.test", "--name", "agent", *extra]
    assert users.run(argv, db, emit=out.append) == 0, out
    (shown,) = [line for line in out if line.startswith("token (shown once): ")]
    (named,) = [line for line in out if line.startswith("token ") and line.endswith(" (agent)")]
    return uuid.UUID(named.split()[1]), shown.removeprefix("token (shown once): ")


def _token(db, uid: uuid.UUID, scopes: set[str] | None, name: str = "agent") -> str:
    _, token = auth.create_api_token(db, uid, name, scopes=scopes)
    db.commit()
    return token


def _stored_scopes(db, token: str):
    with db.cursor() as cur:
        cur.execute("SELECT scopes FROM api_token WHERE token_hash = %s", (auth.token_hash(token),))
        return cur.fetchone()["scopes"]


# --- 1. the CLI's default token can only read ------------------------------------------------------------------


def test_a_cli_default_token_cannot_write(ro, db):
    tid, token = _cli_token(db, "a")
    for method, path, body in (
        ("POST", "/workbench/theses", {}),
        ("POST", "/account/tokens", {"name": "another"}),
        ("DELETE", f"/account/tokens/{tid}", None),
    ):
        r = _call(method, path, token, json=body)
        assert (
            r.status_code == 403
        ), f"a restricted token {method}s {path}: {r.status_code} {r.text}"
    assert _call("GET", "/auth/me", token).status_code == 200  # and it still reads


@pytest.mark.parametrize("key", ["a", "s"])
def test_a_read_only_token_is_refused_on_every_non_get_operation(ro, db, key):
    """The sweep of ``tests/app/test_oauth.py`` over a read-only PAT: a plain user's, and a steward's ``read
    steward`` one. Refused by the gate before any handler or validation runs. The steward skips the two routes
    that start a real job if the gate lets them through (``test_auth_gate._KICKS_A_JOB``): they sit behind the
    same router-level gate as every other ``/admin`` route this sweep covers."""
    role = "super_admin" if key == "s" else "user"
    token = _token(db, ro[key], set(auth.scopes_for_role(role)))
    wrong = []
    n = 0
    for method, path, op in _operations():
        if method in ("GET", "HEAD") or (method, path) in OPEN:
            continue  # POST /auth/login is open by nature: no credential is read there
        if key == "s" and (method, path) in _KICKS_A_JOB:
            continue
        n += 1
        r = _call(method, _fill(path, op), token, json={})
        on_account = path.startswith("/account/") or path == "/auth/change-password"
        expected = NO_ACCOUNT if on_account else READ_ONLY
        if r.status_code != 403 or r.json() != {"detail": expected}:
            wrong.append(f"{method} {path} -> {r.status_code} {r.text[:120]}")
    assert n > 50
    assert wrong == []


def test_a_read_only_token_reads_as_its_user_and_never_reaches_the_account(ro, db):
    token = _token(db, ro["a"], {"read"})
    for path in ACCOUNT_READS:
        r = _call("GET", path, token)
        assert r.status_code == 403 and r.json() == {"detail": NO_ACCOUNT}, path
    me = _call("GET", "/auth/me", token).json()
    assert (me["email"], me["role"]) == ("a@read-only.test", "user")
    assert me["token"] == {"principal": "token", "scopes": ["read"]}
    assert _call("GET", "/theses", token).status_code == 200
    assert _call("GET", "/clock", token).status_code == 200
    assert _call("GET", "/admin/status", token).json() == {"detail": "stewards only"}


# --- 2. the steward scope, capped by the role -------------------------------------------------------------------


def test_a_read_steward_token_reads_the_admin_panels_and_cannot_change_them(ro, db):
    token = _token(db, ro["s"], {"read", "steward"})
    assert _call("GET", "/admin/status", token).status_code == 200
    me = _call("GET", "/auth/me", token).json()
    assert me["role"] == "super_admin"
    assert me["token"] == {"principal": "token", "scopes": ["read", "steward"]}
    # refused by the gate, before the route looks the (unknown) row up: nothing is written
    r = _call("POST", f"/admin/onboarding/{uuid.uuid4()}/retry", token)
    assert r.status_code == 403 and r.json() == {"detail": READ_ONLY}


def test_a_stewards_read_token_is_a_plain_user(ro, db):
    token = _token(db, ro["s"], {"read"})
    r = _call("GET", "/admin/status", token)
    assert r.status_code == 403 and r.json() == {"detail": "stewards only"}
    me = _call("GET", "/auth/me", token).json()
    assert me["role"] == "user" and me["token"]["scopes"] == ["read"]


def test_a_demoted_stewards_read_steward_token_reads_as_a_plain_user(ro, db):
    token = _token(db, ro["s"], {"read", "steward"})
    auth.set_role(db, ro["s"], "user")  # K keeps an enabled super admin
    db.commit()
    assert _call("GET", "/admin/status", token).json() == {"detail": "stewards only"}
    me = _call("GET", "/auth/me", token).json()
    assert me["role"] == "user" and me["token"]["scopes"] == ["read"]  # the ceiling, never a grant
    assert _stored_scopes(db, token) == ["read", "steward"]  # the row is untouched
    auth.set_role(db, ro["s"], "super_admin")  # the inverse
    db.commit()
    assert _call("GET", "/admin/status", token).status_code == 200


# --- 3. a full token behaves exactly as before --------------------------------------------------------------------


def test_a_cli_write_token_is_full_and_still_writes(ro, db):
    tid, token = _cli_token(db, "a", "--write")
    assert _stored_scopes(db, token) is None
    me = _call("GET", "/auth/me", token).json()
    assert me["role"] == "user" and me["token"] == {"principal": "token", "scopes": ["read"]}
    made = _call("POST", "/account/tokens", token, json={"name": "minted by a script"})
    assert made.status_code == 201, made.text
    assert made.json()["scopes"] == ["read"]  # the Settings route mints read-only, whoever asks
    assert (
        _call("DELETE", f"/account/tokens/{tid}", token).status_code == 204
    )  # it may revoke itself
    assert _call("GET", "/auth/me", token).status_code == 401


def test_a_full_stewards_token_keeps_the_full_role(ro, db):
    token = _token(db, ro["s"], None)
    me = _call("GET", "/auth/me", token).json()
    assert me["role"] == "super_admin"
    assert me["token"] == {"principal": "token", "scopes": ["read", "steward"]}
    assert _call("GET", "/account/tokens", token).status_code == 200


# --- 4. last_used_at --------------------------------------------------------------------------------------------


def test_a_read_only_token_is_touched_like_a_full_one(ro, db):
    restricted = _token(db, ro["a"], {"read"}, "restricted")
    full = _token(db, ro["a"], None, "full")
    with db.cursor() as cur:
        cur.execute("UPDATE api_token SET last_used_at = NULL")
    db.commit()
    for token in (restricted, full):
        assert _call("GET", "/theses", token).status_code == 200
    with db.cursor() as cur:
        cur.execute("SELECT name, last_used_at FROM api_token ORDER BY name")
        rows = cur.fetchall()
    assert [r["name"] for r in rows] == ["full", "restricted"]
    assert all(r["last_used_at"] is not None for r in rows)


# --- 5. what a token may hold -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "key,scopes,why",
    [
        ("a", set(), "needs the read scope"),
        ("a", {"steward"}, "needs the read scope"),
        ("a", {"read", "write"}, "read and steward only"),
        ("s", {"read", "write"}, "read and steward only"),
        ("a", {"read", "steward"}, "only a super admin"),
    ],
)
def test_a_token_cannot_be_minted_with_scopes_its_user_may_not_hold(ro, db, key, scopes, why):
    with pytest.raises(ValueError, match=why):
        auth.create_api_token(db, ro[key], "agent", scopes=scopes)
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM api_token")
        assert cur.fetchone()["n"] == 0
