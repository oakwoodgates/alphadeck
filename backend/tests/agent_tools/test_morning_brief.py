"""The ``morning-brief`` prompt's call plan, run through the REAL stdio client against a loopback fake API (as the
Phase 1 scale tests do): at most 5 tool calls and 36,000 characters in all, on the synthetic dashboards.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from tests.agent_tools import dashboard_fixtures as dfx  # noqa: E402
from tests.agent_tools.fake_api import FakeApi, LoopbackServer  # noqa: E402

MAX_CALLS = 5
MAX_CHARS = 36_000


def _run(base: str, steps):
    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_tools", "--backend-base", base],
            cwd=Path(__file__).resolve().parents[2],
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
                await session.initialize()
                prompt = await session.get_prompt("morning-brief", {"asof": dfx.ASOF})
                return prompt.messages[0].content.text, await steps(session)

    return asyncio.run(go())


def test_the_morning_brief_call_plan_costs_at_most_five_calls_and_36000_characters():
    spent: list[tuple[str, int]] = []

    async def steps(session):
        async def read(**args):
            result = await session.call_tool("read_dashboard", args)
            assert not result.is_error, result.content[0].text
            spent.append(
                (
                    args["dashboard"] + "/" + args.get("section", "default"),
                    len(result.content[0].text),
                )
            )
            return result.structured_content

        changed = await read(dashboard="what-changed")  # 1
        digest = await read(dashboard="daily-digest", asof=dfx.ASOF)  # 2
        due = digest["due"]
        if due["items_total"] > len(due["items"]):  # 3: the sections the default cut, due first
            due = await read(dashboard="daily-digest", asof=dfx.ASOF, section="due")
        insider = digest["insider"]
        if insider["rows_total"] > len(insider["rows"]):  # 4: then insider
            insider = await read(dashboard="daily-digest", asof=dfx.ASOF, section="insider")
        tape = digest["tape"]
        if tape["rows_total"] > tape["rows_shown"] + tape["tail_shown"]:  # 5: then the tape
            tape = await read(dashboard="daily-digest", asof=dfx.ASOF, section="tape")
        return changed, digest, due, insider, tape

    with LoopbackServer(FakeApi()) as fake:
        prompt, (changed, digest, due, insider, tape) = _run(fake.base, steps)
    assert dfx.ASOF in prompt and len(prompt) <= 1_500
    assert len(spent) <= MAX_CALLS, spent
    assert sum(chars for _, chars in spent) <= MAX_CHARS, spent
    print(
        "morning-brief cost:",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    # the brief has what it reports from: the pass's changes, the due window, the tape, the insider buys
    assert changed["asof"] is None and changed["items_total"] == 559
    assert digest["due"]["items_total"] == 14 and digest["tape"]["rows"]
    # rises and falls both show in the default; the cut sections arrive whole by the follow-ups
    assert digest["tape"]["tail"] and digest["tape"]["rows_total"] == 69
    assert len(due["items"]) == 14 and len(insider["rows"]) == 9 and insider["rows_total"] == 9
    assert tape["section"] == "tape" and tape["rows"] and tape["rows_total"] == 69
    assert [name for name, _ in spent] == [
        "what-changed/default",
        "daily-digest/default",
        "daily-digest/due",
        "daily-digest/insider",
        "daily-digest/tape",
    ]


def test_the_prompt_names_the_plan_within_1500_characters():
    root = Path(__file__).resolve().parents[2] / "agent_tools" / "prompts" / "morning-brief.md"
    raw = root.read_text("utf-8")
    assert len(raw) <= 1_500
    for needle in (
        "what-changed",
        "daily-digest",
        'section="due"',
        "tail_shown",
        "forming-quietly",
        "5 calls in all",
        "Agent interpretation",
        "display only",
    ):
        assert needle in raw
