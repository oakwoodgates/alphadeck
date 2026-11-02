"""Opt-in real Chromium tests with synthetic data only: ALPHADECK_MCP_BROWSER_TESTS=1."""

import asyncio
import functools
import os
import struct
import threading
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("jsonschema")
pytestmark = pytest.mark.skipif(
    os.environ.get("ALPHADECK_MCP_BROWSER_TESTS") != "1", reason="Opt-in Chromium integration"
)

from agent_access.catalog import Catalog  # noqa: E402
from agent_access.errors import AccessError  # noqa: E402
from agent_access.renderer import FrozenRenderer  # noqa: E402

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
