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
    "fact_revenue_mix": [
        "source_ref"
    ],  # one revenue-mix fact per source (10-K segment) — Workbench purity
    "fact_shares_outstanding": [
        "source_ref"
    ],  # one shares fact per source (10-Q cover) — Workbench mkt cap
    "fact_cash_burn": ["source_ref"],  # one cash/burn fact per source (10-Q) — Workbench runway
    "fact_fund_shares": [
        "security_id",
        "d",
    ],  # one fund shares-out sample per day (ETF net flow) — a restated count is a new version
    "fact_fundamentals": [
        "security_id",
        "metric_key",
        "period_end",
    ],  # one financial fact per (security, metric, fiscal period) — a restatement is a new version (§2.2)
    "fact_corporate_event": [
        "accession"
    ],  # one 8-K filing per accession (within the security scope) — items resolving is a new version
    "fact_activist_stake": [
        "accession"
    ],  # one 13D/G filing per accession (within the SUBJECT security scope) — filer/pct resolving is a new version (S5)
}

# The KNOWABILITY axis per table — the SQL expression for the transaction-time no-lookahead gate
# (``{expr} <= known_at``): "which rows had THIS SYSTEM actually recorded as of the pinned read time?".
# EVERY fact table gates on ``recorded_at`` (our ingest instant) — the strict "what we held" axis, and the
# SINGLE no-lookahead definition across all tables (a mirror test,
# ``test_mirror_has_no_valid_time_lookahead_corporate_events``, pins that strictness).
#
# ``fact_insider_txn`` deliberately does NOT special-case this onto ``COALESCE(accepted, recorded_at)``
# (PR #283 tried that; reverted for one strict definition everywhere). ``accepted`` (the SEC acceptance
# datetime) stays a DISPLAY + metrics column ONLY: the Scoreboard's honest ``disclosed`` line
# (``scoreboard/overlays.py``) and the B2 disclosure-lag metric (``scoreboard/provenance.py``) read it
# DIRECTLY, never through this gate. It must never gate the as-of read — insider rows are re-versioned in
# place by backfills (0037/0040), so a v2 carrying the SAME ``accepted`` but a LATER ``recorded_at`` would
# clear a ``COALESCE(accepted, …)`` gate BEFORE it was recorded, and the ``ORDER BY recorded_at DESC``
# version-pick would then surface it — the same latent lookahead the 8-K resolve carries. Gating on
# ``recorded_at`` closes that. (Live reads pin ``known_at = now`` where ``accepted <= recorded_at <= now``,
# so the choice is inert on every live path; it matters only for an honest past-as-of replay.)
#
# The map is intentionally EMPTY — every table falls through to ``_DEFAULT_KNOWABILITY_EXPR``. It stays as
# the injection-safe seam for any future per-table override (a trusted literal, never caller input); both
# engines (Postgres ``_as_of`` + DuckDB ``ReplayPointInTimeData._as_of``) render whatever it returns
# BYTE-IDENTICALLY, so the replay-parity gate stays provable.
_KNOWABILITY_EXPR: dict[str, str] = {}
_DEFAULT_KNOWABILITY_EXPR = "recorded_at"


def knowability_expr(table: str) -> str:
    """The transaction-time no-lookahead gate column/expression for ``table`` (``_KNOWABILITY_EXPR`` above,
    else ``recorded_at`` — currently every table). ONE source of truth, imported by both the Postgres and
    DuckDB as-of reads so their WHERE gate is identical (replay parity)."""
    return _KNOWABILITY_EXPR.get(table, _DEFAULT_KNOWABILITY_EXPR)


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
    # the knowability gate — recorded_at for every table (the strict "what we held" no-lookahead axis;
    # accepted is a display/metrics column, never a gate); the version-pick ORDER BY is unchanged
    knowability = sql.SQL(knowability_expr(table))  # noqa: S608 — hardcoded map, never caller input
    query = sql.SQL(
        "SELECT DISTINCT ON ({ident}) * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND {scope} = %(scope_id)s "
        "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s "
        "ORDER BY {ident}, recorded_at DESC, id DESC"
    ).format(
        ident=ident,
        table=sql.Identifier(table),
        scope=sql.Identifier(scope_col),
        knowability=knowability,
    )
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


def _fact_exists(
    conn: psycopg.Connection,
    table: str,
    *,
    scope_col: str,
    scope_id: UUID,
    tenant_id: UUID,
    key: dict[str, Any],
) -> bool:
    """Shared "is this natural key stored AT ALL?" probe — see ``fact_exists`` for the contract.

    ``scope_col`` is a TRUSTED literal from the wrappers below, never caller input. Every ``key`` column
    must belong to the table's ``_FACT_IDENTITY`` — the SAME whitelist ``_as_of`` renders from, so the
    dynamic SQL stays injection-safe.
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    if not key:
        raise ValueError(f"fact_exists on {table!r} needs at least one natural-key column")
    allowed = set(_FACT_IDENTITY[table])
    unknown = sorted(set(key) - allowed)
    if unknown:
        raise ValueError(
            f"{unknown} is not part of {table!r}'s natural key ({sorted(allowed)}) — "
            "an existence probe must key on the identity, nothing else"
        )
    where = sql.SQL(" AND ").join(
        sql.SQL("{col} = {val}").format(col=sql.Identifier(c), val=sql.Placeholder(c)) for c in key
    )
    query = sql.SQL(
        "SELECT 1 FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND {scope} = %(scope_id)s AND {where} LIMIT 1"
    ).format(table=sql.Identifier(table), scope=sql.Identifier(scope_col), where=where)
    with conn.cursor() as cur:
        cur.execute(query, {"tenant_id": tenant_id, "scope_id": scope_id, **key})
        return cur.fetchone() is not None


def fact_exists(
    conn: psycopg.Connection,
    table: str,
    *,
    security_id: UUID,
    tenant_id: UUID,
    **key: Any,
) -> bool:
    """Is ANY version of this natural key already stored for (tenant, security)?

    **This is NOT an as-of read** and must never stand in for one. It is deliberately version- AND
    time-agnostic: no ``valid_from <= asof`` gate, no ``recorded_at <= known_at`` gate, no ``DISTINCT ON``
    version pick. It answers exactly one question — "have we stored this fact at all?" — which is the right
    question for a caller replaying a STATIC fixture that never restates a value (``pipeline.seed``, whose
    demo fixtures re-run at every container boot). Anything that needs to know what a fact SAYS at a point
    in time must call ``as_of`` / ``as_of_thesis``.

    It is a guard for the CALLER, never for the writers: the ``ingest_*`` functions stay unguarded, so a
    genuine restatement through the normal ingest paths still appends a new version (later ``recorded_at``,
    latest-version-wins on the as-of read) — the bitemporal contract is untouched.

    ``key`` names the table's ``_FACT_IDENTITY`` columns (e.g. ``source_ref=…``, ``accession=…``); a column
    outside that identity raises.
    """
    return _fact_exists(
        conn, table, scope_col="security_id", scope_id=security_id, tenant_id=tenant_id, key=key
    )


def fact_exists_thesis(
    conn: psycopg.Connection,
    table: str,
    *,
    thesis_id: UUID,
    tenant_id: UUID,
    **key: Any,
) -> bool:
    """The THESIS-scoped form of ``fact_exists`` (e.g. ``fact_theme_conviction``, which is basket-level and
    so is not co-located on a security). Same contract, same caveat: NOT an as-of read."""
    return _fact_exists(
        conn, table, scope_col="thesis_id", scope_id=thesis_id, tenant_id=tenant_id, key=key
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
