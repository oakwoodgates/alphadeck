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
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backtest import manifest as mf

# The store's home — the ``scoreboard/artifact.py`` idiom: the repo's gitignored ``data/`` locally,
# ``/data`` in the container. The CLI that WRITES a run needs the ``.[replay]`` extra the lean prod image
# deliberately lacks, so (from B6) compose overlays this subpath with a READ-ONLY bind: the container can
# serve runs but physically cannot write one.
DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "backtest"
RUNS_DIRNAME = "runs"
INDEX_NAME = "index.json"
INDEX_LOCK_NAME = "index.json.lock"


class RunSummary(BaseModel):
    """One registry row — enough to pick a run without opening it."""

    run_id: str
    # the curve this run is a point of (S1) — see `manifest.make_pass_id`; None for a standalone run
    pass_id: str | None = None
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


class RunDirExists(FileExistsError):
    """This run id already has a directory.

    A subclass of ``FileExistsError`` so nothing that already catches that changes behavior — what changes
    is the MESSAGE. A bare ``FileExistsError`` naming a temp path tells a reader that something collided
    but not what, and the answer ("every component of the id agreed, inside one second") is not guessable
    from the path. It cost a real debugging detour once; the error now says it."""


def create_run_dir(run_id: str, root: str | Path | None = None) -> Path:
    """Create this run's directory. RAISES ``RunDirExists`` if it already exists — a run is immutable, so
    silently reusing one would overwrite an artifact somebody may already have cited."""
    path = runs_root(root) / run_id
    try:
        path.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        parts = " - ".join(mf.RUN_ID_PARTS)
        raise RunDirExists(
            f"run {run_id!r} already exists at {path}. A run id is composed of: {parts} — so this "
            f"collided only because EVERY one of those agreed with an existing run, inside the same "
            f"second. A run is immutable and may already have been cited, so this refuses rather than "
            f"overwriting it: change the hypothesis, move a dial, or wait a second."
        ) from exc
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


@contextmanager
def _index_lock(root: str | Path | None = None, *, timeout_s: float = 30.0) -> Iterator[None]:
    """Serialize the registry's READ-MODIFY-WRITE across processes.

    ``_write_index`` is already atomic, so no reader ever sees a truncated file. That is not enough once
    runs are launched CONCURRENTLY (S1): two processes each read the index, each append their own row, and
    each write — and the second write silently drops the first's row. The registry's whole job is to count
    trials, so losing one is the worst failure it has, and it would be invisible (every run directory and
    manifest is still on disk; only the listing is short).

    An exclusive-create lock file, because it is the one primitive that behaves the same on Windows and
    POSIX without a dependency. A lock older than ``timeout_s`` is treated as ABANDONED and broken: a run
    that died holding it must not wedge every future run, and the worst case of breaking it is the
    lost-row race we already have without any lock at all."""
    path = Path(root or DEFAULT_ROOT) / INDEX_LOCK_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    fd: int | None = None
    while fd is None:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.monotonic() > deadline:
                # abandoned, not contended: break it and take the lock on the next turn
                try:
                    age = time.time() - path.stat().st_mtime
                    if age > timeout_s:
                        path.unlink(missing_ok=True)
                except OSError:
                    pass
                deadline = time.monotonic() + timeout_s
            time.sleep(0.05)
    try:
        yield
    finally:
        os.close(fd)
        path.unlink(missing_ok=True)


def register_run(summary: RunSummary, root: str | Path | None = None) -> Path:
    """Add (or replace) this run's row and rewrite the registry, newest first.

    Replace-by-run_id rather than append-only so a re-registration is idempotent — re-registering the same
    run must not duplicate a trial, which would inflate exactly the count the registry exists to keep
    honest. The whole read-modify-write is under ``_index_lock`` so that CONCURRENT runs (a windowed pass
    launches several at once) cannot drop each other's rows."""
    with _index_lock(root):
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
