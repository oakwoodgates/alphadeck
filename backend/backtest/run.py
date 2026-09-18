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
from typing import Any, Literal
from uuid import UUID

import psycopg
import pyarrow as pa
import pyarrow.parquet as pq

from backtest import manifest as mf
from backtest import store
from backtest.config_overlay import OverlayError, load_overlay, overlay_diff
from backtest.ledger import LEDGER_NAME, SecurityRef, build_ledger
from backtest.nulls import DEFAULT_DRAWS, draw_nulls
from backtest.parallel import default_workers, replay_all_parallel
from backtest.pooled import build_report
from db.session import DEFAULT_TENANT_ID, connect
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash, short_hash
from domain.market_time import market_today
from domain.thesis import Thesis
from replay.episodes import episodes_for
from replay.export import export_snapshot, mirror_clock, read_mirror_manifest
from replay.metrics import compute_metrics
from replay.pit import connect_mirror
from replay.run import arrow_schema
from replay.schema import Episode, Outcome
from replay.scoring import RealizedPrices, score_episodes
from repositories import thesis_repo
from scoreboard.replay_snapshot import ThesisMeta
from securities import master


class MirrorClockMismatch(ValueError):
    """A supplied mirror was handed a ``clock`` it does not carry.

    Raised BEFORE the run directory exists, so a refused run leaves nothing behind. The alternative —
    honoring the argument — would label a run `public` over a record-clock tape, and no reader could
    catch that by looking at the numbers."""


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


def _table_counts(tables: dict[str, Any]) -> dict[str, mf.TableCounts]:
    """The mirror manifest's per-table accounting, in the run manifest's vocabulary.

    The two export modes write different shapes and both are honest. The PUBLIC path already reports the
    four numbers (rows in, rows out, rows the clock could not date, identities lost). The RECORD path
    reports one — ``rows`` — because it is a straight copy: every row, every version, ``recorded_at``
    untouched, nothing dropped and nothing collapsed. So ``rows_in == rows_out == rows`` there is an
    identity of that path, not an assumption about it, and stating it keeps the run manifest equally
    informative in both modes rather than silent on the common one.

    A table whose entry carries neither shape is skipped rather than guessed at."""
    out: dict[str, mf.TableCounts] = {}
    for table, c in (tables or {}).items():
        if not isinstance(c, dict):
            continue
        if "rows_in" in c:
            out[table] = mf.TableCounts.model_validate(c)
        elif isinstance(c.get("rows"), int):
            out[table] = mf.TableCounts(rows_in=c["rows"], rows_out=c["rows"])
    return out


def execute(
    conn: psycopg.Connection,
    *,
    start: date,
    end: date,
    pin: datetime,
    cfg: CallConfig = DEFAULT_CONFIG,
    overlay_path: str | None = None,
    mirror_dir: str | Path | None = None,
    clock: Literal["record", "public"] | None = None,
    workers: int = 1,
    null_draws: int = DEFAULT_DRAWS,
    null_seed: str | None = None,
    hypothesis: str | None = None,
    decision_rule: str | None = None,
    regime: str | None = None,
    pass_id: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    root: str | Path | None = None,
    now: datetime | None = None,
) -> RunOutcome:
    """Run one backtest end to end and leave an immutable, registered artifact behind.

    Read-only over Postgres; writes only into this run's own directory. No row of any kind reaches
    ``calls`` — a simulated call is never the record.

    **THE CLOCK IS A PROPERTY OF THE MIRROR, AND THIS RUN INHERITS IT.** ``clock`` is therefore only an
    instruction for the export this run performs itself; with a SUPPLIED ``mirror_dir`` the axis is already
    baked into the Parquet, so passing a disagreeing one raises ``MirrorClockMismatch`` before any work is
    done and passing the agreeing one is a no-op. ``None`` means "whatever the mirror is" and is what a
    sweep's points pass — which is what makes "every point of a sweep swept one tape on one clock" true by
    construction rather than by every caller remembering to repeat itself.

    ``known_at_mode`` is DERIVED from that clock, never accepted as a flag of its own. On the public clock
    ``recorded_at`` means "when this became public", so the honest transaction cap is the end of each
    session (``lockstep``) — asking "what was disclosed by the end of day T?". On the record clock the
    run-wide determinism pin stays (``pin``). Two flags could disagree; one derivation cannot.
    """
    now = now or datetime.now(timezone.utc)
    # derived ONCE and shared by the ledger's banner and the manifest — two places naming "which dials
    # moved" from two derivations is two places for them to disagree
    diff = overlay_diff(cfg)
    # Resolved BEFORE the run directory exists: a refused run must leave nothing behind, not an orphan
    # directory that a later `mkdir(exist_ok=False)` would then collide with.
    if mirror_dir is not None:
        supplied = mirror_clock(mirror_dir)
        if clock is not None and clock != supplied:
            raise MirrorClockMismatch(
                f"--clock {clock!r} was passed with a mirror exported on the {supplied!r} clock "
                f"({Path(mirror_dir)}). The clock belongs to the mirror: re-export it, or drop the flag "
                f"and inherit."
            )
        run_clock: Literal["record", "public"] = supplied
    else:
        run_clock = clock or "record"
    # DERIVED, never a flag. See the docstring. `run_clock`, never the `clock` PARAMETER, is what every
    # consumer below reads — the parameter is nullable ("inherit") and passing it on would put a None into
    # the ledger's banner and the manifest.
    known_at_mode: Literal["pin", "lockstep"] = "lockstep" if run_clock == "public" else "pin"
    # the CLOCK is part of the id: the same grid under the same hypothesis on both axes is two
    # measurements run back to back, and without it they collide inside the timestamp's one-second
    # resolution (see backtest/manifest.py::make_run_id)
    # the WINDOW START joins the id (S1): a tiling pass launches one point's window runs concurrently, so
    # they differ only by window and would otherwise land in the same second with the same everything else
    run_id = mf.make_run_id(
        cfg, hypothesis=hypothesis, now=now, clock=run_clock, window_start=start
    )
    out = store.create_run_dir(run_id, root)  # raises if it somehow already exists

    timings: dict[str, float] = {}
    # ONE FROZEN MIRROR, optionally SHARED (B7). A sweep exports once and points every variant at the same
    # Parquet files, which is what makes a metric delta attributable to the dial rather than to the tape
    # moving underneath it -- `replay/compare.py` has held that discipline since the first sweep. The run
    # directory then holds its outputs but no copy of the facts, and `mirror.hash` in the manifest is what
    # ties them together: two runs quoting one mirror hash provably swept the same facts.
    mirror = Path(mirror_dir) if mirror_dir else out
    t0 = time.perf_counter()
    if mirror_dir is None:
        export_snapshot(conn, out, tenant_id=tenant_id, clock=run_clock)
    timings["export_s"] = round(time.perf_counter() - t0, 2)
    # Read back rather than kept from the export's return value, so the SUPPLIED-mirror and
    # SELF-EXPORTED paths derive the manifest's mirror facts from exactly one place: the bytes on disk.
    mirror_manifest = read_mirror_manifest(mirror)

    t0 = time.perf_counter()
    con = connect_mirror(mirror)
    timings["connect_mirror_s"] = round(time.perf_counter() - t0, 2)
    try:
        theses = {t.id: t for t in thesis_repo.list_all(conn)}

        t0 = time.perf_counter()
        # B5b — the theses are independent, so the sweep fans out by thesis over the SAME frozen
        # mirror. `workers=1` takes the serial harness untouched; N>1 is byte-identical by test, and the
        # wall clock is bounded by the LARGEST thesis rather than by N (see backtest/parallel.py).
        #
        # `mirror`, NOT `out`. Every worker opens the mirror by PATH (it is a fresh process), so under a
        # shared mirror (B7) handing it the run directory would point each worker at a directory holding
        # no facts. The two were the same expression until a sweep could share one export, which is
        # precisely the kind of silent breakage a textual merge of B5b and B7 cannot see.
        result = replay_all_parallel(
            conn,
            mirror,
            start=start,
            end=end,
            known_at=pin,
            cfg=cfg,
            tenant_id=tenant_id,
            workers=workers,
            known_at_mode=known_at_mode,
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

        # B4 — the two nulls and the pooled view. They run on the SCORED layer (RealizedPrices only, no
        # point-in-time view is reopened), so K draws per episode cost priced windows rather than replays.
        # The seed defaults to the run_id, so a run reproduces its own draws and two runs never share them.
        t0 = time.perf_counter()
        seed = null_seed or run_id
        sessions = sorted({s.asof for snaps in result.timelines.values() for s in snaps})
        rosters = {
            tid: [m.security_id for m in t.basket if m.security_id is not None]
            for tid, t in theses.items()
        }
        nulls = draw_nulls(
            episodes,
            realized,
            # the roster AS OF the entry date. `basket_snapshot` history begins 2026-09-15, so for any
            # earlier window this is the harness's own documented fallback to today's basket -- the real
            # arm and its null then draw from the SAME counterfactual roster, which is the symmetric and
            # honest choice, and is said out loud on the surface rather than only here.
            roster_at=lambda tid, _d, _r=rosters: _r.get(tid, []),
            sessions=sessions,
            seed=seed,
            draws=null_draws,
        )
        pooled = build_report(episodes, nulls, draws=null_draws, seed=seed, sessions=len(sessions))
        store.write_metrics(out, pooled, name="pooled.json")
        timings["nulls_s"] = round(time.perf_counter() - t0, 2)

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
        # The SERVING copy of the episodes (B6). The Parquet file above is the analytical artifact -- a
        # reviewer opens it in DuckDB -- but reading it back needs pyarrow, which the LEAN api image
        # deliberately does not carry (only the sig/fork images bake the `.[replay]` extra). Writing the
        # same rows as JSON is what lets the `/backtest` route serve a run on ANY tier without importing
        # the replay stack, exactly as the Scoreboard's replay panel serves ONE JSON artifact. Same
        # `model_dump(mode="json")` rows, so the two files cannot disagree.
        store.write_metrics(
            out, {"episodes": [e.model_dump(mode="json") for e in episodes]}, name="episodes.json"
        )

        # B6 — THE LEDGER: the same episodes grouped by thesis, in the Scoreboard's own wire vocabulary,
        # so the `/backtest` surface renders its drill-down through the SAME components the replay panel
        # uses instead of a parallel set that could drift. Written here, by the process that holds the DB
        # connection, because the run is immutable: the tickers a run reports are the ones it resolved,
        # not whatever the master says the day somebody opens it. See backtest/ledger.py.
        t0 = time.perf_counter()
        sids = {ep.security_id for ep in episodes}
        # ...and every name a TRIGGER fired on, because the ledger's Why cell resolves those to their
        # own ticker and issuer CIK. On a theme thesis the trigger's security is often not the armed
        # member, so leaving them out would silently dash the evidence links on exactly the rows that
        # most need them (#6).
        sids |= {
            tr.security_id
            for snaps in result.timelines.values()
            for snap in snaps
            for m in snap.members
            for tr in m.triggers
        }
        sids |= {
            m.security_id for snaps in result.timelines.values() for s in snaps for m in s.members
        }
        tickers = master.tickers_for(conn, sids, tenant_id=tenant_id)
        names = master.names_for(conn, sids, tenant_id=tenant_id)
        ciks = master.ciks_for(conn, sids, tenant_id=tenant_id)
        ledger = build_ledger(
            result.timelines,
            list(zip(episodes, outcomes, strict=True)),
            thesis_meta={
                t.id: ThesisMeta(
                    tenant_id=t.tenant_id, name=t.name, ticker=t.ticker, basket_size=len(t.basket)
                )
                for t in theses.values()
            },
            securities={
                sid: SecurityRef(ticker=tickers.get(sid), cik=ciks.get(sid), name=names.get(sid))
                for sid in sorted(sids, key=str)
            },
            window_start=start,
            window_end=end,
            pin=pin,
            generated_at=now,
            # maturity is judged against the DATA edge, not the window end: scoring reads forward
            # without the pin (the no-lookahead rule binds the DECISION, not the measurement of it), so
            # an episode whose exit_by has elapsed in market time is judged — the same rule, and the
            # same market-time definition, the Scoreboard's own snapshot uses.
            matured_asof=market_today(),
            # `run_clock`, NOT the `clock` parameter. B6 wrote this line when `clock` was a local constant
            # and CW turned it into a nullable ARGUMENT meaning "inherit the mirror's"; git merged both
            # cleanly because the two edits never touched the same line. A sweep point passes no clock, so
            # the ledger's banner would have read `None` — the same shape as the B5b/B7 `out`/`mirror`
            # merge, found the same way (auditing every use of the renamed name, not by a conflict marker).
            clock=run_clock,
            config_hash=config_hash(cfg),
            code_sha=mf.resolve_code_sha(),
            dials_moved=sorted(diff),
            realized=realized,
            single_name_security=single_name,
            roster_fallback_theses=result.fallback_theses,
            roster_source_note=result.note(),
        )
        store.write_metrics(out, ledger, name=LEDGER_NAME)
        timings["ledger_s"] = round(time.perf_counter() - t0, 2)

        entries: list[mf.ThesisEntry] = []
        for tid, source in result.roster_sources.items():
            thesis = theses.get(tid)
            if thesis is None:  # archived between list_all and now — skip rather than invent a name
                continue
            # ONE list, hashed and recorded -- so the fingerprint and the roster it fingerprints can
            # never describe different baskets.
            member_ids = _member_ids(thesis)
            entries.append(
                mf.ThesisEntry(
                    thesis_id=tid,
                    name=thesis.name,
                    basket_size=len(thesis.basket),
                    roster_hash=mf.roster_hash(member_ids),
                    member_ids=[str(m) if m is not None else None for m in member_ids],
                    roster_source=source.source,
                    fallback_days=source.fallback_days,
                    total_days=source.total_days,
                )
            )
        entries.sort(key=lambda e: e.name)

        blob = mf.canonical_config_blob(cfg)
        manifest = mf.BacktestManifest(
            run_id=run_id,
            pass_id=pass_id,
            created_at=now.isoformat(),
            code_sha=mf.resolve_code_sha(),
            window_start=start,
            window_end=end,
            # Both read off the MIRROR, never off an argument — see execute()'s docstring. A manifest
            # that names an axis the Parquet does not carry is the one error nobody could catch by
            # reading the numbers.
            clock=run_clock,
            known_at_mode=known_at_mode,
            pin=pin.isoformat(),
            config_hash=config_hash(cfg),
            config_short=short_hash(config_hash(cfg)) or "",
            config_canonical_json=blob,
            # the SAME bytes, parsed — a reader should be able to see the dials without re-deriving them,
            # and a test pins `json.loads(config_canonical_json) == config` so the pair cannot drift
            config=json.loads(blob),
            overlay_diff=diff,
            overlay_path=overlay_path,
            workers=workers,
            theses=entries,
            # `mirror`, not `out`: under a shared mirror (B7) the facts do not live in the run directory,
            # and hashing an empty directory would make every point of a sweep claim the same vacuous
            # hash -- destroying the one property that makes a config delta attributable.
            # ...and the mirror's OWN accounting rides with the hash: WHICH tables were excluded, which
            # detectors that blinded, and B2's per-table counts (rows in/out, rows the public clock could
            # not date, identities lost). A run that saw a hole in the tape must say how big it was —
            # naming the hash alone would leave the reader to go and find the mirror to find out.
            mirror=mf.MirrorInfo(
                hash=mf.mirror_hash(mirror),
                tables=_table_counts(mirror_manifest.tables if mirror_manifest else {}),
                excluded_tables=list(mirror_manifest.excluded_tables) if mirror_manifest else [],
                blind_detectors=list(mirror_manifest.blind_detectors) if mirror_manifest else [],
            ),
            null_draws=null_draws,
            null_seed=seed,
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
                pass_id=pass_id,
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
        choices=("record", "public"),
        # DEFAULT None, not "record". The default lives in `execute` and nowhere else, which is what makes
        # --mirror-dir inherit: a parser default of "record" is indistinguishable from the operator
        # TYPING "record", so it had to be dropped when a mirror was supplied -- and dropping it meant a
        # --clock that disagreed with the mirror was silently ignored instead of refused, exactly against
        # what this help text promises.
        default=None,
        help=(
            "which clock the facts enter on -- a property of the MIRROR this run exports. 'record' = "
            "recorded_at, what this system held (the Scoreboard's axis). 'public' = when anyone could "
            "have known (the disclosure instant); it also derives known_at_mode=lockstep, capping the "
            "facts at the end of each session rather than at the run-wide pin, and it EXCLUDES any fact "
            "table with no declared public clock (the manifest names them and the detectors they blind). "
            "Default: record -- or, with --mirror-dir, the mirror's own clock, which a disagreeing "
            "--clock is refused against rather than silently overriding."
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
    p.add_argument(
        "--workers",
        type=int,
        default=1,
        help=(
            f"replay N theses in parallel over the one frozen mirror (default 1 = the serial harness; "
            f"this machine would default to {default_workers()}). Byte-identical to 1 by test; the wall "
            f"clock is bounded by the LARGEST thesis, not by N."
        ),
    )
    p.add_argument(
        "--null-draws",
        type=int,
        default=DEFAULT_DRAWS,
        help=(
            "K draws per episode for each null model (timing and name-selection). Fewer than K "
            "candidates means the whole population is used -- reporting three peers of a four-name "
            "basket is honest where resampling to K would manufacture confidence."
        ),
    )
    p.add_argument(
        "--null-seed",
        default=None,
        help="the nulls' RNG seed (default: the run_id, so a run reproduces its own draws)",
    )
    p.add_argument("--regime", default=None, help="a label for the market regime the window covers")
    p.add_argument(
        "--mirror-dir",
        default=None,
        help=(
            "replay over an EXISTING frozen mirror instead of exporting one. This is what lets N window "
            "jobs of one pass share a single tape -- a delta between two runs is attributable to the "
            "dial only if they swept the same bytes, and a second export is a second snapshot of a "
            "moving database. The run INHERITS the mirror's clock; passing a --clock that disagrees with "
            "it is refused before any work is done."
        ),
    )
    p.add_argument(
        "--pass-id",
        default=None,
        help=(
            "the curve this run is a point of -- one id shared by every run of one sweep, recorded on the "
            "manifest and in the registry so the grouping survives without sweep.json (which is "
            "latest-only). Set by the sweep runner; rarely passed by hand."
        ),
    )
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
            # UNCONDITIONAL. `execute` resolves None as "inherit the mirror's clock, else record", and
            # refuses a clock that disagrees with a supplied mirror -- which is only reachable if the flag
            # actually gets there.
            clock=args.clock,
            mirror_dir=args.mirror_dir,
            pass_id=args.pass_id,
            workers=args.workers,
            null_draws=args.null_draws,
            null_seed=args.null_seed,
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
