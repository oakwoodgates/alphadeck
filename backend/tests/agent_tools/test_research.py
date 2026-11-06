"""The research-stage tools (``get_triage``, ``get_onboarding``; ``get_discovery`` and ``get_extract`` follow): sizes, cursor
walks, narrowings, rejections, the wire-key guard and the purity of the views. Synthetic fixtures at the MEASURED sizes
(``research_fixtures.py``); the server through the official SDK client against the fake API.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import importlib
import json
from pathlib import Path

import pytest

pytest.importorskip("mcp")

from mcp.client.client import Client  # noqa: E402

from agent_tools import server as server_mod  # noqa: E402
from agent_tools.budget import size  # noqa: E402
from agent_tools.client import ApiClient  # noqa: E402
from agent_tools.views import research as research_pkg  # noqa: E402
from agent_tools.views.research import discovery as discovery_view  # noqa: E402
from agent_tools.views.research import extract as extract_view  # noqa: E402
from agent_tools.views.research import onboarding as onboarding_view  # noqa: E402
from agent_tools.views.research import triage as triage_view  # noqa: E402
from agent_tools.views.research.common import ResearchRequest, ViewError  # noqa: E402
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, ETFS_LABEL  # noqa: E402
from tests.agent_tools import research_fixtures as rfx  # noqa: E402
from tests.agent_tools.fake_api import FakeApi  # noqa: E402

BUDGET = 8_000
THESIS = {"id": rfx.THESIS_ID, "name": "Thesis 01 synthetic theme card"}
SILENT = [CONTEXT_LABEL, ETFS_LABEL, BENCH_LABEL]


def run(api: FakeApi | None, body):
    async def go():
        client = ApiClient("http://127.0.0.1:8001", transport=(api or FakeApi()).transport())
        try:
            async with Client(server_mod.build_server(client)) as session:
                return await body(session)
        finally:
            await client.aclose()

    return asyncio.run(go())


def call(tool: str, **args):
    """One tool call through the server; returns the structured reply (asserted within the 8,000 budget)."""

    async def body(session):
        return await session.call_tool(tool, args)

    result = run(FakeApi(), body)
    assert not result.is_error, result.content[0].text
    assert len(result.content[0].text) <= BUDGET, (tool, args, len(result.content[0].text))
    return result.structured_content


def failure(tool: str, api: FakeApi | None = None, **args) -> dict:
    async def body(session):
        return await session.call_tool(tool, args)

    result = run(api or FakeApi(), body)
    assert result.is_error, result.content[0].text
    return json.loads(result.content[0].text)


def walk(tool: str, list_key: str, **args) -> tuple[list, int]:
    """Every row of one list across the cursor pages of one read, and the number of pages."""
    rows: list = []
    cursor, pages = 0, 0
    while cursor is not None:
        reply = call(tool, **args, **({"cursor": cursor} if cursor else {}))
        rows.extend(reply[list_key])
        pages += 1
        assert pages < 200, "cursor never ended"
        cursor = reply["next_cursor"]
        if cursor is not None:
            assert reply["next_cursor_for"] == list_key
    return rows, pages


TRIAGE = {"thesis": rfx.THESIS_ID, "asof": rfx.ASOF}
SCORED = rfx.scored()
MEMBERS = SCORED["members"]


# --- get_triage --------------------------------------------------------------------------------------------------------


def test_triage_default_carries_the_funnel_the_counts_and_the_first_rows_in_the_api_order():
    reply = call("get_triage", **TRIAGE)
    assert reply["thesis"] == THESIS and reply["asof"] == rfx.ASOF and reply["source"] == "live"
    assert [s["label"] for s in reply["segments"]] == [s["label"] for s in SCORED["segments"]]
    assert [s["silent"] for s in reply["segments"]] == [False] * 4 + [True] * 3
    sizes = {s["label"]: s["members_total"] for s in reply["segments"]}
    assert list(sizes.values()) == list(rfx.MEMBER_COUNTS)
    voting = reply["segments"][:4]
    assert [s["confirmed"] + s["unconfirmed"] for s in voting] == [
        s["members_total"] for s in voting
    ]
    assert sum(s["confirmed"] for s in reply["segments"]) == 6
    assert reply["counts"] == {
        "members_total": 79,
        "silent_rows": 30,
        "unconfirmed_members": 73,
        "thin_price_history": 1,
        "price_feed_limitation": 1,
    }
    shown = reply["members"]
    assert [m["ticker"] for m in shown] == [m["ticker"] for m in MEMBERS[: len(shown)]]
    assert reply["members_total"] == 79 and reply["next_cursor"] == len(shown)
    assert reply["sections"] == {
        "member": "t",
        "segment": "segment",
        "unconfirmed": None,
        "silent": None,
    }
    assert all(s["descriptor"] == s["descriptor"][:80] for s in reply["segments"])


def test_triage_rows_copy_the_meters_and_the_sources():
    row = call("get_triage", **TRIAGE)["members"][0]
    api = MEMBERS[0]
    assert row["meters"] == {
        "purity": api["purity"]["pips"],
        "runway": api["runway"]["pips"],
        "catalysts": api["catalysts"]["pips"],
        "dilution": api["dilution"]["pips"],
        "market_cap": api["market_cap"]["value"],
    }
    assert row["unconfirmed_estimates"] == 0 and row["fit"] == api["fit"]
    assert "ratified" in row["sources"] and row["sources"][0] == "ratified"
    assert len(row["sources"]) == len(set(row["sources"]))  # distinct
    assert "instrument_kind" not in row and "thin_price_history" not in row
    assert len(row["name"]) <= 30


def test_triage_default_cursor_walks_all_79_members_in_at_most_four_pages_in_the_api_order():
    rows, pages = walk("get_triage", "members", **TRIAGE)
    assert pages <= 4, pages
    assert [r["ticker"] for r in rows] == [m["ticker"] for m in MEMBERS]
    # the funnel and the counts ride the first page only
    second = call("get_triage", **TRIAGE, cursor=20)
    assert "segments" not in second and "counts" not in second


@pytest.mark.parametrize(
    ("section", "args", "expected"),
    [
        ("unconfirmed", {}, [m for m in MEMBERS if m["unconfirmed_estimates"] > 0]),
        ("silent", {}, [m for m in MEMBERS if m["segment"] in SILENT]),
        (
            "segment",
            {"segment": "Segment 04"},
            [m for m in MEMBERS if m["segment"] == "Segment 04"],
        ),
    ],
)
def test_triage_list_sections_hold_only_their_rows_and_page_to_the_end(section, args, expected):
    rows, _ = walk("get_triage", "members", **TRIAGE, section=section, **args)
    assert [r["ticker"] for r in rows] == [m["ticker"] for m in expected]
    if section == "unconfirmed":
        assert all(r["unconfirmed_estimates"] > 0 for r in rows) and len(rows) == 73
    reply = call("get_triage", **TRIAGE, section=section, **args)
    assert reply["section"] == section and reply["members_total"] == len(expected)


def test_triage_segment_narrows_the_default_too():
    reply = call("get_triage", **TRIAGE, segment="Segment 02")
    assert reply["narrowed"] == {"segment": "Segment 02"} and reply["members_total"] == 5
    assert {m["segment"] for m in reply["members"]} == {"Segment 02"}
    assert len(reply["segments"]) == 7  # the funnel stays whole


def test_triage_member_section_holds_every_meters_provenance_and_cuts_long_detail():
    payload = copy.deepcopy(SCORED)
    long_note = "x" * 450
    payload["members"][0]["purity"]["provenance"][0]["detail"]["note"] = long_note
    req = ResearchRequest(thesis=THESIS, asof=rfx.ASOF, section="member", t="tk01")
    reply = triage_view.view(payload, req)
    (member,) = reply["members"]
    assert member["ticker"] == "TK01" and member["security_id"] == MEMBERS[0]["security_id"]
    for meter in triage_view.METERS:
        got = member[meter]["provenance"]
        want = MEMBERS[0][meter]["provenance"]
        assert [(p["source"], p["ref"], p["url"]) for p in got] == [
            (p["source"], p["ref"], p["url"]) for p in want
        ]
    note = member["purity"]["provenance"][0]["detail"]
    assert note["note"] == long_note[:200] and note["note_total_chars"] == 450
    assert size(reply) <= BUDGET
    served = call("get_triage", **TRIAGE, section="member", t="TK02")
    assert served["members"][0]["ticker"] == "TK02" and served["next_cursor"] is None


def test_triage_row_flags_appear_only_when_they_hold():
    payload = copy.deepcopy(SCORED)
    payload["members"][33]["instrument_kind"] = "etf"
    rows = []
    for label in ("Segment 04", BENCH_LABEL):
        req = ResearchRequest(thesis=THESIS, asof=rfx.ASOF, section="segment", segment=label)
        rows += triage_view.view(payload, req)["members"]
    by_ticker = {m["ticker"]: m for m in rows}
    assert by_ticker["TK34"]["instrument_kind"] == "etf" and by_ticker["TK34"]["thin_price_history"]
    assert by_ticker["TK62"]["price_feed_limitation"]
    healthy = by_ticker["TK28"]
    assert not {"instrument_kind", "thin_price_history", "price_feed_limitation"} & set(healthy)


def test_triage_never_sorts_the_api_order_rules():
    payload = copy.deepcopy(SCORED)
    payload["members"] = list(reversed(payload["members"]))
    reply = triage_view.view(
        payload, ResearchRequest(thesis=THESIS, asof=rfx.ASOF, section="silent")
    )
    expected = [m["ticker"] for m in payload["members"] if m["segment"] in SILENT]
    assert [m["ticker"] for m in reply["members"]][:3] == expected[:3]


def test_triage_a_member_segment_the_api_list_omits_still_gets_a_funnel_entry():
    payload = copy.deepcopy(SCORED)
    payload["members"][0]["segment"] = "Discovered"
    funnel = triage_view.view(payload, ResearchRequest(thesis=THESIS, asof=rfx.ASOF))["segments"]
    assert funnel[-1]["label"] == "Discovered" and funnel[-1]["members_total"] == 1


def test_triage_an_unknown_segment_or_ticker_is_invalid_arguments_naming_the_labels():
    err = failure("get_triage", **TRIAGE, segment="Segment 99")
    assert err["code"] == "invalid_arguments" and "Segment 01" in err["message"]
    err = failure("get_triage", **TRIAGE, section="member", t="NOPE")
    assert err["code"] == "invalid_arguments" and "NOPE" in err["message"]


@pytest.mark.parametrize(
    ("args", "expect"),
    [
        ({"section": "member"}, "section=member of `get_triage` needs t (a ticker"),
        ({"section": "segment"}, "section=segment of `get_triage` needs segment (a link label"),
        (
            {"section": "nope"},
            "Unknown section 'nope'; `get_triage` has: member, segment, unconfirmed, silent.",
        ),
        ({"asof": "2026-02-30"}, "asof is not a real calendar date."),
        ({"asof": "yesterday"}, "asof takes a date as YYYY-MM-DD"),
    ],
)
def test_triage_rejections_name_the_rule_before_any_request(args, expect):
    api = FakeApi()
    err = failure("get_triage", api, **{"thesis": rfx.THESIS_ID, **args})
    assert err["code"] == "invalid_arguments" and err["retryable"] is False
    assert expect in err["message"], err["message"]
    assert api.requests == [], api.requests


def test_triage_unknown_thesis_and_the_scored_read_is_dated():
    err = failure("get_triage", thesis="no such card", asof=rfx.ASOF)
    assert err["code"] == "unknown_thesis"
    api = FakeApi()
    call_args = {"thesis": rfx.THESIS_ID, "asof": rfx.ASOF}

    async def body(session):
        return await session.call_tool("get_triage", call_args)

    run(api, body)
    scored = [r for r in api.requests if r[1].endswith("/scored")]
    assert len(scored) == 1 and scored[0][2] == {"asof": rfx.ASOF}


def test_triage_omitted_asof_reads_the_apis_current_night():
    api = FakeApi()

    async def body(session):
        return await session.call_tool("get_triage", {"thesis": rfx.THESIS_ID})

    result = run(api, body)
    assert result.structured_content["asof"] == rfx.ASOF
    assert [r[1] for r in api.requests][0] == "/clock"


# --- get_onboarding ----------------------------------------------------------------------------------------------------

ONBOARDING = rfx.onboarding()


def test_onboarding_default_carries_counts_window_and_the_first_names_within_budget():
    reply = call("get_onboarding", thesis=rfx.THESIS_ID)
    assert reply["thesis"] == THESIS and reply["asof"] is None and reply["source"] == "live"
    for key in ("counts", "window", "stalled", "paused", "history_legs", "last_run"):
        assert reply[key] == ONBOARDING[key], key
    assert reply["pending_total"] == 3 and len(reply["pending"]) == 3
    assert reply["recent_done_total"] == 20 and len(reply["recent_done"]) == 5
    assert [n["ticker"] for n in reply["pending"]] == [n["ticker"] for n in ONBOARDING["pending"]]
    assert [n["ticker"] for n in reply["recent_done"]] == [
        n["ticker"] for n in ONBOARDING["recent_done"][:5]
    ]
    failed = reply["pending"][2]
    assert failed["status"] == "failed" and failed["attempts"] == 3
    assert len(failed["last_error"]) == 120 and failed["last_error_total_chars"] > 120
    assert set(reply["recent_done"][0]) == {"ticker", "finished_at", "legs_done"}
    assert reply["next_cursor"] is None and reply["sections"] == {
        "pending": None,
        "recent_done": None,
    }


@pytest.mark.parametrize("section", ["pending", "recent_done"])
def test_onboarding_sections_page_the_full_lists_to_the_end(section):
    rows, _ = walk("get_onboarding", section, thesis=rfx.THESIS_ID, section=section)
    assert [r["security_id"] for r in rows] == [n["security_id"] for n in ONBOARDING[section]]
    reply = call("get_onboarding", thesis=rfx.THESIS_ID, section=section)
    assert reply[f"{section}_total"] == len(ONBOARDING[section])


def test_onboarding_a_long_list_continues_by_cursor_without_gaps():
    payload = copy.deepcopy(ONBOARDING)
    payload["recent_done"] = payload["recent_done"] * 4  # 80 names of 431 characters
    cursor, seen, pages = 0, [], 0
    while cursor is not None:
        reply = onboarding_view.view(
            payload,
            ResearchRequest(thesis=THESIS, section="recent_done", cursor=cursor),
        )
        assert size(reply) <= BUDGET
        seen.extend(reply["recent_done"])
        cursor, pages = reply["next_cursor"], pages + 1
        assert pages < 50
    assert len(seen) == 80 and pages > 1


def test_onboarding_rejections_name_the_rule_before_any_request():
    api = FakeApi()
    err = failure("get_onboarding", api, thesis=rfx.THESIS_ID, section="nope")
    assert err["code"] == "invalid_arguments"
    assert "Unknown section 'nope'; `get_onboarding` has: pending, recent_done." in err["message"]
    assert api.requests == []
    assert failure("get_onboarding", thesis="no such card")["code"] == "unknown_thesis"


def test_onboarding_view_rejects_an_unknown_section_if_the_server_check_were_skipped():
    with pytest.raises(ViewError):
        onboarding_view.view(ONBOARDING, ResearchRequest(thesis=THESIS, section="nope"))


# --- get_discovery -----------------------------------------------------------------------------------------------------

RUN = rfx.run_latest()
PLACEMENTS = RUN["placements"]
DISCOVERY = {"thesis": rfx.THESIS_ID}


def by_status(rows):
    return {s: sum(1 for p in rows if p["status"] == s) for s in discovery_view.STATUSES}


def test_discovery_default_carries_the_report_the_counts_and_the_first_page_of_placements():
    reply = call("get_discovery", **DISCOVERY)
    assert reply["thesis"] == THESIS and reply["asof"] is None and reply["source"] == "live"
    assert [r["run_id"] for r in reply["runs"]] == [rfx.RUN_LATEST_ID, rfx.RUN_SMALL_ID]
    assert reply["run"]["run_id"] == rfx.RUN_LATEST_ID and reply["run"]["scope"] == "full"
    report = reply["run"]["report"]
    assert report["pages_ok"] == 1_296 and report["pages_attempted"] == 1_301
    assert report["capped_terms"] == rfx.CAPPED_TERMS  # the lists whole: the visibility rule
    assert report["empty_terms"] == rfx.EMPTY_TERMS and report["failed_terms"] == rfx.FAILED_TERMS
    assert report["tail_sweep"] == "ran" and report["narration_needed"] == 1_706
    counts = reply["counts"]
    assert counts["placements_total"] == 1_735
    assert counts["by_status"] == {"placed": 640, "verify": 1_066, "ambiguous": 27, "absent": 2}
    assert counts["by_source"] == {"edgar": 1_732, "off_universe": 3}
    assert counts["off_thesis"] == 1_145 and counts["with_candidates"] == 27
    segments = reply["segments"]
    assert [s["label"] for s in segments] == [s["label"] for s in RUN["segments"]]
    assert sum(s["placements_total"] for s in segments) == 1_735
    for s in segments:
        rows = [p for p in PLACEMENTS if p["segment"] == s["label"]]
        assert s["by_status"] == by_status(rows)
        assert s["off_thesis"] == sum(1 for p in rows if p["off_thesis"])
    first = reply["placements"][0]
    assert (
        first["name"] == PLACEMENTS[0]["name"][:30] and first["ticker"] == PLACEMENTS[0]["ticker"]
    )
    assert first["matched_terms"] == PLACEMENTS[0]["matched_terms"][:3]
    assert first["matched_terms_total"] == len(PLACEMENTS[0]["matched_terms"])
    assert reply["placements_total"] == 1_735 and reply["next_cursor_for"] == "placements"
    assert reply["sections"]["placement"] == "t"


def test_discovery_rows_flag_only_the_exceptions():
    rows = {p["ticker"]: p for p in walk("get_discovery", "placements", **DISCOVERY)[0]}
    source = {p["ticker"]: p for p in PLACEMENTS}
    plain = next(t for t, p in source.items() if p["status"] == "verify" and not p["off_thesis"])
    assert not {"off_thesis", "source", "candidates"} & set(rows[plain])
    flagged = next(t for t, p in source.items() if p["off_thesis"] and t in rows)
    assert rows[flagged]["off_thesis"] is True
    universe = [r for r in rows.values() if r.get("source") == "off_universe"]
    assert len(universe) == 3
    ambiguous = [r for r in rows.values() if r["status"] == "ambiguous"]
    assert ambiguous and all(r["candidates"] == 2 for r in ambiguous)


def test_discovery_default_cursor_walks_all_1735_placements_in_the_runs_order():
    rows, pages = walk("get_discovery", "placements", **DISCOVERY)
    assert len(rows) == 1_735 and pages < 60
    assert [r["name"] for r in rows] == [p["name"][:30] for p in PLACEMENTS]
    later = call("get_discovery", **DISCOVERY, cursor=30)
    assert "segments" not in later and "counts" not in later and "runs" not in later
    assert later["run"] == {"run_id": rfx.RUN_LATEST_ID}  # a continuation names the run it walks


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        ({"status": "placed"}, 640),
        ({"status": "ambiguous"}, 27),
        ({"segment": "Segment 03"}, None),
        ({"status": "verify", "segment": "Segment 03"}, None),
    ],
)
def test_discovery_status_and_segment_narrow_the_placements(args, expected):
    rows, _ = walk("get_discovery", "placements", **DISCOVERY, **args)
    want = [
        p
        for p in PLACEMENTS
        if p["status"] == args.get("status", p["status"])
        and p["segment"] == args.get("segment", p["segment"])
    ]
    assert [r["name"] for r in rows] == [p["name"][:30] for p in want]
    if expected is not None:
        assert len(rows) == expected
    reply = call("get_discovery", **DISCOVERY, **args)
    assert reply["narrowed"] == args and reply["placements_total"] == len(want)


def test_discovery_the_ambiguous_status_fits_one_page_and_the_section_lists_all_27_with_candidates():
    reply = call("get_discovery", **DISCOVERY, status="ambiguous")
    assert len(reply["placements"]) == 27 and reply["next_cursor"] is None
    rows, _ = walk("get_discovery", "placements", **DISCOVERY, section="ambiguous")
    assert len(rows) == 27
    source = [p for p in PLACEMENTS if p["candidates"]]
    assert [r["name"] for r in rows] == [p["name"] for p in source]
    for row, p in zip(rows, source, strict=True):
        assert row["candidates"] == [
            {k: c[k] for k in ("security_id", "ticker", "name", "cik")} for c in p["candidates"]
        ]


def test_discovery_placement_section_reads_one_in_full_by_ticker_or_name():
    target = next(p for p in PLACEMENTS if p["status"] == "placed" and len(p["matched_terms"]) > 5)
    by_ticker = call("get_discovery", **DISCOVERY, section="placement", t=target["ticker"].lower())
    (got,) = by_ticker["placements"]
    assert got["prose"] == target["prose"] and got["matched_terms"] == target["matched_terms"]
    assert got["security_id"] == target["security_id"] and got["discovery_source"] == "edgar"
    for key in ("sector", "exchange", "listing_status", "category", "origin", "business_type"):
        assert got[key] == target[key], key
    nameless = next(p for p in PLACEMENTS if p["ticker"] is None and p["candidates"])
    by_name = call("get_discovery", **DISCOVERY, section="placement", t=nameless["name"].upper())
    assert by_name["placements"][0]["name"] == nameless["name"]
    assert by_name["placements"][0]["candidates"] == [
        {k: c[k] for k in ("security_id", "ticker", "name", "cik")} for c in nameless["candidates"]
    ]


def test_discovery_report_section_counts_the_placements_behind_each_flagged_term():
    reply = call("get_discovery", **DISCOVERY, section="report")
    assert (
        reply["report"]["capped_terms"] == rfx.CAPPED_TERMS and reply["report"]["scope"] == "full"
    )
    hits = reply["term_hits"]
    for kind, terms in (
        ("capped", rfx.CAPPED_TERMS),
        ("empty", rfx.EMPTY_TERMS),
        ("failed", rfx.FAILED_TERMS),
    ):
        assert [h["term"] for h in hits[kind]] == terms
        for h in hits[kind]:
            assert h["placements"] == sum(1 for p in PLACEMENTS if h["term"] in p["matched_terms"])
    assert all(h["placements"] == 0 for h in hits["empty"] + hits["failed"])


def test_discovery_terms_section_joins_the_term_set_with_the_run_and_keeps_zero_hit_terms():
    api = FakeApi(details={rfx.THESIS_ID: rfx.thesis_with_terms()})

    async def body(session):
        return await session.call_tool("get_discovery", {**DISCOVERY, "section": "terms"})

    result = run(api, body)
    assert not result.is_error and len(result.content[0].text) <= BUDGET
    reply = result.structured_content
    terms = reply["terms"]
    assert reply["terms_total"] == 31 and [t["term"] for t in terms] == [
        f"term-{k:02d}" for k in range(1, 32)
    ]
    assert terms[0]["tier"] == "signal" and terms[20]["tier"] == "broad"
    assert terms[0]["authored_by"] == "operator_set" and terms[20]["source"] == "keyword_gen"
    for entry in terms:
        assert entry["placements"] == sum(
            1 for p in PLACEMENTS if entry["term"] in p["matched_terms"]
        )
    flags = {t["term"]: {k for k in ("capped", "empty", "failed") if t.get(k)} for t in terms}
    assert flags["term-02"] == {"capped"} and flags["term-27"] == {"empty"}
    assert flags["term-31"] == {"failed"} and flags["term-01"] == set()
    zero = [t for t in terms if t["placements"] == 0]
    assert {t["term"] for t in zero} == {"term-27", "term-28", "term-31"}  # listed, never dropped
    reads = [r[1] for r in api.requests]
    assert f"/theses/{rfx.THESIS_ID}" in reads  # the thesis is read only for this section
    other = FakeApi()

    async def plain(session):
        return await session.call_tool("get_discovery", DISCOVERY)

    run(other, plain)
    assert f"/theses/{rfx.THESIS_ID}" not in [r[1] for r in other.requests]


def test_discovery_a_card_with_no_saved_run_answers_with_a_note_not_an_error():
    api = FakeApi()

    async def body(session):
        return await session.call_tool("get_discovery", {"thesis": rfx.SECOND_THESIS_ID})

    result = run(api, body)
    assert not result.is_error
    reply = result.structured_content
    assert reply["runs"] == [] and reply["note"] == "no saved draft run for this card"
    assert "run" not in reply and "placements" not in reply and reply["next_cursor"] is None
    assert not [r for r in api.requests if "/runs/" in r[1]]
    again = call("get_discovery", thesis=rfx.SECOND_THESIS_ID, section="ambiguous")
    assert again["note"] == "no saved draft run for this card" and again["runs"] == []


def test_discovery_a_disabled_run_loader_and_an_unknown_run_pass_the_apis_detail_through():
    err = failure("get_discovery", FakeApi(runs_disabled=True), **DISCOVERY)
    assert err["code"] == "api_rejected" and err["message"] == "run loader is disabled"
    err = failure("get_discovery", **DISCOVERY, run="2026-01-01T00-00-00_nope")
    assert err["code"] == "api_rejected" and err["message"] == "saved run not found"
    small = call("get_discovery", **DISCOVERY, run=rfx.RUN_SMALL_ID)
    assert small["run"]["run_id"] == rfx.RUN_SMALL_ID
    assert small["counts"]["placements_total"] == 203 and len(small["segments"]) == 4


@pytest.mark.parametrize(
    ("args", "expect"),
    [
        ({"status": "pending"}, "status must be one of: placed, verify, ambiguous, absent."),
        (
            {"section": "placement"},
            "section=placement of `get_discovery` needs t (a ticker, or a name",
        ),
        (
            {"section": "nope"},
            "Unknown section 'nope'; `get_discovery` has: placement, ambiguous, report, terms.",
        ),
    ],
)
def test_discovery_rejections_name_the_rule_before_any_request(args, expect):
    api = FakeApi()
    err = failure("get_discovery", api, **{"thesis": rfx.THESIS_ID, **args})
    assert err["code"] == "invalid_arguments", err
    assert expect in err["message"], err["message"]
    assert api.requests == [], api.requests


def test_discovery_an_unknown_segment_or_placement_is_invalid_arguments_naming_the_labels():
    err = failure("get_discovery", **DISCOVERY, segment="Segment 99")
    assert err["code"] == "invalid_arguments" and "Segment 01" in err["message"]
    err = failure("get_discovery", **DISCOVERY, section="placement", t="NOPE")
    assert err["code"] == "invalid_arguments" and "NOPE" in err["message"]
    assert failure("get_discovery", thesis="no such card")["code"] == "unknown_thesis"


# --- get_extract -------------------------------------------------------------------------------------------------------

EXTRACT = {"t": "TK01"}


def extract_requests(api: FakeApi) -> list[tuple]:
    return [r for r in api.requests if r[1].endswith("/extract")]


def test_extract_default_has_no_thesis_so_no_llm_call_and_cuts_every_excerpt_at_300():
    api = FakeApi()

    async def body(session):
        return await session.call_tool("get_extract", EXTRACT)

    result = run(api, body)
    assert not result.is_error and len(result.content[0].text) <= BUDGET
    reply = result.structured_content
    (read,) = extract_requests(api)
    assert read[1] == f"/workbench/securities/{rfx.SECURITY_ID}/extract" and read[2] == {}
    assert reply["thesis_scoped"] is False and "thesis" not in reply
    assert reply["identity"] == {
        "security_id": rfx.SECURITY_ID,
        "ticker": "TK01",
        "name": "Company 01",
    }
    facts = {f["fact_type"]: f for f in reply["facts"]}
    assert list(facts) == ["revenue_mix", "shares_outstanding", "cash_burn"]
    assert [f["tier"] for f in facts.values()] == ["human", "flag", "auto"]
    assert "estimate_source" not in facts["revenue_mix"]
    source = rfx.extract()["facts"]
    revenue = facts["revenue_mix"]
    assert revenue["passages_total"] == 3 and len(revenue["passages"]) == 2  # at most two a fact
    first = revenue["passages"][0]
    assert len(first["excerpt"]) == 300 and first["excerpt_total_chars"] == 3_004
    assert first["excerpt"] == source[0]["located_passages"][0]["excerpt"][:300]
    assert facts["cash_burn"]["passages"][1]["excerpt_total_chars"] == 700
    assert len(revenue["note"]) == 200 and revenue["note_total_chars"] > 200
    assert facts["shares_outstanding"]["value"] == 152_000_000.0
    assert facts["shares_outstanding"]["flags"] == ["dual-class"]
    assert facts["cash_burn"]["cash_usd"] == 410_000_000.0
    assert facts["cash_burn"]["quarterly_burn_usd"] == 38_000_000.0
    assert reply["empty_reason"] is None and reply["runway_empty_reason"] is None
    for f in reply["facts"]:
        for p in f["passages"]:
            assert len(p["excerpt"]) <= 300


def test_extract_with_a_thesis_passes_its_id_once_and_says_so():
    api = FakeApi()

    async def body(session):
        return await session.call_tool("get_extract", {**EXTRACT, "thesis": rfx.THESIS_ID})

    result = run(api, body)
    assert not result.is_error
    reply = result.structured_content
    (read,) = extract_requests(api)
    assert read[2] == {"thesis_id": rfx.THESIS_ID}
    assert reply["thesis_scoped"] is True and reply["thesis"] == THESIS
    revenue = next(f for f in reply["facts"] if f["fact_type"] == "revenue_mix")
    assert revenue["estimate_source"] == "llm_proposed"


def test_extract_description_states_the_edgar_and_llm_cost():
    async def body(session):
        return (await session.list_tools()).tools

    tool = next(t for t in run(None, body) if t.name == "get_extract")
    for needle in ("EDGAR", "LLM call", "up to 4", "Pass thesis only when the operator asks"):
        assert needle in tool.description, needle


def test_extract_passages_section_reads_one_facts_passages_in_full():
    reply = call("get_extract", **EXTRACT, section="passages", fact="revenue_mix")
    assert reply["fact"] == "revenue_mix" and reply["passages_total"] == 3
    source = rfx.extract()["facts"][0]["located_passages"]
    assert [p["excerpt"] for p in reply["passages"]] == [p["excerpt"] for p in source]
    assert len(reply["passages"][0]["excerpt"]) == 3_004 and reply["next_cursor"] is None
    assert reply["passages"][0]["anchor"] == source[0]["anchor"]


def test_extract_passages_page_by_passage_when_they_do_not_fit():
    payload = rfx.extract()
    payload["facts"][0]["located_passages"] = payload["facts"][0]["located_passages"][:1] * 5
    ident = {"security_id": rfx.SECURITY_ID, "ticker": "TK01", "name": "Company 01"}
    cursor, seen, pages = 0, 0, 0
    while cursor is not None:
        req = ResearchRequest(section="passages", fact="revenue_mix", cursor=cursor)
        reply = extract_view.view(payload, ident, req)
        assert size(reply) <= BUDGET
        seen += len(reply["passages"])
        cursor, pages = reply["next_cursor"], pages + 1
        assert reply["next_cursor_for"] in (None, "passages") and pages < 10
    assert seen == 5 and pages == 3  # 3,004-character excerpts: two to a page


@pytest.mark.parametrize(
    ("args", "expect"),
    [
        ({"section": "passages"}, "section=passages of `get_extract` needs fact (revenue_mix"),
        (
            {"section": "passages", "fact": "purity"},
            "fact must be one of: revenue_mix, shares_outstanding, cash_burn.",
        ),
        (
            {"fact": "cash_burn", "section": "nope"},
            "Unknown section 'nope'; `get_extract` has: passages.",
        ),
    ],
)
def test_extract_rejections_name_the_rule_before_any_request(args, expect):
    api = FakeApi()
    err = failure("get_extract", api, **{**EXTRACT, **args})
    assert err["code"] == "invalid_arguments", err
    assert expect in err["message"], err["message"]
    assert api.requests == [], api.requests


def test_extract_an_unknown_ticker_is_unknown_ticker_and_spends_nothing():
    api = FakeApi()
    err = failure("get_extract", api, t="NOPE")
    assert err["code"] == "unknown_ticker" and not extract_requests(api)
    assert failure("get_extract", t="TK01", thesis="no such card")["code"] == "unknown_thesis"


def test_extract_a_fact_the_extract_does_not_hold_is_invalid_arguments():
    ident = {"security_id": rfx.SECURITY_ID, "ticker": "TK01", "name": "Company 01"}
    payload = {"facts": [], "empty_reason": "no-annual-filing", "runway_empty_reason": None}
    reply = extract_view.view(payload, ident, ResearchRequest())
    assert reply["facts"] == [] and reply["empty_reason"] == "no-annual-filing"
    with pytest.raises(ViewError, match="holds no cash_burn fact"):
        extract_view.view(payload, ident, ResearchRequest(section="passages", fact="cash_burn"))


# --- the contracts -----------------------------------------------------------------------------------------------------


def test_the_research_contracts_are_declared_and_registered():
    from agent_tools.tool_args import CONTRACTS

    declared = research_pkg.CONTRACTS
    assert set(declared) == {"get_triage", "get_discovery", "get_extract", "get_onboarding"}
    for contract in declared.values():
        assert contract in CONTRACTS
    assert declared["get_triage"].dated and not declared["get_onboarding"].dated
    assert dict(declared["get_triage"].needs) == {"member": "t", "segment": "segment"}


# --- the views read only fields the API's wire models declare (a drift guard) -----------------------------------------


def _fields(module: str, model: str) -> set[str]:
    return set(getattr(importlib.import_module(module), model).model_fields)


def _key_checks():
    from agent_tools.views.research import discovery as ds
    from agent_tools.views.research import extract as ex
    from agent_tools.views.research import onboarding as ob
    from agent_tools.views.research import triage as tr

    schemas = "app.schemas_api"
    return [
        (tr.IDENTITY_KEYS, schemas, "ScoredMemberOut"),
        (tr.ROW_KEYS, schemas, "ScoredMemberOut"),
        (tr.METERS, schemas, "ScoredMemberOut"),
        (tr.FIGURE_KEYS, schemas, "ScoredFigureOut"),
        (tr.PROVENANCE_KEYS, schemas, "ProvenanceOut"),
        (tr.SEGMENT_KEYS, "domain.thesis", "Segment"),
        (tr.BODY_KEYS, schemas, "WorkbenchScored"),
        (ds.RUN_KEYS, schemas, "SavedRunSummary"),
        (ds.PLACEMENT_KEYS, "workbench.chain_draft", "ResolvedPlacement"),
        (ds.CANDIDATE_KEYS, "workbench.chain_draft", "SecurityCandidate"),
        (ds.SEGMENT_KEYS, "workbench.chain_draft", "ResolvedSegment"),
        (ds.REPORT_KEYS, schemas, "DraftReportOut"),
        (ds.COVERAGE_KEYS, schemas, "DraftCoverageOut"),
        (ds.BODY_KEYS, schemas, "ChainDraftOut"),
        (ds.TERM_KEYS, "domain.thesis", "TermSetEntry"),
        (ds.THESIS_KEYS, schemas, "ThesisDetail"),
        (ex.FACT_KEYS, "domain.extraction", "ExtractedFact"),
        (ex.PASSAGE_KEYS, "domain.extraction", "LocatedPassage"),
        (ex.BODY_KEYS, "domain.extraction", "ExtractionResult"),
        (ob.NAME_KEYS, schemas, "OnboardingNameOut"),
        (ob.BODY_KEYS, schemas, "OnboardingReadoutOut"),
        (ob.RUN_KEYS, schemas, "OnboardingRunOut"),
    ]


def test_every_field_a_research_view_copies_is_declared_by_the_api_wire_model():
    for keys, module, model in _key_checks():
        missing = set(keys) - _fields(module, model)
        assert not missing, f"{model}: {sorted(missing)}"


# --- purity ------------------------------------------------------------------------------------------------------------


def test_the_research_views_import_nothing_from_the_client_server_httpx_mcp_or_errors():
    root = Path(research_pkg.__file__).parent
    banned = {"httpx", "mcp", "agent_tools.client", "agent_tools.server", "agent_tools.errors"}
    files = list(root.glob("*.py"))
    assert len(files) >= 4
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                if node.level == 0:
                    names = [module]
                else:  # resolve a relative import against agent_tools.views.research
                    base = "agent_tools.views.research".split(".")
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
