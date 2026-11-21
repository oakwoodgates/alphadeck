"""The BACKGROUND EXTRACTION store (V2 W3a): the attempt record (``platform_extraction_attempt``, migration 0207)
and the reads the gate needs (the companies on a live card, their stored periodic filings, their recent attempts).

Every read and write here is the DATA tenant's (``domain.scopes``): an attempt is platform bookkeeping, one record
per company for everyone. The attempt table is append-only (``no_update``); this module offers no edit and no
delete, and ``append_attempt`` calls ``require_write_tenant`` before it writes. No commit here: the caller owns
the transaction. Value-free: it stores and returns rows; the rules live in ``platform_values.extraction``.

``on_card_companies`` reads every workspace's baskets, but answers only company ids and the company's own master
fields (ticker, CIK, instrument kind): no thesis, workspace, user or link label leaves it. The card count itself is
computed in ``platform_values/cards.py`` (the one place), never rebuilt here.
"""

from __future__ import annotations

from collections.abc import Iterable
from uuid import UUID

import psycopg

from domain.scopes import DATA_TENANT_ID, require_write_tenant

# The forms the extractor reads (``platform_values.extraction.PERIODIC_FORMS``; repeated here so the repository
# imports no rule module).
_PERIODIC_FORMS = ["10-Q", "10-K", "20-F", "40-F"]

ATTEMPT_TABLE = "platform_extraction_attempt"


def on_card_companies(conn: psycopg.Connection) -> list[dict]:
    """Every company resolved on at least one live (non-archived) card, in ANY workspace, once:
    ``{security_id, ticker, cik, instrument_kind}``. Ids and the master's own fields only. Read-only.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT sm.id AS security_id, sm.ticker, sm.cik, sm.instrument_kind "
            "FROM security_master sm "
            "WHERE sm.id IN (SELECT bm.security_id FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id "
            "                WHERE t.archived_at IS NULL AND bm.security_id IS NOT NULL) "
            "ORDER BY sm.id"
        )
        return [dict(r) for r in cur.fetchall()]


def periodic_filings(
    conn: psycopg.Connection, security_ids: Iterable[UUID]
) -> dict[UUID, list[dict]]:
    """``{security_id: [{form, accession, filed, accepted}, ...]}``: every stored 10-Q / 10-K / 20-F / 40-F in the
    data tenant's filing index (``fact_filing_index``), one row per accession (its newest version). One query.
    """
    ids = list(dict.fromkeys(security_ids))
    out: dict[UUID, list[dict]] = {sid: [] for sid in ids}
    if not ids:
        return out
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (security_id, accession) security_id, form, accession, filed, accepted "
            "FROM fact_filing_index "
            "WHERE tenant_id = %s AND security_id = ANY(%s) AND form = ANY(%s) "
            "ORDER BY security_id, accession, recorded_at DESC, id DESC",
            (DATA_TENANT_ID, ids, _PERIODIC_FORMS),
        )
        for r in cur.fetchall():
            out[r["security_id"]].append(dict(r))
    return out


def latest_attempts(
    conn: psycopg.Connection, security_ids: Iterable[UUID], *, per_company: int = 10
) -> dict[UUID, list[dict]]:
    """``{security_id: [attempt, ...]}``, newest first, at most ``per_company`` each (a company with none is
    absent). One query."""
    ids = list(dict.fromkeys(security_ids))
    if not ids:
        return {}
    out: dict[UUID, list[dict]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM ("
            "  SELECT a.*, row_number() OVER (PARTITION BY a.security_id "
            "                                 ORDER BY a.recorded_at DESC, a.id DESC) AS rn "
            "  FROM platform_extraction_attempt a "
            "  WHERE a.tenant_id = %s AND a.security_id = ANY(%s)"
            ") x WHERE rn <= %s ORDER BY security_id, rn",
            (DATA_TENANT_ID, ids, per_company),
        )
        for r in cur.fetchall():
            row = dict(r)
            row.pop("rn", None)
            out.setdefault(row["security_id"], []).append(row)
    return out


def any_attempt(conn: psycopg.Connection, run_kind: str, *, status: str | None = None) -> bool:
    """Does any attempt of ``run_kind`` (and of ``status``, when given) exist (in the data tenant)? One query."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT EXISTS (SELECT 1 FROM platform_extraction_attempt "
            "               WHERE tenant_id = %s AND run_kind = %s "
            "               AND (%s::text IS NULL OR status = %s::text)) AS hit",
            (DATA_TENANT_ID, run_kind, status, status),
        )
        return bool(cur.fetchone()["hit"])


def append_attempt(
    conn: psycopg.Connection,
    *,
    security_id: UUID,
    run_kind: str,
    inputs_key: str,
    status: str,
    shares_reason: str | None = None,
    cash_reason: str | None = None,
    error: str | None = None,
    detail: str | None = None,
    live_fetches: int | None = None,
    tenant_id: UUID = DATA_TENANT_ID,
) -> UUID:
    """Append one attempt row (no commit). Refuses a tenant other than the data tenant (``ValueError``) before
    writing; the table's checks refuse a bad shape (an unknown status or run kind, an error with no text).
    """
    require_write_tenant(ATTEMPT_TABLE, tenant_id)
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO platform_extraction_attempt (tenant_id, security_id, run_kind, inputs_key, status, "
            "shares_reason, cash_reason, error, detail, live_fetches) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
            (
                tenant_id,
                security_id,
                run_kind,
                inputs_key,
                status,
                shares_reason,
                cash_reason,
                error,
                detail,
                live_fetches,
            ),
        )
        row = cur.fetchone()
    assert row is not None
    return row["id"]


__all__ = [
    "ATTEMPT_TABLE",
    "any_attempt",
    "append_attempt",
    "latest_attempts",
    "on_card_companies",
    "periodic_filings",
]
