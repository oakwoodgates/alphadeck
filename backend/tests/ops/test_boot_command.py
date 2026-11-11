"""Every boot command that runs ``db.migrate`` waits for Postgres first (``python -m db.wait &&``)."""

from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
_FILES = [
    "backend/Dockerfile",
    "docker-compose.dev.yml",
    "docker-compose.fork.yml",
    "docker-compose.sig.yml",
]


@pytest.mark.parametrize("rel", _FILES)
def test_boot_command_waits_before_migrate(rel: str) -> None:
    lines = [
        ln
        for ln in (_REPO / rel).read_text(encoding="utf-8").splitlines()
        if "python -m db.migrate" in ln and not ln.lstrip().startswith("#")
    ]
    assert lines, f"{rel} no longer runs db.migrate"
    for ln in lines:
        assert "python -m db.wait && python -m db.migrate" in ln, ln
