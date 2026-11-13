"""The login door's guards (auth PR 1b, docs/AUTH.md): an open route is a failing test, not a leak.

* the ROUTE-GATE test walks ``app.routes``: every API route but ``GET /health`` and ``POST /auth/login`` carries
  ``require_user`` or ``require_super_admin`` somewhere in its dependency tree, so a router mounted open fails;
* the 401 SWEEP calls every operation in the schema with no cookie and no header: each answers 401 (a 422 is a
  failure: the gate must run before validation, which is why it is mounted on the router);
* the 403 SWEEP: a signed-in plain user gets 403 on every steward route; a super admin never gets 401/403;
* the SYSTEM principal reads dashboards, the clock and health, and nothing else;
* login / logout / me / change-password / disabled / throttle / expiry / revoked token / the cookie's flags.

Every client here is built WITHOUT the suite's signed-in override (``tests.auth_fixtures.sign_out``), against
real ``app_user`` / ``user_session`` / ``api_token`` rows written through the same ``app.auth`` functions the
``pipeline.users`` CLI calls.
"""

from __future__ import annotations

import re
from datetime import date
from uuid import UUID

import pytest
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app import auth
from app.auth import require_super_admin, require_user
from app.deps import get_conn
from app.main import app
from domain.scopes import FOUNDING_WORKSPACE_ID
from domain.settings import get_settings
from tests.auth_fixtures import sign_out

OPEN = {("GET", "/health"), ("POST", "/auth/login")}
PASSWORD = "correct horse battery"
BUSINESS_TYPE = "/workbench/securities/{security_id}/business-type"
RESOLVE_ETF = "/workbench/securities/resolve-etf"
# the steward-only routes outside /admin and /backtest: each writes the shared security master (docs/AUTH.md)
STEWARD_PATHS = {BUSINESS_TYPE, RESOLVE_ETF}
# Steward routes a super admin is NOT swept through: each one STARTS a real job when it passes the gate (a full
# nightly pass, a database dump). They carry the same router-level gate as every other /admin route, which the
# plain-user 403 sweep and the route-gate test already prove.
_KICKS_A_JOB = {("POST", "/admin/run-daily"), ("POST", "/admin/backup")}


@pytest.fixture
def anon(db, monkeypatch):
    """A client with NO signed-in override, over the test's connection, on https (so the Secure cookie flows)."""
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    with db.cursor() as cur:
        cur.execute(
            "TRUNCATE app_user CASCADE"
        )  # the auth tables are not in the shared fixture's truncate
    db.commit()
    auth.reset_throttle()
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.delenv("ALPHADECK_COOKIE_SECURE", raising=False)
    get_settings.cache_clear()
    yield TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    app.dependency_overrides.clear()
    auth.reset_throttle()
    get_settings.cache_clear()


def _user(db, email: str, *, role: str = "user") -> UUID:
    uid = auth.create_user(
        db,
        email=email,
        display_name=email.split("@")[0],
        password=PASSWORD,
        role=role,
        workspace_id=FOUNDING_WORKSPACE_ID,
    )
    db.commit()
    return uid


def _login(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/auth/login", json={"email": email, "password": password})


def _signed_in(db, email: str, *, role: str = "user") -> TestClient:
    _user(db, email, role=role)
    c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert _login(c, email).status_code == 200
    return c


def _fill(path: str, op: dict) -> str:
    """Put a well-formed dummy value in every path parameter (a UUID, a date, or a string)."""
    kinds = {
        p["name"]: (p["schema"].get("type"), p["schema"].get("format"))
        for p in op.get("parameters", [])
        if p["in"] == "path"
    }

    def one(m: re.Match) -> str:
        _, fmt = kinds.get(m.group(1), ("string", None))
        if fmt == "uuid":
            return "00000000-0000-0000-0000-0000000000ff"
        if fmt == "date":
            return date(2026, 1, 2).isoformat()
        return "x"

    return re.sub(r"\{([^}]+)\}", one, path)


def _operations():
    for path, ops in app.openapi()["paths"].items():
        for method, op in ops.items():
            yield method.upper(), path, op


def _gates(dependant) -> set:
    found = set()
    for d in dependant.dependencies:
        if d.call in (require_user, require_super_admin):
            found.add(d.call)
        found |= _gates(d)
    return found


# --- the route-gate test -----------------------------------------------------------------------------------------


def test_every_route_but_the_two_open_ones_carries_a_gate():
    routes = [r for r in app.routes if isinstance(r, APIRoute)]
    assert len(routes) > 100  # the whole app, not an empty walk
    open_routes = []
    for r in routes:
        for m in r.methods:
            if (m, r.path) in OPEN:
                continue
            if not _gates(r.dependant):
                open_routes.append(f"{m} {r.path}")
    assert open_routes == []


def test_the_two_open_routes_really_are_open_and_nothing_else_is():
    seen = {
        (m, r.path)
        for r in app.routes
        if isinstance(r, APIRoute)
        for m in r.methods
        if not _gates(r.dependant)
    }
    assert seen == OPEN


def test_steward_routes_carry_require_super_admin():
    for r in app.routes:
        if not isinstance(r, APIRoute):
            continue
        steward = r.path.startswith(("/admin", "/backtest")) or r.path in STEWARD_PATHS
        assert (require_super_admin in _gates(r.dependant)) == steward, r.path


# --- the 401 sweep -----------------------------------------------------------------------------------------------


def test_every_operation_answers_401_with_no_credential(anon):
    wrong = []
    n = 0
    for method, path, op in _operations():
        if (method, path) in OPEN:
            continue
        n += 1
        r = anon.request(method, _fill(path, op))
        if r.status_code != 401:
            wrong.append(f"{method} {path} -> {r.status_code}")
    assert n > 100
    assert wrong == []


def test_the_schema_pages_answer_401_with_no_credential(anon):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert anon.get(path).status_code == 401, path


def test_a_bad_cookie_or_a_bad_bearer_is_401(anon):
    anon.cookies.set(auth.SESSION_COOKIE, "not-a-session")
    assert anon.get("/theses").status_code == 401
    anon.cookies.clear()
    assert anon.get("/theses", headers={"Authorization": "Bearer nope"}).status_code == 401
    assert anon.get("/theses", headers={"Authorization": "Basic abc"}).status_code == 401


def test_health_stays_open(anon):
    assert anon.get("/health").status_code == 200


# --- the 403 sweep -----------------------------------------------------------------------------------------------


def _steward_ops():
    for method, path, op in _operations():
        if path.startswith(("/admin", "/backtest")) or path in STEWARD_PATHS:
            yield method, path, op


def test_a_plain_user_gets_403_on_every_steward_route(anon, db):
    c = _signed_in(db, "plain@test.invalid")
    wrong = []
    n = 0
    for method, path, op in _steward_ops():
        n += 1
        r = c.request(method, _fill(path, op))
        if r.status_code != 403:
            wrong.append(f"{method} {path} -> {r.status_code}")
    assert n > 30
    assert wrong == []


def test_a_super_admin_passes_the_gate_on_every_steward_route(anon, db):
    c = _signed_in(db, "steward@test.invalid", role="super_admin")
    wrong = []
    for method, path, op in _steward_ops():
        if (method, path) in _KICKS_A_JOB:
            continue
        r = c.request(method, _fill(path, op))
        if r.status_code in (401, 403):
            wrong.append(f"{method} {path} -> {r.status_code}")
    assert wrong == []


def test_a_plain_user_reads_the_ordinary_routes(anon, db):
    c = _signed_in(db, "reader@test.invalid")
    assert c.get("/theses").status_code == 200
    assert c.get("/clock").status_code == 200
    assert c.get("/openapi.json").status_code == 200
    assert c.get("/docs").status_code == 200


# --- the system principal ----------------------------------------------------------------------------------------


def test_the_system_token_reads_dashboards_and_the_clock_only(anon, monkeypatch):
    monkeypatch.setenv("ALPHADECK_SYSTEM_TOKEN", "sys-token-for-tests")
    get_settings.cache_clear()
    h = {"Authorization": "Bearer sys-token-for-tests"}
    assert anon.get("/dashboards/chain-map/cards", headers=h).status_code not in (401, 403)
    assert anon.get("/clock", headers=h).status_code == 200
    assert anon.get("/theses", headers=h).status_code == 403
    assert anon.get("/admin/status", headers=h).status_code == 403
    assert anon.get("/auth/me", headers=h).status_code == 403
    assert (
        anon.post("/radar/spac/attach", headers=h, json={}).status_code == 403
    )  # no dashboard route is a POST
    assert anon.post("/workbench/theses", headers=h, json={}).status_code == 403
    # a near miss is no system token at all
    assert (
        anon.get("/clock", headers={"Authorization": "Bearer sys-token-for-test"}).status_code
        == 401
    )


def test_no_system_token_configured_means_no_system_principal(anon, monkeypatch):
    monkeypatch.setenv("ALPHADECK_SYSTEM_TOKEN", "")
    get_settings.cache_clear()
    assert get_settings().system_token is None
    assert anon.get("/clock", headers={"Authorization": "Bearer "}).status_code == 401


# --- login / logout / me / change-password -----------------------------------------------------------------------


def test_login_sets_the_cookie_and_me_answers(anon, db):
    uid = _user(db, "Ana@Test.invalid", role="super_admin")
    r = _login(anon, "ana@test.invalid")  # the email matches case-insensitively
    assert r.status_code == 200
    body = r.json()
    assert body["id"] == str(uid)
    assert body["role"] == "super_admin"
    assert body["workspace_id"] == str(FOUNDING_WORKSPACE_ID)
    me = anon.get("/auth/me")
    assert me.status_code == 200 and me.json()["email"] == "Ana@Test.invalid"
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM user_session WHERE user_id = %s", (uid,))
        assert cur.fetchone()["n"] == 1


def test_the_cookie_flags_secure_on(anon, db):
    _user(db, "flags@test.invalid")
    r = _login(anon, "flags@test.invalid")
    raw = r.headers["set-cookie"]
    assert raw.startswith(f"{auth.SESSION_COOKIE}=")
    low = raw.lower()
    assert "httponly" in low and "samesite=lax" in low and "path=/" in low and "secure" in low


def test_the_cookie_flags_secure_off(anon, db, monkeypatch):
    monkeypatch.setenv("ALPHADECK_COOKIE_SECURE", "false")
    get_settings.cache_clear()
    _user(db, "plainhttp@test.invalid")
    c = TestClient(app, raise_server_exceptions=False)  # plain http, as on dev
    r = _login(c, "plainhttp@test.invalid")
    low = r.headers["set-cookie"].lower()
    assert "httponly" in low and "samesite=lax" in low and "secure" not in low
    assert c.get("/auth/me").status_code == 200  # the cookie flows back over http


def test_a_wrong_password_and_an_unknown_email_answer_the_same_401(anon, db):
    _user(db, "bob@test.invalid")
    a = _login(anon, "bob@test.invalid", "wrong password!!")
    b = _login(anon, "nobody@test.invalid", "wrong password!!")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()
    assert "set-cookie" not in a.headers


def test_logout_revokes_the_session(anon, db):
    _user(db, "out@test.invalid")
    _login(anon, "out@test.invalid")
    token = anon.cookies.get(auth.SESSION_COOKIE)
    assert anon.post("/auth/logout").status_code == 204
    assert anon.get("/auth/me").status_code == 401
    replay = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    replay.cookies.set(auth.SESSION_COOKIE, token)
    assert replay.get("/auth/me").status_code == 401  # the old cookie value is dead server-side


def test_change_password_revokes_every_other_session(anon, db):
    _user(db, "cp@test.invalid")
    other = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert _login(other, "cp@test.invalid").status_code == 200
    assert _login(anon, "cp@test.invalid").status_code == 200
    short = anon.post("/auth/change-password", json={"current": PASSWORD, "new": "short"})
    # the request model's password limit (security hardening PR 3): 12 to 1,024 characters, a 422 naming the field
    assert short.status_code == 422
    assert short.json()["detail"][0]["loc"] == ["body", "new"]
    assert "at least 12 characters" in short.json()["detail"][0]["msg"]
    wrong = anon.post(
        "/auth/change-password", json={"current": "not it at all", "new": "a brand new passphrase"}
    )
    assert wrong.status_code == 400
    ok = anon.post(
        "/auth/change-password", json={"current": PASSWORD, "new": "a brand new passphrase"}
    )
    assert ok.status_code == 204
    assert anon.get("/auth/me").status_code == 200  # this session stays
    assert other.get("/auth/me").status_code == 401  # the other one is signed out
    fresh = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert _login(fresh, "cp@test.invalid").status_code == 401
    assert _login(fresh, "cp@test.invalid", "a brand new passphrase").status_code == 200


def test_a_disabled_user_cannot_sign_in_and_loses_live_sessions_and_tokens(anon, db):
    uid = _user(db, "dis@test.invalid")
    assert _login(anon, "dis@test.invalid").status_code == 200
    _, token = auth.create_api_token(db, uid, "mcp")
    db.commit()
    bearer = {"Authorization": f"Bearer {token}"}
    assert anon.get("/theses", headers=bearer).status_code == 200
    auth.set_disabled(db, uid, True)
    db.commit()
    assert anon.get("/auth/me").status_code == 401
    assert anon.get("/theses", headers=bearer).status_code == 401
    fresh = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    assert _login(fresh, "dis@test.invalid").status_code == 401
    auth.set_disabled(db, uid, False)  # the inverse
    db.commit()
    assert _login(fresh, "dis@test.invalid").status_code == 200


def test_a_demoted_super_admin_is_a_plain_user_on_the_next_request(anon, db):
    """``set_role`` takes effect at once: the gate reads the role from ``app_user`` on every request, for a session
    and for a personal token alike (security cleanup Part B item 4)."""
    uid = _user(db, "demo@test.invalid", role="super_admin")
    _user(db, "keeper@test.invalid", role="super_admin")  # the guard keeps one enabled super admin
    assert _login(anon, "demo@test.invalid").status_code == 200
    _, token = auth.create_api_token(db, uid, "mcp")
    db.commit()
    bearer = {"Authorization": f"Bearer {token}"}
    assert anon.get("/auth/me").json()["role"] == "super_admin"
    assert anon.get("/admin/status").status_code == 200
    auth.set_role(db, uid, "user")
    db.commit()
    assert anon.get("/auth/me").json()["role"] == "user"
    assert anon.get("/admin/status").status_code == 403
    assert anon.get("/admin/status", headers=bearer).status_code == 403
    assert anon.get("/theses", headers=bearer).status_code == 200


def test_the_login_throttle_locks_out_then_lets_go(anon, db, monkeypatch):
    monkeypatch.setenv("ALPHADECK_LOGIN_MAX_FAILURES", "3")
    get_settings.cache_clear()
    _user(db, "thr@test.invalid")
    codes = [_login(anon, "thr@test.invalid", "wrong password!!").status_code for _ in range(3)]
    assert codes == [401, 401, 401]
    # locked: even the RIGHT password answers 429 until the lockout ends
    assert _login(anon, "thr@test.invalid").status_code == 429
    # another email from the same address is not locked
    _user(db, "thr2@test.invalid")
    assert _login(anon, "thr2@test.invalid").status_code == 200
    key = auth.throttle_key("thr@test.invalid", "testclient")
    assert auth.is_locked_out(key)
    assert not auth.is_locked_out(key, now=10**12)  # the lockout ends
    assert _login(anon, "thr@test.invalid").status_code == 200


def test_an_expired_session_is_401(anon, db):
    _user(db, "exp@test.invalid")
    _login(anon, "exp@test.invalid")
    with db.cursor() as cur:
        cur.execute("UPDATE user_session SET expires_at = now() - interval '1 second'")
    db.commit()
    assert anon.get("/auth/me").status_code == 401


def test_the_session_idle_deadline_slides_forward_on_use(anon, db):
    _user(db, "slide@test.invalid")
    _login(anon, "slide@test.invalid")
    with db.cursor() as cur:
        cur.execute(
            "UPDATE user_session SET last_seen_at = now() - interval '1 hour', "
            "expires_at = now() + interval '1 day'"
        )
    db.commit()
    assert anon.get("/auth/me").status_code == 200
    with db.cursor() as cur:
        cur.execute("SELECT expires_at - now() AS left FROM user_session")
        assert cur.fetchone()["left"].days >= get_settings().session_idle_days - 1


def test_a_revoked_token_is_401(anon, db):
    uid = _user(db, "tok@test.invalid")
    tid, token = auth.create_api_token(db, uid, "ops")
    db.commit()
    h = {"Authorization": f"Bearer {token}"}
    assert anon.get("/auth/me", headers=h).json()["email"] == "tok@test.invalid"
    with db.cursor() as cur:
        cur.execute("SELECT last_used_at FROM api_token WHERE id = %s", (tid,))
        assert cur.fetchone()["last_used_at"] is not None
    assert auth.revoke_api_token(db, tid)
    db.commit()
    assert anon.get("/auth/me", headers=h).status_code == 401


def test_only_hashes_are_stored(anon, db):
    uid = _user(db, "hash@test.invalid")
    _login(anon, "hash@test.invalid")
    cookie = anon.cookies.get(auth.SESSION_COOKIE)
    _, token = auth.create_api_token(db, uid, "x")
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT password_hash FROM app_user WHERE id = %s", (uid,))
        assert cur.fetchone()["password_hash"].startswith("$argon2id$")
        cur.execute("SELECT token_hash FROM user_session")
        assert cur.fetchone()["token_hash"] == auth.token_hash(cookie) != cookie
        cur.execute("SELECT token_hash FROM api_token")
        assert cur.fetchone()["token_hash"] == auth.token_hash(token) != token


def test_the_hosted_mcp_route_is_gated_and_its_metadata_document_is_open(anon):
    """The hosted agent tools endpoint (docs/AGENT_TOOLS.md §Hosted endpoint) adds two Starlette ``Route``s, not
    ``APIRoute``s: they sit OUTSIDE the route-gate walk and the 401 sweep above BY DESIGN (neither appears in
    ``app.openapi()``), so this test holds them to the rule instead. ``POST /mcp`` without a credential is 401 (the
    SDK's bearer gate, whose verifier IS ``GET /auth/me``); the RFC 9728 protected-resource document is open by
    nature (a client reads it to learn how to sign in). ``OPEN`` above is unchanged."""
    pytest.importorskip("mcp")
    from starlette.applications import Starlette

    from agent_tools.hosted import build_hosted

    hosted = build_hosted(app, public_origin="http://127.0.0.1:8000")
    mounted = TestClient(
        Starlette(routes=hosted.routes, lifespan=lambda _: hosted.lifespan()),
        base_url="http://127.0.0.1:8000",
    )
    with mounted:
        r = mounted.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
            headers={"accept": "application/json, text/event-stream"},
        )
        assert r.status_code == 401
        assert "resource_metadata=" in r.headers["www-authenticate"]
        prm = mounted.get("/.well-known/oauth-protected-resource/mcp")
        assert prm.status_code == 200
        assert prm.json()["resource"] == "http://127.0.0.1:8000/mcp"
    assert not any(
        getattr(r, "path", None) == "/mcp" for r in app.routes
    )  # unset origin: not mounted


# --- the OAuth sign-in (docs/CONNECTORS.md) ------------------------------------------------------------------------

# The authorization server's five routes (the MCP SDK's own, app/oauth/server.py): Starlette routes outside the
# route-gate walk and the 401 sweep, like /mcp. Each authenticates its own way, so each is OPEN BY NATURE: the
# metadata document is public, /authorize hands the decision to the consent page (the user's session), and
# /token, /register and /revoke authenticate the CLIENT. ``OPEN`` above is unchanged.
AS_OPEN_BY_NATURE = (
    "/.well-known/oauth-authorization-server",
    "/authorize",
    "/token",
    "/register",
    "/revoke",
)


def test_the_authorization_server_routes_are_reachable_without_a_credential():
    """Each answers with the SDK's own reply (never our gate's 401, never a 404). None of these requests gets as
    far as the database, so the provider's connection is one that refuses to open."""
    pytest.importorskip("mcp")
    from contextlib import contextmanager

    from starlette.applications import Starlette

    from app.oauth.provider import AlphaDeckAuthProvider
    from app.oauth.server import AS_PATHS, build_oauth_routes

    @contextmanager
    def no_db():
        raise AssertionError("an unauthenticated AS request reached the database")
        yield  # pragma: no cover

    origin = "http://127.0.0.1:8000"
    server = build_oauth_routes(origin, AlphaDeckAuthProvider(no_db, public_origin=origin))
    assert tuple(AS_PATHS) == AS_OPEN_BY_NATURE
    assert sorted(r.path for r in server.routes) == sorted(AS_OPEN_BY_NATURE)
    c = TestClient(Starlette(routes=server.routes), base_url=origin)
    meta = c.get("/.well-known/oauth-authorization-server")
    assert meta.status_code == 200 and meta.json()["issuer"] == f"{origin}/"
    authorize = c.get("/authorize")
    assert authorize.status_code == 400 and authorize.json()["error"] == "invalid_request"
    token = c.post("/token", data={})
    assert token.status_code == 401 and token.json()["error"] == "invalid_client"
    register = c.post("/register", json={})
    assert register.status_code == 400 and register.json()["error"] == "invalid_client_metadata"
    revoke = c.post("/revoke", data={})
    assert revoke.status_code == 401 and revoke.json()["error"] == "unauthorized_client"
    # unset origin (this suite): none of them is mounted on the API
    assert not any(getattr(r, "path", None) in AS_OPEN_BY_NATURE for r in app.routes)


def test_the_consent_routes_are_api_routes_behind_require_user():
    found = {
        (m, r.path): r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/oauth/consent")
        for m in r.methods
    }
    assert set(found) == {
        ("GET", "/oauth/consent/{consent_id}"),
        ("POST", "/oauth/consent/{consent_id}"),
    }
    for r in found.values():
        assert require_user in _gates(r.dependant)
