"""The ``discovery-review`` prompt's call plan on the 1,735-placement run, through the REAL stdio client against a loopback
fake API: ``get_discovery`` (the report and the counts), ``section="terms"``, ``section="ambiguous"`` because
``counts.by_status.ambiguous`` is above zero, and one slice (``status="verify"``). At most 4 calls and 32,000 characters in
all, on the synthetic run.
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

MAX_CALLS = 4
MAX_CHARS = 32_000
PROMPT_FILE = (
    Path(__file__).resolve().parents[2] / "agent_tools" / "prompts" / "discovery-review.md"
)


def test_the_discovery_review_call_plan_costs_at_most_four_calls_and_32000_characters():
    spent: list[tuple[str, int]] = []
    api = FakeApi(details={rfx.THESIS_ID: rfx.thesis_with_terms()})

    async def go(base: str):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_tools", "--backend-base", base],
            cwd=Path(__file__).resolve().parents[2],
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=30) as session:
                await session.initialize()
                prompt = await session.get_prompt("discovery-review", {"thesis": rfx.THESIS_ID})

                async def read(**args):
                    result = await session.call_tool(
                        "get_discovery", {"thesis": rfx.THESIS_ID, **args}
                    )
                    assert not result.is_error, result.content[0].text
                    spent.append(
                        (
                            args.get("section") or args.get("status") or "default",
                            len(result.content[0].text),
                        )
                    )
                    return result.structured_content

                first = await read()  # 1
                terms = await read(section="terms")  # 2
                ambiguous_count = first["counts"]["by_status"]["ambiguous"]
                ambiguous = await read(section="ambiguous") if ambiguous_count else None  # 3
                sliced = await read(status="verify")  # 4: one slice
                return prompt.messages[0].content.text, first, terms, ambiguous, sliced

    with LoopbackServer(api) as fake:
        prompt, first, terms, ambiguous, sliced = asyncio.run(go(fake.base))
    assert rfx.THESIS_ID in prompt and "{{" not in prompt and len(prompt) <= 2_000
    assert len(spent) <= MAX_CALLS, spent
    assert sum(chars for _, chars in spent) <= MAX_CHARS, spent
    print(
        "discovery-review cost:",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    assert all(chars <= 8_000 for _, chars in spent)
    # what the report needs is in those reads
    report = first["run"]["report"]
    assert report["capped_terms"] and report["empty_terms"] and report["failed_terms"]
    assert first["counts"]["by_status"]["ambiguous"] == 27
    assert terms["terms_total"] == 31 and any(t.get("capped") for t in terms["terms"])
    assert ambiguous["placements_total"] == 27
    assert sliced["narrowed"] == {"status": "verify"} and sliced["placements_total"] == 1_066
    # reads only: no write, no spending route
    assert not [r for r in api.requests if r[1].endswith("/extract")]


def test_the_prompt_says_a_missing_run_is_a_finding_and_verify_is_not_a_rejection():
    raw = PROMPT_FILE.read_text("utf-8")
    assert "No saved run" in raw and "not an error" in raw
    assert "VERIFY means surfaced on BROAD terms and never dropped" in raw
    assert "the hits past the cap were not read" in raw
    assert "recall is sacred" in raw and len(raw) <= 2_000
