"""The background extraction's run artifact (V2 W3a): one WRITE-ONLY JSON file per run, ``data/extraction_runs/``.

The writers are the backfill CLI and the onboarding job's extraction step (later PRs), each with
``platform_values.extraction.ExtractionRun.to_json()``; the nightly leg puts the same block into its own pass
artifact instead. The reader is the Admin status line (a later PR), which shows the newest run's ``admin_line``.

The ``pipeline/edition_run_log.py`` discipline: a file, not a fact; no database; atomic writes (``tmp`` +
``os.replace``); fail-open (a write that fails is a logged ``None``, never a failed run); a directory of its own,
so no nightly-pass reader (the catch-up guard, the baselines, the Admin run history) can mistake it for a pass.

The artifact's keys: ``kind`` = ``platform_extraction``, ``run_kind`` (``backfill`` / ``onboarding`` / ...),
``status``, ``started_at`` / ``finished_at``, ``seconds``, the due counts, the outcome counts, ``failed`` and its
labels, ``busy``, ``skipped``, ``deferred``, ``stopped_by``, ``live_fetches``, ``attention`` and ``admin_line``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("alphadeck.platform_extraction")

# the repo's gitignored data/ == the container's /data (the compose `appdata` volume the backend shares)
EXTRACTION_RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "extraction_runs"

KIND = "platform_extraction"


def artifact_path(base_dir: Path, started_at: datetime) -> Path:
    # %H%M%S, no colons (legal on Windows too); the started-at instant names the run, so the name sort is the time
    # sort
    return base_dir / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"


def write_extraction_run(payload: dict[str, Any], *, base_dir: Path | None = None) -> Path | None:
    """Write one run artifact atomically. Fail-open: returns ``None`` (logged) when the write fails."""
    run_dir = base_dir or EXTRACTION_RUNS_DIR
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_path(run_dir, datetime.fromisoformat(payload["started_at"]))
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, path)
        return path
    except (
        Exception
    ):  # noqa: BLE001 — the record is best-effort; the values it describes are already written
        _log.exception("platform extraction run artifact write failed (fail-open)")
        return None


def latest_extraction_run(
    *, base_dir: Path | None = None, run_kind: str | None = None
) -> dict[str, Any] | None:
    """The newest parseable artifact (of ``run_kind`` when given), or ``None``. Fail-open per file (an unreadable
    one is skipped); no database, no network."""
    run_dir = base_dir or EXTRACTION_RUNS_DIR
    if not run_dir.exists():
        return None
    for p in sorted(run_dir.glob("*.json"), reverse=True):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — skip-unreadable, never a failed read
            continue
        if not isinstance(doc, dict) or doc.get("kind") != KIND:
            continue
        if run_kind is None or doc.get("run_kind") == run_kind:
            return doc
    return None


__all__ = [
    "EXTRACTION_RUNS_DIR",
    "KIND",
    "artifact_path",
    "latest_extraction_run",
    "write_extraction_run",
]
