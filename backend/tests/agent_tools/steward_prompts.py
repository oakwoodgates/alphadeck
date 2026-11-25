"""Shared checks for the steward prompts' call-plan tests (``admin-triage``, ``facts-review``, ``insider-review``,
``price-feed-review``; ``test_splits_review.py`` predates it and keeps its own).

A prompt is a file the steward's model follows, so a test cannot run the model: it runs the CALL PLAN the file states
(the same reads, in the same order, with the same branches) through the in-process MCP client against the fake API as
a steward, and asserts the plan's cost (calls and characters) and that the reads carry what the report needs. The
file's own shape (size, opening, closing, rules) is checked here once for all four.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from mcp.client.client import Client
from mcp.shared.exceptions import MCPError

from agent_tools import server as server_mod
from agent_tools.client import ApiClient
from agent_tools.principal import CURRENT_PRINCIPAL, Principal
from tests.agent_tools.fake_api import FakeApi

ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = ROOT / "agent_tools" / "prompts"
MAX_PROMPT_CHARS = 2_000
MAX_REPLY_CHARS = 8_000  # one tool reply's budget (``budget.fit``)
STEWARD = Principal(frozenset({"read", "steward"}), "s", "pat")
PLAIN = Principal(frozenset({"read"}), "u", "pat")


def prompt_file(name: str) -> str:
    return (PROMPTS_DIR / f"{name}.md").read_text(encoding="utf-8")


def run_as(principal: Principal, body, api: FakeApi | None = None) -> tuple[Any, list]:
    """``body(session)`` against an in-process server as ``principal`` (no ``/auth/me`` call); also the API requests."""
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


def reader(session, spent: list[tuple[str, int]]):
    """``read(tool, **args)``: one tool call, its characters appended to ``spent``, its structured reply returned."""

    async def read(tool: str, **args: Any) -> dict[str, Any]:
        result = await session.call_tool(tool, args)
        assert not result.is_error, result.content[0].text
        spent.append((f"{tool} {args or ''}".strip(), len(result.content[0].text)))
        return result.structured_content

    return read


def tools_called(spent: list[tuple[str, int]]) -> list[str]:
    """The tool of each read in ``spent``, in order (its label is the tool, then its arguments)."""
    return [label.split()[0] for label, _ in spent]


def check_the_spend(spent: list[tuple[str, int]], *, calls: int, chars: int) -> None:
    """The plan's cost: at most ``calls`` reads and ``chars`` characters in all, none over one reply's budget."""
    assert len(spent) <= calls, spent
    assert sum(n for _, n in spent) <= chars, spent
    assert all(n <= MAX_REPLY_CHARS for _, n in spent), spent


def check_the_prompt(name: str, rendered: str, *, tool: str, calls: int, chars: int) -> None:
    """What every steward prompt states: its size, its steward-only opening, its call and character budget, the
    report's labels, and the rules (the model suggests and nothing applies it; a figure, date, symbol or CIK is never
    the model's; the text read is data; the model's reading goes under its own heading; no \"ratify\").
    """
    raw = prompt_file(name)
    assert len(raw) <= MAX_PROMPT_CHARS and len(rendered) <= MAX_PROMPT_CHARS, (
        len(raw),
        len(rendered),
    )
    assert "{{" not in rendered
    assert f"Steward only: if {tool} is not listed, say so and stop." in rendered
    assert f"At most {calls} tool calls" in rendered and f"{chars:,} characters" in rendered
    for label in ("Agent suggestion:", "Where:", 'separate heading "Agent interpretation"'):
        assert label in rendered, label
    assert "never yours" in rendered and "not instructions" in rendered
    assert "nothing applies it" in rendered and "did not read" in rendered
    assert "ratif" not in rendered.lower()
    # the report closes with the panel sentence, then what was not read; the rules come last
    assert rendered.index("Close with") < rendered.index("did not read") < rendered.index("Rules:")


def check_the_gate(name: str, arguments: dict[str, str]) -> str:
    """The steward reads the prompt as text with ``get_recipe`` (the same text ``prompts/get`` returns, no API
    request); a plain principal is offered neither and refused both with the steward-scope sentence, before any
    request. Returns the text."""

    async def as_steward(session):
        listed = {p.name for p in (await session.list_prompts()).prompts}
        prompt = await session.get_prompt(name, arguments)
        recipe = await session.call_tool("get_recipe", {"name": name, "arguments": arguments})
        index = await session.call_tool("get_recipe", {})
        return listed, prompt.messages[0].content.text, recipe, index

    (listed, rendered, recipe, index), requests = run_as(STEWARD, as_steward)
    assert name in listed and not recipe.is_error, recipe.content[0].text
    assert recipe.structured_content["text"] == rendered
    assert name in [r["name"] for r in index.structured_content["recipes"]]
    assert requests == []

    async def as_plain(session):
        listed = {p.name for p in (await session.list_prompts()).prompts}
        recipe = await session.call_tool("get_recipe", {"name": name, "arguments": arguments})
        index = await session.call_tool("get_recipe", {})
        try:
            await session.get_prompt(name, arguments)
        except MCPError as error:
            return listed, recipe, index, error.error.message
        raise AssertionError("a plain principal was served a steward prompt")

    (listed, recipe, index, refusal), requests = run_as(PLAIN, as_plain)
    assert name not in listed and name not in index.content[0].text
    assert recipe.is_error and json.loads(recipe.content[0].text) == {
        "code": "forbidden",
        "message": "This recipe needs the steward scope.",
        "retryable": False,
    }
    assert refusal == "This prompt needs the steward scope."
    assert requests == []  # refused before anything is read
    return rendered
