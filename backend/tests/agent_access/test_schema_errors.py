"""A schema-rejected tool call names the offending argument, so a model can correct itself."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

HERE = Path(__file__).resolve().parent
GENERIC = "Unknown tool or arguments do not match the advertised input schema."


async def _calls(requests):
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
    out = []
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=60) as client:
            await client.initialize()
            for tool, arguments in requests:
                reply = await client.call_tool(tool, arguments)
                assert reply.is_error
                out.append(json.loads(reply.content[0].text)["error"])
    return out


def test_rejected_arguments_name_the_problem_and_unknown_tools_stay_generic():
    errors = asyncio.run(
        _calls(
            [
                (
                    "inspect_dashboard_item",
                    {"snapshot_id": "s", "target_id": "t", "pointer": "/state"},
                ),
                ("inspect_dashboard_item", {"snapshot_id": "s", "source_ref_ids": ["r"]}),
                ("describe_dashboard", {"dashboard": "board", "surprise": 1}),
                ("no_such_tool", {"x": 1}),
            ]
        )
    )
    assert all(error["code"] == "invalid_arguments" for error in errors)
    assert all(len(error["message"]) <= 200 for error in errors)
    print("\n" + "\n".join(error["message"] for error in errors))
    assert "source_response_index" in errors[0]["message"]
    assert "target_id" in errors[1]["message"]
    assert "surprise" in errors[2]["message"]
    assert errors[3]["message"] == GENERIC
    assert all(error["message"] != GENERIC for error in errors[:3])
