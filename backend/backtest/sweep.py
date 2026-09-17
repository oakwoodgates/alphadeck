"""``python -m backtest.sweep`` — one dial (or a grid) across a range, as a CURVE.

**Plateau, not argmax, and the code refuses to name a winner.** With roughly one year of one regime and
episodes that are not independent (82% armed alongside a co-member), the best-scoring point of a sweep is
mostly noise: pick it and you have fitted the tape. What survives is a PLATEAU — a contiguous run of
values that all behave, so the choice inside it barely matters — and a SIGN that holds up when the window
is cut in half. So the report carries a curve, a plateau band and a per-point sign agreement, and a test
asserts it has no `best` / `winner` / `optimal` key. Someone reading this file should not be able to ask
it for an answer it cannot honestly give.

**Sub-period sign agreement.** Each point's delta against the baseline is recomputed on each of N disjoint
sub-windows, and the point `agrees` only when every sub-window moves the same way. This is the cheap
stand-in for a hold-out at this n: a dial that helps in the first half and hurts in the second has not
found anything, however good its pooled number looks.

**ONE frozen mirror for the whole sweep.** Exported once, shared by every variant, so the only difference
between two points is `cfg` -- `replay/compare.py`'s discipline, and the reason `mirror.hash` matching
across the runs is worth checking.

**The read window widens WITH the dial.** `call_bounds(cfg)` is derived from the run's own cfg (B5a), so
sweeping a liveness dial widens the PIT floor with it. That is not incidental: without it a sweep over
`insider_core_alpha_liveness_days` would silently truncate the very window it is measuring, and the curve
would flatten for a reason that had nothing to do with the dial. B5a also measured that one liveness dial
moves BOTH bounded tables' floors (180 -> 900 moved the price floor 460 -> 960), so a sweep is never
touching only the one table it names. A test pins that the floor moves.
"""

from __future__ import annotations

import argparse
import itertools
import json
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from typing import Any, Literal

import psycopg
from pydantic import BaseModel, Field

from backtest import store
from backtest.config_overlay import OverlayError, apply_overlay
from backtest.manifest import mirror_hash
from backtest.nulls import DEFAULT_DRAWS
from backtest.run import execute
from db.session import DEFAULT_TENANT_ID, connect
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash, short_hash
from replay.export import export_snapshot

SWEEP_NAME = "sweep.json"


class SweepPoint(BaseModel):
    """One dial setting, its run, and how it behaved — never a rank."""

    dials: dict[str, Any] = Field(default_factory=dict)
    run_id: str
    config_short: str
    n_episodes: int = 0
    n_scored: int = 0
    metric: float | None = None  # the pooled median forward return at this point
    delta_vs_baseline: float | None = None
    # the same delta recomputed on each disjoint sub-window, in order
    subwindow_deltas: list[float | None] = Field(default_factory=list)
    # True only when EVERY sub-window moved the same way. A pooled number that cannot survive cutting the
    # window in half has not found anything.
    sign_agreement: bool = False
    is_baseline: bool = False


class SweepReport(BaseModel):
    """The curve. Deliberately carries no winner — see the module docstring."""

    dial_names: list[str] = Field(default_factory=list)
    metric_name: str = "arm_timing_forward_return_median"
    window_start: date
    window_end: date
    subwindows: int = 2
    # WHICH AXIS the one shared mirror carries. On the curve it is not decoration: a record-clock sweep and
    # a public-clock sweep of the same dial are two different experiments and must never be read as one
    # series, and the run ids below are the only other place that could be checked.
    clock: Literal["record", "public"] = "record"
    mirror_hash: str = ""
    baseline_config_short: str = ""
    points: list[SweepPoint] = Field(default_factory=list)
    # the widest contiguous run of points whose sign agrees with the best-behaving one, as INDICES into
    # `points`. A band, not a pick: if it is one point wide, the sweep found nothing worth adopting.
    plateau: list[int] = Field(default_factory=list)
    banner: str = ""


@dataclass(frozen=True)
class _Scored:
    n_episodes: int
    values: list[tuple[date, float]]  # (arm_date, forward_return)


def _read_scored(run_dir: Path) -> _Scored:
    """Read one run's outcomes back off its own Parquet — the artifact is the source of truth, so a point
    on the curve is derived from the same bytes a reviewer can open."""
    import pyarrow.parquet as pq

    table = pq.read_table(run_dir / "outcomes.parquet").to_pylist()
    vals = [
        (date.fromisoformat(r["arm_date"]), r["forward_return"])
        for r in table
        if r.get("forward_return") is not None and r.get("arm_date")
    ]
    return _Scored(n_episodes=len(table), values=vals)


def _sub_bounds(start: date, end: date, n: int) -> list[tuple[date, date]]:
    """``n`` DISJOINT, contiguous sub-windows covering [start, end]."""
    total = (end - start).days + 1
    if n <= 1 or total < n:
        return [(start, end)]
    step = total // n
    out = []
    cursor = start
    for i in range(n):
        last = end if i == n - 1 else cursor + timedelta(days=step - 1)
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


def _median_in(scored: _Scored, lo: date, hi: date) -> float | None:
    vals = [v for d, v in scored.values if lo <= d <= hi]
    return round(median(vals), 6) if vals else None


def _plateau(points: list[SweepPoint]) -> list[int]:
    """The widest CONTIGUOUS run of points that both agree across sub-windows and move the same way as the
    strongest agreeing point. A band rather than a pick; a one-wide band means nothing was found.

    THE BASELINE COUNTS AS AGREEING, and that is deliberate: its delta is 0 by construction, so a band
    spanning it says "these settings are indistinguishable from today", which is a real and useful answer.

    A POINT CAN AGREE ACROSS SUB-WINDOWS AND STILL FALL OUTSIDE THE BAND, and the first real sweep hit
    exactly that: at 365 days both sub-windows moved +0.15% and +0.11% while the POOLED delta was -0.03%.
    That is not a bug in either number -- widening a liveness dial admits more episodes (n went 369 -> 391
    -> 442 across the three points), so the pooled median is taken over a different MIX than each half is.
    It is a Simpson's-paradox shape, and it is precisely the thing a single pooled figure would have
    hidden. The band keys on the pooled sign because that is the quantity a promotion would cite; the
    per-point `subwindow_deltas` are reported beside it so a reader can see the disagreement rather than
    inherit a silent choice about which to believe."""
    agreeing = [
        i for i, p in enumerate(points) if p.sign_agreement and p.delta_vs_baseline is not None
    ]
    if not agreeing:
        return []
    best = max(agreeing, key=lambda i: abs(points[i].delta_vs_baseline or 0.0))
    sign = (points[best].delta_vs_baseline or 0.0) >= 0
    runs: list[list[int]] = []
    current: list[int] = []
    for i, p in enumerate(points):
        ok = (
            p.sign_agreement
            and p.delta_vs_baseline is not None
            and ((p.delta_vs_baseline >= 0) == sign)
        )
        if ok:
            current.append(i)
        else:
            if current:
                runs.append(current)
            current = []
    if current:
        runs.append(current)
    return max(runs, key=len) if runs else []


def variants(grid: dict[str, list[Any]]) -> list[dict[str, Any]]:
    """The cartesian product of the swept dials, in a stable order — so a sweep's points, and therefore
    its curve, are reproducible."""
    names = sorted(grid)
    return [dict(zip(names, combo)) for combo in itertools.product(*(grid[n] for n in names))]


def run_sweep(
    conn: psycopg.Connection,
    *,
    grid: dict[str, list[Any]],
    start: date,
    end: date,
    pin: datetime,
    hypothesis: str,
    decision_rule: str,
    subwindows: int = 2,
    regime: str | None = None,
    workers: int = 1,
    null_draws: int = DEFAULT_DRAWS,
    clock: Literal["record", "public"] = "record",
    root: str | Path | None = None,
) -> SweepReport:
    """Export the mirror ONCE, replay every variant over it, and report the curve.

    The CLOCK is exported into that one mirror and every point INHERITS it — the points are run with no
    clock argument at all, so "one tape, one axis, N dial settings" is structural rather than something
    each call site has to repeat correctly. It is also why the mirror directory is named for the clock:
    a record-clock and a public-clock sweep of the same window at the same pin would otherwise re-export
    over each other's Parquet, and the earlier sweep's points would end up citing a mirror hash that no
    longer describes the tape they actually swept."""
    root_path = Path(root or store.DEFAULT_ROOT)
    mirror = root_path / "mirrors" / f"{pin.strftime('%Y%m%dT%H%M%SZ')}-{start}-{end}-{clock}"
    mirror.mkdir(parents=True, exist_ok=True)
    # DEFAULT_TENANT_ID explicitly, here and in the points below (`execute`'s own default). The sweep took
    # a `tenant_id` parameter that it then ignored on both legs -- a parameter accepted and dropped is worse
    # than none, because a caller reads it as honored. It is removed rather than wired: nothing can pass one
    # (there is no `--tenant` on this CLI or on `backtest.run`) and the whole backtest package is
    # single-tenant by construction. Multi-tenant sweeps are a real change, not a parameter.
    export_snapshot(conn, mirror, tenant_id=DEFAULT_TENANT_ID, clock=clock)

    points: list[SweepPoint] = []
    baseline: _Scored | None = None
    baseline_short = short_hash(config_hash(DEFAULT_CONFIG)) or ""
    subs = _sub_bounds(start, end, subwindows)

    for dials in variants(grid):
        cfg = apply_overlay(dials)
        outcome = execute(
            conn,
            start=start,
            end=end,
            pin=pin,
            cfg=cfg,
            mirror_dir=mirror,
            workers=workers,  # B5b, now on main -- a pass-through per point
            null_draws=null_draws,  # B4, likewise; a sweep pays this PER POINT
            hypothesis=hypothesis,
            decision_rule=decision_rule,
            regime=regime,
            root=root_path,
        )
        scored = _read_scored(outcome.path)
        is_baseline = all(
            getattr(DEFAULT_CONFIG, k) == v or str(getattr(DEFAULT_CONFIG, k)) == str(v)
            for k, v in dials.items()
        )
        if is_baseline or baseline is None:
            baseline = scored
        points.append(
            SweepPoint(
                dials=dials,
                run_id=outcome.run_id,
                config_short=outcome.manifest.config_short,
                n_episodes=scored.n_episodes,
                n_scored=len(scored.values),
                metric=_median_in(scored, start, end),
                is_baseline=is_baseline,
            )
        )

    # second pass: the deltas need the baseline, which is only known once every point has run
    for p, dials in zip(points, variants(grid), strict=True):
        assert p.dials == dials
        run_dir = store.run_dir(p.run_id, root_path)
        scored = _read_scored(run_dir) if run_dir else _Scored(0, [])
        base_overall = _median_in(baseline, start, end) if baseline else None
        p.delta_vs_baseline = (
            None if p.metric is None or base_overall is None else round(p.metric - base_overall, 6)
        )
        deltas: list[float | None] = []
        for lo, hi in subs:
            here, there = _median_in(scored, lo, hi), (
                _median_in(baseline, lo, hi) if baseline else None
            )
            deltas.append(None if here is None or there is None else round(here - there, 6))
        p.subwindow_deltas = deltas
        known = [d for d in deltas if d is not None]
        # EVERY sub-window must agree, and a sub-window with no data does not get to abstain into a yes:
        # a point that could only be measured on half the window has not demonstrated stability.
        p.sign_agreement = (
            len(known) == len(deltas)
            and len(known) >= 2
            and (all(d >= 0 for d in known) or all(d <= 0 for d in known))
        )

    return SweepReport(
        dial_names=sorted(grid),
        window_start=start,
        window_end=end,
        subwindows=len(subs),
        clock=clock,  # the axis that one tape carries; every point inherited it
        mirror_hash=mirror_hash(mirror),  # the ONE frozen tape every point swept
        baseline_config_short=baseline_short,
        points=points,
        plateau=_plateau(points),
        banner=(
            "A CURVE, NOT A WINNER. One year of one regime, with episodes that are not independent, "
            "cannot support picking the best-scoring point -- that is fitting the tape. Read the PLATEAU "
            "(a contiguous band where the choice barely matters) and the per-point sign agreement across "
            "disjoint sub-windows. A point that wins pooled but disagrees across sub-windows has found "
            "nothing. Promotion of any dial remains a separate operator decision, on the back of run ids."
        ),
    )


def parse_values(raw: str) -> list[Any]:
    """``--values 30,60,90`` -> typed values. JSON per item, so ints, floats, booleans and quoted strings
    all round-trip; a bare word falls back to a string."""
    out: list[Any] = []
    for piece in raw.split(","):
        piece = piece.strip()
        try:
            out.append(json.loads(piece))
        except json.JSONDecodeError:
            out.append(piece)
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backtest.sweep",
        description=(
            "Sweep one dial (or a grid) across a range over ONE frozen mirror and report the CURVE: "
            "per-point deltas, sub-period sign agreement, and the plateau band. Never a winner."
        ),
    )
    p.add_argument("--start", required=True)
    p.add_argument("--end", required=True)
    p.add_argument("--pin", default=None, help="the known_at determinism pin (default: now, UTC)")
    p.add_argument("--dial", default=None, help="the dial to sweep (with --values)")
    p.add_argument("--values", default=None, help="comma-separated values for --dial")
    p.add_argument(
        "--grid",
        action="append",
        default=[],
        help="dial=v1,v2 -- repeatable, for a multi-dial variant set (H1/H3)",
    )
    p.add_argument(
        "--subwindows", type=int, default=2, help="disjoint sub-windows for sign agreement"
    )
    p.add_argument("--hypothesis", required=True, help="pre-registration: what this sweep tests")
    p.add_argument("--decision-rule", required=True, help="what result would change your mind")
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            "replay N theses in parallel WITHIN each sweep point (B5b). The points themselves stay "
            "sequential: they share one mirror and one Postgres, so running two points at once would "
            "contend for both while the wall clock is already bounded by the largest thesis."
        ),
    )
    p.add_argument(
        "--null-draws",
        type=int,
        default=DEFAULT_DRAWS,
        help=(
            "K null draws per episode, PER POINT (B4). A sweep pays this for every point, and the "
            "curve is read off the pooled metric rather than off the nulls, so a wide sweep is a "
            "reasonable place to lower it -- MEASURED: the nulls cost 269 s against 18 s of replay "
            "on a one-week window at K=10, because each timing draw prices the whole basket for its "
            "own benchmark window."
        ),
    )
    p.add_argument(
        "--clock",
        choices=("record", "public"),
        default="record",
        help=(
            "which clock the ONE shared mirror is exported on; every point of the sweep inherits it "
            "(see backtest.run --clock). A record sweep and a public sweep are different experiments "
            "and are never one curve."
        ),
    )
    p.add_argument("--regime", default=None)
    p.add_argument("--out-root", default=None)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    grid: dict[str, list[Any]] = {}
    if args.dial:
        if not args.values:
            print("ERROR: --dial requires --values", file=sys.stderr)
            return 2
        grid[args.dial] = parse_values(args.values)
    for spec in args.grid:
        if "=" not in spec:
            print(f"ERROR: --grid expects dial=v1,v2 (got {spec!r})", file=sys.stderr)
            return 2
        name, raw = spec.split("=", 1)
        grid[name.strip()] = parse_values(raw)
    if not grid:
        print("ERROR: nothing to sweep -- pass --dial/--values or --grid", file=sys.stderr)
        return 2
    try:  # fail on an unknown dial BEFORE paying for a mirror export
        for name, values in grid.items():
            apply_overlay({name: values[0]})
    except OverlayError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    pin = datetime.fromisoformat(args.pin) if args.pin else datetime.now(timezone.utc)
    if pin.tzinfo is None:
        pin = pin.replace(tzinfo=timezone.utc)

    conn = connect()
    try:
        report = run_sweep(
            conn,
            grid=grid,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            pin=pin,
            hypothesis=args.hypothesis,
            decision_rule=args.decision_rule,
            subwindows=args.subwindows,
            regime=args.regime,
            workers=args.workers,
            null_draws=args.null_draws,
            clock=args.clock,
            root=args.out_root,
        )
    finally:
        conn.close()

    root_path = Path(args.out_root or store.DEFAULT_ROOT)
    path = root_path / SWEEP_NAME
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"swept {report.dial_names} -> {len(report.points)} run(s) on the {report.clock} clock; "
        f"curve at {path}"
    )
    for p in report.points:
        mark = "=" if p.is_baseline else ("~" if p.sign_agreement else " ")
        print(
            f"  {mark} {p.dials} n={p.n_scored:4d} metric={p.metric} "
            f"delta={p.delta_vs_baseline} subwindows={p.subwindow_deltas}"
        )
    print(f"  plateau (indices, a BAND not a pick): {report.plateau}")
    return 0


def cfg_for(dials: dict[str, Any]) -> CallConfig:
    """The variant config for one point — exposed so a test can assert the read bounds move with it."""
    return apply_overlay(dials)


if __name__ == "__main__":
    raise SystemExit(main())
