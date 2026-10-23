"""The DATA REPAIR journal (editions S7, migration 0065) — one row per applied repair (and per revoke).

A repair CLI that rewrites FACT history calls ``journal`` right after its own write committed, still under its
repair lock: one row in its OWN write transaction (this function commits it). A dry run never calls it. The
nightly editions job (``editions.cut``) reads ``pending`` — the rows applied after the current edition's cut
(or its last no-change trial) — and runs a repair trial when one changed rows. The journal is append-only (the
``no_update`` trigger).

Hooked (each tool's ``main`` journals its ``--apply`` / ``--revoke``; ``--execute`` for the two backfills):
``repair_benchmark_bars``, ``dedup_identical_versions`` (deletes), ``repair_insider_ownership``,
``repair_insider_filer_text``, ``relink_amendments``, ``rederive_amendment_matches``, ``set_aside_fundamentals``
(apply + revoke), ``repair_split_basis`` (apply + revoke), ``reingest_fundamentals``, ``backfill_splits``,
``repair_activist_misattribution`` (deletes), ``repair_adr_insider_misattribution``,
``repair_impossible_txn_dates`` (deletes), ``backfill_accepted``, ``backfill_aff10b5_1``. Not hooked: what only
touches ``calls`` (``annotate_phantom_arms``, ``repair_reconstructed_precreation``, ``pipeline.backfill``),
ordinary nightly data flow (``detect_splits``), and ``split_repair`` (a pure module, no CLI — its writer is
``repair_split_basis``).

Self-contained: psycopg only, so a repair CLI in the lean image can journal without the editions engine.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from db.session import DEFAULT_TENANT_ID

ACTIONS = ("apply", "revoke")


@dataclass(frozen=True)
class RepairRow:
    """One journaled repair."""

    id: UUID
    tenant_id: UUID
    tool: str
    action: str
    applied_at: datetime
    summary: str
    rows_changed: int
    deletes: bool
    counts: dict[str, Any] = field(default_factory=dict)
    provenance: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "tool": self.tool,
            "action": self.action,
            "applied_at": self.applied_at.isoformat(),
            "summary": self.summary,
            "rows_changed": self.rows_changed,
            "deletes": self.deletes,
            "provenance": self.provenance,
        }


def _row(r: Any) -> RepairRow:
    return RepairRow(
        id=r["id"],
        tenant_id=r["tenant_id"],
        tool=r["tool"],
        action=r["action"],
        applied_at=r["applied_at"],
        summary=r["summary"],
        rows_changed=r["rows_changed"],
        deletes=r["deletes"],
        counts=dict(r["counts"] or {}),
        provenance=r["provenance"],
    )


def journal(
    conn: psycopg.Connection,
    *,
    tool: str,
    summary: str,
    rows_changed: int,
    action: str = "apply",
    counts: Mapping[str, Any] | None = None,
    deletes: bool = False,
    provenance: str | None = None,
    tenant_id: UUID | str = DEFAULT_TENANT_ID,
) -> UUID:
    """Append ONE journal row and COMMIT it. Call it only after the repair's own writes committed (an open
    transaction on ``conn`` holding uncommitted repair writes would be committed with it). Raises on a bad
    ``action`` or a negative count; a database fault propagates — the CLI fails loud, saying the repair is
    committed but not journaled (``python -m editions.run cut`` then checks it by hand)."""
    if action not in ACTIONS:
        raise ValueError(f"action must be one of {ACTIONS}, got {action!r}")
    if rows_changed < 0:
        raise ValueError("rows_changed cannot be negative")
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO data_repair
                   (tenant_id, tool, action, summary, counts, rows_changed, deletes, provenance)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING id""",
            (
                UUID(str(tenant_id)),
                tool,
                action,
                summary,
                Json(_jsonable(dict(counts or {}))),
                int(rows_changed),
                bool(deletes),
                str(provenance) if provenance else None,
            ),
        )
        row = cur.fetchone()
    conn.commit()
    rid = row["id"] if isinstance(row, Mapping) else row[0]
    print(f"journal: data_repair {rid} ({tool} {action}, {rows_changed} row(s) changed)")
    return rid


def _jsonable(x: Any) -> Any:
    if isinstance(x, Mapping):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set, frozenset)):
        return [_jsonable(v) for v in x]
    if x is None or isinstance(x, (bool, int, float, str)):
        return x
    if hasattr(x, "isoformat"):
        return x.isoformat()
    return str(x)


def pending(
    conn: psycopg.Connection, *, tenant_id: UUID, after: datetime, through: datetime | None = None
) -> list[RepairRow]:
    """The tenant's journaled repairs applied after ``after`` (and at or before ``through`` when given),
    oldest first — every row, a zero-change one included (the caller decides what starts a trial).
    """
    sql = "SELECT * FROM data_repair WHERE tenant_id = %s AND applied_at > %s"
    params: list[Any] = [tenant_id, after]
    if through is not None:
        sql += " AND applied_at <= %s"
        params.append(through)
    with conn.cursor() as cur:
        cur.execute(sql + " ORDER BY applied_at, seq", params)
        return [_row(r) for r in cur.fetchall()]


def count(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM data_repair")
        r = cur.fetchone()
    return r["n"] if isinstance(r, Mapping) else r[0]
