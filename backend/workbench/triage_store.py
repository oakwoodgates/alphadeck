"""The TRIAGE-stage prune session — one MUTABLE JSON blob per thesis, the operator's resumable working state.

The Workbench editor holds the operator's PRUNE (a large drafted universe → a shortlist) as browser-only
React state. A refresh wipes it; a fresh Opus re-draft costs minutes + credits. This store lets that working
state survive a refresh and resume across sessions: the FE serializes its ENTIRE working state to one opaque
JSON blob, autosaves it (debounced) on change, and rehydrates from it on editor open.

INVARIANT-GRADE BOUNDS (a session is NOT the record — the promote stays the ONLY spine writer):

- **The dumbest possible module — STRUCTURALLY unable to write the spine.** This module takes NO DB
  connection and imports NO repository. ``state`` is opaque bytes we write to / read from a file; its
  contents cannot trigger a ``basket_member`` / ``fact_*`` write, regardless of payload. Prune progress is
  WORKING STATE, not a fact — ``test_session_put_writes_no_spine_rows`` extends the
  ``test_draft_endpoint_writes_nothing`` family to prove a fat session PUT persists zero spine rows.
- **MUTABLE, single-blob, overwrite-in-place.** Unlike the write-only, append-per-run ``draft_run_log`` (the
  DISCOVER accountability record), this is a MUTABLE cache: one ``latest.json`` per thesis, overwritten on
  every autosave. No archive, no versioning — the operator's ``DELETE`` (start over) is the only remove.
- **Fail-LOUD (the deliberate contrast with ``draft_run_log``).** ``draft_run_log`` is fail-open because a
  bad artifact write must never fail the draft the operator is waiting on. Here it is the opposite: a failed
  autosave must be VISIBLE (the FE's "Not saved" indicator + retry depends on it), so the writer lets I/O
  errors propagate and the endpoint maps them to a 5xx. Silent success on a failed save is the one thing the
  feature must never do.
- **Traversal-safe by construction.** ``thesis_id`` is a ``UUID`` (a UUID cannot contain a path separator),
  keyed into the directory as ``str(thesis_id)``; the filename is the fixed literal ``latest.json``. No
  user-controlled string ever reaches the path.

The home mirrors ``draft_run_log`` (the repo's gitignored ``data/`` locally, ``/data`` in the container — the
compose ``appdata:/data`` volume), so the session survives rebuilds like the draft caches do.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

# Sessions live under the repo's gitignored data/ (== the container's volume-mounted /data), a SEPARATE,
# explicitly-MUTABLE store from data/draft_runs/. Tests pass an explicit base_dir (unit) or monkeypatch this
# constant (the app-suite conftest redirect), exactly as the draft-run home does.
_DEFAULT_TRIAGE = Path(__file__).resolve().parents[2] / "data" / "triage_sessions"

_FILENAME = "latest.json"


def _session_path(thesis_id: UUID, base_dir: Path | None) -> Path:
    """``<base>/<thesis_id>/latest.json``. ``thesis_id`` is a UUID (no separators possible) and the filename is
    a fixed literal, so the path is traversal-safe by construction."""
    return (base_dir or _DEFAULT_TRIAGE) / str(thesis_id) / _FILENAME


def write_session(
    thesis_id: UUID, schema_version: int, state: dict[str, Any], *, base_dir: Path | None = None
) -> dict[str, Any]:
    """Overwrite this thesis's session with ``state``; return the stored envelope (with a server-stamped
    ``updated_at``).

    ``state`` is treated as OPAQUE — this module never interprets, validates, or reads a field of it. FAIL-LOUD
    by contract: an I/O fault propagates so the endpoint surfaces a 5xx and the operator's save indicator is
    honest. The write goes to a temp file then ``replace``s ``latest.json`` so a crash mid-write never leaves a
    truncated (unreadable) session in place of a good one.
    """
    envelope: dict[str, Any] = {
        "thesis_id": str(thesis_id),
        "schema_version": schema_version,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "state": state,
    }
    path = _session_path(thesis_id, base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(envelope), encoding="utf-8")
    tmp.replace(path)  # atomic on the same filesystem — no half-written latest.json
    return envelope


def read_session(thesis_id: UUID, *, base_dir: Path | None = None) -> dict[str, Any] | None:
    """This thesis's stored session envelope, or ``None`` if none exists. ``None`` means GENUINELY-ABSENT — the
    caller must not confuse it with a load error (the route returns ``session: null`` for absent and a 5xx for a
    read fault, so the FE distinguishes "no session yet" from "load failed")."""
    path = _session_path(thesis_id, base_dir)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def delete_session(thesis_id: UUID, *, base_dir: Path | None = None) -> bool:
    """Remove this thesis's session (the operator's explicit "start over"); return whether a file was removed.
    Idempotent — deleting an absent session is a no-op that returns ``False``."""
    path = _session_path(thesis_id, base_dir)
    if not path.exists():
        return False
    path.unlink()
    return True
