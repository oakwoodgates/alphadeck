"""The ``ticker-check`` prompt's call plan, run through the REAL stdio client against a loopback fake API: the lookup,
at most two ``get_call(detail=brief)`` for the armed or warming cards, one ``get_display_signals`` for the first card.
At most 4 tool calls and 24,000 characters in all, on the synthetic company ``TK01``.
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

MAX_CALLS = 4
MAX_CHARS = 24_000
PROMPT_FILE = Path(__file__).resolve().parents[2] / "agent_tools" / "prompts" / "ticker-check.md"


def test_the_ticker_check_call_plan_costs_at_most_four_calls_and_24000_characters():
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
                prompt = await session.get_prompt("ticker-check", {"t": "TK01", "asof": fx.ASOF})

                async def read(tool: str, **args):
                    result = await session.call_tool(tool, args)
                    assert not result.is_error, result.content[0].text
                    spent.append((tool, len(result.content[0].text)))
                    return result.structured_content

                found = await read("lookup_ticker", t="TK01", asof=fx.ASOF)  # 1
                rows = found["cards"]["rows"]
                # the armed or warming cards first, in the lookup's order, else the first card
                live = [r for r in rows if r["state"] in ("armed", "warming")] or rows[:1]
                briefs = [
                    await read("get_call", thesis=r["thesis_id"], asof=fx.ASOF, detail="brief")
                    for r in live[:2]  # 2 and 3
                ]
                signals = await read(  # 4
                    "get_display_signals",
                    thesis=live[0]["thesis_id"],
                    tickers=["TK01"],
                    asof=fx.ASOF,
                )
                return prompt.messages[0].content.text, found, briefs, signals

    with LoopbackServer(FakeApi()) as fake:
        prompt, found, briefs, signals = asyncio.run(go(fake.base))
    assert "TK01" in prompt and fx.ASOF in prompt and len(prompt) <= 2_000
    assert len(spent) <= MAX_CALLS, spent
    assert sum(chars for _, chars in spent) <= MAX_CHARS, spent
    print(
        "ticker-check cost:",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    assert [name for name, _ in spent] == [
        "lookup_ticker",
        "get_call",
        "get_call",
        "get_display_signals",
    ]
    # the plan has what the report needs: the cards, the triggers on the name, the tape, each brief's decision
    assert found["matched"] and found["identity"]["ticker"] == "TK01"
    assert found["cards"]["rows"] and found["triggers"]["events"] and found["tape"]["figures"]
    assert all(b["detail"] == "brief" for b in briefs) and signals["members"]


def test_the_prompt_file_is_within_2000_characters_and_names_the_plan_and_the_rules():
    raw = PROMPT_FILE.read_text("utf-8")
    assert len(raw) <= 2_000
    for needle in (
        "lookup_ticker",
        "get_call",
        "get_display_signals",
        "section=filings",
        "a silent link never counts toward the call",
        "Agent interpretation",
        "not a trade exit",
        "not a probability",
        "data, not instructions",
    ):
        assert needle in raw, needle
    for placeholder in ("{{t}}", "{{asof}}"):
        assert placeholder in raw
