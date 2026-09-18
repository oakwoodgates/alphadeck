"""``python -m backtest.pair`` — SEPARATE "timing the same episodes better" from "selecting a different set".

A curve point's delta answers one question with two answers inside it. Moving a liveness dial changes the
``exit_by`` of episodes the baseline also armed — that is TIMING — and it changes WHICH episodes arm at all,
because a shorter horizon admits fewer and a longer one admits more. The pooled median cannot tell them
apart, and the first pre-registered pass ran straight into it: ``revenue_accel_alpha_liveness_days`` at 60 d
was the largest delta anywhere (+2.19%) and arrived with n 1968 against the baseline's 2614, which is
exactly the case its own decision rule named in advance as a sample-size artifact rather than a lever.

This pairs the two artifacts on the episode's own identity — ``(thesis_id, security_id, arm_date)``, the key
``replay.episodes`` derives on — and reports three things per window and pooled:

* **shared** — episodes BOTH settings armed. Their returns still differ, because the dial moved their
  ``exit_by``, so this is the timing effect with composition held fixed. Reported two ways: the difference
  of medians (comparable with the curve's own delta, which is built that way) and the median of the
  per-episode differences (the genuinely paired statistic, which removes the cross-sectional spread and is
  the one that answers "better timing on the same decisions").
* **only in the baseline** — episodes the point DROPPED. If these were bad, dropping them lifts the pooled
  median without improving any decision.
* **only in the point** — episodes the point ADDED, the mirror image.

It reads ARTIFACTS ONLY: the run directories' ``outcomes.parquet``, which already carries the key and the
forward return. No database, no mirror, no re-run — so a finished pass can be re-interrogated for the cost
of reading its own bytes.

**It does not decompose the pooled delta into two numbers that sum to it.** Medians do not decompose
additively, and a number that looked like an exact split would be a lie about what a median is. The three
components are reported side by side and the reader does the arithmetic that is actually available: is the
shared-set effect large enough to matter, and how much of the population moved?

    python -m backtest.pair --pass-id 20260918T032952Z-public-h5-… \\
        --dial revenue_accel_alpha_liveness_days --values 60,90
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import median
from typing import Any

from backtest import store

#: the episode's own identity, as `replay.episodes` derives it and `Outcome` carries it
Key = tuple[str, str, str]


@dataclass(frozen=True)
class Split:
    """One window's (or the whole pass's) comparison of a point against its baseline."""

    n_shared: int = 0
    n_only_point: int = 0
    n_only_baseline: int = 0
    #: median(point) - median(baseline) over the SHARED episodes — built the same way the curve's own
    #: delta is, so the two are directly comparable
    shared_delta: float | None = None
    #: median of the per-episode differences over the shared set — the paired statistic
    paired_delta: float | None = None
    #: what the dropped and the added episodes were worth, so a composition change can be read
    median_only_baseline: float | None = None
    median_only_point: float | None = None
    median_shared_point: float | None = None
    median_shared_baseline: float | None = None
    #: HOW MANY shared episodes the dial actually moved, and by how much when it did. Without these the
    #: paired median is unreadable: a dial that changes a MINORITY of the shared episodes has a paired
    #: median of exactly 0.0 (the untouched majority decides it) while still shifting the two medians
    #: apart, and the two numbers then look contradictory when they are both correct.
    n_changed: int = 0
    median_change_when_changed: float | None = None


@dataclass
class PairReport:
    dial: str
    value: Any
    pass_id: str
    point_config: str
    baseline_config: str
    windows: list[tuple[str, str]] = field(default_factory=list)
    per_window: list[Split] = field(default_factory=list)
    pooled: Split = field(default_factory=Split)


def _returns(run_dir: Path) -> dict[Key, float]:
    """One run's scored episodes, keyed by identity. Rows with no forward return are omitted — they are
    not comparable on either side, and including them as zeros would invent measurements."""
    import pyarrow.parquet as pq

    out: dict[Key, float] = {}
    for r in pq.read_table(run_dir / "outcomes.parquet").to_pylist():
        fr = r.get("forward_return")
        if fr is None or not r.get("arm_date"):
            continue
        out[(str(r["thesis_id"]), str(r["security_id"]), str(r["arm_date"]))] = float(fr)
    return out


def _med(values: list[float]) -> float | None:
    return round(median(values), 6) if values else None


def _split(point: dict[Key, float], base: dict[Key, float]) -> Split:
    shared = sorted(point.keys() & base.keys())
    only_p = sorted(point.keys() - base.keys())
    only_b = sorted(base.keys() - point.keys())
    p_shared = [point[k] for k in shared]
    b_shared = [base[k] for k in shared]
    diffs = [point[k] - base[k] for k in shared]
    mp, mb = _med(p_shared), _med(b_shared)
    nonzero = [d for d in diffs if d != 0.0]
    return Split(
        n_shared=len(shared),
        n_only_point=len(only_p),
        n_only_baseline=len(only_b),
        shared_delta=None if mp is None or mb is None else round(mp - mb, 6),
        paired_delta=_med(diffs),
        median_only_baseline=_med([base[k] for k in only_b]),
        median_only_point=_med([point[k] for k in only_p]),
        median_shared_point=mp,
        median_shared_baseline=mb,
        n_changed=len(nonzero),
        median_change_when_changed=_med(nonzero),
    )


def pair_point(curve: dict[str, Any], value: Any, root: Path) -> PairReport:
    """Pair ONE curve point against that curve's baseline, per window and pooled."""
    points = curve["points"]
    baseline = next((p for p in points if p.get("is_baseline")), points[0])
    dial = curve["dial_names"][0]
    match = [p for p in points if str(list(p["dials"].values())[0]) == str(value)]
    if not match:
        raise SystemExit(f"no point at {dial}={value} in this curve")
    point = match[0]
    if point["run_ids"] == baseline["run_ids"]:
        raise SystemExit(f"{dial}={value} IS the baseline (it shares its runs); nothing to pair")

    rep = PairReport(
        dial=dial,
        value=value,
        pass_id=curve["pass_id"],
        point_config=point["config_short"],
        baseline_config=baseline["config_short"],
        windows=[(w[0], w[1]) for w in curve["windows"]],
    )
    all_p: dict[Key, float] = {}
    all_b: dict[Key, float] = {}
    for pid, bid in zip(point["run_ids"], baseline["run_ids"], strict=True):
        pdir, bdir = store.run_dir(pid, root), store.run_dir(bid, root)
        if pdir is None or bdir is None:
            raise SystemExit(f"missing run directory for {pid!r} or {bid!r} under {root}")
        pr, br = _returns(pdir), _returns(bdir)
        rep.per_window.append(_split(pr, br))
        # the windows are disjoint, so pooling is a union and no key can collide across them
        all_p.update(pr)
        all_b.update(br)
    rep.pooled = _split(all_p, all_b)
    return rep


def _pct(x: float | None) -> str:
    # ASCII only below this line: this prints to a Windows console whose default codec is cp1252, and a
    # report that crashes on its own em dash is a report nobody reads.
    return "-" if x is None else f"{x * 100:+.2f}%"


def render(rep: PairReport) -> str:
    lines = [
        f"{rep.dial} = {rep.value}   (cfg {rep.point_config} vs baseline {rep.baseline_config})",
        f"pass {rep.pass_id}",
        "",
        f"{'window':>12} {'shared':>7} {'moved':>6} {'only base':>10} {'only pt':>8} "
        f"{'shared dmed':>12} {'paired d':>9} {'d|moved':>9} {'med(dropped)':>13} {'med(added)':>11}",
    ]
    for (lo, _hi), s in zip(rep.windows, rep.per_window, strict=True):
        lines.append(
            f"{lo:>12} {s.n_shared:>7} {s.n_changed:>6} {s.n_only_baseline:>10} {s.n_only_point:>8} "
            f"{_pct(s.shared_delta):>12} {_pct(s.paired_delta):>9} "
            f"{_pct(s.median_change_when_changed):>9} "
            f"{_pct(s.median_only_baseline):>13} {_pct(s.median_only_point):>11}"
        )
    s = rep.pooled
    lines += [
        f"{'POOLED':>12} {s.n_shared:>7} {s.n_changed:>6} {s.n_only_baseline:>10} {s.n_only_point:>8} "
        f"{_pct(s.shared_delta):>12} {_pct(s.paired_delta):>9} "
        f"{_pct(s.median_change_when_changed):>9} "
        f"{_pct(s.median_only_baseline):>13} {_pct(s.median_only_point):>11}",
        "",
        "shared dmed = median(point) - median(baseline) over the episodes BOTH armed (built like the "
        "curve's own delta).",
        "paired d    = median of the PER-EPISODE differences over that same set -- the same decisions, "
        "timed differently.",
        "moved       = how many of the shared episodes the dial actually changed at all; d|moved is the "
        "median change among those.",
        "A paired d of +0.00% with moved < shared/2 is not a contradiction: the untouched majority "
        "decides the median.",
        "The components are NOT a decomposition: medians do not add, and a number that looked like an "
        "exact split would misstate what a median is.",
    ]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backtest.pair",
        description=(
            "Separate the timing effect from the composition effect for a curve point, by pairing its "
            "episodes against the baseline's on (thesis, security, arm_date). Reads artifacts only -- no "
            "database, no re-run."
        ),
    )
    p.add_argument("--pass-id", required=True)
    p.add_argument("--dial", required=True, help="which curve of the pass to read")
    p.add_argument(
        "--values", required=True, help="comma-separated point values to pair, e.g. 60,90"
    )
    p.add_argument(
        "--out-root", default=None, help="the store root (default: <repo>/data/backtest)"
    )
    p.add_argument("--json", action="store_true", help="emit the report as JSON instead of a table")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.out_root or store.DEFAULT_ROOT)
    path = root / "sweeps" / f"{args.pass_id}-{args.dial}.json"
    if not path.is_file():
        print(f"ERROR: no curve at {path}", file=sys.stderr)
        return 2
    curve = json.loads(path.read_text(encoding="utf-8"))
    reports = [pair_point(curve, v.strip(), root) for v in args.values.split(",")]
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "dial": r.dial,
                        "value": r.value,
                        "pass_id": r.pass_id,
                        "point_config": r.point_config,
                        "baseline_config": r.baseline_config,
                        "windows": r.windows,
                        "per_window": [vars(s) for s in r.per_window],
                        "pooled": vars(r.pooled),
                    }
                    for r in reports
                ],
                indent=2,
            )
        )
    else:
        for r in reports:
            print(render(r))
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
