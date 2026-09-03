from __future__ import annotations

from collections.abc import Collection
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


def as_of_many(
    conn: psycopg.Connection,
    table: str,
    *,
    security_ids: Collection[UUID],
    asof: date,
    known_at: datetime,
    tenant_id: UUID,
    valid_from_lower: date | None = None,
) -> dict[UUID, list[dict[str, Any]]]:
    """The BATCH twin of ``as_of``: ONE query for a whole basket of securities, the SAME bitemporal gate.

    Row-for-row equivalent to calling ``as_of`` once per id — same WHERE gate (``valid_from <= asof AND
    <knowability> <= known_at``, tenant-filtered exactly like ``_as_of``), the same latest-version-per-
    natural-key pick, the same deterministic ``recorded_at DESC, id DESC`` tiebreak, and the same
    within-security row ORDER: the ``DISTINCT ON`` partition is the per-security natural key with
    ``security_id`` prefixed, so each security's partition (and its ``ORDER BY`` prefix) is exactly what
    the scoped read sees. Returns an entry for EVERY requested id — ``[]`` when it has no rows — so a
    caller can memoize "nothing on file" and never re-query.

    ``valid_from_lower`` is an OPTIONAL event-time floor (``valid_from >= lower``): the horizon-registry
    read bound (``signals/horizons.py``). It is version-safe on the tables it is used for because every
    version of one logical fact shares its ``valid_from`` (``fact_price_eod``: ``valid_from = d`` and
    ``d`` is the natural key; ``fact_insider_txn``: ``valid_from`` IS a natural-key column), so a floor
    can only drop whole facts older than the floor — never change which VERSION of a fact wins. The
    floor is never applied by this function on its own initiative: it is a caller-supplied, registry-
    derived bound (or None), and a caller that trims further does so in Python (the memo rule).
    """
    if table not in _FACT_IDENTITY:
        raise ValueError(f"unknown fact table: {table!r}")
    ids = list(dict.fromkeys(security_ids))  # de-duplicated, order-preserving
    out: dict[UUID, list[dict[str, Any]]] = {sid: [] for sid in ids}
    if not ids:
        return out
    partition = ["security_id"] + [c for c in _FACT_IDENTITY[table] if c != "security_id"]
    ident = sql.SQL(", ").join(sql.Identifier(c) for c in partition)
    knowability = sql.SQL(knowability_expr(table))  # noqa: S608 — hardcoded map, never caller input
    lower = sql.SQL(" AND valid_from >= %(lower)s") if valid_from_lower is not None else sql.SQL("")
    query = sql.SQL(
        "SELECT DISTINCT ON ({ident}) * FROM {table} "
        "WHERE tenant_id = %(tenant_id)s AND security_id = ANY(%(ids)s) "
        "AND valid_from <= %(asof)s AND {knowability} <= %(known_at)s{lower} "
        "ORDER BY {ident}, recorded_at DESC, id DESC"
    ).format(ident=ident, table=sql.Identifier(table), knowability=knowability, lower=lower)
    with conn.cursor() as cur:
        cur.execute(
            query,
            {
                "tenant_id": tenant_id,
                "ids": ids,
                "asof": asof,
                "known_at": known_at,
                "lower": valid_from_lower,
            },
        )
        for row in cur.fetchall():
            out[row["security_id"]].append(row)
    return out


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
