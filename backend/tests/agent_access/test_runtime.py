"""Offline policy/transport tests: no DB, backend instance, or installed browser needed."""

import asyncio
import copy
import json
import sys
from pathlib import Path

import pytest

pytest.importorskip("mcp")
pytest.importorskip("jsonschema")
pytest.importorskip("playwright")

import httpx  # noqa: E402
from jsonschema import validate  # noqa: E402
from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from agent_access.capture import CaptureClient, loopback_base, validate_capture  # noqa: E402
from agent_access.catalog import Catalog  # noqa: E402
from agent_access.errors import AccessError  # noqa: E402
from agent_access.server import OUTPUT_SCHEMA  # noqa: E402
from agent_access.service import DashboardService, preview  # noqa: E402
from agent_access.snapshots import SnapshotStore  # noqa: E402

CLOCK = {
    "current_night": "2026-10-30",
    "action_day": "2026-11-02",
    "market_today": "2026-11-01",
    "market_tz": "America/New_York",
}


def envelope():
    return {
        "schema_version": "1",
        "dashboard": "board",
        "asof": "2026-10-30",
        "captured_at": "2026-11-01T00:00:00Z",
        "context": {
            "scope": "deployment_visibility_not_authorization",
            "clock": "public",
            "known_at": "2026-10-31T00:00:00Z",
            "decisions_asof": "2026-10-30",
            "countdown_origin": "2026-10-30",
            "clock_response": dict(CLOCK),
        },
        "consistency": {
            "database": "repeatable_read_read_only",
            "generation_stable": True,
            "generation_before": "g1",
            "generation_after": "g1",
        },
        "responses": [
            {"method": "GET", "path": "/clock", "query": {}, "status": 200, "body": dict(CLOCK)},
            {
                "method": "GET",
                "path": "/theses",
                "query": {"include_archived": "true"},
                "status": 200,
                "body": [{"id": "abc", "narrative": "exact text"}],
            },
        ],
    }


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com:8001",
        "http://127.0.0.1:8001/path",
        "http://user:pass@localhost:80",
        "http://localhost:80?x=1",
        "file:///etc/passwd",
        "http://0.0.0.0:8001",
        "http://127.0.0.1",
    ],
)
def test_fail_closed_origins(value):
    with pytest.raises(AccessError):
        loopback_base(value)


def test_catalog_strict_capabilities_settings_and_dates():
    catalog = Catalog()
    assert len(catalog.dashboards) >= 29
    expected = {"board", "rotation-map", "narrative-index"}
    if any(item["slug"] == "cockpit" for item in catalog.dashboards):
        expected.add("cockpit")
    assert {
        item["slug"] for item in catalog.dashboards if item["capabilities"]["observe"]
    } == expected
    _, settings = catalog.prepare("rotation-map", {"group_by": "sector"}, "2026-10-30")
    assert settings["group_by"] == "sector"
    for slug, values in [("board", {"mutation": True}), ("rotation-map", {"group_by": "anything"})]:
        with pytest.raises(AccessError, match="Settings"):
            catalog.prepare(slug, values, None)
    with pytest.raises(AccessError):
        catalog.prepare("shared-names", {}, None)
    with pytest.raises(AccessError):
        catalog.prepare("board", {}, "2026-02-31")


def test_capture_strict_methods_paths_duplicates_and_generation():
    for change in [
        lambda e: e["responses"][0].update(method="POST"),
        lambda e: e["responses"][0].update(path="/admin"),
        lambda e: e["responses"][0].update(path="/../clock"),
        lambda e: e["responses"].append(e["responses"][0]),
        lambda e: e["consistency"].update(generation_stable=False),
        lambda e: e["consistency"].update(generation_after="changed"),
        lambda e: e["context"].update(known_at=None),
        lambda e: e["responses"][0].update(body={}),
        lambda e: e.update(asof="not-a-date"),
    ]:
        data = envelope()
        change(data)
        with pytest.raises(AccessError):
            validate_capture(data, "board")


def test_capture_http_is_single_bounded_get_no_redirects_or_settings_leakage():
    seen = []

    async def handle(request):
        seen.append(request)
        return httpx.Response(200, json=envelope())

    async def run():
        descriptor, settings = Catalog().prepare("board", {"dq_open": True}, None)
        client = CaptureClient("http://127.0.0.1:8001", transport=httpx.MockTransport(handle))
        assert await client.capture(descriptor, settings, None) == envelope()
        assert len(seen) == 1
        assert seen[0].method == "GET"
        assert seen[0].url.path == "/dashboards/capture"
        assert dict(seen[0].url.params) == {"dashboard": "board"}
        with pytest.raises(AccessError, match="requested dashboard date"):
            await client.capture(descriptor, settings, "2026-10-29")

        async def redirect(request):
            return httpx.Response(302, headers={"location": "https://example.com/stolen"})

        with pytest.raises(AccessError):
            await CaptureClient(
                "http://127.0.0.1:8001", transport=httpx.MockTransport(redirect)
            ).capture(descriptor, settings, None)

        async def oversized(request):
            return httpx.Response(
                200,
                headers={"content-type": "application/json"},
                content=b" " * (8 * 1024 * 1024 + 1),
            )

        with pytest.raises(AccessError, match="8 MiB"):
            await CaptureClient(
                "http://127.0.0.1:8001", transport=httpx.MockTransport(oversized)
            ).capture(descriptor, settings, None)

    asyncio.run(run())


def test_store_monotonic_expiry_capacity_and_bytes():
    now = [0]
    store = SnapshotStore(ttl=10, capacity=1, max_bytes=10000, clock=lambda: now[0])
    store.add("one", envelope(), {}, {}, b"png", {})
    store.add("two", envelope(), {}, {}, b"png", {})
    with pytest.raises(AccessError):
        store.get("one")
    assert store.get("two").png == b"png"
    now[0] = 10
    with pytest.raises(AccessError, match="expired"):
        store.get("two")
    with pytest.raises(AccessError, match="memory budget"):
        store.add("huge", envelope(), {}, {}, b"x" * 10000, {})


class FakeCapture:
    def __init__(self):
        self.calls = 0

    async def capture(self, descriptor, settings, asof):
        self.calls += 1
        return envelope()


class FakeRenderer:
    def __init__(self):
        self.calls = []

    async def render(self, capture, snapshot_id, settings, target_id=None):
        self.calls.append(copy.deepcopy((capture, snapshot_id, settings, target_id)))
        return (
            {
                "status": "ready",
                "snapshot_id": snapshot_id,
                "settings": settings,
                "observation": {"text": "Exact rendered label 12.34%"},
                "targets": [
                    {"id": 'card:a"[odd]', "label": "Exact", "text": "12.34%", "visible": True}
                ],
            },
            b"png",
            {"cropped": True},
        )


def test_service_same_capture_drilldown_png_error_and_settings_isolation():
    async def run():
        capture, renderer = FakeCapture(), FakeRenderer()
        service = DashboardService(capture=capture, renderer=renderer)
        settings = {"dq_open": True}
        payload, image = await service.call(
            "read_dashboard", {"dashboard": "board", "settings": settings}
        )
        validate(payload, OUTPUT_SCHEMA)
        assert payload["ok"] and image is None
        snapshot_id = payload["result"]["snapshot_id"]
        settings["dq_open"] = False
        inspected, _ = await service.call(
            "inspect_dashboard_item",
            {
                "snapshot_id": snapshot_id,
                "target_id": 'card:a"[odd]',
                "source_response_index": 1,
                "data_limit": 10,
            },
        )
        assert inspected["result"]["targets"][0]["text"] == "12.34%"
        assert inspected["result"]["source_data"]["next_offset"] == 10
        assert inspected["result"]["source_data"]["truncated"]
        _, png = await service.call("render_dashboard", {"snapshot_id": snapshot_id})
        assert png == b"png" and len(renderer.calls) == 1
        _, png = await service.call(
            "render_dashboard", {"snapshot_id": snapshot_id, "target_id": 'card:a"[odd]'}
        )
        assert png == b"png" and capture.calls == 1 and len(renderer.calls) == 2
        assert renderer.calls[0][:3] == renderer.calls[1][:3]
        assert renderer.calls[1][2]["dq_open"] is True
        error, _ = await service.call("render_dashboard", {"snapshot_id": "stale"})
        validate(error, OUTPUT_SCHEMA)
        assert error["error"]["code"] == "snapshot_expired"

    asyncio.run(run())


def test_total_deadline_and_cancellation():
    class SlowRenderer:
        async def render(self, *args):
            await asyncio.sleep(100)

    async def run():
        service = DashboardService(capture=FakeCapture(), renderer=SlowRenderer(), deadline=0.01)
        payload, _ = await service.call("read_dashboard", {"dashboard": "board"})
        assert payload["error"]["code"] == "request_timeout"
        task = asyncio.create_task(service.call("read_dashboard", {"dashboard": "board"}))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_snapshot_capabilities_and_ui_asset_drift_fail_closed():
    class VersionedRenderer(FakeRenderer):
        version = "a"

        async def render(self, *args):
            view, png, metadata = await super().render(*args)
            return view, png, {**metadata, "asset_digest": self.version}

    async def run():
        renderer = VersionedRenderer()
        service = DashboardService(capture=FakeCapture(), renderer=renderer)
        initial, _ = await service.call("read_dashboard", {"dashboard": "board"})
        snapshot_id = initial["result"]["snapshot_id"]
        renderer.version = "b"
        mismatch, _ = await service.call(
            "render_dashboard", {"snapshot_id": snapshot_id, "target_id": 'card:a"[odd]'}
        )
        assert mismatch["error"]["code"] == "render_mismatch"
        descriptor = service.catalog.describe("board")
        descriptor["capabilities"]["screenshot"] = False
        denied, _ = await service.call("render_dashboard", {"snapshot_id": snapshot_id})
        assert denied["error"]["code"] == "unsupported_capability"
        descriptor["capabilities"]["drilldown"] = False
        denied, _ = await service.call("inspect_dashboard_item", {"snapshot_id": snapshot_id})
        assert denied["error"]["code"] == "unsupported_capability"

    asyncio.run(run())


def test_output_preview_is_bounded_and_reports_omissions():
    value, metadata = preview({"tables": [{"rows": [["x" * 20000] * 10] * 1000}]})
    assert len(json.dumps(value)) < 80000
    assert metadata["truncated"] and metadata["path_count"] > 0


def test_real_official_sdk_stdio_discovery_and_structured_errors():
    async def run():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "agent_access", "--trace"],
            cwd=Path(__file__).resolve().parents[2],
        )
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream, read_timeout_seconds=20) as client:
                initialized = await client.initialize()
                assert initialized.server_info.name == "alphadeck-dashboards"
                tools = await client.list_tools()
                assert len(tools.tools) == 5
                assert all(
                    tool.annotations.read_only_hint and tool.output_schema for tool in tools.tools
                )
                listed = await client.call_tool("list_dashboards", {"limit": 2})
                assert len(listed.structured_content["result"]["dashboards"]) == 2
                assert listed.structured_content["result"]["next_offset"] == 2
                described = await client.call_tool("describe_dashboard", {"dashboard": "board"})
                assert described.structured_content["result"]["dashboard"]["capabilities"][
                    "observe"
                ]
                stale = await client.call_tool("render_dashboard", {"snapshot_id": "unknown"})
                assert (
                    stale.is_error
                    and stale.structured_content["error"]["code"] == "snapshot_expired"
                )
                unsupported = await client.call_tool(
                    "read_dashboard", {"dashboard": "shared-names"}
                )
                assert (
                    unsupported.is_error
                    and unsupported.structured_content["error"]["code"] == "unsupported_capability"
                )

    asyncio.run(run())
