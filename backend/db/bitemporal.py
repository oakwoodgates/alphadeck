from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql

# Whitelist of bitemporal fact tables -> the natural-key columns that identify one logical fact
# (so an as-of read keeps the latest *version* of each, by recorded_at). Whitelisting also keeps
# the dynamic SQL injection-safe.
_FACT_IDENTITY: dict[str, list[str]] = {
    "fact_insider_txn": ["accession", "insider_name", "valid_from", "txn_seq"],
    "fact_price_eod": ["security_id", "d"],
    "fact_dilution": ["accession"],  # one convert offering per accession
    "fact_catalyst": [
        "source_ref"
    ],  # one catalyst per source (accession / award id / ratified URL)
    "fact_theme_conviction": ["source_ref"],  # one theme conviction per source (ratified doc / URL)
}


def _as_of(
    conn: psycopg.Connection,
    table: str,
    *,
    scope_col: str,
    scope_id: UUID,
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
) -> list[dict[str, Any]]:
    """Shared bitemporal point-in-time read, scoped by ``scope_col`` — no lookahead on either axis.

    Returns the latest version (by ``recorded_at``) of each logical fact whose event time
    (``valid_from``) is on/before ``asof`` AND whose ingest time (``recorded_at``) is on/before
    ``known_at``. ``known_at`` is what makes replay honest: a correction recorded after it cannot
    leak into a read pinned at an earlier transaction time. ``scope_col`` is a TRUSTED literal
    ('security_id' / 'thesis_id') from the wrappers below, never caller input — kept injection-safe
    via ``sql.Identifier``.

    On a ``recorded_at`` tie (two versions of one natural key recorded at the same instant), the row with
    the greater ``id`` wins: a deterministic secondary sort so the read is reproducible and the DuckDB
    replay mirror, which applies the identical ``recorded_at DESC, id DESC`` ordering, agrees row-for-row.
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    ident = sql.SQL(", ").join(sql.Identifier(c) for c in _FACT_IDENTITY[table])
    query = sql.SQL(
        "SELECT DISTINCT ON ({ident}) * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND {scope} = %(scope_id)s "
        "AND valid_from <= %(asof)s AND recorded_at <= %(known_at)s "
        "ORDER BY {ident}, recorded_at DESC, id DESC"
    ).format(ident=ident, table=sql.Identifier(table), scope=sql.Identifier(scope_col))
    with conn.cursor() as cur:
        cur.execute(
            query,
            {
                "tenant_id": tenant_id,
                "scope_id": scope_id,
                "asof": asof,
                "known_at": known_at,
            },
        )
        return cur.fetchall()


def as_of(
    conn: psycopg.Connection,
    table: str,
    *,
    security_id: UUID,
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
) -> list[dict[str, Any]]:
    """Bitemporal as-of read for a SECURITY-scoped fact table. **Behavior-identical** to the original
    single-function ``as_of`` (same inputs, same rows); it delegates to the shared ``_as_of``. The only
    SQL difference versus that original is cosmetic: the scope column is rendered quoted via
    ``sql.Identifier`` (``"security_id"``), semantically identical for the lowercase fact-table columns.
    """
    return _as_of(
        conn,
        table,
        scope_col="security_id",
        scope_id=security_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
    )


def as_of_thesis(
    conn: psycopg.Connection,
    table: str,
    *,
    thesis_id: UUID,
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
) -> list[dict[str, Any]]:
    """Bitemporal as-of read for a THESIS-scoped fact table (e.g. ``fact_theme_conviction``) — the same
    honesty as ``as_of`` but keyed by thesis, since a theme conviction is basket-level, not co-located
    on a security."""
    return _as_of(
        conn,
        table,
        scope_col="thesis_id",
        scope_id=thesis_id,
        asof=asof,
        known_at=known_at,
        tenant_id=tenant_id,
    )


def append_fact(conn: psycopg.Connection, table: str, values: dict[str, Any]) -> UUID:
    """Append a fact row. Corrections are new rows with a later ``recorded_at`` — never UPDATEs."""
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    cols = list(values.keys())
    query = sql.SQL("INSERT INTO {table} ({cols}) VALUES ({vals}) RETURNING id").format(
        table=sql.Identifier(table),
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
        vals=sql.SQL(", ").join(sql.Placeholder() for _ in cols),
    )
    with conn.cursor() as cur:
        cur.execute(query, list(values.values()))
        return cur.fetchone()["id"]
