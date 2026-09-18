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
from collections.abc import Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any, Literal, NamedTuple

import psycopg
from pydantic import BaseModel, Field

from backtest import manifest as mf
from backtest import store
from backtest.config_overlay import OverlayError, apply_overlay
from backtest.manifest import mirror_hash
from backtest.nulls import DEFAULT_DRAWS
from backtest.run import execute
from backtest.windows import DEFAULT_WINDOW_DAYS, default_concurrency, tile
from db.session import DEFAULT_TENANT_ID, connect
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash, short_hash
from replay.export import export_snapshot

SWEEP_NAME = "sweep.json"


class SweepPoint(BaseModel):
    """One dial setting, its run, and how it behaved — never a rank."""

    dials: dict[str, Any] = Field(default_factory=dict)
    # ONE PER WINDOW (S1). A point is no longer one run: the unit of work is a sub-window, so a point is
    # the POOLED read across its window runs and every one of them is addressable.
    run_ids: list[str] = Field(default_factory=list)
    config_short: str
    n_episodes: int = 0
    n_scored: int = 0
    metric: float | None = (
        None  # the pooled median forward return at this point, across all windows
    )
    delta_vs_baseline: float | None = None
    # the same delta recomputed on each WINDOW, in order — these replaced the old sub-window split of one
    # long run, and they are a stronger question: the windows are separate measurements, not slices of one
    window_deltas: list[float | None] = Field(default_factory=list)
    # True only when EVERY window moved the same way. A pooled number that cannot survive being recomputed
    # on each window separately has not found anything.
    sign_agreement: bool = False
    is_baseline: bool = False
    # True when this point's runs are SHARED with another point — two dial settings that resolve to the
    # same config are one measurement, and the baseline is usually shared by every ladder that contains
    # the production default. Stated so a shared point is not read as an independent confirmation.
    runs_shared: bool = False


class SweepReport(BaseModel):
    """The curve. Deliberately carries no winner — see the module docstring."""

    dial_names: list[str] = Field(default_factory=list)
    metric_name: str = "arm_timing_forward_return_median"
    # the PASS span: the first window's start and the last window's end
    window_start: date
    window_end: date
    # the disjoint windows every point was run over, in order (S1). A point's `window_deltas` line up with
    # these index for index.
    windows: list[tuple[date, date]] = Field(default_factory=list)
    # how many window jobs ran at once — recorded because a saturated box measures contention as well as
    # dials, and a reader comparing two passes should be able to see if they ran under different load
    concurrency: int = 1
    #: the pass id every one of these runs carries on its own manifest and registry row
    pass_id: str = ""
    #: This curve's own pre-registration. It lives on the CURVE and not only on the runs because a pass
    #: shares runs between ladders — the production baseline most of all — and a shared run cannot carry
    #: six different hypotheses. The runs carry the PASS's text; each curve carries its own.
    hypothesis: str = ""
    decision_rule: str = ""
    #: every curve in this pass, by dial — so a reader holding one curve knows the others exist and that
    #: they were measured against the same baseline over the same tape
    pass_curves: list[str] = Field(default_factory=list)
    # WHICH AXIS the one shared mirror carries. On the curve it is not decoration: a record-clock sweep and
    # a public-clock sweep of the same dial are two different experiments and must never be read as one
    # series, and the run ids below are the only other place that could be checked.
    clock: Literal["record", "public"] = "record"
    mirror_hash: str = ""
    #: the short hash of the baseline every delta is measured against -- the point ACTUALLY used
    baseline_config_short: str = ""
    #: False when the grid did not contain the production default and the first point stood in. Every
    #: delta on this curve is then relative to a chosen setting rather than to today's behavior, which is
    #: a different claim and must not be read as "better than production".
    baseline_is_default: bool = True
    points: list[SweepPoint] = Field(default_factory=list)
    # the widest contiguous run of points whose sign agrees with the best-behaving one, as INDICES into
    # `points`. A band, not a pick: if it is one point wide, the sweep found nothing worth adopting.
    plateau: list[int] = Field(default_factory=list)
    banner: str = ""


@dataclass(frozen=True)
class _Scored:
    n_episodes: int
    values: list[tuple[date, float]]  # (arm_date, forward_return)


class MissingRunArtifacts(FileNotFoundError):
    """A run this curve cites has no readable outcomes on disk."""


def _pooled(run_ids: Sequence[str], root: Path) -> _Scored:
    """The pooled outcomes for ONE point — its window runs, read and concatenated.

    **A missing run is a corrupted store, and it RAISES.** Every launched run is registered under the
    index lock before this reads anything, so a run id on the curve with no directory behind it means the
    store lost something. Tolerating it would be the worse failure: the point would silently pool over
    FEWER windows, and its metric, its n_episodes and its per-window deltas would all just be quietly
    smaller with nothing on the curve to say so. A pass that stops is recoverable; a curve that under-reports
    by an unknown amount is not."""
    dirs: list[Path] = []
    for rid in run_ids:
        d = store.run_dir(rid, root)
        if d is None or not (d / "outcomes.parquet").is_file():
            raise MissingRunArtifacts(
                f"run {rid!r} is cited by this curve but has no readable outcomes at "
                f"{store.runs_root(root) / rid}. Every launched run is registered before the curve is "
                f"assembled, so this means the store lost an artifact -- the pass stops rather than "
                f"pooling the point over fewer windows without saying so."
            )
        dirs.append(d)
    return _read_scored(dirs)


def _read_scored(run_dirs: Path | Sequence[Path]) -> _Scored:
    """Read outcomes back off the runs' own Parquet and POOL them — the artifacts are the source of truth,
    so a point on the curve is derived from the same bytes a reviewer can open.

    Takes N directories because a point is N window runs (S1). Pooling is a concatenation and nothing more:
    the windows are disjoint, so no episode can appear twice, and each run's rows carry their own arm dates
    — which is what lets the same pooled set be re-filtered per window for the deltas without re-reading.
    """
    import pyarrow.parquet as pq

    dirs = [run_dirs] if isinstance(run_dirs, Path) else list(run_dirs)
    n_episodes = 0
    vals: list[tuple[date, float]] = []
    for d in dirs:
        table = pq.read_table(d / "outcomes.parquet").to_pylist()
        n_episodes += len(table)
        vals += [
            (date.fromisoformat(r["arm_date"]), r["forward_return"])
            for r in table
            if r.get("forward_return") is not None and r.get("arm_date")
        ]
    return _Scored(n_episodes=n_episodes, values=vals)


def _median_in(scored: _Scored, lo: date, hi: date) -> float | None:
    vals = [v for d, v in scored.values if lo <= d <= hi]
    return round(median(vals), 6) if vals else None


def _plateau(points: list[SweepPoint]) -> list[int]:
    """The widest CONTIGUOUS run of points that both agree across WINDOWS and move the same way as the
    strongest agreeing point. A band rather than a pick; a one-wide band means nothing was found.

    THE BASELINE COUNTS AS AGREEING, and that is deliberate: its delta is 0 by construction, so a band
    spanning it says "these settings are indistinguishable from today", which is a real and useful answer.

    A POINT CAN AGREE ACROSS WINDOWS AND STILL FALL OUTSIDE THE BAND, and the first real sweep hit
    exactly that: at 365 days both halves moved +0.15% and +0.11% while the POOLED delta was -0.03%.
    That is not a bug in either number -- widening a liveness dial admits more episodes (n went 369 -> 391
    -> 442 across the three points), so the pooled median is taken over a different MIX than each half is.
    It is a Simpson's-paradox shape, and it is precisely the thing a single pooled figure would have
    hidden. The band keys on the pooled sign because that is the quantity a promotion would cite; the
    per-point `window_deltas` are reported beside it so a reader can see the disagreement rather than
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


@dataclass(frozen=True)
class Ladder:
    """ONE CURVE in a pass: a dial swept across its values, with its own pre-registration.

    Distinct from a ``grid``, which is a cartesian variant SET producing a single curve (H3's on/off/scope
    comparison). A pass is N ladders — six dials, six curves — sharing one mirror, one pass id and one
    baseline."""

    dial_names: list[str]
    variants: list[dict[str, Any]]
    hypothesis: str
    decision_rule: str

    @property
    def key(self) -> str:
        return "-".join(self.dial_names) or "grid"


def run_sweep(
    conn: psycopg.Connection,
    *,
    grid: dict[str, list[Any]],
    windows: Sequence[tuple[date, date]],
    pin: datetime,
    hypothesis: str,
    decision_rule: str,
    regime: str | None = None,
    workers: int = 1,
    null_draws: int = DEFAULT_DRAWS,
    clock: Literal["record", "public"] = "record",
    concurrency: int = 1,
    root: str | Path | None = None,
    now: datetime | None = None,
    resume: str | None = None,
) -> SweepReport:
    """ONE curve from a cartesian ``grid`` — the single-curve front door onto ``run_pass``."""
    return run_pass(
        conn,
        ladders=[Ladder(sorted(grid), variants(grid), hypothesis, decision_rule)],
        windows=windows,
        pin=pin,
        hypothesis=hypothesis,
        decision_rule=decision_rule,
        regime=regime,
        workers=workers,
        null_draws=null_draws,
        clock=clock,
        concurrency=concurrency,
        root=root,
        now=now,
        resume=resume,
    )[0]


def _resume_index(pass_id: str, root: Path) -> dict[tuple[str, str, str], str]:
    """The ``(config_hash, window start, window end) -> run_id`` pairs this pass has ALREADY measured.

    Read from the REGISTRY rather than by walking directories, because the registry is the thing that says
    a run finished: ``execute`` registers under the index lock as its last act. A row whose outcomes are
    not readable does not count — a job killed mid-write leaves a directory and can leave a row, and
    reusing it would pool a point over a truncated run."""
    found: dict[tuple[str, str, str], str] = {}
    for r in store.list_runs(root):
        if r.pass_id != pass_id:
            continue
        d = store.run_dir(r.run_id, root)
        if d is None or not (d / "outcomes.parquet").is_file():
            continue
        found[(r.config_hash, r.window_start, r.window_end)] = r.run_id
    return found


def run_pass(
    conn: psycopg.Connection,
    *,
    ladders: Sequence[Ladder],
    windows: Sequence[tuple[date, date]],
    pin: datetime,
    hypothesis: str,
    decision_rule: str,
    regime: str | None = None,
    workers: int = 1,
    null_draws: int = DEFAULT_DRAWS,
    clock: Literal["record", "public"] = "record",
    concurrency: int = 1,
    root: str | Path | None = None,
    now: datetime | None = None,
    resume: str | None = None,
) -> list[SweepReport]:
    """Export ONE mirror, run every distinct (config x WINDOW) over it once, and report a curve per ladder.

    **THE UNIT OF WORK IS A WINDOW, and a curve point is the POOLED read across its windows.** The reason
    is the ANALYSIS, not throughput: the windows are separate measurements, so a point's delta being
    recomputed on each asks whether a dial helps consistently rather than on average. A year-long run is
    also not a unit of work on this box — MEASURED, one did not produce a registry row in 3h40m before it
    was killed — and a window job that dies costs ~90 s rather than an hour. See ``backtest/windows.py``.

    It costs nothing in fidelity. ``export_snapshot`` takes no date bound, so the mirror is the whole tape
    whatever window reads it — ONE export serves every window of every point of every LADDER, which is
    also what keeps a delta attributable to the dial rather than to a second snapshot of a moving
    database. Every job inherits that mirror's clock (CW).

    **THE BASELINE IS RUN ONCE FOR THE WHOLE PASS.** Variants are deduplicated by ``config_hash`` ACROSS
    ladders, so the production default — which every ladder contains — is measured once per window and
    shared by all six curves. MEASURED cost of not doing this: six separate sweeps re-ran their own
    baseline, 45 redundant runs and five redundant exports, about 1.1 h of a 6.5 h pass. Points that share
    runs are marked ``runs_shared``, so a shared point is never read as an independent confirmation.

    **PRE-REGISTRATION.** The runs carry the PASS's hypothesis and decision rule, and they have to: the
    baseline run belongs to six curves at once and cannot carry six different texts. Each CURVE carries
    its own on the report, which is the artifact a reader quotes a dial's result from.

    **``resume``** takes a pass id and skips any ``(config, window)`` whose run is already registered under
    it with readable outcomes, re-running only the rest and assembling the curve from the union. The seed
    is preserved by construction, because the seed IS the pass id — so a resumed pass draws the same nulls
    as the one it continues. A dead pass's completed runs still count as trials in the registry; resume
    does not mint new ones, which is precisely why it is the cheaper recovery.
    """
    if not windows:
        raise ValueError("a pass needs at least one window")
    if not ladders:
        raise ValueError("a pass needs at least one ladder")
    windows = [(lo, hi) for lo, hi in windows]
    pass_start, pass_end = windows[0][0], windows[-1][1]
    root_path = Path(root or store.DEFAULT_ROOT)
    now = now or datetime.now(timezone.utc)
    pass_id = resume or mf.make_pass_id(hypothesis=hypothesis, now=now, clock=clock)

    mirror = (
        root_path / "mirrors" / f"{pin.strftime('%Y%m%dT%H%M%SZ')}-{pass_start}-{pass_end}-{clock}"
    )
    mirror.mkdir(parents=True, exist_ok=True)
    # DEFAULT_TENANT_ID explicitly, here and in the points below (`execute`'s own default). The sweep took
    # a `tenant_id` parameter that it then ignored on both legs -- a parameter accepted and dropped is worse
    # than none, because a caller reads it as honored. It is removed rather than wired: nothing can pass one
    # (there is no `--tenant` on this CLI or on `backtest.run`) and the whole backtest package is
    # single-tenant by construction. Multi-tenant sweeps are a real change, not a parameter.
    export_snapshot(conn, mirror, tenant_id=DEFAULT_TENANT_ID, clock=clock)

    # every variant of every ladder, resolved to a config -- then ONE job per distinct config_hash
    per_ladder = [[(d, apply_overlay(d)) for d in lad.variants] for lad in ladders]
    flat = [pair for group in per_ladder for pair in group]
    by_hash: dict[str, CallConfig] = {}
    for _, cfg in flat:
        by_hash.setdefault(config_hash(cfg), cfg)
    shared_hashes = {h for h in by_hash if sum(1 for _, c in flat if config_hash(c) == h) > 1}

    already = _resume_index(pass_id, root_path) if resume else {}
    run_ids: dict[tuple[str, int], str] = {}
    jobs: list[_Job] = []
    keys: list[tuple[str, int]] = []
    for h, cfg in by_hash.items():
        for w, (lo, hi) in enumerate(windows):
            done = already.get((h, lo.isoformat(), hi.isoformat()))
            if done is not None:
                run_ids[(h, w)] = done
                continue
            keys.append((h, w))
            jobs.append(
                _Job(
                    mirror=str(mirror),
                    start=lo.isoformat(),
                    end=hi.isoformat(),
                    pin=pin.isoformat(),
                    cfg_json=cfg.model_dump_json(),
                    workers=workers,
                    null_draws=null_draws,
                    # ONE seed for the whole pass, so the same episode draws the SAME counterfactuals in
                    # every point it appears in. The seed otherwise defaults to the run_id, which differs
                    # per run by construction -- and then two points' null distributions would differ by
                    # the seed as well as by the dial, which is noise in exactly the comparison the curve
                    # exists to make. It is also what makes a RESUMED pass draw identically.
                    null_seed=pass_id,
                    hypothesis=hypothesis,
                    decision_rule=decision_rule,
                    regime=regime,
                    pass_id=pass_id,
                    root=str(root_path),
                )
            )
    run_ids.update(zip(keys, _launch(jobs, concurrency), strict=True))

    scored_by_hash: dict[str, _Scored] = {}
    reports: list[SweepReport] = []
    curve_keys = [lad.key for lad in ladders]
    for lad, cfgs in zip(ladders, per_ladder, strict=True):
        points: list[SweepPoint] = []
        for dials, cfg in cfgs:
            h = config_hash(cfg)
            ids = [run_ids[(h, w)] for w in range(len(windows))]
            # `if not in` rather than `setdefault`: the latter evaluates its default EAGERLY, so every
            # point sharing a config would re-read all N Parquet files only to discard them.
            if h not in scored_by_hash:
                scored_by_hash[h] = _pooled(ids, root_path)
            scored = scored_by_hash[h]
            points.append(
                SweepPoint(
                    dials=dials,
                    run_ids=ids,
                    config_short=short_hash(h) or "",
                    n_episodes=scored.n_episodes,
                    n_scored=len(scored.values),
                    metric=_median_in(scored, pass_start, pass_end),
                    is_baseline=h == config_hash(DEFAULT_CONFIG),
                    runs_shared=h in shared_hashes,
                )
            )

        # THE BASELINE ACTUALLY USED. When the ladder contains the production default it is that point;
        # when it does not -- a ladder that brackets today's value without including it -- the first point
        # stands in, and the report says so rather than reporting DEFAULT_CONFIG's hash for a baseline
        # that is not it.
        baseline_point = next((p for p in points if p.is_baseline), points[0] if points else None)
        baseline = (
            scored_by_hash[config_hash(apply_overlay(baseline_point.dials))]
            if baseline_point
            else None
        )
        for p in points:
            scored = scored_by_hash[config_hash(apply_overlay(p.dials))]
            base_overall = _median_in(baseline, pass_start, pass_end) if baseline else None
            p.delta_vs_baseline = (
                None
                if p.metric is None or base_overall is None
                else round(p.metric - base_overall, 6)
            )
            deltas: list[float | None] = []
            for lo, hi in windows:
                here, there = _median_in(scored, lo, hi), (
                    _median_in(baseline, lo, hi) if baseline else None
                )
                deltas.append(None if here is None or there is None else round(here - there, 6))
            p.window_deltas = deltas
            known = [d for d in deltas if d is not None]
            # EVERY window must agree, and a window with no data does not get to abstain into a yes: a
            # point measurable on only some of the pass has not demonstrated stability. Two is the floor --
            # one window cannot agree with anything, so a single-window pass reports no agreement at all,
            # which is the honest answer rather than a vacuous True.
            p.sign_agreement = (
                len(known) == len(deltas)
                and len(known) >= 2
                and (all(d >= 0 for d in known) or all(d <= 0 for d in known))
            )

        reports.append(
            SweepReport(
                dial_names=list(lad.dial_names),
                window_start=pass_start,
                window_end=pass_end,
                windows=windows,
                concurrency=concurrency,
                pass_id=pass_id,
                hypothesis=lad.hypothesis,
                decision_rule=lad.decision_rule,
                pass_curves=curve_keys,
                clock=clock,  # the axis that one tape carries; every point inherited it
                mirror_hash=mirror_hash(mirror),  # the ONE frozen tape every point swept
                baseline_config_short=(baseline_point.config_short if baseline_point else ""),
                baseline_is_default=bool(baseline_point and baseline_point.is_baseline),
                points=points,
                plateau=_plateau(points),
                banner=(
                    "A CURVE, NOT A WINNER. One regime, with episodes that are not independent, cannot "
                    "support picking the best-scoring point -- that is fitting the tape. Read the PLATEAU "
                    "(a contiguous band where the choice barely matters) and the per-point sign agreement "
                    "ACROSS WINDOWS: each point is pooled over disjoint windows that were run as separate "
                    "measurements, and a point that wins pooled but disagrees across them has found "
                    "nothing. Points marked as sharing runs are one measurement cited twice, not two -- "
                    "the baseline is shared by every curve in this pass. Promotion of any dial remains a "
                    "separate operator decision, on the back of run ids."
                ),
            )
        )
    return reports


class _Job(NamedTuple):
    """One window job's whole payload, as primitives.

    Primitives because `spawn` is the start method on Windows: a worker re-imports the module and
    inherits nothing from the parent's memory, which is also why the mirror travels as a PATH rather than
    as an open connection and the config as JSON rather than as a model."""

    mirror: str
    start: str
    end: str
    pin: str
    cfg_json: str
    workers: int
    null_draws: int
    null_seed: str
    hypothesis: str
    decision_rule: str
    regime: str | None
    pass_id: str
    root: str


def _run_window(job: _Job) -> str:
    """Run ONE window job in a fresh process and return its run id.

    The job opens its own database connection: `execute` needs one for the roster reads and a connection
    cannot cross a process boundary. The database is otherwise untouched by a pass after the export, so
    concurrent jobs contend on CPU alone."""
    # `connect` and `execute` come from this module's own top-level imports: a spawned worker re-imports
    # `backtest.sweep` anyway, so a local re-import would buy nothing and shadow the names.
    conn = connect()
    try:
        outcome = execute(
            conn,
            start=date.fromisoformat(job.start),
            end=date.fromisoformat(job.end),
            pin=datetime.fromisoformat(job.pin),
            cfg=CallConfig.model_validate_json(job.cfg_json),
            mirror_dir=job.mirror,  # inherits the mirror's clock (CW); a disagreeing one is refused
            workers=job.workers,
            null_draws=job.null_draws,
            null_seed=job.null_seed,
            hypothesis=job.hypothesis,
            decision_rule=job.decision_rule,
            regime=job.regime,
            pass_id=job.pass_id,
            root=job.root,
        )
        return outcome.run_id
    finally:
        conn.close()


def _launch(jobs: list[_Job], concurrency: int) -> list[str]:
    """Run the window jobs, at most ``concurrency`` at a time, and return their run ids IN ORDER.

    ``concurrency <= 1`` runs them in this process, in order — the proven path, not a one-worker special
    case of a new one (the same rule `replay_all_parallel` holds). Above that, a process pool: separate
    processes are the point, because each job's null phase is serial within itself.

    Order is preserved because the caller zips these back onto ``(config, window)`` keys. A job that
    raises is allowed to take the pass down rather than being swallowed: a curve missing a point it
    believes it measured is worse than a pass that stopped.

    **ONE VENV FOR A WHOLE PASS.** Every window job must run from the same interpreter, because the
    Parquet writer stamps its own build into the file (`created_by`), so two venvs with different pyarrow
    versions produce artifacts that differ byte for byte while being identical row for row — and the
    mirror hash, which is a hash of those bytes, differs with them. MEASURED while verifying M1: two runs
    of the same window on the same data differed in exactly that string and nothing else. The jobs inherit
    this process's interpreter, so a pass launched from one place is safe by construction; it is running
    PART of a pass from a second checkout that breaks it.

    **CONCURRENCY IS NO LONGER THE POINT IT WAS.** The split was designed when the null phase was serial
    and 96% of a run; after the tape memo the nulls are ~7 s and the REPLAY dominates, and the replay
    already fans out over its own workers. Two concurrent jobs still help — one job's wall clock is its
    LARGEST thesis, so its workers idle near the end and a second job fills that tail — but the box is
    ~2.8 usable cores either way, so expect a fraction, not a factor. The split's durable value is the
    analysis (cross-window agreement between separate measurements) and blast radius (a job that dies
    costs ~90 s, not an hour), not throughput."""
    if concurrency <= 1:
        return [_run_window(job) for job in jobs]
    with ProcessPoolExecutor(max_workers=concurrency) as pool:
        return list(pool.map(_run_window, jobs))


def split_spec(spec: str, flag: str) -> tuple[str, str]:
    """``dial=rest`` -> ``(dial, rest)``. One parser for ``--grid``, ``--ladder`` and the per-ladder
    pre-registration overrides, so they cannot drift on what counts as a well-formed spec."""
    if "=" not in spec:
        raise OverlayError(f"{flag} expects dial=... (got {spec!r})")
    name, raw = spec.split("=", 1)
    if not name.strip():
        raise OverlayError(f"{flag} expects a dial name before the '=' (got {spec!r})")
    return name.strip(), raw


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
        help=(
            "dial=v1,v2 -- repeatable, for a CARTESIAN variant set producing ONE curve (H3's "
            "on/off/scope comparison). Not the same thing as --ladder."
        ),
    )
    p.add_argument(
        "--ladder",
        action="append",
        default=[],
        help=(
            "dial=v1,v2,... -- repeatable, ONE CURVE PER LADDER in a single pass. Every ladder shares one "
            "mirror export, one pass id and ONE BASELINE: the production default appears in every ladder "
            "and is deduplicated by config_hash ACROSS them, so it is measured once per window rather "
            "than once per dial. MEASURED cost of not doing that: six separate sweeps re-ran their own "
            "baseline -- 45 redundant runs and five redundant exports, about 1.1 h of a 6.5 h pass."
        ),
    )
    p.add_argument(
        "--ladder-hypothesis",
        action="append",
        default=[],
        metavar="DIAL=TEXT",
        help=(
            "override the pass's --hypothesis for ONE ladder's curve. The RUNS always carry the pass's "
            "text and must: the baseline run belongs to every curve at once and cannot carry six "
            "different hypotheses. This rides the curve file, which is what a dial's result is quoted "
            "from."
        ),
    )
    p.add_argument(
        "--ladder-decision-rule",
        action="append",
        default=[],
        metavar="DIAL=TEXT",
        help="override the pass's --decision-rule for ONE ladder's curve (see --ladder-hypothesis)",
    )
    p.add_argument(
        "--resume",
        default=None,
        metavar="PASS_ID",
        help=(
            "continue an interrupted pass: skip every (config, window) already registered under this "
            "pass id WITH readable outcomes, re-run the rest, and assemble the curves from the union. "
            "The nulls draw identically because the seed IS the pass id. NOTE: the dead pass's completed "
            "runs already count as trials in the registry and resume does not mint new ones -- which is "
            "exactly why it is the cheaper recovery."
        ),
    )
    p.add_argument(
        "--window-days",
        type=int,
        default=DEFAULT_WINDOW_DAYS,
        help=(
            f"tile [--start, --end] into disjoint windows of at most this many days (default "
            f"{DEFAULT_WINDOW_DAYS}). THE WINDOW IS THE UNIT OF WORK: each point is run once per window, "
            "its metric is pooled across them and its delta is recomputed on each -- so sign agreement is "
            "agreement across genuinely separate measurements rather than across slices of one run. A "
            "window job that dies also costs ~90 s rather than an hour."
        ),
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=None,
        help=(
            "how many window jobs run at once (default: this box's measured ceiling, 2). Each job is a "
            "whole backtest.run whose replay phase may itself fan out, and MEASURED usable parallelism "
            "here is ~2.8 cores -- a pass that saturates its box measures contention as well as dials."
        ),
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
    if args.ladder and (args.dial or args.grid):
        print(
            "ERROR: --ladder is a set of CURVES (one per dial) and --dial/--grid is ONE curve; "
            "pass one or the other, not both",
            file=sys.stderr,
        )
        return 2
    try:
        ladders = _ladders_from_args(args)
    except OverlayError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    if not ladders:
        print(
            "ERROR: nothing to sweep -- pass --ladder, or --dial/--values, or --grid",
            file=sys.stderr,
        )
        return 2

    pin = datetime.fromisoformat(args.pin) if args.pin else datetime.now(timezone.utc)
    if pin.tzinfo is None:
        pin = pin.replace(tzinfo=timezone.utc)

    conn = connect()
    try:
        reports = run_pass(
            conn,
            ladders=ladders,
            pin=pin,
            hypothesis=args.hypothesis,
            decision_rule=args.decision_rule,
            windows=tile(
                date.fromisoformat(args.start), date.fromisoformat(args.end), args.window_days
            ),
            concurrency=(
                args.concurrency if args.concurrency is not None else default_concurrency()
            ),
            regime=args.regime,
            workers=args.workers,
            null_draws=args.null_draws,
            clock=args.clock,
            root=args.out_root,
            resume=args.resume,
        )
    finally:
        conn.close()

    root_path = Path(args.out_root or store.DEFAULT_ROOT)
    kept = [write_curve(r, root_path) for r in reports]
    # `sweep.json` keeps its shape and holds the LAST curve of the pass -- latest-only, unchanged, so the
    # /backtest sweep view renders exactly as before. Every curve of the pass is named in each report's
    # `pass_curves`, and each is kept under `sweeps/<pass_id>-<dial>.json` where nothing overwrites it.
    path = root_path / SWEEP_NAME
    path.write_text(reports[-1].model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    print(
        f"pass {reports[-1].pass_id}: {len(reports)} curve(s) over {len(reports[-1].windows)} "
        f"window(s) on the {reports[-1].clock} clock at concurrency {reports[-1].concurrency}"
        + (f" (resumed {args.resume})" if args.resume else "")
    )
    for report, keep in zip(reports, kept, strict=True):
        print(f"  {report.dial_names} -> {len(report.points)} point(s); kept at {keep}")
        for p in report.points:
            mark = "=" if p.is_baseline else ("~" if p.sign_agreement else " ")
            shared = " (shared runs)" if p.runs_shared else ""
            print(
                f"    {mark} {p.dials} n={p.n_scored:4d} metric={p.metric} "
                f"delta={p.delta_vs_baseline} windows={p.window_deltas}{shared}"
            )
        print(f"    plateau (indices, a BAND not a pick): {report.plateau}")
    print(f"  latest curve also at {path}")
    return 0


def write_curve(report: SweepReport, root: Path) -> Path:
    """Keep one curve where the next pass cannot overwrite it.

    `sweep.json` is latest-only -- a known gap, and one a multi-curve pass leans on much harder: without
    this the only record of which runs formed which curve would be a file the next sweep replaces. (The
    runs themselves also carry `pass_id`, so the grouping survives even if every curve file is lost.)
    """
    keep = root / "sweeps" / f"{report.pass_id}-{'-'.join(report.dial_names) or 'grid'}.json"
    keep.parent.mkdir(parents=True, exist_ok=True)
    keep.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n")
    return keep


def _ladders_from_args(args: argparse.Namespace) -> list[Ladder]:
    """The pass's curves, from either front door.

    ``--ladder`` is N curves, one per dial. ``--dial``/``--grid`` is ONE curve over a cartesian variant
    set, which is what H3 needs (its three arms live on two dials). Unknown dials fail HERE, before the
    mirror export, because a typo should not cost a minute of Parquet."""
    overrides = {
        "hypothesis": dict(
            split_spec(spec, "--ladder-hypothesis") for spec in args.ladder_hypothesis
        ),
        "decision_rule": dict(
            split_spec(spec, "--ladder-decision-rule") for spec in args.ladder_decision_rule
        ),
    }
    ladders: list[Ladder] = []
    if args.ladder:
        for spec in args.ladder:
            name, raw = split_spec(spec, "--ladder")
            values = parse_values(raw)
            apply_overlay({name: values[0]})  # unknown dial -> OverlayError, before any work
            ladders.append(
                Ladder(
                    dial_names=[name],
                    variants=[{name: v} for v in values],
                    hypothesis=overrides["hypothesis"].get(name, args.hypothesis),
                    decision_rule=overrides["decision_rule"].get(name, args.decision_rule),
                )
            )
        unknown = set(overrides["hypothesis"]) | set(overrides["decision_rule"])
        unknown -= {lad.dial_names[0] for lad in ladders}
        if unknown:
            raise OverlayError(
                f"per-ladder pre-registration given for dial(s) with no --ladder: {sorted(unknown)}"
            )
        return ladders

    grid: dict[str, list[Any]] = {}
    if args.dial:
        if not args.values:
            raise OverlayError("--dial requires --values")
        grid[args.dial] = parse_values(args.values)
    for spec in args.grid:
        name, raw = split_spec(spec, "--grid")
        grid[name] = parse_values(raw)
    if not grid:
        return []
    for name, values in grid.items():
        apply_overlay({name: values[0]})
    return [Ladder(sorted(grid), variants(grid), args.hypothesis, args.decision_rule)]


def cfg_for(dials: dict[str, Any]) -> CallConfig:
    """The variant config for one point — exposed so a test can assert the read bounds move with it."""
    return apply_overlay(dials)


if __name__ == "__main__":
    raise SystemExit(main())
