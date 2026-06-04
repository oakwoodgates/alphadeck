from __future__ import annotations

from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.security import Security
from securities import figi, sec_tickers


def _row_to_security(row: dict) -> Security:
    """The row -> domain boundary for the security master."""
    return Security(
        id=row["id"],
        tenant_id=row["tenant_id"],
        ticker=row["ticker"],
        name=row.get("name"),
        cik=row.get("cik"),
        cusip=row.get("cusip"),
        figi=row.get("figi"),
    )


def _lookup(conn: psycopg.Connection, ticker: str, tenant_id: UUID) -> Security | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM security_master WHERE tenant_id = %s AND ticker = %s "
            "ORDER BY recorded_at DESC LIMIT 1",
            (tenant_id, ticker),
        )
        row = cur.fetchone()
    return _row_to_security(row) if row else None


def resolve(
    conn: psycopg.Connection,
    ticker: str,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    effective_date: date | None = None,
    figi_cache_dir: Path | None = None,
    sec_cache_dir: Path | None = None,
    allow_live: bool = False,
) -> Security:
    """Resolve a ticker to a canonical Security, inserting it into the master if new (append-only).

    FIGI/name come from OpenFIGI, CIK from SEC company_tickers — both cache-first, live only behind
    ``allow_live`` (the caller wires the env flag). Idempotent: an already-resolved ticker is read
    back from the master, never re-inserted.
    """
    ticker = ticker.upper()
    existing = _lookup(conn, ticker, tenant_id)
    if existing is not None:
        return existing

    mapping = figi.map_ticker(ticker, cache_dir=figi_cache_dir, allow_live=allow_live)
    cik = sec_tickers.cik_for(ticker, cache_dir=sec_cache_dir, allow_live=allow_live)

    sid = uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, figi, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                sid,
                tenant_id,
                ticker,
                cik,
                mapping.get("figi"),
                mapping.get("name"),
                effective_date or date.today(),
            ),
        )
    conn.commit()
    return Security(
        id=sid,
        tenant_id=tenant_id,
        ticker=ticker,
        name=mapping.get("name"),
        cik=cik,
        figi=mapping.get("figi"),
    )
