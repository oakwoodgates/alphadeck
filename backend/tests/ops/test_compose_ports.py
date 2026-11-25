"""Every published port of every compose stack binds loopback only, on both address families.

Prod's Postgres, API and app, and every dev, sig and fork stack, answer on this machine only: a bare ``"5544:5432"``
publishes on every interface, so another device on the network could reach the database with the compose password
and every API route (docs/SECURITY.md, "Published ports"). Each port is published twice,
``127.0.0.1:H:C`` and ``[::1]:H:C``: ``scripts/deploy.sh`` reads ``[::1]`` first (Docker Desktop's IPv4 loopback
forward intermittently resets on this host) and a browser's ``localhost`` resolves to either.

The compose files are parsed as text (the backend has no YAML dependency) and discovered, so a new overlay cannot
slip past the guard. An overlay's ``ports:`` must be ``!override``: Compose otherwise appends the list to the base
file's, and the merged stack would carry prod's ports too.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_LOOPBACKS = {"127.0.0.1", "[::1]"}
_BASE = "docker-compose.yml"

# The files that must exist and publish, with the (service -> host port token, container port) each carries. A
# fork's host ports come from ``.env.fork`` by interpolation, so its token is compared by prefix.
_EXPECTED: dict[str, dict[str, tuple[str, str]]] = {
    "docker-compose.yml": {
        "postgres": ("5544", "5432"),
        "backend": ("8000", "8000"),
        "frontend": ("8080", "80"),
    },
    "docker-compose.dev.yml": {
        "postgres": ("5545", "5432"),
        "backend": ("8001", "8000"),
        "frontend": ("8081", "80"),
    },
    "docker-compose.sig.yml": {
        "postgres": ("5546", "5432"),
        "backend": ("8002", "8000"),
        "frontend": ("8082", "80"),
    },
    "docker-compose.fork.yml": {
        "postgres": ("${FORK_PG_PORT", "5432"),
        "backend": ("${FORK_API_PORT", "8000"),
        "frontend": ("${FORK_APP_PORT", "80"),
    },
    "infra/docker-compose.yml": {"postgres": ("5544", "5432")},
}
# Prod's overlay publishes nothing (a read-only bind only): it is discovered and held to the same rule.
_NO_PORTS = {"docker-compose.prod.yml"}

_PORTS_KEY = re.compile(r"^(?P<indent> +)ports:(?P<rest>.*)$")
_SERVICE = re.compile(r"^ {2}(?P<name>[A-Za-z0-9_.-]+):\s*(?:#.*)?$")
_ITEM = re.compile(
    r"""^\s*-\s+(?:"(?P<dq>[^"]*)"|'(?P<sq>[^']*)'|(?P<bare>[^\s#"']+))\s*(?:#.*)?$"""
)
_SPEC = re.compile(
    r"^(?P<ip>127\.0\.0\.1|\[::1\]):(?P<host>\d+|\$\{[A-Z_]+:\?[^}]*\}):(?P<ctr>\d+)(?:/(?P<proto>tcp|udp))?$"
)


def _compose_files() -> list[Path]:
    return sorted(
        [*_REPO.glob("docker-compose*.yml"), *(_REPO / "infra").glob("docker-compose*.yml")]
    )


def _rel(path: Path) -> str:
    return path.relative_to(_REPO).as_posix()


def _blocks(text: str) -> list[tuple[str, str, list[str]]]:
    """Each ``ports:`` block as (service, the text after ``ports:``, its raw item lines).

    A block is the run of ``- ...`` lines under the key; blank and comment lines inside it are skipped.
    """
    found: list[tuple[str, str, list[str]]] = []
    service = ""
    in_services = False
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line and not line[0].isspace() and not line.startswith("#"):
            in_services = line.rstrip().startswith("services:")
        elif in_services and (m := _SERVICE.match(line)):
            service = m["name"]
        elif in_services and (m := _PORTS_KEY.match(line)):
            items: list[str] = []
            j = i + 1
            while j < len(lines):
                nxt = lines[j]
                if not nxt.strip() or nxt.lstrip().startswith("#"):
                    j += 1
                elif nxt.lstrip().startswith("- ") and len(nxt) - len(nxt.lstrip()) >= len(
                    m["indent"]
                ):
                    items.append(nxt)
                    j += 1
                else:
                    break
            found.append((service, m["rest"].strip(), items))
            i = j - 1
        i += 1
    return found


def _raw(item: str) -> str:
    """The entry's text without its quotes and comment; empty when the line is not a plain scalar item."""
    m = _ITEM.match(item)
    return next((g for g in m.group("dq", "sq", "bare") if g), "") if m else ""


def _specs(text: str) -> dict[str, list[tuple[str, str, str]]]:
    """Per service, every published entry as (ip, host port token, container port)."""
    out: dict[str, list[tuple[str, str, str]]] = {}
    for service, _rest, items in _blocks(text):
        for item in items:
            spec = _SPEC.match(_raw(item))
            if spec:
                out.setdefault(service, []).append((spec["ip"], spec["host"], spec["ctr"]))
    return out


def violations(text: str, *, overlay: bool) -> list[str]:
    """What breaks the loopback rule in one compose file's text; empty when it holds."""
    bad: list[str] = []
    for service, rest, items in _blocks(text):
        where = f"service {service!r}"
        keyword = rest.split("#", 1)[0].strip()
        if keyword not in ("", "!override"):
            bad.append(f"{where}: `ports:` must be a block list (found `ports: {keyword}`)")
        if overlay and keyword != "!override":
            bad.append(
                f"{where}: an overlay's `ports:` must be `!override`, or Compose appends it to the base list"
            )
        if not items:
            bad.append(f"{where}: an empty `ports:` block")
        seen: dict[tuple[str, str], set[str]] = {}
        for item in items:
            spec = _SPEC.match(_raw(item))
            if not spec:
                bad.append(f"{where}: {item.strip()!r} is not `127.0.0.1:H:C` or `[::1]:H:C`")
                continue
            seen.setdefault((spec["host"], spec["ctr"]), set()).add(spec["ip"])
        for (host, ctr), ips in seen.items():
            if ips != _LOOPBACKS:
                missing = sorted(_LOOPBACKS - ips)
                bad.append(f"{where}: {host}:{ctr} lacks its {' and '.join(missing)} entry")
    return bad


def test_every_compose_file_is_found() -> None:
    names = {_rel(p) for p in _compose_files()}
    expected = set(_EXPECTED) | _NO_PORTS
    assert expected <= names, f"compose files vanished: {sorted(expected - names)}"


@pytest.mark.parametrize("path", _compose_files(), ids=_rel)
def test_published_ports_bind_loopback_on_both_families(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    found = violations(text, overlay=path.name != _BASE and path.parent == _REPO)
    assert not found, f"{_rel(path)} publishes past loopback:\n" + "\n".join(found)


@pytest.mark.parametrize("path", _compose_files(), ids=_rel)
def test_each_stack_publishes_its_documented_ports(path: Path) -> None:
    rel = _rel(path)
    published = _specs(path.read_text(encoding="utf-8"))
    expected = _EXPECTED.get(rel, {})
    assert (
        rel in _EXPECTED or rel in _NO_PORTS
    ), f"{rel} is a new compose file: add it to _EXPECTED or _NO_PORTS"
    assert set(published) == set(
        expected
    ), f"{rel}: services that publish {sorted(published)}, expected {sorted(expected)}"
    for service, (host, ctr) in expected.items():
        entries = published[service]
        assert len(entries) == 2, f"{rel} {service}: {entries}"
        for ip, got_host, got_ctr in entries:
            assert (
                got_host.startswith(host) and got_ctr == ctr
            ), f"{rel} {service}: {ip}:{got_host}:{got_ctr}"


# The checker itself: each shape it must refuse, so a green real file is not a checker that sees nothing.
_GOOD = """\
services:
  postgres:
    ports:
      - "127.0.0.1:5544:5432" # a comment
      - "[::1]:5544:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
"""


def _with_ports(lines: str, key: str = "ports:") -> str:
    return f"services:\n  postgres:\n    {key}\n{lines}    volumes:\n      - x:/y\n"


def test_checker_accepts_a_loopback_pair() -> None:
    assert violations(_GOOD, overlay=False) == []
    assert violations(_GOOD.replace("ports:", "ports: !override"), overlay=True) == []


@pytest.mark.parametrize(
    ("lines", "needle"),
    [
        ('      - "5544:5432"\n', "is not `127.0.0.1:H:C`"),
        ('      - "0.0.0.0:5544:5432"\n', "is not `127.0.0.1:H:C`"),
        ('      - "[::]:5544:5432"\n', "is not `127.0.0.1:H:C`"),
        ('      - "192.168.1.5:5544:5432"\n', "is not `127.0.0.1:H:C`"),
        ("      - 5544:5432\n", "is not `127.0.0.1:H:C`"),
        ("      - target: 5432\n", "is not `127.0.0.1:H:C`"),
        ('      - "127.0.0.1:5544:5432"\n', "lacks its [::1] entry"),
        ('      - "[::1]:5544:5432"\n', "lacks its 127.0.0.1 entry"),
        ('      - "127.0.0.1:5544:5432"\n      - "[::1]:5545:5432"\n', "lacks its"),
        ("", "an empty `ports:` block"),
    ],
)
def test_checker_refuses_what_publishes_past_loopback(lines: str, needle: str) -> None:
    found = violations(_with_ports(lines), overlay=False)
    assert any(needle in v for v in found), found


def test_checker_refuses_an_inline_list() -> None:
    found = violations(_with_ports("", key='ports: ["127.0.0.1:5544:5432"]'), overlay=False)
    assert any("must be a block list" in v for v in found), found


def test_checker_refuses_an_overlay_that_appends() -> None:
    found = violations(_GOOD, overlay=True)
    assert any("must be `!override`" in v for v in found), found


def test_checker_reads_an_interpolated_host_port() -> None:
    text = _with_ports(
        '      - "127.0.0.1:${FORK_PG_PORT:?set it (and pass --env-file .env.fork)}:5432"\n'
        '      - "[::1]:${FORK_PG_PORT:?set it (and pass --env-file .env.fork)}:5432"\n',
        key="ports: !override",
    )
    assert violations(text, overlay=True) == []
