"""The principal seam (``agent_tools/principal.py`` + the scope-gated registry in ``server.py``): no I/O, no DB.

* READ-ONLY IS STRUCTURAL: every registered tool is annotated read-only and has a scope in ``SCOPES``. A later
  builder who adds a writer fails here first.
* The scope gate: a tool registered ``scope="steward"`` is neither listed nor callable for a plain principal
  (``forbidden``), is both for a steward, and no principal at all behaves as plain.
* The stdio principal comes from ``GET /auth/me``; any failure is the plain default.
* Grep guards: nothing under ``agent_tools/`` names a tenant or reaches the database except through the API.
"""

from __future__ import annotations

import ast
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

import httpx
import pytest

pytest.importorskip("mcp")

from mcp import types  # noqa: E402
from mcp.client.client import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402
from mcp_types import INVALID_PARAMS  # noqa: E402

from agent_tools import server as server_mod  # noqa: E402
from agent_tools.client import ApiClient  # noqa: E402
from agent_tools.principal import (  # noqa: E402
    CURRENT_PRINCIPAL,
    DEFAULT_PRINCIPAL,
    Principal,
    scopes_for_role,
)
from agent_tools.window import in_nightly_window  # noqa: E402
from tests.agent_tools.fake_api import FakeApi  # noqa: E402
from tests.agent_tools.test_server import TOOLS  # noqa: E402

_AGENT_TOOLS = Path(server_mod.__file__).resolve().parent
STEWARD_PRINCIPAL = Principal(frozenset({"read", "steward"}), "s", "pat")
PLAIN_PRINCIPAL = Principal(frozenset({"read"}), "u", "pat")
THROWAWAY = "zz_steward_probe"
STEWARD_TOOLS = {"get_splits", "get_admin_status"}  # the two registered with scope="steward"


def _client() -> ApiClient:
    return ApiClient("http://127.0.0.1:8001", transport=FakeApi().transport())


# --- read-only, structurally ----------------------------------------------------------------------------------


def test_every_registered_tool_is_read_only_and_has_a_scope():
    async def go():
        client = _client()
        try:
            server = server_mod.build_server(lambda: client)
            registered = server._tool_manager.list_tools()
        finally:
            await client.aclose()
        return registered

    registered = asyncio.run(go())
    assert {t.name for t in registered} == TOOLS | STEWARD_TOOLS
    for t in registered:
        a = t.annotations
        assert a is not None, t.name
        assert a.read_only_hint is True, t.name
        assert a.destructive_hint is False, t.name
        assert a.idempotent_hint is True, t.name
        assert a.open_world_hint is False, t.name
        assert server_mod.SCOPES.get(t.name) in {"read", "steward"}, t.name
    assert {server_mod.SCOPES[t.name] for t in registered} == {"read", "steward"}
    assert {n for n, scope in server_mod.SCOPES.items() if scope == "steward"} == STEWARD_TOOLS


def test_scopes_for_role():
    assert scopes_for_role("user") == {"read"}
    assert scopes_for_role("super_admin") == {"read", "steward"}
    assert scopes_for_role(None) == {"read"}
    assert DEFAULT_PRINCIPAL.scopes == {"read"}


def test_an_unknown_scope_is_refused_at_registration():
    server = server_mod.AlphaDeckServer(name="t")
    with pytest.raises(ValueError, match="unknown scope"):
        server_mod.register_tool(server, "zz_bad", "x", scope="write")


# --- the scope gate --------------------------------------------------------------------------------------------


@pytest.fixture
def gated():
    """A server with one throwaway steward-scoped tool; ``SCOPES`` is restored after."""
    saved = dict(server_mod.SCOPES)

    def build():
        client = _client()
        server = server_mod.build_server(lambda: client)

        @server_mod.register_tool(server, THROWAWAY, "A test-only probe.", scope="steward")
        async def probe() -> Annotated[types.CallToolResult, server_mod._Reply]:
            return server_mod._result({"asof": None, "source": "live", "ok": True})

        return server, client

    yield build
    server_mod.SCOPES.clear()
    server_mod.SCOPES.update(saved)


def _list_and_call(server, principal):
    async def go():
        reset = CURRENT_PRINCIPAL.set(principal)
        try:
            async with Client(server) as session:
                names = {t.name for t in (await session.list_tools()).tools}
                called = await session.call_tool(THROWAWAY, {})
                plain = await session.call_tool("get_clock", {})
                return names, called, plain
        finally:
            CURRENT_PRINCIPAL.reset(reset)

    return asyncio.run(go())


def test_a_plain_principal_neither_lists_nor_calls_a_steward_tool(gated):
    server, client = gated()
    names, called, plain = _list_and_call(server, PLAIN_PRINCIPAL)
    asyncio.run(client.aclose())
    assert names == TOOLS
    assert called.is_error
    assert json.loads(called.content[0].text) == {
        "code": "forbidden",
        "message": "This tool needs the steward scope.",
        "retryable": False,
    }
    assert not plain.is_error


def test_a_steward_principal_lists_and_calls_it(gated):
    server, client = gated()
    names, called, plain = _list_and_call(server, STEWARD_PRINCIPAL)
    asyncio.run(client.aclose())
    assert names == TOOLS | STEWARD_TOOLS | {THROWAWAY}
    assert not called.is_error and called.structured_content["ok"] is True
    assert not plain.is_error


def test_no_principal_behaves_as_plain(gated):
    server, client = gated()
    names, called, _ = _list_and_call(server, None)
    asyncio.run(client.aclose())
    assert names == TOOLS
    assert json.loads(called.content[0].text)["code"] == "forbidden"


# --- the two steward reads: the three layers, each tested with the others bypassed -------------------------------


def _steward_call(
    api: FakeApi, principal, tool: str = "get_splits", *, bypass_registry: bool = False
):
    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=api.transport())
        reset = CURRENT_PRINCIPAL.set(principal)
        try:
            server = server_mod.build_server(lambda: client)
            if (
                bypass_registry
            ):  # (the build records each tool's scope, so the bypass comes after it)
                server_mod.SCOPES[tool] = "read"
            async with Client(server) as session:
                names = {t.name for t in (await session.list_tools()).tools}
                return names, await session.call_tool(tool, {})
        finally:
            CURRENT_PRINCIPAL.reset(reset)
            await client.aclose()

    return asyncio.run(go())


def test_the_steward_lists_sixteen_tools_and_a_plain_principal_fourteen():
    names, _ = _steward_call(FakeApi(), STEWARD_PRINCIPAL)
    assert names == TOOLS | STEWARD_TOOLS and len(names) == 16
    names, _ = _steward_call(FakeApi(), PLAIN_PRINCIPAL)
    assert names == TOOLS and len(names) == 14


def _listing(principal) -> dict:
    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=FakeApi().transport())
        reset = CURRENT_PRINCIPAL.set(principal)
        try:
            async with Client(server_mod.build_server(lambda: client)) as session:
                return {t.name: t for t in (await session.list_tools()).tools}
        finally:
            CURRENT_PRINCIPAL.reset(reset)
            await client.aclose()

    return asyncio.run(go())


def test_the_monitor_descriptions_say_what_the_new_fields_mean():
    tools = _listing(STEWARD_PRINCIPAL)
    clock = tools["get_clock"].description
    assert "current_night = the night the last pass recorded" in clock
    assert "RECORD night" in clock and "market_today = today's date in market time" in clock
    assert "no_record: true" in tools["list_theses"].description
    assert "not_yet_created" in tools["lookup_ticker"].description


@pytest.mark.parametrize("tool", sorted(STEWARD_TOOLS))
def test_layer_one_the_registry_refuses_a_plain_principal_before_any_request(tool):
    api = FakeApi(steward=True)  # the API would answer: only the registry stands in the way
    _, called = _steward_call(api, PLAIN_PRINCIPAL, tool)
    assert called.is_error
    assert json.loads(called.content[0].text) == {
        "code": "forbidden",
        "message": "This tool needs the steward scope.",
        "retryable": False,
    }
    assert [
        p for _, p, _ in api.requests if p.startswith("/admin")
    ] == []  # refused before dispatch


@pytest.mark.parametrize(
    "tool,path", [("get_splits", "/admin/splits"), ("get_admin_status", "/admin/status")]
)
def test_layer_three_the_apis_403_answers_forbidden_with_the_registry_bypassed(tool, path):
    saved = dict(server_mod.SCOPES)
    api = FakeApi(steward=False)
    try:  # layer 1 bypassed: the registry treats the tool as `read`, so a plain principal holds it
        names, called = _steward_call(api, PLAIN_PRINCIPAL, tool, bypass_registry=True)
    finally:
        server_mod.SCOPES.clear()
        server_mod.SCOPES.update(saved)
    assert tool in names  # listed and callable, so only the API can refuse
    assert called.is_error
    body = json.loads(called.content[0].text)
    assert body["code"] == "forbidden" and body["message"] == "stewards only"
    assert [p for _, p, _ in api.requests if p.startswith("/admin")] == [path]  # it reached the API


# --- the scope-gated prompt ------------------------------------------------------------------------------------


def _prompts(principal, *, get: str | None = None, arguments: dict | None = None):
    async def go():
        client = _client()
        reset = CURRENT_PRINCIPAL.set(principal)
        try:
            async with Client(server_mod.build_server(lambda: client)) as session:
                names = [p.name for p in (await session.list_prompts()).prompts]
                tools = {t.name for t in (await session.list_tools()).tools}
                got = None
                if get is not None:
                    try:
                        got = await session.get_prompt(get, arguments)
                    except MCPError as error:
                        got = error
                return names, tools, got
        finally:
            CURRENT_PRINCIPAL.reset(reset)
            await client.aclose()

    return asyncio.run(go())


def test_the_steward_lists_sixteen_tools_and_eight_prompts_a_plain_principal_fourteen_and_seven():
    names, tools, _ = _prompts(STEWARD_PRINCIPAL)
    assert len(names) == 8 and "splits-review" in names and len(tools) == 16
    names, tools, _ = _prompts(PLAIN_PRINCIPAL)
    assert len(names) == 7 and "splits-review" not in names and len(tools) == 14
    names, tools, _ = _prompts(None)  # no principal at all is plain
    assert len(names) == 7 and len(tools) == 14


def test_a_plain_principal_cannot_get_the_steward_prompt_and_is_told_why():
    _, _, got = _prompts(PLAIN_PRINCIPAL, get="splits-review", arguments={})
    assert isinstance(got, MCPError)
    assert got.error.code == INVALID_PARAMS
    assert got.error.message == "This prompt needs the steward scope."
    # a prompt of the read scope is unaffected
    _, _, fine = _prompts(PLAIN_PRINCIPAL, get="ticker-check", arguments={"t": "TK01"})
    assert not isinstance(fine, MCPError) and "TK01" in fine.messages[0].content.text


def test_the_steward_gets_the_prompt_with_its_placeholder_filled():
    _, _, bare = _prompts(STEWARD_PRINCIPAL, get="splits-review", arguments={})
    text = bare.messages[0].content.text
    assert text.startswith("Splits review: ") and "{{" not in text
    _, _, named = _prompts(STEWARD_PRINCIPAL, get="splits-review", arguments={"t": "TK01"})
    assert named.messages[0].content.text.startswith("Splits review, starting with TK01: ")


# --- the stdio principal ---------------------------------------------------------------------------------------


def _me_transport(status: int, body: object):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/auth/me"
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "status,body,expected",
    [
        (200, {"id": "u-1", "role": "user"}, Principal(frozenset({"read"}), "u-1", "pat")),
        (
            200,
            {"id": "s-1", "role": "super_admin"},
            Principal(frozenset({"read", "steward"}), "s-1", "pat"),
        ),
        (401, {"detail": "sign in required"}, DEFAULT_PRINCIPAL),
        (403, {"detail": "the system token can only read dashboards"}, DEFAULT_PRINCIPAL),
        (500, {"detail": "boom"}, DEFAULT_PRINCIPAL),
    ],
)
def test_the_stdio_principal_comes_from_auth_me(status, body, expected):
    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=_me_transport(status, body))
        try:
            return await server_mod.stdio_principal(client)
        finally:
            await client.aclose()

    assert asyncio.run(go()) == expected


def test_the_stdio_principal_is_plain_when_the_api_is_down():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=httpx.MockTransport(handler))
        try:
            return await server_mod.stdio_principal(client)
        finally:
            await client.aclose()

    assert asyncio.run(go()) == DEFAULT_PRINCIPAL


# --- the nightly window (pure) ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "utc,inside",
    [
        (datetime(2026, 11, 10, 3, 14, tzinfo=timezone.utc), False),  # 22:14 EST
        (datetime(2026, 11, 10, 3, 15, tzinfo=timezone.utc), True),  # 22:15 EST
        (datetime(2026, 11, 10, 4, 29, tzinfo=timezone.utc), True),  # 23:29 EST
        (datetime(2026, 11, 10, 4, 30, tzinfo=timezone.utc), False),  # 23:30 EST
        (datetime(2026, 7, 10, 2, 20, tzinfo=timezone.utc), True),  # 22:20 EDT
        (datetime(2026, 7, 10, 15, 0, tzinfo=timezone.utc), False),
    ],
)
def test_the_nightly_window(utc, inside):
    assert in_nightly_window(utc) is inside


def test_the_window_refuses_a_naive_time():
    with pytest.raises(ValueError):
        in_nightly_window(datetime(2026, 11, 10, 22, 30))


# --- grep guards: no tenant, no database, only the API ---------------------------------------------------------


def _sources() -> list[Path]:
    return sorted(_AGENT_TOOLS.rglob("*.py"))


@pytest.mark.parametrize("word", ["WORKSPACE_ANY", "DEFAULT_TENANT_ID", "tenant_id"])
def test_no_tenant_is_named_under_agent_tools(word):
    offenders = [str(p) for p in _sources() if word in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_agent_tools_imports_nothing_from_repositories_or_db():
    offenders = []
    for path in _sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                if name.split(".")[0] in {"repositories", "db"}:
                    offenders.append(f"{path.name}: {name}")
    assert offenders == []
