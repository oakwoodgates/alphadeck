from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from pathlib import Path
from uuid import UUID, uuid4

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.security import Security, SecurityIdentity
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
        sector=row.get("sector"),
        exchange=row.get("exchange"),
        status=row.get("status"),
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
    """Resolve a ticker to a canonical Security, INSERTING it into the master if new (this path only ever
    inserts — idempotent, never updates).

    FIGI/name come from OpenFIGI, CIK from SEC company_tickers — both cache-first, live only behind
    ``allow_live`` (the caller wires the env flag). Idempotent: an already-resolved ticker is read
    back from the master, never re-inserted.

    Coexists with ``populate_universe`` (the bulk broadener): both set ``cik``, so neither double-inserts the
    other's rows (resolve dedups by ticker via ``_lookup``; the broadener by ``(cik, ticker)``). NOTE: the
    master is NOT append-only — the broadener UPDATEs a name in place (the id stays stable); see
    ``populate_universe``. Post-broadener the universe is already loaded, so this path rarely inserts.
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


def populate_universe(
    conn: psycopg.Connection,
    rows: Iterable[tuple[str, str, str | None]],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    effective_date: date | None = None,
) -> dict[str, int]:
    """Populate THIS tenant's master from the SEC ``company_tickers`` universe — idempotent, additive, and
    keyed on ``(cik, ticker)``. The broadener that lifts the loop from "the seeded basket" to "any name you
    just thought of". The caller commits.

    Per ``(cik, ticker)`` triple (from ``sec_tickers.load_all``): absent -> INSERT a new row (fresh id);
    present with a changed ``name`` -> UPDATE the name **in place** (id stable); unchanged -> skip. Returns
    ``{"inserted", "updated", "skipped"}``.

    INVARIANT #2 by construction: only EXACT ``company_tickers`` mappings are written, never a fuzzy guess
    (the ``search`` discovery net still only suggests; the operator still picks the exact id). Identity is
    keyed on the stable CIK (the extractor keys on CIK; renames preserve it), with ticker in the key so a
    CIK's several share classes (dual-class) each stay a pickable row. The seeded names reconcile for free —
    their ``(cik, ticker)`` is already present, so their ids are reused, never duplicated, and their facts
    stay linked.

    NOTE — the master's FIRST in-place mutation. Legal: the ``no_update`` trigger guards the *fact* tables,
    NOT ``security_master``. Safe: nothing reads the master as-of, so dropping the prior name (we keep only
    the current mapping) leaks into no point-in-time read. Necessary: 8 tables FK ``security_id`` ->
    ``security_master(id)``, so the id MUST stay stable or those facts orphan — an in-place UPDATE keeps it.
    """
    existing: dict[tuple[str, str], tuple[UUID, str | None]] = {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (cik, ticker) cik, ticker, id, name FROM security_master "
            "WHERE tenant_id = %s AND cik IS NOT NULL AND ticker IS NOT NULL "
            "ORDER BY cik, ticker, recorded_at DESC",
            (tenant_id,),
        )
        for r in cur.fetchall():
            existing[(r["cik"], r["ticker"])] = (r["id"], r["name"])

    valid_from = effective_date or date.today()
    inserts: list[tuple] = []
    updates: list[tuple] = []
    seen: set[tuple[str, str]] = set()
    for cik, ticker, name in rows:
        if not cik or not ticker:
            continue
        ticker = ticker.upper()
        key = (cik, ticker)
        if key in seen:  # company_tickers shouldn't repeat a (cik, ticker); be defensive anyway
            continue
        seen.add(key)
        current = existing.get(key)
        if current is None:
            inserts.append((uuid4(), tenant_id, cik, ticker, name, valid_from))
        elif name != current[1]:
            updates.append((name, current[0]))

    with conn.cursor() as cur:
        if inserts:
            cur.executemany(
                "INSERT INTO security_master (id, tenant_id, cik, ticker, name, valid_from) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                inserts,
            )
        if updates:
            cur.executemany(
                "UPDATE security_master SET name = %s, recorded_at = now() WHERE id = %s",
                updates,
            )
    return {
        "inserted": len(inserts),
        "updated": len(updates),
        "skipped": len(seen) - len(inserts) - len(updates),
    }


def enrich(
    conn: psycopg.Connection,
    security_id: UUID,
    identity: SecurityIdentity,
    *,
    source: str,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> bool:
    """Enrich one master row with machine-parsed IDENTITY (sector/exchange/status) from EDGAR submissions —
    UPDATE-in-place, the same identity-mutable pattern as ``populate_universe``'s name-update. The id stays
    stable (the fact tables that FK ``security_id`` never orphan); nothing reads the master as-of, so
    overwriting a stale value leaks into no point-in-time read. Idempotent — a re-run overwrites, never appends.

    ``source`` is the ENRICHMENT BASIS stored alongside (e.g. ``submissions:CIK0001849056``). Identity carries
    a basis, NEVER the facts' ``ratified_by`` — so machine-parsed identity can't masquerade as an
    operator-vouched fact (#1/#3: identity is not a number on a call card). ``identity.former_names`` is NOT
    persisted here — the identity-bridge slice owns that.

    Returns whether a row was updated: a foreign/unknown id under this tenant updates nothing (fail-closed, the
    same write-side tenant boundary as ``exists``). The caller commits (so an enrichment pass can batch).
    """
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE security_master SET sector = %s, exchange = %s, status = %s, "
            "enriched_source = %s, enriched_at = now() WHERE tenant_id = %s AND id = %s",
            (identity.sector, identity.exchange, identity.status, source, tenant_id, security_id),
        )
        return cur.rowcount > 0


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


def ids_for_ciks(
    conn: psycopg.Connection,
    ciks: Iterable[str],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> dict[str, UUID]:
    """Map CIKs -> their canonical security id (the inverse of ``ciks_for``). The EDGAR-first discovery path:
    EFTS returns the CIKs of US filers in a theme; this resolves each to an EXACT master member (INVARIANT #2,
    the cleanest form — CIK is the stable identity, so a rename / DBA / ticker change can't break the match).

    One id per CIK (``DISTINCT ON (cik)``, latest ``recorded_at`` — a CIK's several share classes collapse to
    its primary row; the operator picks a specific class if it matters). CIKs with no master row are omitted
    (foreign / no US ticker -> the tail-sweep's job, not placeable here).

    The master stores CIKs as the EDGAR zero-padded 10-digit string (``sec_tickers`` writes ``f"{int:010d}"``);
    EFTS returns the same form, so the match is direct. We zero-pad numeric inputs anyway so an unpadded caller
    can't silently miss (a format mismatch would return nothing — the invisible-failure class). Keys returned
    are the normalized 10-digit form."""
    wanted = {(c.zfill(10) if c.isdigit() else c) for c in ciks if c}
    if not wanted:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (cik) cik, id FROM security_master "
            "WHERE tenant_id = %s AND cik = ANY(%s) ORDER BY cik, recorded_at DESC",
            (tenant_id, list(wanted)),
        )
        return {row["cik"]: row["id"] for row in cur.fetchall()}


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


def get(
    conn: psycopg.Connection, security_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> Security | None:
    """Fetch one security by id within THIS tenant (``None`` if absent). The full row for the few callers
    that need the ticker / name / CIK, not just existence (cf. ``exists``). Tenant-scoped like every master
    read."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM security_master WHERE tenant_id = %s AND id = %s LIMIT 1",
            (tenant_id, security_id),
        )
        row = cur.fetchone()
    return _row_to_security(row) if row else None
