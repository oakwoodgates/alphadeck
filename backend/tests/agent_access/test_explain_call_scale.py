"""explain-call 1.2.0 at REAL scale, through the real MCP server over stdio.

The Board scale fixture (`board_scale`) plus a Cockpit at the measured dev scale (`cockpit_scale`:
504 targets, a frozen observation of about 391K characters, one target of about 35K). A scripted
official-SDK client follows the recipe's path: read the Board, find the card with `match`, confirm it
with ONE batch read of its `thesis_summary` reference, follow its "Open Cockpit" link (a fresh
capture), map the Cockpit with `index`, open the call target and the largest target in full, outline
and read the call by pointer, and read frozen parts with `view_pointer`. EVERY reply must stay under
the reply ceiling, at the default (40,000) and at 20,000. Synthetic stand-ins for the capture and the
browser; the service, server, adapters' references and recipe are real.
"""

import asyncio
import functools
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")

import board_scale  # noqa: E402
import cockpit_scale  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

HERE = Path(__file__).resolve().parent
CEILINGS = (40_000, 20_000)


def subject() -> dict:
    """An armed card of the Board fixture: its /theses row and its call."""
    data = board_scale.envelope()
    paths = [item["path"] for item in data["responses"]]
    rows = data["responses"][paths.index("/theses")]["body"]
    calls = {
        item["body"]["thesis_id"]: item["body"]
        for item in data["responses"]
        if item["path"].endswith("/call")
    }
    row = next(r for r in rows if not r["archived"] and calls[r["id"]]["state"] == "armed")
    return {"row": row, "call": calls[row["id"]]}


@functools.cache
def explain(ceiling: int) -> dict:
    return asyncio.run(_explain(ceiling))


async def _explain(ceiling: int) -> dict:
    backend = HERE.parents[1]
    script = (
        "import asyncio; from cockpit_scale import scale_service; "
        f"from agent_access.server import run; asyncio.run(run(scale_service({ceiling})))"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", script],
        cwd=backend,
        env={"PYTHONPATH": os.pathsep.join((str(HERE), str(backend)))},
    )
    thesis = subject()["row"]["name"]
    replies, seen = [], {}

    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=60) as client:
            await client.initialize()

            async def call(tool, **arguments):
                reply = await client.call_tool(tool, arguments)
                text = reply.content[0].text
                assert json.loads(text) == reply.structured_content
                replies.append((tool, len(text)))
                assert not reply.is_error, reply.structured_content
                return reply.structured_content["result"]

            workflow = await call("get_workflow", name="explain-call", arguments={"thesis": thesis})
            assert workflow["workflow"]["version"] == "1.2.0"
            assert "match" in workflow["instructions"] and "index: true" in workflow["instructions"]
            await call("describe_dashboard", dashboard="board")
            await call("describe_dashboard", dashboard="cockpit")
            board = await call(
                "read_dashboard",
                dashboard="board",
                settings={"dq_open": True, "archived_open": False},
            )
            # Step 2: find the card with match; the queue item's label holds the name too.
            matches, offset = [], 0
            while offset is not None:
                page = await call(
                    "inspect_dashboard_item",
                    snapshot_id=board["snapshot_id"],
                    match=thesis,
                    offset=offset,
                )
                matches += page["targets"]
                offset = page["next_offset"]
            seen["matches"] = matches
            (card,) = [t for t in matches if t["label"] == thesis and t["links"]]
            summary = next(r for r in card["source_refs"] if r["category"] == "thesis_summary")
            confirmed = await call(
                "inspect_dashboard_item",
                snapshot_id=board["snapshot_id"],
                target_id=card["id"],
                source_ref_ids=[summary["id"]],
            )
            seen["row"] = json.loads(confirmed["source_data_items"][0]["text"])
            # Step 3: a fresh capture of the card's Cockpit.
            child = await call(
                "read_dashboard",
                from_snapshot_id=board["snapshot_id"],
                target_id=card["id"],
                link_id=card["links"][0]["id"],
                freshness="fresh",
            )
            seen["child"] = child
            snapshot = child["snapshot_id"]
            # Step 4: map the Cockpit with index, not a full listing.
            index, offset = [], 0
            while offset is not None:
                page = await call(
                    "inspect_dashboard_item", snapshot_id=snapshot, index=True, offset=offset
                )
                assert page["target_shape"] == "index" and page["targets"]
                index += page["targets"]
                offset = page["next_offset"]
            seen["index"] = index
            call_target = next(t for t in index if t["label"].startswith("CALL ·"))
            record = await call(
                "inspect_dashboard_item", snapshot_id=snapshot, target_id=call_target["id"]
            )
            (opened,) = record["targets"]
            reference = next(r for r in opened["source_refs"] if r["category"] == "call_result")
            outline = await call(
                "inspect_dashboard_item",
                snapshot_id=snapshot,
                target_id=call_target["id"],
                source_ref_id=reference["id"],
                outline=True,
            )
            shape = outline["source_data"]
            verdict = await call(
                "inspect_dashboard_item",
                snapshot_id=snapshot,
                source_response_index=shape["response_index"],
                pointer="/verdict",
            )
            seen["verdict"] = json.loads(verdict["source_data"]["text"])
            # The largest target (the thesis area, about 35K of DOM details) opens cut to fit;
            # its omitted parts read through view_pointer.
            main = index[0]
            big = await call("inspect_dashboard_item", snapshot_id=snapshot, target_id=main["id"])
            seen["big"] = big
            rows = await call(
                "inspect_dashboard_item",
                snapshot_id=snapshot,
                target_id=main["id"],
                view_pointer="/tables/0/rows",
                outline=True,
            )
            seen["rows_outline"] = rows["view_data"]
            last = rows["view_data"]["outline_total"] - 1
            row = await call(
                "inspect_dashboard_item",
                snapshot_id=snapshot,
                target_id=main["id"],
                view_pointer=f"/tables/0/rows/{last}",
            )
            seen["last_row"] = json.loads(row["view_data"]["text"])
            annotations = await call(
                "inspect_dashboard_item",
                snapshot_id=snapshot,
                view_pointer="/annotations",
                outline=True,
            )
            seen["annotations"] = annotations["view_data"]
            first = await call(
                "inspect_dashboard_item", snapshot_id=snapshot, view_pointer="/annotations/0"
            )
            seen["annotation"] = json.loads(first["view_data"]["text"])
    return {"replies": replies, "seen": seen}


@pytest.mark.parametrize("ceiling", CEILINGS)
def test_explain_call_runs_at_real_cockpit_scale_and_reads_the_frozen_view(ceiling):
    outcome = explain(ceiling)
    seen, expected = outcome["seen"], subject()
    data = board_scale.envelope()
    cockpit = cockpit_scale.cockpit_envelope(data, expected["row"]["id"])
    view = cockpit_scale.view(cockpit)
    # match: the queue item and the card, both holding the name; the card resolves exactly one row.
    assert len(seen["matches"]) == 2
    assert (seen["row"]["id"], seen["row"]["name"], seen["row"]["archived"]) == (
        expected["row"]["id"],
        expected["row"]["name"],
        False,
    )
    child = seen["child"]
    assert child["dashboard"] == "cockpit" and child["observation"]["target_count"] == 504
    assert child["observation"]["annotation_count"] == 1180
    assert child["observation"]["tables"] == [
        {"headers": cockpit_scale.TABLE_HEADERS, "row_count": 77}
    ]
    assert "annotations" in child["observation"]["capture_truncated"]
    # The index lists every target, in order, as id and label only.
    assert [t["id"] for t in seen["index"]] == [t["id"] for t in view["targets"]]
    assert all(set(t) <= {"id", "label", "label_total_characters"} for t in seen["index"])
    assert seen["verdict"] == expected["call"]["verdict"]
    # The 35K target opens cut to the ceiling; its truncation names view_pointer paths.
    (big,) = seen["big"]["targets"]
    truncation = seen["big"]["truncation"]
    assert big["id"] == view["targets"][0]["id"] and big["source_refs"]
    assert truncation["truncated"] and truncation["paths"]
    assert seen["rows_outline"]["outline_total"] == 77
    assert seen["last_row"] == view["targets"][0]["tables"][0]["rows"][76]
    assert seen["annotations"]["outline_total"] == 1000
    assert seen["annotation"] == view["observation"]["annotations"][0]
    print(
        f"\nexplain-call 1.2.0 at Cockpit scale, ceiling {ceiling:,}: {len(outcome['replies'])} "
        f"calls, {sum(size for _, size in outcome['replies']):,} characters: "
        + ", ".join(f"{tool} {size:,}" for tool, size in outcome["replies"])
    )


@pytest.mark.parametrize("ceiling", CEILINGS)
def test_every_explain_call_reply_stays_under_the_reply_ceiling(ceiling):
    replies = explain(ceiling)["replies"]
    over = [(tool, size) for tool, size in replies if size > ceiling]
    assert not over, over
