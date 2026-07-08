"""Read saved draft-run artifacts back for the run-loader picker (the DISCOVER-stage cost-saver).

The counterpart to ``draft_run_log`` (the WRITER): a small, READ-ONLY reader that lets a gated picker load a
saved run back into the editable workbench instead of paying for a fresh Opus draft. Deliberately kept OUT of
``draft_run_log`` so the writer's "write-only-on-the-spine-path" bound stays legible, and it lives in the same
``workbench`` package so it shares the writer's single path source.

INVARIANT-GRADE BOUNDS (a file is a seed, not a fact):

- **Non-spine.** Reading a run and handing it to the editor seeds FE state only. Nothing here writes a fact or
  a placement; the operator's promote stays the ONLY spine writer. This module never opens a DB connection.
- **Plain dicts, one-way layering.** Returns plain dicts / lists — it never imports the ``app`` wire schema
  (``ChainDraftOut`` lives in ``app.schemas_api``); the ROUTER validates the returned draft dict into the wire
  model. Same one-way layering ``draft_run_log`` keeps.
- **Fail-open read.** A corrupt / partial artifact is skipped (list) or a missing/absent one is ``None``
  (detail) — never a 500 from a bad file on disk.
- **Traversal-safe.** ``read_run`` serves only a ``run_id`` that is an ACTUAL entry in the runs dir (a
  membership check — a ``../`` id can never match a listed filename).

The path source is ``draft_run_log._DEFAULT_RUNS`` read at CALL TIME, so the app-suite conftest's autouse
redirect (which monkeypatches that attribute) covers this reader too — one store, one source.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from uuid import UUID

from workbench import draft_run_log

_log = logging.getLogger("alphadeck.workbench")


def _runs_dir(thesis_id: UUID, base_dir: Path | None) -> Path:
    # read the writer's default at call time so a monkeypatch of it (tests) redirects the reader too
    return (base_dir or draft_run_log._DEFAULT_RUNS) / str(thesis_id)


def list_runs(thesis_id: UUID, *, base_dir: Path | None = None) -> list[dict[str, Any]]:
    """List a thesis's saved draft-run artifacts, NEWEST-FIRST. Pure directory read → plain summary dicts
    (``run_id`` = filename stem, plus the cheap label fields already in the payload: ``written_at``,
    ``job_id``, and placement/segment counts). No runs (missing dir) → ``[]``; a corrupt file is skipped.
    """
    d = _runs_dir(thesis_id, base_dir)
    if not d.is_dir():
        return []
    out: list[dict[str, Any]] = []
    # filenames are ``<UTC-timestamp>-<job_id>.json`` → reverse-lexicographic == newest-first
    for f in sorted(d.glob("*.json"), reverse=True):
        try:
            payload = json.loads(f.read_text(encoding="utf-8"))
            draft = payload.get("draft") or {}
            out.append(
                {
                    "run_id": f.stem,
                    "written_at": payload.get("written_at"),
                    "job_id": payload.get("job_id"),
                    "placement_count": len(draft.get("placements") or []),
                    "segment_count": len(draft.get("segments") or []),
                }
            )
        except Exception:  # noqa: BLE001 — a corrupt/partial artifact is skipped, never fatal
            _log.warning("skipping unreadable draft-run artifact %s", f, exc_info=True)
    return out


def read_run(
    thesis_id: UUID, run_id: str, *, base_dir: Path | None = None
) -> dict[str, Any] | None:
    """Return one saved run's INNER DRAFT object (``payload['draft']``) as a plain dict for the router to
    validate into ``ChainDraftOut``. TRAVERSAL-SAFE: ``run_id`` must be an actual ``<run_id>.json`` entry in
    the runs dir (membership guard — a ``../`` id can't match), else ``None`` → the endpoint 404s. ``None``
    also for a missing dir / absent ``draft`` key."""
    d = _runs_dir(thesis_id, base_dir)
    if not d.is_dir():
        return None
    allowed = {f.stem for f in d.glob("*.json")}  # only real entries — defeats path traversal
    if run_id not in allowed:
        return None
    payload = json.loads((d / f"{run_id}.json").read_text(encoding="utf-8"))
    return payload.get("draft")
