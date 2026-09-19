from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from backtest import store

# The dial_values backfill (scripts/backfill_backtest_dial_values.py). It copies each moved dial's VALUE
# from a run's manifest UP into its registry row — a one-time enrichment for runs written before the field,
# so the /backtest picker can name a run's arm ("liveness = 90") off the row alone. Two properties carry
# the whole design: it enriches only value-less rows that MOVED a dial (so a re-run is a no-op and a
# baseline row stays empty), and a candidate whose manifest is missing is WARNed and left rather than
# aborting the pass.
#
# Loaded from its file path because scripts/ is not an importable package — the same reason the script
# itself puts backend/ on sys.path. Importing (not running) is safe: the module guards main() behind
# `if __name__ == "__main__"`.

_SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "backfill_backtest_dial_values.py"


def _load():
    spec = importlib.util.spec_from_file_location("backfill_backtest_dial_values", _SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write(root, run_id, *, dials_moved, overlay_diff, write_manifest: bool = True) -> None:
    """A pre-backfill registry row (no `dial_values`) plus its manifest on disk — what the backfill reads."""
    if write_manifest:
        d = store.create_run_dir(run_id, root)
        (d / "manifest.json").write_text(
            json.dumps({"run_id": run_id, "overlay_diff": overlay_diff}), encoding="utf-8"
        )
    store.register_run(
        store.RunSummary(
            run_id=run_id,
            created_at="2026-09-18T04:12:07+00:00",
            config_short=run_id[:8],
            config_hash=run_id,
            clock="record",
            known_at_mode="pin",
            window_start="2025-09-01",
            window_end="2026-09-14",
            dials_moved=dials_moved,
        ),
        root,
    )


def test_backfill_enriches_value_less_rows_and_is_idempotent(tmp_path):
    mod = _load()
    _write(
        tmp_path,
        "variant",
        dials_moved=["insider_core_alpha_liveness_days"],
        overlay_diff={"insider_core_alpha_liveness_days": {"default": 180, "run": 90}},
    )
    _write(tmp_path, "baseline", dials_moved=[], overlay_diff={})

    enriched, skipped, total = mod.backfill_dial_values(tmp_path)
    assert (enriched, skipped, total) == (1, 1, 2)
    rows = {r.run_id: r for r in store.list_runs(tmp_path)}
    assert rows["variant"].dial_values == {"insider_core_alpha_liveness_days": 90}
    assert rows["baseline"].dial_values == {}  # a baseline row is left empty forever

    # idempotent — the enriched row is now skipped, and nothing changes
    assert mod.backfill_dial_values(tmp_path) == (0, 2, 2)
    rows2 = {r.run_id: r for r in store.list_runs(tmp_path)}
    assert rows2["variant"].dial_values == {"insider_core_alpha_liveness_days": 90}
    assert rows2["baseline"].dial_values == {}


def test_backfill_warns_and_leaves_a_candidate_whose_manifest_is_missing(tmp_path, capsys):
    """A run that moved a dial but whose manifest never reached disk is skipped with a WARN, not an abort —
    one bad run must never take the whole pass down."""
    mod = _load()
    _write(tmp_path, "orphan", dials_moved=["d"], overlay_diff={}, write_manifest=False)
    assert mod.backfill_dial_values(tmp_path) == (0, 1, 1)
    assert store.list_runs(tmp_path)[0].dial_values == {}
    out = capsys.readouterr().out
    assert "WARN" in out and "orphan" in out
