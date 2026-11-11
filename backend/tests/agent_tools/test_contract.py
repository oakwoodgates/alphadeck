"""The shared tool contract: every registered ``Contract`` rejects the same bad calls the same way, and every
rejection names what the tool takes. Parametrized over the registry, so a new tool's contract joins by registering.
"""

from __future__ import annotations

import pytest

pytest.importorskip("mcp")

from agent_tools import tool_args  # noqa: E402
from agent_tools.errors import ToolFailure  # noqa: E402
from agent_tools.tool_args import CONTRACTS, Contract, Setting  # noqa: E402
from agent_tools.views import (  # noqa: E402,F401  (importing registers the contracts)
    admin,
    dashboards,
    research,
)


def ids(contracts):
    return [c.name for c in contracts]


def reject(contract: Contract, **kw) -> ToolFailure:
    args = {"asof": None, "section": None, "narrows": {}, "given": {}}
    args.update(kw)
    with pytest.raises(ToolFailure) as caught:
        tool_args.check(contract, **args)
    assert caught.value.code == "invalid_arguments"
    return caught.value


def test_the_eight_dashboards_are_registered():
    assert len(CONTRACTS) >= 8


@pytest.mark.parametrize("c", CONTRACTS, ids=ids(CONTRACTS))
def test_asof_on_an_undated_tool_is_rejected(c):
    if c.dated:
        tool_args.check(c, asof="2026-11-05", section=None, narrows={}, given={})
        return
    err = reject(c, asof="2026-11-05")
    assert err.message == f"`{c.name}` is not dated, so it takes no asof; " + c.accepted()


@pytest.mark.parametrize("c", CONTRACTS, ids=ids(CONTRACTS))
def test_a_setting_it_does_not_take_is_rejected(c):
    err = reject(c, given={"not_a_setting": "x"})
    assert err.message == f"`{c.name}` does not take not_a_setting; " + c.accepted()


@pytest.mark.parametrize("c", CONTRACTS, ids=ids(CONTRACTS))
def test_a_narrowing_it_does_not_take_is_rejected(c):
    missing = [n for n in tool_args.NARROWING_ORDER if n not in c.narrows]
    if not missing:
        pytest.skip("takes every narrowing")
    for name in missing:
        err = reject(c, narrows={name: "x"})
        assert err.message == f"`{c.name}` does not take {name}; " + c.accepted()


@pytest.mark.parametrize("c", CONTRACTS, ids=ids(CONTRACTS))
def test_an_unknown_section_is_rejected(c):
    err = reject(c, section="nope")
    assert err.message == f"Unknown section 'nope'; `{c.name}` has: {', '.join(c.section_names())}."


@pytest.mark.parametrize("c", CONTRACTS, ids=ids(CONTRACTS))
def test_a_section_whose_need_is_missing_is_rejected(c):
    needing = [(s, c.needs_of(s)) for s in c.section_names() if c.needs_of(s)]
    if not needing:
        pytest.skip("no section needs a thesis or a group")
    for section, need in needing:
        err = reject(c, section=section, narrows={})
        what = {
            "thesis": "thesis (a card's id or name)",
            "group": "group (a key from the default reply)",
            "t": "t (a ticker, or a name for a placement without one)",
            "segment": "segment (a link label from the default reply)",
            "fact": "fact (revenue_mix, shares_outstanding or cash_burn)",
        }[need]
        assert err.message == f"section={section} of `{c.name}` needs {what}."
        # given the need, the same call passes
        tool_args.check(c, asof=None, section=section, narrows={need: "x"}, given={})


@pytest.mark.parametrize("c", CONTRACTS, ids=ids(CONTRACTS))
def test_a_settings_own_value_rule_is_enforced(c):
    ruled = [s for s in c.settings if s.validate is not None]
    if not ruled:
        pytest.skip("no setting carries a value rule")
    for s in ruled:
        err = reject(c, given={s.name: "definitely not valid"})
        assert err.message == s.validate("definitely not valid")


def test_the_since_rule_keeps_its_wording_and_its_accepted_values():
    since = next(s for s in tool_args.CONTRACTS if s.name == "what-changed").settings[0]
    rule = "since takes last_night, 7d or a real date as YYYY-MM-DD."
    for good in ("last_night", "7d", "2026-11-05"):
        assert since.validate(good) is None
    for bad in ("yesterday", "last-night", "2026-13-01", "2026-13-40", "2026-02-30", "30d"):
        assert since.validate(bad) == rule


@pytest.mark.parametrize("c", CONTRACTS, ids=ids(CONTRACTS))
def test_accepted_names_every_setting_narrowing_section_and_cursor(c):
    sentence = c.accepted()
    assert sentence.startswith("it takes ") and sentence.endswith(".")
    names = set(sentence.removeprefix("it takes ").removesuffix(".").split(", "))
    expected = set(c.setting_names()) | set(c.narrows) | {"section", "cursor"}
    if c.dated:
        expected.add("asof")
    assert names == expected


@pytest.mark.parametrize("c", CONTRACTS, ids=ids(CONTRACTS))
def test_defaults_are_filled_and_a_given_value_wins(c):
    assert tool_args.check(c, asof=None, section=None, narrows={}, given={}) == {
        s.name: s.default for s in c.settings
    }


def test_a_setting_without_a_rule_is_a_plain_pass_through():
    c = Contract("x", False, ("a",), settings=(Setting("n", "integer", (1, 2), 1, "x"),))
    assert tool_args.check(c, asof=None, section="a", narrows={}, given={"n": 2}) == {"n": 2}


# --- the prompt table -------------------------------------------------------------------------------------------

import asyncio  # noqa: E402

from mcp.client.client import Client  # noqa: E402

from agent_tools import server as server_mod  # noqa: E402
from agent_tools.client import ApiClient  # noqa: E402
from agent_tools.prompt_args import PROMPT_NAMES, PROMPTS  # noqa: E402
from tests.agent_tools.fake_api import FakeApi  # noqa: E402


def advertised():
    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=FakeApi().transport())
        try:
            async with Client(server_mod.build_server(lambda: client)) as session:
                return (await session.list_prompts()).prompts
        finally:
            await client.aclose()

    return asyncio.run(go())


def test_prompts_list_advertises_exactly_the_table():
    listed = advertised()  # a plain caller: the steward-scoped prompt is hidden
    offered = [s for s in PROMPTS if s.scope == "read"]
    assert [p.name for p in listed] == [s.name for s in offered]
    assert [s.name for s in PROMPTS if s.scope != "read"] == ["splits-review"]
    assert list(PROMPT_NAMES) == [s.name for s in PROMPTS]
    for spec, prompt in zip(offered, listed, strict=True):
        assert prompt.description == spec.description, spec.name
        assert [(a.name, bool(a.required)) for a in prompt.arguments] == [
            (name, required) for name, required, _ in spec.arguments
        ], spec.name


@pytest.mark.parametrize("spec", PROMPTS, ids=[s.name for s in PROMPTS])
def test_every_prompt_file_fills_every_placeholder(spec):
    text = server_mod._prompt_text(spec.name)
    given = {n: "2026-11-05" for n, required, _ in spec.arguments if required}
    if spec.check is not None:
        given["end"] = "2026-11-06"
    out = server_mod._prompt_handler(spec)(**given)
    assert "{{" not in out and out != text
