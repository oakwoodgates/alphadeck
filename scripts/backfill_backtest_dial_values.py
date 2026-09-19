#!/usr/bin/env python
"""Backfill the registry's `dial_values` from each run's manifest — a ONE-TIME enrichment for runs that
predate the field. `dial_values` (the SIBLING of `dials_moved`) lets the /backtest picker name a run's
arm ("liveness = 90") off the registry row alone; a run written before the field has an empty one, and its
value still lives in `runs/<run_id>/manifest.json` under `overlay_diff[dial]["run"]`. This copies it up.

Run from the MAIN checkout root under the backend venv (it imports backtest.store). The store lives under
backend/, so put that on sys.path first — hence the deliberate mid-file import + noqa. Reads every
manifest it enriches; writes ONLY the registry index.json (atomically, via the store's own writer).

IDEMPOTENT by construction: a row is enriched only when it MOVED a dial (`dials_moved` non-empty) but has
no `dial_values` yet, so a second run is a no-op and a baseline run (no dial moved) is left empty forever.

FROZEN PROD ARCHIVE: the prod stack serves data/backtest_prod, a byte-for-byte copy whose index.json holds
its rows VERBATIM (archive_backtest_prod.py copies them). So prod picks the values up by backfilling the
SOURCE store first, then re-running archive_backtest_prod.py, then a backend redeploy — see docs/DEPLOY.md.

DO NOT execute this against real data as part of this change — it is only unit-tested against a tmp store.
The operator runs it deliberately (and, on the prod source, after a backup).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# The store lives under backend/; put it on sys.path before importing it (mirrors archive_backtest_prod.py).
sys.path.insert(0, str(Path("backend").resolve()))
from backtest import store  # noqa: E402


def backfill_dial_values(root: str | Path = store.DEFAULT_ROOT) -> tuple[int, int, int]:
    """Enrich every value-less registry row from its manifest. Returns (enriched, skipped, total).

    A row is a CANDIDATE when it moved a dial but has no `dial_values` yet. Baseline rows (no dial moved)
    and already-enriched rows are correctly left untouched, which is what makes a re-run a no-op. A
    candidate whose manifest is missing, unreadable, or carries no usable `overlay_diff` is WARNed and
    left as it is — one bad run never aborts the pass. `skipped` = every row left unchanged (candidates
    that could not be enriched, plus the baselines and already-done rows), so enriched + skipped = total.
    """
    root = Path(root)
    index = store.read_index(root)
    enriched = 0
    for row in index.runs:
        if not row.dials_moved or row.dial_values:
            continue  # baseline or already-enriched — left unchanged (this is the idempotency)
        manifest_path = store.runs_root(root) / row.run_id / "manifest.json"
        try:
            overlay = json.loads(manifest_path.read_text(encoding="utf-8")).get(
                "overlay_diff"
            )
        except (OSError, json.JSONDecodeError) as exc:
            print(
                f"WARN: {row.run_id}: cannot read manifest ({exc}); leaving row unchanged"
            )
            continue
        if not isinstance(overlay, dict) or not overlay:
            print(
                f"WARN: {row.run_id}: manifest has no overlay_diff; leaving row unchanged"
            )
            continue
        try:
            row.dial_values = {k: v["run"] for k, v in overlay.items()}
        except (
            KeyError,
            TypeError,
        ) as exc:  # overlay_diff not in the {dial: {"default","run"}} shape
            print(
                f"WARN: {row.run_id}: overlay_diff shape unexpected ({exc}); leaving row unchanged"
            )
            continue
        enriched += 1
    if enriched:
        store._write_index(index, root)  # atomic — same writer the run registrar uses
    total = len(index.runs)
    return enriched, total - enriched, total


def main() -> int:
    root = Path(sys.argv[1]) if len(sys.argv) > 1 else store.DEFAULT_ROOT
    if not store.index_path(root).is_file():
        print(
            f"FAIL: {store.index_path(root)} not found — pass the store root, or run from the main "
            f"checkout root where data/backtest/index.json lives."
        )
        return 1
    enriched, skipped, total = backfill_dial_values(root)
    print(f"enriched {enriched} / skipped {skipped} / total {total}")
    print(f"OK: dial_values backfilled at {Path(root).resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
