"""The ``card-data-check`` prompt's call plan on the 79-member card (V2 W5d: was ``ratify-queue``), run through the REAL
stdio client against a loopback fake API: ``get_triage`` once, ``get_thesis`` once and
``get_discovery(section="ambiguous")`` because the card has a saved run. At most 3 calls and 24,000 characters in all,
on the synthetic card; ``get_extract`` is never called (it spends). No prompt tells anyone to ratify a share count or
a cash value: those are shared values, set by the platform and the steward; a user flags one in the Workbench.
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
PROMPTS_DIR = Path(__file__).resolve().parents[2] / "agent_tools" / "prompts"
PROMPT_FILE = PROMPTS_DIR / "card-data-check.md"


def test_the_card_data_check_call_plan_costs_at_most_three_calls_and_24000_characters():
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
                    "card-data-check", {"thesis": rfx.THESIS_ID, "asof": rfx.ASOF}
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
        "card-data-check cost:",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    assert [tool for tool, _ in spent] == ["get_triage", "get_thesis", "get_discovery"]
    assert all(chars <= 8_000 for _, chars in spent)
    # what the report needs is in those three reads
    assert triage["counts"]["awaiting_members"] == len(rfx.AWAITING_SHARES | rfx.NOT_EXTRACTED_CASH)
    assert sum(s["awaiting"] for s in triage["segments"]) == triage["counts"]["awaiting_members"]
    assert all("values" in m for m in triage["members"])
    assert "values_asof" in triage
    assert any(m["signed_off"] is False for s in thesis["segments"] for m in s["members"])
    assert any(
        m["authored_by"] == "system_drafted" for s in thesis["segments"] for m in s["members"]
    )
    assert "exclusions_count" in thesis
    assert ambiguous["placements_total"] == 27 and ambiguous["placements"][0]["candidates"]
    # the extract spends and is never part of the plan
    assert not [r for r in api.requests if r[1].endswith("/extract")]


def test_the_prompt_says_the_extract_spends_and_that_nothing_is_set_by_the_agent():
    raw = PROMPT_FILE.read_text("utf-8")
    assert "get_extract SPENDS" in raw and "only when the operator names the company" in raw
    assert "you propose nothing and set nothing" in raw
    assert "never feeds the call" in raw and "never a call input" in raw
    assert "Agent interpretation" in raw and len(raw) <= 2_000


def test_the_prompt_ends_with_the_flag_and_never_says_ratify_or_unconfirmed():
    text = PROMPT_FILE.read_text(encoding="utf-8")
    assert text.rstrip().endswith("To dispute a shared value, flag it in the Workbench.")
    assert "ratif" not in text.lower() and "unconfirmed" not in text.lower()
    assert not (PROMPTS_DIR / "ratify-queue.md").exists()


def test_no_prompt_tells_anyone_to_ratify_a_share_count_or_a_cash_value():
    """A grep guard: a prompt may speak of ratifying purity (the user's own), never of ratifying the shared values."""
    for path in sorted(PROMPTS_DIR.glob("*.md")):
        for sentence in path.read_text(encoding="utf-8").replace("\n", " ").split(". "):
            low = sentence.lower()
            if "ratif" in low:
                assert not any(
                    w in low for w in ("share count", "shares", "cash", "runway", "market cap")
                ), (
                    path.name,
                    sentence,
                )
