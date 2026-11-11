"""The per-company CHANGE LOG (migration 0081, ``security_change``): "this company's stored data changed".

The dashboards' cache (``dashboards/cache.py``, docs/DASHBOARDS.md rule 7) keeps per-company pieces and whole
pages. It learns that one company's data changed by reading this log, and drops exactly that company's
pieces. So every event that changes a company's stored data appends a row for the company:

- the company write lock's release (``db.write_lock.CompanyLock.release``): every fact ingest holds that lock
  and releases it after its writes are committed, so the release is the "this company's write is finished"
  event (the nightly, the fast-lane ingest, the staged history load, the per-name price pull, fundamentals,
  benchmarks, split history);
- every narrow writer that changes a company's stored data OUTSIDE that lock calls ``mark_changed`` in the
  same transaction as its write (the master's identity writers, the split list, the Form 4/A decisions, the
  ratified facts, the price-feed repair log, the company-list refresh).

**The rule for authors:** a writer that changes a company's stored data outside the company write lock calls
``mark_changed``, or dashboards serve that company stale.

APPEND-ONLY: a stamp is an INSERT (one row per company per change), so no stamp ever waits for another and a
rolled-back stamp never hides a committed one. Bounded by ``prune``. Bookkeeping, never a fact and never a
history gate (invariant #1).

The reader's contract: ``stamp_fingerprint`` is one aggregate, ``(max(seq), sum(seq), count)``. Every committed
insert raises the sum, so the fingerprint moves on every committed change even when two transactions commit out
of sequence order; a prune lowers it, which the cache also reads as a move. Never compare ``seq > last_seen``:
a transaction that drew a lower seq can commit after one that drew a higher one.

LAYERING: like ``db/write_lock.py``, this module imports only psycopg (and ``db.session`` for its CLI), so every
writer can call it.

CLI: ``python -m db.change_stamp --prune [--older-than-hours 24]`` — the cron sidecar runs it after each
nightly pass (``scripts/daily_cron.sh``), fail-open.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from uuid import UUID

import psycopg

# One statement: the master's own tenant id rides onto the row (DATA scope, domain/scopes.py), and a company
# not in the master stamps nothing. A plain INSERT: it takes no lock another stamp could hold.
_MARK_SQL = """
INSERT INTO security_change (tenant_id, security_id)
SELECT sm.tenant_id, sm.id
  FROM security_master sm
 WHERE sm.id = ANY(%s)
"""

DEFAULT_PRUNE_HOURS = 24


def mark_changed(conn: psycopg.Connection, security_ids: Iterable[UUID | None]) -> int:
    """Append one change row for each company in ``security_ids``. Runs in the CALLER's transaction and never
    commits. Duplicates and ``None`` are ignored; an empty input is a no-op (no statement runs). Returns how
    many companies were stamped (an id not in the master stamps nothing)."""
    ids = sorted({i for i in security_ids if i is not None})
    if not ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(_MARK_SQL, (ids,))
        return cur.rowcount


def stamp_fingerprint(conn: psycopg.Connection) -> tuple[int, int, int]:
    """``(max(seq), sum(seq), count(*))`` over the log, in ONE aggregate read (the cache reads it on every
    lookup). The sum is numeric, so it cannot overflow."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(max(seq), 0) AS mx, coalesce(sum(seq), 0)::numeric AS total, "
            "count(*) AS n FROM security_change"
        )
        row = cur.fetchone()
    if row is None:
        return (0, 0, 0)
    vals = list(row.values()) if isinstance(row, dict) else list(row)
    return (int(vals[0]), int(vals[1]), int(vals[2]))


def stamp_map(conn: psycopg.Connection) -> dict[UUID, int]:
    """``{security_id: its newest seq}`` over the log (read only when the fingerprint moved)."""
    with conn.cursor() as cur:
        cur.execute("SELECT security_id, max(seq) AS seq FROM security_change GROUP BY security_id")
        rows = cur.fetchall()
    out: dict[UUID, int] = {}
    for r in rows:
        sid, seq = (r["security_id"], r["seq"]) if isinstance(r, dict) else (r[0], r[1])
        out[sid] = int(seq)
    return out


def prune(conn: psycopg.Connection, older_than_hours: float = DEFAULT_PRUNE_HOURS) -> int:
    """Delete the log rows older than ``older_than_hours`` (``changed_at < now() - interval``) in the caller's
    transaction; returns how many. Keeps the table small: the fingerprint is read on every dashboard lookup.

    WHY 24 HOURS IS SAFE: the prune runs right after a nightly pass. That pass's start and finish artifacts
    hard-clear every cache instance at its next lookup (``dashboards.cache``: a new cron run clears page entries
    and pieces and re-reads the whole map), and a row more than 24 hours old was committed long before the pass
    started, so no surviving cache state can still need it. A cache synced before the prune reads the lowered
    fingerprint as a move: the pruned companies read as "gone" and their pieces drop (harmless)."""
    if older_than_hours < 0:
        raise ValueError("older_than_hours cannot be negative")
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM security_change WHERE changed_at < now() - make_interval(secs => %s)",
            (float(older_than_hours) * 3600,),
        )
        return cur.rowcount


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="The dashboards' per-company change log (security_change): prune old rows."
    )
    p.add_argument("--prune", action="store_true", help="delete rows older than the cut")
    p.add_argument("--older-than-hours", type=float, default=DEFAULT_PRUNE_HOURS)
    args = p.parse_args(argv)
    if not args.prune:
        p.error("nothing to do: pass --prune")
    from db.session import connect

    conn = connect()
    try:
        n = prune(conn, args.older_than_hours)
        conn.commit()
    finally:
        conn.close()
    print(f"change log: pruned {n} row(s) older than {args.older_than_hours:g} h")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
