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
earlier cut exactly. ``publish`` (S3) writes a run into the editions store.
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
    args = ap.parse_args(argv)

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
