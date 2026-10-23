"""The editions nightly page job's run artifact — one WRITE-ONLY JSON file per run, ``data/edition_runs/``.

The writer is ``editions.nightly`` (the Scoreboard editions S5 job the cron sidecar fires after the nightly
backup); the reader is the Admin status endpoint, which shows the newest APPLIED run's one plain-English line.
This module lives outside ``editions/`` on purpose: nothing outside that package may import it (the
import-graph test), and the API image is lean, so the Admin reads the file here with the stdlib alone.

Same discipline as ``pipeline/cron_run_log.py``: a file, not a fact; no DB; atomic writes (``tmp`` +
``os.replace``); fail-open (a write that fails is a logged ``None``, never a failed job); a separate
directory from ``cron_runs`` / ``onboard_runs``, so no nightly-pass reader (the catch-up guard, the baselines,
the Admin run history) can ever mistake an editions run for a pass.

The artifact's keys (``editions.nightly.NightlyResult.payload``): ``kind`` = ``editions_nightly``, ``mode``
(``apply`` / ``dry_run``), ``status`` (``done`` / ``skipped`` / ``crashed``), ``started_at`` /
``finished_at``, ``end`` (the night it paged through), ``known_by`` (the export's watermark), the edition,
the gate, ``pages`` / ``held`` / ``noted`` / ``archived`` per thesis, ``entries_appended``, ``ink``,
``self_check``, ``timings``, ``counts_before`` / ``counts_after`` and ``admin_line``.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

_log = logging.getLogger("alphadeck.editions")

# the repo's gitignored data/ == the container's /data (the compose `appdata` volume the backend shares)
EDITION_RUNS_DIR = Path(__file__).resolve().parents[2] / "data" / "edition_runs"

KIND = "editions_nightly"
MODE_APPLY = "apply"
MODE_DRY_RUN = "dry_run"


def artifact_path(base_dir: Path, started_at: datetime) -> Path:
    # %H%M%S, no colons — legal on Windows too; the started-at instant names the run (the name sort is the
    # time sort)
    return base_dir / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"


def write_edition_run(payload: dict[str, Any], *, base_dir: Path | None = None) -> Path | None:
    """Write one run artifact atomically. Fail-open: returns ``None`` (logged) when the write fails."""
    run_dir = base_dir or EDITION_RUNS_DIR
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
        path = artifact_path(run_dir, datetime.fromisoformat(payload["started_at"]))
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, path)
        return path
    except Exception:  # noqa: BLE001 — the record is best-effort; the pages it describes are not
        _log.exception("editions run artifact write failed (fail-open)")
        return None


def latest_edition_run(
    *, base_dir: Path | None = None, mode: str = MODE_APPLY
) -> dict[str, Any] | None:
    """The newest parseable artifact of ``mode`` (default: an APPLIED run — a dry run is a look, never what
    the Admin reports), or ``None``. Fail-open per file (an unreadable one is skipped); no DB, no network.
    """
    run_dir = base_dir or EDITION_RUNS_DIR
    if not run_dir.exists():
        return None
    for p in sorted(run_dir.glob("*.json"), reverse=True):
        try:
            doc = json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — skip-unreadable, never a failed read
            continue
        if isinstance(doc, dict) and doc.get("kind") == KIND and doc.get("mode") == mode:
            return doc
    return None
