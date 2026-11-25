"""``get_recipe``: every guided read as text, for a client that cannot run MCP prompts (``agent_tools/server.py``).

The prompts stay the single source: a recipe is the SAME render ``prompts/get`` returns, through one function
(``render_prompt``), scope-gated by one helper (``prompt_held``). In-process against the fake API; no database.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from mcp.client.client import Client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from agent_tools import server as server_mod  # noqa: E402
from agent_tools.client import ApiClient  # noqa: E402
from agent_tools.principal import CURRENT_PRINCIPAL, Principal  # noqa: E402
from agent_tools.prompt_args import PROMPTS, PromptSpec  # noqa: E402
from tests.agent_tools.fake_api import FakeApi  # noqa: E402

STEWARD = Principal(frozenset({"read", "steward"}), "s", "pat")
PLAIN = Principal(frozenset({"read"}), "u", "pat")

# one valid set of arguments per recipe that takes any (the values ``prompts/get`` is also given)
ARGS = {
    "board-review": {"asof": "2026-11-05", "max_items": "3"},
    "explain-call": {"thesis": "Thesis 01", "asof": "2026-11-05"},
    "morning-brief": {"asof": "2026-11-05"},
    "ticker-check": {"t": "TK01", "asof": "2026-11-05"},
    "compare-rotation": {
        "start": "2026-10-01",
        "end": "2026-11-05",
        "group_by": "sector",
        "group": "Group 01",
    },
    "card-data-check": {"thesis": "Thesis 01", "asof": "2026-11-05"},
    "discovery-review": {"thesis": "Thesis 01"},
    "splits-review": {"t": "TK01"},
}


def run_as(principal, body, api: FakeApi | None = None):
    """``body(session)`` against an in-process server as ``principal``; also returns the API requests made."""
    fake = api or FakeApi()

    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=fake.transport())
        reset = CURRENT_PRINCIPAL.set(principal)
        try:
            async with Client(server_mod.build_server(lambda: client)) as session:
                return await body(session)
        finally:
            CURRENT_PRINCIPAL.reset(reset)
            await client.aclose()

    return asyncio.run(go()), fake.requests


def test_a_stewards_get_recipe_of_splits_review_is_the_text_prompts_get_returns():
    async def body(session):
        prompt = await session.get_prompt("splits-review", {"t": "TK01"})
        recipe = await session.call_tool(
            "get_recipe", {"name": "splits-review", "arguments": {"t": "TK01"}}
        )
        return prompt.messages[0].content.text, recipe

    (prompt_text, recipe), requests = run_as(STEWARD, body)
    assert not recipe.is_error, recipe.content[0].text
    assert recipe.structured_content["text"] == prompt_text
    assert prompt_text.startswith("Splits review, starting with TK01: ")
    assert requests == []  # a recipe is the package's own file: no API request


def _json(result) -> dict:
    return json.loads(result.content[0].text)


def _error(result) -> dict:
    assert result.is_error, result.content[0].text
    return _json(result)


def test_every_recipe_text_equals_prompts_get_for_every_principal_that_holds_it():
    async def body(session):
        listed = {p.name for p in (await session.list_prompts()).prompts}
        out = {}
        for spec in PROMPTS:
            if spec.name not in listed:
                continue
            args = ARGS[spec.name]
            prompt = await session.get_prompt(spec.name, args)
            recipe = await session.call_tool("get_recipe", {"name": spec.name, "arguments": args})
            assert not recipe.is_error, (spec.name, recipe.content[0].text)
            assert recipe.structured_content["text"] == prompt.messages[0].content.text, spec.name
            out[spec.name] = recipe.structured_content
        return out

    for principal, held in (
        (STEWARD, {s.name for s in PROMPTS}),
        (PLAIN, {s.name for s in PROMPTS if s.scope == "read"}),
    ):
        replies, requests = run_as(principal, body)
        assert set(replies) == held and requests == []
        for name, reply in replies.items():
            assert "{{" not in reply["text"], name
            assert reply["name"] == name and reply["asof"] is None and reply["source"] == "live"


def test_the_defaults_fill_exactly_as_prompts_get_fills_them():
    async def body(session):
        pairs = []
        for name, args in (
            ("board-review", {}),
            ("morning-brief", {}),
            ("compare-rotation", {"start": "2026-10-01", "end": "2026-11-05"}),
        ):
            prompt = await session.get_prompt(name, args)
            recipe = await session.call_tool("get_recipe", {"name": name, "arguments": args})
            pairs.append((prompt.messages[0].content.text, recipe.structured_content["text"]))
        # no ``arguments`` at all is the same as an empty map
        bare = await session.call_tool("get_recipe", {"name": "morning-brief"})
        return pairs, bare.structured_content["text"]

    (pairs, bare), _ = run_as(PLAIN, body)
    for from_prompt, from_recipe in pairs:
        assert from_prompt == from_recipe
    board, brief, compare = (text for _, text in pairs)
    assert "limit=5" in board and "the current night (omit asof)" in board
    assert bare == brief and "the current night (omit asof)" in bare
    assert "grouped by business_type." in compare and "{{" not in compare


def test_an_integer_argument_is_read_as_the_prompts_get_path_reads_it():
    async def body(session):
        prompt = await session.get_prompt("board-review", {"max_items": "03"})
        recipe = await session.call_tool(
            "get_recipe", {"name": "board-review", "arguments": {"max_items": "03"}}
        )
        bad = await session.call_tool(
            "get_recipe", {"name": "board-review", "arguments": {"max_items": "many"}}
        )
        return prompt.messages[0].content.text, recipe.structured_content["text"], bad

    (from_prompt, from_recipe, bad), _ = run_as(PLAIN, body)
    assert from_prompt == from_recipe and "first 3 queue ids" in from_recipe
    assert _error(bad)["code"] == "invalid_arguments"
    assert "max_items must be a whole number" in _error(bad)["message"]


def test_the_index_lists_the_recipes_the_caller_holds_in_prompt_order():
    async def body(session):
        return await session.call_tool("get_recipe", {})

    for principal, expected in (
        (PLAIN, [s for s in PROMPTS if s.scope == "read"]),
        (STEWARD, list(PROMPTS)),
    ):
        result, requests = run_as(principal, body)
        assert not result.is_error, result.content[0].text
        index = result.structured_content
        assert [r["name"] for r in index["recipes"]] == [s.name for s in expected]
        for entry, spec in zip(index["recipes"], expected, strict=True):
            assert entry["description"] == spec.description
            assert entry["arguments"] == [
                {"name": n, "required": req, "default": default}
                for n, req, default in spec.arguments
            ]
            assert set(entry) == {"name", "description", "arguments"}
        assert index["asof"] is None and index["source"] == "live"
        assert index["next_cursor"] is None
        assert requests == []
    # a plain principal's index never names a steward recipe
    plain, _ = run_as(PLAIN, body)
    assert "splits-review" not in plain.content[0].text


def test_a_plain_principal_is_forbidden_a_steward_recipe_before_anything_else():
    async def body(session):
        no_args = await session.call_tool("get_recipe", {"name": "splits-review"})
        bad_args = await session.call_tool(
            "get_recipe", {"name": "splits-review", "arguments": {"nonsense": "x"}}
        )
        fine = await session.call_tool(
            "get_recipe", {"name": "ticker-check", "arguments": {"t": "TK01"}}
        )
        return no_args, bad_args, fine

    (no_args, bad_args, fine), requests = run_as(PLAIN, body)
    for result in (no_args, bad_args):
        assert _error(result) == {
            "code": "forbidden",
            "message": "This recipe needs the steward scope.",
            "retryable": False,
        }
    assert not fine.is_error and requests == []  # zero API requests for all three


def test_an_unknown_name_and_bad_arguments_are_invalid_arguments_with_the_rule():
    async def body(session):
        return {
            "unknown": await session.call_tool("get_recipe", {"name": "no-such-recipe"}),
            "missing": await session.call_tool("get_recipe", {"name": "explain-call"}),
            "foreign": await session.call_tool(
                "get_recipe", {"name": "morning-brief", "arguments": {"thesis": "Thesis 01"}}
            ),
            "check": await session.call_tool(
                "get_recipe",
                {
                    "name": "compare-rotation",
                    "arguments": {"start": "2026-11-05", "end": "2026-11-05"},
                },
            ),
            "link": await session.call_tool(
                "get_recipe",
                {
                    "name": "compare-rotation",
                    "arguments": {"start": "2026-10-01", "end": "2026-11-05", "group_by": "link"},
                },
            ),
            "arguments_without_a_name": await session.call_tool(
                "get_recipe", {"arguments": {"t": "TK01"}}
            ),
            "cursor_with_a_name": await session.call_tool(
                "get_recipe", {"name": "morning-brief", "cursor": 1}
            ),
        }

    replies, requests = run_as(PLAIN, body)
    errors = {key: _error(result) for key, result in replies.items()}
    assert all(
        e["code"] == "invalid_arguments" and e["retryable"] is False for e in errors.values()
    )
    assert errors["unknown"]["message"] == "Unknown recipe; get_recipe() lists yours."
    assert "explain-call needs thesis" in errors["missing"]["message"]
    assert "morning-brief does not take thesis" in errors["foreign"]["message"]
    assert "asof" in errors["foreign"]["message"]  # it names what the recipe does take
    assert (
        errors["check"]["message"]
        == "Invalid arguments: start 2026-11-05 must be before end 2026-11-05."
    )
    assert "group_by=link needs group" in errors["link"]["message"]
    assert "name" in errors["arguments_without_a_name"]["message"]
    assert "cursor" in errors["cursor_with_a_name"]["message"]
    assert requests == []


def test_the_rule_the_prompts_path_gives_is_the_rule_the_tool_gives():
    async def body(session):
        args = {"start": "2026-11-05", "end": "2026-11-05"}
        try:
            await session.get_prompt("compare-rotation", args)
        except MCPError as error:
            from_prompt = error.error.message
        recipe = await session.call_tool(
            "get_recipe", {"name": "compare-rotation", "arguments": args}
        )
        return from_prompt, _error(recipe)["message"]

    (from_prompt, from_recipe), _ = run_as(PLAIN, body)
    assert (
        from_prompt
        == from_recipe
        == "Invalid arguments: start 2026-11-05 must be before end 2026-11-05."
    )


@pytest.mark.parametrize(
    "arguments",
    [
        {f"key_{chr(97 + i)}": "x" for i in range(9)},  # nine keys
        {"Thesis": "x"},  # a key outside ^[a-z_]{1,20}$
        {"a" * 21: "x"},
        {"thesis": "x" * 201},  # a value past 200 characters
    ],
)
def test_the_arguments_are_bounded_before_any_recipe_is_read(arguments):
    async def body(session):
        return await session.call_tool(
            "get_recipe", {"name": "explain-call", "arguments": arguments}
        )

    result, requests = run_as(PLAIN, body)
    assert _error(result)["code"] == "invalid_arguments" and requests == []


def test_the_largest_reply_is_within_the_8000_character_budget():
    """The worst case: every argument of every recipe at 200 characters (a date or a number where the check needs one)."""
    biggest = ("", 0)

    async def body(session):
        nonlocal biggest
        for spec in PROMPTS:
            args = {}
            for name, _, default in spec.arguments:
                if name in ("start", "end", "asof"):
                    args[name] = {"start": "2026-10-01", "end": "2026-11-05"}.get(
                        name, "2026-11-05"
                    )
                elif name == "max_items":
                    args[name] = "9" * 200
                elif name == "group_by":
                    args[name] = "sector"
                else:
                    args[name] = "x" * 200
            result = await session.call_tool("get_recipe", {"name": spec.name, "arguments": args})
            assert not result.is_error, (spec.name, result.content[0].text)
            chars = len(result.content[0].text)
            assert chars <= 8_000, (spec.name, chars)
            if chars > biggest[1]:
                biggest = (spec.name, chars)
        index = await session.call_tool("get_recipe", {})
        assert len(index.content[0].text) <= 8_000
        return len(index.content[0].text)

    index_chars, _ = run_as(STEWARD, body)
    print("largest get_recipe reply:", biggest, "index:", index_chars)
    assert biggest[1] > 0


def test_the_index_pages_through_budget_fit_with_a_cursor(monkeypatch):
    from agent_tools.views import recipe as recipe_view

    monkeypatch.setattr(recipe_view, "BUDGET", 900)

    async def body(session):
        seen, pages, cursor = [], 0, 0
        while cursor is not None:
            result = await session.call_tool("get_recipe", {"cursor": cursor})
            assert not result.is_error, result.content[0].text
            seen.extend(r["name"] for r in result.structured_content["recipes"])
            assert len(result.content[0].text) <= 900
            assert result.structured_content["next_cursor_for"] in (None, "recipes")
            cursor, pages = result.structured_content["next_cursor"], pages + 1
        return seen, pages

    (seen, pages), requests = run_as(STEWARD, body)
    assert pages > 1 and requests == []
    assert seen == [s.name for s in PROMPTS]  # every recipe, once, in order


def test_get_recipe_is_a_read_scope_read_only_tool_every_principal_lists():
    async def body(session):
        return {t.name: t for t in (await session.list_tools()).tools}

    for principal in (PLAIN, STEWARD):
        tools, _ = run_as(principal, body)
        tool = tools["get_recipe"]
        a = tool.annotations
        assert a.read_only_hint is True and a.destructive_hint is False, principal
        assert a.idempotent_hint is True and a.open_world_hint is False, principal
        assert server_mod.SCOPES["get_recipe"] == "read"
        assert {"asof", "source"} <= set(tool.output_schema["properties"])
        assert "nightly_window" in tool.output_schema["properties"]


def test_a_new_steward_prompt_reaches_the_index_and_the_gate_with_no_edit_to_the_tool(monkeypatch):
    """New steward prompts join ``PROMPTS``; the index, the render and the scope gate read that table, never a name list."""
    extra = PromptSpec(
        "zz-new-recipe", "A recipe added later.", (("asof", False, None),), scope="steward"
    )
    table = (*PROMPTS, extra)
    monkeypatch.setattr(server_mod, "PROMPTS", table)
    monkeypatch.setattr(server_mod, "PROMPT_SCOPES", {p.name: p.scope for p in table})
    monkeypatch.setattr(server_mod, "PROMPT_NAMES", tuple(p.name for p in table))
    real_text = server_mod._prompt_text  # noqa: SLF001
    monkeypatch.setattr(
        server_mod,
        "_prompt_text",
        lambda name: "New recipe as of {{asof}}." if name == extra.name else real_text(name),
    )

    async def body(session):
        index = await session.call_tool("get_recipe", {})
        recipe = await session.call_tool("get_recipe", {"name": extra.name})
        prompt = await session.get_prompt(extra.name)
        return index, recipe, prompt

    (index, recipe, prompt), _ = run_as(STEWARD, body)
    assert [r["name"] for r in index.structured_content["recipes"]][-1] == extra.name
    assert recipe.structured_content["text"] == "New recipe as of the current night (omit asof)."
    assert prompt.messages[0].content.text == recipe.structured_content["text"]

    async def plain(session):
        return (
            await session.call_tool("get_recipe", {}),
            await session.call_tool("get_recipe", {"name": extra.name}),
        )

    (plain_index, plain_recipe), _ = run_as(PLAIN, plain)
    assert extra.name not in plain_index.content[0].text
    assert _error(plain_recipe)["code"] == "forbidden"
