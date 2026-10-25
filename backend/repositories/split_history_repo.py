"""``security_split_history`` (migration 0067) — which companies have had their split history filled.

One row per (tenant, security): the S1 backfill's coverage (seeded), a newcomer the job filled
(``pipeline/split_history.py``), or a ``pipeline.backfill_splits --apply`` run. OPERATIONAL state: a row is
written once per fill and updated in place only when an incomplete fill is retried. The caller commits.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from uuid import UUID

import psycopg

SOURCE_SEED = "s1_backfill"
SOURCE_JOB = "newcomer"
SOURCE_CLI = "cli"


@dataclass(frozen=True)
class Marker:
    tenant_id: UUID
    security_id: UUID
    since: date
    source: str
    complete: bool
    attempts: int
    polygon_splits: int
    appended: int
    note: str | None
    filled_at: datetime


def get(
    conn: psycopg.Connection, *, tenant_id: UUID, security_ids: Iterable[UUID]
) -> dict[UUID, Marker]:
    """The markers of ``security_ids`` (one tenant). Read-only."""
    ids = list({s for s in security_ids})
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id, security_id, since, source, complete, attempts, polygon_splits, appended, note, "
            "filled_at FROM security_split_history WHERE tenant_id = %s AND security_id = ANY(%s::uuid[])",
            (tenant_id, ids),
        )
        return {r["security_id"]: Marker(**r) for r in cur.fetchall()}


def mark(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    since: date,
    source: str,
    complete: bool,
    polygon_splits: int = 0,
    appended: int = 0,
    note: str | None = None,
) -> None:
    """Record one company's fill. IDEMPOTENT: a first fill inserts; a later fill of a COMPLETE row changes
    nothing (the first fill stands); a fill of an INCOMPLETE row (a retry) records the new outcome and counts
    one more attempt, adding what it appended. The caller commits."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO security_split_history
                   (tenant_id, security_id, since, source, complete, polygon_splits, appended, note)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (tenant_id, security_id) DO UPDATE
                  SET complete = EXCLUDED.complete,
                      attempts = security_split_history.attempts + 1,
                      polygon_splits = EXCLUDED.polygon_splits,
                      appended = security_split_history.appended + EXCLUDED.appended,
                      note = EXCLUDED.note,
                      source = EXCLUDED.source,
                      since = LEAST(security_split_history.since, EXCLUDED.since),
                      updated_at = now()
                WHERE NOT security_split_history.complete""",
            (tenant_id, security_id, since, source, complete, polygon_splits, appended, note),
        )


def mark_many(
    conn: psycopg.Connection,
    members: Iterable[tuple[UUID, UUID]],
    *,
    since: date,
    source: str,
    polygon_splits: Mapping[UUID, int] | None = None,
    appended: Mapping[UUID, int] | None = None,
) -> int:
    """``mark`` every ``(tenant_id, security_id)`` as COMPLETE (a backfill run that covered them). Returns how
    many were given. The caller commits."""
    n = 0
    for tenant_id, sid in members:
        mark(
            conn,
            tenant_id=tenant_id,
            security_id=sid,
            since=since,
            source=source,
            complete=True,
            polygon_splits=(polygon_splits or {}).get(sid, 0),
            appended=(appended or {}).get(sid, 0),
        )
        n += 1
    return n


def count(conn: psycopg.Connection) -> int:
    """``count(*)`` of the table (the idempotency tests count it)."""
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_split_history")
        return int(cur.fetchone()["n"])
