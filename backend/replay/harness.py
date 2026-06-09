from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

import duckdb
import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.thesis import Thesis
from pipeline.core import assemble_from_pit
from replay.pit import ReplayPointInTimeData
from replay.schema import CallSnapshot
from repositories import thesis_repo


def trading_sessions(
    con: duckdb.DuckDBPyConnection,
    security_ids: list[UUID],
    start: date,
    end: date,
    tenant_id: UUID,
) -> list[date]:
    """The real EOD sessions to sweep — the union of ``fact_price_eod.d`` over the given securities within
    ``[start, end]`` (so we step on actual trading days, not weekends/holidays with a stale tape).
    """
    if not security_ids:
        return []
    placeholders = ", ".join("?" for _ in security_ids)
    rows = con.execute(
        f"SELECT DISTINCT d FROM fact_price_eod "
        f"WHERE tenant_id = ? AND security_id IN ({placeholders}) AND d BETWEEN ? AND ? "
        f"ORDER BY d",
        [str(tenant_id), *[str(s) for s in security_ids], start, end],
    ).fetchall()
    return [r[0] for r in rows]


def replay_thesis(
    con: duckdb.DuckDBPyConnection,
    thesis: Thesis,
    *,
    start: date,
    end: date,
    known_at: datetime,
    cfg: CallConfig = DEFAULT_CONFIG,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> list[CallSnapshot]:
    """Sweep one thesis's call across its real trading sessions in the window, running the REAL pipeline
    (``assemble_from_pit``) over a replay pit capped at each ``(T, known_at)``. ZERO forward knowledge —
    the loop only records snapshots; scoring is a separate pass (``replay.scoring``)."""
    sids = [m.security_id for m in thesis.basket if m.security_id is not None]
    snapshots: list[CallSnapshot] = []
    for t in trading_sessions(con, sids, start, end, tenant_id):
        pit = ReplayPointInTimeData(con, asof=t, known_at=known_at, tenant_id=tenant_id)
        snapshots.append(CallSnapshot.from_card(assemble_from_pit(pit, thesis, t, cfg)))
    return snapshots


def replay_all(
    conn: psycopg.Connection,
    con: duckdb.DuckDBPyConnection,
    *,
    start: date,
    end: date,
    known_at: datetime,
    cfg: CallConfig = DEFAULT_CONFIG,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[UUID, list[CallSnapshot]]:
    """Replay every thesis (definitions loaded from the operational SoR — see the KNOWN LIMITATION in
    docs/REPLAY.md: thesis defs are NOT replayed bitemporally) over the window. Returns the per-thesis
    call timeline. ``cfg`` is a parameter so the recalibration pass (step 2) can sweep the dials."""
    return {
        thesis.id: replay_thesis(
            con, thesis, start=start, end=end, known_at=known_at, cfg=cfg, tenant_id=tenant_id
        )
        for thesis in thesis_repo.list_all(conn)
    }
