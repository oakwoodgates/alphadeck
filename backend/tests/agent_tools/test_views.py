"""Shape + budget per view, on the synthetic real-size fixtures. No DB, no API, no MCP.

Each default reply must fit its tool's budget (a size regression fails here), and every cursor must walk its list
to the end with no repeat and no gap.
"""

from __future__ import annotations

import ast
import copy
import json
from pathlib import Path

import pytest

from agent_tools.budget import OutputTooLarge, TrimRule, fit, size
from agent_tools.views import call as call_view
from agent_tools.views import decisions as decisions_view
from agent_tools.views import flags as flags_view
from agent_tools.views import signals as signals_view
from agent_tools.views import theses as theses_view
from agent_tools.views import thesis as thesis_view
from agent_tools.views import ticker as ticker_view
from tests.agent_tools import fixtures as fx

THESIS = {"id": fx.uid("thesis", 0), "name": "Thesis 01 synthetic theme card"}


def thesis_members(reply):
    """``get_thesis`` members in reply order, each with its link label."""
    return [{"segment": s["label"], **m} for s in reply["segments"] for m in s["members"]]


def walk(make, key_path, ident):
    """Follow ``next_cursor`` from 0 to the end; return every item's identity in order and the page sizes.
    ``key_path`` names the list, or is a function of the reply returning it."""
    seen, sizes, cursor = [], [], 0
    for _ in range(500):
        reply = make(cursor)
        sizes.append(size(reply))
        if callable(key_path):
            node = key_path(reply)
        else:
            node = reply
            for k in key_path:
                node = node[k]
        seen.extend(ident(item) for item in node)
        if reply["next_cursor"] is None:
            return seen, sizes
        assert reply["next_cursor"] > cursor, "a cursor must move forward"
        cursor = reply["next_cursor"]
    raise AssertionError("cursor never finished")


# --- the size rule ----------------------------------------------------------------------------------------------


def test_fit_keeps_the_longest_prefix_and_points_at_the_first_dropped_index():
    reply = {"asof": "2026-11-05", "items": [{"n": i, "pad": "x" * 50} for i in range(100)]}
    out = fit(reply, 1_000, [TrimRule(("items",), start=40)])
    assert size(out) <= 1_000
    kept = len(out["items"])
    assert 0 < kept < 100
    assert out["next_cursor"] == 40 + kept and out["next_cursor_for"] == "items"
    one_more = copy.deepcopy(out)
    one_more["items"].append({"n": kept, "pad": "x" * 50})
    assert size(one_more) > 1_000  # the longest prefix: one more item would not fit


def test_fit_leaves_a_reply_within_budget_untouched():
    reply = {"items": [1, 2, 3]}
    assert fit(reply, 1_000, [TrimRule(("items",))]) == {
        "items": [1, 2, 3],
        "next_cursor": None,
        "next_cursor_for": None,
    }


def test_fit_raises_output_too_large_naming_the_parameter():
    reply = {"blob": "x" * 5_000, "items": [1]}
    with pytest.raises(OutputTooLarge) as raised:
        fit(reply, 1_000, [TrimRule(("items",), min_keep=1, hint="tickers")])
    assert raised.value.hint == "tickers" and raised.value.budget == 1_000


def test_views_import_nothing_from_the_client_server_httpx_or_mcp():
    root = Path(thesis_view.__file__).parent
    banned = {"httpx", "mcp", "agent_tools.client", "agent_tools.server", "agent_tools.errors"}
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                rel = "agent_tools." if node.level else ""
                names = [rel + (node.module or "")]
                if node.level and node.module is None:
                    names = [f"agent_tools.{a.name}" for a in node.names]
                elif node.level == 2:
                    names = [f"agent_tools.{node.module}"]
            else:
                continue
            for name in names:
                assert not any(
                    name == b or name.startswith(b + ".") for b in banned
                ), f"{path.name} imports {name}"


# --- list_theses ----------------------------------------------------------------------------------------------


def test_list_theses_queue_is_the_list_order_filtered_to_armed_and_current():
    theses = fx.theses_list()
    reply = theses_view.view_list_theses(theses, asof=fx.ASOF, clock=fx.CLOCK)
    assert reply["queue"] == fx.queue_order()  # never sorted, never stale, never archived
    stale_armed = fx.uid("thesis", 5)
    assert stale_armed not in reply["queue"]
    assert reply["asof"] == fx.ASOF and reply["source"] == "record"
    assert reply["counts"] == {
        "cards": 68,
        "armed": 38,
        "warming": 27,
        "incubating": 1,
        "managing": 0,
        "no_record": 2,
        "stale": 2,
    }
    assert size(reply) <= theses_view.BUDGET_QUEUE


def _all_cards(theses, **kwargs):
    """Every card across the pages, keyed by id (and the first page)."""
    pages, cursor = [], 0
    while cursor is not None:
        pages.append(
            theses_view.view_list_theses(
                theses, asof=fx.ASOF, clock=fx.CLOCK, cursor=cursor, **kwargs
            )
        )
        cursor = pages[-1]["next_cursor"]
    return {c["id"]: c for p in pages for c in p["cards"]}, pages[0]


def test_list_theses_queue_cards_carry_the_full_record_and_the_rest_are_brief():
    theses = fx.theses_list()
    by_id, page_one = _all_cards(theses)
    queue = page_one["queue"]
    first = by_id[queue[0]]
    source = next(t for t in theses if t["id"] == first["id"])["call"]
    assert first["verdict"] == source["verdict"]
    assert first["setup_strength"] == source["confidence"]
    assert first["grades"]["entry"] == source["entry_grade"]
    assert first["armed_ticker"] == source["armed_ticker"]
    # queue-first: page one holds EVERY queue card, in queue order, within the 28K page
    shown = [c["id"] for c in page_one["cards"]]
    assert shown[: len(queue)] == queue and len(queue) == 37
    assert size(page_one) <= theses_view.BUDGET_QUEUE == 28_000
    assert all(len(c["triggers"]) == 1 for c in page_one["cards"][: len(queue)])
    for qid in queue:
        card = by_id[qid]
        assert card["trigger_count"] == 8 and len(card["triggers"]) == 1
        assert card["triggers"] == [
            {k: t[k] for k in ("ticker", "kind", "label", "event_date")}
            for t in next(x for x in theses if x["id"] == qid)["call"]["triggers"][:1]
        ]
        for absent in ("ticker", "basket_size", "archived", "stale"):  # lean: null / false omitted
            assert absent not in card
    assert by_id[fx.uid("thesis", 20)]["reconstructed"] is True
    stale = by_id[fx.uid("thesis", 5)]  # armed but stale: brief, never queued
    assert set(stale) == {"id", "name", "state", "record_asof"}
    assert by_id[fx.uid("thesis", 10)]["state"] is None  # no record
    assert list(by_id)[-2:] == [fx.uid("thesis", 66), fx.uid("thesis", 67)]  # archived last


def test_list_theses_names_are_cut_only_past_120_characters():
    theses = fx.theses_list()
    theses[0] = {**theses[0], "name": "N" * 119}
    theses[1] = {**theses[1], "name": "M" * 130}
    by_id, _ = _all_cards(theses)
    assert by_id[theses[0]["id"]]["name"] == "N" * 119
    assert "name_total_chars" not in by_id[theses[0]["id"]]
    assert by_id[theses[1]["id"]]["name"] == "M" * 120
    assert by_id[theses[1]["id"]]["name_total_chars"] == 130


def test_list_theses_queue_mode_cursor_walks_every_card_in_list_order():
    theses = fx.theses_list()

    def make(cursor):
        return theses_view.view_list_theses(theses, asof=fx.ASOF, clock=fx.CLOCK, cursor=cursor)

    seen, sizes = walk(make, ["cards"], lambda c: c["id"])
    assert seen == fx.queue_first_order()
    assert max(sizes) <= theses_view.BUDGET_QUEUE
    # every page repeats the whole queue (ids), so the first page alone names every queued card
    assert make(0)["queue"] == make(30)["queue"] == fx.queue_order()


def test_list_theses_full_detail_fits_and_its_cursor_walks_every_card():
    theses = fx.theses_list()

    def make(cursor):
        return theses_view.view_list_theses(
            theses, asof=fx.ASOF, clock=fx.CLOCK, detail="full", cursor=cursor
        )

    seen, sizes = walk(make, ["cards"], lambda c: c["id"])
    assert seen == fx.queue_first_order()
    assert max(sizes) <= theses_view.BUDGET_FULL
    by_id, _ = _all_cards(theses, detail="full")
    stale = by_id[fx.uid("thesis", 5)]
    assert stale["stale"] is True and stale["state"] == "armed"
    assert all("basket_size" in c for c in by_id.values())  # kept with detail=full


def test_list_theses_state_filter_lists_only_that_state():
    reply = theses_view.view_list_theses(
        fx.theses_list(), asof=fx.ASOF, clock=fx.CLOCK, detail="full", state="incubating"
    )
    assert [c["state"] for c in reply["cards"]] == ["incubating"]
    assert reply["counts"]["armed"] == 38  # the counts are always over every card


# --- get_thesis -----------------------------------------------------------------------------------------------


def test_get_thesis_summary_fits_and_groups_members_in_link_order_with_silent_links_last():
    detail = fx.thesis_detail()
    reply = thesis_view.view_thesis(detail)
    assert size(reply) <= thesis_view.BUDGET_SUMMARY
    labels = [s["label"] for s in reply["segments"]]
    assert labels == fx.REAL_LINKS + ["Discovered"] + fx.SILENT
    assert [s["silent"] for s in reply["segments"]] == [False] * 6 + [True] * 3
    assert reply["next_cursor"] is None  # all 28 members fit the summary, grouped under their links
    assert sum(len(s["members"]) for s in reply["segments"]) == reply["members_total"] == 28
    assert [s["member_count"] for s in reply["segments"]] == [
        len(s["members"]) for s in reply["segments"]
    ]
    by_link = {s["label"]: [m["ticker"] for m in s["members"]] for s in reply["segments"]}
    for m in detail["basket"]:
        assert m["ticker"] in by_link[m["segment"]]
    assert reply["narrative"]["total_chars"] == 1_200 and len(reply["narrative"]["text"]) == 600
    assert reply["term_set"] == {
        "signal": [f"synthetic term {k:02d}" for k in range(6)],
        "broad_count": 4,
    }
    assert reply["asof"] is None and reply["source"] == "live"


def test_get_thesis_names_context_role_and_never_a_bare_role():
    reply = thesis_view.view_thesis(fx.thesis_detail(), mode="full")
    members = thesis_members(reply)
    for m in members:
        assert "role" not in m and "context_role" in m and "thesis_fit" in m
    assert any(m["context_role"] == "payer" for m in members)


def test_get_thesis_cursor_walks_every_member_in_both_modes():
    detail = fx.thesis_detail()
    for mode, budget in (
        ("summary", thesis_view.BUDGET_SUMMARY),
        ("full", thesis_view.BUDGET_FULL),
    ):
        seen, sizes = walk(
            lambda c, mode=mode: thesis_view.view_thesis(detail, mode=mode, cursor=c),
            thesis_members,
            lambda m: m["ticker"],
        )
        assert sorted(seen) == sorted(m["ticker"] for m in detail["basket"])
        assert len(seen) == len(set(seen)) == 28
        assert max(sizes) <= budget


# --- get_call -------------------------------------------------------------------------------------------------


def test_get_call_default_fits_and_copies_the_decision_fields():
    call = fx.call_payload()
    reply = call_view.view_call(call, asof=fx.ASOF, thesis=THESIS)
    assert size(reply) <= call_view.BUDGET
    assert reply["asof"] == fx.ASOF and reply["source"] == "live"
    assert (reply["state"], reply["verdict"]) == (call["state"], call["verdict"])
    assert reply["setup_strength"] == call["confidence"]
    assert reply["keys"]["conviction"]["label"] == call["key_conviction"]["label"]
    assert reply["armed"] == {"ticker": "TK00", "security_id": call["armed_security_id"]}
    assert reply["risk_signals"] == {
        "count": 25,
        "by_kind": {"breakdown": 12, "corporate_risk": 5, "dilution_risk": 5, "insider_sell": 3},
    }
    assert reply["counter_case"]["total_chars"] == 5_000
    assert reply["watch_members"] == ["TK27"]
    assert len(reply["triggers_fired"]) == 8 and reply["next_cursor"] is None
    for t in reply["triggers_fired"]:
        assert t["sources"] and all("detail" not in s for s in t["sources"])


def test_get_call_brief_is_the_decision_alone_within_its_budget():
    call = fx.call_payload()
    reply = call_view.view_call(call, asof=fx.ASOF, thesis=THESIS, detail="brief")
    assert size(reply) <= call_view.BRIEF_BUDGET == 2_500
    assert reply["detail"] == "brief" and reply["asof"] == fx.ASOF and reply["source"] == "live"
    assert (reply["state"], reply["verdict"]) == (call["state"], call["verdict"])
    assert reply["keys"] == {"conviction": {"turned": True}, "confirmation": {"turned": True}}
    assert reply["counter_case"] == {"total_chars": 5_000}
    assert reply["catalyst_surface_total"] == 14
    assert reply["risk_signals"]["count"] == 25
    assert reply["missing"] == call["missing"]
    shown = reply["triggers_fired"]
    assert reply["triggers_fired_total"] == 8
    for got, want in zip(shown, call["triggers_fired"]):
        assert got == {
            "ticker": want["ticker"],
            "kind": want["kind"],
            "label": want["label"],
            "grade": want["grade"],
            "event_date": want["event_date"],
            "source_count": len(want["sources"]),
        }
    if reply["next_cursor"] is not None:  # the cursor continues the brief trigger list
        rest = call_view.view_call(
            call, asof=fx.ASOF, thesis=THESIS, detail="brief", cursor=reply["next_cursor"]
        )
        assert len(shown) + len(rest["triggers_fired"]) == 8
    for absent in ("armed_members", "watch_members", "expression", "catalyst_surface"):
        assert absent not in reply


@pytest.mark.parametrize(
    "section,key,total",
    [
        ("triggers", "triggers_fired", 8),
        ("risk_signals", "risk_signals", 25),
        ("members", "members", 8),
    ],
)
def test_get_call_sections_walk_to_the_end_within_budget(section, key, total):
    call = fx.call_payload()
    seen, sizes = walk(
        lambda c: call_view.view_call(call, asof=fx.ASOF, thesis=THESIS, section=section, cursor=c),
        [key],
        lambda item: json.dumps(item, sort_keys=True),
    )
    assert len(seen) == len(set(seen)) == total
    assert max(sizes) <= call_view.SECTION_BUDGET
    if section != "members":
        assert all(
            s.get("detail") is not None for item in seen for s in json.loads(item)["sources"]
        )


def test_get_call_risk_signals_pages_ten_at_a_time():
    reply = call_view.view_call(
        fx.call_payload(), asof=fx.ASOF, thesis=THESIS, section="risk_signals"
    )
    assert len(reply["risk_signals"]) == 10 and reply["next_cursor"] == 10


@pytest.mark.parametrize(
    "section,key", [("counter_case", "counter_case"), ("expression", "expression")]
)
def test_get_call_prose_sections_return_the_whole_string(section, key):
    call = fx.call_payload()
    reply = call_view.view_call(call, asof=fx.ASOF, thesis=THESIS, section=section)
    assert reply[key] == call[key]
    assert size(reply) <= call_view.SECTION_BUDGET


# --- get_data_flags -------------------------------------------------------------------------------------------


def test_get_data_flags_per_thesis_fits_and_counts_the_clean_members():
    reply = flags_view.view_data_flags(fx.data_flags_one(), asof=fx.ASOF, thesis=THESIS)
    assert size(reply) <= flags_view.BUDGET
    assert reply["members_total"] == 28 and reply["members_clean"] == 25
    assert [m["ticker"] for m in reply["members"]] == ["TK03", "TK11", "TK19"]
    flag = reply["members"][0]["flags"][0]
    assert set(flag) == {
        "kind",
        "label",
        "level",
        "reason",
        "clears",
        "since",
        "affects",
        "call_affected",
    }


def test_get_data_flags_cross_card_walks_every_member_with_its_cards():
    payload = fx.data_flags_all()
    seen, sizes = walk(
        lambda c: flags_view.view_data_flags(
            payload, asof=fx.ASOF, thesis=None, compute_s=15.6, cursor=c
        ),
        ["members"],
        lambda m: m["ticker"] + m["cards"][0]["id"],
    )
    assert len(seen) == len(set(seen)) == 200
    assert max(sizes) <= flags_view.BUDGET
    first = flags_view.view_data_flags(payload, asof=fx.ASOF, thesis=None, compute_s=15.6)
    assert first["compute_s"] == 15.6 and first["scope"] == "all_cards"
    assert len(first["members"][0]["cards"]) == 2


# --- get_display_signals ---------------------------------------------------------------------------------------


def test_get_display_signals_keeps_headlines_and_drops_series_events_basis():
    payload = fx.display_signals()
    reply = signals_view.view_display_signals(payload, asof=fx.ASOF, thesis=THESIS)
    assert size(reply) <= signals_view.BUDGET
    text = json.dumps(reply)
    for dropped in ('"series"', '"events"', '"basis"', '"metrics"'):
        assert dropped not in text
    assert reply["breadth"] == {"kind": "theme_breadth", "key": "above", "label": "Above 50d"}
    member = reply["members"][0]
    assert set(member["signals"]) == set(fx.SIGNAL_KINDS)


def test_get_display_signals_cursor_walks_every_member():
    payload = fx.display_signals()
    seen, sizes = walk(
        lambda c: signals_view.view_display_signals(payload, asof=fx.ASOF, thesis=THESIS, cursor=c),
        ["members"],
        lambda m: m["ticker"],
    )
    assert seen == [fx.ticker(m) for m in range(28)]
    assert max(sizes) <= signals_view.BUDGET


def test_get_display_signals_with_tickers_adds_metrics_and_names_the_missing():
    reply = signals_view.view_display_signals(
        fx.display_signals(), asof=fx.ASOF, thesis=THESIS, tickers=["tk01", "TK02", "NOPE"]
    )
    assert [m["ticker"] for m in reply["members"]] == ["TK01", "TK02"]
    assert reply["tickers_not_found"] == ["NOPE"]
    assert all("metrics" in s for s in reply["members"][0]["signals"].values())
    assert size(reply) <= signals_view.BUDGET


def test_get_display_signals_eight_tickers_fit():
    tickers = [fx.ticker(m) for m in range(8)]
    reply = signals_view.view_display_signals(
        fx.display_signals(), asof=fx.ASOF, thesis=THESIS, tickers=tickers
    )
    assert size(reply) <= signals_view.BUDGET
    assert reply["next_cursor"] is None or reply["next_cursor_for"] == "members"


# --- get_decisions --------------------------------------------------------------------------------------------


def test_get_decisions_passes_entries_through_and_walks_them():
    payload = fx.decisions()
    first = decisions_view.view_decisions(payload, thesis=THESIS)
    assert first["decisions"][0] == payload[0]  # as the API gives them
    seen, sizes = walk(
        lambda c: decisions_view.view_decisions(payload, thesis=THESIS, cursor=c),
        ["decisions"],
        lambda d: d["id"],
    )
    assert seen == [d["id"] for d in payload]
    assert max(sizes) <= decisions_view.BUDGET


# --- lookup_ticker --------------------------------------------------------------------------------------------


def test_lookup_ticker_default_fits_and_summarizes_the_filings():
    page = fx.ticker_lookup()
    reply = ticker_view.view_ticker(page, resolved=fx.ticker_resolve(), asof=fx.ASOF)
    assert size(reply) <= ticker_view.BUDGET
    assert reply["cache"] == "computed" and reply["compute_ms"] == 2400
    assert reply["identity"]["ticker"] == "TK01"
    assert reply["cards"]["rows"][0]["segments"] == [fx.REAL_LINKS[0]]
    assert len(reply["filings"]["newest"]) == 5
    assert sum(reply["filings"]["count_by_form"].values()) == 94
    assert len(reply["card_catalysts"]) == 5 and reply["card_catalysts_upcoming"] == 12
    assert "sparkline" not in reply["tape"]
    for e in reply["triggers"]["events"]:
        assert all("detail" not in s for s in e["sources"])


def test_lookup_ticker_default_cursor_walks_every_trigger_event():
    page = fx.ticker_lookup()
    seen, sizes = walk(
        lambda c: ticker_view.view_ticker(
            page, resolved=fx.ticker_resolve(), asof=fx.ASOF, cursor=c
        ),
        ["triggers", "events"],
        lambda e: e["recorded_on"],
    )
    assert len(seen) == len(set(seen)) == 9
    assert max(sizes) <= ticker_view.BUDGET


def test_lookup_ticker_filings_section_walks_every_row_newest_first():
    page = fx.ticker_lookup()
    seen, sizes = walk(
        lambda c: ticker_view.view_ticker(
            page, resolved=fx.ticker_resolve(), asof=fx.ASOF, section="filings", cursor=c
        ),
        ["filings"],
        lambda r: r["accession"],
    )
    expected = [r["accession"] for day in page["filings"]["days"] for r in day["rows"]]
    assert seen == expected
    assert max(sizes) <= ticker_view.BUDGET
