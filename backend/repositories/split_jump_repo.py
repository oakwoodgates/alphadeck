"""The operator's DISMISS of an "is this real?" card (migration 0068, 2026-10-24) — the append-only log behind the
Admin Splits panel's released, unlisted split-shaped jumps (``pipeline.split_jumps``).

THE VERBS, each with its visible inverse (WB#1):

- ``dismiss`` — the operator says the jump was a REAL MOVE: the card leaves the default list and is listed under
  the panel's "dismissed" filter (WB#2 — hidden, never gone). Refused while a dismissal of that jump stands.
- ``undo`` — the Undo of a standing dismissal: appends an ``undo`` row naming it; the card is back on the list.

DISPLAY ONLY. Nothing here is a call input: the split-night hold never reads this table (``signals/split_hold.py``)
— a dismissal neither lists nor rejects a split, and changes no card. Every write is append-only, names its author
(``operator:*``), and runs under a per-jump advisory lock (two clicks serialize). The caller owns the transaction.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

AUTHOR_DISMISS = "operator:dismiss"
AUTHOR_UNDO = "operator:undo"
ACTION_DISMISS = "dismiss"
ACTION_UNDO = "undo"


class JumpDismissalConflict(Exception):
    """A dismissal the current state does not allow (one already stands, or the undo names no standing one). The
    API maps it to **409**."""


JumpKey = tuple[UUID, date]


def _lock(conn: psycopg.Connection, tenant_id: UUID, security_id: UUID, step_date: date) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"split_jump_dismissal:{tenant_id}:{security_id}:{step_date.isoformat()}",),
        )


def _rows(
    conn: psycopg.Connection, tenant_id: UUID, security_id: UUID | None = None
) -> list[dict[str, Any]]:
    where = "" if security_id is None else " AND security_id = %(sid)s"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM split_jump_dismissal WHERE tenant_id = %(t)s"
            + where
            + " ORDER BY recorded_at, id",  # noqa: S608 — a fixed clause
            {"t": tenant_id, "sid": security_id},
        )
        return cur.fetchall()


def standing(rows: list[Mapping[str, Any]]) -> dict[JumpKey, Mapping[str, Any]]:
    """PURE — ``(security_id, step_date) -> the standing dismissal`` (a ``dismiss`` no ``undo`` names; the newest
    wins should two ever stand)."""
    undone = {str(r["undoes"]) for r in rows if r["action"] == ACTION_UNDO and r.get("undoes")}
    out: dict[JumpKey, Mapping[str, Any]] = {}
    for r in rows:
        if r["action"] == ACTION_DISMISS and str(r["id"]) not in undone:
            out[(r["security_id"], r["step_date"])] = r
    return out


def standing_dismissals(
    conn: psycopg.Connection, *, tenant_id: UUID
) -> dict[JumpKey, Mapping[str, Any]]:
    """Every standing dismissal of the tenant. Read-only."""
    return standing(_rows(conn, tenant_id))


def dismiss(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    step_date: date,
    reason: str,
    detail: Mapping[str, Any] | None = None,
    author: str = AUTHOR_DISMISS,
) -> dict[str, Any]:
    """Append the operator's dismissal of one jump ("this was a real move"). ``JumpDismissalConflict`` while a
    dismissal of it already stands (undo it first); ``ValueError`` on a blank reason / a non-operator author.
    """
    if not reason.strip():
        raise ValueError("a dismissal needs a reason")
    if not author.startswith("operator:"):
        raise ValueError("only the operator dismisses a jump")
    _lock(conn, tenant_id, security_id, step_date)
    if (security_id, step_date) in standing(_rows(conn, tenant_id, security_id)):
        raise JumpDismissalConflict(
            f"the {step_date.isoformat()} jump is already dismissed — undo that first"
        )
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO split_jump_dismissal (tenant_id, security_id, step_date, action, reason, author, "
            "detail) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                tenant_id,
                security_id,
                step_date,
                ACTION_DISMISS,
                reason.strip(),
                author,
                Json(dict(detail or {})),
            ),
        )
        return cur.fetchone()


def undo(
    conn: psycopg.Connection,
    dismissal_id: UUID,
    *,
    tenant_id: UUID,
    reason: str,
    author: str = AUTHOR_UNDO,
) -> dict[str, Any]:
    """Undo one STANDING dismissal by appending an ``undo`` row naming it — the card is back on the list.
    ``LookupError`` for an unknown dismissal; ``JumpDismissalConflict`` when it was already undone.
    """
    if not reason.strip():
        raise ValueError("an undo needs a reason")
    if not author.startswith("operator:"):
        raise ValueError("only the operator undoes a dismissal")
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM split_jump_dismissal WHERE id = %s AND tenant_id = %s",
            (dismissal_id, tenant_id),
        )
        target = cur.fetchone()
    if target is None or target["action"] != ACTION_DISMISS:
        raise LookupError(f"no dismissal with id {dismissal_id}")
    _lock(conn, tenant_id, target["security_id"], target["step_date"])
    rows = _rows(conn, tenant_id, target["security_id"])
    if any(r["action"] == ACTION_UNDO and str(r["undoes"]) == str(dismissal_id) for r in rows):
        raise JumpDismissalConflict(f"dismissal {dismissal_id} was already undone")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO split_jump_dismissal (tenant_id, security_id, step_date, action, undoes, reason, "
            "author) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                tenant_id,
                target["security_id"],
                target["step_date"],
                ACTION_UNDO,
                dismissal_id,
                reason.strip(),
                author,
            ),
        )
        return cur.fetchone()
