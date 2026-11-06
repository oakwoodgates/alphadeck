"""The dashboards as agent views: the registry, ``list_dashboards``, ``read_dashboard``'s argument rules and (as the
steps land) each dashboard's default reply, sections and cursors over synthetic payloads at the MEASURED sizes.
"""

from __future__ import annotations

import ast
import asyncio
import copy
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
from agent_tools.views.dashboards.common import Request  # noqa: E402
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


# --- the shared walk: every default reply and every section reply fits 8,000 and the cursor reaches the end -------

from tests.agent_tools import dashboard_fixtures as dfx  # noqa: E402

BUDGET = 8_000
UNDATED = {"what-changed", "catalyst-calendar"}


def call(slug: str, **args):
    """One ``read_dashboard`` call through the server; returns the structured reply."""
    payload = {"dashboard": slug, **({} if slug in UNDATED else {"asof": dfx.ASOF}), **args}

    async def body(session):
        return await session.call_tool("read_dashboard", payload)

    result = run(FakeApi(), body)
    assert not result.is_error, result.content[0].text
    assert len(result.content[0].text) <= BUDGET, (slug, args, len(result.content[0].text))
    return result.structured_content


def walk(slug: str, list_path: tuple[str, ...], **args) -> list:
    """Every row of the list at ``list_path`` across the cursor pages of one read."""
    rows: list = []
    cursor = 0
    pages = 0
    while cursor is not None:
        reply = call(slug, cursor=cursor, **args) if cursor else call(slug, **args)
        node = reply
        for key in list_path:
            node = node[key]
        rows.extend(node)
        pages += 1
        assert pages < 500, "cursor never ended"
        cursor = reply["next_cursor"]
        if cursor is not None:
            assert reply["next_cursor_for"] == "/".join(list_path)
    return rows


def assert_walk(rows: list, expected: list) -> None:
    assert rows == expected, (len(rows), len(expected))  # no repeats, no gaps, the API's order


def test_header_fields_are_the_same_on_every_dashboard():
    for slug in dfx.PAYLOADS:
        reply = call(slug)
        assert reply["dashboard"] == slug and reply["source"] == "live"
        assert reply["asof"] == (None if slug in UNDATED else dfx.ASOF)
        assert list(reply["sections"]) == list(REGISTRY[slug].sections)
        assert reply["settings"] == {s.name: s.default for s in REGISTRY[slug].settings}
        assert isinstance(reply["coverage"], dict)
        assert "cache" not in reply or reply["cache"] in ("hit", "computed")


# --- daily-digest -----------------------------------------------------------------------------------------------


def test_daily_digest_default_reply_copies_the_api_and_pages_the_changed_cards():
    src = dfx.daily_digest()
    reply = call("daily-digest")
    assert size(reply) <= BUDGET
    assert reply["cache"] == "computed" and reply["compute_ms"] == 41_700
    assert reply["clock"] == "record"
    assert reply["counts"] == src["counts"] and reply["nights"] == src["nights"]
    assert reply["forming"] == src["forming"] and reply["positions"] == src["positions"]
    assert reply["coverage"]["flagged"]["count"] == 8
    assert len(reply["coverage"]["flagged"]["reasons"]) == 5
    last = reply["last_night"]
    assert last["cards_total"] == 60 and last["cards_with_change"] == 60 and last["lines"] == 120
    first = last["cards"][0]
    want = src["last_night"]["cards"][0]
    assert first["thesis_name"] == want["thesis_name"] and first["line_count"] == 2
    assert first["headline"]["change"] == want["headline"]["change"]
    assert first["headline"]["label"] == want["headline"]["label"][:120]
    assert "label_total_chars" not in first["headline"]  # only a cut string carries its full length
    assert reply["tape"]["rows_total"] == 69 and len(reply["tape"]["rows"]) == 3
    assert reply["insider"]["rows_total"] == 9 and len(reply["insider"]["rows"]) == 2
    assert reply["due"]["items_total"] == 14 and len(reply["due"]["items"]) == 4
    assert reply["next_cursor_for"] == "last_night/cards"  # the one paged list


def test_a_string_cut_by_a_view_carries_its_full_length():
    payload = copy.deepcopy(dfx.daily_digest())
    payload["last_night"]["cards"][0]["headline"]["label"] = "L" * 300
    payload["due"]["items"][0]["label"] = "D" * 90
    reply = REGISTRY["daily-digest"].view()(payload, Request(asof=dfx.ASOF))
    headline = reply["last_night"]["cards"][0]["headline"]
    assert headline["label"] == "L" * 120 and headline["label_total_chars"] == 300
    item = reply["due"]["items"][0]
    assert item["label"] == "D" * 80 and item["label_total_chars"] == 90


def test_daily_digest_default_cursor_walks_every_changed_card():
    src = dfx.daily_digest()["last_night"]["cards"]
    rows = walk("daily-digest", ("last_night", "cards"))
    assert_walk([c["thesis_id"] for c in rows], [c["thesis_id"] for c in src])


@pytest.mark.parametrize(
    "section,listkey,key,expected",
    [
        (
            "last_night",
            "cards",
            lambda r: r["thesis_id"],
            lambda p: [c["thesis_id"] for c in p["last_night"]["cards"]],
        ),
        (
            "tape",
            "rows",
            lambda r: (r["ticker"], r["thesis_name"]),
            lambda p: [(r["ticker"], r["thesis_name"]) for r in p["tape"]["rows"]],
        ),
        (
            "insider",
            "rows",
            lambda r: r["ticker"],
            lambda p: [r["ticker"] for r in p["insider"]["rows"]],
        ),
        (
            "due",
            "items",
            lambda r: (r["thesis_name"], r["item"]),
            lambda p: [(i["thesis_name"], i["item"]) for i in p["due"]["items"]],
        ),
    ],
)
def test_daily_digest_sections_walk_without_repeats_or_gaps(section, listkey, key, expected):
    rows = walk("daily-digest", (listkey,), section=section)
    assert_walk([key(r) for r in rows], expected(dfx.daily_digest()))


def test_daily_digest_last_night_section_carries_every_line_and_thesis_narrows_it():
    src = dfx.daily_digest()["last_night"]["cards"]
    one = call("daily-digest", section="last_night", thesis=dfx.tid(3))
    assert one["narrowed"]["thesis"]["id"] == dfx.tid(3) and one["cards_total"] == 1
    card = one["cards"][0]
    assert [x["label"] for x in card["lines"]] == [x["label"] for x in src[3]["lines"]]
    assert set(card["lines"][0]) == {
        "change",
        "ticker",
        "from_state",
        "to_state",
        "label",
        "on",
        "signal_kind",
    }
    insider = call("daily-digest", section="insider")["rows"][0]
    assert insider["card_count"] == 3 and insider["trigger_count"] == 2


# --- what-changed -----------------------------------------------------------------------------------------------


def test_what_changed_is_undated_and_states_its_anchor():
    src = dfx.what_changed()
    reply = call("what-changed")
    assert reply["asof"] is None and "cache" not in reply
    assert reply["anchor"] == {
        "since": "last_night",
        "since_date": src["since_date"],
        "baseline": src["baseline"],
        "latest": src["latest"],
        "first_recorded": src["first_recorded"],
    }
    assert reply["settings"] == {"since": "last_night"}
    assert reply["items_total"] == 559
    assert reply["by_change"]["risk_new"] == 423 and reply["by_change"]["trigger_redated"] == 34
    assert sum(reply["by_section"].values()) == 559
    assert reply["coverage"]["unrecorded"]["count"] == 2
    assert reply["coverage"]["cards_with_change"] == 60
    assert reply["next_cursor_for"] == "items"


def test_what_changed_passes_since_through_and_sections_filter_the_same_rows():
    src = dfx.what_changed()["items"]
    api = FakeApi()

    async def body(session):
        args = {"dashboard": "what-changed", "since": "7d"}
        return await session.call_tool("read_dashboard", args)

    reply = run(api, body).structured_content
    assert reply["settings"] == {"since": "7d"}
    assert [q for _, p, q in api.requests if p == "/dashboards/what-changed"] == [{"since": "7d"}]
    for section in REGISTRY["what-changed"].sections:
        want = [i["ticker"] for i in src if i["section"] == section]
        rows = walk("what-changed", ("items",), section=section)
        assert_walk([r["ticker"] for r in rows], want)
        assert all(r["section"] == section for r in rows)


def test_what_changed_default_cursor_walks_all_559_items_in_the_api_order():
    src = dfx.what_changed()["items"]
    rows = walk("what-changed", ("items",))
    assert len(rows) == 559
    assert_walk(
        [(r["ticker"], r["change"], r["on"]) for r in rows],
        [(i["ticker"], i["change"], i["on"]) for i in src],
    )
    assert call("what-changed", thesis=dfx.tid(4))["items_total"] == sum(
        1 for i in src if i["thesis_id"] == dfx.tid(4)
    )


# --- the views read only fields the API's wire models declare (a drift guard) ---------------------------------------


def _model_fields(module: str, model: str) -> set[str]:
    import importlib

    return set(
        getattr(importlib.import_module(f"app.routers.dashboards.{module}"), model).model_fields
    )


def _key_checks():
    from agent_tools.views.dashboards import daily_digest as dd
    from agent_tools.views.dashboards import what_changed as wc

    return [
        (dd.CARD_KEYS, "daily_digest", "DigestCardOut"),
        (dd.HEADLINE_KEYS, "daily_digest", "DigestLineOut"),
        (dd.LINE_KEYS, "daily_digest", "DigestLineOut"),
        (dd.TAPE_KEYS, "daily_digest", "DigestTapeRowOut"),
        (dd.INSIDER_KEYS, "daily_digest", "DigestBuyRowOut"),
        (dd.DUE_KEYS, "daily_digest", "DigestDueItemOut"),
        (dd.DUE_INTS, "daily_digest", "DigestDueOut"),
        (wc.ITEM_KEYS, "what_changed", "WhatChangedItemOut"),
        (wc.ANCHOR_KEYS, "what_changed", "WhatChangedOut"),
    ]


def test_every_field_a_view_copies_is_declared_by_the_api_wire_model():
    for keys, module, model in _key_checks():
        missing = set(keys) - _model_fields(module, model)
        assert not missing, f"{model}: {sorted(missing)}"
