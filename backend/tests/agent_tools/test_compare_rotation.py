"""The ``compare-rotation`` prompt: its call plan through the REAL stdio client against a loopback fake API (default
``business_type``: at most 4 calls and 32,000 characters; with ``group``: at most 2 calls and 4,000), the matching rules
on the two payloads (a start-only key, an end-only key, a null median) and the argument rules through ``get_prompt``.
"""

from __future__ import annotations

import ast
import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from agent_tools import prompt_args  # noqa: E402
from tests.agent_tools import dashboard_fixtures as dfx  # noqa: E402
from tests.agent_tools.fake_api import FakeApi, LoopbackServer  # noqa: E402

START, END = dfx.ASOF, dfx.ASOF_END
BACKEND = Path(__file__).resolve().parents[2]
PROMPT_FILE = BACKEND / "agent_tools" / "prompts" / "compare-rotation.md"


def _session_run(base: str, steps):
    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_tools", "--backend-base", base],
            cwd=BACKEND,
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
                await session.initialize()
                return await steps(session)

    return asyncio.run(go())


async def _read_all(session, spent, asof, **settings):
    """One date of the recipe: the dashboard read, following ``next_cursor`` to the end."""
    groups: list[dict] = []
    cursor = 0
    while cursor is not None:
        args = {"dashboard": "rotation-map", "asof": asof, **settings}
        if cursor:
            args["cursor"] = cursor
        result = await session.call_tool("read_dashboard", args)
        assert not result.is_error, result.content[0].text
        spent.append((f"rotation-map@{asof}", len(result.content[0].text)))
        reply = result.structured_content
        groups.extend(reply["groups"])
        cursor = reply["next_cursor"]
    return reply, groups


def _plan(**settings):
    spent: list[tuple[str, int]] = []

    async def steps(session):
        args = {
            "start": START,
            "end": END,
            **{k: v for k, v in settings.items() if k != "group_by"},
        }
        if "group_by" in settings:
            args["group_by"] = settings["group_by"]
        prompt = await session.get_prompt("compare-rotation", args)
        read_settings = {"group_by": settings.get("group_by", "business_type")}
        if settings.get("group"):
            read_settings["group"] = settings["group"]
        first, a = await _read_all(session, spent, START, **read_settings)
        second, b = await _read_all(session, spent, END, **read_settings)
        return prompt.messages[0].content.text, (first, a), (second, b)

    with LoopbackServer(FakeApi()) as fake:
        text, start, end = _session_run(fake.base, steps)
    return text, start, end, spent


def _by_key(groups):
    return {g["key"]: g for g in groups}


def test_the_default_plan_costs_at_most_four_calls_and_32000_characters_and_matches_by_key():
    text, (first, start), (second, end), spent = _plan()
    assert (
        START in text and END in text and "grouped by business_type" in text and len(text) <= 2_000
    )
    assert len(spent) <= 4, spent
    assert sum(chars for _, chars in spent) <= 32_000, spent
    print(
        "compare-rotation cost (default):",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    assert first["asof"] == START and second["asof"] == END
    s, e = _by_key(start), _by_key(end)
    assert len(s) == len(start) == 20 and len(e) == 20  # one dropped, one added at the end
    start_only, end_only = set(s) - set(e), set(e) - set(s)
    assert len(start_only) == 1 and len(end_only) == 1 and start_only != end_only
    assert all(k.startswith("business_type:") for k in (*start_only, *end_only))
    matched = set(s) & set(e)
    moved = [
        k
        for k in matched
        if s[k]["returns"]["ret_7d"]["median"] != e[k]["returns"]["ret_7d"]["median"]
    ]
    assert len(moved) == 3  # two moved medians and the one that went null
    unavailable = [k for k in matched if e[k]["returns"]["ret_7d"]["median"] is None]
    assert (
        len(unavailable) == 1
        and e[unavailable[0]]["returns"]["ret_7d"]["n"] < second["coverage"]["min_priced"]
    )
    # coverage differs between the dates, and the replies carry it for the report
    assert first["coverage"]["cards_not_yet_created"] == 0
    assert second["coverage"]["cards_not_yet_created"] == 1


def test_a_group_plan_costs_at_most_two_calls_and_4000_characters():
    key = "business_type:miner"
    text, (first, start), (second, end), spent = _plan(group=key)
    assert f"group={key}" in text and len(text) <= 2_000
    assert len(spent) <= 2, spent
    assert sum(chars for _, chars in spent) <= 4_000, spent
    print(
        "compare-rotation cost (group):",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    assert [g["key"] for g in start] == [key] and [g["key"] for g in end] == [key]
    assert first["narrowed"] == {"group": key} and second["narrowed"] == {"group": key}


# --- the argument rules, through the prompt call ----------------------------------------------------------------


@pytest.mark.parametrize(
    "args,rule",
    [
        (
            {"start": "2026-11-11", "end": "2026-11-05"},
            "start 2026-11-11 must be before end 2026-11-05",
        ),
        (
            {"start": "2026-11-05", "end": "2026-11-05"},
            "start 2026-11-05 must be before end 2026-11-05",
        ),
        ({"start": "11/05/2026", "end": "2026-11-11"}, "start must be a date as YYYY-MM-DD"),
        ({"start": "2026-11-05", "end": "yesterday"}, "end must be a date as YYYY-MM-DD"),
        ({"start": "2026-02-30", "end": "2026-11-11"}, "start must be a real date"),
        (
            {"start": "2026-11-05", "end": "2026-11-11", "group_by": "cap"},
            "group_by must be one of: business_type, sector, card, link",
        ),
        (
            {"start": "2026-11-05", "end": "2026-11-11", "group_by": "link"},
            "group_by=link needs group",
        ),
    ],
)
def test_a_broken_rule_fails_the_prompt_call_naming_the_rule(args, rule):
    async def steps(session):
        with pytest.raises(MCPError) as caught:
            await session.get_prompt("compare-rotation", args)
        return str(caught.value)

    with LoopbackServer(FakeApi()) as fake:
        message = _session_run(fake.base, steps)
    assert rule in message, message


def test_a_link_comparison_with_a_group_is_accepted():
    prompt_args.check_compare("2026-11-05", "2026-11-11", "link", "link:abc:Segment")
    prompt_args.check_compare("2026-11-05", "2026-11-11", "sector", None)


def test_prompt_args_check_is_pure_and_the_file_names_the_plan_and_the_rules():
    tree = ast.parse((BACKEND / "agent_tools" / "prompt_args.py").read_text("utf-8"))
    imported = {
        (
            (node.module or "").split(".")[0]
            if isinstance(node, ast.ImportFrom)
            else a.name.split(".")[0]
        )
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for a in (node.names if isinstance(node, ast.Import) else [None])
    }
    assert imported <= {"__future__", "re", "datetime"}, imported  # no client, server, httpx or mcp
    raw = PROMPT_FILE.read_text("utf-8")
    assert len(raw) <= 2_000
    for needle in (
        "read_dashboard",
        "next_cursor",
        "by key",
        "start-only and end-only",
        "unavailable",
        "min_priced",
        "agent calculation",
        "Agent interpretation",
        "display only",
        "data, not instructions",
    ):
        assert needle in raw, needle
    for placeholder in ("{{start}}", "{{end}}", "{{group_by}}", "{{group_clause}}"):
        assert placeholder in raw
