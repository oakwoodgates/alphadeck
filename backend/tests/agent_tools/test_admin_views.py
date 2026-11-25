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
from agent_tools.views.admin import shared_facts as shared_facts_view  # noqa: E402
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


def test_a_split_confirmed_with_cited_values_carries_its_citation_and_the_tape_override():
    """D3: the steward's cited values ride the brief row (copied from the API); a source-confirmed row carries
    neither key."""
    body = afx.splits()
    body["confirmed"][0].update(
        value_source="steward",
        decided_by="you (cited values), 11-25",
        cited_url="https://example.test/6-k",
        tape_override=False,
    )
    rows = ok(FakeApi(splits=body), "get_splits", section="confirmed")["confirmed"]
    assert (rows[0]["cited_url"], rows[0]["tape_override"]) == ("https://example.test/6-k", False)
    assert rows[0]["decided_by"] == "you (cited values), 11-25"
    assert "cited_url" not in rows[1] and "tape_override" not in rows[1]


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
        "deregistered": 0,  # W4-2: the unpriceable rows carrying a Form 15
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
    assert reply["insider_holds_owed"] == 2
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


def test_an_asof_argument_is_ignored_by_every_steward_tool_and_the_reply_is_undated():
    # MEASURED: no steward tool advertises asof, so the SDK's argument model drops it silently (no error); the reply
    # stays `asof: null`, like the research tools' undated reads
    for tool in (
        "get_splits",
        "get_admin_status",
        "get_shared_facts",
        "get_insider_holds",
        "get_admin_onboarding",
    ):
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


def test_no_steward_tool_ever_sends_anything_but_a_get_and_only_to_the_panels_routes():
    api = FakeApi()
    ok(api, "get_splits")
    ok(api, "get_admin_status")
    ok(api, "get_shared_facts")
    ok(api, "get_shared_facts", section="company", t="TK01")
    ok(api, "get_insider_holds")
    ok(api, "get_admin_onboarding")
    assert {m for m, _, _ in api.requests} == {"GET"}
    assert [p for _, p, _ in api.requests] == [
        "/admin/splits",
        "/admin/status",
        "/admin/platform-values/queue",
        "/dashboards/ticker-lookup/resolve",
        f"/admin/platform-values/{afx.SHARED_SECURITY_ID}",
        "/admin/insider-holds",
        "/admin/onboarding",
    ]


# --- get_shared_facts ----------------------------------------------------------------------------------------------


def _walk(api: FakeApi, tool: str, key: str, **args) -> list[dict]:
    """Every row of ``key`` over the reply's cursor chain, from the first reply to the last (each within budget)."""
    rows: list[dict] = []
    cursor = 0
    for _ in range(60):  # a bound, so a stuck cursor fails the test instead of hanging it
        reply = ok(api, tool, cursor=cursor, **args) if cursor else ok(api, tool, **args)
        rows += reply[key]
        if reply["next_cursor"] is None:
            return rows
        assert reply["next_cursor_for"] == key
        assert reply["next_cursor"] > cursor
        cursor = reply["next_cursor"]
    raise AssertionError("the cursor chain did not end")


def test_the_default_shared_facts_reply_is_the_queue_in_the_apis_order():
    api = FakeApi()
    reply = ok(api, "get_shared_facts")
    assert reply["asof"] is None and reply["source"] == "live" and reply["pen"] == "after"
    assert reply["total"] == 6 and reply["pen_only_total"] == 2
    assert [r["ticker"] for r in reply["items"]] == [f"TK0{k}" for k in range(1, 7)]
    assert reply["sections"] == {"company": "t"} and reply["next_cursor"] is None
    assert api.requests[0][1:] == (
        "/admin/platform-values/queue",
        {"limit": "25", "offset": "0", "pen": "after"},
    )
    first, second, third, fourth, _, pen = reply["items"]
    assert first["cards"] == 3 and first["pen_only"] is False and first["open_flags"] == 0
    assert pen["pen_only"] is True and pen["cards"] == 0
    shares = first["shares"]
    assert shares["state"] == "awaiting_steward" and shares["value"] is None
    cand = shares["candidate"]
    assert cand["shares"] == 10_000_001.0 and cand["flags"] == ["stale_share_count"]
    assert cand["filing_accession"] == "0000000001-26-000020" and cand["event_date"] == "2026-10-30"
    assert cand["passages"] == 2 and "passages_unreadable" not in cand and "reason" not in cand
    assert "ads_ratio" not in cand  # an optional figure rides only when the candidate states it
    assert len(cand["note"]) == 160 and cand["note_total_chars"] == 400
    assert (
        "located_passages" not in cand
    )  # the queue row counts its passages; the company page reads them
    cash = first["cash_burn"]["candidate"]
    assert cash["cash_usd"] == 4_000_001.0 and cash["quarterly_burn_usd"] == 750_000.0
    assert cash["investments_usd"] == 1_000_000.0 and cash["statement_currency"] == "USD"
    assert second["cash_burn"] is None and second["shares"] is not None
    shown = third["shares"]
    assert shown["state"] == "from_filing"
    assert set(shown["value"]) == {"valid_from", "ratified_by", "shares"}
    assert fourth["shares"] is None and fourth["cash_burn"] is None and fourth["open_flags"] == 2
    assert afx.USER_EMAIL not in json.dumps(reply)


@pytest.mark.parametrize(
    "pen,tickers",
    [
        ("only", ["TK05", "TK06"]),
        ("exclude", ["TK01", "TK02", "TK03", "TK04"]),
        ("after", [f"TK0{k}" for k in range(1, 7)]),
    ],
)
def test_pen_is_passed_to_the_api_and_both_totals_stay_whole(pen, tickers):
    api = FakeApi()
    reply = ok(api, "get_shared_facts", pen=pen)
    assert reply["pen"] == pen and [r["ticker"] for r in reply["items"]] == tickers
    assert (
        reply["total"] == 6 and reply["pen_only_total"] == 2
    )  # every company waiting, whatever the pen
    assert api.requests[0][2]["pen"] == pen


def test_the_cursor_is_the_apis_offset_and_next_cursor_is_set_when_the_api_page_fits_whole(
    monkeypatch,
):
    monkeypatch.setattr(shared_facts_view, "QUEUE_PAGE", 3)
    api = FakeApi()
    first = ok(api, "get_shared_facts")
    assert [r["ticker"] for r in first["items"]] == ["TK01", "TK02", "TK03"]
    assert first["next_cursor"] == 3 and first["next_cursor_for"] == "items"
    assert api.requests[0][2]["limit"] == "3" and api.requests[0][2]["offset"] == "0"
    second = ok(api, "get_shared_facts", cursor=3)
    assert api.requests[-1][2]["offset"] == "3"
    assert [r["ticker"] for r in second["items"]] == ["TK04", "TK05", "TK06"]
    assert second["next_cursor"] is None  # the last page: 3 + 3 reached the total
    only = ok(api, "get_shared_facts", pen="only")
    assert [r["ticker"] for r in only["items"]] == ["TK05", "TK06"] and only["next_cursor"] is None
    exclude = ok(api, "get_shared_facts", pen="exclude", cursor=3)
    assert [r["ticker"] for r in exclude["items"]] == ["TK04"] and exclude["next_cursor"] is None


def test_a_forty_company_queue_at_the_longest_notes_fits_and_the_cursor_walks_it_to_its_end():
    items = afx.queue_items(
        40,
        5,
        note_chars=afx.LONGEST_NOTE,
        passage_chars=afx.LONGEST_PASSAGE,
        passages=3,
    )
    api = FakeApi(queue=items)
    first = ok(api, "get_shared_facts")
    shown = len(first["items"])
    assert 1 <= shown < 25 and first["total"] == 40
    assert first["next_cursor"] == shown and first["next_cursor_for"] == "items"
    rows = _walk(api, "get_shared_facts", "items")
    assert [r["ticker"] for r in rows] == [i["ticker"] for i in items]  # the API's order, none lost
    assert all(m == "GET" for m, _, _ in api.requests)


def test_a_company_page_reads_both_values_and_cuts_every_long_text_with_its_length():
    page = afx.company(
        passage_chars=900,
        passages=1,
        note_chars=100,
        candidates=1,
        versions=1,
        decisions=0,
        attempts=1,
        open_flags=1,
    )
    api = FakeApi(company=page)
    reply = ok(api, "get_shared_facts", section="company", t="tk01")
    assert reply["section"] == "company" and reply["t"] == "tk01"
    assert (reply["ticker"], reply["name"]) == ("TK01", "Company 01")
    assert [p for _, p, _ in api.requests] == [
        "/dashboards/ticker-lookup/resolve",
        f"/admin/platform-values/{afx.SHARED_SECURITY_ID}",
    ]
    shares, cash = reply["shares"], reply["cash_burn"]
    assert shares["state"] == "awaiting_steward" and shares["value"] is None
    wait = shares["awaiting"]
    assert shares["pending"] is None and wait["passages_total"] == 1
    passage = wait["located_passages"][0]
    assert len(passage["excerpt"]) == 600 and passage["excerpt_total_chars"] == 900
    assert wait["shares"] == 10_000_001.0 and len(wait["note"]) == 100
    assert "note_total_chars" not in wait  # under its cut: whole
    assert cash["pinned"] is True and cash["pin"]["action"] == "pin"
    assert cash["pending"] is not None and cash["awaiting"] is None
    assert cash["value"]["ratified_by"] == "auto" and cash["value"]["currency"] == "USD"
    assert cash["versions_total"] == 1 and len(cash["versions"]) == 1
    assert cash["candidates_total"] == 1 and len(cash["candidates"]) == 1
    assert cash["decisions_total"] == 0 and cash["decisions"] == []
    assert reply["attempts_total"] == 1 and len(reply["attempts"]) == 1
    assert reply["flags"]["open_total"] == 1 and reply["flags"]["closed_total"] == 3
    flag = reply["flags"]["open"][0]
    assert len(flag["note"]) == 300 and flag["note_total_chars"] > 300
    assert flag["seen_candidate"] and "user_email" not in flag
    assert afx.USER_EMAIL not in json.dumps(reply)  # a user's email never reaches the model


def test_a_company_page_with_more_rows_than_fit_shows_the_newest_and_the_totals():
    page = afx.company()  # 4 versions, 6 candidates, 7 decisions, 4 attempts
    reply = ok(FakeApi(company=page), "get_shared_facts", section="company", t="TK01")
    cash = reply["cash_burn"]
    assert (cash["versions_total"], cash["candidates_total"], cash["decisions_total"]) == (4, 6, 7)
    assert reply["attempts_total"] == 4
    shown = len(cash["versions"])
    assert 0 < shown < 4  # the API lists versions oldest first: the newest ones, in its order
    assert [v["id"] for v in cash["versions"]] == [
        v["id"] for v in page["cash_burn"]["versions"][-shown:]
    ]
    # candidates and decisions come newest first: the first ones, in the API's order
    assert [c["id"] for c in cash["candidates"]] == [
        c["id"] for c in page["cash_burn"]["candidates"][: len(cash["candidates"])]
    ]
    assert [d["id"] for d in cash["decisions"]] == [
        d["id"] for d in page["cash_burn"]["decisions"][: len(cash["decisions"])]
    ]


def test_the_largest_company_page_steps_down_to_fit_and_keeps_every_total():
    big = afx.company(
        passages=4,
        passage_chars=afx.LONGEST_PASSAGE,
        note_chars=afx.LONGEST_NOTE,
        candidates=20,
        versions=30,
        decisions=50,
        open_flags=12,
        attempts=10,
    )
    reply = ok(FakeApi(company=big), "get_shared_facts", section="company", t="TK01")
    assert (
        reply["cash_burn"]["versions_total"] == 30 and reply["cash_burn"]["decisions_total"] == 50
    )
    assert reply["cash_burn"]["candidates_total"] == 20 and reply["attempts_total"] == 10
    assert reply["flags"]["open_total"] == 12
    wait = reply["shares"]["awaiting"]
    assert (
        wait["passages_total"] == 4 and wait["located_passages"]
    )  # a cut list says how many there are
    assert wait["located_passages"][0]["excerpt_total_chars"] == afx.LONGEST_PASSAGE


def test_fact_reads_one_value_with_its_passages_and_every_candidate_paged_by_the_cursor():
    api = FakeApi()
    reply = ok(api, "get_shared_facts", section="company", t="TK01", fact="shares_outstanding")
    assert reply["fact"] == "shares_outstanding" and "cash_burn" not in reply
    assert [c["id"] for c in reply["candidates"]] == [
        c["id"] for c in afx.company()["shares"]["candidates"]
    ]  # every candidate, newest first as the API lists them
    assert reply["shares"]["candidates_total"] == 6 and "candidates" not in reply["shares"]
    assert reply["flags"]["open_total"] == 1  # only this value's user flags
    big = afx.company(
        passages=4,
        passage_chars=afx.LONGEST_PASSAGE,
        note_chars=afx.LONGEST_NOTE,
        candidates=20,
    )
    api = FakeApi(company=big)
    first = ok(api, "get_shared_facts", section="company", t="TK01", fact="cash_burn")
    assert first["next_cursor_for"] == "candidates" and first["next_cursor"] == len(
        first["candidates"]
    )
    assert first["cash_burn"]["pending"]["located_passages"][0][
        "excerpt"
    ]  # the waiting candidate, read whole
    walked = _walk(
        api, "get_shared_facts", "candidates", section="company", t="TK01", fact="cash_burn"
    )
    assert [c["id"] for c in walked] == [c["id"] for c in big["cash_burn"]["candidates"]]


def test_an_unknown_ticker_on_the_company_page_is_unknown_ticker():
    err = failed(FakeApi(), "get_shared_facts", section="company", t="TK99")
    assert err["code"] == "unknown_ticker"


@pytest.mark.parametrize(
    "args,needle",
    [
        ({"t": "TK01"}, "t narrows section=company only."),
        ({"fact": "cash_burn"}, "fact narrows section=company only."),
        (
            {"section": "company"},
            "section=company of `get_shared_facts` needs t (a company's ticker).",
        ),
        ({"section": "bogus"}, "Unknown section 'bogus'; `get_shared_facts` has: company."),
        ({"pen": "sideways"}, "pen must be one of: after, only, exclude."),
        ({"section": "company", "t": "TK01", "pen": "only"}, "pen narrows the queue"),
        (
            {"section": "company", "t": "TK01", "fact": "revenue_mix"},
            "fact must be one of: shares_outstanding, cash_burn.",
        ),
    ],
)
def test_a_bad_get_shared_facts_call_is_rejected_before_any_request(args, needle):
    api = FakeApi()
    body = failed(api, "get_shared_facts", **args)
    assert body["code"] == "invalid_arguments" and needle in body["message"]
    assert api.requests == []


# --- get_insider_holds ---------------------------------------------------------------------------------------------


def test_the_default_holds_reply_is_counts_words_and_the_owed_rows_in_the_apis_order():
    api = FakeApi()
    reply = ok(api, "get_insider_holds")
    assert reply["asof"] is None and reply["source"] == "live" and reply["total"] == 9
    assert reply["counts"] == dict(zip(afx.HOLD_BUCKETS, (3, 2, 1, 1, 1, 1), strict=True))
    assert reply["bucket_words"] == afx.HOLD_BUCKET_WORDS
    assert [r["accession"] for r in reply["owed"]] == [h["accession"] for h in afx.holds()["owed"]]
    row = reply["owed"][0]
    assert row["row"] == afx.holds()["owed"][0]["row"]  # the filing's row, copied
    assert row["date_as_filed"] == "10/31/2027" and row["period_of_report_as_filed"] == "10/31/2026"
    assert row["suggested_date"] == "2026-10-31" and row["agrees"] is True
    assert row["market_date"] == "2026-11-02" and row["filing_url"].startswith(
        "https://www.sec.gov/"
    )
    assert len(row["owed_why"]) == 200 and row["owed_why_total_chars"] > 200
    # W3-1: where the filing writes the suggestion, its words (cut, the length beside), the filing's window, copies
    assert (row["suggestion_basis"], row["window_from"], row["window_to"], row["copies"]) == (
        "period_of_report",
        "2026-10-31",
        "2026-11-02",
        [],
    )
    assert len(row["suggestion_words"]) == 200 and row["suggestion_words_total_chars"] > 200
    assert {"decision", "why", "filing_night", "history"}.isdisjoint(row)
    assert reply["sections"] == dict([(b, None) for b in afx.HOLD_BUCKETS] + [("one", "t")])
    assert [p for _, p, _ in api.requests] == ["/admin/insider-holds"]


@pytest.mark.parametrize("bucket", afx.HOLD_BUCKETS[1:])
def test_a_bucket_section_pages_that_bucket_with_the_fields_its_state_needs(bucket):
    body = afx.holds()
    reply = ok(FakeApi(holds=body), "get_insider_holds", section=bucket)
    assert reply["section"] == bucket and reply["total"] == len(body[bucket])
    assert [r["hold_id"] for r in reply[bucket]] == [h["hold_id"] for h in body[bucket]]
    row = reply[bucket][0]
    assert ("filing_night" in row) == (bucket == "placed_by_rule")
    assert ("why" in row) == (bucket == "filer_corrected")
    assert ("decision" in row) == (bucket in ("decided", "dismissed"))
    if bucket in ("decided", "dismissed"):
        decision = row["decision"]
        assert decision["action"] == ("approve" if bucket == "decided" else "dismiss")
        assert len(decision["reason"]) == 200 and decision["reason_total_chars"] > 200
        assert decision["decided_by"]["display_name"] == "Steward 01"
    if bucket == "filer_corrected":
        assert len(row["why"]) == 200


def test_a_forty_row_owed_bucket_fits_the_budget_and_the_cursor_walks_it_to_its_end():
    api = FakeApi(holds=afx.holds(owed=40))
    reply = ok(api, "get_insider_holds")
    assert 1 <= len(reply["owed"]) < 40 and reply["counts"]["owed"] == 40
    assert reply["next_cursor"] == len(reply["owed"]) and reply["next_cursor_for"] == "owed"
    walked = _walk(api, "get_insider_holds", "owed", section="owed")
    assert [r["hold_id"] for r in walked] == [h["hold_id"] for h in afx.holds(owed=40)["owed"]]


def test_one_reads_a_tickers_rows_in_full_with_their_history_and_pages_them():
    body = afx.holds_on_one_ticker(18)
    api = FakeApi(holds=body)
    first = ok(api, "get_insider_holds", section="one", t="tk01")  # lower case matches
    assert first["found"] == 18 and first["t"] == "tk01" and first["next_cursor_for"] == "holds"
    full = first["holds"][0]
    assert {"security_id", "accepted", "bucket", "reason", "history", "history_total"} <= set(full)
    assert len(full["suggestion_words"]) == 600 and full["suggestion_words_total_chars"] > 600
    assert full["copies"] == [] and full["suggestion_basis"] == "period_of_report"
    walked = _walk(api, "get_insider_holds", "holds", section="one", t="TK01")
    assert len(walked) == 18 and len({h["hold_id"] for h in walked}) == 18
    decided = [h for h in walked if h["bucket"] == "decided"]
    assert decided and all(h["history_total"] == 2 and len(h["history"]) == 2 for h in decided)
    assert (
        decided[0]["history"][1]["action"] == "revoke"
        and decided[0]["decision"]["action"] == "approve"
    )
    assert (
        len(decided[0]["decision"]["reason"]) == 300
    )  # the full row cuts a reason at 300, its length beside it
    assert ok(FakeApi(), "get_insider_holds", section="one", t="TK99")["found"] == 0


@pytest.mark.parametrize(
    "args,needle",
    [
        ({"t": "TK01"}, "t narrows section=one only."),
        ({"section": "owed", "t": "TK01"}, "t narrows section=one only."),
        (
            {"section": "one"},
            "section=one of `get_insider_holds` needs t (a ticker with held insider rows).",
        ),
        (
            {"section": "bogus"},
            "Unknown section 'bogus'; `get_insider_holds` has: owed, placed_by_rule, filer_corrected, "
            "decided, dismissed, amendment_row, one.",
        ),
    ],
)
def test_a_bad_get_insider_holds_call_is_rejected_before_any_request(args, needle):
    api = FakeApi()
    body = failed(api, "get_insider_holds", **args)
    assert body["code"] == "invalid_arguments" and needle in body["message"]
    assert api.requests == []


# --- get_admin_onboarding ------------------------------------------------------------------------------------------


def test_the_default_admin_onboarding_reply_is_the_shared_queue_with_failed_names_first():
    api = FakeApi(admin_onboarding=afx.admin_onboarding())
    reply = ok(api, "get_admin_onboarding")
    assert reply["asof"] is None and reply["source"] == "live" and "thesis" not in reply
    assert reply["counts"]["failed"] == 3 and reply["pending_total"] == 116
    assert [n["status"] for n in reply["pending"][:5]] == [
        "failed",
        "failed",
        "failed",
        "in_progress",
        "queued",
    ]
    assert len(reply["pending"]) == 10 and reply["recent_done_total"] == 20
    assert reply["pending"][0]["last_error_total_chars"] == 210 + len("Synthetic: ")
    assert reply["window"] and reply["stalled"] is None and reply["last_run"]["status"] == "ok"
    assert reply["sections"] == {"pending": None, "recent_done": None}
    assert [p for _, p, _ in api.requests] == ["/admin/onboarding"]


def test_the_pending_section_walks_116_names_to_the_end_in_the_apis_order():
    api = FakeApi(admin_onboarding=afx.admin_onboarding(pending=116))
    names = _walk(api, "get_admin_onboarding", "pending", section="pending")
    assert [n["ticker"] for n in names] == [p["ticker"] for p in afx.admin_onboarding()["pending"]]
    assert [n["status"] for n in names[:4]] == ["failed", "failed", "failed", "in_progress"]
    recent = ok(api, "get_admin_onboarding", section="recent_done")
    assert recent["recent_done_total"] == 20 and len(recent["recent_done"]) == 20


def test_a_parked_admin_onboarding_name_says_parked_in_the_brief_and_the_full_row():
    payload = afx.admin_onboarding(pending=6, failed=1)
    ame = payload["pending"][1]  # the in-progress name: loading, then its card was removed
    assert ame["status"] == "in_progress"
    ame.update(in_live_basket=False, parked=True)
    payload["counts"]["parked"] = 1
    api = FakeApi(admin_onboarding=payload)

    brief = {n["ticker"]: n for n in ok(api, "get_admin_onboarding")["pending"]}
    assert brief[ame["ticker"]]["parked"] is True
    assert [t for t, n in brief.items() if n["parked"]] == [ame["ticker"]]

    full = {n["ticker"]: n for n in ok(api, "get_admin_onboarding", section="pending")["pending"]}
    assert full[ame["ticker"]]["parked"] is True
    assert [t for t, n in full.items() if n["parked"]] == [ame["ticker"]]


def test_an_admin_onboarding_queue_that_is_empty_reads_as_zero_counts():
    quiet = afx.admin_onboarding(pending=0, failed=0, recent=0)
    quiet["counts"] = {"queued": 0, "in_progress": 0, "failed": 0, "done": 0, "parked": 0}
    reply = ok(FakeApi(admin_onboarding=quiet), "get_admin_onboarding")
    assert reply["pending_total"] == 0 and reply["pending"] == [] and reply["recent_done"] == []


def test_a_bad_get_admin_onboarding_call_is_rejected_before_any_request():
    api = FakeApi()
    body = failed(api, "get_admin_onboarding", section="bogus")
    assert body["code"] == "invalid_arguments"
    assert body["message"] == (
        "Unknown section 'bogus'; `get_admin_onboarding` has: pending, recent_done."
    )
    assert api.requests == []


# --- every new steward tool, together ------------------------------------------------------------------------------


def test_each_new_tool_refuses_to_run_without_the_api_saying_the_caller_is_a_steward():
    api = FakeApi(steward=False)
    for tool, path in (
        ("get_shared_facts", "/admin/platform-values/queue"),
        ("get_insider_holds", "/admin/insider-holds"),
        ("get_admin_onboarding", "/admin/onboarding"),
    ):
        body = failed(api, tool)
        assert body["code"] == "forbidden" and body["message"] == "stewards only"
        assert api.requests[-1][1] == path


def test_the_new_tools_name_their_panel_and_say_they_only_read():
    async def listing():
        client = ApiClient("http://127.0.0.1:8001", transport=FakeApi().transport())
        reset = CURRENT_PRINCIPAL.set(STEWARD)
        try:
            async with Client(server_mod.build_server(lambda: client)) as session:
                return {t.name: t for t in (await session.list_tools()).tools}
        finally:
            CURRENT_PRINCIPAL.reset(reset)
            await client.aclose()

    tools = asyncio.run(listing())
    for name, panel in (
        ("get_shared_facts", "Admin > Shared facts"),
        ("get_insider_holds", "Admin > Held insider rows"),
        ("get_admin_onboarding", "Admin > Onboarding"),
    ):
        text = tools[name].description
        assert panel in text and text.endswith("Steward scope."), name
        assert "Reads only" in text, name
        assert "asof" not in tools[name].input_schema["properties"], name
    assert "positive when the company consumed cash" in tools["get_shared_facts"].description
    assert "never shown" in tools["get_shared_facts"].description  # a user's email
    assert "Retry" in tools["get_admin_onboarding"].description
