"""The PLATFORM VALUE store (V2 W2a): extractor candidates (0200), steward pins (0201) and steward decisions (0205)
for a company's share count and cash/burn, plus every stored version of the two value tables.

Every function here reads or writes the DATA tenant only (``domain.scopes``): a platform value is one value per
company for everyone. The three tables are append-only (``no_update``); this module offers no edit and no delete.
A writer calls ``require_write_tenant`` first, so a row under a workspace is refused before it is written.

Value-free by design (the repository discipline): it stores and returns rows; it decides nothing. The rules live
in ``domain.platform_values`` (pure) and the state read in ``platform_values.state``; the writers that call the
``append_*`` functions are the machine writer (W2b, ``platform_values.machine``) and the steward actions (W2c,
``platform_values.steward``). Neither takes a lock here: the caller holds the company write lock
(``db.write_lock.acquire_company_lock``) and owns the transaction (no commit here).

Rows are returned as dicts keyed by the table's own columns (a candidate's standing decision is a separate read,
``standing_decision_for_candidate``). Read helpers add:

- a decision's ``undone`` (bool): a later ``undo`` names it;
- a version's ``set_aside`` (bool): every read skips it (``db.set_aside``, an undone approve / correct).

Order is total everywhere (``recorded_at``, then ``id``), so two reads of the same rows agree.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any
from uuid import UUID

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from db.set_aside import active_ids_sql
from domain.platform_values import CLOSING_ACTIONS, TABLE_OF, FactType
from domain.scopes import DATA_TENANT_ID, require_write_tenant

# The candidate columns a writer may set (``extraction_candidate``, 0200). ``id`` / ``tenant_id`` /
# ``recorded_at`` are the store's own.
CANDIDATE_COLUMNS: tuple[str, ...] = (
    "reason",
    "flags",
    "source",
    "source_ref",
    "event_date",
    "shares",
    "cash_usd",
    "quarterly_burn_usd",
    "statement_currency",
    "ads_ratio",
    "ads_ratio_status",
    "filing_accession",
    "filing_accepted",
    "note",
    "located_passages",
)

# A decision with its ``undone`` flag (a later undo names it). ``d`` = platform_value_decision.
_DECISION_SELECT = (
    "SELECT d.*, EXISTS (SELECT 1 FROM platform_value_decision u WHERE u.revokes = d.id) AS undone "
    "FROM platform_value_decision d"
)


def _one(conn: psycopg.Connection, query: str | sql.Composable, params: Any) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return dict(row) if row is not None else None


def _all(conn: psycopg.Connection, query: str | sql.Composable, params: Any) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


# --- candidates (extraction_candidate, 0200) -------------------------------------------------------------------


def candidate(conn: psycopg.Connection, candidate_id: UUID) -> dict | None:
    """One candidate by id (data tenant), or None."""
    return _one(
        conn,
        "SELECT * FROM extraction_candidate WHERE id = %s AND tenant_id = %s",
        (candidate_id, DATA_TENANT_ID),
    )


def latest_candidate(
    conn: psycopg.Connection, security_id: UUID, fact_type: FactType
) -> dict | None:
    """The NEWEST candidate for (company, type) by ``recorded_at``, then ``id`` — the one the state reads."""
    return _one(
        conn,
        "SELECT * FROM extraction_candidate WHERE tenant_id = %s AND security_id = %s AND fact_type = %s "
        "ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (DATA_TENANT_ID, security_id, fact_type),
    )


def latest_candidates(
    conn: psycopg.Connection, security_ids: Iterable[UUID], fact_type: FactType
) -> dict[UUID, dict]:
    """The BATCH twin of ``latest_candidate``: ``{security_id: its newest candidate}`` for every id that has one,
    in ONE query (the same order: ``recorded_at``, then ``id``)."""
    rows = _all(
        conn,
        "SELECT DISTINCT ON (security_id) * FROM extraction_candidate "
        "WHERE tenant_id = %s AND fact_type = %s AND security_id = ANY(%s) "
        "ORDER BY security_id, recorded_at DESC, id DESC",
        (DATA_TENANT_ID, fact_type, list(dict.fromkeys(security_ids))),
    )
    return {r["security_id"]: r for r in rows}


def candidates_for(
    conn: psycopg.Connection, security_id: UUID, fact_type: FactType, *, limit: int = 20
) -> list[dict]:
    """The newest ``limit`` candidates for (company, type), newest first."""
    return _all(
        conn,
        "SELECT * FROM extraction_candidate WHERE tenant_id = %s AND security_id = %s AND fact_type = %s "
        "ORDER BY recorded_at DESC, id DESC LIMIT %s",
        (DATA_TENANT_ID, security_id, fact_type, limit),
    )


def append_candidate(
    conn: psycopg.Connection,
    *,
    security_id: UUID,
    fact_type: FactType,
    outcome: str,
    tenant_id: UUID = DATA_TENANT_ID,
    **columns: Any,
) -> UUID:
    """Append one candidate (``outcome`` ``flag`` or ``nothing``; any of ``CANDIDATE_COLUMNS``). Refuses a
    tenant other than the data tenant (``ValueError``) before writing; an unknown column raises ``TypeError``. The
    table's checks refuse a bad shape (a ``nothing`` without a reason, a ``flag`` without its filing and date).
    No de-duplication here: the machine writer decides whether a result repeats the newest candidate.
    """
    require_write_tenant("extraction_candidate", tenant_id)
    unknown = set(columns) - set(CANDIDATE_COLUMNS)
    if unknown:
        raise TypeError(f"not a candidate column: {sorted(unknown)}")
    values: dict[str, Any] = {
        "tenant_id": tenant_id,
        "security_id": security_id,
        "fact_type": fact_type,
        "outcome": outcome,
        **columns,
    }
    if "located_passages" in values:
        values["located_passages"] = Jsonb(list(values["located_passages"] or []))
    if "flags" in values:
        values["flags"] = list(values["flags"] or [])
    query = sql.SQL(
        "INSERT INTO extraction_candidate ({cols}) VALUES ({vals}) RETURNING id"
    ).format(
        cols=sql.SQL(", ").join(sql.Identifier(c) for c in values),
        vals=sql.SQL(", ").join(sql.Placeholder() for _ in values),
    )
    row = _one(conn, query, tuple(values.values()))
    assert row is not None
    return row["id"]


def awaiting(conn: psycopg.Connection, *, limit: int | None = 200) -> list[dict]:
    """Every company-and-type whose NEWEST candidate is a ``flag`` no standing decision closes, newest first, with
    the company's ``ticker`` and ``name`` (``limit`` None = all). Raw: whether it is still awaiting beside a shown value (a newer date)
    is the state's call (``platform_values.state.awaiting``)."""
    closing = sorted(CLOSING_ACTIONS)
    return _all(
        conn,
        "SELECT c.*, m.ticker, m.name FROM ("
        "  SELECT DISTINCT ON (security_id, fact_type) * FROM extraction_candidate WHERE tenant_id = %(t)s"
        "  ORDER BY security_id, fact_type, recorded_at DESC, id DESC"
        ") c JOIN security_master m ON m.id = c.security_id "
        "WHERE c.outcome = 'flag' AND NOT EXISTS ("
        "  SELECT 1 FROM platform_value_decision d WHERE d.candidate_id = c.id AND d.action = ANY(%(closing)s)"
        "  AND NOT EXISTS (SELECT 1 FROM platform_value_decision u WHERE u.revokes = d.id)"
        ") ORDER BY c.recorded_at DESC, c.id DESC LIMIT %(limit)s",
        {"t": DATA_TENANT_ID, "closing": closing, "limit": limit},
    )


# --- decisions (platform_value_decision, 0205) -----------------------------------------------------------------


def decision(conn: psycopg.Connection, decision_id: UUID) -> dict | None:
    """One decision by id (data tenant) with its ``undone`` flag, or None."""
    return _one(
        conn,
        f"{_DECISION_SELECT} WHERE d.id = %s AND d.tenant_id = %s",  # noqa: S608 — a module constant
        (decision_id, DATA_TENANT_ID),
    )


def decisions_for(
    conn: psycopg.Connection, security_id: UUID, fact_type: FactType, *, limit: int = 50
) -> list[dict]:
    """The newest ``limit`` decisions for (company, type), newest first, each with ``undone``."""
    return _all(
        conn,
        f"{_DECISION_SELECT} WHERE d.tenant_id = %s AND d.security_id = %s AND d.fact_type = %s "  # noqa: S608
        "ORDER BY d.recorded_at DESC, d.id DESC LIMIT %s",
        (DATA_TENANT_ID, security_id, fact_type, limit),
    )


def standing_decision_for_candidate(conn: psycopg.Connection, candidate_id: UUID) -> dict | None:
    """The STANDING decision that closes a candidate: the newest ``approve`` / ``correct`` / ``dismiss`` naming it
    that no ``undo`` names, or None (the candidate is open)."""
    return _one(
        conn,
        f"{_DECISION_SELECT} WHERE d.candidate_id = %s AND d.action = ANY(%s) "  # noqa: S608
        "AND NOT EXISTS (SELECT 1 FROM platform_value_decision u WHERE u.revokes = d.id) "
        "ORDER BY d.recorded_at DESC, d.id DESC LIMIT 1",
        (candidate_id, sorted(CLOSING_ACTIONS)),
    )


def standing_decisions_for_candidates(
    conn: psycopg.Connection, candidate_ids: Iterable[UUID]
) -> dict[UUID, dict]:
    """The BATCH twin of ``standing_decision_for_candidate``: ``{candidate_id: its standing decision}`` for every
    candidate a standing decision closes, in ONE query (the same rule and order). An open candidate is absent.
    """
    rows = _all(
        conn,
        "SELECT DISTINCT ON (d.candidate_id) d.*, "
        "EXISTS (SELECT 1 FROM platform_value_decision u WHERE u.revokes = d.id) AS undone "
        "FROM platform_value_decision d WHERE d.candidate_id = ANY(%s) AND d.action = ANY(%s) "
        "AND NOT EXISTS (SELECT 1 FROM platform_value_decision u WHERE u.revokes = d.id) "
        "ORDER BY d.candidate_id, d.recorded_at DESC, d.id DESC",
        (list(dict.fromkeys(candidate_ids)), sorted(CLOSING_ACTIONS)),
    )
    return {r["candidate_id"]: r for r in rows}


def append_decision(
    conn: psycopg.Connection,
    *,
    security_id: UUID,
    fact_type: FactType,
    action: str,
    decided_by: str,
    candidate_id: UUID | None = None,
    fact_id: UUID | None = None,
    revokes: UUID | None = None,
    reason: str | None = None,
    tenant_id: UUID = DATA_TENANT_ID,
) -> UUID:
    """Append one decision (``approve`` / ``correct`` / ``dismiss`` / ``undo``). Refuses a tenant other than the data
    tenant (``ValueError``) before writing. The table refuses a bad shape: its checks (``CheckViolation``), its
    trigger (an undo of an undo, a foreign target or candidate: ``RaiseException``), one undo per decision and one
    decision per fact version (``UniqueViolation``). It checks no rule (already decided, would it be shown): the
    steward actions do, under the company lock."""
    require_write_tenant("platform_value_decision", tenant_id)
    row = _one(
        conn,
        "INSERT INTO platform_value_decision (tenant_id, security_id, fact_type, action, candidate_id, fact_id, "
        "revokes, reason, decided_by) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING id",
        (
            tenant_id,
            security_id,
            fact_type,
            action,
            candidate_id,
            fact_id,
            revokes,
            reason,
            decided_by,
        ),
    )
    assert row is not None
    return row["id"]


# --- pins (steward_pin, 0201) ----------------------------------------------------------------------------------


def latest_pin(conn: psycopg.Connection, security_id: UUID, fact_type: FactType) -> dict | None:
    """The newest pin event for (company, type), or None (never pinned)."""
    return _one(
        conn,
        "SELECT * FROM steward_pin WHERE tenant_id = %s AND security_id = %s AND value_type = %s "
        "ORDER BY recorded_at DESC, id DESC LIMIT 1",
        (DATA_TENANT_ID, security_id, fact_type),
    )


def latest_pins(
    conn: psycopg.Connection, security_ids: Iterable[UUID], fact_type: FactType
) -> dict[UUID, dict]:
    """The BATCH twin of ``latest_pin``: ``{security_id: its newest pin event}`` for every id ever pinned, in ONE
    query (the same order)."""
    rows = _all(
        conn,
        "SELECT DISTINCT ON (security_id) * FROM steward_pin "
        "WHERE tenant_id = %s AND value_type = %s AND security_id = ANY(%s) "
        "ORDER BY security_id, recorded_at DESC, id DESC",
        (DATA_TENANT_ID, fact_type, list(dict.fromkeys(security_ids))),
    )
    return {r["security_id"]: r for r in rows}


def is_pinned(conn: psycopg.Connection, security_id: UUID, fact_type: FactType) -> bool:
    """Is (company, type) pinned now: its newest pin event is ``pin``."""
    pin = latest_pin(conn, security_id, fact_type)
    return pin is not None and pin["action"] == "pin"


def append_pin(
    conn: psycopg.Connection,
    *,
    security_id: UUID,
    fact_type: FactType,
    action: str,
    decided_by: str,
    note: str | None = None,
    tenant_id: UUID = DATA_TENANT_ID,
) -> UUID:
    """Append one pin event (``pin`` / ``unpin``). Refuses a tenant other than the data tenant (``ValueError``)."""
    require_write_tenant("steward_pin", tenant_id)
    row = _one(
        conn,
        "INSERT INTO steward_pin (tenant_id, security_id, value_type, action, note, decided_by) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING id",
        (tenant_id, security_id, fact_type, action, note, decided_by),
    )
    assert row is not None
    return row["id"]


# --- the stored versions of the two value tables ---------------------------------------------------------------


def versions(conn: psycopg.Connection, security_id: UUID, fact_type: FactType) -> list[dict]:
    """EVERY stored version of the company's value in the data tenant (all filings, superseded and set-aside ones
    too), oldest first, each with ``set_aside`` (every read skips it: ``db.set_aside``)."""
    table = TABLE_OF[fact_type]
    query = sql.SQL(
        "SELECT f.*, f.id IN ({aside}) AS set_aside FROM {table} f "
        "WHERE f.tenant_id = %s AND f.security_id = %s ORDER BY f.recorded_at, f.id"
    ).format(
        aside=sql.SQL(
            active_ids_sql(table)
        ),  # a module constant (db.set_aside), never caller input
        table=sql.Identifier(table),
    )
    return _all(conn, query, (DATA_TENANT_ID, security_id))


def stored_source_refs(
    conn: psycopg.Connection, security_id: UUID, fact_type: FactType
) -> set[str]:
    """The filing of EVERY stored version (set-aside ones included): the machine's "already on file" test, so a
    filing whose approve the steward undid is never re-applied (a writer sees every version)."""
    table = TABLE_OF[fact_type]
    query = sql.SQL(
        "SELECT DISTINCT source_ref FROM {table} WHERE tenant_id = %s AND security_id = %s"
    ).format(table=sql.Identifier(table))
    return {r["source_ref"] for r in _all(conn, query, (DATA_TENANT_ID, security_id))}


def companies(conn: psycopg.Connection, security_ids: Iterable[UUID]) -> dict[UUID, dict]:
    """``{security_id: {ticker, name}}`` from the master (data tenant) for the ids that exist."""
    ids: Sequence[UUID] = list(dict.fromkeys(security_ids))
    rows = _all(
        conn,
        "SELECT id, ticker, name FROM security_master WHERE id = ANY(%s) AND tenant_id = %s",
        (ids, DATA_TENANT_ID),
    )
    return {r["id"]: r for r in rows}


__all__ = [
    "CANDIDATE_COLUMNS",
    "candidate",
    "latest_candidate",
    "latest_candidates",
    "candidates_for",
    "append_candidate",
    "awaiting",
    "decision",
    "decisions_for",
    "standing_decision_for_candidate",
    "standing_decisions_for_candidates",
    "append_decision",
    "latest_pin",
    "latest_pins",
    "is_pinned",
    "append_pin",
    "versions",
    "stored_source_refs",
    "companies",
]
