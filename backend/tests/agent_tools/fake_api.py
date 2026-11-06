"""A fake Alpha Deck API over the synthetic fixtures: an httpx ``MockTransport`` handler for in-process tests and
a stdlib loopback HTTP server for the real stdio-client test. GET only; it records every request it answers.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import httpx

from tests.agent_tools import dashboard_fixtures as dfx
from tests.agent_tools import fixtures as fx

UNDATED = {"what-changed", "catalyst-calendar"}  # the two dashboards whose route takes no asof
THESIS_ID = fx.uid("thesis", 0)
SECURITY_ID = fx.uid("sec", "lookup")


class FakeApi:
    def __init__(
        self, theses: list[dict] | None = None, calls: dict[str, dict] | None = None
    ) -> None:
        self.theses = theses
        self.calls = (
            calls or {}
        )  # thesis id -> its /call payload (default: fx.call_payload for every card)
        self.requests: list[tuple[str, str, dict]] = []

    def route(self, method: str, path: str, query: dict[str, str]) -> tuple[int, object]:
        self.requests.append((method, path, query))
        if method != "GET":
            return 405, {"detail": "read-only fake"}
        known = {t["id"] for t in fx.theses_list(with_call=False)}
        if path == "/clock":
            return 200, fx.CLOCK
        if path == "/theses":
            with_call = query.get("with_call") == "true"
            if with_call and "asof" not in query:
                return 422, {"detail": "with_call=true requires asof (YYYY-MM-DD)"}
            listed = self.theses if self.theses is not None else fx.theses_list(with_call)
            return 200, listed
        if path == "/data-flags":
            return 200, fx.data_flags_all()
        if path == "/dashboards/ticker-lookup/resolve":
            if query.get("t", "").upper() == "TK01":
                return 200, fx.ticker_resolve()
            return 404, {"detail": "no company with ticker"}
        if path == f"/dashboards/ticker-lookup/{SECURITY_ID}":
            return 200, fx.ticker_lookup()
        parts = path.strip("/").split("/")
        if parts[0] == "dashboards" and len(parts) == 2 and parts[1] in dfx.PAYLOADS:
            if parts[1] not in UNDATED and "asof" not in query:
                return 422, {"detail": "asof: Field required"}
            if parts[1] == "rotation-map" and query.get("group_by") == "business_type":
                # the comparison's two dates: the end night serves the end variant (keys differ, not labels)
                if query.get("asof") == dfx.ASOF_END:
                    return 200, dfx.rotation_map_end("business_type")
                return 200, dfx.rotation_map("business_type")
            return 200, dfx.PAYLOADS[parts[1]]()
        if parts[0] == "theses" and len(parts) >= 2:
            if parts[1] not in known:
                return 404, {"detail": "thesis not found"}
            tail = "/".join(parts[2:])
            if tail == "call" and parts[1] in self.calls:
                return 200, self.calls[parts[1]]
            payload = {
                "": fx.thesis_detail,
                "call": fx.call_payload,
                "data-flags": fx.data_flags_one,
                "display-signals": fx.display_signals,
                "decisions": fx.decisions,
            }.get(tail)
            if payload is not None:
                return 200, payload()
        return 404, {"detail": "Not Found"}

    def handler(self, request: httpx.Request) -> httpx.Response:
        query = dict(parse_qsl(request.url.query.decode()))
        status, body = self.route(request.method, request.url.path, query)
        return httpx.Response(status, json=body)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


class LoopbackServer:
    """The fake API on ``127.0.0.1:<free port>`` in a thread (for a subprocess MCP server to read)."""

    def __init__(self, api: FakeApi) -> None:
        fake = api

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 (stdlib name)
                url = urlsplit(self.path)
                status, body = fake.route("GET", url.path, dict(parse_qsl(url.query)))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):  # quiet
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.base = f"http://127.0.0.1:{self.httpd.server_address[1]}"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self) -> LoopbackServer:
        self.thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
