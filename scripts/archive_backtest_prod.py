#!/usr/bin/env python
"""Curate the FROZEN prod backtest archive: 3 keeper passes + their shared mirror + sweeps, and assert the
copied mirror's content hash with the store's OWN function. Run from the MAIN checkout root under the backend
venv (it imports backtest.manifest). Read-only on the source; writes ONLY under data/backtest_prod.
For a clean re-archive, delete data/backtest_prod first."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

# Import the store's OWN content-hash fn (no re-derivation): rebench.py:186 / run.py / sweep.py all use it.
# It lives under backend/, so put that on sys.path first — hence the deliberate mid-file import + noqa.
sys.path.insert(0, str(Path("backend").resolve()))
from backtest.manifest import mirror_hash  # noqa: E402

SRC = Path("data/backtest")
DST = Path("data/backtest_prod")
KEEPER_PASSES = {
    "20260918T032952Z-public-h5-phase-1-the-convictio",  # phase 1  — 225 runs, 6 sweeps
    "20260918T182921Z-public-h5-phase-1b-a-ratified-c",  # phase 1b —  45 runs, 1 sweep
    "20260919T015210Z-public-h1-h3-co-first-measureme",  # H1/H3    —  54 runs, 5 sweeps
}
KEEPER_MIRROR = "20260915T000000Z-2025-09-01-2026-09-14-public"
EXPECTED_MIRROR_HASH = "ad8bd98a6f53a430bda1013ff3903ba4ccd80731de5688669c873a2df37d2705"  # all 324 keepers


def main() -> int:
    if not (SRC / "index.json").is_file():
        print(
            f"FAIL: {SRC / 'index.json'} not found — run from the main checkout root."
        )
        return 1
    idx = json.loads((SRC / "index.json").read_text(encoding="utf-8"))
    keeper_runs = [r for r in idx["runs"] if r.get("pass_id") in KEEPER_PASSES]
    keeper_ids = [r["run_id"] for r in keeper_runs]
    print(f"keeper runs: {len(keeper_ids)} (expected 324)")
    if len(keeper_ids) != 324:
        print(
            "FAIL: keeper run count != 324 — source changed since planning; aborting."
        )
        return 1
    for sub in ("runs", "mirrors", "sweeps"):
        (DST / sub).mkdir(parents=True, exist_ok=True)
    for rid in keeper_ids:  # 1) full run dirs, byte-for-byte
        s = SRC / "runs" / rid
        if not s.is_dir():
            print(f"FAIL: missing run dir on disk: {rid}")
            return 1
        shutil.copytree(s, DST / "runs" / rid, dirs_exist_ok=True)
    print(f"copied {len(keeper_ids)} run dirs")
    ms = SRC / "mirrors" / KEEPER_MIRROR  # 2) the one shared mirror
    if not ms.is_dir():
        print(f"FAIL: keeper mirror missing: {ms}")
        return 1
    shutil.copytree(ms, DST / "mirrors" / KEEPER_MIRROR, dirs_exist_ok=True)
    # ACCEPTANCE CHECK on the COPY, with the store's own fn — a truncated/corrupt copy fails loudly here.
    got = mirror_hash(DST / "mirrors" / KEEPER_MIRROR)
    if got != EXPECTED_MIRROR_HASH:
        print(f"FAIL: copied mirror hash {got} != expected {EXPECTED_MIRROR_HASH}")
        return 1
    print(f"copied mirror {KEEPER_MIRROR} — hash OK ({got})")
    n = 0  # 3) the 12 keeper sweep curves
    for sp in sorted((SRC / "sweeps").glob("*.json")):
        if any(sp.name.startswith(p) for p in KEEPER_PASSES):
            shutil.copy2(sp, DST / "sweeps" / sp.name)
            n += 1
    print(f"copied {n} sweep curves (expected 12)")
    if (SRC / "sweep.json").is_file():  # 4) root latest-only default curve
        shutil.copy2(SRC / "sweep.json", DST / "sweep.json")
    out = dict(idx)  # 5) filtered index — kept rows verbatim
    out["runs"] = keeper_runs
    (DST / "index.json").write_text(
        json.dumps(out, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(f"wrote {DST / 'index.json'} with {len(keeper_runs)} runs")
    print("OK: archive ready at", DST.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
