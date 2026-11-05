"""board-review 1.1.0 at REAL scale, through the real MCP server over stdio.

A scripted official-SDK client follows the recipe on the 66-card / 42-armed fixture (`board_scale`):
read the Board, list the whole Decision Queue, check its order from the `theses[<i>]` labels, and make
ONE batch read per shortlisted item. It sums the characters of every tool reply and counts the calls,
so the cost of a review is measured, not estimated. The capture and renderer are synthetic stand-ins
(no DB, backend or browser); the service, server, Board adapter references and recipe are real.
"""

import asyncio
import functools
import json
import os
import re
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")

from board_scale import ARMED, envelope, view  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

HERE = Path(__file__).resolve().parent
MAX_ITEMS = 5
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
# The operator-set budget for one 5-item review (the target is under 100K in about 15 calls).
CHARACTER_BUDGET, CALL_BUDGET = 120_000, 20


@functools.cache
def review() -> dict:
    """Run the recipe once per process; both tests read its outcome."""
    return asyncio.run(_review())


async def _review() -> dict:
    backend = HERE.parents[1]
    script = (
        "import asyncio; from board_scale import scale_service; "
        "from agent_access.server import run; asyncio.run(run(scale_service()))"
    )
    params = StdioServerParameters(
        command=sys.executable,
        args=["-c", script],
        cwd=backend,
        env={"PYTHONPATH": os.pathsep.join((str(HERE), str(backend)))},
    )
    replies = []

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

            workflow = await call(
                "get_workflow", name="board-review", arguments={"max_items": MAX_ITEMS}
            )
            assert workflow["workflow"]["version"] == "1.1.0"
            assert "source_ref_ids" in workflow["instructions"]
            await call("describe_dashboard", dashboard="board")
            board = await call(
                "read_dashboard",
                dashboard="board",
                settings={"dq_open": True, "archived_open": False},
                target_limit=50,
            )
            snapshot = board["snapshot_id"]
            # Step 2: the queue's length is the rendered Armed column count.
            armed = int(re.search(r"(?i)armed\s+act now\s+(\d+)", board["observation"]["text"])[1])
            listed, offset = list(board["targets"]), board["next_target_offset"]
            while len(listed) < armed and offset is not None:
                page = await call(
                    "inspect_dashboard_item",
                    snapshot_id=snapshot,
                    offset=offset,
                    limit=min(50, armed - len(listed)),
                )
                assert page["snapshot_id"] == snapshot and page["offset"] == offset
                listed += page["targets"]
                offset = page["next_offset"]
            queue = listed[:armed]
            # Step 3: order from each queue target's thesis_summary label.
            order = [
                int(re.fullmatch(r"theses\[(\d+)\]", ref["label"])[1])
                for target in queue
                for ref in target["source_refs"]
                if ref["category"] == "thesis_summary"
            ]
            # Step 4: ONE batch read per shortlisted item.
            items = []
            for target in queue[:MAX_ITEMS]:
                ids = [
                    ref["id"]
                    for ref in target["source_refs"]
                    if (ref["category"] == "call_result" and ref["label"] in CALL_FIELDS)
                    or ref["category"] == "data_flag"
                ]
                batch = await call(
                    "inspect_dashboard_item",
                    snapshot_id=snapshot,
                    target_id=target["id"],
                    source_ref_ids=ids,
                )
                assert batch["snapshot_id"] == snapshot
                items.append((target, batch["source_data_items"]))
    return {"armed": armed, "queue": queue, "order": order, "items": items, "replies": replies}


def test_board_review_runs_at_real_scale_in_one_snapshot_and_matches_the_capture():
    outcome = review()
    data = envelope()
    paths = [item["path"] for item in data["responses"]]
    rows = data["responses"][paths.index("/theses")]["body"]
    calls = {
        item["body"]["thesis_id"]: item["body"]
        for item in data["responses"]
        if "/call" in item["path"]
    }
    members = data["responses"][paths.index("/data-flags")]["body"]["members"]
    expected = [
        i
        for i, row in enumerate(rows)
        if not row["archived"] and calls[row["id"]]["state"] == "armed"
    ]
    # The whole queue is listed, in /theses order, and its labels strictly increase.
    assert outcome["armed"] == ARMED == len(outcome["queue"])
    assert outcome["order"] == expected and outcome["order"] == sorted(set(outcome["order"]))
    rendered = view(data)["targets"][:ARMED]
    assert [target["id"] for target in outcome["queue"]] == [target["id"] for target in rendered]

    for (target, items), index in zip(outcome["items"], expected[:MAX_ITEMS], strict=True):
        call = calls[rows[index]["id"]]
        by_label = {item["label"]: item for item in items if item["category"] == "call_result"}
        assert set(by_label) == set(CALL_FIELDS)
        assert json.loads(by_label["state"]["text"]) == "armed"  # queue membership
        for field, item in by_label.items():
            serialized = json.dumps(call[field], ensure_ascii=True, separators=(",", ":"))
            assert item["total_characters"] == len(serialized)
            assert serialized.startswith(item["text"]) and len(item["text"]) <= 4000
            if not item["truncated"]:
                assert json.loads(item["text"]) == call[field]
        # the headline armed member is within the first (cut) item text
        headline = call["armed_members"][0]
        assert by_label["armed_members"]["truncated"]
        assert f'"ticker":"{headline["ticker"]}"' in by_label["armed_members"]["text"]
        flags = [item for item in items if item["category"] == "data_flag"]
        held = [m for m in members if any(h["id"] == call["thesis_id"] for h in m["theses"])]
        assert [json.loads(item["text"]) for item in flags] == held

    replies = outcome["replies"]
    print(
        f"\nboard-review 1.1.0, max_items={MAX_ITEMS}: {len(replies)} calls, "
        f"{sum(size for _, size in replies):,} characters: "
        + ", ".join(f"{tool} {size:,}" for tool, size in replies)
    )
    assert len(replies) <= CALL_BUDGET


@pytest.mark.xfail(
    strict=True,
    reason="NOT MET (measured 2026-11-05: about 214K characters in 9 calls). Listing the whole "
    "42-target queue with each target's 13-21 references costs about 119K, and five batch reads "
    "about 88K. Kept at the operator's budget, never loosened; see docs/MCP.md 'Known limits'.",
)
def test_board_review_fits_the_character_budget():
    total = sum(size for _, size in review()["replies"])
    assert total <= CHARACTER_BUDGET, f"{total:,} characters"
