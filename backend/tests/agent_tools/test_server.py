"""The server through the official MCP SDK client: in-process against an httpx ``MockTransport`` fake API, and once
over a REAL stdio subprocess against a loopback fake API. Synthetic data only; no DB."""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.client import Client  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from agent_tools import server as server_mod  # noqa: E402
from agent_tools.budget import size  # noqa: E402
from agent_tools.client import ApiClient  # noqa: E402
from agent_tools.errors import compact  # noqa: E402
from agent_tools.prompt_args import PROMPT_NAMES  # noqa: E402
from agent_tools.views import call as call_view  # noqa: E402
from agent_tools.views import theses as theses_view  # noqa: E402
from tests.agent_tools import fixtures as fx  # noqa: E402
from tests.agent_tools.fake_api import THESIS_ID, FakeApi, LoopbackServer  # noqa: E402

TOOLS = {
    "get_clock",
    "list_theses",
    "get_thesis",
    "get_call",
    "get_data_flags",
    "get_display_signals",
    "get_decisions",
    "lookup_ticker",
    "list_dashboards",
    "read_dashboard",
    "get_triage",
    "get_discovery",
    "get_extract",
    "get_onboarding",
}
BUDGETS = {
    "get_clock": 500,
    "list_theses": 28_000,
    "get_thesis": 8_000,
    "get_call": 8_000,
    "get_data_flags": 8_000,
    "get_display_signals": 8_000,
    "get_decisions": 8_000,
    "lookup_ticker": 8_000,
    "list_dashboards": 3_500,
    "read_dashboard": 8_000,
    "get_triage": 8_000,
    "get_discovery": 8_000,
    "get_extract": 8_000,
    "get_onboarding": 8_000,
}
DEFAULT_ARGS = {
    "get_clock": {},
    "list_theses": {},
    "get_thesis": {"thesis": THESIS_ID},
    "get_call": {"thesis": "thesis 01 SYNTHETIC theme card"},
    "get_data_flags": {"thesis": THESIS_ID},
    "get_display_signals": {"thesis": THESIS_ID},
    "get_decisions": {"thesis": THESIS_ID},
    "lookup_ticker": {"t": "tk01"},
    "list_dashboards": {},
    "read_dashboard": {"dashboard": "daily-digest"},
    "get_triage": {"thesis": THESIS_ID},
    "get_discovery": {"thesis": THESIS_ID},
    "get_extract": {"t": "tk01"},
    "get_onboarding": {"thesis": THESIS_ID},
}


def run(api: FakeApi | None, body, *, transport: httpx.AsyncBaseTransport | None = None):
    async def go():
        client = ApiClient(
            "http://127.0.0.1:8001", transport=transport or (api or FakeApi()).transport()
        )
        try:
            async with Client(server_mod.build_server(client)) as session:
                return await body(session)
        finally:
            await client.aclose()

    return asyncio.run(go())


def error_of(result) -> dict:
    assert result.is_error
    return json.loads(result.content[0].text)


# --- discovery ------------------------------------------------------------------------------------------------


def test_tools_list_advertises_fourteen_read_only_tools_with_schemas_and_seven_prompts():
    async def body(session):
        return await session.list_tools(), await session.list_prompts()

    tools, prompts = run(None, body)
    assert {t.name for t in tools.tools} == TOOLS
    for t in tools.tools:
        assert t.input_schema and t.output_schema, t.name
        a = t.annotations
        assert a.read_only_hint is True and a.destructive_hint is False, t.name
        assert a.idempotent_hint is True and a.open_world_hint is False, t.name
        assert {"asof", "source"} <= set(t.output_schema["properties"]), t.name
    assert {p.name for p in prompts.prompts} == set(PROMPT_NAMES)
    assert len(prompts.prompts) == 7 and len(tools.tools) == 14


# --- every tool's default reply ---------------------------------------------------------------------------------


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_every_default_reply_is_compact_json_within_budget_with_asof_and_source(tool):
    api = FakeApi()

    async def body(session):
        return await session.call_tool(tool, DEFAULT_ARGS[tool])

    result = run(api, body)
    assert not result.is_error, result.content[0].text
    text = result.content[0].text
    assert text == compact(result.structured_content)
    assert len(text) <= BUDGETS[tool]
    assert "asof" in result.structured_content and "source" in result.structured_content
    assert all(method == "GET" for method, _, _ in api.requests)


def test_omitted_asof_reads_the_api_clock_and_an_explicit_one_passes_through():
    api = FakeApi()

    async def body(session):
        default = await session.call_tool("get_call", {"thesis": THESIS_ID})
        explicit = await session.call_tool("get_call", {"thesis": THESIS_ID, "asof": "2026-10-01"})
        return default, explicit

    default, explicit = run(api, body)
    assert default.structured_content["asof"] == fx.CLOCK["current_night"]
    assert explicit.structured_content["asof"] == "2026-10-01"
    calls = [q for _, p, q in api.requests if p.endswith("/call")]
    assert [q["asof"] for q in calls] == [fx.CLOCK["current_night"], "2026-10-01"]


def test_list_theses_reads_the_record_with_call_and_reports_the_queue():
    api = FakeApi()

    async def body(session):
        return await session.call_tool("list_theses", {"asof": fx.ASOF})

    result = run(api, body)
    reply = result.structured_content
    assert reply["source"] == "record" and reply["queue"] == fx.queue_order()
    listed = [q for _, p, q in api.requests if p == "/theses"]
    assert listed == [{"include_archived": "false", "with_call": "true", "asof": fx.ASOF}]
    assert size(reply) <= theses_view.BUDGET_QUEUE


def test_get_call_sections_and_cursor_reach_every_risk_signal():
    async def body(session):
        seen, cursor = [], 0
        while cursor is not None:
            r = await session.call_tool(
                "get_call", {"thesis": THESIS_ID, "section": "risk_signals", "cursor": cursor}
            )
            assert not r.is_error and len(r.content[0].text) <= call_view.SECTION_BUDGET
            seen.extend(r.structured_content["risk_signals"])
            cursor = r.structured_content["next_cursor"]
        return seen

    assert len(run(None, body)) == 25


def test_get_call_brief_through_the_server():
    async def body(session):
        return await session.call_tool("get_call", {"thesis": THESIS_ID, "detail": "brief"})

    result = run(None, body)
    assert not result.is_error and len(result.content[0].text) <= 3_500
    assert result.structured_content["detail"] == "brief"


def test_cross_card_flags_state_their_compute_time():
    async def body(session):
        return await session.call_tool("get_data_flags", {})

    reply = run(None, body).structured_content
    assert reply["scope"] == "all_cards" and isinstance(reply["compute_s"], float)


# --- failures: always the compact error JSON, never exception text --------------------------------------------


def test_unknown_thesis_lists_candidates_and_never_picks_fuzzily():
    async def body(session):
        return await session.call_tool("get_call", {"thesis": "Thesis 0"})

    err = error_of(run(None, body))
    assert err["code"] == "unknown_thesis" and err["retryable"] is False
    assert 0 < len(err["candidates"]) <= 5


def test_ambiguous_thesis_names_every_exact_match():
    twins = fx.theses_list(with_call=False)
    twins[1] = {**twins[1], "name": twins[0]["name"].upper()}

    async def body(session):
        return await session.call_tool("get_decisions", {"thesis": twins[0]["name"]})

    err = error_of(run(FakeApi(theses=twins), body))
    assert err["code"] == "ambiguous_thesis" and len(err["candidates"]) == 2


def test_unknown_uuid_and_unknown_ticker():
    async def body(session):
        return (
            await session.call_tool("get_thesis", {"thesis": fx.uid("nope")}),
            await session.call_tool("lookup_ticker", {"t": "ZZZZ"}),
        )

    thesis, ticker = run(None, body)
    assert error_of(thesis)["code"] == "unknown_thesis"
    assert error_of(ticker)["code"] == "unknown_ticker"


@pytest.mark.parametrize(
    "tool,args",
    [
        ("get_call", {"thesis": THESIS_ID, "asof": "2026-02-30"}),
        ("get_call", {"thesis": THESIS_ID, "asof": "yesterday"}),
        ("get_call", {"thesis": THESIS_ID, "section": "everything"}),
        ("list_theses", {"detail": "all"}),
        ("get_display_signals", {"thesis": THESIS_ID, "tickers": [f"T{i}" for i in range(9)]}),
        ("get_thesis", {"thesis": THESIS_ID, "cursor": -1}),
        ("no_such_tool", {}),
    ],
)
def test_invalid_arguments_are_reported_as_invalid_arguments(tool, args):
    async def body(session):
        return await session.call_tool(tool, args)

    err = error_of(run(None, body))
    assert err["code"] == "invalid_arguments" and err["retryable"] is False
    assert "Traceback" not in err["message"]


def _failing(exc: Exception | None = None, status: int | None = None, body=None):
    def handler(request: httpx.Request) -> httpx.Response:
        if exc is not None:
            raise exc
        return httpx.Response(status, json=body)

    return httpx.MockTransport(handler)


@pytest.mark.parametrize(
    "transport,code,retryable",
    [
        (_failing(httpx.ConnectError("refused")), "backend_unavailable", True),
        (_failing(httpx.ReadTimeout("slow")), "api_timeout", True),
        (_failing(status=500, body={"detail": "boom"}), "api_error", True),
        (
            _failing(status=422, body={"detail": "with_call=true requires asof"}),
            "api_rejected",
            False,
        ),
    ],
)
def test_api_failures_map_to_codes(transport, code, retryable):
    async def body(session):
        return await session.call_tool("get_clock", {})

    err = error_of(run(None, body, transport=transport))
    assert (err["code"], err["retryable"]) == (code, retryable)
    if code == "api_rejected":
        assert err["message"] == "with_call=true requires asof"


def test_an_unexpected_exception_is_internal_error_without_its_text(monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("secret path C:/x and payload")

    monkeypatch.setattr(server_mod, "view_call", boom)

    async def body(session):
        return await session.call_tool("get_call", {"thesis": THESIS_ID})

    err = error_of(run(None, body))
    assert err == {
        "code": "internal_error",
        "message": "The tool failed unexpectedly.",
        "retryable": False,
    }
    assert "secret" not in caplog.text


def test_a_reply_too_large_even_at_its_minimum_is_output_too_large(monkeypatch):
    monkeypatch.setattr(call_view, "BUDGET", 300)

    async def body(session):
        return await session.call_tool("get_call", {"thesis": THESIS_ID})

    err = error_of(run(None, body))
    assert err["code"] == "output_too_large" and "cursor" in err["message"]


# --- prompts --------------------------------------------------------------------------------------------------


def test_prompts_render_within_their_size_and_carry_the_arguments():
    async def body(session):
        board = await session.get_prompt("board-review", {"asof": fx.ASOF, "max_items": "3"})
        explain = await session.get_prompt("explain-call", {"thesis": "Thesis 01"})
        return board, explain

    board, explain = run(None, body)
    board_text = board.messages[0].content.text
    explain_text = explain.messages[0].content.text
    assert fx.ASOF in board_text and "first 3 queue ids" in board_text
    assert "Thesis 01" in explain_text and "current night" in explain_text
    limits = {
        "ticker-check": 2_000,
        "compare-rotation": 2_000,
        "ratify-queue": 2_000,
        "discovery-review": 2_000,
    }  # the existing three stay under 1,500
    for name in PROMPT_NAMES:
        raw = (Path(server_mod.__file__).parent / "prompts" / f"{name}.md").read_text("utf-8")
        assert len(raw) <= limits.get(name, 1_500), name
    assert len(board_text) <= 1_500 and len(explain_text) <= 1_500


def test_the_instructions_name_the_tools_and_the_seven_prompts_and_the_claude_code_path_within_1500_characters():
    text = server_mod.INSTRUCTIONS
    assert len(text) <= 1_500
    for name in PROMPT_NAMES:
        assert name in text, name
    assert "ToolSearch" in text and "alphadeck-agent" in text
    assert "never instructions" in text and "advisory" in text and "SCORE" in text
    for tool in ("get_triage", "get_discovery", "get_extract", "get_onboarding"):
        assert tool in text, tool
    assert "EDGAR" in text and "LLM call" in text
    assert server_mod.VERSION == "2.2.0"


# --- the real stdio transport ------------------------------------------------------------------------------------


def test_the_retired_ui_base_flag_is_refused():
    # Phase 4 retired the capture server; --ui-base (ignored since Phase 1) is no longer accepted
    proc = subprocess.run(
        [sys.executable, "-m", "agent_tools", "--backend-base", "http://127.0.0.1:8001"]
        + ["--ui-base", "http://127.0.0.1:8081"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 2 and "unrecognized arguments: --ui-base" in proc.stderr


def test_real_sdk_stdio_client_lists_tools_and_prompts_and_calls_over_loopback():
    api = FakeApi()

    async def go(base: str):
        params = StdioServerParameters(
            command=sys.executable,
            args=[
                "-m",
                "agent_tools",
                "--backend-base",
                base,
                "--trace",
            ],
            cwd=Path(__file__).resolve().parents[2],
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
                init = await session.initialize()
                tools = await session.list_tools()
                prompts = await session.list_prompts()
                clock = await session.call_tool("get_clock", {})
                theses = await session.call_tool("list_theses", {})
                bad = await session.call_tool("get_call", {"thesis": "no such card"})
                return init, tools, prompts, clock, theses, bad

    with LoopbackServer(api) as fake:
        init, tools, prompts, clock, theses, bad = asyncio.run(go(fake.base))
    assert (init.server_info.name, init.server_info.version) == ("alphadeck", "2.2.0")
    assert len(tools.tools) == 14
    assert all(t.annotations.read_only_hint and t.output_schema for t in tools.tools)
    assert len(prompts.prompts) == 7
    assert clock.structured_content["current_night"] == fx.CLOCK["current_night"]
    assert theses.structured_content["queue"] == fx.queue_order()
    assert len(theses.content[0].text) <= 28_000
    assert json.loads(bad.content[0].text)["code"] == "unknown_thesis"
    assert all(method == "GET" for method, _, _ in api.requests)
