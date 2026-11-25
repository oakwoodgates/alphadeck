"""The ``price-feed-review`` prompt's call plan, run through the in-process MCP SDK client against a fake API as a
steward (``/auth/me`` answers ``super_admin``): ``get_admin_status`` once, then ``section="price_repairs"`` and its
next page when the first was cut. At most 3 calls and 24,000 characters in all, on a panel of 3 renamed names, 4 never
priced and 2 re-filed under a new CIK, and on one big enough to need the second page. The prompt suggests an Adopt
only for the probe-verified symbol or the recommended CIK; a symbol or a CIK is never the model's. A plain caller is
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
)

NAME = "price-feed-review"
MAX_CALLS = 3  # the status + the panel's rows + the panel's next page
MAX_CHARS = 24_000  # three replies at the 8,000-character reply budget


def _run_the_plan(**panel: int):
    api = FakeApi(me_role="super_admin", status=afx.status_with_price_repairs(**panel))
    spent: list[tuple[str, int]] = []

    async def steps(session):
        listed = {p.name for p in (await session.list_prompts()).prompts}
        prompt = await session.get_prompt(NAME)
        read = reader(session, spent)
        status = await read("get_admin_status")  # 1
        repairs = await read("get_admin_status", section="price_repairs")  # 2
        more = None
        if repairs["next_cursor"] is not None:  # the panel's rows were cut
            more = await read(  # 3
                "get_admin_status", section="price_repairs", cursor=repairs["next_cursor"]
            )
        return listed, prompt.messages[0].content.text, status, repairs, more

    return (*run_in_process(api, steps), spent)


def test_the_price_feed_review_call_plan_costs_at_most_three_calls_and_24000_characters():
    listed, prompt, status, repairs, more, spent = _run_the_plan()
    assert NAME in listed and more is None and len(spent) == 2
    check_the_spend(spent, calls=MAX_CALLS, chars=MAX_CHARS)
    check_the_prompt(NAME, prompt, tool="get_admin_status", calls=MAX_CALLS, chars=MAX_CHARS)
    assert prompt == prompt_file(NAME) and prompt.startswith("Price feed review: ")
    # the default reply counts what is owed; the section carries each name's evidence and its recommendation
    counts = status["price_repairs"]
    assert (counts["renamed"], counts["unpriceable"], counts["successor"]) == (3, 4, 2)
    assert counts["asof"] == afx.ASOF
    assert repairs["totals"]["renamed"] == 3 and repairs["totals"]["successor"] == 2
    verified, none_verified, unprobed = repairs["renamed"]
    assert verified["recommended"] == "TK01N" and verified["candidates"][0]["verified"] is True
    assert (verified["candidates"][0]["overlap"], verified["candidates"][0]["ratio"]) == (250, 1.0)
    assert none_verified["probed"] is True and none_verified["recommended"] is None
    assert not any(c["verified"] for c in none_verified["candidates"])
    assert unprobed["probed"] is False and unprobed["recommended"] is None
    assert verified["edgar_tickers"] == ["TK01N"] and verified["former_names"]
    assert repairs["unpriceable"][0]["vendor_error"].startswith("404 Not Found")
    # W4-1: a never-priced ADR member's probed candidate rides with both class marks, nothing recommended
    adr = repairs["unpriceable"][0]
    assert adr["member_class"] == "adr" and adr["probed"] is True and adr["recommended"] is None
    (cand,) = adr["candidates"]
    assert (cand["symbol"], cand["listing_class"], cand["bars"]) == ("TKTAF", "ordinary", 240)
    assert cand["last_bar"] == "2026-11-05"
    ok_row, ambiguous = repairs["successor"]
    assert ok_row["recommended"] == ok_row["candidates"][0]["cik"]
    assert ok_row["candidates"][0]["identity_read"] is True and ok_row["stored_cik"]
    assert ambiguous["recommended"] is None and len(ambiguous["candidates"]) == 2
    assert repairs["probe_evaluated"] is True and repairs["probe_error"] is None
    assert repairs["successor_error"] is None


def test_a_cut_panel_is_read_to_its_next_page_within_the_budget():
    listed, prompt, status, repairs, more, spent = _run_the_plan(
        renamed=8, unpriceable=10, successor=4
    )
    assert repairs["next_cursor"] is not None and more is not None and len(spent) == 3
    check_the_spend(spent, calls=MAX_CALLS, chars=MAX_CHARS)
    print("price-feed-review cost:", len(spent), "calls,", sum(n for _, n in spent), "chars", spent)
    seen = {k: len(repairs[k]) + len(more[k]) for k in ("renamed", "unpriceable", "successor")}
    assert seen == {"renamed": 8, "unpriceable": 10, "successor": 4}  # none lost between the pages
    assert more["next_cursor"] is None


def test_the_prompt_suggests_only_the_probe_verified_symbol_and_the_recommended_cik():
    text = prompt_file(NAME)
    for needle in (
        "Adopt <recommended>",
        "probe-verified",
        "Set price symbol",
        "Acknowledge",
        "remove it in the basket editor",
        "Adopt CIK",
        "identity_read",
        "member_class",
        "listing_class",
        "the classes differ",
        "Admin > Price feed repairs",
        "propose none",
    ):
        assert needle in text, needle


def test_the_price_feed_review_recipe_is_the_prompt_and_a_plain_principal_is_refused():
    assert check_the_gate(NAME, {}).startswith("Price feed review: ")
