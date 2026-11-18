"""The ``ratify-queue`` prompt's call plan on the 79-member card, run through the REAL stdio client against a loopback fake
API: ``get_triage`` once, ``get_thesis`` once and ``get_discovery(section="ambiguous")`` because the card has a saved run.
At most 3 calls and 24,000 characters in all, on the synthetic card; ``get_extract`` is never called (it spends).
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from tests.agent_tools import research_fixtures as rfx  # noqa: E402
from tests.agent_tools.fake_api import FakeApi, LoopbackServer  # noqa: E402

MAX_CALLS = 3
MAX_CHARS = 24_000
PROMPT_FILE = Path(__file__).resolve().parents[2] / "agent_tools" / "prompts" / "ratify-queue.md"


def test_the_ratify_queue_call_plan_costs_at_most_three_calls_and_24000_characters():
    spent: list[tuple[str, int]] = []
    api = FakeApi()

    async def go(base: str):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_tools", "--backend-base", base],
            cwd=Path(__file__).resolve().parents[2],
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
                await session.initialize()
                prompt = await session.get_prompt(
                    "ratify-queue", {"thesis": rfx.THESIS_ID, "asof": rfx.ASOF}
                )

                async def read(tool: str, **args):
                    result = await session.call_tool(tool, args)
                    assert not result.is_error, result.content[0].text
                    spent.append((tool, len(result.content[0].text)))
                    return result.structured_content

                triage = await read("get_triage", thesis=rfx.THESIS_ID, asof=rfx.ASOF)  # 1
                thesis = await read("get_thesis", thesis=rfx.THESIS_ID)  # 2
                ambiguous = await read(  # 3: the card has a saved run
                    "get_discovery", thesis=rfx.THESIS_ID, section="ambiguous"
                )
                return prompt.messages[0].content.text, triage, thesis, ambiguous

    with LoopbackServer(api) as fake:
        prompt, triage, thesis, ambiguous = asyncio.run(go(fake.base))
    assert rfx.THESIS_ID in prompt and rfx.ASOF in prompt and "{{" not in prompt
    assert len(prompt) <= 2_000
    assert len(spent) <= MAX_CALLS, spent
    assert sum(chars for _, chars in spent) <= MAX_CHARS, spent
    print(
        "ratify-queue cost:",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    assert [tool for tool, _ in spent] == ["get_triage", "get_thesis", "get_discovery"]
    assert all(chars <= 8_000 for _, chars in spent)
    # what the report needs is in those three reads
    assert triage["counts"]["unconfirmed_members"] == 73
    assert sum(s["confirmed"] for s in triage["segments"]) == 6
    assert any(m["signed_off"] is False for s in thesis["segments"] for m in s["members"])
    assert any(
        m["authored_by"] == "system_drafted" for s in thesis["segments"] for m in s["members"]
    )
    assert "exclusions_count" in thesis
    assert ambiguous["placements_total"] == 27 and ambiguous["placements"][0]["candidates"]
    # the extract spends and is never part of the plan
    assert not [r for r in api.requests if r[1].endswith("/extract")]


def test_the_prompt_says_the_extract_spends_and_that_nothing_is_ratified_by_the_agent():
    raw = PROMPT_FILE.read_text("utf-8")
    assert "get_extract SPENDS" in raw and "only when the operator names the company" in raw
    assert "you propose nothing and ratify nothing" in raw
    assert "never feeds the call" in raw and "never a call input" in raw
    assert "Agent interpretation" in raw and len(raw) <= 2_000
