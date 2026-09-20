"""MARGINAL PER-FAMILY NULLS — does one algorithm family beat ITS OWN nulls, not just the whole book's?

The pooled report (`backtest/pooled.py`) reads the algorithm over the WHOLE book and over fully-crossed
cells, each beside its two nulls. What it does NOT answer is the MARGINAL question: take one family --
`key1_source=insider`, or `key1_source=insider AND confirmation_grade=core` -- and ask whether THAT pocket's
actual return beats a timing null and a name null drawn over THAT pocket. A data run on the stored actuals
can show a pocket with a positive median while the book is negative; that is not evidence until the pocket
is read against its own nulls.

The nulls are drawn at run time and persisted only as pool-level and fully-crossed-cell stats in
`pooled.json` -- the raw per-episode draws are not saved, and `sweep.py --metric-slice` re-slices only the
ACTUAL return (`outcomes.parquet`). So a marginal slice's own nulls have to be RE-DRAWN. This module
re-draws them FAITHFULLY: same K, same seed, each episode's own horizon and sub-seed, the run's population
rule, over the FROZEN mirror the pass already cites -- never a re-run of the pass, never a new mirror.

**WHY A FAITHFUL SUBSET IS EVEN POSSIBLE.** A draw is a pure function of `(run seed, episode identity)`:
`nulls.episode_seed` sub-seeds each episode off `sha256(seed|thesis|security|arm_date)`, the timing null
draws from the run's `sessions` (a per-RUN property) and the name null from the roster (a per-THESIS one) --
never from which other episodes co-armed. `tests/backtest/test_nulls.py::
test_adding_an_episode_does_not_reshuffle_the_others` pins exactly that. So `draw_nulls` over a subset
reproduces, episode for episode, the draws the full pass made -- a filter, not a fresh random draw.

**WHY IT IS TRUSTWORTHY.** Two run-level inputs are reconstructed rather than stored: the name-null roster
(the LIVE basket the run's nulls used -- `run.py` builds `roster_at` from `list_all`, date-independent --
which is exactly `manifest.member_ids`), and the timing-null `sessions` (`harness.trading_sessions` over the
union of those member ids and the manifest window, on the frozen mirror). Neither is trusted: for every run
the WHOLE pool is re-drawn and checked against the stored `pooled.json` (`vs_timing`/`vs_name` to
`rebench.TOLERANCE`), and a run that does not reproduce is REFUSED, never pooled. Whole-pool reproduction
implies every episode's draws reproduced, hence the slice's (a subset) are faithful -- the gate is the
proof, the same "worth only what the old numbers reproduce" rule `rebench.py` holds. It also makes the
point-in-time roster question moot: the numbers themselves are verified, not a roster proxy.

READ-ONLY over the frozen mirror + completed runs. No Postgres, no re-run, no write to the store. CLI only;
no served route, no OpenAPI contract.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

from backtest import manifest as mf
from backtest import store

# Intra-package reuse of the exact primitives the run itself used, so the re-draw cannot drift from it.
# Importing module-privates within the package follows the `rebench` precedent
# (`from backtest.nulls import _BasketBenchmark`): `_slice_key` is the canonical enum->wire stringify the
# pooled report groups on, and `_episodes` / `_find_mirror` are the one reader for a run's episodes and its
# frozen mirror.
from backtest.nulls import EpisodeNulls, draw_nulls
from backtest.pooled import SLICE_KEYS, Stat, _slice_key, build_report
from backtest.rebench import TOLERANCE, _episodes, _find_mirror
from db.session import DEFAULT_TENANT_ID
from replay.harness import trading_sessions
from replay.pit import connect_mirror
from replay.schema import Episode
from replay.scoring import RealizedPrices

#: The timing null draws from a window's trading sessions; below this it is near-vacuous and `vs_timing`
#: comes back close to the actual for a reason that has nothing to do with the timing. Mirrors
#: `frontend/src/backtest/pooled.ts::timingNullCaveat` (the backend equivalent it asks for).
SHORT_WINDOW_SESSIONS = 30

#: The pooled metric whose two nulls the reproduction gate verifies against — `pooled.build_report`'s only
#: metric, named once so a rename there fails loudly here rather than silently skipping the check.
_TIMING_METRIC = "arm_timing_forward_return"


class SliceKeyError(ValueError):
    """A slice named a dimension that is not an ALGORITHM dimension, or matched nothing.

    #4 enforced structurally, the same guard `sweep.MetricSliceError` applies: slicing by `thesis_id` is
    the leaderboard trap, and a typo like `key1_sources` (plural) would match nothing and read as a null
    result. The authority is `pooled.SLICE_KEYS` -- one definition of what a family IS, shared with the
    pooled report and the sweep. A local exception (not `sweep.MetricSliceError`) so this read-only tool
    need not import the sweep harness."""


@dataclass(frozen=True)
class SliceSpec:
    """One or two ``attr=value`` constraints on algorithm dimensions -- the marginal family to read."""

    pairs: tuple[tuple[str, str], ...]

    @classmethod
    def parse(cls, raw: str) -> "SliceSpec":
        """``key1_source=insider`` or ``key1_source=insider,confirmation_grade=core`` -> the spec.

        Refuses a thesis-level or unknown key (#4), an empty value, a repeated key, and more than two
        constraints (a marginal read is one family or a pair of them, never the whole cross)."""
        specs = [p.strip() for p in raw.split(",") if p.strip()]
        if not specs or len(specs) > 2:
            raise SliceKeyError(
                f"--slice takes one or two attr=value constraints (got {raw!r}); a marginal read is one "
                f"family or a pair of them, never the whole cross"
            )
        pairs: list[tuple[str, str]] = []
        seen: set[str] = set()
        for spec in specs:
            attr, sep, value = spec.partition("=")
            attr, value = attr.strip(), value.strip()
            if not sep or not value:
                raise SliceKeyError(f"--slice expects attr=value with a value (got {spec!r})")
            if attr not in SLICE_KEYS:
                raise SliceKeyError(
                    f"{attr!r} is not an algorithm dimension; a slice may only be read on "
                    f"{list(SLICE_KEYS)} -- slicing by thesis is the leaderboard trap (#4)"
                )
            if attr in seen:
                raise SliceKeyError(f"{attr!r} appears twice in {raw!r}")
            seen.add(attr)
            pairs.append((attr, value))
        return cls(pairs=tuple(pairs))

    @property
    def label(self) -> str:
        return ",".join(f"{a}={v}" for a, v in self.pairs)

    def matches(self, ep: Episode) -> bool:
        """Canonical family membership -- `_slice_key` is the exact enum->wire stringify build_report groups
        on, so a match here and a cell there can never disagree (None -> "none", Grade.CORE -> "core").
        """
        key = _slice_key(ep)
        return all(key.get(a) == v for a, v in self.pairs)


def _matches(ep: Episode, slice_: SliceSpec | None) -> bool:
    """``None`` is the WHOLE pool (used by the reproduction oracle); a spec is one family."""
    return slice_ is None or slice_.matches(ep)


def _slice_window(
    eps: Sequence[Episode], nulls: Sequence[EpisodeNulls], slice_: SliceSpec | None
) -> tuple[list[EpisodeNulls], Stat, Stat, Stat]:
    """The slice's SCOREABLE episode nulls (a faithful subset of ``nulls``) and its three Stats.

    Filters ``eps`` by family (``None`` = the whole pool), joins to the drawn ``nulls`` on episode identity,
    and keeps only ``forward_return is not None`` -- the same scoreable filter the pooled report applies.
    Pure, so the slicing is testable without a mirror on disk."""
    by_key = {(n.thesis_id, n.security_id, n.arm_date): n for n in nulls}
    slice_nulls = [
        n
        for ep in eps
        if _matches(ep, slice_)
        and (n := by_key.get((ep.thesis_id, ep.security_id, ep.arm_date))) is not None
        and n.forward_return is not None
    ]
    return (
        slice_nulls,
        Stat.of([n.forward_return for n in slice_nulls]),
        Stat.of([d.forward_return for n in slice_nulls for d in n.timing]),
        Stat.of([d.forward_return for n in slice_nulls for d in n.name]),
    )


@dataclass
class WindowNulls:
    """One window (= one baseline run) re-drawn over the slice, plus the reproduction verdict.

    A refused window contributes NOTHING to the pool -- its `slice_nulls` stays empty and its `refused`
    string says why (mirror mismatch, no pooled.json, or a re-draw that did not reproduce the stored one).
    """

    run_id: str
    window_start: date
    window_end: date
    null_draws: int = 0
    timing_candidate_sessions: int = 0
    reproduced: bool = False
    refused: str = ""
    #: the slice's SCOREABLE episode nulls (forward_return is not None) -- the faithful subset
    slice_nulls: list[EpisodeNulls] = field(default_factory=list)
    actual: Stat = field(default_factory=Stat)
    vs_timing: Stat = field(default_factory=Stat)
    vs_name: Stat = field(default_factory=Stat)

    @property
    def measurable(self) -> bool:
        """The slice had at least one scoreable episode in this window."""
        return self.actual.n > 0

    @property
    def short_window(self) -> bool:
        return 0 < self.timing_candidate_sessions < SHORT_WINDOW_SESSIONS


@dataclass
class SliceNullsReport:
    """The marginal family, pooled across the pass's windows, beside its two nulls -- and the per-window
    tally, because a pooled median can hide a single window carrying the pocket."""

    slice_label: str
    pass_id: str | None
    null_draws: int
    recomputed_at: str
    run_ids: list[str]
    refused: dict[str, str]
    # pooled across the kept windows, on RAW forward_return -- the headline the two nulls read against
    actual: Stat
    vs_timing: Stat
    vs_name: Stat
    # SECONDARY: the same excess-over-basket the pooled panel headlines on, free off the re-drawn nulls
    excess_vs_basket_median: Stat
    excess_vs_basket_mean: Stat
    n_episodes: int
    #: distinct (security_id, arm_date) name-days -- the independence-adjusted n (82% of episodes co-armed)
    effective_n: int
    pct_positive: float | None
    #: (windows where the slice's actual median beat the null median, windows where that was answerable)
    beat_timing: tuple[int, int]
    beat_name: tuple[int, int]
    n_measurable: int
    n_unmeasurable: int
    short_windows: list[str]
    windows: list[WindowNulls]

    def to_json(self) -> dict:
        """A small JSON payload -- the scalars and Stats, never the raw draws."""
        return {
            "slice": self.slice_label,
            "pass_id": self.pass_id,
            "null_draws": self.null_draws,
            "recomputed_at": self.recomputed_at,
            "run_ids": self.run_ids,
            "refused": self.refused,
            "actual": self.actual.model_dump(),
            "vs_timing": self.vs_timing.model_dump(),
            "vs_name": self.vs_name.model_dump(),
            "excess_vs_basket_median": self.excess_vs_basket_median.model_dump(),
            "excess_vs_basket_mean": self.excess_vs_basket_mean.model_dump(),
            "n_episodes": self.n_episodes,
            "effective_n": self.effective_n,
            "pct_positive": self.pct_positive,
            "beat_timing_windows": list(self.beat_timing),
            "beat_name_windows": list(self.beat_name),
            "n_measurable_windows": self.n_measurable,
            "n_unmeasurable_windows": self.n_unmeasurable,
            "short_windows": self.short_windows,
        }

    def render(self) -> str:
        """ASCII only -- this goes to a cp1252 console."""
        lines = [
            f"MARGINAL NULL READ -- slice {self.slice_label or '(whole pool)'}",
            f"Re-drawn off the frozen mirror, K={self.null_draws} per episode, each run verified against "
            f"its own pooled.json. Recomputed {self.recomputed_at}.",
            "",
            f"runs read: {len(self.run_ids)}   refused: {len(self.refused)}   scoreable episodes in "
            f"slice: {self.n_episodes}   effective n (name-days): {self.effective_n}",
        ]
        for rid, why in sorted(self.refused.items()):
            lines.append(f"  REFUSED {rid[:52]}: {why}")
        lines += [
            "",
            "| figure | n | median | mean |",
            "|---|---|---|---|",
            f"| actual (the slice)           | {self.actual.n} | {_pct(self.actual.median)} | "
            f"{_pct(self.actual.mean)} |",
            f"| vs TIMING null               | {self.vs_timing.n} | {_pct(self.vs_timing.median)} | "
            f"{_pct(self.vs_timing.mean)} |",
            f"| vs NAME null                 | {self.vs_name.n} | {_pct(self.vs_name.median)} | "
            f"{_pct(self.vs_name.mean)} |",
            f"| excess vs basket MEDIAN      | {self.excess_vs_basket_median.n} | "
            f"{_pct(self.excess_vs_basket_median.median)} | {_pct(self.excess_vs_basket_median.mean)} |",
            f"| excess vs basket MEAN        | {self.excess_vs_basket_mean.n} | "
            f"{_pct(self.excess_vs_basket_mean.median)} | {_pct(self.excess_vs_basket_mean.mean)} |",
            "",
            f"% of slice episodes with a positive forward return: {_pct(self.pct_positive)}",
            "",
            "PER-WINDOW (the pooled figures can hide a single window carrying the pocket):",
            f"  measurable windows: {self.n_measurable} of {self.n_measurable + self.n_unmeasurable}"
            + (
                f"   ({self.n_unmeasurable} held no episode in this slice)"
                if self.n_unmeasurable
                else ""
            ),
            f"  actual beat its TIMING null: {self.beat_timing[0]} of {self.beat_timing[1]} windows",
            f"  actual beat its NAME null:   {self.beat_name[0]} of {self.beat_name[1]} windows"
            + (
                f"   ({self.n_measurable - self.beat_name[1]} measurable window(s) had no peers to compare)"
                if self.n_measurable - self.beat_name[1]
                else ""
            ),
        ]
        if self.short_windows:
            lines.append(
                f"  SHORT WINDOWS (timing null drew < {SHORT_WINDOW_SESSIONS} sessions, so vs_timing is "
                f"near-vacuous there): {', '.join(self.short_windows)}"
            )
        return "\n".join(lines)


def _pct(x: float | None) -> str:
    return "--" if x is None else f"{x * 100:.3f}%"


def _close(a: float | None, b: float | None) -> bool:
    """Both None, or within TOLERANCE. A None on ONE side is a mismatch: an empty null beside a populated
    one is exactly the reconstruction failure the gate exists to catch."""
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    return abs(a - b) <= TOLERANCE


def _rosters(man: mf.BacktestManifest) -> dict[UUID, list[UUID]]:
    """The roster each thesis's NULLS drew from -- the LIVE basket, recorded on the manifest.

    `run.py` builds the nulls' `roster_at` from `thesis_repo.list_all` (date-independent), and writes that
    same list to `ThesisEntry.member_ids`. So this reconstructs the name-null population for ANY run
    regardless of `roster_source`: the nulls never used a point-in-time snapshot, only the live basket.
    """
    return {e.thesis_id: [UUID(m) for m in e.member_ids if m is not None] for e in man.theses}


def _member_union(man: mf.BacktestManifest) -> list[UUID]:
    """Every resolved member across the run's theses -- the securities `sessions` is taken over. Order is
    irrelevant (`trading_sessions` does DISTINCT d ORDER BY d); dedup keeps it a clean set."""
    seen: dict[UUID, None] = {}
    for e in man.theses:
        for m in e.member_ids:
            if m is not None:
                seen.setdefault(UUID(m), None)
    return list(seen)


def analyze_run(
    run_dir: Path,
    mirror: Path,
    slice_: SliceSpec | None,
    *,
    mirror_digest: str | None = None,
) -> WindowNulls:
    """Re-draw ONE run's nulls faithfully, verify against its stored pooled.json, and slice.

    ``slice_ is None`` reads the WHOLE pool -- the reproduction oracle. Refuses (and contributes nothing)
    when the mirror is not this run's, there is no pooled.json to check against, or the re-draw does not
    reproduce the stored nulls."""
    man = mf.BacktestManifest.model_validate_json((run_dir / mf.MANIFEST_NAME).read_text("utf-8"))
    out = WindowNulls(
        run_id=man.run_id,
        window_start=man.window_start,
        window_end=man.window_end,
        null_draws=man.null_draws,
    )

    if (mirror_digest or mf.mirror_hash(mirror)) != man.mirror.hash:
        out.refused = f"mirror {mirror.name} is not this run's mirror"
        return out
    pooled_path = run_dir / "pooled.json"
    if not pooled_path.is_file():
        out.refused = "no pooled.json to verify the re-draw against"
        return out
    stored = json.loads(pooled_path.read_text("utf-8"))
    stored_metric = next(
        (m for m in stored.get("metrics", []) if m.get("name") == _TIMING_METRIC), None
    )
    if stored_metric is None:
        out.refused = f"pooled.json carries no {_TIMING_METRIC} metric to verify against"
        return out

    con = connect_mirror(mirror)
    try:
        realized = RealizedPrices(con, tenant_id=DEFAULT_TENANT_ID)
        # THE TIMING-NULL SESSIONS, reconstructed exactly: the run's `sessions` is the union over its theses
        # of `trading_sessions(live basket)`, which equals `trading_sessions(union of member ids)` because
        # the DISTINCT-over-securities commutes with set union. Same query, same ORDER BY d.
        sessions = trading_sessions(
            con, _member_union(man), man.window_start, man.window_end, DEFAULT_TENANT_ID
        )
        out.timing_candidate_sessions = len(sessions)
        eps = _episodes(run_dir)
        rosters = _rosters(man)
        nulls = draw_nulls(
            eps,
            realized,
            roster_at=lambda tid, _d, _r=rosters: _r.get(tid, []),
            sessions=sessions,
            seed=man.null_seed,
            draws=man.null_draws,
        )
    finally:
        con.close()

    # THE REPRODUCTION GATE — the re-draw is worth only what the stored numbers reproduce. Whole-pool
    # reproduction implies every episode's draws reproduced (per-episode sub-seeding), hence the slice is a
    # faithful subset. A run that does not reproduce is refused rather than silently biasing the marginal.
    rep = build_report(eps, nulls, draws=man.null_draws, seed=man.null_seed, sessions=len(sessions))
    m = rep.metrics[0]
    st, sn = stored_metric.get("vs_timing", {}), stored_metric.get("vs_name", {})
    if not (
        _close(m.vs_timing.median, st.get("median"))
        and _close(m.vs_timing.mean, st.get("mean"))
        and _close(m.vs_name.median, sn.get("median"))
        and _close(m.vs_name.mean, sn.get("mean"))
    ):
        out.refused = (
            f"the re-drawn nulls do not reproduce pooled.json (vs_timing median {m.vs_timing.median} vs "
            f"{st.get('median')}, vs_name median {m.vs_name.median} vs {sn.get('median')}) -- the sessions "
            f"or roster reconstruction is wrong for this run"
        )
        return out
    out.reproduced = True

    # THE SLICE — a faithful subset of the just-verified draws, scoreable only (the pool's own filter).
    out.slice_nulls, out.actual, out.vs_timing, out.vs_name = _slice_window(eps, nulls, slice_)
    return out


def _pool_windows(
    windows: Sequence[WindowNulls],
    *,
    slice_label: str,
    pass_id: str | None,
    now: datetime | None = None,
) -> SliceNullsReport:
    """Pool the kept windows into the marginal answer, and tally the per-window beats. PURE -- takes the
    already-analyzed windows, so it is testable without a mirror on disk."""
    kept = [w for w in windows if not w.refused]
    pooled_nulls = [n for w in kept for n in w.slice_nulls]
    actual_vals = [n.forward_return for n in pooled_nulls if n.forward_return is not None]
    measurable = [w for w in kept if w.measurable]

    # X of M: a beat is the slice's actual median above the null median IN THAT WINDOW; the denominator is
    # the measurable windows in which the null could be drawn at all (a window with no timing session, or a
    # slice of single-name theses with no peers, could not answer and is not counted against the tally).
    beat_t = sum(
        1
        for w in measurable
        if w.vs_timing.median is not None and w.actual.median > w.vs_timing.median
    )
    beat_n = sum(
        1 for w in measurable if w.vs_name.median is not None and w.actual.median > w.vs_name.median
    )
    denom_t = sum(1 for w in measurable if w.vs_timing.median is not None)
    denom_n = sum(1 for w in measurable if w.vs_name.median is not None)

    return SliceNullsReport(
        slice_label=slice_label,
        pass_id=pass_id,
        null_draws=next((w.null_draws for w in kept), 0),
        recomputed_at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        run_ids=[w.run_id for w in kept],
        refused={w.run_id: w.refused for w in windows if w.refused},
        actual=Stat.of(actual_vals),
        vs_timing=Stat.of([d.forward_return for n in pooled_nulls for d in n.timing]),
        vs_name=Stat.of([d.forward_return for n in pooled_nulls for d in n.name]),
        excess_vs_basket_median=Stat.of([n.excess_return_vs_median for n in pooled_nulls]),
        excess_vs_basket_mean=Stat.of([n.excess_return for n in pooled_nulls]),
        n_episodes=len(pooled_nulls),
        effective_n=len({(n.security_id, n.arm_date) for n in pooled_nulls}),
        pct_positive=(
            round(sum(1 for v in actual_vals if v > 0) / len(actual_vals), 4)
            if actual_vals
            else None
        ),
        beat_timing=(beat_t, denom_t),
        beat_name=(beat_n, denom_n),
        n_measurable=len(measurable),
        n_unmeasurable=len(kept) - len(measurable),
        short_windows=[f"{w.window_start}..{w.window_end}" for w in kept if w.short_window],
        windows=list(windows),
    )


def analyze_pass(
    run_dirs: Sequence[Path],
    mirror: Path,
    slice_: SliceSpec | None,
    *,
    pass_id: str | None = None,
    now: datetime | None = None,
) -> SliceNullsReport:
    """Re-draw and pool a set of runs' marginal nulls over one shared mirror.

    Raises ``SliceKeyError`` when a real slice matched 0 scoreable episodes across every REPRODUCED window
    (an absent value reads as a null result otherwise) -- but not when the emptiness is because runs were
    refused, where the refusals are the story and are reported instead."""
    digest = mf.mirror_hash(mirror)  # hashed once; every run of a pass cites the same mirror
    windows = [analyze_run(d, mirror, slice_, mirror_digest=digest) for d in run_dirs]
    report = _pool_windows(
        windows, slice_label=(slice_.label if slice_ else ""), pass_id=pass_id, now=now
    )
    if slice_ is not None and report.n_episodes == 0 and not report.refused:
        raise SliceKeyError(
            f"slice {slice_.label!r} matched 0 scoreable episodes across {len(report.run_ids)} reproduced "
            f"window(s) -- check the value (a valid key with an absent value reads as a null result "
            f"otherwise)"
        )
    return report


# --- the CLI ---------------------------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backtest.slice_nulls",
        description=(
            "Read a MARGINAL algorithm family (one or two SLICE_KEYS) against its OWN two nulls, pooled "
            "across a pass's windows. Re-draws the nulls faithfully off the frozen mirror the pass cites "
            "(same K, same seed, each episode's own horizon) and refuses any run whose re-draw does not "
            "reproduce its stored pooled.json. Read-only; no re-run, no served route."
        ),
    )
    p.add_argument("--pass-id", default=None, help="read the runs of this pass")
    p.add_argument(
        "--config-hash",
        default=None,
        help=(
            "restrict to ONE point of the pass (default: the production baseline, the reading a write-up "
            "quotes). Takes a full config_hash or a short one."
        ),
    )
    p.add_argument("--run-id", action="append", default=[], help="read these runs (repeatable)")
    p.add_argument(
        "--slice",
        required=True,
        help=(
            "the family: attr=value, or attr=value,attr=value. Only algorithm dimensions "
            f"({list(SLICE_KEYS)}); slicing by thesis is the leaderboard trap and is refused (#4)."
        ),
    )
    p.add_argument(
        "--mirror-dir",
        default=None,
        help="the frozen mirror (default: found in the store by the manifest's hash)",
    )
    p.add_argument("--out", default=None, help="also write the result as JSON to this path")
    p.add_argument(
        "--root", default=None, help="the artifact store (default: the repo's data/backtest)"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        slice_ = SliceSpec.parse(args.slice)
    except SliceKeyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    root = Path(args.root or store.DEFAULT_ROOT)
    rows = store.list_runs(root)
    pass_id: str | None = None
    if args.run_id:
        wanted = [r for r in rows if r.run_id in set(args.run_id)]
    elif args.pass_id:
        pass_id = args.pass_id
        wanted = [r for r in rows if r.pass_id == args.pass_id]
        if args.config_hash:
            wanted = [
                r
                for r in wanted
                if r.config_hash == args.config_hash or r.config_short == args.config_hash
            ]
        else:
            from domain.config import DEFAULT_CONFIG, config_hash

            wanted = [r for r in wanted if r.config_hash == config_hash(DEFAULT_CONFIG)]
    else:
        print("ERROR: pass --pass-id or --run-id", file=sys.stderr)
        return 2
    if not wanted:
        print("ERROR: no runs matched", file=sys.stderr)
        return 2

    dirs = [d for r in wanted if (d := store.run_dir(r.run_id, root)) is not None]
    if not dirs:
        print("ERROR: matched runs have no directories in the store", file=sys.stderr)
        return 2
    first = mf.BacktestManifest.model_validate_json((dirs[0] / mf.MANIFEST_NAME).read_text("utf-8"))
    mirror = Path(args.mirror_dir) if args.mirror_dir else _find_mirror(root, first.mirror.hash)
    if mirror is None:
        print(
            f"ERROR: no mirror in {root / 'mirrors'} hashes to {first.mirror.hash[:12]}... -- the pass's "
            "own tape is gone, and re-drawing over another one would be a different experiment",
            file=sys.stderr,
        )
        return 2

    try:
        result = analyze_pass(dirs, mirror, slice_, pass_id=pass_id)
    except SliceKeyError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(result.render())
    if args.out:
        Path(args.out).write_text(
            json.dumps(result.to_json(), indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"  written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
