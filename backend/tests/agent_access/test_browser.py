"""Opt-in real Chromium tests with synthetic data only: ALPHADECK_MCP_BROWSER_TESTS=1."""

import asyncio
import base64
import copy
import functools
import json
import os
import struct
import sys
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("jsonschema")
pytest.importorskip("mcp")
pytestmark = pytest.mark.skipif(
    os.environ.get("ALPHADECK_MCP_BROWSER_TESTS") != "1", reason="Opt-in Chromium integration"
)

from mcp import ClientSession, StdioServerParameters  # noqa: E402
from mcp.client.stdio import stdio_client  # noqa: E402

from agent_access.capture import validate_capture  # noqa: E402
from agent_access.catalog import Catalog  # noqa: E402
from agent_access.errors import AccessError  # noqa: E402
from agent_access.references import context_hash  # noqa: E402
from agent_access.renderer import OCCLUSION_CHECK, FrozenRenderer  # noqa: E402

CLOCK = {
    "current_night": "2026-10-30",
    "action_day": "2026-11-02",
    "market_today": "2026-11-01",
    "market_tz": "America/New_York",
}


@contextmanager
def serve(directory):
    class QuietHandler(SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(
        ("127.0.0.1", 0), functools.partial(QuietHandler, directory=str(directory))
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield "http://127.0.0.1:" + str(server.server_port)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)


def capture(slug, responses):
    return {
        "schema_version": "1",
        "dashboard": slug,
        "asof": "2026-10-30",
        "captured_at": "2026-11-01T00:00:00Z",
        "context": {"clock_response": CLOCK},
        "responses": [
            {"method": "GET", "path": "/clock", "query": {}, "status": 200, "body": CLOCK}
        ]
        + responses,
    }


def response(path, body, query=None):
    return {"method": "GET", "path": path, "query": query or {}, "status": 200, "body": body}


def test_real_browser_scrolled_target_and_ancestor_clip(tmp_path):
    (tmp_path / "mcp-render.html").write_text(
        """<!doctype html><style>body{margin:0}#gap{height:1500px}#nested{height:80px;width:400px;overflow:hidden}#target{height:160px;background:rgb(0,255,0)}</style>
    <div data-mcp-root><div id=gap></div><div id=nested><div id=target data-mcp-id='card:a"[odd]'>Synthetic target</div></div></div>
    <script>const s=window.__ALPHA_DECK_MCP_SNAPSHOT__;window.__ALPHA_DECK_MCP_VIEW__={status:'ready',snapshot_id:s.snapshot_id,settings:s.settings,observation:{text:'Synthetic target'},targets:[{id:'card:a"[odd]',text:'Synthetic target'}]};</script>""",
        encoding="utf-8",
    )
    with serve(tmp_path) as base:
        _, png, metadata = asyncio.run(
            FrozenRenderer(base).render(capture("board", []), "synthetic", {}, 'card:a"[odd]')
        )
    width, height = struct.unpack(">II", png[16:24])
    assert width == 400 and height == 80
    assert metadata["page_scroll"]["y"] > 0
    assert metadata["document_clip"]["y"] >= 1500
    assert metadata["viewport_clip"]["y"] < 1080
    assert metadata["cropped"]


@pytest.mark.parametrize("slug", ["board", "rotation-map", "narrative-index"])
def test_real_dashboard_harness_all_three_empty_states(slug):
    dist = Path(__file__).resolve().parents[3] / "frontend" / "dist"
    if not (dist / "mcp-render.html").exists():
        pytest.skip("Build frontend first")
    _, settings = Catalog().prepare(slug, None, None)
    if slug == "board":
        responses = [
            response("/theses", [], {"include_archived": "true"}),
            response(
                "/data-flags",
                {"asof": "2026-10-30", "names": 0, "members": []},
                {"asof": "2026-10-30"},
            ),
        ]
    elif slug == "rotation-map":
        body = {
            "asof": "2026-10-30",
            "group_by": "link",
            "include_silent": False,
            "live_cards": 0,
            "cards_counted": 0,
            "cards_not_yet_created": 0,
            "roster_fallback_cards": 0,
            "basket_rows": 0,
            "silent_rows_left_out": 0,
            "unresolved_rows": 0,
            "companies": 0,
            "priced_companies": 0,
            "spy": None,
            "groups": [],
            "flagged": [],
            "min_priced": 3,
            "cross_days": 7,
            "cache": "computed",
            "compute_ms": 0,
        }
        responses = [
            response(
                "/dashboards/rotation-map",
                body,
                {"asof": "2026-10-30", "group_by": "link", "include_silent": "false"},
            )
        ]
    else:
        body = {
            "asof": "2026-10-30",
            "window": "1y",
            "include_silent": False,
            "sessions": 0,
            "dates": [],
            "session_source": "none",
            "live_cards": 0,
            "cards_counted": 0,
            "cards_not_yet_created": 0,
            "roster_fallback_cards": 0,
            "companies": 0,
            "priced_companies": 0,
            "silent_rows_left_out": 0,
            "unresolved_rows": 0,
            "flagged_companies": 0,
            "benchmarks": [],
            "cards": [],
            "window_sessions": {"1y": 252, "90d": 90, "30d": 30},
            "cache": "computed",
            "compute_ms": 0,
        }
        responses = [
            response(
                "/dashboards/narrative-index",
                body,
                {"asof": "2026-10-30", "window": "1y", "include_silent": "false"},
            )
        ]
    with serve(dist) as base:
        view, png, metadata = asyncio.run(
            FrozenRenderer(base).render(capture(slug, responses), "synthetic", settings)
        )
    assert view["status"] == "ready" and view["observation"]["text"]
    assert view["snapshot_id"] == "synthetic"
    assert png.startswith(b"\x89PNG") and metadata["viewport"] == {"width": 1440, "height": 1080}


def test_real_browser_blocks_unmatched_api_and_external_requests(tmp_path):
    for url in ["/api/unmatched", "http://example.com/never"]:
        (tmp_path / "mcp-render.html").write_text(
            "<div data-mcp-root>synthetic</div><script>fetch("
            + repr(url)
            + ").catch(()=>{}); const s=window.__ALPHA_DECK_MCP_SNAPSHOT__;window.__ALPHA_DECK_MCP_VIEW__={status:'ready',snapshot_id:s.snapshot_id,settings:s.settings,observation:{text:'synthetic'},targets:[]}</script>",
            encoding="utf-8",
        )
        with serve(tmp_path) as base:
            with pytest.raises(AccessError) as error:
                asyncio.run(FrozenRenderer(base).render(capture("board", []), "synthetic", {}))
            assert error.value.code == "blocked_request"


@pytest.mark.parametrize(
    "overlay_style",
    [
        "",
        "pointer-events:none",
        "pointer-events:none!important",
        "left:200px;width:200px",
        "width:1px",
        "height:1px",
    ],
)
def test_target_screenshot_rejects_visual_overlay_even_with_inert_root(tmp_path, overlay_style):
    (tmp_path / "mcp-render.html").write_text(
        '''<!doctype html><style>body{margin:0}#target{width:400px;height:200px;background:green}#overlay{position:fixed;inset:0 auto auto 0;width:400px;height:200px;background:red}</style>
    <div data-mcp-root inert><div id=target data-mcp-id=target>Intended synthetic source</div><div id=overlay style="'''
        + overlay_style
        + """">Unrelated covering panel</div></div>
    <script>const s=window.__ALPHA_DECK_MCP_SNAPSHOT__;window.__ALPHA_DECK_MCP_VIEW__={status:'ready',snapshot_id:s.snapshot_id,settings:s.settings,observation:{text:'Synthetic'},targets:[]};</script>""",
        encoding="utf-8",
    )
    with serve(tmp_path) as base:
        with pytest.raises(AccessError) as error:
            asyncio.run(
                FrozenRenderer(base).render(capture("board", []), "synthetic", {}, "target")
            )
    assert error.value.code == "target_occluded"


def test_occlusion_measurement_restores_inert_and_pointer_behavior_even_on_error():
    from playwright.async_api import Error, async_playwright

    async def run():
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.set_content(
                    '<style>body{margin:0}#target{width:400px;height:200px}#cover{position:fixed;inset:0;width:400px;height:200px;background:red;pointer-events:none!important}</style><div id="host" inert><div id="target">Synthetic target</div><div id="cover" style="color: blue; --synthetic-note: 123">Synthetic overlay</div></div>'
                )
                state = "() => ({inert:document.getElementById('host').getAttribute('inert'),pointer:getComputedStyle(document.getElementById('cover')).pointerEvents,inline_style:document.getElementById('cover').getAttribute('style'),styles:document.querySelectorAll('style').length})"
                before = await page.evaluate(state)
                result = await page.locator("#target").evaluate(
                    OCCLUSION_CHECK, {"x": 0, "y": 0, "width": 400, "height": 200}
                )
                assert (
                    result["sampled_fully_covered"] and result["assurance"] == "sampled_best_effort"
                )
                assert await page.evaluate(state) == before
                await page.evaluate(
                    "() => { document.elementsFromPoint = () => { throw new Error('Synthetic measurement failure'); }; }"
                )
                with pytest.raises(Error):
                    await page.locator("#target").evaluate(
                        OCCLUSION_CHECK, {"x": 0, "y": 0, "width": 400, "height": 200}
                    )
                assert await page.evaluate(state) == before
            finally:
                await browser.close()

    asyncio.run(run())


def test_uncovered_rounded_target_allows_ancestor_background_at_corners(tmp_path):
    (tmp_path / "mcp-render.html").write_text(
        """<!doctype html><style>body{margin:0}#target{width:400px;height:200px;background:green;border-radius:24px}</style>
    <div data-mcp-root inert><div id=target data-mcp-id=target>Rounded synthetic authored evidence</div></div>
    <script>const s=window.__ALPHA_DECK_MCP_SNAPSHOT__;window.__ALPHA_DECK_MCP_VIEW__={status:'ready',snapshot_id:s.snapshot_id,settings:s.settings,observation:{text:'Synthetic'},targets:[]};</script>""",
        encoding="utf-8",
    )
    with serve(tmp_path) as base:
        _, png, metadata = asyncio.run(
            FrozenRenderer(base).render(capture("board", []), "synthetic", {}, "target")
        )
    measured = metadata["occlusion"]
    assert measured["covered_points"] == 0
    assert measured["target_owned_points"] > 0 and measured["background_points"] > 0
    assert (
        measured["target_owned_points"] + measured["background_points"] == measured["sample_count"]
    )
    assert png.startswith(b"\x89PNG")


def populated_cockpit():
    repo = Path(__file__).resolve().parents[3]
    data = json.loads(
        (repo / "frontend/src/mcp/__tests__/fixtures/cockpit.json").read_text("utf-8")
    )
    data["schema_version"] = "1"
    data["captured_at"] = "2026-10-31T12:00:00Z"
    data["context"].update(
        clock="record",
        known_at=None,
        decisions_asof="2026-10-31",
        countdown_origin="2026-11-02",
        scope="deployment_visibility_not_authorization",
        limitations=["Synthetic fixture; external URLs are references only."],
    )
    data["context"]["clock_response"]["market_tz"] = "America/New_York"
    data["responses"][0]["body"] = copy.deepcopy(data["context"]["clock_response"])
    data["consistency"] = {
        "database": "repeatable_read_read_only",
        "generation_before": "synthetic",
        "generation_after": "synthetic",
        "generation_stable": True,
    }
    return data


class PopulatedCapture:
    """Only trusted synthetic backend-shaped envelopes; not a live HTTP backend."""

    async def capture(self, descriptor, settings, asof, *, expected_context_hash=None):
        data = populated_cockpit()
        if descriptor["slug"] == "board":
            thesis = copy.deepcopy(data["responses"][1]["body"])
            thesis.update(archived=False, basket_size=len(thesis["basket"]))
            data.update(
                dashboard="board",
                responses=[
                    data["responses"][0],
                    response("/theses", [thesis], {"include_archived": "true"}),
                    data["responses"][2],
                    response(
                        "/data-flags",
                        {"asof": data["asof"], "members": [], "names": 0},
                        {"asof": data["asof"]},
                    ),
                ],
                references=[],
                navigation=[
                    {
                        "id": "nav_synthetic_board",
                        "kind": "fresh_capture",
                        "destination": {
                            "dashboard": "cockpit",
                            "settings": {"thesis_id": thesis["id"]},
                        },
                    }
                ],
            )
        if expected_context_hash is not None:
            assert context_hash(data) == expected_context_hash
        return validate_capture(data, descriptor["slug"], descriptor)


def test_official_sdk_real_board_cockpit_evidence_and_selected_member():
    repo = Path(__file__).resolve().parents[3]
    dist = repo / "frontend/dist"
    if not (dist / "mcp-render.html").exists():
        pytest.skip("Build frontend first")

    async def run(base):
        backend = repo / "backend"
        script = "import asyncio,os; from test_browser import PopulatedCapture; from agent_access.service import DashboardService; from agent_access.renderer import FrozenRenderer; from agent_access.server import run; asyncio.run(run(DashboardService(capture=PopulatedCapture(),renderer=FrozenRenderer(os.environ['MCP_TEST_UI_BASE']))))"
        params = StdioServerParameters(
            command=sys.executable,
            args=["-c", script],
            cwd=backend,
            env={
                "PYTHONPATH": os.pathsep.join((str(Path(__file__).parent), str(backend))),
                "MCP_TEST_UI_BASE": base,
            },
        )
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer, read_timeout_seconds=60) as client:
                await client.initialize()

                async def call(name, arguments):
                    response = await client.call_tool(name, arguments)
                    assert not response.is_error, response.structured_content.get("error", {}).get(
                        "code"
                    )
                    return response.structured_content["result"], response

                async def targets(result):
                    found, offset = list(result["targets"]), result.get("next_target_offset")
                    while offset is not None:
                        page, _ = await call(
                            "inspect_dashboard_item",
                            {"snapshot_id": result["snapshot_id"], "offset": offset, "limit": 50},
                        )
                        found.extend(page["targets"])
                        offset = page["next_offset"]
                    return found

                parent, _ = await call("read_dashboard", {"dashboard": "board"})
                board_card = next(target for target in await targets(parent) if target.get("links"))
                child, _ = await call(
                    "read_dashboard",
                    {
                        "from_snapshot_id": parent["snapshot_id"],
                        "target_id": board_card["id"],
                        "link_id": board_card["links"][0]["id"],
                        "freshness": "fresh",
                    },
                )
                assert child["origin"]["parent_snapshot_id"] == parent["snapshot_id"]
                assert child["origin"]["consistency"] == "independent_capture"
                assert child["asof"] == parent["asof"] and context_hash(child) == context_hash(
                    parent
                )
                captured_targets = await targets(child)
                fixture = populated_cockpit()
                source_refs = [
                    ref
                    for ref in fixture["references"]
                    if ref["pointer"].startswith("/triggers_fired/0/sources/")
                ]
                assert len(source_refs) > 1
                for ref in source_refs:
                    source_target = next(
                        target
                        for target in captured_targets
                        if any(item["id"] == ref["id"] for item in target.get("source_refs", []))
                    )
                    expected = fixture["responses"][2]["body"]["triggers_fired"][0]["sources"][
                        int(ref["pointer"].rsplit("/", 1)[1])
                    ]
                    assert (
                        expected["source"] in source_target["text"]
                        and expected["ref"] in source_target["text"]
                    )
                    assert all(key in source_target["text"] for key in expected["detail"])
                    source, _ = await call(
                        "inspect_dashboard_item",
                        {
                            "snapshot_id": child["snapshot_id"],
                            "target_id": source_target["id"],
                            "source_ref_id": source_target["source_refs"][0]["id"],
                        },
                    )
                    assert json.loads(source["source_data"]["text"]) == expected
                overview, overview_response = await call(
                    "render_dashboard", {"snapshot_id": child["snapshot_id"]}
                )
                image, image_response = await call(
                    "render_dashboard",
                    {"snapshot_id": child["snapshot_id"], "target_id": source_target["id"]},
                )
                assert image["snapshot_id"] == overview["snapshot_id"] == child["snapshot_id"]
                assert image["image"]["asset_digest"] == overview["image"]["asset_digest"]
                for response in (overview_response, image_response):
                    assert base64.b64decode(
                        next(
                            content.data for content in response.content if content.type == "image"
                        )
                    ).startswith(b"\x89PNG")
                member = next(target for target in captured_targets if target.get("links"))
                selected, _ = await call(
                    "read_dashboard",
                    {
                        "from_snapshot_id": child["snapshot_id"],
                        "target_id": member["id"],
                        "link_id": member["links"][0]["id"],
                        "freshness": "fresh",
                    },
                )
                selected_targets = await targets(selected)
                assert any(
                    "per-name panel" in target.get("label", "") for target in selected_targets
                )
                panel_refs = [
                    ref
                    for ref in fixture["references"]
                    if ref["pointer"].startswith("/armed_members/0/triggers/0/sources/")
                ]
                panel_source = next(
                    target
                    for target in selected_targets
                    if target.get("visible")
                    and any(
                        ref["id"] == item["id"]
                        for ref in panel_refs
                        for item in target.get("source_refs", [])
                    )
                )
                panel_image, _ = await call(
                    "render_dashboard",
                    {"snapshot_id": selected["snapshot_id"], "target_id": panel_source["id"]},
                )
                ownership = panel_image["image"]["occlusion"]
                assert ownership["sample_count"] == 25 and ownership["covered_points"] == 0
                assert ownership["target_owned_points"] > 0
                assert (
                    ownership["target_owned_points"] + ownership["background_points"]
                    == ownership["sample_count"]
                )
                assert panel_source["id"] in ownership["hit_target_ids"]
                for ref in source_refs:
                    hidden_rail_source = next(
                        target
                        for target in selected_targets
                        if any(item["id"] == ref["id"] for item in target.get("source_refs", []))
                    )
                    assert hidden_rail_source["visible"] is False
                    rejected = await client.call_tool(
                        "render_dashboard",
                        {
                            "snapshot_id": selected["snapshot_id"],
                            "target_id": hidden_rail_source["id"],
                        },
                    )
                    assert rejected.is_error and rejected.structured_content["error"]["code"] in {
                        "target_not_visible",
                        "target_occluded",
                    }
                invalid = await client.call_tool(
                    "read_dashboard",
                    {
                        "dashboard": "cockpit",
                        "asof": child["asof"],
                        "settings": {
                            "thesis_id": fixture["settings"]["thesis_id"],
                            "selected_name": "absent-synthetic-name",
                        },
                    },
                )
                assert (
                    invalid.is_error
                    and invalid.structured_content["error"]["code"] == "invalid_view_state"
                )

    with serve(dist) as base:
        asyncio.run(run(base))
