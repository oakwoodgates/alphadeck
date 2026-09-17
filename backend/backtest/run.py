"""``python -m backtest.run`` — one immutable, addressable backtest run.

Composes the engine's primitives (export -> replay -> episodes -> score -> metrics) rather than calling
``replay.run.run()``, which is the pattern ``replay/compare.py`` and ``scoreboard/replay_snapshot.py``
already follow: each caller needs a different slice of the intermediate state, and this one needs the
timings and the roster provenance that ``run()`` prints and discards.

What it adds over ``python -m replay.run`` is IDENTITY: a fresh directory per run, a manifest that names the
code, the dials, the clock, the pin and the rosters, and a registry row so trials can be counted.

    python -m backtest.run --start 2025-09-01 --end 2026-09-14
    python -m backtest.run --start ... --end ... --config overlays/h5-horizon-090.json \\
        --hypothesis "H5: the exit_by horizon is the timing lever" \\
        --decision-rule "adopt only on a plateau with sign agreement in 2+ disjoint sub-windows"
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

from backtest import manifest as mf
from backtest import store
from backtest.config_overlay import OverlayError, load_overlay, overlay_diff
from db.session import DEFAULT_TENANT_ID, connect
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash, short_hash
from domain.thesis import Thesis
from replay.episodes import episodes_for
from replay.export import export_snapshot
from replay.harness import replay_all
from replay.metrics import compute_metrics
from replay.pit import connect_mirror
from replay.run import arrow_schema
from replay.schema import Episode, Outcome
from replay.scoring import RealizedPrices, score_episodes
from repositories import thesis_repo


@dataclass(frozen=True)
class RunOutcome:
    """What a completed run reports to its caller — the run id, where it landed, and its manifest."""

    run_id: str
    path: Path
    manifest: mf.BacktestManifest


def _write_parquet(path: Path, rows: list[dict], schema: pa.Schema) -> None:
    """ALWAYS write, schema DECLARED — ``replay/run.py``'s F3 rule, held here too. An empty run must leave
    an EMPTY file carrying the full schema, never nothing: nothing would leave a previous artifact beside a
    fresh manifest, and inference on a populated run would make two runs' files disagree on types.
    """
    pq.write_table(pa.Table.from_pylist(rows, schema=schema), path)


def _member_ids(thesis: Thesis) -> list[UUID | None]:
    return [m.security_id for m in thesis.basket]


def execute(
    conn: psycopg.Connection,
    *,
    start: date,
    end: date,
    pin: datetime,
    cfg: CallConfig = DEFAULT_CONFIG,
    overlay_path: str | None = None,
    hypothesis: str | None = None,
    decision_rule: str | None = None,
    regime: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    root: str | Path | None = None,
    now: datetime | None = None,
) -> RunOutcome:
    """Run one backtest end to end and leave an immutable, registered artifact behind.

    Read-only over Postgres; writes only into this run's own directory. No row of any kind reaches
    ``calls`` — a simulated call is never the record."""
    now = now or datetime.now(timezone.utc)
    run_id = mf.make_run_id(cfg, hypothesis=hypothesis, now=now)
    out = store.create_run_dir(run_id, root)  # raises if it somehow already exists

    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    export_snapshot(conn, out, tenant_id=tenant_id)
    timings["export_s"] = round(time.perf_counter() - t0, 2)

    t0 = time.perf_counter()
    con = connect_mirror(out)
    timings["connect_mirror_s"] = round(time.perf_counter() - t0, 2)
    try:
        theses = {t.id: t for t in thesis_repo.list_all(conn)}

        t0 = time.perf_counter()
        result = replay_all(
            conn, con, start=start, end=end, known_at=pin, cfg=cfg, tenant_id=tenant_id
        )
        timings["replay_s"] = round(time.perf_counter() - t0, 2)

        t0 = time.perf_counter()
        episodes = episodes_for(result.timelines)
        realized = RealizedPrices(con, tenant_id=tenant_id)
        outcomes = score_episodes(episodes, realized)
        timings["score_s"] = round(time.perf_counter() - t0, 2)

        t0 = time.perf_counter()
        single_name = {
            t.id: [m.security_id for m in t.basket if m.security_id is not None][0]
            for t in theses.values()
            if len([m for m in t.basket if m.security_id is not None]) == 1
        }
        metrics = compute_metrics(
            outcomes,
            timeline=result.timelines,
            realized=realized,
            single_name_security=single_name,
        )
        timings["metrics_s"] = round(time.perf_counter() - t0, 2)

        _write_parquet(
            out / "episodes.parquet",
            [e.model_dump(mode="json") for e in episodes],
            arrow_schema(Episode),
        )
        _write_parquet(
            out / "outcomes.parquet",
            [o.model_dump(mode="json") for o in outcomes],
            arrow_schema(Outcome),
        )
        store.write_metrics(out, metrics)

        entries: list[mf.ThesisEntry] = []
        for tid, source in result.roster_sources.items():
            thesis = theses.get(tid)
            if thesis is None:  # archived between list_all and now — skip rather than invent a name
                continue
            entries.append(
                mf.ThesisEntry(
                    thesis_id=tid,
                    name=thesis.name,
                    basket_size=len(thesis.basket),
                    roster_hash=mf.roster_hash(_member_ids(thesis)),
                    roster_source=source.source,
                    fallback_days=source.fallback_days,
                    total_days=source.total_days,
                )
            )
        entries.sort(key=lambda e: e.name)

        blob = mf.canonical_config_blob(cfg)
        manifest = mf.BacktestManifest(
            run_id=run_id,
            created_at=now.isoformat(),
            code_sha=mf.resolve_code_sha(),
            window_start=start,
            window_end=end,
            clock="record",  # B2 adds "public"
            known_at_mode="pin",  # B2 adds "lockstep"
            pin=pin.isoformat(),
            config_hash=config_hash(cfg),
            config_short=short_hash(config_hash(cfg)) or "",
            config_canonical_json=blob,
            # the SAME bytes, parsed — a reader should be able to see the dials without re-deriving them,
            # and a test pins `json.loads(config_canonical_json) == config` so the pair cannot drift
            config=json.loads(blob),
            overlay_diff=overlay_diff(cfg),
            overlay_path=overlay_path,
            theses=entries,
            mirror=mf.MirrorInfo(hash=mf.mirror_hash(out)),
            hypothesis=hypothesis,
            decision_rule=decision_rule,
            regime=regime,
            timings=timings,
            n_episodes=len(episodes),
            n_theses=len(entries),
        )
        mf.write_manifest(out, manifest)
        store.register_run(
            store.RunSummary(
                run_id=run_id,
                created_at=manifest.created_at,
                hypothesis=hypothesis,
                decision_rule=decision_rule,
                config_short=manifest.config_short,
                config_hash=manifest.config_hash,
                clock=manifest.clock,
                known_at_mode=manifest.known_at_mode,
                window_start=str(start),
                window_end=str(end),
                n_theses=manifest.n_theses,
                n_episodes=manifest.n_episodes,
                dials_moved=sorted(manifest.overlay_diff),
            ),
            root,
        )
        # The roster caveat is said out loud, exactly as `replay.run` says it — silence means every thesis
        # replayed on a real point-in-time roster.
        note = result.note()
        if note:
            print(f"ROSTER: {note}")
        return RunOutcome(run_id=run_id, path=out, manifest=manifest)
    finally:
        con.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="backtest.run",
        description=(
            "One immutable, addressable backtest run: replay the call algorithm over a window and "
            "leave a manifest, the episodes, their outcomes and the metrics under "
            "data/backtest/runs/<run_id>/."
        ),
    )
    p.add_argument("--start", required=True, help="window start, YYYY-MM-DD")
    p.add_argument("--end", required=True, help="window end, YYYY-MM-DD")
    p.add_argument(
        "--pin",
        default=None,
        help="the known_at determinism pin, ISO timestamp (default: now, UTC)",
    )
    p.add_argument(
        "--clock",
        choices=("record",),
        default="record",
        help=(
            "which clock facts enter on. 'record' = recorded_at, what this system held. "
            "'public' (when anyone could have known) arrives with B2."
        ),
    )
    p.add_argument(
        "--config",
        default=None,
        help=(
            "a JSON overlay of {dial: value} applied to DEFAULT_CONFIG. Requires --hypothesis and "
            "--decision-rule: a run that moves a dial is an experiment, and an experiment is "
            "pre-registered or it is not evidence."
        ),
    )
    p.add_argument("--hypothesis", default=None, help="what this run is testing (pre-registration)")
    p.add_argument(
        "--decision-rule",
        default=None,
        help="what result would change your mind, written BEFORE the run",
    )
    p.add_argument("--regime", default=None, help="a label for the market regime the window covers")
    p.add_argument(
        "--out-root", default=None, help="the store root (default: <repo>/data/backtest)"
    )
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.config and not (args.hypothesis and args.decision_rule):
        print(
            "ERROR: --config requires --hypothesis and --decision-rule. A dial moved without a "
            "pre-registered hypothesis and decision rule is not a measurement.",
            file=sys.stderr,
        )
        return 2
    try:
        cfg = load_overlay(args.config) if args.config else DEFAULT_CONFIG
    except OverlayError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    pin = datetime.fromisoformat(args.pin) if args.pin else datetime.now(timezone.utc)
    if pin.tzinfo is None:  # the recorded_at axis is tz-aware; assume UTC for a bare timestamp
        pin = pin.replace(tzinfo=timezone.utc)

    conn = connect()
    try:
        outcome = execute(
            conn,
            start=date.fromisoformat(args.start),
            end=date.fromisoformat(args.end),
            pin=pin,
            cfg=cfg,
            overlay_path=args.config,
            hypothesis=args.hypothesis,
            decision_rule=args.decision_rule,
            regime=args.regime,
            root=args.out_root,
        )
    finally:
        conn.close()

    m = outcome.manifest
    moved = ", ".join(sorted(m.overlay_diff)) or "none (production dials)"
    print(
        f"run {m.run_id}\n"
        f"  {outcome.path}\n"
        f"  window {m.window_start} -> {m.window_end} | clock {m.clock} | known_at {m.known_at_mode}\n"
        f"  policy {m.config_short} | dials moved: {moved}\n"
        f"  theses {m.n_theses} | episodes {m.n_episodes} | timings {m.timings}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
