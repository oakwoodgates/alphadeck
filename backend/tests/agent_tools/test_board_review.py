"""The ``board-review`` prompt's call plan at five queue cards, run through the REAL stdio client against a loopback
fake API: ``list_theses(limit=5)``, one ``get_call(detail=brief)`` per queue card and ``get_data_flags`` only where a
missing condition reads as a data gap, never for a risk condition ("Risk must clear before arming"). At most 10 calls
and 36,000 characters in all, on the synthetic cards.

The ``/theses?with_call=true`` record carries no count of the data flags that reach the call (``RecordedCallSummary``
has ``missing_count`` only), so the plan keys on the brief's ``missing`` text, as the prompt's step 3 says.
"""

from __future__ import annotations

import asyncio
import copy
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from app.schemas_api import RecordedCallSummary  # noqa: E402
from tests.agent_tools import fixtures as fx  # noqa: E402
from tests.agent_tools.fake_api import FakeApi, LoopbackServer  # noqa: E402

MAX_ITEMS = 5
MAX_CALLS = 10
MAX_CHARS = 36_000
PROMPT_FILE = Path(__file__).resolve().parents[2] / "agent_tools" / "prompts" / "board-review.md"
RISK_ONLY = "Risk must clear before arming: breakdown on TK03 " + fx.text(60, 1)
DATA_GAP = "Unpriced name: TK05 has a stale tape (a data flag holds its confirmation key)"


def _reads_as_a_data_gap(missing: str) -> bool:
    """The prompt's step 3 as the plan applies it: a flag, a stale tape or an unpriced name; never a risk condition."""
    if missing.startswith("Risk must clear before arming"):
        return False
    lowered = missing.lower()
    return any(word in lowered for word in ("flag", "stale", "unpriced"))


def _api() -> tuple[FakeApi, str, str]:
    """The first queue card misses only a risk condition, the second a data gap; both say so in their record."""
    theses = fx.theses_list()
    queue = fx.queue_order()
    risk_card, gap_card = queue[0], queue[1]
    for t in theses:
        if t["id"] in (risk_card, gap_card):
            t["call"]["missing_count"] = 1
    risk = copy.deepcopy(fx.call_payload())
    risk["missing"] = [RISK_ONLY]
    gap = copy.deepcopy(fx.call_payload())
    gap["missing"] = [DATA_GAP]
    return FakeApi(theses=theses, calls={risk_card: risk, gap_card: gap}), risk_card, gap_card


def test_the_list_record_carries_no_count_of_flags_that_reach_the_call():
    # Step 0 (c) part 1: if the wire grows such a count, list_theses should surface it and step 3 key on it
    assert not [f for f in RecordedCallSummary.model_fields if "flag" in f]


def test_the_board_review_call_plan_at_five_cards_costs_at_most_ten_calls_and_36000_characters():
    spent: list[tuple[str, int]] = []
    flagged: list[str] = []
    api, risk_card, gap_card = _api()

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
                    brief = await read("get_call", thesis=card["id"], detail="brief")
                    # the brief's missing entries keep their 100-character cut
                    assert all(len(m["text"]) <= 100 for m in brief["missing"])
                    if any(_reads_as_a_data_gap(m["text"]) for m in brief["missing"]):
                        flagged.append(card["id"])
                        await read("get_data_flags", thesis=card["id"])
                return prompt.messages[0].content.text, listed

    with LoopbackServer(api) as fake:
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
    # flags only where a missing condition is a data gap: never on the risk-only card
    assert flagged == [gap_card] and risk_card not in flagged
    assert [c["missing_count"] for c in listed["cards"][:2]] == [1, 1]


def test_the_prompt_names_the_data_gap_rule_and_never_flags_a_risk_condition():
    raw = PROMPT_FILE.read_text("utf-8")
    assert "data gap" in raw and "Risk must clear before arming" in raw
    assert "missing_count is above zero" not in raw
