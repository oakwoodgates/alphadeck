"""The run STORE — where runs live, and the registry that lists them.

    data/backtest/
      index.json                     the registry: one summary row per completed run, newest first
      runs/<run_id>/
        manifest.json                what this run is (backtest/manifest.py)
        episodes.parquet             the arm episodes
        outcomes.parquet             those episodes scored against realized prices
        metrics.json                 the metric set
        *.parquet                    the frozen fact mirror the sweep read

Two rules, and they are the whole design:

**A run directory is created fresh and never reused.** ``mkdir(exist_ok=False)`` — not tidiness, the
integrity property. ``replay/run.py``'s ``--out`` overwrites in place, which is why a metrics.json could sit
beside a previous run's episodes (the F3 bug, fixed by always writing). Here the failure mode cannot arise:
a run's bytes are written once and are then immutable, so a run id in a PR description means exactly one set
of numbers, forever.

**The registry is rewritten atomically.** A listing that is truncated mid-write reads as "some runs
vanished", which is the worst possible failure for an artifact whose job is to count trials.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

# The store's home — the ``scoreboard/artifact.py`` idiom: the repo's gitignored ``data/`` locally,
# ``/data`` in the container. The CLI that WRITES a run needs the ``.[replay]`` extra the lean prod image
# deliberately lacks, so (from B6) compose overlays this subpath with a READ-ONLY bind: the container can
# serve runs but physically cannot write one.
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "backtest"
RUNS_DIRNAME = "runs"
INDEX_NAME = "index.json"


class RunSummary(BaseModel):
    """One registry row — enough to pick a run without opening it."""

    run_id: str
    created_at: str
    hypothesis: str | None = None
    decision_rule: str | None = None
    config_short: str
    config_hash: str
    clock: str
    known_at_mode: str
    window_start: str
    window_end: str
    n_theses: int = 0
    n_episodes: int = 0
    # Which dials this run moved off the default — the registry's answer to "count your trials": how many
    # runs have touched THIS dial is a listing away, rather than an archaeology exercise.
    dials_moved: list[str] = Field(default_factory=list)


class RunIndex(BaseModel):
    runs: list[RunSummary] = Field(default_factory=list)


def runs_root(root: str | Path | None = None) -> Path:
    return Path(root or DEFAULT_ROOT) / RUNS_DIRNAME


def index_path(root: str | Path | None = None) -> Path:
    return Path(root or DEFAULT_ROOT) / INDEX_NAME


def create_run_dir(run_id: str, root: str | Path | None = None) -> Path:
    """Create this run's directory. RAISES ``FileExistsError`` if it already exists — a run is immutable,
    so silently reusing one would overwrite an artifact somebody may already have cited."""
    path = runs_root(root) / run_id
    path.mkdir(parents=True, exist_ok=False)
    return path


def read_index(root: str | Path | None = None) -> RunIndex:
    """The registry, or an EMPTY one when it is absent or unreadable.

    Absence rather than an exception, and the same reasoning as the Scoreboard artifact's reader: a missing
    registry means "no runs on this stack", which is a legitimate state (prod), not an outage."""
    path = index_path(root)
    if not path.is_file():
        return RunIndex()
    try:
        return RunIndex.model_validate_json(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — an unreadable registry is absence, not an outage
        return RunIndex()


def _write_index(index: RunIndex, root: str | Path | None = None) -> Path:
    """Rewrite the registry ATOMICALLY: a temp file in the same directory, then ``os.replace``.

    Same directory so the replace is a rename within one filesystem (atomic on POSIX and on Windows for an
    existing destination). A reader therefore sees either the whole old registry or the whole new one, never
    a truncated middle — which would read as runs having disappeared."""
    path = index_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(index.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    os.replace(tmp, path)
    return path


def register_run(summary: RunSummary, root: str | Path | None = None) -> Path:
    """Add (or replace) this run's row and rewrite the registry, newest first.

    Replace-by-run_id rather than append-only so a re-registration is idempotent — re-registering the same
    run must not duplicate a trial, which would inflate exactly the count the registry exists to keep
    honest."""
    index = read_index(root)
    rows = [r for r in index.runs if r.run_id != summary.run_id]
    rows.append(summary)
    rows.sort(key=lambda r: (r.created_at, r.run_id), reverse=True)
    return _write_index(RunIndex(runs=rows), root)


def list_runs(root: str | Path | None = None) -> list[RunSummary]:
    """Every registered run, newest first."""
    return list(read_index(root).runs)


def run_dir(run_id: str, root: str | Path | None = None) -> Path | None:
    """The directory for ``run_id``, or ``None`` when it does not exist. ``run_id`` is validated against the
    registry's own naming shape rather than joined blindly — a path component from a caller must never be
    able to walk out of the store."""
    if not run_id or "/" in run_id or "\\" in run_id or run_id.startswith("."):
        return None
    path = runs_root(root) / run_id
    return path if path.is_dir() else None


def write_metrics(run_dir_path: str | Path, payload: Any, name: str = "metrics.json") -> Path:
    """Write one JSON artifact into the run directory (a Pydantic model or a plain mapping).

    ``name`` because a run now writes two: ``metrics.json`` (the engine's seven claim-tied metrics) and
    ``pooled.json`` (the algorithm-level view with its nulls). Same writer so both get the same encoding
    and line-ending discipline rather than one of them drifting."""
    path = Path(run_dir_path) / name
    text = (
        payload.model_dump_json(indent=2)
        if isinstance(payload, BaseModel)
        else json.dumps(payload, indent=2, sort_keys=True, default=str)
    )
    path.write_text(text + "\n", encoding="utf-8", newline="\n")
    return path
