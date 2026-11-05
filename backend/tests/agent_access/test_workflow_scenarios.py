"""Scripted official-SDK acceptance for recipes and their read-only tool sequences.

The client makes each decision explicitly. This checks discoverability and available
evidence, without testing model interpretation, autonomous reasoning, or live data.
"""

import asyncio
import json
import os
import re
import sys
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")
pytest.importorskip("playwright")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from test_browser import populated_cockpit, serve  # noqa: E402

from agent_access.references import context_hash  # noqa: E402

HERE = Path(__file__).resolve().parent
DIST = HERE.parents[2] / "frontend/dist"
# board-review 1.1.0, step 4: the call fields one batch read takes per shortlisted item.
CALL_FIELDS = (
    "state",
    "verdict",
    "conviction_grade",
    "confirmation_grade",
    "entry_grade",
    "armed_members",
    "missing",
    "counter_case",
    "risk_signals",
    "arm_until",
    "exit_by",
)


@asynccontextmanager
async def session(factory, base=None):
    backend = HERE.parents[1]
    script = (
        f"import asyncio; from workflow_synthetic import {factory}; "
        f"from agent_access.server import run; asyncio.run(run({factory}()))"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", script],
        cwd=backend,
        env={
            "PYTHONPATH": os.pathsep.join((str(HERE), str(backend))),
            **({"MCP_TEST_UI_BASE": base} if base else {}),
        },
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=60) as client:
            await client.initialize()
            yield client


async def call(client, tool, **arguments):
    reply = await client.call_tool(tool, arguments)
    assert not reply.is_error, reply.structured_content
    return reply.structured_content["result"]


async def error(client, code, tool, **arguments):
    reply = await client.call_tool(tool, arguments)
    assert reply.is_error and reply.structured_content["error"]["code"] == code


async def recipe(client, name, arguments, dashboards):
    result = await call(client, "get_workflow", name=name, arguments=arguments)
    assert result["ready"] and set(result["workflow"]["required_dashboards"]) == dashboards
    assert result["instructions"].strip()
    return result["arguments"]


async def targets(client, capture):
    found = list(capture["targets"])
    offset = capture["next_target_offset"]
    while offset is not None:
        page = await call(
            client, "inspect_dashboard_item", snapshot_id=capture["snapshot_id"], offset=offset
        )
        assert page["offset"] == offset and page["targets"]
        found.extend(page["targets"])
        offset = page["next_offset"]
    assert len({target["id"] for target in found}) == len(found) == capture["target_total"]
    return found


def follow(parent, target):
    return {
        "from_snapshot_id": parent["snapshot_id"],
        "target_id": target["id"],
        "link_id": target["links"][0]["id"],
        "freshness": "fresh",
    }


async def read_source(client, capture, *, data_limit=12000, **locator):
    parts, offset = [], 0
    while offset is not None:
        page = await call(
            client,
            "inspect_dashboard_item",
            snapshot_id=capture["snapshot_id"],
            data_offset=offset,
            data_limit=data_limit,
            **locator,
        )
        assert page["snapshot_id"] == capture["snapshot_id"]
        source = page["source_data"]
        assert source["offset"] == offset
        parts.append(source["text"])
        offset = source["next_offset"]
    serialized = "".join(parts)
    assert len(serialized) == source["total_characters"]
    return json.loads(serialized), source


async def response_body(client, capture, path, *, data_limit=12000):
    response = next(item for item in capture["source_responses"] if item["path"] == path)
    return await read_source(
        client, capture, data_limit=data_limit, source_response_index=response["index"]
    )


def test_recipe_discovery_and_snapshot_recovery_over_stdio():
    async def run():
        async with session("recovery_service") as client:
            names = {"board-review", "explain-call", "compare-rotation"}
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
            assert {
                "list_workflows",
                "get_workflow",
                "list_dashboards",
                "describe_dashboard",
                "read_dashboard",
                "inspect_dashboard_item",
                "render_dashboard",
            } <= tools.keys()
            discovered = await call(client, "list_workflows", limit=2)
            tail = await call(client, "list_workflows", offset=discovered["next_offset"])
            descriptors = {
                entry["id"]: entry for entry in discovered["workflows"] + tail["workflows"]
            }
            assert descriptors.keys() == names and tail["next_offset"] is None
            prompts = {prompt.name: prompt for prompt in (await client.list_prompts()).prompts}
            assert prompts.keys() == names
            for name, descriptor in descriptors.items():
                template = await call(client, "get_workflow", name=name)
                assert template["workflow"] == descriptor
                assert not template["ready"] and template["arguments"] is None
                assert {arg.name for arg in prompts[name].arguments if arg.required} == set(
                    descriptor["input_schema"].get("required", [])
                )
                assert {arg.name for arg in prompts[name].arguments} == set(
                    descriptor["input_schema"]["properties"]
                )
            board = await call(client, "get_workflow", name="board-review", arguments={})
            assert board["ready"] and board["arguments"] == {"max_items": 5}
            prompt = await client.get_prompt("board-review")
            assert len(prompt.messages) == 1 and prompt.messages[0].role == "user"
            assert prompt.messages[0].content.text == board["instructions"]

            arguments = await recipe(
                client, "explain-call", {"thesis": "Synthetic subject"}, {"board", "cockpit"}
            )
            first = await call(client, "read_dashboard", dashboard="board", target_limit=1)
            assert first["next_target_offset"] is not None
            assert arguments["thesis"] == "Synthetic subject"
            target = next(t for t in await targets(client, first) if t["links"])
            assert target not in first["targets"]
            await error(client, "context_changed", "read_dashboard", **follow(first, target))

            restarted = await call(client, "read_dashboard", dashboard="board")
            assert restarted["snapshot_id"] != first["snapshot_id"]
            await error(client, "snapshot_expired", "read_dashboard", **follow(first, target))
            advertised = next(t for t in restarted["targets"] if t["links"])
            child = await call(client, "read_dashboard", **follow(restarted, advertised))
            assert child["origin"]["parent_snapshot_id"] == restarted["snapshot_id"]

    asyncio.run(run())


@pytest.fixture
def browser_base():
    if os.environ.get("ALPHADECK_MCP_BROWSER_TESTS") != "1":
        pytest.skip("Opt-in Chromium integration")
    if not (DIST / "mcp-render.html").exists():
        pytest.skip("Build frontend first")
    with serve(DIST) as base:
        yield base


def test_board_review_and_explain_call_use_rendered_queue_and_frozen_evidence(browser_base):
    from app.routers.dashboards.capture_adapters.cockpit import LIMITATIONS as COCKPIT_LIMITATIONS

    async def run():
        async with session("browser_service", browser_base) as client:
            review = await recipe(
                client, "board-review", {"asof": "2026-10-30", "max_items": 2}, {"board"}
            )
            board_settings = {"dq_open": True, "archived_open": False}
            descriptor = await call(client, "describe_dashboard", dashboard="board")
            assert (
                board_settings.keys()
                <= descriptor["dashboard"]["settings_schema"]["properties"].keys()
            )
            board = await call(
                client,
                "read_dashboard",
                dashboard="board",
                settings=board_settings,
                asof=review["asof"],
                target_limit=50,
            )
            assert board["settings"] == board_settings and board["asof"] == review["asof"]
            # board-review 1.1.0, step 2: the queue's length is the rendered Armed column count.
            armed = int(re.search(r"(?i)armed\s+act now\s+(\d+)", board["observation"]["text"])[1])
            listed, offset = list(board["targets"]), board["next_target_offset"]
            while len(listed) < armed and offset is not None:
                page = await call(
                    client,
                    "inspect_dashboard_item",
                    snapshot_id=board["snapshot_id"],
                    offset=offset,
                    limit=min(50, armed - len(listed)),
                )
                listed.extend(page["targets"])
                offset = page["next_offset"]
            queue = listed[:armed]
            # Step 3: order from the thesis_summary labels; the expected queue keeps /theses order.
            order = [
                int(re.fullmatch(r"theses\[(\d+)\]", ref["label"])[1])
                for target in queue
                for ref in target["source_refs"]
                if ref["category"] == "thesis_summary"
            ]
            assert order == [0, 2, 3]  # Zebra, Example thesis, Alpha; warming 1 and archived 4 out
            queue_names = ["Synthetic Zebra", "Example thesis", "Synthetic Alpha"]
            assert all(
                name in target["text"] and "STARTER entry" in target["text"]
                for name, target in zip(queue_names, queue, strict=True)
            )
            # Step 4: one batch read per shortlisted item.
            selected = queue[: review["max_items"]]
            assert len(selected) == 2
            assert all(target["visible"] and "CORE" in target["text"] for target in selected)
            fixture_call = populated_cockpit()["responses"][2]["body"]
            flags = []
            for target in selected:
                wanted = [
                    ref["id"]
                    for ref in target["source_refs"]
                    if (ref["category"] == "call_result" and ref["label"] in CALL_FIELDS)
                    or ref["category"] == "data_flag"
                ]
                batch = await call(
                    client,
                    "inspect_dashboard_item",
                    snapshot_id=board["snapshot_id"],
                    target_id=target["id"],
                    source_ref_ids=wanted,
                )
                items = batch["source_data_items"]
                assert [item["reference_id"] for item in items] == wanted
                fields = {
                    item["label"]: json.loads(item["text"])
                    for item in items
                    if item["category"] == "call_result"
                }
                assert fields.keys() == {field for field in CALL_FIELDS if field in fixture_call}
                assert fields["state"] == "armed"  # queue membership
                assert fields["verdict"] == fixture_call["verdict"]
                assert fields["missing"] == fixture_call["missing"]
                flags += [
                    json.loads(item["text"]) for item in items if item["category"] == "data_flag"
                ]
            # The flag entry is held by Example thesis (the second queue item) and read through it.
            (member,) = flags
            (flag,) = member["flags"]
            assert flag["columns"] == ["ret_7d"] and flag["reason"] == "Synthetic price-series gap"
            assert flag["call_affected"] is False
            assert any(
                flag["label"] in (item.get("title") or "")
                for item in board["observation"]["annotations"]
            )
            assert "Synthetic warming" in board["observation"]["text"]
            assert "Synthetic archived" not in board["observation"]["text"]

            explanation = await recipe(
                client,
                "explain-call",
                {"thesis": "Example thesis", "asof": board["asof"]},
                {"board", "cockpit"},
            )
            board_targets = await targets(client, board)
            card = next(
                t for t in board_targets if t["label"] == explanation["thesis"] and t["links"]
            )
            # explain-call 1.1.0, step 2: confirm the identity from the card's /theses row.
            summary = next(r for r in card["source_refs"] if r["category"] == "thesis_summary")
            confirmed = await call(
                client,
                "inspect_dashboard_item",
                snapshot_id=board["snapshot_id"],
                target_id=card["id"],
                source_ref_ids=[summary["id"]],
            )
            (row_item,) = confirmed["source_data_items"]
            row = json.loads(row_item["text"])
            assert (row["name"], row["archived"]) == ("Example thesis", False)
            child = await call(client, "read_dashboard", **follow(board, card))
            assert child["origin"]["parent_snapshot_id"] == board["snapshot_id"]
            assert child["origin"]["consistency"] == "independent_capture"
            # The temporal context matches; only the per-surface limitations differ, as on a
            # real deployment: the Cockpit adds its adapter's notes to the Board's.
            assert child["asof"] == board["asof"]
            assert context_hash(child) == context_hash(board)
            assert child["context"]["limitations"] == board["context"]["limitations"] + list(
                COCKPIT_LIMITATIONS
            )
            fixture = populated_cockpit()["responses"][2]["body"]
            expected = fixture["triggers_fired"][0]["sources"][0]
            cockpit_targets = await targets(client, child)
            call_target, call_ref = next(
                (target, reference)
                for target in cockpit_targets
                for reference in target["source_refs"]
                if reference["category"] == "call_result"
            )
            # Step 4: outline the whole-call reference, then read single fields by pointer.
            outline = await call(
                client,
                "inspect_dashboard_item",
                snapshot_id=child["snapshot_id"],
                target_id=call_target["id"],
                source_ref_id=call_ref["id"],
                outline=True,
            )
            shape = outline["source_data"]
            assert {"verdict", "missing"} <= {entry["key"] for entry in shape["entries"]}
            stored = {}
            for field in ("verdict", "missing"):
                value = await call(
                    client,
                    "inspect_dashboard_item",
                    snapshot_id=child["snapshot_id"],
                    source_response_index=shape["response_index"],
                    pointer="/" + field,
                )
                stored[field] = json.loads(value["source_data"]["text"])
            assert stored["verdict"] == "starter_entry"
            assert stored["missing"] == ["Synthetic supporting filing unavailable"]
            evidence_target, reference = next(
                (target, reference)
                for target in cockpit_targets
                for reference in target["source_refs"]
                if target["visible"]
                and reference["category"] == "call_input"
                and expected["ref"] in target["text"]
            )
            batch = await call(
                client,
                "inspect_dashboard_item",
                snapshot_id=child["snapshot_id"],
                target_id=evidence_target["id"],
                source_ref_ids=[ref["id"] for ref in evidence_target["source_refs"]],
            )
            item = next(
                item
                for item in batch["source_data_items"]
                if item["reference_id"] == reference["id"]
            )
            assert item["category"] == "call_input" and json.loads(item["text"]) == expected
            # A cut value continues through a single reference read with data_offset.
            data, source = await read_source(
                client,
                child,
                target_id=evidence_target["id"],
                source_ref_id=reference["id"],
                data_limit=32,
            )
            assert source["category"] == "call_input" and source["freshness"] == "frozen"
            assert source["offset"] > 0 and data == expected
            evidence_args = {
                "snapshot_id": child["snapshot_id"],
                "target_id": evidence_target["id"],
            }
            await error(
                client,
                "unknown_reference",
                "inspect_dashboard_item",
                **{**evidence_args, "source_ref_id": "absent-synthetic-evidence"},
            )
            await error(
                client,
                "unknown_reference",
                "inspect_dashboard_item",
                **{**evidence_args, "source_ref_ids": [reference["id"], "absent-synthetic"]},
            )

    asyncio.run(run())


def test_compare_rotation_uses_comparable_displayed_medians(browser_base):
    async def run():
        async with session("browser_service", browser_base) as client:
            arguments = await recipe(
                client,
                "compare-rotation",
                {"start_date": "2026-09-10", "end_date": "2026-10-30"},
                {"rotation-map"},
            )
            assert arguments["group_by"] == "card"
            settings = {
                "group_by": arguments["group_by"],
                "include_silent": False,
                "vs_spy": False,
                "flagged_open": True,
                "sort_col": "ret_7d",
                "sort_dir": "desc",
                "open_groups": [],
            }
            start, end = [
                await call(
                    client, "read_dashboard", dashboard="rotation-map", asof=day, settings=settings
                )
                for day in (arguments["start_date"], arguments["end_date"])
            ]
            assert start["snapshot_id"] != end["snapshot_id"]
            assert (start["asof"], end["asof"]) == (arguments["start_date"], arguments["end_date"])
            assert start["settings"] == end["settings"]
            assert {key: start["settings"][key] for key in settings} == settings
            payloads = []
            for capture in (start, end):
                payload, source = await response_body(
                    client, capture, "/dashboards/rotation-map", data_limit=600
                )
                assert source["offset"] > 0
                assert source["query"] == {
                    "asof": capture["asof"],
                    "group_by": "card",
                    "include_silent": "false",
                }
                payloads.append(payload)
            earlier, later = payloads
            assert (earlier["roster_fallback_cards"], later["roster_fallback_cards"]) == (1, 0)
            before, after = ({group["key"]: group for group in p["groups"]} for p in payloads)
            pair_key, thin_key, old_key, new_key = (str(UUID(int=n)) for n in (101, 102, 103, 104))
            assert before.keys() - after.keys() == {old_key}
            assert after.keys() - before.keys() == {new_key}
            assert before[pair_key]["thesis_name"] != after[pair_key]["thesis_name"]
            before_members, after_members = (
                {row["security_id"] for row in groups[pair_key]["members"]}
                for groups in (before, after)
            )
            assert before_members - after_members == {str(UUID(int=4))}
            assert after_members - before_members == {str(UUID(int=5))}

            def week(group):
                return next(row for row in group["windows"] if row["key"] == "ret_7d")

            paired = [week(groups[pair_key]) for groups in (before, after)]
            assert [(w["up"], w["n"]) for w in paired] == [(2, 4), (3, 4)]
            change_pp = Decimal(str(paired[1]["median"])) - Decimal(str(paired[0]["median"]))
            assert change_pp == Decimal("3.5")
            thin = week(before[thin_key])
            assert thin["median"] is None and (thin["up"], thin["n"]) == (1, 2)

            def cell(result, label):
                (table,) = result["observation"]["tables"]
                index = next(
                    i for i, title in enumerate(table["headers"]) if "7d median" in title.lower()
                )
                row = next(row for row in table["rows"] if label in row[0])
                return row[index]

            begin, finish = (
                cell(capture, groups[pair_key]["thesis_name"])
                for capture, groups in ((start, before), (end, after))
            )
            assert "2/4 up" in begin and "3/4 up" in finish

            def median(value):
                return Decimal(value.split("%")[0].replace("−", "-"))

            assert median(finish) - median(begin) == change_pp
            assert "—" in cell(start, "Synthetic thin") and "1/2 up" in cell(
                start, "Synthetic thin"
            )
            assert "%" in cell(end, "Synthetic thin")
            assert "roster stands in for 1 card" in start["observation"]["text"]
            assert "roster stands in" not in end["observation"]["text"]
            assert "SYNFLAG" in start["observation"]["text"]

    asyncio.run(run())
