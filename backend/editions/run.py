"""``python -m editions.run`` — the editions CLI.

    python -m editions.run trial [--out DIR] [--end YYYY-MM-DD] [--known-by ISO] [--workers N]

``trial`` (S2) runs the whole re-run on the PUBLIC clock and writes ONLY a run directory — never Postgres:

    DIR/mirror/              the public-clock Parquet mirror it read, cut at the watermark (rebuildable)
    DIR/entries.json         every join date it used
    DIR/cards/<thesis>.json  every night's card, per thesis
    DIR/selfcheck.json       the live edge: the engine at the latest recorded night vs that night's record
    DIR/run.json             the cut, engine fingerprint, timings, counts
    DIR/preview.json + .txt  the comparison with the original record (``editions.preview``)

``--end`` defaults to the latest recorded night; ``--known-by`` (a cut at or before now) re-computes an
earlier cut exactly.

    python -m editions.run publish [--apply] [--out DIR] [--end YYYY-MM-DD] [--known-by ISO] [--workers N]

``publish`` (S3) publishes EDITION 1 — the initial edition, label "first re-run", cause ``initial``. It runs
exactly the ``trial`` above at the cut (the same run directory, kept as the edition's evidence), then stores
it through ``repositories.editions_repo``: the edition row (number, label, causes, engine fingerprint,
config hash, code sha, the cut as ``data_known_by``, the first night), every join date as ``edition_entry``,
one ``edition_call`` page per thesis-night (``page_kind = rerun``) and a ``published`` event. DRY-RUN by
default: the run directory is written and the plan printed, and the connection stays READ-ONLY throughout.
``--apply`` writes, in one transaction. A tenant that already holds Edition 1 (or an edition at the same
cut) is REFUSED before anything runs, and again inside the write transaction — a second publish appends
nothing. The ``calls`` log is only read, and the counts printed before and after the write prove it.

    python -m editions.run nightly [--apply] [--end YYYY-MM-DD] [--workers N] [--max-nights N] [--keep-mirror]

``nightly`` (S5) appends tonight's page (and any missed session's catch-up page) to the CURRENT edition, inks
the episodes whose signal window ended, and runs the live-edge self-check — ``editions.nightly`` has the whole
contract. The cron sidecar runs it with ``--apply`` after the nightly backup; it runs only when the newest
run artifact is a finished nightly pass (``--end`` bypasses that gate for a manual run) and refuses with exit
75 while a pass holds the call-of-record lock or another editions job runs. DRY-RUN by default (read-only, no
page, a ``dry_run`` artifact the Admin ignores).
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID, connect
from domain.call import CallCard
from domain.config import DEFAULT_CONFIG
from domain.settings import get_settings
from editions.engine import prepare, run_jobs
from editions.entry import Entry
from editions.fingerprint import engine_fingerprint
from editions.preview import compare_to_record, render
from editions.selfcheck import live_edge
from repositories import editions_repo


def run_trial(
    conn: psycopg.Connection,
    out: Path,
    *,
    end: date | None = None,
    known_by: datetime | None = None,
    workers: int = 1,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """The whole re-run into ``out`` (see the module docstring). Returns the ``run.json`` content."""
    out.mkdir(parents=True, exist_ok=False)
    timings: dict[str, float] = {}
    t0 = time.perf_counter()
    prep = prepare(conn, out / "mirror", end=end, known_by=known_by, tenant_id=tenant_id)
    cut = prep.cut
    timings["prepare_s"] = round(time.perf_counter() - t0, 1)

    t0 = time.perf_counter()
    cards = run_jobs(cut.path, prep.jobs, cfg=DEFAULT_CONFIG, workers=workers)
    timings["rerun_s"] = round(time.perf_counter() - t0, 1)

    (out / "cards").mkdir()
    for tid, cs in cards.items():
        (out / "cards" / f"{tid}.json").write_text(
            json.dumps([c.model_dump(mode="json") for c in cs]), encoding="utf-8"
        )
    (out / "entries.json").write_text(
        json.dumps([e.as_json() for es in prep.entries.values() for e in es.values()], indent=1),
        encoding="utf-8",
    )

    edge = live_edge(
        conn,
        cards,
        known_by=cut.known_by,
        night=prep.end,
        cfg=DEFAULT_CONFIG,
        code_sha=get_settings().image_sha,
    )
    conn.commit()
    (out / "selfcheck.json").write_text(
        json.dumps([r.as_json() for r in edge], indent=1), encoding="utf-8"
    )

    t0 = time.perf_counter()
    preview = compare_to_record(conn, cards, end=prep.end)
    timings["preview_s"] = round(time.perf_counter() - t0, 1)
    (out / "preview.json").write_text(json.dumps(preview, indent=1), encoding="utf-8")
    (out / "preview.txt").write_text(render(preview) + "\n", encoding="utf-8")

    meta = {
        "kind": "trial",
        "tenant_id": str(tenant_id),
        "end": prep.end.isoformat(),
        "known_by": cut.known_by.isoformat(),
        "snapshot_at": cut.snapshot_at.isoformat(),
        "clock": cut.manifest.get("clock"),
        "engine": engine_fingerprint(DEFAULT_CONFIG),
        "workers": workers,
        "timings": timings,
        "n_theses": sum(1 for cs in cards.values() if cs),
        "n_pages": sum(len(cs) for cs in cards.values()),
        "n_entries": sum(len(es) for es in prep.entries.values()),
        "n_entries_estimated": sum(
            1 for es in prep.entries.values() for e in es.values() if e.estimated
        ),
        "live_edge": {
            o: sum(1 for r in edge if r.outcome == o) for o in ("match", "mismatch", "no_page")
        },
        "mirror": {
            k: cut.manifest.get(k)
            for k in ("excluded_tables", "blind_detectors", "identity_table", "split_table")
        },
        "insider_rows_dropped_no_acceptance": (
            cut.manifest["tables"].get("fact_insider_txn", {}).get("rows_dropped_null_clock")
        ),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    (out / "run.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return meta


def load_run(path: Path) -> tuple[dict[str, Any], dict[UUID, list[CallCard]], list[Entry]]:
    """Read a trial run directory back: ``(run.json, cards by thesis, entries)``."""
    meta = json.loads((path / "run.json").read_text(encoding="utf-8"))
    cards = {
        UUID(p.stem): [
            CallCard.model_validate(c) for c in json.loads(p.read_text(encoding="utf-8"))
        ]
        for p in sorted((path / "cards").glob("*.json"))
    }
    entries = [
        Entry.from_json(x) for x in json.loads((path / "entries.json").read_text(encoding="utf-8"))
    ]
    return meta, cards, entries


def _default_out(kind: str) -> Path:
    return Path("data/editions/runs") / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{kind}"


# --- publish (S3) -----------------------------------------------------------------------------------------

INITIAL_NUMBER = 1
INITIAL_LABEL = "first re-run"
INITIAL_CAUSES = ("initial",)


class PublishRefused(Exception):
    """The edition already exists for the tenant; nothing ran and nothing was written."""


def _refusal(conn: psycopg.Connection, tenant_id: UUID, known_by: datetime | None) -> str | None:
    prior = editions_repo.get_by_number(conn, tenant_id=tenant_id, number=INITIAL_NUMBER)
    if prior is None and known_by is not None:
        prior = editions_repo.get_by_cut(conn, tenant_id=tenant_id, data_known_by=known_by)
    if prior is None:
        return None
    return (
        f"REFUSED: Edition {prior.number} ({prior.label}) is already published, cut "
        f"{prior.data_known_by.isoformat()}, {prior.n_pages} pages. Editions are immutable; a later "
        "edition is cut by a rule change or a data repair (S7). Nothing was run or written."
    )


def publish_plan(
    conn: psycopg.Connection,
    meta: dict[str, Any],
    cards: dict[UUID, list[CallCard]],
    entries: list[Entry],
    *,
    tenant_id: UUID,
) -> dict[str, Any]:
    """What ``--apply`` would store, from a finished run: the pages (every thesis-night), the join dates of
    THIS tenant's theses (the trial's entries list carries every tenant's), and the counts."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM thesis WHERE tenant_id = %s", (tenant_id,))
        own = {r["id"] for r in cur.fetchall()}
    conn.commit()
    pages = [c for tid in sorted(cards, key=str) for c in cards[tid]]
    kept = [e for e in entries if e.thesis_id in own]
    nights = [p.asof for p in pages]
    return {
        "number": INITIAL_NUMBER,
        "label": INITIAL_LABEL,
        "causes": list(INITIAL_CAUSES),
        "data_known_by": meta["known_by"],
        "first_night": min(nights).isoformat() if nights else None,
        "last_night": max(nights).isoformat() if nights else None,
        "engine": meta["engine"],
        "n_pages": len(pages),
        "n_theses": sum(1 for cs in cards.values() if cs),
        "n_entries": len(kept),
        "n_entries_estimated": sum(1 for e in kept if e.estimated),
        "pages": pages,
        "entries": kept,
    }


def run_publish(
    conn: psycopg.Connection,
    out: Path,
    *,
    apply: bool = False,
    end: date | None = None,
    known_by: datetime | None = None,
    workers: int = 1,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[str, Any]:
    """Publish Edition 1 (see the module docstring). Returns a summary: the plan's counts, and with
    ``apply`` the new edition's id plus the table counts before and after the write. Raises
    ``PublishRefused`` when the tenant already holds it — before the engine runs, and again inside the write
    transaction (the unique constraints are the last guard)."""
    refused = _refusal(conn, tenant_id, known_by)
    conn.commit()
    if refused:
        raise PublishRefused(refused)
    prior_ro = conn.read_only
    conn.read_only = True  # the run itself writes no row, dry-run or not
    try:
        meta = run_trial(
            conn, out, end=end, known_by=known_by, workers=workers, tenant_id=tenant_id
        )
        meta, cards, entries = load_run(out)
        plan = publish_plan(conn, meta, cards, entries, tenant_id=tenant_id)
    finally:
        if conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            conn.rollback()
        conn.read_only = prior_ro
    summary = {k: v for k, v in plan.items() if k not in ("pages", "entries")}
    summary["run_dir"] = str(out)
    summary["live_edge"] = meta["live_edge"]
    summary["applied"] = False
    if not apply:
        return summary

    cut = datetime.fromisoformat(meta["known_by"])
    try:
        refused = _refusal(conn, tenant_id, cut)
        if refused:
            raise PublishRefused(refused)
        before = editions_repo.table_counts(conn)
        edition_id = editions_repo.publish_edition(
            conn,
            tenant_id=tenant_id,
            number=INITIAL_NUMBER,
            label=INITIAL_LABEL,
            causes=INITIAL_CAUSES,
            engine=meta["engine"],
            data_known_by=cut,
            pages=plan["pages"],
            entries=[e.as_json() for e in plan["entries"]],
            computed_at=datetime.fromisoformat(meta["created_at"]),
            page_kind="rerun",
            reason={
                "engine": meta["engine"],
                "run": {
                    k: meta.get(k)
                    for k in (
                        "end",
                        "known_by",
                        "snapshot_at",
                        "clock",
                        "workers",
                        "timings",
                        "n_theses",
                        "n_pages",
                        "n_entries",
                        "n_entries_estimated",
                        "live_edge",
                        "mirror",
                        "insider_rows_dropped_no_acceptance",
                    )
                },
                "run_dir": str(out),
            },
            event_detail={
                k: summary[k]
                for k in (
                    "n_pages",
                    "n_theses",
                    "n_entries",
                    "n_entries_estimated",
                    "first_night",
                    "last_night",
                    "live_edge",
                )
            },
        )
        conn.commit()
    except editions_repo.EditionExists as exc:
        conn.rollback()
        raise PublishRefused(f"REFUSED: {exc}") from exc
    except Exception:
        conn.rollback()
        raise
    after = editions_repo.table_counts(conn)
    conn.commit()
    summary.update(
        applied=True, edition_id=str(edition_id), counts_before=before, counts_after=after
    )
    return summary


def _print_publish(s: dict[str, Any]) -> None:
    eng = s["engine"]
    print(
        f"Edition {s['number']} · {s['label']} · causes {s['causes']} · cut {s['data_known_by']}\n"
        f"  nights {s['first_night']} .. {s['last_night']} · {s['n_pages']} pages over "
        f"{s['n_theses']} theses · {s['n_entries']} join dates ({s['n_entries_estimated']} estimated)\n"
        f"  engine {eng['fingerprint'][:12]} · policy {eng['config_hash'][:12]} · code "
        f"{eng.get('code_sha') or 'unknown'} · live edge {s['live_edge']}\n"
        f"  run dir {s['run_dir']}"
    )
    if not s["applied"]:
        print("DRY RUN — nothing written to the database (re-run with --apply to publish)")
        return
    print(f"PUBLISHED edition {s['edition_id']}")
    for k in ("edition", "edition_event", "edition_entry", "edition_call", "calls"):
        print(f"  {k:14} {s['counts_before'][k]:>7} -> {s['counts_after'][k]:>7}")


def _main_nightly(args: argparse.Namespace) -> None:
    """The ``nightly`` subcommand: exit 0 = ran (or the gate said not tonight), 75 = refused (a lock), any
    other = it failed (a ``crashed`` artifact says so on the Admin page). Imported here, not at the top, so
    ``trial`` / ``publish`` load nothing they do not use."""
    from editions import nightly

    started = datetime.now(timezone.utc)
    conn = connect()
    if not args.apply:
        conn.read_only = True
    try:
        result = nightly.run_nightly(
            conn,
            apply=args.apply,
            end=args.end,
            workers=args.workers,
            keep_mirror=args.keep_mirror,
            max_nights=args.max_nights or nightly.MAX_NIGHTS_PER_RUN,
        )
    except nightly.NightlyRefused as exc:
        print(
            f"editions nightly: REFUSED — {exc}. Nothing was written (exit {nightly.EXIT_ALREADY_RUNNING})"
        )
        raise SystemExit(nightly.EXIT_ALREADY_RUNNING) from None
    except Exception as exc:
        if args.apply:
            nightly.crashed_artifact(started_at=started, error=exc)
        raise
    finally:
        conn.close()
    nightly.print_result(result)


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="python -m editions.run", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    tr = sub.add_parser("trial", help="re-run everything into a run directory (writes no DB rows)")
    tr.add_argument("--out", type=Path, default=None)
    tr.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="last night (default: the latest recorded night)",
    )
    tr.add_argument(
        "--known-by",
        type=datetime.fromisoformat,
        default=None,
        help="the cut (default: now) — an aware ISO instant at or before now",
    )
    tr.add_argument("--workers", type=int, default=1)
    pb = sub.add_parser(
        "publish", help="publish Edition 1 (dry-run unless --apply; refuses a second publish)"
    )
    pb.add_argument("--apply", action="store_true", help="write the edition (default: dry-run)")
    pb.add_argument("--out", type=Path, default=None)
    pb.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="last night (default: the latest recorded night)",
    )
    pb.add_argument(
        "--known-by",
        type=datetime.fromisoformat,
        default=None,
        help="the cut (default: now) — an aware ISO instant at or before now",
    )
    pb.add_argument("--workers", type=int, default=1)
    nt = sub.add_parser(
        "nightly",
        help="append tonight's pages to the current edition (dry-run unless --apply)",
    )
    nt.add_argument("--apply", action="store_true", help="write the pages + ink (default: dry-run)")
    nt.add_argument(
        "--end",
        type=date.fromisoformat,
        default=None,
        help="page through this night, bypassing the finished-nightly-pass gate (a manual run)",
    )
    nt.add_argument("--workers", type=int, default=1)
    nt.add_argument(
        "--max-nights",
        type=int,
        default=None,
        help="sessions to page per thesis in one run, oldest first (default 30)",
    )
    nt.add_argument(
        "--keep-mirror", action="store_true", help="keep the run's mirror (default: removed)"
    )
    args = ap.parse_args(argv)

    if args.cmd == "nightly":
        _main_nightly(args)
        return

    if args.cmd == "publish":
        conn = connect()
        try:
            summary = run_publish(
                conn,
                args.out or _default_out("publish"),
                apply=args.apply,
                end=args.end,
                known_by=args.known_by,
                workers=args.workers,
            )
        except PublishRefused as exc:
            print(exc)
            raise SystemExit(2) from exc
        finally:
            conn.close()
        _print_publish(summary)
        return

    if args.cmd == "trial":
        conn = connect()
        conn.read_only = True
        try:
            out = args.out or _default_out("trial")
            meta = run_trial(conn, out, end=args.end, known_by=args.known_by, workers=args.workers)
            print((out / "preview.txt").read_text(encoding="utf-8"))
            print(
                f"\nrun dir {out} | cut {meta['known_by']} | {meta['n_theses']} theses, "
                f"{meta['n_pages']} nightly pages | live edge {meta['live_edge']} | "
                f"timings {meta['timings']}"
            )
        finally:
            conn.close()


if __name__ == "__main__":
    main()
