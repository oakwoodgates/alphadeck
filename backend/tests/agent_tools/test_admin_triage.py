"""The ``admin-triage`` prompt's call plan, run through the in-process MCP SDK client against a fake API as a steward
(``/auth/me`` answers ``super_admin``): ``get_admin_status``, ``get_shared_facts``, ``get_insider_holds`` only when
``insider_holds_owed`` is above zero, then ``get_admin_onboarding``. At most 4 calls and 32,000 characters in all, on a
production-sized queue (40 companies at the longest notes, 40 owed held rows, 116 names onboarding). No tool reads the
Insider amendments panel (its GET is 19 to 38 s and 29 MB), so the prompt names it as read by hand. A plain caller is
not offered the prompt, and the steward reads it as a recipe.
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

NAME = "admin-triage"
MAX_CALLS = 4  # status, the facts queue, the owed held rows (only when owed), onboarding
MAX_CHARS = 32_000  # four replies at the 8,000-character reply budget


def _api(*, owed: int) -> FakeApi:
    status = afx.status()
    status["insider_holds_owed"] = owed
    return FakeApi(
        me_role="super_admin",
        status=status,
        queue=afx.queue_items(
            40, 5, note_chars=afx.LONGEST_NOTE, passage_chars=afx.LONGEST_PASSAGE, passages=3
        ),
        holds=afx.holds(owed=owed),
        admin_onboarding=afx.admin_onboarding(),
    )


def _run_the_plan(api: FakeApi):
    spent: list[tuple[str, int]] = []

    async def steps(session):
        listed = {p.name for p in (await session.list_prompts()).prompts}
        prompt = await session.get_prompt(NAME)
        read = reader(session, spent)
        status = await read("get_admin_status")  # 1
        facts = await read("get_shared_facts")  # 2
        holds = None
        if status["insider_holds_owed"] > 0:
            holds = await read("get_insider_holds")  # 3: only when held rows are owed
        onboarding = await read("get_admin_onboarding")  # 4
        return listed, prompt.messages[0].content.text, status, facts, holds, onboarding

    return (*run_in_process(api, steps), spent)


def test_the_admin_triage_call_plan_costs_at_most_four_calls_and_32000_characters():
    listed, prompt, status, facts, holds, onboarding, spent = _run_the_plan(_api(owed=40))
    assert NAME in listed
    assert tools_called(spent) == [
        "get_admin_status",
        "get_shared_facts",
        "get_insider_holds",
        "get_admin_onboarding",
    ]
    check_the_spend(spent, calls=MAX_CALLS, chars=MAX_CHARS)
    print("admin-triage cost:", len(spent), "calls,", sum(n for _, n in spent), "chars", spent)
    check_the_prompt(NAME, prompt, tool="get_admin_status", calls=MAX_CALLS, chars=MAX_CHARS)
    assert prompt.startswith("Admin triage: ")
    # what the worklist needs is in those four reads
    assert status["splits_needs_review"] == 3 and status["insider_holds_owed"] == 40
    assert {"renamed", "unpriceable", "successor"} <= set(status["price_repairs"])
    assert status["cron"]["status"] == "healthy" and status["cron"]["alarms"]
    assert facts["total"] == 40 and facts["pen_only_total"] == 5
    first = facts["items"][0]
    assert first["ticker"] == "TK01" and first["cards"] == 3 and first["security_id"]
    assert holds["counts"]["owed"] == 40 and holds["owed"][0]["accession"]
    assert holds["owed"][0]["hold_id"] and holds["owed"][0]["ticker"]
    assert onboarding["counts"]["failed"] == 3 and onboarding["pending"][0]["status"] == "failed"


def test_the_held_rows_read_is_skipped_when_none_are_owed():
    _, _, status, _, holds, _, spent = _run_the_plan(_api(owed=0))
    assert status["insider_holds_owed"] == 0 and holds is None
    assert tools_called(spent) == ["get_admin_status", "get_shared_facts", "get_admin_onboarding"]
    check_the_spend(spent, calls=MAX_CALLS - 1, chars=MAX_CHARS)


def test_the_prompt_names_every_panel_the_amendments_as_manual_and_the_follow_up_reviews():
    text = prompt_file(NAME)
    for needle in (
        "get_shared_facts",
        "get_insider_holds",
        "get_admin_onboarding",
        "insider_holds_owed",
        "run splits-review",
        "run facts-review",
        "run insider-review",
        "run price-feed-review",
        "Retry",
        "/admin/facts/<security_id>",
    ):
        assert needle in text, needle
    # no tool reads the amendments: the prompt says so and says where they are decided
    assert "get_insider_amendments" not in text
    assert "Admin > Insider amendments" in text and "No tool reads" in text


def test_the_admin_triage_recipe_is_the_prompt_and_a_plain_principal_is_refused():
    assert check_the_gate(NAME, {}).startswith("Admin triage: ")
