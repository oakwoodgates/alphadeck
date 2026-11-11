"""The ``splits-review`` prompt's call plan, run through the REAL stdio client against a loopback fake API as a steward
(``/auth/me`` answers ``super_admin``, so the stdio server learns the ``steward`` scope): ``get_splits`` once, then
``section="one"`` for the needs-review names, then ``section="jumps"`` because the default reply counted an unlisted
jump. At most 4 calls and 24,000 characters in all, on the synthetic list. A plain caller is not offered the prompt.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from tests.agent_tools.fake_api import FakeApi, LoopbackServer  # noqa: E402

MAX_CALLS = 4
MAX_CHARS = 24_000
ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = ROOT / "agent_tools" / "prompts" / "splits-review.md"


def _session(base: str):
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "agent_tools", "--backend-base", base], cwd=ROOT
    )
    return stdio_client(params)


def test_the_splits_review_call_plan_costs_at_most_four_calls_and_24000_characters():
    spent: list[tuple[str, int]] = []
    api = FakeApi(me_role="super_admin")

    async def go(base: str):
        async with _session(base) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
                await session.initialize()
                listed = {p.name for p in (await session.list_prompts()).prompts}
                prompt = await session.get_prompt("splits-review", {"t": "TK01"})

                async def read(**args):
                    result = await session.call_tool("get_splits", args)
                    assert not result.is_error, result.content[0].text
                    spent.append((f"get_splits {args or ''}".strip(), len(result.content[0].text)))
                    return result.structured_content

                default = await read()  # 1
                one = await read(section="one", t="TK01")  # 2: the named name
                abstained = await read(section="one", t="TK02")  # 3: its extractor abstained
                jumps = await read(section="jumps")  # 4: the default reply counted an unlisted jump
                return listed, prompt.messages[0].content.text, default, one, abstained, jumps

    with LoopbackServer(api) as fake:
        listed, prompt, default, one, abstained, jumps = asyncio.run(go(fake.base))
    assert "splits-review" in listed
    assert prompt.startswith("Splits review, starting with TK01: ") and "{{" not in prompt
    assert len(PROMPT_FILE.read_text(encoding="utf-8")) <= 2_000 and len(prompt) <= 2_000
    assert len(spent) <= MAX_CALLS, spent
    assert sum(chars for _, chars in spent) <= MAX_CHARS, spent
    print(
        "splits-review cost:",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    assert all(chars <= 8_000 for _, chars in spent)
    # what the report needs is in those reads
    assert default["counts"]["needs_review"] == 3 and default["counts"]["unlisted_jumps"] == 1
    assert [r["ticker"] for r in default["needs_review"]] == ["TK01", "TK02", "TK03"]
    assert one["found"] == 2 and one["entries"][0]["sec"]["passage_chars"] > 300
    assert abstained["entries"][0]["sec"]["abstain_reason"] == "two ratios in the passage"
    assert [j["ticker"] for j in jumps["unlisted"]] == ["TK08"]


def test_a_plain_stdio_caller_is_not_offered_the_prompt_or_the_tools():
    api = FakeApi(me_role="user")

    async def go(base: str):
        async with _session(base) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
                await session.initialize()
                prompts = {p.name for p in (await session.list_prompts()).prompts}
                tools = {t.name for t in (await session.list_tools()).tools}
                return prompts, tools

    with LoopbackServer(api) as fake:
        prompts, tools = asyncio.run(go(fake.base))
    assert "splits-review" not in prompts and len(prompts) == 7
    assert not tools & {"get_splits", "get_admin_status"} and len(tools) == 14
