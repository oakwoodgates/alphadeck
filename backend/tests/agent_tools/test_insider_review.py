"""The ``insider-review`` prompt's call plan, run through the in-process MCP SDK client against a fake API as a steward
(``/auth/me`` answers ``super_admin``): ``get_insider_holds`` once, its owed bucket's next page when the default reply
was cut, then ``section="one"`` for at most 2 tickers. At most 4 calls and 32,000 characters in all, on 40 owed held
rows. There is no amendments tool (that panel's GET is 19 to 38 s and 29 MB), so the prompt reads held rows only and
says the Form 4/As are decided by hand on Admin > Insider amendments. A plain caller is not offered the prompt, and the
steward reads it as a recipe.
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

NAME = "insider-review"
MAX_CALLS = 4  # the held rows + the owed bucket's next page + at most 2 tickers read in full
MAX_CHARS = 32_000  # four replies at the 8,000-character reply budget


def _run_the_plan(arguments: dict[str, str]):
    api = FakeApi(me_role="super_admin", holds=afx.holds(owed=40))
    spent: list[tuple[str, int]] = []

    async def steps(session):
        listed = {p.name for p in (await session.list_prompts()).prompts}
        prompt = await session.get_prompt(NAME, arguments)
        read = reader(session, spent)
        default = await read("get_insider_holds")  # 1
        rest = None
        if default["next_cursor"] is not None:  # the default reply was cut
            rest = await read(  # 2
                "get_insider_holds", section="owed", cursor=default["next_cursor"]
            )
        # at most 2 tickers: the named one first, then the first owed rows'
        tickers = [arguments["t"]] if "t" in arguments else []
        for row in default["owed"]:
            if row["ticker"] not in tickers:
                tickers.append(row["ticker"])
        ones = [
            await read("get_insider_holds", section="one", t=ticker) for ticker in tickers[:2]
        ]  # 3 and 4
        return listed, prompt.messages[0].content.text, default, rest, ones

    return (*run_in_process(api, steps), spent)


def test_the_insider_review_call_plan_costs_at_most_four_calls_and_32000_characters():
    listed, prompt, default, rest, ones, spent = _run_the_plan({})
    assert NAME in listed
    assert tools_called(spent) == ["get_insider_holds"] * 4
    check_the_spend(spent, calls=MAX_CALLS, chars=MAX_CHARS)
    print("insider-review cost:", len(spent), "calls,", sum(n for _, n in spent), "chars", spent)
    check_the_prompt(NAME, prompt, tool="get_insider_holds", calls=MAX_CALLS, chars=MAX_CHARS)
    assert prompt.startswith("Insider review: ")
    # what the report needs is in those reads: the filing's dates as written, the API's own suggested date and
    # whether it agrees, why the rule did not place the row, the filing's link, and the decision history
    assert default["counts"]["owed"] == 40 and default["bucket_words"]["owed"]
    assert default["next_cursor_for"] == "owed" and rest["section"] == "owed"
    assert (
        rest["owed"][0]["hold_id"] != default["owed"][0]["hold_id"]
    )  # the next page, not the first again
    row = default["owed"][0]
    assert row["accession"] and row["filing_url"].startswith("https://www.sec.gov/")
    assert (row["date_as_filed"], row["period_of_report_as_filed"]) == ("10/31/2027", "10/31/2026")
    assert row["signature_date_as_filed"] == "11/02/2026" and row["market_date"] == "2026-11-02"
    assert row["suggested_date"] == "2026-10-31" and row["agrees"] is True
    assert (
        row["row"]["txn_code"] == "P" and "owed_why_total_chars" in row
    )  # a long reason is cut, its length beside
    full = ones[0]["holds"][0]
    assert ones[0]["found"] == 1 and full["ticker"] == row["ticker"] == "TK01"
    assert full["owed_why"].startswith("Filed late") and "owed_why_total_chars" not in full
    assert full["history_total"] == 0 and full["decision"] is None


def test_a_named_ticker_is_read_in_full_first():
    listed, prompt, default, rest, ones, spent = _run_the_plan({"t": "TK05"})
    assert prompt.startswith("Insider review, starting with TK05: ") and "{{" not in prompt
    assert [o["t"] for o in ones] == ["TK05", "TK01"]  # the named one, then the first owed row's
    check_the_spend(spent, calls=MAX_CALLS, chars=MAX_CHARS)


def test_the_prompt_reads_held_rows_only_and_names_the_amendments_as_manual():
    text = prompt_file(NAME)
    for needle in (
        "accept the suggested date",
        "agrees",
        "set a date on the panel",
        "Dismiss: not a trade",
        "quoted as written",
        "Admin > Held insider rows",
        "Admin > Insider amendments",
        "no tool",
    ):
        assert needle in text, needle
    assert "get_insider_amendments" not in text and "Use the correction" not in text


def test_the_insider_review_recipe_is_the_prompt_and_a_plain_principal_is_refused():
    assert check_the_gate(NAME, {"t": "TK01"}).startswith("Insider review, starting with TK01: ")
    assert check_the_gate(NAME, {}).startswith("Insider review: ")
