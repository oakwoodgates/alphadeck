"""The per-company CHANGE STAMP (migration 0081, ``security_change``): "this company's stored data changed".

The dashboards' cache (``dashboards/cache.py``, docs/DASHBOARDS.md rule 7) keeps per-company pieces and whole
pages. It learns that one company's data changed by reading this table, and drops exactly that company's
pieces. So every event that changes a company's stored data moves the company's stamp:

- the company write lock's release (``db.write_lock.CompanyLock.release``): every fact ingest holds that lock
  and releases it after its writes are committed, so the release is the "this company's write is finished"
  event (the nightly, the fast-lane ingest, the staged history load, the per-name price pull, fundamentals,
  benchmarks, split history);
- every narrow writer that changes a company's stored data OUTSIDE that lock calls ``mark_changed`` in the
  same transaction as its write (the master's identity writers, the split list, the Form 4/A decisions, the
  ratified facts, the price-feed repair log, the company-list refresh).

**The rule for authors:** a writer that changes a company's stored data outside the company write lock calls
``mark_changed``, or dashboards serve that company stale.

Bookkeeping, never a fact and never a history gate (invariant #1): one row per company, updated in place.

The reader's contract: ``stamp_fingerprint`` is one aggregate, ``(max(seq), sum(seq), count)``. Every committed
change raises the sum (an update replaces a seq with a larger one; an insert adds one), so the fingerprint moves
on every committed change even when two transactions commit out of sequence order. Never compare
``seq > last_seen``: a transaction that drew a lower seq can commit after one that drew a higher one.

LAYERING: like ``db/write_lock.py``, this module imports only psycopg (no ``pipeline``, ``ingest``, ``app`` or
``dashboards``), so every writer can call it.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

import psycopg

# One statement: the master's own tenant id rides onto the row (DATA scope, domain/scopes.py), a company not in
# the master stamps nothing, and the rows go in sorted id order so two stampers lock rows in the same order.
_MARK_SQL = """
INSERT INTO security_change (tenant_id, security_id, seq, changed_at)
SELECT sm.tenant_id, sm.id, nextval('security_change_seq'), now()
  FROM security_master sm
 WHERE sm.id = ANY(%s)
 ORDER BY sm.id
ON CONFLICT (security_id) DO UPDATE SET seq = EXCLUDED.seq, changed_at = EXCLUDED.changed_at
"""


def mark_changed(conn: psycopg.Connection, security_ids: Iterable[UUID | None]) -> int:
    """Move the change stamp of each company in ``security_ids``: insert its row, or draw it a new ``seq``.
    Runs in the CALLER's transaction and never commits. Duplicates and ``None`` are ignored; an empty input is
    a no-op (no statement runs). Returns how many companies were stamped (an id not in the master stamps
    nothing)."""
    ids = sorted({i for i in security_ids if i is not None})
    if not ids:
        return 0
    with conn.cursor() as cur:
        cur.execute(_MARK_SQL, (ids,))
        return cur.rowcount


def stamp_fingerprint(conn: psycopg.Connection) -> tuple[int, int, int]:
    """``(max(seq), sum(seq), count(*))`` over every stamp, in ONE aggregate read (the cache reads it on every
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
    """``{security_id: seq}`` for every stamped company (read only when the fingerprint moved)."""
    with conn.cursor() as cur:
        cur.execute("SELECT security_id, seq FROM security_change")
        rows = cur.fetchall()
    out: dict[UUID, int] = {}
    for r in rows:
        sid, seq = (r["security_id"], r["seq"]) if isinstance(r, dict) else (r[0], r[1])
        out[sid] = int(seq)
    return out
