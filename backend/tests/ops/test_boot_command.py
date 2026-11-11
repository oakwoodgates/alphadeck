"""Every boot command that runs ``db.migrate`` waits for Postgres first (``python -m db.wait &&``).

The files are discovered (the Dockerfile plus every repo-root ``docker-compose*.yml``), so a new overlay
cannot silently drop the wait.
"""

from __future__ import annotations

from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_KNOWN = {
    "backend/Dockerfile",
    "docker-compose.dev.yml",
    "docker-compose.fork.yml",
    "docker-compose.sig.yml",
}


def _boot_lines() -> dict[str, list[str]]:
    files = [_REPO / "backend" / "Dockerfile", *sorted(_REPO.glob("docker-compose*.yml"))]
    found: dict[str, list[str]] = {}
    for f in files:
        lines = [
            ln
            for ln in f.read_text(encoding="utf-8").splitlines()
            if "python -m db.migrate" in ln and not ln.lstrip().startswith("#")
        ]
        if lines:
            found[f.relative_to(_REPO).as_posix()] = lines
    return found


def test_boot_command_waits_before_migrate() -> None:
    found = _boot_lines()
    assert _KNOWN <= set(found), f"boot commands vanished: {_KNOWN - set(found)}"
    for rel, lines in found.items():
        for ln in lines:
            assert "python -m db.wait && python -m db.migrate" in ln, f"{rel}: {ln}"
