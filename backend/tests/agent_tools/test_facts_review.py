"""The ``facts-review`` prompt's call plan, run through the in-process MCP SDK client against a fake API as a steward
(``/auth/me`` answers ``super_admin``): ``get_shared_facts`` once (skipped when a ticker is named), then
``section="company"`` for at most 3 companies, then one ``fact=`` read when a located passage was cut. At most 5 calls
and 40,000 characters in all, on a production-sized queue (40 companies at the longest notes). The prompt suggests only
Approve, Dismiss or "open the company page"; a figure is never the model's. A plain caller is not offered the prompt,
and the steward reads it as a recipe.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from tests.agent_tools import admin_fixtures as afx  # noqa: E402
from tests.agent_tools.fake_api import FakeApi  # noqa: E402
from tests.agent_tools.mcp_session import run_in_process  # noqa: E402
from tests.agent_tools.steward_prompts import (  # noqa: E402
    check_the_gate,
    check_the_prompt,
    check_the_spend,
    prompt_file,
    reader,
    tools_called,
)

NAME = "facts-review"
MAX_CALLS = 5  # the queue + at most 3 company pages + one fact read
MAX_CHARS = 40_000  # five replies at the 8,000-character reply budget


def _api() -> FakeApi:
    return FakeApi(
        me_role="super_admin",
        queue=afx.queue_items(
            40, 5, note_chars=afx.LONGEST_NOTE, passage_chars=afx.LONGEST_PASSAGE, passages=3
        ),
        # one passage over the company page's 600-character cut, under the fact read's 3,000
        company=afx.company(passages=2, passage_chars=1_200),
    )


def _run_the_plan(arguments: dict[str, str]):
    api = _api()
    spent: list[tuple[str, int]] = []

    async def steps(session):
        listed = {p.name for p in (await session.list_prompts()).prompts}
        prompt = await session.get_prompt(NAME, arguments)
        read = reader(session, spent)
        queue = None
        if "t" not in arguments:
            queue = await read("get_shared_facts")  # 1: skipped when a ticker is named
        ticker = arguments.get("t") or queue["items"][0]["ticker"]
        company = await read("get_shared_facts", section="company", t=ticker)  # 2
        passages = company["shares"]["awaiting"]["located_passages"]
        one = None
        if any("excerpt_total_chars" in p for p in passages):  # a passage was cut
            one = await read(  # 3
                "get_shared_facts", section="company", t=ticker, fact="shares_outstanding"
            )
        return listed, prompt.messages[0].content.text, queue, company, one

    return (*run_in_process(api, steps), spent)


def test_the_facts_review_call_plan_costs_at_most_five_calls_and_40000_characters():
    listed, prompt, queue, company, one, spent = _run_the_plan({})
    assert NAME in listed
    assert tools_called(spent) == ["get_shared_facts"] * 3
    check_the_spend(spent, calls=MAX_CALLS, chars=MAX_CHARS)
    print("facts-review cost:", len(spent), "calls,", sum(n for _, n in spent), "chars", spent)
    check_the_prompt(NAME, prompt, tool="get_shared_facts", calls=MAX_CALLS, chars=MAX_CHARS)
    assert prompt.startswith("Facts review: ")
    # what the report needs is in those reads: the queue's first row, each waiting value's own figure, filing and
    # located passage, and the passage whole after the fact read
    assert queue["total"] == 40 and queue["pen_only_total"] == 5
    row = queue["items"][0]
    assert row["ticker"] == "TK01" and row["cards"] == 3 and row["security_id"]
    candidate = row["shares"]["candidate"]
    assert candidate["shares"] == 10_000_001.0 and candidate["filing_accession"]
    assert candidate["event_date"] == "2026-10-30" and candidate["flags"] == ["stale_share_count"]
    wait = company["shares"]["awaiting"]
    assert company["ticker"] == row["ticker"] and company["security_id"]
    assert (
        wait["shares"] == 10_000_001.0 and wait["filing_accepted"] and wait["passages_total"] == 2
    )
    cut = wait["located_passages"][0]
    assert (
        len(cut["excerpt"]) < 1_200 and cut["excerpt_total_chars"] == 1_200
    )  # cut, its length beside it
    whole = one["shares"]["awaiting"]["located_passages"][0]
    assert len(whole["excerpt"]) == 1_200 and "excerpt_total_chars" not in whole
    assert one["fact"] == "shares_outstanding" and "user_email" not in str(one)


def test_a_named_ticker_skips_the_queue_read():
    listed, prompt, queue, company, one, spent = _run_the_plan({"t": "TK01"})
    assert prompt.startswith("Facts review, starting with TK01: ") and "{{" not in prompt
    assert queue is None and len(spent) == 2
    assert tools_called(spent) == ["get_shared_facts"] * 2
    check_the_spend(spent, calls=MAX_CALLS - 1, chars=MAX_CHARS)
    assert company["ticker"] == "TK01" and one is not None


def test_the_prompt_suggests_only_the_panels_verbs_and_never_a_figure():
    text = prompt_file(NAME)
    for needle in (
        "Approve",
        "Dismiss",
        "open the company page",
        "/admin/facts/<security_id>",
        "never propose a figure",
        "the candidate's own figure",
    ):
        assert needle in text, needle
    assert "get_insider_amendments" not in text


def test_the_facts_review_recipe_is_the_prompt_and_a_plain_principal_is_refused():
    assert check_the_gate(NAME, {"t": "TK01"}).startswith("Facts review, starting with TK01: ")
    assert check_the_gate(NAME, {}).startswith("Facts review: ")
