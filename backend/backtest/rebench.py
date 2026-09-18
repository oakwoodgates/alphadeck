"""RETROACTIVE BENCHMARKING — read a FINISHED run's excess-over-basket again, both ways, without re-running it.

B added the basket's MEDIAN move beside its equal-weight MEAN. Every run before B carries only the mean,
and re-running a four-hour pass to obtain a statistic that costs nothing new would be absurd: the mirror is
frozen and content-addressed, the episodes are on disk, and the benchmark is a pure function of the two.
So this module recomputes both figures from the artifacts and reports them BESIDE the stored ones.

**IT IS A RECOMPUTE, NOT A RE-RUN, AND THE DIFFERENCE IS THE WHOLE POINT.** Nothing here re-opens a
point-in-time view, re-decides anything, or writes into a run directory. It reads the frozen mirror, prices
the same windows, and says what the basket did. Three things are therefore checked rather than assumed, and
a mismatch on any of them means the recompute is NOT describing the run it claims to:

1. **The mirror is the run's own mirror**, proved by `manifest.mirror_hash` and not by its path.
2. **The roster is still the one the run replayed on.** Every phase-1-era run reads `live_fallback` --
   `basket_snapshot` history begins 2026-09-15 -- so its benchmark was taken over TODAY's basket as it
   stood at run time. If a basket has been edited since, that benchmark cannot be rebuilt and the honest
   answer is to refuse. A manifest written after B carries `member_ids` and needs no database at all; an
   older one is checked against the live basket by hash.
3. **The recompute reproduces the stored numbers.** `forward_return` and `exit_date` are re-derived and
   compared to `outcomes.parquet`, and the recomputed MEAN-based excess is compared to the stored
   `pooled.json`. The new median figure is only trustworthy to the extent the old one reproduces.

Refusal is the default everywhere: a run whose roster moved, whose mirror is missing, or whose numbers do
not reproduce is REPORTED and excluded, never quietly folded into the pooled answer.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

from backtest import manifest as mf
from backtest import store
from backtest.nulls import _BasketBenchmark, horizon_days
from backtest.pooled import Stat
from db.session import DEFAULT_TENANT_ID
from replay.pit import connect_mirror
from replay.schema import Episode
from replay.scoring import RealizedPrices, score_window

#: how close a recomputed figure must sit to the stored one to count as reproduced. Not zero: the stored
#: value went through JSON and a `round(..., 6)`, so demanding bit equality would fail on the formatting
#: rather than on the arithmetic.
TOLERANCE = 5e-7


@dataclass(frozen=True)
class RosterCheck:
    """One thesis's roster, then and now."""

    thesis_id: UUID
    name: str
    manifest_hash: str
    live_hash: str | None
    #: "unchanged" | "changed" | "gone" | "recorded" -- `recorded` means the manifest carried the roster
    #: itself (post-B), so nothing had to be asked of a live database
    status: str

    @property
    def ok(self) -> bool:
        return self.status in ("unchanged", "recorded")


def check_rosters(
    entries: Sequence[mf.ThesisEntry], live: Mapping[UUID, Sequence[UUID | None]] | None
) -> list[RosterCheck]:
    """Is every roster this run replayed on still the one it replayed on?

    PURE -- the database read happens in the CLI and arrives as `live`. `None` means no live basket was
    supplied, which is only sufficient when every manifest entry carries its own `member_ids`.
    """
    out: list[RosterCheck] = []
    for e in sorted(entries, key=lambda x: x.name):
        if e.member_ids:
            # the roster IS the manifest's; nothing to ask anyone
            out.append(RosterCheck(e.thesis_id, e.name, e.roster_hash, e.roster_hash, "recorded"))
            continue
        if live is None or e.thesis_id not in live:
            out.append(RosterCheck(e.thesis_id, e.name, e.roster_hash, None, "gone"))
            continue
        h = mf.roster_hash(list(live[e.thesis_id]))
        out.append(
            RosterCheck(
                e.thesis_id,
                e.name,
                e.roster_hash,
                h,
                "unchanged" if h == e.roster_hash else "changed",
            )
        )
    return out


@dataclass
class RunRebench:
    """One run, read again."""

    run_id: str
    n_episodes: int = 0
    n_scored: int = 0
    excess_mean: list[float] = field(default_factory=list)
    excess_median: list[float] = field(default_factory=list)
    #: episodes the run's own null pass never benched -- no horizon, or a non-positive one. Counted
    #: rather than dropped quietly: they are the difference between this population and the file's.
    n_no_horizon: int = 0
    #: episodes whose re-derived return or exit date did NOT match the stored outcome
    n_return_mismatch: int = 0
    n_exit_mismatch: int = 0
    #: the stored pooled figure this run's recomputed mean is checked against
    stored_excess_median_of: float | None = None
    recomputed_excess_median_of: float | None = None
    refused: str = ""


def _episodes(run_dir: Path) -> list[Episode]:
    import pyarrow.parquet as pq

    return [
        Episode.model_validate(r) for r in pq.read_table(run_dir / "episodes.parquet").to_pylist()
    ]


def _outcomes(run_dir: Path) -> dict[tuple[str, str, str], dict[str, Any]]:
    import pyarrow.parquet as pq

    out = {}
    for r in pq.read_table(run_dir / "outcomes.parquet").to_pylist():
        if r.get("arm_date"):
            out[(str(r["thesis_id"]), str(r["security_id"]), str(r["arm_date"]))] = r
    return out


def _roster_for(
    entries: Sequence[mf.ThesisEntry], live: Mapping[UUID, Sequence[UUID | None]] | None
):
    """The roster each thesis's benchmark is taken over — the manifest's own when it has one."""
    recorded: dict[UUID, list[UUID]] = {}
    for e in entries:
        if e.member_ids:
            recorded[e.thesis_id] = [UUID(m) for m in e.member_ids if m is not None]
        elif live is not None and e.thesis_id in live:
            recorded[e.thesis_id] = [m for m in live[e.thesis_id] if m is not None]

    def roster_at(tid: UUID, _d: date) -> list[UUID]:
        # Date-independent BY CONSTRUCTION, and only correct for a run that was entirely `live_fallback`
        # -- which `rebench_run` refuses to proceed without. A run that read real snapshots had a roster
        # that MOVED during its window, and rebuilding that is a re-run, not a recompute.
        return recorded.get(tid, [])

    return roster_at


def rebench_run(
    run_dir: Path,
    mirror: Path,
    live: Mapping[UUID, Sequence[UUID | None]] | None,
    *,
    mirror_digest: str | None = None,
) -> RunRebench:
    """Recompute one run's two excess figures from its own artifacts and its own mirror."""
    man = mf.BacktestManifest.model_validate_json((run_dir / mf.MANIFEST_NAME).read_text("utf-8"))
    out = RunRebench(run_id=man.run_id)

    checks = check_rosters(man.theses, live)
    bad = [c for c in checks if not c.ok]
    if bad:
        out.refused = "roster " + ", ".join(f"{c.name}:{c.status}" for c in bad)
        return out
    moved = [t for t in man.theses if t.fallback_days != t.total_days]
    if moved:
        out.refused = (
            "roster is point-in-time for "
            + ", ".join(t.name for t in moved)
            + " (snapshot days), so its benchmark cannot be rebuilt without re-running the harness"
        )
        return out
    # Hashed ONCE per call site, not once per run: it is a sha256 over the whole mirror (186 MB on the
    # phase-1 tape) and every run of a pass cites the same one.
    if (mirror_digest or mf.mirror_hash(mirror)) != man.mirror.hash:
        out.refused = f"mirror {mirror.name} is not this run's mirror"
        return out

    con = connect_mirror(mirror)
    try:
        # The manifest does not record a tenant (the deployment is single-tenant and the run read the
        # default), so this states that rather than inventing a field to read.
        realized = RealizedPrices(con, tenant_id=DEFAULT_TENANT_ID)
        bench = _BasketBenchmark(realized, _roster_for(man.theses, live))
        stored = _outcomes(run_dir)
        for ep in _episodes(run_dir):
            if ep.arm_date is None or ep.exit_by is None:
                continue
            # THE RUN'S OWN POPULATION RULE, not a new one: `draw_nulls` skips an episode with no
            # horizon (or a non-positive one), so those episodes have no stored excess to reproduce and
            # including them here would silently compare two different populations. MEASURED while
            # building this: 3 such episodes in a 324-episode window moved the recomputed median by
            # 0.22 pp, which is exactly the kind of drift the reproduction check exists to catch.
            if horizon_days(ep) is None:
                out.n_no_horizon += 1
                continue
            out.n_episodes += 1
            w = score_window(realized, ep.security_id, ep.arm_date, ep.exit_by)
            if w is None:
                continue
            out.n_scored += 1
            key = (str(ep.thesis_id), str(ep.security_id), ep.arm_date.isoformat())
            was = stored.get(key)
            if was is not None:
                if (
                    was.get("forward_return") is not None
                    and abs(was["forward_return"] - w.forward_return) > TOLERANCE
                ):
                    out.n_return_mismatch += 1
                if str(was.get("exit_date")) != w.exit_date.isoformat():
                    out.n_exit_mismatch += 1
            move = bench(ep.thesis_id, ep.arm_date, w.exit_date)
            if move.mean is not None:
                out.excess_mean.append(w.forward_return - move.mean)
            if move.median is not None:
                out.excess_median.append(w.forward_return - move.median)
    finally:
        con.close()

    pooled = json.loads((run_dir / "pooled.json").read_text("utf-8"))
    metric = next((m for m in pooled.get("metrics", []) if m.get("excess")), None)
    out.stored_excess_median_of = (metric or {}).get("excess", {}).get("median")
    out.recomputed_excess_median_of = Stat.of(out.excess_mean).median
    return out


@dataclass
class PassRebench:
    """The pooled answer, and everything that had to hold for it to mean anything."""

    recomputed_at: str
    run_ids: list[str]
    refused: dict[str, str]
    n_scored: int
    excess_vs_basket_mean: Stat
    excess_vs_basket_median: Stat
    n_no_horizon: int
    n_return_mismatch: int
    n_exit_mismatch: int
    stored_vs_recomputed: list[tuple[str, float | None, float | None]]

    def render(self) -> str:
        """ASCII only -- this goes to a cp1252 console."""
        lines = [
            "RETROACTIVE RECOMPUTE -- not a re-run. The frozen mirror re-read, the same windows re-priced,",
            f"the basket's move taken two ways. Recomputed at {self.recomputed_at}.",
            "",
            f"runs read: {len(self.run_ids)}   refused: {len(self.refused)}   episodes scored: "
            f"{self.n_scored}   skipped (no horizon, as the run itself skipped them): {self.n_no_horizon}",
        ]
        for rid, why in sorted(self.refused.items()):
            lines.append(f"  REFUSED {rid}: {why}")
        lines += [
            "",
            "| figure | n | median | mean |",
            "|---|---|---|---|",
            f"| excess vs basket MEDIAN (the headline) | {self.excess_vs_basket_median.n} | "
            f"{_pct(self.excess_vs_basket_median.median)} | {_pct(self.excess_vs_basket_median.mean)} |",
            f"| excess vs basket MEAN (an equal-weight position) | {self.excess_vs_basket_mean.n} | "
            f"{_pct(self.excess_vs_basket_mean.median)} | {_pct(self.excess_vs_basket_mean.mean)} |",
            "",
            "REPRODUCTION CHECK (the recompute is only worth what the old numbers are):",
            f"  forward_return mismatches: {self.n_return_mismatch}   exit_date mismatches: {self.n_exit_mismatch}",
        ]
        for rid, stored, again in self.stored_vs_recomputed:
            mark = (
                "OK"
                if stored is not None and again is not None and abs(stored - again) <= TOLERANCE
                else "DIFFERS"
            )
            lines.append(f"  {mark}  {rid[:52]}: stored {_pct(stored)} vs recomputed {_pct(again)}")
        return "\n".join(lines)


def _pct(x: float | None) -> str:
    return "--" if x is None else f"{x * 100:.3f}%"


def rebench(
    run_dirs: Sequence[Path],
    mirror: Path,
    live: Mapping[UUID, Sequence[UUID | None]] | None,
    *,
    now: datetime | None = None,
) -> PassRebench:
    """Pool a set of runs' recomputed excess figures. Refused runs contribute NOTHING."""
    digest = mf.mirror_hash(mirror)
    results = [rebench_run(d, mirror, live, mirror_digest=digest) for d in run_dirs]
    kept = [r for r in results if not r.refused]
    return PassRebench(
        recomputed_at=(now or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
        run_ids=[r.run_id for r in kept],
        refused={r.run_id: r.refused for r in results if r.refused},
        n_scored=sum(r.n_scored for r in kept),
        excess_vs_basket_mean=Stat.of([v for r in kept for v in r.excess_mean]),
        excess_vs_basket_median=Stat.of([v for r in kept for v in r.excess_median]),
        n_no_horizon=sum(r.n_no_horizon for r in kept),
        n_return_mismatch=sum(r.n_return_mismatch for r in kept),
        n_exit_mismatch=sum(r.n_exit_mismatch for r in kept),
        stored_vs_recomputed=[
            (r.run_id, r.stored_excess_median_of, r.recomputed_excess_median_of) for r in kept
        ],
    )


# --- the CLI ---------------------------------------------------------------------------------------------


def _live_rosters() -> dict[UUID, list[UUID | None]]:
    """TODAY's baskets, READ-ONLY, in the order `roster_hash` hashes them.

    ARCHIVED theses included: the question is whether a ROSTER moved, not whether the thesis is still on
    the board, and a thesis archived since the run would otherwise read as "gone" when its basket is
    untouched."""
    from db.session import connect
    from repositories import thesis_repo

    conn = connect()
    try:
        return {
            t.id: [m.security_id for m in t.basket]
            for t in thesis_repo.list_all(conn, include_archived=True)
        }
    finally:
        conn.close()


def _find_mirror(root: Path, want: str) -> Path | None:
    for d in sorted((root / "mirrors").glob("*")):
        if d.is_dir() and mf.mirror_hash(d) == want:
            return d
    return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backtest.rebench",
        description=(
            "Recompute a FINISHED run's excess-over-basket both ways (equal-weight mean and median) "
            "from its own artifacts and its own frozen mirror. A recompute, never a re-run: it refuses "
            "any run whose roster has moved, whose mirror is missing, or whose stored numbers it cannot "
            "reproduce."
        ),
    )
    p.add_argument("--pass-id", default=None, help="rebench the runs of this pass")
    p.add_argument(
        "--config-hash",
        default=None,
        help=(
            "restrict to ONE point of the pass (default: the production baseline, which is the reading "
            "a write-up quotes). Takes a full config_hash or a short one."
        ),
    )
    p.add_argument("--run-id", action="append", default=[], help="rebench these runs (repeatable)")
    p.add_argument(
        "--mirror-dir",
        default=None,
        help="the frozen mirror (default: found in the store by the manifest's hash)",
    )
    p.add_argument(
        "--no-db",
        action="store_true",
        help=(
            "do not read live baskets. Only works for manifests that carry `member_ids` (post-B); an "
            "older manifest is then REFUSED rather than benched against a roster nobody verified."
        ),
    )
    p.add_argument("--out", default=None, help="also write the result as JSON to this path")
    p.add_argument("--root", default=None, help="the artifact store (default: the repo's)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = Path(args.root or store.DEFAULT_ROOT)

    rows = store.list_runs(root)
    if args.run_id:
        wanted = [r for r in rows if r.run_id in set(args.run_id)]
    elif args.pass_id:
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
    first = mf.BacktestManifest.model_validate_json((dirs[0] / mf.MANIFEST_NAME).read_text("utf-8"))
    mirror = Path(args.mirror_dir) if args.mirror_dir else _find_mirror(root, first.mirror.hash)
    if mirror is None:
        print(
            f"ERROR: no mirror in {root / 'mirrors'} hashes to {first.mirror.hash[:12]}... -- the run's "
            "own tape is gone, and benching against another one would be a different experiment",
            file=sys.stderr,
        )
        return 2

    live = None if args.no_db else _live_rosters()
    result = rebench(dirs, mirror, live)
    print(result.render())
    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "recomputed_at": result.recomputed_at,
                    "run_ids": result.run_ids,
                    "refused": result.refused,
                    "n_scored": result.n_scored,
                    "n_no_horizon": result.n_no_horizon,
                    "excess_vs_basket_mean": result.excess_vs_basket_mean.model_dump(),
                    "excess_vs_basket_median": result.excess_vs_basket_median.model_dump(),
                    "n_return_mismatch": result.n_return_mismatch,
                    "n_exit_mismatch": result.n_exit_mismatch,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
            newline="\n",
        )
        print(f"  written to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
