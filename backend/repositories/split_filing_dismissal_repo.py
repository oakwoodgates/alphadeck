"""The steward's DISMISS of a split filing the nightly could not place (migration 0218, framework completion F1-2):
the append-only log behind the unplaced split filings (``pipeline.split_unplaced``) on the Admin Splits panel.
The shape and the verbs are ``split_jump_repo``'s, keyed by the filing ``(security_id, accession)`` instead of the
jump's session.

THE VERBS, each with its visible inverse (WB#1):

- ``dismiss``: the steward says the filing is not a split to list. It leaves the default list and is listed as
  dismissed (WB#2: hidden, never gone). Refused while a dismissal of that filing stands.
- ``undo``: the Undo of a standing dismissal. It appends an ``undo`` row naming it; the filing is back on the list.

DISPLAY ONLY. Nothing here is a call input: the split detector, the split list and the split-night hold never read
this table, and no editions engine source names it. No ``mark_changed``: no dashboard shows unplaced filings.
The table is DATA scope (``domain.scopes``): every write names the data tenant (``require_write_tenant``) and every
read names one tenant. Every write is append-only, names its author (``operator:*``) and runs under a per-filing
advisory lock (two clicks serialize). The caller owns the transaction.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from domain.scopes import require_write_tenant

TABLE = "split_filing_dismissal"
AUTHOR_DISMISS = "operator:dismiss"
AUTHOR_UNDO = "operator:undo"
ACTION_DISMISS = "dismiss"
ACTION_UNDO = "undo"

# the migration's CHECK: an SEC accession number, ``0001628280-26-066700``
_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")


class FilingDismissalConflict(Exception):
    """A dismissal the current state does not allow (one already stands, or the undo names one already undone).
    The API maps it to **409**."""


FilingKey = tuple[UUID, str]


def _lock(conn: psycopg.Connection, tenant_id: UUID, security_id: UUID, accession: str) -> None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(hashtext(%s))",
            (f"{TABLE}:{tenant_id}:{security_id}:{accession}",),
        )


def _rows(
    conn: psycopg.Connection, tenant_id: UUID, security_id: UUID | None = None
) -> list[dict[str, Any]]:
    where = "" if security_id is None else " AND security_id = %(sid)s"
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM split_filing_dismissal WHERE tenant_id = %(t)s"
            + where
            + " ORDER BY recorded_at, id",  # noqa: S608 — a fixed clause
            {"t": tenant_id, "sid": security_id},
        )
        return cur.fetchall()


def _check(reason: str, author: str) -> None:
    if not reason.strip():
        raise ValueError("a split-filing dismissal or its undo needs a reason")
    if not author.startswith("operator:"):
        raise ValueError("only the operator dismisses a split filing or undoes a dismissal")


def standing(rows: list[Mapping[str, Any]]) -> dict[FilingKey, Mapping[str, Any]]:
    """PURE: ``(security_id, accession) -> the standing dismissal`` (a ``dismiss`` no ``undo`` names). Should two
    ever stand for one filing, the newest (``recorded_at``, then ``id``) wins, whatever order ``rows`` is in.
    """
    undone = {str(r["undoes"]) for r in rows if r["action"] == ACTION_UNDO and r.get("undoes")}
    out: dict[FilingKey, Mapping[str, Any]] = {}
    for r in sorted(rows, key=lambda r: (r["recorded_at"], str(r["id"]))):
        if r["action"] == ACTION_DISMISS and str(r["id"]) not in undone:
            out[(r["security_id"], r["accession"])] = r
    return out


def standing_dismissals(
    conn: psycopg.Connection, *, tenant_id: UUID
) -> dict[FilingKey, Mapping[str, Any]]:
    """Every standing dismissal of the tenant. Read-only."""
    return standing(_rows(conn, tenant_id))


def dismiss(
    conn: psycopg.Connection,
    *,
    tenant_id: UUID,
    security_id: UUID,
    accession: str,
    reason: str,
    detail: Mapping[str, Any] | None = None,
    author: str = AUTHOR_DISMISS,
) -> dict[str, Any]:
    """Append the steward's dismissal of one split filing ("not a split to list"). ``FilingDismissalConflict``
    while a dismissal of it already stands (undo it first); ``ValueError`` on a blank reason, a non-operator
    author, a malformed accession or a tenant other than the data tenant.
    """
    require_write_tenant(TABLE, tenant_id)
    _check(reason, author)
    if not _ACCESSION.fullmatch(accession):
        raise ValueError(f"not an SEC accession number: {accession!r}")
    _lock(conn, tenant_id, security_id, accession)
    if (security_id, accession) in standing(_rows(conn, tenant_id, security_id)):
        raise FilingDismissalConflict(f"filing {accession} is already dismissed: undo that first")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO split_filing_dismissal (tenant_id, security_id, accession, action, reason, author, "
            "detail) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                tenant_id,
                security_id,
                accession,
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
    """Undo one STANDING dismissal by appending an ``undo`` row naming it: the filing is back on the list.
    ``LookupError`` for an unknown dismissal (or another tenant's); ``FilingDismissalConflict`` when it was already
    undone; ``ValueError`` as ``dismiss``.
    """
    require_write_tenant(TABLE, tenant_id)
    _check(reason, author)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM split_filing_dismissal WHERE id = %s AND tenant_id = %s",
            (dismissal_id, tenant_id),
        )
        target = cur.fetchone()
    if target is None or target["action"] != ACTION_DISMISS:
        raise LookupError(f"no split-filing dismissal with id {dismissal_id}")
    _lock(conn, tenant_id, target["security_id"], target["accession"])
    rows = _rows(conn, tenant_id, target["security_id"])
    if any(r["action"] == ACTION_UNDO and str(r["undoes"]) == str(dismissal_id) for r in rows):
        raise FilingDismissalConflict(f"dismissal {dismissal_id} was already undone")
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO split_filing_dismissal (tenant_id, security_id, accession, action, undoes, reason, "
            "author) VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                tenant_id,
                target["security_id"],
                target["accession"],
                ACTION_UNDO,
                dismissal_id,
                reason.strip(),
                author,
            ),
        )
        return cur.fetchone()
