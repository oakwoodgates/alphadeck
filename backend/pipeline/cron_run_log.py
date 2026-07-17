"""The daily cron's run-of-record — one WRITE-ONLY JSON artifact per cron pass.

The cron went ~11 days silently frozen (the R1 submissions-cache freeze) and it was found only because the
OPERATOR happened to look at a thesis and think "that doesn't seem right." The cron had no memory of itself:
the exit code is swallowed by the sleep-loop wrapper, the notifier falls back to stdout, and stdout dies on
the next `docker compose up`. Answering "did it run last night, and did it do anything?" took forensics on the
`calls` table. This closes that gap the same way the DISCOVER stage closed its own (`draft_run_log.py`) and the
back half closed its call gap (the immutable `calls` log): an append-only run record the platform writes about
itself, so the next freeze is noticed by the platform, not by eye.

BOUNDS (a file is not a fact — the `draft_run_log.py` discipline):

- **Write-only, no DB.** This module WRITES a file and opens no connection; it cannot touch a spine row. It is
  called by `pipeline.daily.main` AFTER the run completes, from the already-collected results.
- **Fail-open, logged.** A run-log write that fails (disk full, permissions) is a logged exception and `None`,
  NEVER a failed cron. The record is best-effort; the ingest + call-of-record it records are not.
- **Value-free.** It records counts + outcomes the run already produced (appended / unchanged / errored,
  per-thesis ingest tallies); it computes no number and reads no fact (#3).

The home mirrors the caches + the draft log: the repo's gitignored `data/` locally, `/data` in the container
(the compose `appdata:/data` volume), so the record survives rebuilds — the very thing whose absence made the
cron invisible.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid importing the pipeline module at import time (keeps the layering one-way)
    from pipeline.daily import ThesisRunResult

_log = logging.getLogger("alphadeck.cron")

# Runtime artifacts live under the repo's gitignored data/ (== the container's volume-mounted /data);
# tests pass an explicit base_dir.
_DEFAULT_CRON_RUNS = Path(__file__).resolve().parents[2] / "data" / "cron_runs"


def write_cron_run_log(
    results: list[ThesisRunResult],
    *,
    asof: date,
    allow_live: bool,
    started_at: datetime,
    finished_at: datetime,
    base_dir: Path | None = None,
) -> Path | None:
    """Dump one cron pass to ``<base>/<utc-timestamp>.json``; return the path (or ``None`` fail-open).

    The payload answers, from a file read, every question the freeze investigation answered by forensics:
    *did it run* (`started_at`/`finished_at`), *for real or cache-only* (`mode` — the R2 no-live signal),
    *did it do anything* (per-thesis `form4_appended`/`price_bars_appended` + `names_ingested`/`names_errored`,
    so a 0-fact run backed by a real ingest is distinguishable from a total no-op), and *what moved*
    (`recorded`/`transition`/`error`). ``names_errored == names_ingested`` on every thesis is the fingerprint
    of a total-ingest failure (the Source-C shape R2 will gate on).
    """
    try:
        recorded = sum(1 for r in results if r.recorded)
        payload = {
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_s": round((finished_at - started_at).total_seconds(), 3),
            "asof": asof.isoformat(),
            "mode": "live" if allow_live else "no-live",  # the R2 recording-gate signal
            "summary": {
                "theses": len(results),
                "appended": recorded,
                "unchanged": sum(1 for r in results if r.recorded is False),
                "errored": sum(1 for r in results if r.error),
                "transitions": sum(1 for r in results if r.transition),
            },
            "theses": [
                {
                    "id": str(r.thesis_id),
                    "name": r.name,
                    "recorded": r.recorded,
                    "transition": r.transition,
                    "error": r.error,
                    "names_ingested": len(r.ingested),
                    "names_errored": sum(1 for x in r.ingested if x.error),
                    "form4_appended": sum(x.form4_appended for x in r.ingested),
                    "price_bars_appended": sum(x.price_bars_appended for x in r.ingested),
                    "form4_skipped": sum(x.form4_skipped for x in r.ingested),
                }
                for r in results
            ],
        }
        run_dir = base_dir or _DEFAULT_CRON_RUNS
        run_dir.mkdir(parents=True, exist_ok=True)
        # %H%M%S, no colons — legal on Windows too; the started-at instant names the run
        path = run_dir / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract: log, never fail the cron
        _log.exception("cron run log write failed (fail-open — the cron run is unaffected)")
        return None
