from __future__ import annotations

import logging
from pathlib import Path

from scoreboard.schema import ReplaySnapshot

_log = logging.getLogger("alphadeck.scoreboard")

# The replay-panel artifact home — the draft-run-log pattern (workbench/draft_run_log.py): the
# repo's gitignored ``data/`` locally, ``/data`` in the container. The CLI that WRITES it runs on
# the HOST dev venv (it needs the .[replay] extra, which the lean prod image deliberately lacks),
# so compose overlays this one subpath with a READ-ONLY bind of the host dir — the container can
# serve the artifact but physically cannot write it. Latest-only by design (operator-locked): the
# snapshot is deterministic per (SoR, pin, window, cfg), so a re-run IS the history.
_DEFAULT_HOME = Path(__file__).resolve().parents[2] / "data" / "scoreboard_replay"
ARTIFACT_NAME = "latest.json"


def write_snapshot(snap: ReplaySnapshot, base_dir: Path | None = None) -> Path:
    home = base_dir or _DEFAULT_HOME
    home.mkdir(parents=True, exist_ok=True)
    path = home / ARTIFACT_NAME
    path.write_text(snap.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return path


def read_snapshot(base_dir: Path | None = None) -> ReplaySnapshot | None:
    """The endpoint's read: ``None`` when no artifact exists OR it fails validation (logged) —
    a stale/corrupt file must render as "no history available", never a 500."""
    path = (base_dir or _DEFAULT_HOME) / ARTIFACT_NAME
    if not path.is_file():
        return None
    try:
        return ReplaySnapshot.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — an unreadable artifact is absence, not an outage
        _log.warning("scoreboard replay artifact unreadable at %s: %s", path, e)
        return None
