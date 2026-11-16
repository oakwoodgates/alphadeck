"""The ONE repository of users' flags on platform values (``value_flag``, migrations 0202 and 0210; V2 W5b).

A flag is private to the user who raised it and the steward. Every user-side read here filters by the user's id IN
SQL (``user_id = %s``), never after the fetch; ``tests/app/test_value_flag_guard.py`` fails when another product
file queries the table, or when a user-side read here loses its filter. The steward-side reads (``for_company``,
``open_counts``) are for steward routes only.

No commit here: the caller (``platform_values.flags``, then the route) owns the transaction. Rows are dicts. The
closed-once trigger (0202) refuses any update of a closed row, so every close below updates only an open one.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

import psycopg

from domain.scopes import DATA_TENANT_ID

# The stored columns a flag carries (``f`` = value_flag), plus the company's ticker and name from the master.
_COLS = (
    "f.id, f.user_id, f.security_id, f.value_type, f.value_ref, f.seen_fact_id, f.seen_candidate_id, f.note, "
    "f.opened_at, f.closed_at, f.closed_by, f.outcome, f.outcome_note, f.fixed_decision_id, f.reopens"
)
_WITH_COMPANY = (
    f"SELECT {_COLS}, s.ticker, s.name FROM value_flag f "
    "LEFT JOIN security_master s ON s.id = f.security_id AND s.tenant_id = %s "
)
# The caller's own list: open first (newest opened first), then closed (newest closed first); id breaks a tie.
_MINE_ORDER = "ORDER BY (f.closed_at IS NOT NULL), COALESCE(f.closed_at, f.opened_at) DESC, f.opened_at DESC, f.id DESC"


def _one(conn: psycopg.Connection, query: str, params: Any) -> dict | None:
    with conn.cursor() as cur:
        cur.execute(query, params)
        row = cur.fetchone()
    return None if row is None else dict(row)


def _all(conn: psycopg.Connection, query: str, params: Any) -> list[dict]:
    with conn.cursor() as cur:
        cur.execute(query, params)
        return [dict(r) for r in cur.fetchall()]


# --- the user's side (every read names user_id = %s) ---------------------------------------------------------------


def open_flag(
    conn: psycopg.Connection, user_id: UUID, security_id: UUID, value_type: str
) -> dict | None:
    """The user's open flag on (company, value), or None. Shares and cash carry ``value_ref`` ''."""
    return _one(
        conn,
        f"SELECT {_COLS} FROM value_flag f WHERE f.user_id = %s AND f.security_id = %s AND f.value_type = %s "
        "AND f.value_ref = '' AND f.closed_at IS NULL",
        (user_id, security_id, value_type),
    )


def for_user(conn: psycopg.Connection, flag_id: UUID, user_id: UUID) -> dict | None:
    """One flag by id, ONLY when ``user_id`` raised it (else None: another user's flag reads as no flag)."""
    return _one(
        conn,
        _WITH_COMPANY + "WHERE f.id = %s AND f.user_id = %s",
        (DATA_TENANT_ID, flag_id, user_id),
    )


def mine(conn: psycopg.Connection, user_id: UUID, *, limit: int, offset: int) -> list[dict]:
    """The user's own flags, open first then the newest closed, one page."""
    return _all(
        conn,
        _WITH_COMPANY + "WHERE f.user_id = %s " + _MINE_ORDER + " LIMIT %s OFFSET %s",
        (DATA_TENANT_ID, user_id, limit, offset),
    )


def count_mine(conn: psycopg.Connection, user_id: UUID) -> int:
    """How many flags the user has, open and closed."""
    row = _one(conn, "SELECT count(*) AS n FROM value_flag WHERE user_id = %s", (user_id,))
    return 0 if row is None else int(row["n"])


def withdraw(conn: psycopg.Connection, flag_id: UUID, user_id: UUID) -> dict | None:
    """Close the user's OWN open flag as ``withdrawn`` (``closed_by`` the literal ``user``); None when no open flag
    of theirs has that id (a closed one, another user's, or none). A concurrent close waits on the row lock and then
    finds it closed."""
    return _one(
        conn,
        "UPDATE value_flag SET closed_at = now(), closed_by = 'user', outcome = 'withdrawn' "
        "WHERE id = %s AND user_id = %s AND closed_at IS NULL RETURNING *",
        (flag_id, user_id),
    )


def insert(
    conn: psycopg.Connection,
    *,
    user_id: UUID,
    security_id: UUID,
    value_type: str,
    note: str | None,
    seen_fact_id: UUID | None,
    seen_candidate_id: UUID | None,
    reopens: UUID | None = None,
) -> dict:
    """Append one OPEN flag. The partial unique index refuses a second open flag on the same (user, value)
    (``UniqueViolation``); 0210's trigger refuses a reopen that changes the identity."""
    row = _one(
        conn,
        "INSERT INTO value_flag (user_id, security_id, value_type, note, seen_fact_id, seen_candidate_id, "
        "reopens) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
        (user_id, security_id, value_type, note, seen_fact_id, seen_candidate_id, reopens),
    )
    assert row is not None
    return row


# --- the steward's side (steward routes only) ----------------------------------------------------------------------


def lock_row(conn: psycopg.Connection, flag_id: UUID) -> dict | None:
    """One flag by id, its row locked for the rest of the transaction (``FOR UPDATE``), or None."""
    return _one(conn, "SELECT * FROM value_flag WHERE id = %s FOR UPDATE", (flag_id,))


def close(
    conn: psycopg.Connection,
    flag_id: UUID,
    *,
    outcome: str,
    closed_by: str,
    outcome_note: str | None,
    fixed_decision_id: UUID | None,
) -> dict | None:
    """Close an OPEN flag as ``fixed`` or ``kept``; None when it is no longer open."""
    return _one(
        conn,
        "UPDATE value_flag SET closed_at = now(), closed_by = %s, outcome = %s, outcome_note = %s, "
        "fixed_decision_id = %s WHERE id = %s AND closed_at IS NULL RETURNING *",
        (closed_by, outcome, outcome_note, fixed_decision_id, flag_id),
    )


def is_reopened(conn: psycopg.Connection, flag_id: UUID) -> bool:
    row = _one(conn, "SELECT EXISTS (SELECT 1 FROM value_flag WHERE reopens = %s) AS r", (flag_id,))
    return bool(row and row["r"])


def any_open(
    conn: psycopg.Connection, user_id: UUID, security_id: UUID, value_type: str, value_ref: str
) -> bool:
    row = _one(
        conn,
        "SELECT EXISTS (SELECT 1 FROM value_flag WHERE user_id = %s AND security_id = %s AND value_type = %s "
        "AND value_ref = %s AND closed_at IS NULL) AS o",
        (user_id, security_id, value_type, value_ref),
    )
    return bool(row and row["o"])


_STEWARD_COLS = f"{_COLS}, u.email AS user_email"
_STEWARD_FROM = "FROM value_flag f LEFT JOIN app_user u ON u.id = f.user_id "


def company_open(conn: psycopg.Connection, security_id: UUID) -> list[dict]:
    """A company's open flags, oldest first, each with the flagging user's email (decision B4 (a))."""
    return _all(
        conn,
        f"SELECT {_STEWARD_COLS} {_STEWARD_FROM}"
        "WHERE f.security_id = %s AND f.closed_at IS NULL ORDER BY f.opened_at, f.id",
        (security_id,),
    )


def company_closed(
    conn: psycopg.Connection, security_id: UUID, *, limit: int, offset: int
) -> list[dict]:
    """A company's closed flags, newest closed first, one page, each with the user's email."""
    return _all(
        conn,
        f"SELECT {_STEWARD_COLS} {_STEWARD_FROM}"
        "WHERE f.security_id = %s AND f.closed_at IS NOT NULL "
        "ORDER BY f.closed_at DESC, f.id DESC LIMIT %s OFFSET %s",
        (security_id, limit, offset),
    )


def company_closed_count(conn: psycopg.Connection, security_id: UUID) -> int:
    row = _one(
        conn,
        "SELECT count(*) AS n FROM value_flag WHERE security_id = %s AND closed_at IS NOT NULL",
        (security_id,),
    )
    return 0 if row is None else int(row["n"])


def open_counts(conn: psycopg.Connection, security_ids: Iterable[UUID] | None = None) -> list[dict]:
    """One row per company with an open flag (``security_id``, ``n``, ``shares``, ``cash_burn``, ``oldest``), for
    every company (None) or the given ones; one statement."""
    where = "closed_at IS NULL"
    params: tuple = ()
    if security_ids is not None:
        where += " AND security_id = ANY(%s)"
        params = (list(dict.fromkeys(security_ids)),)
    return _all(
        conn,
        "SELECT security_id, count(*) AS n, "
        "count(*) FILTER (WHERE value_type = 'shares_outstanding') AS shares, "
        "count(*) FILTER (WHERE value_type = 'cash_burn') AS cash_burn, "
        f"min(opened_at) AS oldest FROM value_flag WHERE {where} GROUP BY security_id",
        params,
    )


__all__ = [
    "any_open",
    "close",
    "company_closed",
    "company_closed_count",
    "company_open",
    "count_mine",
    "for_user",
    "insert",
    "is_reopened",
    "lock_row",
    "mine",
    "open_counts",
    "open_flag",
    "withdraw",
]
