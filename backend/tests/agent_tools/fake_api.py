"""A fake Alpha Deck API over the synthetic fixtures: an httpx ``MockTransport`` handler for in-process tests and
a stdlib loopback HTTP server for the real stdio-client test. GET only; it records every request it answers.
"""

from __future__ import annotations

import json
import re
import threading
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import httpx

from tests.agent_tools import admin_fixtures as afx
from tests.agent_tools import dashboard_fixtures as dfx
from tests.agent_tools import fixtures as fx
from tests.agent_tools import research_fixtures as rfx

UNDATED = {"what-changed", "catalyst-calendar"}  # the two dashboards whose route takes no asof
THESIS_ID = fx.uid("thesis", 0)
SECURITY_ID = fx.uid("sec", "lookup")


# --- the routes, in the order they are tried: (method, exact path | compiled pattern, handler) ----------------------
# A handler takes (api, query, **named groups) and returns (status, body), or None to let the next route try.


def _clock(api: FakeApi, query: dict[str, str]):
    return 200, fx.CLOCK


def _theses(api: FakeApi, query: dict[str, str]):
    with_call = query.get("with_call") == "true"
    if with_call and "asof" not in query:
        return 422, {"detail": "with_call=true requires asof (YYYY-MM-DD)"}
    listed = api.theses if api.theses is not None else fx.theses_list(with_call)
    return 200, listed


def _data_flags(api: FakeApi, query: dict[str, str]):
    return 200, fx.data_flags_all()


def _ticker_resolve(api: FakeApi, query: dict[str, str]):
    if query.get("t", "").upper() == "TK01":
        return 200, fx.ticker_resolve()
    return 404, {"detail": "no company with ticker"}


def _ticker_lookup(api: FakeApi, query: dict[str, str]):
    return 200, fx.ticker_lookup()


def _dashboard(api: FakeApi, query: dict[str, str], slug: str):
    if slug not in dfx.PAYLOADS:
        return None
    if slug not in UNDATED and "asof" not in query:
        return 422, {"detail": "asof: Field required"}
    if slug == "rotation-map" and query.get("group_by") == "business_type":
        # the comparison's two dates: the end night serves the end variant (keys differ, not labels)
        if query.get("asof") == dfx.ASOF_END:
            return 200, dfx.rotation_map_end("business_type")
        return 200, dfx.rotation_map("business_type")
    return 200, dfx.PAYLOADS[slug]()


def _thesis(api: FakeApi, query: dict[str, str], thesis_id: str, tail: str):
    if thesis_id not in {t["id"] for t in fx.theses_list(with_call=False)}:
        return 404, {"detail": "thesis not found"}
    tail = tail.strip("/")
    if tail == "" and thesis_id in api.details:
        return 200, api.details[thesis_id]
    if tail == "call" and thesis_id in api.calls:
        return 200, api.calls[thesis_id]
    payload = {
        "": fx.thesis_detail,
        "call": fx.call_payload,
        "data-flags": fx.data_flags_one,
        "display-signals": fx.display_signals,
        "decisions": fx.decisions,
    }.get(tail)
    return (200, payload()) if payload is not None else None


def _known(thesis_id: str) -> bool:
    return thesis_id in {t["id"] for t in fx.theses_list(with_call=False)}


def _scored(api: FakeApi, query: dict[str, str], thesis_id: str):
    if not _known(thesis_id):
        return 404, {"detail": "thesis not found"}
    if "asof" not in query:
        return 422, {"detail": "asof: Field required"}
    return 200, rfx.scored()


def _runs(api: FakeApi, query: dict[str, str], thesis_id: str):
    if not _known(thesis_id):
        return 404, {"detail": "thesis not found"}
    if api.runs_disabled:
        return 404, {"detail": "run loader is disabled"}
    return 200, rfx.runs() if thesis_id == rfx.THESIS_ID else []


def _run(api: FakeApi, query: dict[str, str], thesis_id: str, run_id: str):
    if not _known(thesis_id):
        return 404, {"detail": "thesis not found"}
    if api.runs_disabled:
        return 404, {"detail": "run loader is disabled"}
    drafts = {rfx.RUN_LATEST_ID: rfx.run_latest, rfx.RUN_SMALL_ID: rfx.run_small}
    if thesis_id != rfx.THESIS_ID or run_id not in drafts:
        return 404, {"detail": "saved run not found"}
    return 200, drafts[run_id]()


def _extract(api: FakeApi, query: dict[str, str], security_id: str):
    if security_id != SECURITY_ID:
        return 404, {"detail": "security not found"}
    return 200, rfx.extract(thesis_scoped="thesis_id" in query)


def _onboarding(api: FakeApi, query: dict[str, str], thesis_id: str):
    if not _known(thesis_id):
        return 404, {"detail": "thesis not found"}
    return 200, rfx.onboarding()


def _auth_me(api: FakeApi, query: dict[str, str]):
    if (
        api.me_role is None
    ):  # the default: no login door, so a stdio server stays the plain principal
        return None
    return 200, {"id": "00000000-0000-0000-0000-00000000fa4e", "role": api.me_role}


def _admin(api: FakeApi, query: dict[str, str], build: Callable[[], dict]):
    if not api.steward:  # the real route's ``require_super_admin``
        return 403, {"detail": "stewards only"}
    return 200, build()


def _admin_splits(api: FakeApi, query: dict[str, str]):
    return _admin(api, query, lambda: api.splits if api.splits is not None else afx.splits())


def _admin_status(api: FakeApi, query: dict[str, str]):
    return _admin(api, query, lambda: api.status if api.status is not None else afx.status())


ROUTES: tuple[tuple[str, str | re.Pattern[str], Callable[..., tuple[int, object] | None]], ...] = (
    ("GET", "/clock", _clock),
    ("GET", "/auth/me", _auth_me),
    ("GET", "/admin/splits", _admin_splits),
    ("GET", "/admin/status", _admin_status),
    ("GET", "/theses", _theses),
    ("GET", "/data-flags", _data_flags),
    ("GET", "/dashboards/ticker-lookup/resolve", _ticker_resolve),
    ("GET", f"/dashboards/ticker-lookup/{SECURITY_ID}", _ticker_lookup),
    ("GET", re.compile(r"^/*workbench/theses/(?P<thesis_id>[^/]+)/scored$"), _scored),
    ("GET", re.compile(r"^/*workbench/theses/(?P<thesis_id>[^/]+)/onboarding$"), _onboarding),
    ("GET", re.compile(r"^/*workbench/theses/(?P<thesis_id>[^/]+)/runs$"), _runs),
    (
        "GET",
        re.compile(r"^/*workbench/theses/(?P<thesis_id>[^/]+)/runs/(?P<run_id>[^/]+)$"),
        _run,
    ),
    ("GET", re.compile(r"^/*workbench/securities/(?P<security_id>[^/]+)/extract$"), _extract),
    ("GET", re.compile(r"^/*dashboards/(?P<slug>[^/]+)/*$"), _dashboard),
    ("GET", re.compile(r"^/*theses/(?P<thesis_id>[^/]+)(?:/(?P<tail>.*))?$"), _thesis),
)


class FakeApi:
    def __init__(
        self,
        theses: list[dict] | None = None,
        calls: dict[str, dict] | None = None,
        details: dict[str, dict] | None = None,
        runs_disabled: bool = False,
        steward: bool = True,
        me_role: str | None = None,
        splits: dict | None = None,
        status: dict | None = None,
    ) -> None:
        # GET /auth/me answers this role (None = 404): what a stdio server learns its scopes from
        self.me_role = me_role
        # the caller is a super admin: /admin/* answers 403 "stewards only" otherwise
        self.steward = steward
        self.splits = splits  # the GET /admin/splits payload (default: afx.splits())
        self.status = status  # the GET /admin/status payload (default: afx.status())
        self.theses = theses
        self.details = (
            details or {}
        )  # thesis id -> its GET /theses/{id} payload (default: fx.thesis_detail)
        self.runs_disabled = runs_disabled  # the run loader off: the two /runs reads answer 404
        self.calls = (
            calls or {}
        )  # thesis id -> its /call payload (default: fx.call_payload for every card)
        self.requests: list[tuple[str, str, dict]] = []

    def route(self, method: str, path: str, query: dict[str, str]) -> tuple[int, object]:
        self.requests.append((method, path, query))
        if method != "GET":
            return 405, {"detail": "read-only fake"}
        for route_method, matcher, handler in ROUTES:
            if route_method != method:
                continue
            if isinstance(matcher, str):
                groups = {} if path == matcher else None
            else:
                hit = matcher.match(path)
                groups = hit.groupdict(default="") if hit else None
            if groups is None:
                continue
            answer = handler(self, query, **groups)
            if answer is not None:  # None = this route declines; a later route may answer
                return answer
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
