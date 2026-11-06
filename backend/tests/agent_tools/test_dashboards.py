"""The dashboards as agent views: the registry, ``list_dashboards``, ``read_dashboard``'s argument rules and (as the
steps land) each dashboard's default reply, sections and cursors over synthetic payloads at the MEASURED sizes.
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.client.client import Client  # noqa: E402

from agent_tools import server as server_mod  # noqa: E402
from agent_tools.budget import size  # noqa: E402
from agent_tools.client import ApiClient  # noqa: E402
from agent_tools.views import dashboards as dashboards_pkg  # noqa: E402
from agent_tools.views.dashboards import REGISTRY, SLUGS  # noqa: E402
from agent_tools.views.dashboards.listing import view_list_dashboards  # noqa: E402
from tests.agent_tools.fake_api import FakeApi  # noqa: E402


def run(api: FakeApi | None, body):
    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=(api or FakeApi()).transport())
        try:
            async with Client(server_mod.build_server(client)) as session:
                return await body(session)
        finally:
            await client.aclose()

    return asyncio.run(go())


def error_of(result) -> dict:
    assert result.is_error
    return json.loads(result.content[0].text)


# --- the registry and list_dashboards --------------------------------------------------------------------------


def test_the_registry_names_the_eight_dashboards():
    assert tuple(REGISTRY) == SLUGS and len(SLUGS) == 8
    assert {s for s in SLUGS if not REGISTRY[s].dated} == {"what-changed", "catalyst-calendar"}


def test_list_dashboards_names_the_eight_with_settings_and_sections_within_3000_chars():
    reply = view_list_dashboards()
    assert size(reply) <= 3_000
    assert [d["slug"] for d in reply["dashboards"]] == list(SLUGS)
    by_slug = {d["slug"]: d for d in reply["dashboards"]}
    assert by_slug["trigger-log"]["settings"][0] == {
        "name": "days",
        "type": "integer",
        "allowed": [30, 90, 180],
        "default": 90,
        "applies": "the window of every reply",
    }
    assert [s["name"] for s in by_slug["rotation-map"]["settings"]] == [
        "group_by",
        "include_silent",
    ]
    assert by_slug["what-changed"]["dated"] is False and by_slug["hot-board"]["dated"] is True
    assert by_slug["catalyst-calendar"]["narrows"] == ["thesis", "group"]
    assert "settings" not in by_slug["daily-digest"]
    for d in reply["dashboards"]:
        assert d["sections"]


def test_list_dashboards_through_the_server_is_a_pure_registry_read():
    api = FakeApi()

    async def body(session):
        return await session.call_tool("list_dashboards", {})

    result = run(api, body)
    assert not result.is_error and len(result.content[0].text) <= 3_000
    assert result.structured_content["asof"] is None
    assert api.requests == []  # no I/O


# --- argument rules: checked before any request ------------------------------------------------------------------


@pytest.mark.parametrize(
    "args,expect",
    [
        ({"dashboard": "what-changed", "asof": "2026-11-05"}, "not dated"),
        ({"dashboard": "catalyst-calendar", "asof": "2026-11-05"}, "not dated"),
        (
            {"dashboard": "daily-digest", "rows": "all"},
            "`daily-digest` takes only: asof, thesis, section.",
        ),
        (
            {"dashboard": "hot-board", "days": 30},
            "`hot-board` takes only: asof, rows, thesis, section.",
        ),
        ({"dashboard": "rotation-map", "thesis": "Thesis 01"}, "`thesis` does not narrow"),
        ({"dashboard": "hot-board", "group": "x"}, "`group` does not narrow"),
        ({"dashboard": "trigger-log", "include_silent": False}, "takes only: asof, days, role"),
        ({"dashboard": "hot-board", "section": "nope"}, "has: row"),
        ({"dashboard": "what-changed", "since": "2026-02-30"}, "real date"),
        ({"dashboard": "what-changed", "since": "yesterday"}, "since"),
        ({"dashboard": "rotation-map", "group_by": "cap"}, "group_by"),
        ({"dashboard": "trigger-log", "days": 45}, "days"),
        ({"dashboard": "no-such-dashboard"}, "dashboard"),
    ],
)
def test_a_setting_the_dashboard_does_not_take_is_invalid_arguments_naming_what_it_takes(
    args, expect
):
    api = FakeApi()

    async def body(session):
        return await session.call_tool("read_dashboard", args)

    err = error_of(run(api, body))
    assert err["code"] == "invalid_arguments" and err["retryable"] is False
    assert expect in err["message"], err["message"]
    assert not [
        r for r in api.requests if r[1].startswith("/dashboards")
    ]  # rejected before any read


# --- purity ---------------------------------------------------------------------------------------------------


def test_the_dashboard_views_import_nothing_from_the_client_server_httpx_mcp_or_errors():
    root = Path(dashboards_pkg.__file__).parent
    banned = {"httpx", "mcp", "agent_tools.client", "agent_tools.server", "agent_tools.errors"}
    files = list(root.glob("*.py"))
    assert files
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level == 0:
                    names = [module]
                else:  # resolve a relative import against agent_tools.views.dashboards
                    base = "agent_tools.views.dashboards".split(".")
                    base = base[: len(base) - (node.level - 1)]
                    names = [".".join([*base, *([module] if module else [])])]
                    if not module:
                        names = [".".join([*base, a.name]) for a in node.names]
            else:
                continue
            for name in names:
                assert not any(
                    name == b or name.startswith(b + ".") for b in banned
                ), f"{path.name} imports {name}"
