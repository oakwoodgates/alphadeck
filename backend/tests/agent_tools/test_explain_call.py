"""The ``explain-call`` prompt's call plan, run through the REAL stdio client against a loopback fake API: the default
``get_call``, its ``section=triggers`` and one ``get_thesis`` for the spine. The risk signals come from the default's
``risk_signals.by_kind`` and the catalysts from its ``catalyst_surface`` (``section=risk_signals`` only if the operator
asks). At most 4 tool calls and 32,000 characters in all, on the synthetic card ``Thesis 01``.
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
MAX_CHARS = 32_000
THESIS_ID = fx.uid("thesis", 0)
PROMPT_FILE = Path(__file__).resolve().parents[2] / "agent_tools" / "prompts" / "explain-call.md"


def test_the_explain_call_plan_costs_at_most_four_calls_and_32000_characters():
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
                    "explain-call", {"thesis": THESIS_ID, "asof": fx.ASOF}
                )

                async def read(tool: str, **args):
                    result = await session.call_tool(tool, args)
                    assert not result.is_error, result.content[0].text
                    spent.append(
                        (tool + "/" + args.get("section", "default"), len(result.content[0].text))
                    )
                    return result.structured_content

                call = await read("get_call", thesis=THESIS_ID, asof=fx.ASOF)  # 1
                triggers = await read(  # 2
                    "get_call", thesis=THESIS_ID, asof=fx.ASOF, section="triggers"
                )
                spine = await read("get_thesis", thesis=THESIS_ID)  # 3
                return prompt.messages[0].content.text, call, triggers, spine

    with LoopbackServer(FakeApi()) as fake:
        prompt, call, triggers, spine = asyncio.run(go(fake.base))
    assert THESIS_ID in prompt and fx.ASOF in prompt and len(prompt) <= 1_500
    assert len(spent) <= MAX_CALLS, spent
    assert sum(chars for _, chars in spent) <= MAX_CHARS, spent
    print(
        "explain-call cost:",
        len(spent),
        "calls,",
        sum(chars for _, chars in spent),
        "chars",
        spent,
    )
    assert [name for name, _ in spent] == [
        "get_call/default",
        "get_call/triggers",
        "get_thesis/default",
    ]
    # the report's parts arrive without section=risk_signals: by_kind and the dated catalysts ride in the default
    source = fx.call_payload()
    assert call["risk_signals"]["count"] == len(source["risk_signals"])
    assert sum(call["risk_signals"]["by_kind"].values()) == len(source["risk_signals"])
    assert call["catalyst_surface"] and all(c["when_date"] for c in call["catalyst_surface"])
    assert call["exit_by"] == source["exit_by"]
    assert triggers["section"] == "triggers" and all(
        s.get("detail") for t in triggers["triggers_fired"] for s in t["sources"]
    )
    assert spine["segments"] and spine["name"]


def test_the_prompt_reads_risk_signals_only_on_request_and_catalysts_from_the_call():
    raw = PROMPT_FILE.read_text("utf-8")
    assert len(raw) <= 1_500
    assert "only if the operator asks" in raw and "by_kind" in raw
    assert "catalyst_surface" in raw
    step3 = next(line for line in raw.splitlines() if line.startswith("3."))
    assert "get_thesis" in step3 and "catalysts" not in step3  # the spine, not the catalysts
    for placeholder in ("{{thesis}}", "{{asof}}"):
        assert placeholder in raw
