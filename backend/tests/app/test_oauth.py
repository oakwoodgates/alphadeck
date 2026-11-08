"""Our own OAuth sign-in for the hosted endpoint (``app/oauth``, ``app/routers/oauth_consent.py``,
docs/CONNECTORS.md) against the REAL API and the REAL login door.

The authorization server's routes (the MCP SDK's own, over ``AlphaDeckAuthProvider`` on the test's connection) and
the hosted ``/mcp`` routes sit on throwaway Starlette apps whose upstream is the real ``app.main.app``, as Phase 1's
``tests/agent_tools/test_hosted.py`` does; the consent and account routes go through ``TestClient(app)`` with real
cookies from ``POST /auth/login`` (no signed-in override). Two plain users A and B on their own workspaces with one
card each, and a steward S.

DB-backed (the shared ``db`` fixture; SKIPS when Postgres is unreachable, which is not a pass).
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import secrets
import uuid
from contextlib import contextmanager
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

pytest.importorskip("mcp")

from fastapi.testclient import TestClient  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

from agent_tools.hosted import build_hosted  # noqa: E402
from app import auth  # noqa: E402
from app.deps import get_conn  # noqa: E402
from app.main import app  # noqa: E402
from app.oauth import store  # noqa: E402
from app.oauth.provider import AlphaDeckAuthProvider  # noqa: E402
from app.oauth.server import build_oauth_routes  # noqa: E402
from domain.scopes import DATA_TENANT_ID  # noqa: E402
from domain.settings import get_settings  # noqa: E402
from ingest.prices.eod_loader import ingest_prices  # noqa: E402
from tests.agent_tools.test_hosted import _session_call  # noqa: E402
from tests.agent_tools.test_server import TOOLS  # noqa: E402
from tests.app.test_auth_gate import OPEN, _fill, _operations  # noqa: E402
from tests.auth_fixtures import sign_out  # noqa: E402
from tests.workspaces import new_workspace  # noqa: E402
from workbench import ingest_jobs  # noqa: E402

ORIGIN = "http://127.0.0.1:8000"
MCP_URL = f"{ORIGIN}/mcp"
ISSUER = "http://127.0.0.1:8000/"
PASSWORD = "correct horse battery"
CLAUDE_CODE = "https://claude.ai/oauth/claude-code-client-metadata"
CLAUDE = "alphadeck-claude"
CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "oauth-test", "version": "0"},
    },
}
MCP_HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
_APP_OAUTH = Path(__file__).resolve().parents[2] / "app" / "oauth"


def _bars(asof: date, n: int) -> list[dict]:
    days: list[date] = []
    d = asof
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    days.reverse()
    return [
        {
            "d": day,
            "open": 10.0,
            "high": 10.5,
            "low": 9.5,
            "close": 10 + 0.01 * i,
            "volume": 500_000,
        }
        for i, day in enumerate(days)
    ]


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge


class World:
    """The fixture's handles plus the flow helpers every test uses."""

    def __init__(self, db, users, pats, cards, server):
        self.db = db
        self.users = users
        self.pats = pats
        self.cards = cards
        self.server = server
        self.asx = TestClient(
            Starlette(routes=server.routes), base_url=ORIGIN, raise_server_exceptions=False
        )
        self.sessions: dict[str, TestClient] = {}

    # --- the API, as a browser or a bearer -------------------------------------------------------------------

    def session(self, who: str) -> TestClient:
        if who not in self.sessions:
            c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
            r = c.post("/auth/login", json={"email": f"{who}@oauth.test", "password": PASSWORD})
            assert r.status_code == 200, r.text
            self.sessions[who] = c
        return self.sessions[who]

    def api(self, method: str, path: str, token: str, **kw):
        c = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
        return c.request(method, path, headers={"authorization": f"Bearer {token}"}, **kw)

    # --- the authorization server -----------------------------------------------------------------------------

    def register(self, **body):
        doc = {
            "redirect_uris": [CLAUDE_CALLBACK],
            "client_name": "Research Desk",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            **body,
        }
        return self.asx.post("/register", json=doc)

    def authorize(
        self,
        client_id: str,
        redirect_uri: str | None,
        challenge: str,
        *,
        scope: str | None = None,
        resource: str | None = MCP_URL,
        state: str = "st-1",
    ):
        params = {
            "response_type": "code",
            "client_id": client_id,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if redirect_uri is not None:
            params["redirect_uri"] = redirect_uri
        if scope is not None:
            params["scope"] = scope
        if resource is not None:
            params["resource"] = resource
        return self.asx.get("/authorize", params=params, follow_redirects=False)

    def consent_id(self, client_id, redirect_uri, challenge, **kw) -> str:
        r = self.authorize(client_id, redirect_uri, challenge, **kw)
        assert r.status_code == 302, r.text
        loc = r.headers["location"]
        m = re.fullmatch(re.escape(ORIGIN) + r"/oauth/consent/([0-9a-f-]{36})", loc)
        assert m, loc
        return m.group(1)

    def decide(self, who: str, consent_id: str, decision: str = "allow"):
        return self.session(who).post(f"/oauth/consent/{consent_id}", json={"decision": decision})

    def token(self, **form):
        return self.asx.post("/token", data=form)

    def connect(
        self,
        who: str,
        *,
        client_id: str = CLAUDE_CODE,
        redirect_uri: str = "http://localhost:54321/callback",
        scope: str | None = None,
        secret: str | None = None,
    ) -> dict:
        """The whole flow for one user: authorize -> allow -> token. Returns the token reply."""
        verifier, challenge = _pkce()
        cid = self.consent_id(client_id, redirect_uri, challenge, scope=scope)
        d = self.decide(who, cid)
        assert d.status_code == 200, d.text
        q = parse_qs(urlsplit(d.json()["redirect_to"]).query)
        form = {
            "grant_type": "authorization_code",
            "code": q["code"][0],
            "code_verifier": verifier,
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "resource": MCP_URL,
        }
        if secret:
            form["client_secret"] = secret
        r = self.token(**form)
        assert r.status_code == 200, r.text
        return r.json()

    # --- /mcp ------------------------------------------------------------------------------------------------

    def mcp(self, token: str, body):
        async def go():
            hosted = build_hosted(app, public_origin=ORIGIN)
            asgi = Starlette(routes=hosted.routes)
            async with hosted.lifespan():
                return await _session_call(asgi, token, body)

        return asyncio.run(go())

    def mcp_status(self, token: str) -> int:
        hosted = build_hosted(app, public_origin=ORIGIN)
        c = TestClient(
            Starlette(routes=hosted.routes, lifespan=lambda _: hosted.lifespan()), base_url=ORIGIN
        )
        with c:
            return c.post(
                "/mcp", json=INIT, headers={**MCP_HEADERS, "authorization": f"Bearer {token}"}
            ).status_code


@pytest.fixture
def ow(db, pinned_today, monkeypatch):
    """Users A, B (plain) and S (steward) on three workspaces; a card each for A and B on one synthetic security;
    the authorization server over the test's connection; ALPHADECK_PUBLIC_ORIGIN set for the consent routes.
    """
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    ingest_jobs.reset_state()
    monkeypatch.setattr(ingest_jobs, "_DEFAULT_EXECUTOR", lambda job: None)
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    monkeypatch.setenv("ALPHADECK_PUBLIC_ORIGIN", ORIGIN)
    get_settings.cache_clear()
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")  # cascades to every oauth table but oauth_client
        cur.execute("DELETE FROM oauth_client WHERE registration = 'dcr'")
        cur.execute("UPDATE oauth_client SET disabled_at = NULL")
    auth.reset_throttle()

    w_a, w_b, w_s = (new_workspace(db, f"oauth {x}") for x in ("A", "B", "S"))
    users = {}
    for key, ws, role in (("a", w_a, "user"), ("b", w_b, "user"), ("s", w_s, "super_admin")):
        users[key] = auth.create_user(
            db,
            email=f"{key}@oauth.test",
            display_name=key.upper(),
            password=PASSWORD,
            role=role,
            workspace_id=ws,
        )
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, "TK01", "0009990001", "2026-01-01"),
        )
    ingest_prices(db, sid, _bars(pinned_today, 260))
    pats = {k: auth.create_api_token(db, uid, "oauth test")[1] for k, uid in users.items()}
    db.commit()

    cards = {}
    rest = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    for key, label in (("a", "Thesis 01"), ("b", "Thesis 02")):
        name = f"{label} {uuid.uuid4().hex[:8]}"
        r = rest.post(
            "/workbench/theses",
            headers={"authorization": f"Bearer {pats[key]}"},
            json={
                "id": None,
                "name": name,
                "narrative": f"{name}: a synthetic narrative for the OAuth suite",
                "basket": [{"ticker": "TK01", "role": "the name", "security_id": str(sid)}],
                "segments": [],
            },
        )
        assert r.status_code == 200, r.text
        cards[key] = {"id": r.json()["id"], "name": name}
    db.commit()

    @contextmanager
    def same_conn():
        yield db

    server = build_oauth_routes(ORIGIN, AlphaDeckAuthProvider(same_conn, public_origin=ORIGIN))
    app.state.oauth = server
    yield World(db, users, pats, cards, server)
    del app.state.oauth
    app.dependency_overrides.clear()
    ingest_jobs.reset_state()
    auth.reset_throttle()
    get_settings.cache_clear()


def _count(db, table: str) -> int:
    with db.cursor() as cur:
        cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 - a fixed table name
        return cur.fetchone()["n"]


# --- 1. the happy path, as claude.ai does it -------------------------------------------------------------------


def test_the_claude_ai_flow_end_to_end(ow):
    reg = ow.register()  # no auth method: the SDK issues a secret (client_secret_post)
    assert reg.status_code == 201, reg.text
    client = reg.json()
    assert client["token_endpoint_auth_method"] == "client_secret_post" and client["client_secret"]
    with ow.db.cursor() as cur:
        cur.execute(
            "SELECT client_secret_hash, registration, metadata FROM oauth_client WHERE client_id = %s",
            (client["client_id"],),
        )
        row = cur.fetchone()
    assert row["registration"] == "dcr"
    assert row["client_secret_hash"] == store.hash_secret(client["client_secret"])
    assert "client_secret" not in row["metadata"]  # the secret is never stored in the clear

    verifier, challenge = _pkce()
    cid = ow.consent_id(client["client_id"], CLAUDE_CALLBACK, challenge, state="xyz-state")
    page = ow.session("a").get(f"/oauth/consent/{cid}")
    assert page.status_code == 200, page.text
    body = page.json()
    assert body["client_name"] == "Research Desk" and body["client_known"] is False
    assert body["scopes"] == [
        {"name": "read", "label": store.SCOPE_LABELS["read"], "grantable": True}
    ]
    assert body["redirect_host"] == "claude.ai"

    d = ow.decide("a", cid)
    assert d.status_code == 200, d.text
    target = urlsplit(d.json()["redirect_to"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == CLAUDE_CALLBACK
    q = parse_qs(target.query)
    assert q["state"] == ["xyz-state"] and q["iss"] == [ISSUER]
    form = {
        "grant_type": "authorization_code",
        "code": q["code"][0],
        "code_verifier": verifier,
        "client_id": client["client_id"],
        "client_secret": client["client_secret"],
        "redirect_uri": CLAUDE_CALLBACK,
        "resource": MCP_URL,
    }
    wrong_secret = ow.token(**{**form, "client_secret": "not-the-secret"})
    assert wrong_secret.status_code == 401 and wrong_secret.json()["error"] == "invalid_client"
    tok = ow.token(**form)
    assert tok.status_code == 200, tok.text
    t = tok.json()
    assert t["token_type"] == "Bearer" and t["expires_in"] == 3600 and t["scope"] == "read"
    assert t["access_token"] and t["refresh_token"]
    with ow.db.cursor() as cur:  # only hashes in the tables
        cur.execute("SELECT token_hash FROM oauth_access_token")
        assert [r["token_hash"] for r in cur.fetchall()] == [store.hash_secret(t["access_token"])]

    async def body_fn(session, init):
        tools = await session.list_tools()
        listed = await session.call_tool("list_theses", {})
        return tools, listed

    tools, listed = ow.mcp(t["access_token"], body_fn)
    assert {x.name for x in tools.tools} == TOOLS and len(tools.tools) == 14
    assert [c["id"] for c in listed.structured_content["cards"]] == [ow.cards["a"]["id"]]
    me = ow.api("GET", "/auth/me", t["access_token"])
    assert me.status_code == 200
    assert me.json()["email"] == "a@oauth.test" and me.json()["role"] == "user"
    assert me.json()["token"] == {
        "principal": "oauth",
        "client_id": client["client_id"],
        "scopes": ["read"],
    }
    # a browser session's /auth/me carries no token object
    assert "token" not in ow.session("a").get("/auth/me").json()


# --- 2. Claude Code's shape --------------------------------------------------------------------------------------


def test_claude_codes_static_client_any_loopback_port_no_secret(ow):
    t = ow.connect("a", redirect_uri="http://localhost:54321/callback")
    assert t["scope"] == "read"
    assert ow.mcp_status(t["access_token"]) == 200
    _, challenge = _pkce()
    other = ow.authorize(CLAUDE_CODE, "http://127.0.0.1:61000/callback", challenge)
    assert other.status_code == 302 and "/oauth/consent/" in other.headers["location"]
    bad = ow.authorize(CLAUDE_CODE, "http://localhost/other", challenge)
    assert (
        bad.status_code == 400 and bad.json()["error"] == "invalid_request"
    )  # direct, no redirect
    https_other = ow.authorize(CLAUDE_CODE, "https://localhost:54321/callback", challenge)
    assert https_other.status_code == 400


# --- 3. PKCE and binding -----------------------------------------------------------------------------------------


def test_pkce_single_use_redirect_and_resource_binding(ow):
    redirect = "http://localhost:50001/callback"
    verifier, challenge = _pkce()
    cid = ow.consent_id(CLAUDE_CODE, redirect, challenge)
    code = parse_qs(urlsplit(ow.decide("a", cid).json()["redirect_to"]).query)["code"][0]
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "code_verifier": verifier,
        "client_id": CLAUDE_CODE,
        "redirect_uri": redirect,
        "resource": MCP_URL,
    }
    wrong = ow.token(**{**form, "code_verifier": "x" * 50})
    assert wrong.status_code == 400 and wrong.json()["error"] == "invalid_grant"
    moved = ow.token(**{**form, "redirect_uri": "http://localhost:50002/callback"})
    assert moved.status_code == 400 and moved.json()["error"] == "invalid_request"
    first = ow.token(**form)
    assert first.status_code == 200, first.text
    access = first.json()["access_token"]
    assert ow.mcp_status(access) == 200
    again = ow.token(**form)
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert ow.mcp_status(access) == 401  # the replayed code revoked the grant
    with ow.db.cursor() as cur:
        cur.execute("SELECT revoked_at FROM oauth_grant")
        assert all(r["revoked_at"] is not None for r in cur.fetchall())

    _, challenge2 = _pkce()
    foreign = ow.authorize(CLAUDE_CODE, redirect, challenge2, resource="https://evil.example/mcp")
    assert foreign.status_code == 302
    q = parse_qs(urlsplit(foreign.headers["location"]).query)
    assert q["error"] == ["invalid_target"] and q["state"] == ["st-1"]

    v3, c3 = _pkce()
    cid3 = ow.consent_id(
        CLAUDE_CODE, redirect, c3, resource=None
    )  # no resource: bound to ours anyway
    code3 = parse_qs(urlsplit(ow.decide("a", cid3).json()["redirect_to"]).query)["code"][0]
    t3 = ow.token(
        grant_type="authorization_code",
        code=code3,
        code_verifier=v3,
        client_id=CLAUDE_CODE,
        redirect_uri=redirect,
    )
    assert t3.status_code == 200, t3.text
    with ow.db.cursor() as cur:
        cur.execute(
            "SELECT resource FROM oauth_access_token WHERE token_hash = %s",
            (store.hash_secret(t3.json()["access_token"]),),
        )
        assert cur.fetchone()["resource"] == MCP_URL
    assert ow.mcp_status(t3.json()["access_token"]) == 200


# --- 4. refresh ------------------------------------------------------------------------------------------------


def test_refresh_rotates_and_a_replay_revokes_the_grant(ow):
    t = ow.connect("a")
    r1 = ow.token(
        grant_type="refresh_token", refresh_token=t["refresh_token"], client_id=CLAUDE_CODE
    )
    assert r1.status_code == 200, r1.text
    n = r1.json()
    assert n["refresh_token"] != t["refresh_token"] and n["access_token"] != t["access_token"]
    assert n["expires_in"] == 3600 and n["scope"] == "read"
    with ow.db.cursor() as cur:
        cur.execute(
            "SELECT rotated_to FROM oauth_refresh_token WHERE token_hash = %s",
            (store.hash_secret(t["refresh_token"]),),
        )
        assert cur.fetchone()["rotated_to"] == store.hash_secret(n["refresh_token"])
    assert ow.mcp_status(n["access_token"]) == 200
    outside = ow.token(
        grant_type="refresh_token",
        refresh_token=n["refresh_token"],
        client_id=CLAUDE_CODE,
        scope="steward",
    )
    assert outside.status_code == 400 and outside.json()["error"] == "invalid_scope"
    replay = ow.token(
        grant_type="refresh_token", refresh_token=t["refresh_token"], client_id=CLAUDE_CODE
    )
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    for access in (t["access_token"], n["access_token"]):
        assert ow.mcp_status(access) == 401
        assert ow.api("GET", "/theses", access).status_code == 401
    dead = ow.token(
        grant_type="refresh_token", refresh_token=n["refresh_token"], client_id=CLAUDE_CODE
    )
    assert dead.status_code == 400 and dead.json()["error"] == "invalid_grant"


def test_an_expired_refresh_token_is_invalid_grant(ow):
    t = ow.connect("b")
    with ow.db.cursor() as cur:
        cur.execute("UPDATE oauth_refresh_token SET expires_at = now() - interval '1 second'")
    ow.db.commit()
    r = ow.token(
        grant_type="refresh_token", refresh_token=t["refresh_token"], client_id=CLAUDE_CODE
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


# --- 5. consent is the session's -------------------------------------------------------------------------------


def test_only_a_browser_session_answers_a_consent(ow, monkeypatch):
    oauth_token = ow.connect("a")["access_token"]
    monkeypatch.setenv("ALPHADECK_SYSTEM_TOKEN", "system-token-for-the-oauth-suite")
    get_settings.cache_clear()
    _, challenge = _pkce()
    cid = ow.consent_id(CLAUDE_CODE, "http://localhost:5/callback", challenge)
    for bearer in (ow.pats["a"], oauth_token, "system-token-for-the-oauth-suite"):
        assert ow.api("GET", f"/oauth/consent/{cid}", bearer).status_code == 403
        post = ow.api("POST", f"/oauth/consent/{cid}", bearer, json={"decision": "allow"})
        assert post.status_code == 403
    with ow.db.cursor() as cur:
        cur.execute("SELECT decided_at FROM oauth_consent WHERE id = %s", (cid,))
        assert cur.fetchone()["decided_at"] is None


def test_whoever_signs_in_decides_for_their_own_account(ow):
    verifier, challenge = _pkce()
    redirect = "http://localhost:7000/callback"
    cid = ow.consent_id(CLAUDE_CODE, redirect, challenge)  # "A's" request, answered by B
    d = ow.decide("b", cid)
    assert d.status_code == 200
    code = parse_qs(urlsplit(d.json()["redirect_to"]).query)["code"][0]
    t = ow.token(
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        client_id=CLAUDE_CODE,
        redirect_uri=redirect,
    ).json()
    with ow.db.cursor() as cur:
        cur.execute("SELECT user_id FROM oauth_consent WHERE id = %s", (cid,))
        assert cur.fetchone()["user_id"] == ow.users["b"]
        cur.execute("SELECT user_id FROM oauth_grant")
        assert [r["user_id"] for r in cur.fetchall()] == [ow.users["b"]]
    listed = ow.api("GET", "/theses", t["access_token"]).json()
    assert [c["id"] for c in listed] == [ow.cards["b"]["id"]]
    assert ow.cards["a"]["name"] not in json.dumps(listed)


def test_expired_decided_and_denied_consents(ow):
    _, challenge = _pkce()
    redirect = "http://localhost:7001/callback"
    expired = ow.consent_id(CLAUDE_CODE, redirect, challenge)
    with ow.db.cursor() as cur:
        cur.execute(
            "UPDATE oauth_consent SET expires_at = now() - interval '1 second' WHERE id = %s",
            (expired,),
        )
    ow.db.commit()
    assert ow.session("a").get(f"/oauth/consent/{expired}").status_code == 410
    assert ow.decide("a", expired).status_code == 410
    assert ow.session("a").get(f"/oauth/consent/{uuid.uuid4()}").status_code == 404

    twice = ow.consent_id(CLAUDE_CODE, redirect, challenge)
    assert ow.decide("a", twice).status_code == 200
    assert ow.decide("a", twice).status_code == 409
    assert ow.session("a").get(f"/oauth/consent/{twice}").status_code == 410

    grants = _count(ow.db, "oauth_grant")
    denied = ow.consent_id(CLAUDE_CODE, redirect, challenge, state="deny-state")
    d = ow.decide("b", denied, "deny")
    assert d.status_code == 200
    q = parse_qs(urlsplit(d.json()["redirect_to"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["deny-state"] and "code" not in q
    assert _count(ow.db, "oauth_grant") == grants  # no grant for a Deny
    with ow.db.cursor() as cur:
        cur.execute("SELECT decision, user_id FROM oauth_consent WHERE id = %s", (denied,))
        row = cur.fetchone()
    assert row["decision"] == "deny" and row["user_id"] == ow.users["b"]


# --- 6. scopes cap the role -----------------------------------------------------------------------------------


def test_a_plain_user_cannot_grant_steward(ow):
    _, challenge = _pkce()
    cid = ow.consent_id(CLAUDE, CLAUDE_CALLBACK, challenge, scope="read steward")
    page = ow.session("a").get(f"/oauth/consent/{cid}").json()
    assert page["client_name"] == "Claude" and page["client_known"] is True
    assert [(s["name"], s["grantable"]) for s in page["scopes"]] == [
        ("read", True),
        ("steward", False),
    ]
    t = ow.connect("a", client_id=CLAUDE, redirect_uri=CLAUDE_CALLBACK, scope="read steward")
    assert t["scope"] == "read"


def test_the_stewards_scopes_decide_the_admin_role(ow):
    page_cid = ow.consent_id(CLAUDE, CLAUDE_CALLBACK, _pkce()[1], scope="read steward")
    page = ow.session("s").get(f"/oauth/consent/{page_cid}").json()
    assert all(s["grantable"] for s in page["scopes"])
    full = ow.connect("s", client_id=CLAUDE, redirect_uri=CLAUDE_CALLBACK, scope="read steward")
    assert full["scope"] == "read steward"
    read_only = ow.connect("s", client_id=CLAUDE_CODE, scope="read")
    assert read_only["scope"] == "read"
    assert ow.api("GET", "/admin/status", read_only["access_token"]).status_code == 403
    assert ow.api("GET", "/auth/me", read_only["access_token"]).json()["role"] == "user"
    assert ow.api("GET", "/admin/status", full["access_token"]).status_code == 200
    assert ow.api("GET", "/auth/me", full["access_token"]).json()["role"] == "super_admin"
    assert ow.session("s").get("/admin/status").status_code == 200  # the session is unaffected

    async def names(session, init):
        return sorted(t.name for t in (await session.list_tools()).tools)

    assert len(ow.mcp(full["access_token"], names)) == 14


# --- 7. read-only tokens cannot write ---------------------------------------------------------------------------


def test_a_read_token_is_refused_on_every_non_get_operation(ow):
    token = ow.connect("a")["access_token"]
    wrong = []
    n = 0
    for method, path, op in _operations():
        if method in ("GET", "HEAD") or (method, path) in OPEN:
            continue  # POST /auth/login is open by nature: no credential is read there
        n += 1
        r = ow.api(method, _fill(path, op), token, json={})
        if r.status_code != 403:
            wrong.append(f"{method} {path} -> {r.status_code}")
    assert n > 50
    assert wrong == []
    assert ow.api("POST", "/workbench/theses", token, json={}).status_code == 403
    assert ow.api("DELETE", f"/account/tokens/{uuid.uuid4()}", token).status_code == 403
    # the reads answer as A, and the account is not the app's to read either
    assert [c["id"] for c in ow.api("GET", "/theses", token).json()] == [ow.cards["a"]["id"]]
    assert ow.api("GET", "/clock", token).status_code == 200
    assert ow.api("GET", "/account/tokens", token).status_code == 403
    assert ow.api("GET", "/account/connections", token).status_code == 403


# --- 8. connected apps -----------------------------------------------------------------------------------------


def test_connected_apps_list_and_disconnect(ow):
    t = ow.connect("a")
    a_list = ow.session("a").get("/account/connections")
    assert a_list.status_code == 200
    rows = a_list.json()["connections"]
    assert len(rows) == 1
    row = rows[0]
    assert row["client_name"] == "Claude Code" and row["client_known"] is True
    assert row["scopes"] == ["read"] and row["revoked_at"] is None and row["created_at"]
    assert ow.session("b").get("/account/connections").json() == {"connections": []}
    assert ow.session("b").delete(f"/account/connections/{row['id']}").status_code == 404
    assert ow.mcp_status(t["access_token"]) == 200
    assert ow.session("a").delete(f"/account/connections/{row['id']}").status_code == 204
    assert ow.mcp_status(t["access_token"]) == 401
    r = ow.token(
        grant_type="refresh_token", refresh_token=t["refresh_token"], client_id=CLAUDE_CODE
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
    assert ow.session("a").delete(f"/account/connections/{row['id']}").status_code == 204
    after = ow.session("a").get("/account/connections").json()["connections"]
    assert len(after) == 1 and after[0]["revoked_at"] is not None  # kept, never deleted
    # a self-registered app is not "known"
    reg = ow.register(token_endpoint_auth_method="none").json()
    ow.connect("a", client_id=reg["client_id"], redirect_uri=CLAUDE_CALLBACK)
    live = ow.session("a").get("/account/connections").json()["connections"][0]
    assert live["client_name"] == "Research Desk" and live["client_known"] is False


def test_agents_reports_the_oauth_sign_in(ow):
    agents = ow.session("a").get("/account/agents").json()
    assert agents["hosted"] is True and agents["oauth"] is True
    assert agents["connector_url"] == MCP_URL


# --- 9. the documents --------------------------------------------------------------------------------------------


def test_the_metadata_document_agrees_with_the_protected_resource_document(ow):
    meta = ow.asx.get("/.well-known/oauth-authorization-server")
    assert meta.status_code == 200
    doc = meta.json()
    hosted = build_hosted(app, public_origin=ORIGIN)
    prm = TestClient(Starlette(routes=hosted.routes), base_url=ORIGIN).get(
        "/.well-known/oauth-protected-resource/mcp"
    )
    assert prm.status_code == 200
    assert doc["issuer"] == prm.json()["authorization_servers"][0] == ISSUER
    assert doc["code_challenge_methods_supported"] == ["S256"]
    assert {"none", "client_secret_post"} <= set(doc["token_endpoint_auth_methods_supported"])
    assert "none" in doc["revocation_endpoint_auth_methods_supported"]
    assert doc["authorization_response_iss_parameter_supported"] is True
    assert doc["scopes_supported"] == ["read", "steward"]
    assert doc["authorization_endpoint"] == f"{ORIGIN}/authorize"
    assert doc["token_endpoint"] == f"{ORIGIN}/token"
    assert doc["registration_endpoint"] == f"{ORIGIN}/register"
    assert doc["revocation_endpoint"] == f"{ORIGIN}/revoke"
    as_json = ow.asx.post(
        "/token", json={"grant_type": "authorization_code", "client_id": CLAUDE_CODE}
    )
    assert 400 <= as_json.status_code < 500 and "error" in as_json.json()  # form only, never a 500


def test_revoke_endpoint_revokes_an_access_token_and_a_refresh_token_revokes_the_grant(ow):
    t = ow.connect("a")
    r = ow.asx.post("/revoke", data={"token": t["access_token"], "client_id": CLAUDE_CODE})
    assert r.status_code == 200
    assert ow.mcp_status(t["access_token"]) == 401
    t2 = ow.connect("b")
    r2 = ow.asx.post(
        "/revoke",
        data={
            "token": t2["refresh_token"],
            "token_type_hint": "refresh_token",
            "client_id": CLAUDE_CODE,
        },
    )
    assert r2.status_code == 200
    assert ow.mcp_status(t2["access_token"]) == 401
    with ow.db.cursor() as cur:
        cur.execute("SELECT revoked_at FROM oauth_grant WHERE user_id = %s", (ow.users["b"],))
        assert cur.fetchone()["revoked_at"] is not None


# --- 10. registration hygiene + a disabled client ------------------------------------------------------------------


def test_registration_hygiene(ow):
    evil = ow.register(redirect_uris=["http://evil.example/cb"])
    assert evil.status_code == 400 and evil.json()["error"] == "invalid_redirect_uri"
    loopback = ow.register(
        redirect_uris=["http://127.0.0.1:3000/cb"], token_endpoint_auth_method="none"
    )
    assert loopback.status_code == 201
    long_name = ow.register(client_name="R" * 200)
    with ow.db.cursor() as cur:
        cur.execute(
            "SELECT client_name FROM oauth_client WHERE client_id = %s",
            (long_name.json()["client_id"],),
        )
        assert cur.fetchone()["client_name"] == "R" * 60
    ok = 2  # two successful registrations so far
    while ok < 20:
        assert ow.register().status_code == 201
        ok += 1
    over = ow.register()
    assert over.status_code == 429
    assert _count(ow.db, "oauth_client") == 2 + 20  # the two static clients + twenty registrations


def test_a_disabled_client_is_refused(ow, db):
    from pipeline import users as users_cli

    t = ow.connect("a")
    out: list[str] = []
    assert (
        users_cli.run(["oauth-client", "disable", "--client-id", CLAUDE_CODE], db, emit=out.append)
        == 0
    )
    listed: list[str] = []
    users_cli.run(["oauth-client", "list"], db, emit=listed.append)
    assert any(line.startswith(CLAUDE_CODE) and "disabled" in line for line in listed)
    _, challenge = _pkce()
    r = ow.authorize(CLAUDE_CODE, "http://localhost:5/callback", challenge)
    assert r.status_code == 400 and "location" not in r.headers  # direct error, no redirect
    assert r.json()["error"] == "invalid_request"  # the SDK's answer for a client it does not know
    tok = ow.token(
        grant_type="refresh_token", refresh_token=t["refresh_token"], client_id=CLAUDE_CODE
    )
    assert tok.status_code == 401 and tok.json()["error"] == "invalid_client"
    assert ow.mcp_status(t["access_token"]) == 401
    assert (
        users_cli.run(["oauth-client", "disable", "--client-id", "nope"], db, emit=out.append) == 1
    )


def test_admin_status_counts_the_clients(ow):
    ow.register()
    status = ow.session("s").get("/admin/status")
    assert status.status_code == 200
    assert status.json()["oauth_clients"] == {"static": 2, "dcr": 1}


# --- 11. the gate: tests/app/test_auth_gate.py lists the five AS routes and the two consent routes ------------


# --- 12. no tenant under app/oauth ----------------------------------------------------------------------------


@pytest.mark.parametrize("word", ["WORKSPACE_ANY", "DEFAULT_TENANT_ID", "tenant_id"])
def test_no_tenant_is_named_under_app_oauth(word):
    offenders = [
        p.name for p in sorted(_APP_OAUTH.rglob("*.py")) if word in p.read_text(encoding="utf-8")
    ]
    assert offenders == []
