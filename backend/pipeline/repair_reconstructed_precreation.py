"""Repair CLI: delete RECONSTRUCTED call-of-record rows dated before their thesis existed.

THE GAP: ``pipeline.backfill`` (#331) reconstructed the missed nights for EVERY non-archived thesis, on
every night — including theses created AFTER the night. A thesis that did not exist was never in that
night's cron, so its reconstructed row records a call the platform could not have made. MEASURED on the
dev copy of the 2026-09-09 backfill: 37 of 144 reconstructed rows are dated before their thesis's
creation, 20 of them warming/armed; two produced arm episodes for a thesis that did not exist (one of
which matured into the metrics). The backfill now refuses to write such a row (``thesis_existed_on``);
this tool removes the ones already written. Nothing else.

THE RULE (one rule, shared): a row is a candidate iff ``calls.reconstructed`` (migration 0042 — the
explicit marker; never the derived lag heuristic) AND the thesis did NOT exist on the row's ``asof`` by
``pipeline.backfill.thesis_existed_on`` — ``thesis.created_at`` in MARKET time vs the as-of day (a thesis
created during the day of ``asof`` WAS in that night's cron and is KEPT). The rule is imported, not
re-stated, so this tool and the backfill's gate cannot disagree. A nightly row (``reconstructed = false``)
is NEVER a candidate, whatever its date. Reconstructed rows for theses that DID exist are NOT touched
either — they are still reported-not-scored by the Scoreboard, and they are the evidence the backfill
happened (reversible: the record path filters them; deleting them is a separate, unasked-for decision).

SAFETY (the ``dedup_identical_versions`` shape):
- **Dry-run is the DEFAULT** and prints EVERY row it would delete (thesis, asof, state / verdict, the
  creation date); ``--apply`` deletes and prints per-thesis counts + a SUMMARY line.
- ``DATABASE_URL`` MUST be set explicitly (no dev-default fallback for a destructive tool); the target is
  printed (host:port/db, credentials redacted) as the first line of every run.
- One transaction for the whole delete; the ``calls`` table's ``no_update`` trigger guards UPDATE only, so
  a DELETE needs no trigger dance. Back up first (``docs/FEED_LOOP.md`` §Backups).

    DATABASE_URL=... python -m pipeline.repair_reconstructed_precreation            # dry-run (default)
    DATABASE_URL=... python -m pipeline.repair_reconstructed_precreation --apply    # delete + counts

Never run against prod by an agent — the operator runs it, after a backup, after re-running the
classification query there (the pins differ per stack).
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime
from urllib.parse import urlsplit
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from domain.market_time import market_tz
from pipeline.backfill import thesis_existed_on
from pipeline.dedup_identical_versions import require_database_url


@dataclass(frozen=True)
class PrecreationRow:
    """One reconstructed row dated before its thesis existed — everything the dry-run prints."""

    call_id: UUID
    thesis_id: UUID
    thesis_name: str
    asof: date
    state: str
    verdict: str
    created_at: datetime


def find_precreation_rows(conn: psycopg.Connection) -> list[PrecreationRow]:
    """Every RECONSTRUCTED row whose ``asof`` precedes its thesis's creation (market time), ordered by
    thesis name, asof, seq. Reads only the reconstructed rows (a few hundred at most) and applies the
    shared ``thesis_existed_on`` rule in Python — the same function the backfill gates on."""
    tz = market_tz()
    with conn.cursor() as cur:
        cur.execute("""SELECT c.id, c.thesis_id, t.name, c.asof, c.state, c.verdict, t.created_at
                 FROM calls c
                 JOIN thesis t ON t.id = c.thesis_id
                WHERE c.reconstructed
                ORDER BY t.name, c.asof, c.seq""")
        rows = cur.fetchall()
    return [
        PrecreationRow(
            call_id=r["id"],
            thesis_id=r["thesis_id"],
            thesis_name=r["name"],
            asof=r["asof"],
            state=r["state"],
            verdict=r["verdict"],
            created_at=r["created_at"],
        )
        for r in rows
        if not thesis_existed_on(r["created_at"], r["asof"], tz)
    ]


def delete_rows(conn: psycopg.Connection, rows: list[PrecreationRow]) -> int:
    """DELETE exactly the given rows by id (uncommitted — the caller commits). Returns the row count the
    DELETE reported, which the caller checks against ``len(rows)``."""
    if not rows:
        return 0
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM calls WHERE id = ANY(%s) AND reconstructed", ([r.call_id for r in rows],)
        )
        return cur.rowcount


def _redact(url: str) -> str:
    """host:port/dbname only — NEVER the credentials."""
    parts = urlsplit(url)
    return f"{parts.hostname}:{parts.port}{parts.path}"


def _print_rows(rows: list[PrecreationRow], tz) -> None:
    for r in rows:
        print(
            f"  {r.thesis_name}: asof {r.asof} · {r.state} / {r.verdict} · "
            f"thesis created {r.created_at.astimezone(tz).date()} (market) · call {r.call_id}"
        )


def main(argv: list[str] | None = None) -> None:
    p = argparse.ArgumentParser(
        description="Delete RECONSTRUCTED call-of-record rows dated before their thesis existed "
        "(the backfill's pre-creation rows). Dry-run by default; --apply deletes."
    )
    p.add_argument(
        "--apply", action="store_true", help="delete the rows (default: dry-run, print only)"
    )
    args = p.parse_args(argv)

    url = require_database_url()
    print(f"target DB : {_redact(url)}")
    tz = market_tz()

    conn = psycopg.connect(url, row_factory=dict_row)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT current_database() AS db")
            print(f"connected : current_database() = {cur.fetchone()['db']}")

        rows = find_precreation_rows(conn)
        per_thesis = Counter(r.thesis_name for r in rows)

        if not args.apply:
            print("\n=== repair_reconstructed_precreation — DRY-RUN (nothing written) ===")
            _print_rows(rows, tz)
            conn.rollback()  # read-only; nothing to commit
            print(
                f"\nSUMMARY: dry-run only, nothing written — {len(rows)} reconstructed pre-creation "
                f"row(s) across {len(per_thesis)} thesis/theses; rerun with --apply to delete them"
            )
            return

        print("\n=== repair_reconstructed_precreation — APPLY ===")
        _print_rows(rows, tz)
        deleted = delete_rows(conn, rows)
        if deleted != len(rows):
            conn.rollback()
            print(
                f"\nABORTED: expected to delete {len(rows)} row(s) but the DELETE reported {deleted} — "
                "ROLLED BACK, nothing written"
            )
            raise SystemExit(1)
        conn.commit()
        for name, n in sorted(per_thesis.items()):
            print(f"  {name}: -{n} row(s)")
        print(f"\nSUMMARY: total_deleted={deleted} theses={len(per_thesis)}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
