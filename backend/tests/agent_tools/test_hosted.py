"""The hosted endpoint (``agent_tools/hosted.py``, docs/AGENT_TOOLS.md §Hosted endpoint) against the REAL API.

A throwaway Starlette app carries ``build_hosted(app, ...)``'s two routes; its upstream is the real ``app.main.app``
with the suite's ``get_conn`` override (the test's connection) and the real login door (no signed-in override).
The official SDK client drives it in-process: ``mcp.client.streamable_http.streamable_http_client`` with an
``httpx2.AsyncClient`` over ``httpx2.ASGITransport`` (the SDK's own HTTP stack; 2.3.0 has no in-process HTTP test
helper, its ``Client(server)`` shortcut skips the HTTP layer this file is about).

Two real users A and B on two workspaces, one card each on the same synthetic security, one personal access token
each, plus a steward S: every reply B gets is B's workspace only, concurrently interleaved with A's calls too.

DB-backed (the shared ``db`` fixture; SKIPS when Postgres is unreachable, which is not a pass).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

pytest.importorskip("mcp")

import httpx2  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.client import Client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from starlette.applications import Starlette  # noqa: E402

from agent_tools import server as server_mod  # noqa: E402
from agent_tools.client import ApiClient  # noqa: E402
from agent_tools.hosted import allowed_hosts, build_hosted, connector_url  # noqa: E402
from app import auth  # noqa: E402
from app.deps import get_conn  # noqa: E402
from app.main import app  # noqa: E402
from domain.scopes import DATA_TENANT_ID  # noqa: E402
from domain.settings import get_settings  # noqa: E402
from ingest.prices.eod_loader import ingest_prices  # noqa: E402
from tests.agent_tools.fake_api import FakeApi  # noqa: E402
from tests.agent_tools.test_server import TOOLS  # noqa: E402
from tests.auth_fixtures import sign_out  # noqa: E402
from tests.workspaces import new_workspace  # noqa: E402
from workbench import ingest_jobs  # noqa: E402

ORIGIN = "http://127.0.0.1:8000"
MCP_URL = f"{ORIGIN}/mcp"
PRM_PATH = "/.well-known/oauth-protected-resource/mcp"
PASSWORD = "correct horse battery"
INIT = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "hosted-test", "version": "0"},
    },
}
POST_HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream"}
# 22:30 ET on a November weekday (EST = UTC-5) is inside the nightly window; 10:00 ET is not
IN_WINDOW = datetime(2026, 11, 10, 3, 30, tzinfo=timezone.utc)
OUT_OF_WINDOW = datetime(2026, 11, 10, 15, 0, tzinfo=timezone.utc)


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
            "open": 10 + 0.05 * i - 0.1,
            "high": 10 + 0.05 * i + 0.3,
            "low": 10 + 0.05 * i - 0.3,
            "close": 10 + 0.05 * i,
            "volume": 500_000 + 100 * i,
        }
        for i, day in enumerate(days)
    ]


@pytest.fixture
def world(db, pinned_today, monkeypatch):
    """Users A, B (plain) and S (steward) on three workspaces; one card each for A and B on one synthetic
    security; a PAT each. Cards are promoted through the API with each user's own bearer."""
    sign_out(app)
    app.dependency_overrides[get_conn] = lambda: db
    ingest_jobs.reset_state()
    monkeypatch.setattr(ingest_jobs, "_DEFAULT_EXECUTOR", lambda job: None)  # no real ingest thread
    monkeypatch.delenv("ALPHADECK_SYSTEM_TOKEN", raising=False)
    get_settings.cache_clear()
    with db.cursor() as cur:
        cur.execute("TRUNCATE app_user CASCADE")
    auth.reset_throttle()

    w_a, w_b, w_s = (new_workspace(db, f"hosted {x}") for x in ("A", "B", "S"))
    users = {}
    for key, ws, role in (("a", w_a, "user"), ("b", w_b, "user"), ("s", w_s, "super_admin")):
        users[key] = auth.create_user(
            db,
            email=f"{key}@hosted.test",
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
    tokens = {k: auth.create_api_token(db, uid, "hosted test") for k, uid in users.items()}
    db.commit()

    cards = {}
    rest = TestClient(app, base_url="https://testserver", raise_server_exceptions=False)
    for key, label in (("a", "Thesis 01"), ("b", "Thesis 02")):
        name = f"{label} {uuid.uuid4().hex[:8]}"
        r = rest.post(
            "/workbench/theses",
            headers={"authorization": f"Bearer {tokens[key][1]}"},
            json={
                "id": None,
                "name": name,
                "narrative": f"{name}: a synthetic narrative for the hosted endpoint",
                "basket": [{"ticker": "TK01", "role": "the name", "security_id": str(sid)}],
                "segments": [],
            },
        )
        assert r.status_code == 200, r.text
        cards[key] = {"id": r.json()["id"], "name": name}
    db.commit()

    yield {
        "users": users,
        "tokens": {k: t for k, (_, t) in tokens.items()},
        "token_ids": {k: tid for k, (tid, _) in tokens.items()},
        "cards": cards,
        "sid": sid,
        "asof": pinned_today,
    }
    app.dependency_overrides.clear()
    ingest_jobs.reset_state()
    auth.reset_throttle()
    get_settings.cache_clear()


def _hosted(api=app, **kwargs):
    hosted = build_hosted(api, public_origin=ORIGIN, **kwargs)
    return hosted, Starlette(routes=hosted.routes)


def _http(asgi, headers: dict[str, str] | None = None) -> httpx2.AsyncClient:
    return httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=asgi),
        base_url=ORIGIN,
        headers=headers or {},
        timeout=60,
        follow_redirects=False,
    )


async def _session_call(asgi, token: str, body):
    async with _http(asgi, {"authorization": f"Bearer {token}"}) as http:
        async with streamable_http_client(MCP_URL, http_client=http) as (read, write):
            async with ClientSession(read, write) as session:
                init = await session.initialize()
                return await body(session, init)


def run_as(token: str, body, **hosted_kwargs):
    async def go():
        hosted, asgi = _hosted(**hosted_kwargs)
        async with hosted.lifespan():
            return await _session_call(asgi, token, body)

    return asyncio.run(go())


def _raw(method: str, path: str, *, headers=None, body=None, **hosted_kwargs):
    async def go():
        hosted, asgi = _hosted(**hosted_kwargs)
        async with hosted.lifespan():
            async with _http(asgi) as http:
                return await http.request(
                    method,
                    path,
                    headers={**POST_HEADERS, **(headers or {})},
                    content=json.dumps(body).encode() if body is not None else None,
                )

    return asyncio.run(go())


def _text(result) -> str:
    return result.content[0].text


# --- 8.1 who is refused, and how --------------------------------------------------------------------------------

UNAUTHORIZED = {"error": "invalid_token", "error_description": "Authentication required"}


def _assert_401(r) -> None:
    assert r.status_code == 401, r.text
    assert r.json() == UNAUTHORIZED  # the SDK's fixed body: never exception text
    assert "Traceback" not in r.text
    challenge = r.headers["www-authenticate"]
    assert challenge.startswith("Bearer ")
    assert f'resource_metadata="{ORIGIN}{PRM_PATH}"' in challenge


def test_no_bearer_is_401_with_the_resource_metadata_challenge(world):
    _assert_401(_raw("POST", "/mcp", body=INIT))


def test_a_session_cookie_alone_is_401(world, db):
    cookie = auth.create_session(db, world["users"]["b"])
    db.commit()
    _assert_401(_raw("POST", "/mcp", body=INIT, headers={"cookie": f"alphadeck_session={cookie}"}))


def test_the_system_token_is_401(world, monkeypatch):
    monkeypatch.setenv("ALPHADECK_SYSTEM_TOKEN", "system-token-for-the-hosted-suite")
    get_settings.cache_clear()
    r = _raw(
        "POST",
        "/mcp",
        body=INIT,
        headers={"authorization": "Bearer system-token-for-the-hosted-suite"},
    )
    _assert_401(r)


def test_a_revoked_pat_is_401(world, db):
    assert auth.revoke_api_token(db, world["token_ids"]["b"])
    db.commit()
    _assert_401(
        _raw(
            "POST",
            "/mcp",
            body=INIT,
            headers={"authorization": f"Bearer {world['tokens']['b']}"},
        )
    )


def test_a_disabled_users_pat_is_401(world, db):
    auth.set_disabled(db, world["users"]["b"], True)
    db.commit()
    _assert_401(
        _raw(
            "POST",
            "/mcp",
            body=INIT,
            headers={"authorization": f"Bearer {world['tokens']['b']}"},
        )
    )


def test_a_garbage_bearer_is_401(world):
    _assert_401(
        _raw("POST", "/mcp", body=INIT, headers={"authorization": "Bearer not-a-real-token"})
    )


# --- 8.2 the protected-resource document --------------------------------------------------------------------------


def test_the_protected_resource_document(world):
    r = _raw("GET", PRM_PATH)
    assert r.status_code == 200, r.text
    doc = r.json()
    assert doc["resource"] == MCP_URL
    # The SDK types the issuer as a pydantic AnyHttpUrl, which spells a bare origin with a trailing slash.
    assert [s.rstrip("/") for s in doc["authorization_servers"]] == [ORIGIN]
    assert doc["scopes_supported"] == ["read", "steward"]


# --- 8.3 a user sees only their own workspace ------------------------------------------------------------------


def test_b_sees_only_b(world):
    a, b = world["cards"]["a"], world["cards"]["b"]

    async def body(session, init):
        tools = await session.list_tools()
        replies = [
            await session.call_tool("list_theses", {}),
            await session.call_tool("get_thesis", {"thesis": b["id"]}),
            await session.call_tool("get_call", {"thesis": b["id"]}),
            await session.call_tool("get_data_flags", {"thesis": b["id"]}),
            await session.call_tool("lookup_ticker", {"t": "TK01"}),
        ]
        foreign = await session.call_tool("get_thesis", {"thesis": a["id"]})
        return init, tools, replies, foreign

    init, tools, replies, foreign = run_as(world["tokens"]["b"], body)
    assert (init.server_info.name, init.server_info.version) == ("alphadeck", "2.4.0")
    assert {t.name for t in tools.tools} == TOOLS and len(tools.tools) == 14
    listed, thesis, call, flags, ticker = replies
    for r in replies:
        assert not r.is_error, _text(r)
        assert a["id"] not in _text(r) and a["name"] not in _text(r)
    assert [c["id"] for c in listed.structured_content["cards"]] == [b["id"]]
    assert thesis.structured_content["id"] == b["id"]
    assert (
        ticker.structured_content["cards"]["live_cards"] == 1
    )  # B's workspace: one live card, not two
    err = json.loads(_text(foreign))
    assert foreign.is_error and err["code"] == "unknown_thesis"
    assert a["id"] not in _text(foreign) and a["name"] not in _text(foreign)


# --- 8.4 interleaved callers never see each other's principal or data ------------------------------------------


def test_fifty_interleaved_calls_each_answer_their_own_caller(world):
    cards, tokens = world["cards"], world["tokens"]

    async def go():
        hosted, asgi = _hosted()
        async with hosted.lifespan():
            async with (
                _http(asgi, {"authorization": f"Bearer {tokens['a']}"}) as http_a,
                _http(asgi, {"authorization": f"Bearer {tokens['b']}"}) as http_b,
                streamable_http_client(MCP_URL, http_client=http_a) as (ra, wa),
                streamable_http_client(MCP_URL, http_client=http_b) as (rb, wb),
                ClientSession(ra, wa) as sa,
                ClientSession(rb, wb) as sb,
            ):
                await asyncio.gather(sa.initialize(), sb.initialize())
                plan = [("a", sa) if i % 2 == 0 else ("b", sb) for i in range(50)]

                async def one(who, session):
                    # get_thesis by id: the server resolves it against THIS caller's /theses
                    r = await session.call_tool("get_thesis", {"thesis": cards[who]["id"]})
                    return who, r

                return await asyncio.gather(*(one(who, s) for who, s in plan))

    results = asyncio.run(go())
    assert len(results) == 50
    for who, r in results:
        assert not r.is_error, _text(r)
        assert r.structured_content["id"] == cards[who]["id"]
        other = "b" if who == "a" else "a"
        assert cards[other]["id"] not in _text(r)


# --- 8.5 the steward lists the same tools in Phase 1 -----------------------------------------------------------


def test_the_steward_lists_the_same_fourteen_tools(world):
    async def names(session, init):
        return sorted(t.name for t in (await session.list_tools()).tools)

    assert run_as(world["tokens"]["s"], names) == run_as(world["tokens"]["b"], names)
    assert len(run_as(world["tokens"]["s"], names)) == 14


# --- 8.6 no redirect, no 5xx ------------------------------------------------------------------------------------


def test_post_get_and_delete_are_answered_directly(world):
    bearer = {"authorization": f"Bearer {world['tokens']['b']}"}
    post = _raw("POST", "/mcp", body=INIT, headers=bearer)
    assert post.status_code == 200, post.text
    assert "alphadeck" in post.text
    for method in ("GET", "DELETE"):
        r = _raw(method, "/mcp", headers={**bearer, "accept": "text/event-stream"})
        assert not 300 <= r.status_code < 400, f"{method} -> {r.status_code}"
        assert r.status_code < 500, f"{method} -> {r.status_code}"
    # stateless: no standalone event stream to open (the transport spec's 405), never an idle open GET
    assert _raw("GET", "/mcp", headers={**bearer, "accept": "text/event-stream"}).status_code == 405
    # unauthenticated GET still gets the challenge, not the 405
    assert _raw("GET", "/mcp", headers={"accept": "text/event-stream"}).status_code == 401
    # and without a credential: a 401, never a redirect to /mcp/
    assert _raw("POST", "/mcp", body=INIT).status_code == 401


def test_the_host_check_accepts_the_origin_and_refuses_another_host(world):
    bearer = {"authorization": f"Bearer {world['tokens']['b']}"}
    assert allowed_hosts(ORIGIN) == ["127.0.0.1", "127.0.0.1:*"]
    assert allowed_hosts("https://deck.example.com") == ["deck.example.com", "deck.example.com:*"]
    assert connector_url("https://deck.example.com") == "https://deck.example.com/mcp"
    bad = _raw("POST", "/mcp", body=INIT, headers={**bearer, "host": "evil.example.com"})
    assert bad.status_code == 421
    origin = _raw(
        "POST", "/mcp", body=INIT, headers={**bearer, "origin": "https://evil.example.com"}
    )
    assert origin.status_code == 403


# --- 8.7 the nightly-window note ---------------------------------------------------------------------------------


def test_a_hosted_reply_inside_the_window_carries_the_note_and_outside_does_not(world):
    async def clock(session, init):
        return await session.call_tool("get_clock", {})

    inside = run_as(world["tokens"]["b"], clock, clock=lambda: IN_WINDOW)
    outside = run_as(world["tokens"]["b"], clock, clock=lambda: OUT_OF_WINDOW)
    assert inside.structured_content["nightly_window"] is True
    assert json.loads(_text(inside))["nightly_window"] is True
    assert "nightly_window" not in outside.structured_content
    assert "nightly_window" not in _text(outside)

    async def failing(session, init):
        return await session.call_tool("get_thesis", {"thesis": "no such card"})

    err = run_as(world["tokens"]["b"], failing, clock=lambda: IN_WINDOW)
    assert err.is_error and json.loads(_text(err))["nightly_window"] is True


def test_a_stdio_reply_never_carries_the_note():
    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=FakeApi().transport())
        try:
            async with Client(server_mod.build_server(lambda: client)) as session:
                return await session.call_tool("get_clock", {})
        finally:
            await client.aclose()

    result = asyncio.run(go())
    assert "nightly_window" not in result.structured_content


# --- 8.8 every API read rode the caller's bearer ----------------------------------------------------------------


def test_every_api_request_the_mount_made_carried_the_callers_bearer(world, db):
    seen: list[tuple[str, str | None]] = []

    async def spy(scope, receive, send):
        if scope["type"] == "http":
            auth_header = dict(scope["headers"]).get(b"authorization")
            seen.append((scope["path"], auth_header.decode() if auth_header else None))
        await app(scope, receive, send)

    async def body(session, init):
        await session.call_tool("list_theses", {})
        await session.call_tool("get_call", {"thesis": world["cards"]["b"]["id"]})

    with db.cursor() as cur:
        cur.execute("UPDATE api_token SET last_used_at = NULL")
    db.commit()
    run_as(world["tokens"]["b"], body, api=spy)
    paths = [p for p, _ in seen]
    assert "/auth/me" in paths and "/theses" in paths and "/clock" in paths
    assert {h for _, h in seen} == {f"Bearer {world['tokens']['b']}"}
    with db.cursor() as cur:
        cur.execute("SELECT id, last_used_at FROM api_token")
        used = {r["id"]: r["last_used_at"] for r in cur.fetchall()}
    assert used[world["token_ids"]["b"]] is not None
    assert used[world["token_ids"]["a"]] is None and used[world["token_ids"]["s"]] is None
