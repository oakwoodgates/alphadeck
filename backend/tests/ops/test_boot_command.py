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


def test_the_api_bounds_its_in_flight_requests() -> None:
    """Security hardening PR 2 (docs/DEPLOY.md §Connections): the API's uvicorn runs ONE worker (the in-process job
    registries) with ``--limit-concurrency`` from ``ALPHADECK_UVICORN_LIMIT_CONCURRENCY`` (default 64), so in-flight
    requests, and with them request connections, stay below Postgres's connection limit."""
    text = (_REPO / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert "ENV ALPHADECK_UVICORN_LIMIT_CONCURRENCY=$UVICORN_LIMIT_CONCURRENCY" in text
    assert "ARG UVICORN_LIMIT_CONCURRENCY=64" in text
    cmd = next(ln for ln in text.splitlines() if ln.startswith("CMD ") and "uvicorn" in ln)
    assert "--workers 1" in cmd
    assert "--limit-concurrency ${ALPHADECK_UVICORN_LIMIT_CONCURRENCY:-64}" in cmd


def test_every_overlay_that_runs_uvicorn_bounds_its_in_flight_requests() -> None:
    """The dev, sig and fork overlays replace the image's command (to add ``--reload``); each replacement must keep
    the same in-flight bound. ``$$`` hands the expansion to the container's shell (Compose's escape), so the value
    comes from the container's ``ALPHADECK_UVICORN_LIMIT_CONCURRENCY`` exactly as the Dockerfile CMD reads it.
    """
    overlays: dict[str, list[str]] = {}
    for f in sorted(_REPO.glob("docker-compose*.yml")):
        lines = [
            ln
            for ln in f.read_text(encoding="utf-8").splitlines()
            if "uvicorn app.main:app" in ln and not ln.lstrip().startswith("#")
        ]
        if lines:
            overlays[f.name] = lines
    assert {"docker-compose.dev.yml", "docker-compose.fork.yml", "docker-compose.sig.yml"} <= set(
        overlays
    ), f"overlay commands vanished: {sorted(overlays)}"
    for name, lines in overlays.items():
        for ln in lines:
            assert (
                "--limit-concurrency $${ALPHADECK_UVICORN_LIMIT_CONCURRENCY:-64}" in ln
            ), f"{name}: {ln}"
