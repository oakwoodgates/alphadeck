"""The browser security headers in ``frontend/nginx.conf`` (security hardening PR 4a, spec item 5).

Every response nginx sends — the SPA's files and the proxied ``/api/``, ``/mcp`` and OAuth routes alike — carries
``X-Content-Type-Options``, ``Referrer-Policy``, the enforced ``frame-ancestors 'none'`` policy and the full
policy as ``Content-Security-Policy-Report-Only``, each with ``always`` (so error responses carry them too).

nginx's trap: ``add_header`` is inherited from the server block only into a ``location`` that sets NO
``add_header`` of its own. So the headers live on the server block, and any location that adds a header of its
own must repeat all of them. nginx itself cannot run in the test suite; this parses the file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_CONF = Path(__file__).resolve().parents[3] / "frontend" / "nginx.conf"

REPORT_ONLY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
    "font-src https://fonts.gstatic.com; img-src 'self' data:; connect-src 'self'; base-uri 'self'; "
    "object-src 'none'; form-action 'self'"
)
EXPECTED: dict[str, str] = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Content-Security-Policy-Report-Only": REPORT_ONLY_POLICY,
}
# the proxied locations named in the spec: each must be present (so the test covers them) and carry the headers
PROXIED = {"/api/", "= /mcp", "= /authorize", "= /token", "= /register", "= /revoke"}


@dataclass
class Block:
    name: str
    directives: list[list[str]] = field(default_factory=list)
    children: list[Block] = field(default_factory=list)

    def add_headers(self) -> dict[str, tuple[str, bool]]:
        out: dict[str, tuple[str, bool]] = {}
        for d in self.directives:
            if d[0] == "add_header":
                out[d[1]] = (d[2], d[-1] == "always")
        return out


_TOKEN = re.compile(r"""\s*(?:"((?:[^"\\]|\\.)*)"|'((?:[^'\\]|\\.)*)'|([{};])|([^\s{};"']+))""")


def _tokens(text: str) -> list[str]:
    text = "\n".join(line.split("#", 1)[0] for line in text.splitlines())
    out: list[str] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            if text[pos:].strip() == "":
                break
            raise AssertionError(f"unparseable nginx.conf near: {text[pos:pos + 40]!r}")
        pos = m.end()
        dq, sq, punct, word = m.groups()
        out.append(dq if dq is not None else sq if sq is not None else punct or word)
    return out


def _parse(tokens: list[str]) -> Block:
    root = Block("root")
    stack = [root]
    current: list[str] = []
    for tok in tokens:
        if tok == "{":
            child = Block(" ".join(current))
            stack[-1].children.append(child)
            stack.append(child)
            current = []
        elif tok == "}":
            stack.pop()
        elif tok == ";":
            stack[-1].directives.append(current)
            current = []
        else:
            current.append(tok)
    return root


def _server() -> Block:
    root = _parse(_tokens(_CONF.read_text(encoding="utf-8")))
    servers = [b for b in root.children if b.name == "server"]
    assert len(servers) == 1
    return servers[0]


def _locations(block: Block) -> list[Block]:
    out = []
    for child in block.children:
        if child.name.startswith("location"):
            out.append(child)
        out.extend(_locations(child))
    return out


def test_the_server_block_sets_every_header_with_always() -> None:
    headers = _server().add_headers()
    for name, value in EXPECTED.items():
        assert name in headers, f"missing add_header {name}"
        assert headers[name] == (value, True), f"{name}: {headers[name]}"


def test_no_hsts_here() -> None:
    # nginx serves plain port 80; HSTS belongs to the TLS layer in front of it
    assert "Strict-Transport-Security" not in _server().add_headers()


def test_the_proxied_locations_exist_so_the_inheritance_check_covers_them() -> None:
    names = {b.name.removeprefix("location ").strip() for b in _locations(_server())}
    assert PROXIED <= names, PROXIED - names


def test_no_location_drops_the_headers() -> None:
    """A location with an ``add_header`` of its own inherits none from the server block, so it must repeat
    every header; a location with none inherits them all."""
    for loc in _locations(_server()):
        own = loc.add_headers()
        if not own:
            continue
        for name, value in EXPECTED.items():
            assert own.get(name) == (value, True), f"{loc.name} drops {name}"


def test_the_parser_sees_a_location_that_would_drop_them() -> None:
    conf = (
        "server { add_header X-Content-Type-Options nosniff always;"
        " location /x { add_header Cache-Control no-store; } }"
    )
    server = _parse(_tokens(conf)).children[0]
    (loc,) = _locations(server)
    assert loc.add_headers() and "X-Content-Type-Options" not in loc.add_headers()
