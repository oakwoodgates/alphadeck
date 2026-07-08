"""The DISCOVER stage's run-of-record — one WRITE-ONLY JSON artifact per COMPLETED draft job.

The draft is response-only and its result lives only in the in-memory job registry: once the operator prunes
and promotes, what the run actually SURFACED (the universe, the term set as used, the honesty report) is gone —
there is no way to answer "what did the 2026-07-06 draft see, and under which dials?" after the fact. This is
the same accountability gap the back half closes with the immutable ``calls`` log, so the front half gets the
same pattern: an append-only run record, dumped by the JOB layer on every successful draft
(``draft_jobs._run_job`` → the route's ``on_success`` hook → ``write_draft_run_log``).

INVARIANT-GRADE BOUNDS (a file is not a fact):

- **Write-only ON THE SPINE PATH.** This module WRITES; nothing here auto-loads an artifact into a fact or a
  placement. A gated, read-only loader (``workbench/run_loader.py``, behind ``ALPHADECK_RUN_LOADER_ENABLED`` —
  a dev/test cost-saver) MAY read an artifact back to SEED the editable workbench, but that is a NON-SPINE read:
  it sets FE draft state only. The operator's promote stays the ONLY spine writer, and
  ``test_draft_endpoint_writes_nothing`` (zero ``fact_*``, zero ``basket_member``) stays the load-bearing proof
  — the draft endpoint, this writer, and the run-loader read endpoints all touch zero spine rows (this module
  never opens a DB connection at all).
- **Fail-open, logged.** An artifact write that fails (disk full, permissions, a pathological payload) is a
  logged exception and ``None`` — NEVER a failed draft. The record is best-effort by design; the draft the
  operator is waiting on is not.
- **Value-free provenance.** The artifact carries the draft as returned (prose, tiers, matched terms, the
  report) plus the inputs that produced it — it neither adds nor computes a number (#3).

The home mirrors the ingest caches (``ingest/edgar/client.py``): the repo's gitignored ``data/`` locally,
``/data`` in the container — the compose ``appdata:/data`` volume, so the record survives rebuilds.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from domain.settings import get_settings
from domain.thesis import Thesis

_log = logging.getLogger("alphadeck.workbench")

# Runtime artifacts live under the repo's gitignored data/ (== the container's volume-mounted /data);
# tests pass an explicit base_dir (unit) or monkeypatch this (the app-suite conftest redirect).
_DEFAULT_RUNS = Path(__file__).resolve().parents[2] / "data" / "draft_runs"


def write_draft_run_log(
    thesis: Thesis, draft: Any, job_id: str, *, base_dir: Path | None = None
) -> Path | None:
    """Dump one completed draft run to ``<base>/<thesis_id>/<utc-timestamp>-<job_id>.json``; return the path.

    The payload is the run's full accountability record: the thesis identity + narrative, the term set AS USED
    (the exact entries discovery read — term/tier/authored_by/source), the dials in effect (the hit cap + the
    two draft models — the knobs that make one run's universe differ from another's), and the draft itself
    (segments, every placement with its provenance, the honesty report) via ``model_dump(mode="json")``, so
    the ``draft`` key round-trips ``ChainDraftOut.model_validate`` byte-honestly. ``draft`` is typed ``Any``
    deliberately — this workbench module never imports the ``app`` wire schema (the layering stays one-way);
    any pydantic model dumps.

    FAIL-OPEN: any fault (assembly or I/O) is logged with its traceback and swallowed (``None``) — the run
    record must never fail the draft it records.
    """
    try:
        s = get_settings()
        now = datetime.now(timezone.utc)
        payload = {
            "written_at": now.isoformat(),
            "job_id": job_id,
            "thesis": {
                "id": str(thesis.id),
                "name": thesis.name,
                "narrative": thesis.narrative,
            },
            "term_set": [e.model_dump(mode="json") for e in thesis.term_set],
            "dials": {
                "discovery_hit_cap": s.discovery_hit_cap,
                "research_model": s.llm_research_model,
                "decompose_model": s.llm_decompose_model,
            },
            "draft": draft.model_dump(mode="json"),
        }
        run_dir = (base_dir or _DEFAULT_RUNS) / str(thesis.id)
        run_dir.mkdir(parents=True, exist_ok=True)
        # %H%M%S, no colons — the filename must be legal on Windows too; job_id disambiguates same-second runs
        path = run_dir / f"{now.strftime('%Y%m%dT%H%M%SZ')}-{job_id}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract: log, never fail the draft
        _log.exception(
            "draft run log write failed for thesis %s job %s (fail-open — the draft is unaffected)",
            thesis.id,
            job_id,
        )
        return None
