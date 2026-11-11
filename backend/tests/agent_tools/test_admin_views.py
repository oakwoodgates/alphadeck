"""The steward tools (``get_splits`` and ``get_admin_status``) through the official MCP client as a steward, against the
fake API: the default replies, every section, the shared rejections, the budget and the read-only guarantee. Synthetic
data only (``tests/agent_tools/admin_fixtures.py``); no DB.
"""

from __future__ import annotations

import asyncio
import json

import pytest

pytest.importorskip("mcp")

from mcp.client.client import Client  # noqa: E402

from agent_tools import server as server_mod  # noqa: E402
from agent_tools.budget import size  # noqa: E402
from agent_tools.client import ApiClient  # noqa: E402
from agent_tools.principal import CURRENT_PRINCIPAL, Principal  # noqa: E402
from tests.agent_tools import admin_fixtures as afx  # noqa: E402
from tests.agent_tools.fake_api import FakeApi  # noqa: E402

STEWARD = Principal(frozenset({"read", "steward"}), "s", "pat")
BUDGET = 8_000


def call(api: FakeApi, tool: str, **args):
    """One tool call as the steward: the CallToolResult."""

    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=api.transport())
        reset = CURRENT_PRINCIPAL.set(STEWARD)
        try:
            async with Client(server_mod.build_server(lambda: client)) as session:
                return await session.call_tool(tool, args)
        finally:
            CURRENT_PRINCIPAL.reset(reset)
            await client.aclose()

    return asyncio.run(go())


def ok(api: FakeApi, tool: str, **args) -> dict:
    result = call(api, tool, **args)
    assert not result.is_error, result.content[0].text
    assert len(result.content[0].text) <= BUDGET
    return result.structured_content


def failed(api: FakeApi, tool: str, **args) -> dict:
    result = call(api, tool, **args)
    assert result.is_error
    return json.loads(result.content[0].text)


# --- get_splits ----------------------------------------------------------------------------------------------


def test_the_default_splits_reply_is_the_counts_and_the_needs_review_rows_in_the_apis_order():
    api = FakeApi()
    reply = ok(api, "get_splits")
    assert reply["asof"] is None and reply["source"] == "live"
    assert reply["counts"] == {
        "needs_review": 3,
        "announced": 1,
        "confirmed": 3,
        "set_aside": 1,
        "unlisted_jumps": 1,
        "dismissed_jumps": 1,
        "total": 8,
        "needs_review_by_reason": {
            "sources_differ": 1,
            "extractor_abstained": 1,
            "one_source": 1,
        },
    }
    assert [r["review_reason"] for r in reply["needs_review"]] == [
        "sources_differ",
        "extractor_abstained",
        "one_source",
    ]
    assert "unlisted_jumps_error" not in reply
    assert [r["ticker"] for r in reply["needs_review"]] == [
        "TK01",
        "TK02",
        "TK03",
    ]  # the API's order
    first = reply["needs_review"][0]
    assert first["name"] == "Company 01"
    assert first["confirm_options"] == ["SEC: 1-for-10", "Massive.com: 1-for-10"]
    assert first["sec"]["passage"] == afx.splits()["needs_review"][0]["sec"]["passage"][:300]
    assert first["sec"]["passage_chars"] == afx.PASSAGE_CHARS
    assert first["polygon"] == {"execution_date": "2026-11-02", "ratio_label": "1-for-10"}
    assert reply["needs_review"][1]["sec"]["abstain_reason"] == "two ratios in the passage"
    assert reply["needs_review"][2]["sec"] is None
    assert "restore_to" not in first  # only a set-aside row carries it
    assert [j["ticker"] for j in reply["jumps"]] == ["TK08"]  # the unlisted jump only
    jump = reply["jumps"][0]
    assert set(jump) == {
        "ticker",
        "prior_date",
        "step_date",
        "prior_close",
        "close",
        "step",
        "label",
        "sessions_since",
    }
    assert reply["next_cursor"] is None


def test_the_reason_counts_sum_to_the_needs_review_count_and_reason_narrows_the_section():
    api = FakeApi()
    counts = ok(api, "get_splits")["counts"]
    assert sum(counts["needs_review_by_reason"].values()) == counts["needs_review"]
    reply = ok(api, "get_splits", section="needs_review", reason="sources_differ")
    assert [r["ticker"] for r in reply["needs_review"]] == ["TK01"]
    assert reply["total"] == 1 and reply["narrowed"] == {"reason": "sources_differ"}
    both = ok(api, "get_splits", section="needs_review")
    assert both["total"] == 3 and "narrowed" not in both
    # the reason is applied by the view: the API is asked for the whole list
    assert all(path == "/admin/splits" for _, path, _ in api.requests)


def test_reason_is_refused_on_another_section_and_an_unknown_one_names_the_three():
    api = FakeApi()
    err = failed(api, "get_splits", section="announced", reason="sources_differ")
    assert err["code"] == "invalid_arguments"
    assert err["message"] == "reason narrows section=needs_review only."
    err = failed(api, "get_splits", section="needs_review", reason="nope")
    assert err["code"] == "invalid_arguments"
    assert "sources_differ, extractor_abstained, one_source" in err["message"]


def test_an_unlisted_jumps_error_is_copied():
    body = afx.splits()
    body["unlisted_jumps_error"] = "the bar read failed"
    reply = ok(FakeApi(splits=body), "get_splits")
    assert reply["unlisted_jumps_error"] == "the bar read failed"


@pytest.mark.parametrize(
    "section,tickers",
    [
        ("needs_review", ["TK01", "TK02", "TK03"]),
        ("announced", ["TK04"]),
        ("confirmed", ["TK05", "TK01", "TK06"]),
        ("set_aside", ["TK07"]),
    ],
)
def test_a_bucket_section_pages_that_bucket_in_the_apis_order(section, tickers):
    reply = ok(FakeApi(), "get_splits", section=section)
    assert reply["section"] == section and reply["total"] == len(tickers)
    assert [r["ticker"] for r in reply[section]] == tickers


def test_a_set_aside_row_carries_its_restore_target_and_a_cut_note():
    row = ok(FakeApi(), "get_splits", section="set_aside")["set_aside"][0]
    assert row["restore_to"] == "needs_review" and row["actions"] == ["restore"]
    assert len(row["note"]) == 200 and row["note_total_chars"] > 200


def test_a_bucket_section_continues_at_its_cursor():
    reply = ok(FakeApi(), "get_splits", section="confirmed", cursor=1)
    assert [r["ticker"] for r in reply["confirmed"]] == ["TK01", "TK06"]


def test_the_jumps_section_pages_unlisted_then_dismissed_together():
    reply = ok(FakeApi(), "get_splits", section="jumps")
    assert [j["ticker"] for j in reply["unlisted"]] == ["TK08"]
    dismissed = reply["dismissed"][0]
    assert dismissed["ticker"] == "TK06" and dismissed["dismissed_at"] == "2026-11-04T15:00:00Z"
    assert len(dismissed["dismissed_reason"]) == 120
    assert reply["totals"] == {"unlisted": 1, "dismissed": 1}
    second = ok(FakeApi(), "get_splits", section="jumps", cursor=1)
    assert second["unlisted"] == [] and [j["ticker"] for j in second["dismissed"]] == ["TK06"]


def test_one_reads_every_entry_and_jump_of_a_ticker_in_full_across_buckets():
    api = FakeApi()
    reply = ok(api, "get_splits", section="one", t="tk01")  # lower case matches
    assert reply["t"] == "tk01" and reply["found"] == 2
    assert [e["status"] for e in reply["entries"]] == ["needs_review", "confirmed"]
    entry = reply["entries"][0]
    assert entry["version_id"] and entry["sec"]["url"] and entry["sec"]["document"]
    assert len(entry["sec"]["passage"]) == afx.PASSAGE_CHARS  # under 1,500: whole
    assert entry["sec"]["passage_chars"] == afx.PASSAGE_CHARS
    assert [o["source"] for o in entry["confirm_options"]] == ["sec", "polygon"]
    assert reply["jumps"] == []
    jumped = ok(api, "get_splits", section="one", t="TK08")
    assert jumped["found"] == 0 and len(jumped["jumps"]) == 1
    assert len(jumped["jumps"][0]["after"]) == 3 and "prior_volume" in jumped["jumps"][0]


def test_one_for_an_unlisted_ticker_is_an_empty_page_not_an_error():
    reply = ok(FakeApi(), "get_splits", section="one", t="TK99")
    assert reply["found"] == 0 and reply["entries"] == [] and reply["jumps"] == []


def test_one_matches_the_massive_row_ticker_when_the_master_ticker_is_null():
    body = afx.splits()
    body["needs_review"][2]["ticker"] = None
    body["needs_review"][2]["polygon"]["ticker"] = "TK03"
    reply = ok(FakeApi(splits=body), "get_splits", section="one", t="tk03")
    assert reply["found"] == 1


def test_the_full_entry_cuts_a_passage_over_1500_characters_and_says_so():
    body = afx.splits()
    body["needs_review"][0]["sec"]["passage"] = "x" * 2_000
    reply = ok(FakeApi(splits=body), "get_splits", section="one", t="TK01")
    sec = reply["entries"][0]["sec"]
    assert len(sec["passage"]) == 1_500 and sec["passage_chars"] == 2_000


@pytest.mark.parametrize(
    "args,needle",
    [
        ({"t": "TK01"}, "t narrows section=one only."),
        ({"section": "needs_review", "t": "TK01"}, "t narrows section=one only."),
        ({"section": "one"}, "section=one of `get_splits` needs t"),
        (
            {"section": "bogus"},
            "Unknown section 'bogus'; `get_splits` has: needs_review, announced, confirmed, set_aside, "
            "jumps, one.",
        ),
    ],
)
def test_a_bad_get_splits_call_is_rejected_before_any_request(args, needle):
    api = FakeApi()
    result = call(api, "get_splits", **args)
    assert result.is_error
    assert needle in result.content[0].text
    assert [p for _, p, _ in api.requests if p.startswith("/admin")] == []


def test_a_forty_row_needs_review_list_fits_the_budget_with_a_more_hint():
    api = FakeApi(splits=afx.splits(extra_needs_review=37))  # 3 + 37 = 40
    reply = ok(api, "get_splits")
    assert reply["counts"]["needs_review"] == 40
    shown = len(reply["needs_review"])
    assert 1 <= shown < 40
    assert reply["next_cursor"] == shown and reply["next_cursor_for"] == "needs_review"
    more = ok(api, "get_splits", section="needs_review", cursor=reply["next_cursor"])
    assert more["needs_review"][0]["ticker"] == afx.splits(37)["needs_review"][shown]["ticker"]
    assert size(reply) <= BUDGET


# --- get_admin_status ----------------------------------------------------------------------------------------


def test_the_default_status_reply_is_the_verdict_and_the_counts():
    reply = ok(FakeApi(), "get_admin_status")
    assert reply["asof"] is None and reply["source"] == "live"
    assert reply["cron"] == {
        "status": "healthy",
        "detail": "The last pass was clean.",
        "alarms": ["Synthetic alarm: backup is old."],
    }
    record = reply["record"]
    assert record["missed"] == 2 and record["missed_asofs"] == ["2026-10-14", "2026-10-15"]
    assert record["missed_asofs_total"] == 2 and record["partial_nights"] == 1
    assert record["reconstructed_nights"] == 0 and record["late_only_nights"] == 0
    run = reply["last_run"]
    assert len(run["problems"]) == 3 and run["problems_total"] == 4
    assert run["audit"] == {"checks": 2, "failed": 1, "failed_checks": ["benchmark_tape"]}
    assert run["healthy"] is True and run["code_sha"] == "abc1234"
    assert reply["unfinished_run"] is None
    assert reply["backend_sha"] == reply["cron_sha"] == "abc1234"
    assert reply["last_backup"]["name"].startswith("alphadeck_")
    assert reply["tape"]["stale"] == 2 and reply["tape"]["newly_stale"] == ["TK01 price"]
    assert reply["split_defects"]["defects"] == 0
    assert reply["price_repairs"] == {
        "asof": "2026-11-06",
        "renamed": 1,
        "unpriceable": 1,
        "successor": 0,
        "limitations": 1,
        "closed": 0,
    }
    assert set(reply["lines"]) == {
        "editions_nightly",
        "missing_closes",
        "editions_cut",
        "master_refresh",
    }
    assert reply["lines"]["editions_nightly"] == {
        "line": "Editions: tonight's pages appended.",
        "attention": False,
    }
    assert reply["splits_needs_review"] == 3 and reply["data_tenant_theses"] == 0
    assert reply["oauth_clients"] == {"static": 2, "dcr": 1}


def test_a_line_the_api_does_not_have_is_absent_and_a_null_panel_stays_null():
    body = afx.status()
    body["master_refresh"] = None
    body["tape"] = None
    body["last_run"] = None
    reply = ok(FakeApi(status=body), "get_admin_status")
    assert "master_refresh" not in reply["lines"]
    assert reply["tape"] is None and reply["last_run"] is None


def test_the_tape_section_pages_the_stale_rows_whole():
    reply = ok(FakeApi(), "get_admin_status", section="tape")
    assert reply["section"] == "tape" and reply["newly_stale"] == ["TK01 price"]
    assert [r["ticker"] for r in reply["stale"]] == ["TK01", "TK02"]
    assert (
        reply["stale"][0]["thesis"] == "workspace 1a2b3c4d · thesis 5e6f7a8b"
    )  # the API's redaction, copied
    assert reply["totals"] == {"stale": 2}


def test_the_price_repairs_section_names_each_row_by_its_list():
    reply = ok(FakeApi(), "get_admin_status", section="price_repairs")
    assert set(reply["totals"]) == {
        "renamed",
        "unpriceable",
        "acknowledged",
        "closed",
        "applied",
        "successor",
        "cik_applied",
        "limitations",
    }
    assert [r["ticker"] for r in reply["renamed"]] == ["TK03"]
    assert [r["ticker"] for r in reply["unpriceable"]] == ["TK04"]
    assert reply["limitations"][0]["reason"] == "synthetic limitation"


def test_the_split_defects_missing_closes_and_record_sections_read_their_panel():
    api = FakeApi()
    defects = ok(api, "get_admin_status", section="split_defects")
    assert defects["defects"] == [] and defects["settled"] == [] and defects["window_days"] == 30
    closes = ok(api, "get_admin_status", section="missing_closes")
    assert closes["line"] == "All closes present." and closes["judged"] == 300
    assert closes["missing"] == [] and closes["still_missing"] == [] and closes["arrived"] == []
    record = ok(api, "get_admin_status", section="record")
    assert record["partial_nights"][0]["expected"] == 6
    assert record["daytime_only_asofs"] == ["2026-10-15"] and record["edge"] == "2026-11-06"


def test_the_run_section_reads_the_last_run_whole():
    run = ok(FakeApi(), "get_admin_status", section="run")["last_run"]
    assert len(run["problems"]) == 4 and len(run["audit"]) == 2
    assert run["audit"][1]["ok"] is False


def test_a_panel_the_api_has_not_produced_yet_reads_as_not_present():
    body = afx.status()
    body["tape"] = None
    reply = ok(FakeApi(status=body), "get_admin_status", section="tape")
    assert reply["present"] is False


@pytest.mark.parametrize(
    "args,message",
    [
        (
            {"section": "bogus"},
            "Unknown section 'bogus'; `get_admin_status` has: tape, price_repairs, split_defects, "
            "missing_closes, record, run.",
        ),
    ],
)
def test_a_bad_get_admin_status_call_is_rejected(args, message):
    api = FakeApi()
    body = failed(api, "get_admin_status", **args)
    assert body["code"] == "invalid_arguments" and body["message"] == message
    assert [p for _, p, _ in api.requests if p.startswith("/admin")] == []


def test_an_asof_argument_is_ignored_by_either_steward_tool_and_the_reply_is_undated():
    # MEASURED: neither tool advertises asof, so the SDK's argument model drops it silently (no error); the reply
    # stays `asof: null`, like the research tools' undated reads
    for tool in ("get_splits", "get_admin_status"):
        reply = ok(FakeApi(), tool, asof="2026-11-01")
        assert reply["asof"] is None and reply["source"] == "live"


def test_an_empty_split_list_renders_zero_counts_and_an_empty_page():
    empty = {
        "needs_review": [],
        "announced": [],
        "confirmed": [],
        "set_aside": [],
        "total": 0,
        "unlisted_jumps": [],
        "dismissed_jumps": [],
        "unlisted_jumps_error": None,
    }
    api = FakeApi(splits=empty)
    reply = ok(api, "get_splits")
    assert reply["counts"] == {
        "needs_review": 0,
        "announced": 0,
        "confirmed": 0,
        "set_aside": 0,
        "unlisted_jumps": 0,
        "dismissed_jumps": 0,
        "total": 0,
        "needs_review_by_reason": {"sources_differ": 0, "extractor_abstained": 0, "one_source": 0},
    }
    assert reply["needs_review"] == [] and reply["jumps"] == [] and reply["next_cursor"] is None
    page = ok(api, "get_splits", section="needs_review")
    assert page["needs_review"] == [] and page["total"] == 0
    jumps = ok(api, "get_splits", section="jumps")
    assert jumps["unlisted"] == [] and jumps["dismissed"] == []


def test_neither_steward_tool_ever_sends_anything_but_a_get():
    api = FakeApi()
    ok(api, "get_splits")
    ok(api, "get_admin_status")
    assert {m for m, _, _ in api.requests} == {"GET"}
    assert [p for _, p, _ in api.requests] == ["/admin/splits", "/admin/status"]
