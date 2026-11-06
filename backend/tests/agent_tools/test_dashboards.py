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


def test_list_dashboards_names_the_eight_with_settings_and_sections_within_3500_chars():
    reply = view_list_dashboards()
    assert size(reply) <= 3_500
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
        assert list(d["sections"]) == list(REGISTRY[d["slug"]].sections)
    # (d) which sections read one card or one group: said once in the listing, enforced by check()
    needs = {d["slug"]: {s: n for s, n in d["sections"].items() if n} for d in reply["dashboards"]}
    assert needs["hot-board"] == {"row": "thesis"} and needs["trigger-log"] == {"row": "thesis"}
    assert needs["catalyst-calendar"] == {"bucket": "group"}
    assert needs["rotation-map"] == {"members": "group"}
    assert set(needs["forming-quietly"]) == set(REGISTRY["forming-quietly"].sections)
    assert set(needs["forming-quietly"].values()) == {"thesis"}
    assert not needs["daily-digest"] and not needs["entry-windows"] and not needs["what-changed"]
    assert "change" in by_slug["what-changed"]["narrows"]


def test_list_dashboards_through_the_server_is_a_pure_registry_read():
    api = FakeApi()

    async def body(session):
        return await session.call_tool("list_dashboards", {})

    result = run(api, body)
    assert not result.is_error and len(result.content[0].text) <= 3_500
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
            "`daily-digest` does not take rows; it takes asof, thesis, section, cursor.",
        ),
        (
            {"dashboard": "hot-board", "days": 30},
            "`hot-board` does not take days; it takes asof, rows, thesis, section, cursor.",
        ),
        (
            {"dashboard": "rotation-map", "thesis": "Thesis 01"},
            "does not take thesis; it takes asof, group_by, include_silent, group, section, cursor",
        ),
        ({"dashboard": "hot-board", "group": "x"}, "does not take group; it takes asof, rows"),
        (
            {"dashboard": "trigger-log", "include_silent": False},
            "it takes asof, days, role, thesis, section, cursor",
        ),
        (
            {"dashboard": "hot-board", "change": "risk_new"},
            "`hot-board` does not take change; it takes asof, rows",
        ),
        ({"dashboard": "hot-board", "section": "nope"}, "has: row"),
        ({"dashboard": "what-changed", "since": "2026-02-30"}, "real date"),
        (
            {"dashboard": "what-changed", "since": "yesterday"},
            "since takes last_night, 7d or YYYY-MM-DD",
        ),
        (
            {"dashboard": "rotation-map", "group_by": "cap"},
            "group_by must be one of: link, business_type, sector or card",
        ),
        ({"dashboard": "trigger-log", "days": 45}, "days must be one of: 30, 90 or 180"),
        (
            {"dashboard": "no-such-dashboard"},
            "dashboard must be one of: daily-digest, what-changed, hot-board, entry-windows, "
            "forming-quietly, catalyst-calendar, rotation-map or trigger-log",
        ),
        ({"dashboard": "hot-board", "asof": "yesterday"}, "asof takes a date as YYYY-MM-DD"),
        (
            {"dashboard": "hot-board", "section": "row"},
            "section=row of `hot-board` needs thesis",
        ),
        (
            {"dashboard": "rotation-map", "section": "members"},
            "section=members of `rotation-map` needs group",
        ),
        (
            {"dashboard": "forming-quietly", "section": "risks"},
            "section=risks of `forming-quietly` needs thesis",
        ),
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
    assert reply["coverage"]["flagged"]["reasons_shown"] == 5  # (f) a cut list says it was cut
    last = reply["last_night"]
    assert last["cards_total"] == 60 and last["cards_with_change"] == 60 and last["lines"] == 120
    first = last["cards"][0]
    want = src["last_night"]["cards"][0]
    assert first["thesis_name"] == want["thesis_name"] and first["line_count"] == 2
    assert first["headline"]["change"] == want["headline"]["change"]
    assert first["headline"]["label"] == want["headline"]["label"][:120]
    assert "label_total_chars" not in first["headline"]  # only a cut string carries its full length
    assert reply["tape"]["rows_total"] == 69 and len(reply["tape"]["rows"]) == 3
    api_rows = src["tape"]["rows"]  # (a) both ends of the API's list, in its order, never re-sorted
    assert [r["ticker"] for r in reply["tape"]["rows"]] == [r["ticker"] for r in api_rows[:3]]
    assert [r["ticker"] for r in reply["tape"]["tail"]] == [r["ticker"] for r in api_rows[-3:]]
    assert {"rise", "fall"} <= {r["cut"] for r in reply["tape"]["rows"] + reply["tape"]["tail"]}
    # (b) the cut is visible: how many of each end the reply carries, beside the API's total
    assert reply["tape"]["rows_shown"] == len(reply["tape"]["rows"]) == 3
    assert reply["tape"]["tail_shown"] == len(reply["tape"]["tail"]) == 3
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
    assert one["narrowed"]["thesis"]["id"] == dfx.tid(3) and one["lines_total"] == 2
    assert one["card"]["thesis_id"] == dfx.tid(3) and "lines" not in one["card"]
    assert [x["label"] for x in one["lines"]] == [x["label"] for x in src[3]["lines"]]
    assert set(one["lines"][0]) == {
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
    from agent_tools.views.dashboards import catalyst_calendar as cc
    from agent_tools.views.dashboards import daily_digest as dd
    from agent_tools.views.dashboards import entry_windows as ew
    from agent_tools.views.dashboards import forming_quietly as fq
    from agent_tools.views.dashboards import hot_board as hb
    from agent_tools.views.dashboards import rotation_map as rm
    from agent_tools.views.dashboards import trigger_log as tl
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
        (hb.ROW_KEYS, "hot_board", "HotRowOut"),
        (hb.TRIGGER_KEYS, "hot_board", "HotTriggerOut"),
        (hb.SINCE_ARM_KEYS, "hot_board", "HotSinceArmOut"),
        (hb.CAP_KEYS, "hot_board", "HotCapOut"),
        (hb.TAPE_KEYS, "hot_board", "HotTapeOut"),
        (hb.BODY_KEYS, "hot_board", "HotBoardOut"),
        (ew.ROW_KEYS, "entry_windows", "EntryWindowsRowOut"),
        (ew.SINCE_KEYS, "entry_windows", "EntryWindowsSinceOut"),
        (ew.CLOCK_KEYS, "entry_windows", "EntryWindowsMemberClockOut"),
        (ew.MEMBER_KEYS, "entry_windows", "EntryWindowsMemberOut"),
        (ew.CATALYST_KEYS, "entry_windows", "EntryWindowsCatalystOut"),
        (ew.BODY_KEYS, "entry_windows", "EntryWindowsOut"),
        (fq.ROW_KEYS, "forming_quietly", "FormingRowOut"),
        (fq.BREADTH_KEYS, "forming_quietly", "FormingBreadthOut"),
        (fq.ADOPTION_KEYS, "forming_quietly", "FormingAdoptionOut"),
        (fq.QUIET_INTS, "forming_quietly", "FormingQuietOut"),
        (fq.TRIGGER_KEYS, "forming_quietly", "FormingTriggerOut"),
        (cc.COUNT_KEYS, "catalyst_calendar", "CatalystCalendarOut"),
        (cc.BUCKET_KEYS, "catalyst_calendar", "CatalystCalendarBucketOut"),
        (cc.ITEM_KEYS, "catalyst_calendar", "CatalystCalendarItemOut"),
        (cc.GROUP_KEYS, "catalyst_calendar", "CatalystCalendarUndatedGroupOut"),
        (cc.CATALYST_KEYS, "catalyst_calendar", "CatalystCalendarUndatedOut"),
        (rm.GROUP_KEYS, "rotation_map", "RotationGroupOut"),
        (rm.LABEL_KEYS, "rotation_map", "RotationGroupOut"),
        (rm.WINDOW_KEYS, "rotation_map", "RotationWindowOut"),
        (rm.MEMBER_KEYS, "rotation_map", "RotationNameOut"),
        (tl.BODY_KEYS, "trigger_log", "TriggerLogOut"),
        (tl.ROW_KEYS, "trigger_log", "TriggerLogRowOut"),
        (tl.FULL_KEYS, "trigger_log", "TriggerLogRowOut"),
    ]


def test_every_field_a_view_copies_is_declared_by_the_api_wire_model():
    for keys, module, model in _key_checks():
        missing = set(keys) - _model_fields(module, model)
        assert not missing, f"{model}: {sorted(missing)}"


# --- hot-board ---------------------------------------------------------------------------------------------------


def test_hot_board_default_rows_copy_the_api_order_and_drop_the_sparkline():
    src = dfx.hot_board()
    reply = call("hot-board")
    assert reply["settings"] == {"rows": "armed"} and reply["rows_loaded"] == "armed"
    assert reply["candidate_counts"] == src["candidate_counts"] and reply["benchmark"] == "IWM"
    assert reply["coverage"]["calendar_source"] == "benchmarks"
    assert reply["rows_total"] == 69 and reply["next_cursor_for"] == "rows"
    first, want = reply["rows"][0], src["rows"][0]
    assert (first["ticker"], first["kind"], first["verdict"]) == (
        want["ticker"],
        want["kind"],
        want["verdict"],
    )
    assert first["setup_strength"] == want["confidence"] and "confidence" not in first
    assert (
        first["also_armed_count"] == 2
        and first["trigger_count"] == 4
        and len(first["triggers"]) == 2
    )
    assert first["clocks"] == want["clocks"]
    assert first["since_arm"] == {k: want["since_arm"][k] for k in ("ret_pct", "iwm_pct", "gap")}
    assert "spark" not in first["tape"] and set(first["tape"]) == set(hb_keys())
    assert first["cap"] == {"value": 450_000_000.0, "tier": "small", "gap": None}


def hb_keys():
    from agent_tools.views.dashboards import hot_board

    return hot_board.TAPE_KEYS


def test_hot_board_rows_setting_is_passed_through_and_the_cursor_walks_every_row_in_order():
    api = FakeApi()

    async def body(session):
        args = {"dashboard": "hot-board", "asof": dfx.ASOF, "rows": "all"}
        return await session.call_tool("read_dashboard", args)

    reply = run(api, body).structured_content
    assert reply["settings"] == {"rows": "all"}
    sent = [q for _, p, q in api.requests if p == "/dashboards/hot-board"]
    assert sent == [{"asof": dfx.ASOF, "rows": "all"}]
    rows = walk("hot-board", ("rows",))
    assert_walk([r["ticker"] for r in rows], [r["ticker"] for r in dfx.hot_board()["rows"]])


def test_hot_board_row_section_returns_one_cards_rows_in_full():
    src = [r for r in dfx.hot_board()["rows"] if r["thesis_id"] == dfx.tid(0)]
    assert len(src) == 2
    rows = walk("hot-board", ("rows",), section="row", thesis=dfx.tid(0))
    assert [r["ticker"] for r in rows] == [r["ticker"] for r in src]
    assert len(rows[0]["triggers"]) == 4 and rows[0]["triggers"][0]["sources"]
    assert rows[0]["triggers"][0]["label"] == src[0]["triggers"][0]["label"]  # not cut
    assert rows[0]["tape"]["spark"] == src[0]["tape"]["spark"]
    narrowed_default = call("hot-board", thesis=dfx.tid(0))
    assert narrowed_default["rows_total"] == 2 and narrowed_default["narrowed"]["thesis"][
        "id"
    ] == dfx.tid(0)


def test_a_section_that_needs_a_card_without_one_is_invalid_arguments():
    for slug, section in (("hot-board", "row"), ("forming-quietly", "names")):

        async def body(session, slug=slug, section=section):
            args = {"dashboard": slug, "asof": dfx.ASOF, "section": section}
            return await session.call_tool("read_dashboard", args)

        err = error_of(run(FakeApi(), body))
        assert err["code"] == "invalid_arguments" and "needs thesis" in err["message"]


# --- entry-windows -----------------------------------------------------------------------------------------------


def test_entry_windows_default_rows_copy_the_clocks_and_count_the_members():
    src = dfx.entry_windows()
    reply = call("entry-windows")
    assert reply["axis_start"] == src["axis_start"] and reply["axis_end"] == src["axis_end"]
    assert (
        reply["coverage"]["counts"] == src["counts"] and reply["coverage"]["flagged"]["count"] == 2
    )
    assert reply["coverage"]["session_source"] == "benchmarks"
    first, want = reply["rows"][0], src["rows"][0]
    for key in (
        "thesis_name",
        "state",
        "arm_night",
        "in_effect",
        "arm_until",
        "exit_by",
        "hold_days",
    ):
        assert first[key] == want[key]
    assert first["setup_strength"] == want["confidence"]
    assert first["member_count"] == 3 and first["catalyst_count"] == 4
    assert first["since"] == {
        k: want["since"][k] for k in ("names", "priced", "median", "iwm", "spy")
    }
    rows = walk("entry-windows", ("rows",))
    assert_walk([r["thesis_name"] for r in rows], [r["thesis_name"] for r in src["rows"]])


@pytest.mark.parametrize("section,listkey", [("members", "members"), ("catalysts", "catalysts")])
def test_entry_windows_sections_page_by_card_to_the_end(section, listkey):
    src = dfx.entry_windows()["rows"]
    cards = walk("entry-windows", ("cards",), section=section)
    assert_walk([c["thesis_name"] for c in cards], [r["thesis_name"] for r in src])
    if section == "members":
        assert len(cards[0]["members"]) == 3 and len(cards[0]["member_clocks"]) == 3
        assert cards[0]["members"][0]["trigger_count"] == 1
    else:
        assert cards[0]["catalysts"] == [
            {k: c[k] for k in ("label", "kind", "when_date", "past")} for c in src[0]["catalysts"]
        ]
    one = call("entry-windows", section=section, thesis=dfx.tid(5))
    assert one["cards_total"] == 1 and one["cards"][0]["thesis_name"] == dfx.thesis_name(5)


# --- forming-quietly ---------------------------------------------------------------------------------------------


def test_forming_quietly_default_is_a_digest_of_each_card_not_its_38k():
    src = dfx.forming_quietly()
    reply = call("forming-quietly")
    assert (
        reply["coverage"]["cards_with_term_months"] == 25 and reply["coverage"]["names_read"] == 560
    )
    assert reply["rows_total"] == 29
    first, want = reply["rows"][0], src["rows"][0]
    assert first["name_count"] == 20 and len(first["names"]) == 8 and first["missing_count"] == 3
    assert first["names"] == [n["ticker"] for n in want["names"][:8]]
    assert len(first["missing"]) == 2 and first["missing"][0] == {
        "text": want["missing"][0],
        "total_chars": 120,
        "truncated": False,
    }
    assert first["risk_count"] == 40 and sum(first["risks_by_kind"].values()) == 40
    assert first["watch_count"] == 4 and first["keys"] == {
        "conviction": True,
        "confirmation": False,
    }
    assert first["quiet"]["coiling"] == 8 and first["quiet"]["leading"] == 5
    assert first["quiet"]["breadth"]["pct"] == 66.7
    assert first["adoption"] == {"percent": 42.5, "recent": 120, "prior": 84}
    assert size(first) < 1_000 < len(str(want))
    rows = walk("forming-quietly", ("rows",))
    assert_walk([r["thesis_name"] for r in rows], [r["thesis_name"] for r in src["rows"]])


def test_forming_quietly_sections_return_one_cards_part_in_full_and_page_to_the_end():
    card = dfx.forming_quietly()["rows"][3]
    args = {"thesis": dfx.tid(3)}
    names = walk("forming-quietly", ("names",), section="names", **args)
    assert_walk(
        [(n["ticker"], len(n["triggers"])) for n in names],
        [(n["ticker"], len(n["triggers"])) for n in card["names"]],
    )
    risks = walk("forming-quietly", ("risks",), section="risks", **args)
    assert_walk(
        [(r["ticker"], r["kind"]) for r in risks], [(r["ticker"], r["kind"]) for r in card["risks"]]
    )
    assert walk("forming-quietly", ("missing",), section="missing", **args) == card["missing"]
    watch = walk("forming-quietly", ("watch",), section="watch", **args)
    assert watch == [w["ticker"] for w in card["watch"]]
    quiet = call("forming-quietly", section="quiet", **args)["quiet"]
    assert quiet["coiling_total"] == 8 and quiet["breadth"] == card["quiet"]["breadth"]
    assert quiet["insider"] == card["quiet"]["insider"]
    adoption = call("forming-quietly", section="adoption", **args)
    assert adoption["adoption"] == card["adoption"] and adoption["card_in_forming"] is True


def view_walk(slug: str, list_key: str, req_args: dict | None = None, **kw) -> list:
    """The same cursor walk straight through the view (no HTTP): for the 5.6 MB trigger log."""
    rows, cursor = [], 0
    payload = dfx.PAYLOADS[slug]()
    while cursor is not None:
        req = Request(asof=None if slug in UNDATED else dfx.ASOF, cursor=cursor, **kw)
        reply = REGISTRY[slug].view()(payload, req)
        assert size(reply) <= BUDGET
        rows.extend(reply[list_key])
        cursor = reply["next_cursor"]
    return rows


# --- catalyst-calendar -------------------------------------------------------------------------------------------


def test_catalyst_calendar_is_undated_and_pages_the_flat_items_across_buckets():
    src = dfx.catalyst_calendar()
    reply = call("catalyst-calendar")
    assert reply["asof"] is None and reply["settings"] == {}
    assert reply["counts"] == {
        k: src[k]
        for k in (
            "today",
            "catalysts",
            "dated",
            "upcoming_catalysts",
            "passed_catalysts",
            "undated",
            "deadlines",
        )
    }
    assert reply["coverage"]["cards_without_catalysts"] == 1
    assert [b["key"] for b in reply["buckets"]] == [b["key"] for b in src["buckets"]]
    assert all(b["item_count"] == 40 for b in reply["buckets"])
    assert reply["passed_count"] == 134 and reply["undated_groups_count"] == 63
    assert reply["items_total"] == 200 and reply["next_cursor_for"] == "items"
    flat = [(b["key"], i["thesis_name"], i["on"]) for b in src["buckets"] for i in b["items"]]
    rows = walk("catalyst-calendar", ("items",))
    assert_walk([(r["bucket"], r["thesis_name"], r["on"]) for r in rows], flat)
    assert {r["bucket"] for r in rows} == {
        b["key"] for b in src["buckets"]
    }  # the cursor crosses buckets


def test_catalyst_calendar_sections_bucket_passed_and_undated():
    src = dfx.catalyst_calendar()
    bucket = src["buckets"][2]
    rows = walk("catalyst-calendar", ("items",), section="bucket", group=bucket["key"])
    assert_walk(
        [(r["thesis_name"], r["on"]) for r in rows],
        [(i["thesis_name"], i["on"]) for i in bucket["items"]],
    )
    passed = walk("catalyst-calendar", ("items",), section="passed")
    assert len(passed) == 134 and passed[0]["days"] == src["passed"][0]["days"]
    groups = walk("catalyst-calendar", ("groups",), section="undated")
    assert_walk(
        [g["thesis_name"] for g in groups], [g["thesis_name"] for g in src["undated_groups"]]
    )
    one = call("catalyst-calendar", section="undated", thesis=dfx.tid(7))
    assert [c["when_label"] for c in one["catalysts"]] == ["mid 2027"] * 3 and one[
        "catalysts_total"
    ] == 3
    assert call("catalyst-calendar", group="this_week")["items_total"] == 40


def test_catalyst_calendar_bucket_section_needs_a_known_group():
    for args, expect in (
        ({"section": "bucket"}, "needs group"),
        ({"group": "soon"}, "Unknown bucket"),
    ):

        async def body(session, args=args):
            return await session.call_tool(
                "read_dashboard", {"dashboard": "catalyst-calendar", **args}
            )

        err = error_of(run(FakeApi(), body))
        assert err["code"] == "invalid_arguments" and expect in err["message"]


# --- rotation-map ------------------------------------------------------------------------------------------------


def test_rotation_map_default_groups_copy_the_api_order_with_window_returns():
    src = dfx.rotation_map()
    reply = call("rotation-map")
    assert reply["settings"] == {"group_by": "link", "include_silent": False}
    assert reply["spy"] == src["spy"] and reply["coverage"]["silent_rows_left_out"] == 300
    assert (
        reply["coverage"]["priced_companies"] == 436 and reply["coverage"]["flagged"]["count"] == 4
    )
    assert reply["groups_total"] == 66 and reply["next_cursor_for"] == "groups"
    first, want = reply["groups"][0], src["groups"][0]
    assert first["key"] == want["key"] and first["label"] == want["link"]
    assert set(first["returns"]) == set(dfx.WINDOWS)
    assert first["returns"]["ret_30d"] == {"median": 2.5, "n": 7, "up": 5, "above_spy": 4}
    assert (first["names"], first["above_50d"], first["golden"], first["rs_leaders"]) == (
        7,
        5,
        1,
        3,
    )
    assert "members" not in first
    rows = walk("rotation-map", ("groups",))
    assert_walk([g["key"] for g in rows], [g["key"] for g in src["groups"]])


def test_rotation_map_passes_its_settings_and_reads_members_by_group_key():
    api = FakeApi()

    async def body(session):
        args = {
            "dashboard": "rotation-map",
            "asof": dfx.ASOF,
            "group_by": "sector",
            "include_silent": True,
        }
        return await session.call_tool("read_dashboard", args)

    reply = run(api, body).structured_content
    assert reply["settings"] == {"group_by": "sector", "include_silent": True}
    sent = [q for _, p, q in api.requests if p == "/dashboards/rotation-map"]
    assert sent == [{"asof": dfx.ASOF, "group_by": "sector", "include_silent": "true"}]
    src = dfx.rotation_map()["groups"][4]
    members = call("rotation-map", section="members", group=src["key"])
    assert members["members_total"] == 7 and members["narrowed"] == {"group": src["key"]}
    first = members["members"][0]
    assert first["cards"] == 2 and first["returns"] == src["members"][0]["returns"]
    assert (first["cross"], first["cross_date"]) == ("golden", src["members"][0]["cross_date"])
    flagged = call("rotation-map", section="flagged")
    assert flagged["flagged_total"] == 4 and set(flagged["flagged"][0]) == {"ticker", "reason"}
    only = call("rotation-map", group=src["key"])
    assert only["groups_total"] == 1


def test_rotation_map_members_need_a_known_group():
    for args, expect in (
        ({"section": "members"}, "needs group"),
        ({"group": "nope"}, "Unknown group"),
    ):

        async def body(session, args=args):
            return await session.call_tool(
                "read_dashboard", {"dashboard": "rotation-map", "asof": dfx.ASOF, **args}
            )

        err = error_of(run(FakeApi(), body))
        assert err["code"] == "invalid_arguments" and expect in err["message"]


# --- trigger-log -------------------------------------------------------------------------------------------------


def test_trigger_log_default_reply_is_leading_rows_with_counts_and_a_5mb_source():
    src = dfx.trigger_log()
    assert len(src["rows"]) == 5_002
    reply = call("trigger-log")
    assert reply["settings"] == {"days": 90, "role": "entry"}
    assert (
        reply["window_days"] == 90
        and reply["role"] == "entry"
        and reply["sessions"] == src["sessions"]
    )
    assert reply["rows_total"] == 5_002 and reply["next_cursor_for"] == "rows"
    assert sum(reply["by_kind"].values()) == 5_002 == sum(reply["by_week"].values())
    cov = reply["coverage"]
    assert cov["counts"]["other_role_rows"] == 16_000 and cov["unpriced"] == {"count": 3}
    assert cov["flagged"]["count"] == 2
    first, want = reply["rows"][0], src["rows"][0]
    assert first["ticker"] == want["ticker"] and first["label"] == want["label"][:100]
    from agent_tools.views.dashboards import trigger_log as tl

    assert set(first) == {*tl.ROW_KEYS, "label"}  # a 95-character label is not cut
    assert "label_changes" not in first and "sources" not in first


def test_trigger_log_passes_days_and_role_and_walks_all_5002_rows_in_the_api_order():
    api = FakeApi()

    async def body(session):
        args = {"dashboard": "trigger-log", "asof": dfx.ASOF, "days": 30, "role": "both"}
        return await session.call_tool("read_dashboard", args)

    reply = run(api, body).structured_content
    assert reply["settings"] == {"days": 30, "role": "both"}
    sent = [q for _, p, q in api.requests if p == "/dashboards/trigger-log"]
    assert sent == [{"asof": dfx.ASOF, "days": "30", "role": "both"}]
    src = dfx.trigger_log()["rows"]
    rows = view_walk("trigger-log", "rows")
    assert_walk(
        [(r["ticker"], r["thesis_name"], r["event_date"], r["kind"]) for r in rows],
        [(r["ticker"], r["thesis_name"], r["event_date"], r["kind"]) for r in src],
    )


def test_trigger_log_thesis_narrows_the_rows_and_the_row_section_carries_every_field():
    src = [r for r in dfx.trigger_log()["rows"] if r["thesis_id"] == dfx.tid(3)]
    narrowed_reply = call("trigger-log", thesis=dfx.tid(3))
    assert narrowed_reply["rows_total"] == len(src) and narrowed_reply["narrowed"]["thesis"][
        "id"
    ] == dfx.tid(3)
    rows = view_walk(
        "trigger-log", "rows", thesis={"id": dfx.tid(3), "name": dfx.thesis_name(3)}, section="row"
    )
    assert len(rows) == len(src)
    assert [r["label_changes"] for r in rows] == [r["label_changes"] for r in src]
    assert rows[0]["sources"] == src[0]["sources"] and rows[0]["since"] == src[0]["since"]
    assert rows[0]["label"] == src[0]["label"]
    paged = walk("trigger-log", ("rows",), section="row", thesis=dfx.tid(3))
    assert len(paged) == len(src)


def test_forming_quietly_names_a_card_that_is_not_forming():
    reply = call("forming-quietly", section="adoption", thesis=dfx.tid(50))
    assert reply["card_in_forming"] is False and reply["adoption"] is None


# --- Phase 3 Step 0: the fixes from the native retest --------------------------------------------------------------


def test_compute_ms_is_this_reads_time_only_when_computed_and_cold_compute_ms_on_a_hit():
    computed = call("daily-digest")  # the fixture says cache=computed, 41,700 ms
    assert computed["cache"] == "computed" and computed["compute_ms"] == 41_700
    assert "cold_compute_ms" not in computed
    hit = call("trigger-log")  # the fixture says cache=hit, 4 ms: the API's number is the cold time
    assert hit["cache"] == "hit" and hit["cold_compute_ms"] == 4 and "compute_ms" not in hit


def _not_fresh_digest() -> dict:
    payload = copy.deepcopy(dfx.daily_digest())
    for i in (1, 2, 3):
        payload["last_night"]["cards"][i]["ingest_fresh"] = False
    payload["last_night"]["cards"][5]["ingest_fresh"] = None  # unstamped (a reconstruction)
    payload["positions"][0]["ingest_fresh"] = False
    return payload


def test_a_card_that_is_not_fresh_keeps_its_flag_and_coverage_names_the_cards():
    payload = _not_fresh_digest()
    req = Request(asof=dfx.ASOF)
    reply = REGISTRY["daily-digest"].view()(payload, req)
    shown = {c["thesis_name"]: c for c in reply["last_night"]["cards"]}
    for i in (1, 2, 3):
        assert shown[dfx.thesis_name(i)]["ingest_fresh"] is False
    # omitted when true or unstamped
    assert sum("ingest_fresh" in c for c in reply["last_night"]["cards"]) == 3
    assert reply["coverage"]["cards_not_fresh"] == [
        dfx.thesis_name(1),
        dfx.thesis_name(2),
        dfx.thesis_name(3),
        dfx.thesis_name(0),  # the open position's card, after the changed cards, in the API's order
    ]
    assert reply["positions"][0]["ingest_fresh"] is False
    full = REGISTRY["daily-digest"].view()(payload, Request(asof=dfx.ASOF, section="last_night"))
    assert sum(c.get("ingest_fresh") is False for c in full["cards"]) == 3
    # the clean fixture carries neither the flag nor the list
    clean = REGISTRY["daily-digest"].view()(dfx.daily_digest(), req)
    assert "cards_not_fresh" not in clean["coverage"]
    assert not any("ingest_fresh" in c for c in clean["last_night"]["cards"])


def test_cards_not_fresh_lists_at_most_ten_names():
    payload = copy.deepcopy(dfx.daily_digest())
    for c in payload["last_night"]["cards"][:25]:
        c["ingest_fresh"] = False
    reply = REGISTRY["daily-digest"].view()(payload, Request(asof=dfx.ASOF))
    names = reply["coverage"]["cards_not_fresh"]
    assert names == [c["thesis_name"] for c in payload["last_night"]["cards"][:10]]


def test_what_changed_change_narrows_to_one_kind_and_matches_by_change():
    whole = call("what-changed", section="triggers")
    assert whole["by_change"] == {"trigger_redated": 34, "trigger_new": 26, "trigger_gone": 15}
    for change, count in whole["by_change"].items():
        narrowed = call("what-changed", section="triggers", change=change)
        assert narrowed["items_total"] == count == whole["by_change"][change]
        assert narrowed["by_change"] == whole["by_change"]  # the keys `change` takes stay visible
        assert narrowed["narrowed"]["change"] == change
        assert {i["change"] for i in narrowed["items"]} == {change}
    rows = walk("what-changed", ("items",), change="trigger_new")
    src = [i for i in dfx.what_changed()["items"] if i["change"] == "trigger_new"]
    assert_walk([r["ticker"] for r in rows], [i["ticker"] for i in src])
    # with no section: the change across the whole read
    assert call("what-changed", change="risk_new")["items_total"] == 423


def test_an_empty_section_or_narrowing_says_so_with_a_note():
    empty = call("what-changed", section="keys", change="key_off")
    assert empty["items_total"] == 0 and empty["items"] == []
    assert empty["note"].startswith("nothing changed in 'key_off' in keys since last_night")
    assert "key_on" in empty["note"]  # names the changes the read does hold
    payload = copy.deepcopy(dfx.what_changed())
    payload["items"] = [i for i in payload["items"] if i["section"] != "keys"]
    reply = REGISTRY["what-changed"].view()(payload, Request(section="keys"))
    assert reply["items_total"] == 0
    assert reply["note"] == "nothing changed in keys since last_night"
    assert "note" not in call("what-changed", section="keys")
    # a narrowing on a section with no changes at all: no empty list after the colon
    none = REGISTRY["what-changed"].view()(
        payload, Request(section="keys", change="no_such_change")
    )
    assert none["note"] == (
        "nothing changed in 'no_such_change' in keys since last_night; keys holds no changes at all"
    )


def test_change_on_another_dashboard_is_invalid_arguments():
    api = FakeApi()

    async def body(session):
        return await session.call_tool("read_dashboard", {"dashboard": "hot-board", "change": "x"})

    err = error_of(run(api, body))
    assert err["code"] == "invalid_arguments" and "does not take change" in err["message"]
    assert not [r for r in api.requests if r[1].startswith("/dashboards")]


# --- Phase 4 Step 0: the fixes from the Phase 3 native retest ------------------------------------------------------


def _long_card_digest(lines: int = 40, chars: int = 300) -> dict:
    """The retest's failing night: one changed card whose lines alone exceed 8,000 characters."""
    payload = copy.deepcopy(dfx.daily_digest())
    card = payload["last_night"]["cards"][0]
    template = card["lines"][0]
    card["lines"] = [
        {**template, "ticker": f"TK{k:02d}", "label": f"{k:02d} " + "L" * (chars - 3)}
        for k in range(lines)
    ]
    return payload


def test_a_card_with_40_long_lines_fits_the_last_night_section_page():
    payload = _long_card_digest()
    reply = REGISTRY["daily-digest"].view()(payload, Request(asof=dfx.ASOF, section="last_night"))
    assert size(reply) <= BUDGET
    first = reply["cards"][0]
    assert first["lines_total"] == 40 and len(first["lines"]) == 12  # cut at LINES_SHOWN
    assert [x["label"] for x in first["lines"]] == [
        x["label"] for x in payload["last_night"]["cards"][0]["lines"][:12]
    ]
    assert reply["cards"][1]["lines_total"] == 2 and len(reply["cards"][1]["lines"]) == 2


def test_the_thesis_narrowed_last_night_walk_covers_all_40_lines():
    payload = _long_card_digest()
    card = payload["last_night"]["cards"][0]
    want = [x["label"] for x in card["lines"]]
    thesis = {"id": card["thesis_id"], "name": card["thesis_name"]}
    seen: list[str] = []
    cursor, pages = 0, 0
    while cursor is not None:
        reply = REGISTRY["daily-digest"].view()(
            payload, Request(asof=dfx.ASOF, section="last_night", thesis=thesis, cursor=cursor)
        )
        assert size(reply) <= BUDGET
        assert reply["card"]["thesis_id"] == card["thesis_id"] and reply["lines_total"] == 40
        seen.extend(x["label"] for x in reply["lines"])
        pages += 1
        assert pages < 50
        cursor = reply["next_cursor"]
        if cursor is not None:
            assert reply["next_cursor_for"] == "lines"
    assert seen == want and pages > 1


def test_a_thesis_with_no_change_last_night_reads_as_no_card():
    payload = copy.deepcopy(dfx.daily_digest())
    gone = payload["last_night"]["cards"].pop(4)
    thesis = {"id": gone["thesis_id"], "name": gone["thesis_name"]}
    reply = REGISTRY["daily-digest"].view()(
        payload, Request(asof=dfx.ASOF, section="last_night", thesis=thesis)
    )
    assert reply["card"] is None and reply["lines"] == [] and reply["lines_total"] == 0


def test_a_last_night_page_too_large_names_the_thesis_narrowing(monkeypatch):
    from agent_tools.views.dashboards import daily_digest as dd

    monkeypatch.setattr(dd, "BUDGET", 600)

    async def body(session):
        return await session.call_tool(
            "read_dashboard",
            {"dashboard": "daily-digest", "asof": dfx.ASOF, "section": "last_night"},
        )

    err = error_of(run(FakeApi(), body))
    assert err["code"] == "output_too_large"
    assert "thesis=<card> for its lines" in err["message"]
