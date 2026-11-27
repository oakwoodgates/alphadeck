"""The ``splits-review`` prompt's call plan, run through the in-process MCP SDK client against a fake API as a steward
(``/auth/me`` answers ``super_admin``, so ``stdio_principal`` learns the ``steward`` scope): ``get_splits`` once, the
sources_differ read, then ``section="unplaced"`` from the briefs shown (the default reply shows 5 of 7 unplaced
filings, so this read takes the extractor_abstained read's place), ``section="one"`` for the named ticker, then
``section="jumps"`` because the default reply counted an unlisted jump. At most 5 calls and 24,000 characters in all,
on the synthetic list. A plain caller is not offered the prompt.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("mcp")

from tests.agent_tools import admin_fixtures as afx  # noqa: E402
from tests.agent_tools.fake_api import FakeApi  # noqa: E402
from tests.agent_tools.mcp_session import run_in_process  # noqa: E402

MAX_CALLS = 5  # one list read + at most 3 names + the jumps call; the fixture needs 5
MAX_CHARS = 24_000
ROOT = Path(__file__).resolve().parents[2]
PROMPT_FILE = ROOT / "agent_tools" / "prompts" / "splits-review.md"


def test_the_splits_review_call_plan_costs_at_most_five_calls_and_24000_characters():
    spent: list[tuple[str, int]] = []
    api = FakeApi(me_role="super_admin", splits=afx.splits(extra_unplaced=5))  # 7 unplaced filings

    async def steps(session):
        listed = {p.name for p in (await session.list_prompts()).prompts}
        prompt = await session.get_prompt("splits-review", {"t": "TK01"})

        async def read(**args):
            result = await session.call_tool("get_splits", args)
            assert not result.is_error, result.content[0].text
            spent.append((f"get_splits {args or ''}".strip(), len(result.content[0].text)))
            return result.structured_content

        default = await read()  # 1
        differ = await read(section="needs_review", reason="sources_differ")  # 2
        # 3: counts.unplaced exceeds the briefs shown, so the unplaced read replaces the extractor_abstained one
        assert default["counts"]["unplaced"] > len(default["unplaced"])
        unplaced = await read(section="unplaced", cursor=len(default["unplaced"]))
        one = await read(
            section="one", t="TK01"
        )  # 4: the named name (fewer `one` reads: two reads before it)
        jumps = await read(section="jumps")  # 5: the default reply counted an unlisted jump
        return (
            listed,
            prompt.messages[0].content.text,
            default,
            differ,
            unplaced,
            one,
            jumps,
        )

    listed, prompt, default, differ, unplaced, one, jumps = run_in_process(api, steps)
    assert "splits-review" in listed
    assert prompt.startswith("Splits review, starting with TK01: ") and "{{" not in prompt
    assert 'section="unplaced"' in prompt and "listed_near" in prompt
    assert "same_ratio_listed" in prompt  # F8-3
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
    assert default["counts"]["needs_review_by_reason"] == {
        "sources_differ": 1,
        "extractor_abstained": 1,
        "one_source": 1,
    }
    assert [r["ticker"] for r in differ["needs_review"]] == ["TK01"]
    assert differ["needs_review"][0]["review_reason"] == "sources_differ"
    assert one["found"] == 2 and one["entries"][0]["sec"]["passage_chars"] > 300
    # section=one carries the stored closes around the open entry's first session; nothing else reads the tape
    assert one["entries"][0]["tape"]["bars"] and "tape" not in one["entries"][1]
    tk01 = one["entries"][0]["version_id"]
    assert [p for _, p, _ in api.requests if p.startswith("/admin")] == [
        "/admin/splits",
        "/admin/splits",
        "/admin/splits",
        "/admin/splits",
        f"/admin/splits/{tk01}/tape",
        "/admin/splits",
    ]
    # the unplaced read continues after the default reply's five briefs: the last two listed, then the dismissed one
    assert [r["ticker"] for r in default["unplaced"]] == ["TK09", "TK01", "TK30", "TK31", "TK32"]
    assert [r["ticker"] for r in unplaced["unplaced"]] == ["TK33", "TK34"]
    assert [r["ticker"] for r in unplaced["unplaced_dismissed"]] == ["TK05"]
    assert default["unplaced"][1]["listed_near"]["same_ratio"] is True
    # F8-3: the open TK01 names the listed split of its ratio near it in the default reply already
    assert default["needs_review"][0]["same_ratio_listed"][0]["days_from"] == -154
    assert [j["ticker"] for j in jumps["unlisted"]] == ["TK08"]


def test_a_plain_stdio_caller_is_not_offered_the_prompt_or_the_tools():
    api = FakeApi(me_role="user")

    async def steps(session):
        prompts = {p.name for p in (await session.list_prompts()).prompts}
        tools = {t.name for t in (await session.list_tools()).tools}
        return prompts, tools

    prompts, tools = run_in_process(api, steps)
    assert "splits-review" not in prompts and len(prompts) == 7
    steward = {
        "get_splits",
        "get_admin_status",
        "get_shared_facts",
        "get_insider_holds",
        "get_admin_onboarding",
    }
    assert not tools & steward and len(tools) == 15
