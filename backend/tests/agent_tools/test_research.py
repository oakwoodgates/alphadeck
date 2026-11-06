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


# --- the contracts -----------------------------------------------------------------------------------------------------


def test_the_research_contracts_are_declared_and_registered():
    from agent_tools.tool_args import CONTRACTS

    declared = research_pkg.CONTRACTS
    assert {"get_triage", "get_onboarding"} <= set(declared)
    for contract in declared.values():
        assert contract in CONTRACTS
    assert declared["get_triage"].dated and not declared["get_onboarding"].dated
    assert dict(declared["get_triage"].needs) == {"member": "t", "segment": "segment"}


# --- the views read only fields the API's wire models declare (a drift guard) -----------------------------------------


def _fields(module: str, model: str) -> set[str]:
    return set(getattr(importlib.import_module(module), model).model_fields)


def _key_checks():
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
