"""The request-body ceiling per route in nginx (security hardening PR 3, item 5).

The app's own limits (``domain/text_types.py``) bound each field; nginx bounds the body before it reaches the app:
1 MB by default (nginx's own default, set explicitly at server level), 25 MB only on the two EXACT locations that
legitimately carry a large body: the triage-session autosave (a big discovery draft serializes to several MB) and the
promote (a full-replace resend of the whole card). Parsed from ``frontend/nginx.conf`` (nginx itself is not run).

PR 4a's header rule (a ``location`` that sets its own ``add_header`` must repeat all four security headers) is kept by
construction: these locations set no ``add_header`` at all, so they inherit the server's.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CONF = Path(__file__).resolve().parents[3] / "frontend" / "nginx.conf"

PROMOTE = "/api/workbench/theses"
TRIAGE = "/api/workbench/theses/0b5c6e0e-6f43-4a51-9c1e-2b7d3f0a9e11/triage-session"


def _strip_comments(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


def _blocks(text: str) -> tuple[str, list[tuple[str, str]]]:
    """(server-level directives, [(location header, location body)]) for the one ``server`` block."""
    text = _strip_comments(text)
    start = text.index("server {") + len("server {")
    depth, i = 1, start
    while depth:
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        i += 1
    server = text[start : i - 1]
    locations: list[tuple[str, str]] = []
    top: list[str] = []
    pos = 0
    # a header may be quoted (nginx requires it when a regex holds "{" or "}")
    header_re = r"""location\s+((?:"[^"]*"|'[^']*'|[^{"'])+?)\s*\{"""
    for m in re.finditer(header_re, server):
        if m.start() < pos:
            continue
        top.append(server[pos : m.start()])
        depth, j = 1, m.end()
        while depth:
            depth += {"{": 1, "}": -1}.get(server[j], 0)
            j += 1
        locations.append((m.group(1).strip(), server[m.end() : j - 1]))
        pos = j
    top.append(server[pos:])
    return "".join(top), locations


def _body_size(directives: str) -> str | None:
    m = re.search(r"client_max_body_size\s+(\S+)\s*;", directives)
    return m.group(1) if m else None


def _match(header: str, path: str) -> bool:
    parts = header.split(None, 1)
    if len(parts) == 2:
        parts[1] = parts[1].strip("\"'")
    if parts[0] == "=":
        return path == parts[1]
    if parts[0] in ("~", "~*"):
        return re.search(parts[1], path, re.IGNORECASE if parts[0] == "~*" else 0) is not None
    if parts[0] == "^~":
        return path.startswith(parts[1])
    return path.startswith(parts[0])


def _location_for(locations: list[tuple[str, str]], path: str) -> tuple[str, str]:
    """nginx's choice: an exact match wins; then a ``^~`` prefix that is the longest prefix; then the first regex
    that matches; else the longest prefix."""
    exact = [loc for loc in locations if loc[0].startswith("= ") and _match(loc[0], path)]
    if exact:
        return exact[0]
    prefixes = [
        loc for loc in locations if not loc[0].startswith(("=", "~")) and _match(loc[0], path)
    ]
    longest = max(prefixes, key=lambda loc: len(loc[0].split()[-1]), default=None)
    if longest is not None and longest[0].startswith("^~"):
        return longest
    for loc in locations:
        if loc[0].startswith("~") and _match(loc[0], path):
            return loc
    assert longest is not None, path
    return longest


@pytest.fixture(scope="module")
def conf() -> tuple[str, list[tuple[str, str]]]:
    return _blocks(CONF.read_text(encoding="utf-8"))


def test_the_server_default_body_ceiling_is_one_megabyte(conf):
    server, _ = conf
    assert _body_size(server) == "1m"


@pytest.mark.parametrize("path", [PROMOTE, TRIAGE])
def test_only_the_promote_and_triage_session_locations_allow_25_megabytes(conf, path):
    _, locations = conf
    header, body = _location_for(locations, path)
    assert _body_size(body) == "25m", (path, header)


def test_no_other_location_raises_the_ceiling(conf):
    _, locations = conf
    raised = [h for h, body in locations if _body_size(body) not in (None, "1m")]
    assert sorted(raised) == sorted(
        [
            "= /api/workbench/theses",
            '~ "^/api/workbench/theses/[0-9a-fA-F-]{36}/triage-session$"',
        ]
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/workbench/theses/0b5c6e0e-6f43-4a51-9c1e-2b7d3f0a9e11/terms/edit",
        "/api/theses/0b5c6e0e-6f43-4a51-9c1e-2b7d3f0a9e11/catalysts",
        "/api/auth/login",
        "/api/workbench/theses/0b5c6e0e-6f43-4a51-9c1e-2b7d3f0a9e11/triage-session/x",
        "/api/workbench/thesesX",
        "/register",
        "/mcp",
    ],
)
def test_every_other_route_keeps_the_one_megabyte_default(conf, path):
    _, locations = conf
    _, body = _location_for(locations, path)
    assert _body_size(body) is None  # inherits the server's 1m


@pytest.mark.parametrize("path", [PROMOTE, TRIAGE])
def test_the_large_body_locations_proxy_like_the_api_location(conf, path):
    """Same upstream path (the /api prefix stripped), Host header and 300 s timeouts as ``location /api/``."""
    _, locations = conf
    _, api = _location_for(locations, "/api/anything")
    _, body = _location_for(locations, path)
    for directive in (
        "proxy_set_header Host $host;",
        "proxy_read_timeout 300s;",
        "proxy_send_timeout 300s;",
    ):
        assert directive in " ".join(api.split()), directive
        assert directive in " ".join(body.split()), (path, directive)
    flat = " ".join(body.split())
    assert "proxy_pass http://backend:8000" in flat
    if "proxy_pass http://backend:8000/workbench/theses;" not in flat:
        assert "rewrite ^/api/(.*)$ /$1 break;" in flat


@pytest.mark.parametrize("path", [PROMOTE, TRIAGE])
def test_the_large_body_locations_set_no_headers_of_their_own(conf, path):
    """PR 4a's rule: a location with its own ``add_header`` drops the server's security headers. These add none."""
    _, locations = conf
    _, body = _location_for(locations, path)
    assert "add_header" not in body
