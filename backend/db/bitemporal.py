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
    "fact_insider_txn": ["accession", "insider_name", "valid_from"],
    "fact_price_eod": ["security_id", "d"],
}


def as_of(
    conn: psycopg.Connection,
    table: str,
    *,
    security_id: UUID,
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
) -> list[dict[str, Any]]:
    """Bitemporal point-in-time read — no lookahead on either axis.

    Returns the latest version (by ``recorded_at``) of each logical fact whose event time
    (``valid_from``) is on/before ``asof`` AND whose ingest time (``recorded_at``) is on/before
    ``known_at``. ``known_at`` is what makes replay honest: a correction recorded after it cannot
    leak into a read pinned at an earlier transaction time.
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    ident = sql.SQL(", ").join(sql.Identifier(c) for c in _FACT_IDENTITY[table])
    query = sql.SQL(
        "SELECT DISTINCT ON ({ident}) * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND security_id = %(security_id)s "
        "AND valid_from <= %(asof)s AND recorded_at <= %(known_at)s "
        "ORDER BY {ident}, recorded_at DESC"
    ).format(ident=ident, table=sql.Identifier(table))
    with conn.cursor() as cur:
        cur.execute(
            query,
            {
                "tenant_id": tenant_id,
                "security_id": security_id,
                "asof": asof,
                "known_at": known_at,
            },
        )
        return cur.fetchall()


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
