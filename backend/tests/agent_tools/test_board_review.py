"""The ``board-review`` prompt's call plan at five queue cards, run through the REAL stdio client against a loopback
fake API: ``list_theses(limit=5)``, one ``get_call(detail=brief)`` per queue card and ``get_data_flags`` only where a
card's ``missing_count`` is above zero. At most 10 calls and 36,000 characters in all, on the synthetic cards.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from tests.agent_tools import fixtures as fx  # noqa: E402
from tests.agent_tools.fake_api import FakeApi, LoopbackServer  # noqa: E402

MAX_ITEMS = 5
MAX_CALLS = 10
MAX_CHARS = 36_000


def test_the_board_review_call_plan_at_five_cards_costs_at_most_ten_calls_and_36000_characters():
    spent: list[tuple[str, int]] = []

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
                    "board-review", {"asof": fx.ASOF, "max_items": str(MAX_ITEMS)}
                )

                async def read(tool: str, **args):
                    result = await session.call_tool(tool, args)
                    assert not result.is_error, result.content[0].text
                    spent.append((tool, len(result.content[0].text)))
                    return result.structured_content

                listed = await read("list_theses", detail="queue", limit=MAX_ITEMS)  # 1
                cards = listed["cards"][:MAX_ITEMS]
                assert [c["id"] for c in cards] == listed["queue"][:MAX_ITEMS]
                for card in cards:  # 2..6
                    await read("get_call", thesis=card["id"], detail="brief")
                    if card["missing_count"]:  # only where the record says something is missing
                        await read("get_data_flags", thesis=card["id"])
                return prompt.messages[0].content.text, listed

    with LoopbackServer(FakeApi()) as fake:
        prompt, listed = asyncio.run(go(fake.base))
    assert f"limit={MAX_ITEMS}" in prompt and f"first {MAX_ITEMS} queue ids" in prompt
    assert len(prompt) <= 1_500
    assert len(spent) <= MAX_CALLS, spent
    assert sum(chars for _, chars in spent) <= MAX_CHARS, spent
    print(
        "board-review x5 cost:",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    # limit keeps queue and counts whole, and the review's first read is one page within 8,000
    assert listed["limit"] == MAX_ITEMS and len(listed["queue"]) > MAX_ITEMS
    assert spent[0][0] == "list_theses" and spent[0][1] <= 8_000
