"""The backfill's provenance — one WRITE-ONLY JSON artifact per ``pipeline.backfill`` invocation.

A reconstructed call-of-record row is indistinguishable from a nightly one by its ``asof`` alone, and the
``calls`` log is immutable (``recorded_at`` is always now, the row carries no ``known_at``). So the ONE place
that says "this night's row was reconstructed on <date> with ``known_at`` pinned at <instant>, resolved
by <policy>" is this artifact. It mirrors ``cron_run_log.py`` exactly and inherits its bounds:

- **Write-only, no DB.** It writes a file and opens no connection; it is called by the pass AFTER the run
  completes, from the already-collected results.
- **Fail-open, logged.** A write that fails is a logged exception and ``None``, NEVER a failed backfill.
- **Value-free.** It records what the run already produced (state / verdict / recorded / error per thesis);
  it computes no number and reads no fact (#3).
- **NOT a cron run artifact.** It lives in its OWN directory (``data/backfills/``, never ``data/cron_runs/``),
  so ``already_ran_live`` stays False for the night and a later ``--catch-up`` is not suppressed by a
  reconstruction, and the Admin run history never mistakes a backfill for a nightly pass.

The home mirrors the caches + the run log: the repo's gitignored ``data/`` locally, ``/data`` in the
container (the compose ``appdata:/data`` volume), so the record survives rebuilds.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # keep the layering one-way (the pass imports this module, not the reverse)
    from pipeline.backfill import BackfillResult

_log = logging.getLogger("alphadeck.backfill")

# Runtime artifacts live under the repo's gitignored data/ (== the container's volume-mounted /data);
# tests pass an explicit base_dir. Deliberately NOT data/cron_runs (see the module docstring).
_DEFAULT_BACKFILLS = Path(__file__).resolve().parents[2] / "data" / "backfills"


def build_backfill_payload(
    results: list[BackfillResult],
    *,
    asof: date,
    known_at: datetime,
    known_at_policy: str,
    dry_run: bool,
    started_at: datetime,
    finished_at: datetime,
) -> dict:
    """The provenance payload — PURE (no I/O). ``known_at`` is normalized to UTC ISO so the artifact reads
    the same from any zone; ``known_at_policy`` is ``"explicit"`` (the operator typed the instant) or
    ``"next-run"`` (resolved from the cron run log — the first live run after the night). ``dry_run`` is
    carried for honesty, but the pass never WRITES a dry run's artifact (a dry run touches neither the DB
    nor the disk), so a written artifact always reads ``false`` here."""
    return {
        "started_at": started_at.isoformat(),
        "finished_at": finished_at.isoformat(),
        "duration_s": round((finished_at - started_at).total_seconds(), 3),
        "asof": asof.isoformat(),
        "known_at": known_at.astimezone(timezone.utc).isoformat(),
        "known_at_policy": known_at_policy,
        "dry_run": dry_run,
        "summary": {
            "theses": len(results),
            "appended": sum(1 for r in results if r.recorded),
            "unchanged": sum(1 for r in results if r.recorded is False),
            "errored": sum(1 for r in results if r.error),
        },
        "theses": [
            {
                "id": str(r.thesis_id),
                "name": r.name,
                "state": r.state,
                "verdict": r.verdict,
                "armed": r.armed,
                "recorded": r.recorded,
                "error": r.error,
            }
            for r in results
        ],
    }


def write_backfill_log(
    results: list[BackfillResult],
    *,
    asof: date,
    known_at: datetime,
    known_at_policy: str,
    dry_run: bool,
    started_at: datetime,
    finished_at: datetime,
    base_dir: Path | None = None,
) -> Path | None:
    """Dump one backfill invocation (``build_backfill_payload``) to ``<base>/<utc-timestamp>.json``; return
    the path (or ``None`` fail-open). The whole write — payload build included — stays inside the fail-open
    try: a provenance fault is a logged exception, never a failed backfill."""
    try:
        payload = build_backfill_payload(
            results,
            asof=asof,
            known_at=known_at,
            known_at_policy=known_at_policy,
            dry_run=dry_run,
            started_at=started_at,
            finished_at=finished_at,
        )
        run_dir = base_dir or _DEFAULT_BACKFILLS
        run_dir.mkdir(parents=True, exist_ok=True)
        # %H%M%S, no colons — legal on Windows too; the started-at instant names the invocation
        path = run_dir / f"{started_at.strftime('%Y%m%dT%H%M%SZ')}.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001 — fail-open by contract: log, never fail the backfill
        _log.exception("backfill log write failed (fail-open — the backfill itself is unaffected)")
        return None
