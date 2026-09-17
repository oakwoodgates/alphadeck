"""Reading a backtest run back off disk — the serving side of the store.

The `/backtest` route serves ARTIFACTS, never a live computation: a run is written once by a CLI in a
venv that carries the `.[replay]` extra, and the API only reads the JSON it left behind. That is the same
shape the Scoreboard's replay panel already has, and it is what keeps a multi-hour sweep out of a web
request entirely.

**Everything here is JSON, deliberately.** A run also writes `episodes.parquet` / `outcomes.parquet` for
analysis, but reading those needs pyarrow, which the LEAN api image does not carry — only the sig and fork
images bake `.[replay]`. So the run writes a JSON copy of the episodes beside the Parquet and this module
touches nothing else, which is what lets the surface work on every tier rather than only where the
backtest can run.

**Absence is never an error.** A missing store, a missing run, a half-written directory and an unreadable
file all collapse to "not available", and the route turns that into `available: false` rather than a 500.
On prod the store does not exist at all, and that is the intended state: the route is dev/sig-only BY DATA
AVAILABILITY, with no build flag anywhere.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from backtest import store
from backtest.ledger import LEDGER_NAME, BacktestLedger
from backtest.manifest import BacktestManifest, read_manifest

_log = logging.getLogger("alphadeck.backtest")

SWEEP_NAME = "sweep.json"


def _read_json(path: Path) -> Any | None:
    """One JSON artifact, or ``None`` when it is absent or unreadable. Missing and corrupt collapse to the
    same answer on purpose: a half-written run directory is a run that did not finish, and the surface has
    to skip it quietly rather than fail the whole listing."""
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:  # noqa: BLE001 — unreadable is absence, not an outage
        _log.warning("backtest artifact unreadable at %s: %s", path, exc)
        return None


def store_exists(root: str | Path | None = None) -> bool:
    """Is there a backtest store on this stack at all? Prod has none, and that is the whole gate."""
    return store.index_path(root).is_file() or store.runs_root(root).is_dir()


def list_runs(root: str | Path | None = None) -> list[store.RunSummary]:
    """The registry, newest first. Empty when there is no store."""
    return store.list_runs(root)


def dial_trial_counts(runs: list[store.RunSummary]) -> dict[str, int]:
    """How many runs have touched each dial — "count your trials", answerable per DIAL.

    Derived from the registry rather than stored, so it cannot fall out of date, and surfaced beside the
    manifest because a result read without knowing how many times its dial was swept is a result read
    without its multiple-comparisons context."""
    counts: dict[str, int] = {}
    for r in runs:
        for dial in r.dials_moved:
            counts[dial] = counts.get(dial, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def read_run(run_id: str, root: str | Path | None = None) -> dict[str, Any] | None:
    """One run's whole served payload, or ``None`` when the run does not exist.

    The manifest is REQUIRED — without it there is no run, only a directory. Everything else degrades:
    a run written before the pooled view existed simply has no `pooled`, and the surface says so rather
    than pretending the nulls were computed."""
    path = store.run_dir(run_id, root)
    if path is None:
        return None
    manifest: BacktestManifest | None = read_manifest(path)
    if manifest is None:
        return None
    episodes = _read_json(path / "episodes.json") or {}
    return {
        "manifest": manifest,
        "pooled": _read_json(path / "pooled.json"),
        # NB `metrics.json` is deliberately NOT served. It scores every outcome; the LEDGER's own
        # metric set scores the eligible ones (matured + non-censored — the Scoreboard's rule) and is
        # the set whose rows are on screen beside it. Serving both would put two differently-gated
        # numbers on one page with nothing on the wire to tell them apart. The file stays in the run
        # directory for analysis.
        "episodes": episodes.get("episodes", []) if isinstance(episodes, dict) else [],
        "ledger": read_ledger(path),
    }


def read_ledger(run_path: Path) -> BacktestLedger | None:
    """The run's per-thesis ledger, VALIDATED — or ``None`` when it is absent or does not parse.

    Validated rather than passed through, unlike the other artifacts here, because this one is
    re-projected onto the Scoreboard's wire models at serve time: the route indexes into it, so a shape
    surprise has to become "no ledger" at the boundary instead of a 500 three layers in. A run written
    before B6 simply has no ledger, and the surface says so."""
    blob = _read_json(run_path / LEDGER_NAME)
    if blob is None:
        return None
    try:
        return BacktestLedger.model_validate(blob)
    except ValidationError as exc:  # a shape that moved is absence, not an outage
        _log.warning("backtest ledger did not validate at %s: %s", run_path, exc)
        return None


def read_sweep(root: str | Path | None = None) -> dict[str, Any] | None:
    """The sweep curve, or ``None`` when none has been run.

    LATEST-ONLY, and that is a known asymmetry rather than a design: `backtest.sweep` writes ONE
    `sweep.json` at the store root, so a second sweep overwrites the first. Runs are immutable and
    addressable; sweeps are not yet. The curve's points each cite their own run_id, so the underlying
    evidence survives — it is the curve that does not. Making sweeps addressable (`sweeps/<id>.json`) is
    the obvious follow-up and is noted in docs/BACKTEST.md.
    """
    return _read_json(Path(root or store.DEFAULT_ROOT) / SWEEP_NAME)
