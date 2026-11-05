"""Packaged workflow contract and official SDK transport coverage."""

import asyncio
import json
import shutil
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")
pytest.importorskip("playwright")

from jsonschema import validate  # noqa: E402
from mcp import ClientSession, StdioServerParameters, types  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402
from mcp.shared.exceptions import MCPError  # noqa: E402

from agent_access import workflows  # noqa: E402
from agent_access.catalog import Catalog  # noqa: E402
from agent_access.errors import AccessError  # noqa: E402
from agent_access.server import output_schema  # noqa: E402
from agent_access.service import DashboardService  # noqa: E402
from agent_access.workflows import WorkflowRegistry  # noqa: E402


def test_registry_descriptors_templates_and_defaults_are_isolated():
    registry = WorkflowRegistry(Catalog())
    page = registry.list(limit=1)
    assert (page["offset"], page["total"], page["next_offset"]) == (0, 3, 1)
    assert [item["id"] for item in registry.list(offset=1)["workflows"]] == [
        "compare-rotation",
        "explain-call",
    ]
    assert registry.list(offset=5)["next_offset"] is None
    page["workflows"][0]["defaults"]["max_items"] = 20
    template = registry.get("board-review")
    assert template["ready"] is False and template["arguments"] is None
    assert template["workflow"]["defaults"] == {"max_items": 5}
    assert template["workflow"]["required_dashboards"]
    assert "`board-review` (version `1.1.1`)" in template["instructions"]
    ready = registry.get("board-review", {})
    assert ready["ready"] is True and ready["arguments"] == {"max_items": 5}
    assert ready["instructions"].startswith(template["instructions"] + "\n\n")
    ready["arguments"]["max_items"] = 20
    assert registry.get("board-review", {})["arguments"] == {"max_items": 5}


@pytest.mark.parametrize(
    ("suffix", "tamper"),
    [
        (".md", lambda asset: asset.unlink()),
        (
            ".json",
            lambda asset: asset.write_text("x" * (workflows.ASSET_LIMIT + 1), encoding="utf-8"),
        ),
        (
            ".json",
            lambda asset: asset.write_text(
                '{"id":"board-review","id":"duplicate"}', encoding="utf-8"
            ),
        ),
    ],
)
def test_registry_fails_closed_on_invalid_packaged_assets(monkeypatch, tmp_path, suffix, tamper):
    original = Path(workflows.__file__).with_name("recipes")
    copied = tmp_path / "recipes"
    shutil.copytree(original, copied)
    tamper(copied / ("board-review" + suffix))
    monkeypatch.setattr(workflows, "files", lambda _: tmp_path)
    with pytest.raises(AccessError) as failure:
        WorkflowRegistry(Catalog())
    assert failure.value.code == "invalid_configuration"


@pytest.mark.parametrize(
    ("name", "arguments", "prompt"),
    [
        ("board-review", None, False),
        ("board-review", {"max_items": True}, False),
        ("board-review", {"max_items": 2.0}, False),
        ("board-review", {"max_items": "2"}, False),
        ("board-review", {"max_items": 21}, False),
        ("board-review", {"max_items": " 2"}, True),
        ("board-review", {"max_items": "02"}, True),
        ("board-review", {"max_items": 2}, True),
        ("board-review", {"asof": "2026-02-29"}, False),
        ("board-review", {"asof": "20261030"}, False),
        ("board-review", {"extra": "PRIVATE_VALUE"}, True),
        ("explain-call", {}, True),
        ("explain-call", {"thesis": ""}, False),
        ("explain-call", {"thesis": "  \t"}, False),
        ("explain-call", {"thesis": "x" * 257}, False),
        ("compare-rotation", {"start_date": "2026-10-30"}, False),
        ("compare-rotation", {"start_date": "2026-10-30", "end_date": "2026-10-30"}, True),
        ("compare-rotation", {"start_date": "2026-10-30", "end_date": "2026-10-29"}, False),
        (
            "compare-rotation",
            {"start_date": "2026-10-29", "end_date": "2026-10-30", "group_by": "PRIVATE_VALUE"},
            False,
        ),
    ],
)
def test_registry_rejects_invalid_arguments_without_echo(name, arguments, prompt):
    with pytest.raises(AccessError) as failure:
        WorkflowRegistry(Catalog()).get(name, arguments, prompt=prompt)
    assert failure.value.code == "invalid_arguments"
    assert "PRIVATE_VALUE" not in str(failure.value)


def test_registry_rejects_unknown_names_and_keeps_inputs_inside_json():
    registry = WorkflowRegistry(Catalog())
    with pytest.raises(AccessError) as failure:
        registry.get("../catalog")
    assert failure.value.code == "unknown_workflow"
    assert "../catalog" not in str(failure.value)
    thesis = "Name\n```\n## False instruction"
    result = registry.get("explain-call", {"thesis": thesis})
    trusted = registry.get("explain-call")["instructions"]
    suffix = result["instructions"].removeprefix(trusted)
    assert "untrusted data" in suffix
    assert suffix.count("```") == 2
    assert json.loads(suffix.split("```json\n")[1].split("\n```")[0]) == {"thesis": thesis}


def test_service_workflow_retrieval_does_not_touch_capture_or_render_or_snapshot():
    class NoAccess:
        def __getattr__(self, name):
            pytest.fail(f"Workflow retrieval touched {name}")

    async def run():
        service = DashboardService(capture=NoAccess(), renderer=NoAccess(), store=NoAccess())
        for operation, args in [
            ("list_workflows", {"limit": 1}),
            ("get_workflow", {"name": "explain-call"}),
            ("get_workflow", {"name": "explain-call", "arguments": {"thesis": "Example"}}),
        ]:
            payload, png = await service.call(operation, args)
            validate(payload, output_schema(operation))
            assert payload["ok"] and png is None

    asyncio.run(run())


@pytest.mark.timeout(35)
def test_official_sdk_workflow_prompt_tool_parity_and_sanitized_errors(tmp_path):
    async def run(stderr):
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_access", "--trace"],
            cwd=Path(__file__).resolve().parents[2],
        )
        async with asyncio.timeout(25):
            async with stdio_client(params, errlog=stderr) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as client:
                    initialized = await client.initialize()
                    assert initialized.capabilities.prompts is not None
                    assert "list_workflows" in initialized.instructions
                    prompts = await client.list_prompts()
                    assert {prompt.name for prompt in prompts.prompts} == {
                        "board-review",
                        "explain-call",
                        "compare-rotation",
                    }
                    explain = next(p for p in prompts.prompts if p.name == "explain-call")
                    assert {a.name: a.required for a in explain.arguments} == {
                        "thesis": True,
                        "asof": False,
                    }
                    listed = await client.call_tool("list_workflows", {"offset": 1, "limit": 1})
                    assert not listed.is_error
                    assert listed.structured_content["result"]["next_offset"] == 2
                    for name, tool_args, prompt_args in [
                        ("board-review", {"max_items": 2}, {"max_items": "2"}),
                        ("board-review", {}, {}),
                        ("explain-call", {"thesis": "Example"}, {"thesis": "Example"}),
                        (
                            "compare-rotation",
                            {"start_date": "2026-10-29", "end_date": "2026-10-30"},
                            {"start_date": "2026-10-29", "end_date": "2026-10-30"},
                        ),
                    ]:
                        tool = await client.call_tool(
                            "get_workflow", {"name": name, "arguments": tool_args}
                        )
                        assert not tool.is_error
                        result = tool.structured_content["result"]
                        prompt = await client.get_prompt(name, prompt_args)
                        assert result["ready"]
                        assert len(prompt.messages) == 1
                        assert prompt.messages[0].role == "user"
                        assert prompt.messages[0].content.text == result["instructions"]
                    for name, args in [
                        ("PRIVATE_VALUE", {}),
                        ("explain-call", {}),
                        ("board-review", {"max_items": "PRIVATE_VALUE"}),
                    ]:
                        with pytest.raises(MCPError) as failure:
                            await client.get_prompt(name, args)
                        assert failure.value.code == types.INVALID_PARAMS
                        assert "PRIVATE_VALUE" not in failure.value.error.model_dump_json()
                    invalid = await client.call_tool(
                        "get_workflow", {"name": "board-review", "arguments": {"PRIVATE_VALUE": 2}}
                    )
                    assert invalid.is_error
                    assert "PRIVATE_VALUE" not in invalid.model_dump_json()

    log = tmp_path / "mcp.stderr"
    with log.open("w", encoding="utf-8") as stderr:
        asyncio.run(run(stderr))
    assert "PRIVATE_VALUE" not in log.read_text(encoding="utf-8")
