from __future__ import annotations

from collections.abc import Iterable
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


def ciks_for(
    conn: psycopg.Connection,
    security_ids: Iterable[UUID],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[UUID, str | None]:
    """Map security ids -> their issuer CIK (to resolve filing provenance to an EDGAR URL).

    The URL must be built from the ISSUER's CIK, not a filing accession's prefix (which is the filing
    agent's CIK). Ids with no master row are omitted.
    """
    ids = list({sid for sid in security_ids})
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, cik FROM security_master WHERE tenant_id = %s AND id = ANY(%s)",
            (tenant_id, ids),
        )
        return {row["id"]: row["cik"] for row in cur.fetchall()}


def ids_for_tickers(
    conn: psycopg.Connection,
    tickers: Iterable[str],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[str, UUID]:
    """Map tickers -> their security id (the inverse of ``tickers_for``).

    The DOE feed resolves an awardee's ticker (from the curated table) to the security it should fire a
    catalyst on. Tickers with no master row are omitted — the feed skips awardees outside this universe.
    """
    wanted = {t.upper() for t in tickers}
    if not wanted:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (ticker) ticker, id FROM security_master "
            "WHERE tenant_id = %s AND ticker = ANY(%s) ORDER BY ticker, recorded_at DESC",
            (tenant_id, list(wanted)),
        )
        return {row["ticker"]: row["id"] for row in cur.fetchall()}


def search(
    conn: psycopg.Connection,
    query: str,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    limit: int = 10,
) -> list[Security]:
    """Discovery net over the per-tenant master: the securities whose ticker or name contains ``query``
    (case-insensitive), latest row per ticker, for the operator to PICK from when authoring a basket.

    INVARIANT #2 by construction — "fuzzy is a discovery net, never a decider": every row returned is an
    EXACT master member; the operator picks the exact ``security_id``. **Read-only** — it never ingests
    (cf. ``resolve``'s ``allow_live`` live path) and never conjures an unknown ticker into existence. A
    blank query matches all (capped at ``limit``); no match returns ``[]``. Tenant-scoped like every
    master read.
    """
    like = f"%{query.strip().upper()}%"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (ticker) * FROM security_master "
            "WHERE tenant_id = %s AND ticker IS NOT NULL AND (ticker LIKE %s OR UPPER(name) LIKE %s) "
            "ORDER BY ticker, recorded_at DESC LIMIT %s",
            (tenant_id, like, like, limit),
        )
        return [_row_to_security(row) for row in cur.fetchall()]


def tickers_for(
    conn: psycopg.Connection,
    security_ids: Iterable[UUID],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[UUID, str | None]:
    """Map security ids -> their ticker (to attribute each fired trigger to its name on the card).

    A multi-name basket fires triggers on several securities; the card lists them by ticker so the
    operator sees which name moved. Ids with no master row are omitted.
    """
    ids = list({sid for sid in security_ids})
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker FROM security_master WHERE tenant_id = %s AND id = ANY(%s)",
            (tenant_id, ids),
        )
        return {row["id"]: row["ticker"] for row in cur.fetchall()}


def exists(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> bool:
    """Whether ``security_id`` is in THIS tenant's master — the write-side tenant-boundary check. A write
    path (e.g. ratifying a fact) validates this fail-closed before persisting: the tenant comes from the
    deployment resolver, but the ``security_id`` is caller-supplied, so a foreign/unknown id must NOT write
    a fact under the deployment tenant."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM security_master WHERE tenant_id = %s AND id = %s LIMIT 1",
            (tenant_id, security_id),
        )
        return cur.fetchone() is not None
