"""The security CIK change log (migration 0049, T7) — the operator's ratified re-point of a basket name's
``security_master.cik`` to its SUCCESSOR issuer, with an EXACT inverse (WB#1). The T2 price-feed repair log
(``price_feed_repo.py``) is the template; this is its CIK twin.

One append-only table, one pair of verbs:

- ``adopt`` — re-point ``cik`` IN PLACE (``master.set_cik`` — the row id stays, so the tape, every fact, the
  basket links and the sign-off stay attached) and refresh the CIK-dependent identity from the successor's
  submissions through the EXISTING derivation: ``master.enrich`` with the ``parse_identity`` result the
  nightly detector read (``pipeline/successor_cik.py``) — the ``enrich_identity`` path, never a fork — plus
  the successor's entity name (``master.set_name``). No network: the identity arrives with the call.
- ``undo`` — put every touched column back VERBATIM (``master.restore_cik_identity``), appending an ``undo``
  row that names the adopt it reverses.

EXACTNESS RULES (the T2 rules, kept):

1. Every write locks the master row ``FOR UPDATE`` first, so two clicks on one name serialize.
2. An undo applies only to the security's STANDING change — the newest adopt no undo reverses — and only
   while the master still holds exactly that change's NEW values (all of ``CIK_IDENTITY_COLUMNS``). If
   anything moved the master since (an ``enrich_identity`` run, a populate name refresh, a later change), the
   undo REFUSES (``RepairConflict``) rather than clobber a value it did not write.
3. The restore writes the stored prior values VERBATIM — so the touched columns come back byte-for-byte.

An adopt also refuses (``RepairConflict``) when the master no longer holds the CIK the detector saw (it moved
since the pass), when it already holds the successor, and when ANOTHER master row of the tenant already
carries the successor CIK — re-pointing would then leave two rows on one CIK (two "primary" rows for
``ids_for_ciks``); that is a merge of rows, which this verb does not do.

Value-free by design: it stores and returns decisions; it judges nothing (which name is a successor case is
the detector's; whether to re-point is the operator's). The caller owns the transaction.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json
from pydantic import BaseModel

from domain.security import SecurityIdentity
from repositories.price_feed_repo import RepairConflict
from securities import master

# THE STANDING PREDICATE, stated once: adopt `c` still stands iff no undo row reverses it.
NOT_UNDONE_SQL = "NOT EXISTS (SELECT 1 FROM security_cik_change u WHERE u.undoes = c.id)"

__all__ = [
    "CikChange",
    "RepairConflict",
    "StandingCikChange",
    "adopt",
    "standing_change_for",
    "standing_changes",
    "undo",
]


class CikChange(BaseModel):
    """One ``security_cik_change`` row, as stored. ``prior_values`` / ``new_values`` are the JSON snapshots of every
    ``CIK_IDENTITY_COLUMNS`` value before / after the write (``enriched_at`` as an ISO instant)."""

    id: UUID
    tenant_id: UUID
    security_id: UUID
    action: str
    prior_cik: str | None = None
    new_cik: str | None = None
    prior_values: dict[str, Any] = {}
    new_values: dict[str, Any] = {}
    basis: str
    detail: dict[str, Any] = {}
    undoes: UUID | None = None
    created_at: datetime


class StandingCikChange(CikChange):
    """A standing adopt joined to the master's CURRENT values and ticker — what the Admin panel renders
    (``undoable`` = the master still holds this change's new values, so an undo would be exact)."""

    ticker: str | None = None
    current: dict[str, Any] = {}

    @property
    def undoable(self) -> bool:
        return _holds(self.new_values, self.current)

    @property
    def current_cik(self) -> str | None:
        return self.current.get("cik")


def _encode(row: dict) -> dict[str, Any]:
    """A master snapshot (the ``CIK_IDENTITY_COLUMNS`` values) in its stored JSON form: every value as
    read, ``enriched_at`` as an ISO-8601 instant (microseconds + offset — an exact round trip)."""
    out: dict[str, Any] = {}
    for c in master.CIK_IDENTITY_COLUMNS:
        v = row.get(c)
        out[c] = v.isoformat() if isinstance(v, datetime) else v
    return out


def _decode(snapshot: dict) -> dict[str, Any]:
    """The inverse of ``_encode``: the values ``master.restore_cik_identity`` writes (``enriched_at`` back
    to an aware datetime — the same instant, whatever offset it was rendered in)."""
    out = {c: snapshot.get(c) for c in master.CIK_IDENTITY_COLUMNS}
    if isinstance(out["enriched_at"], str):
        out["enriched_at"] = datetime.fromisoformat(out["enriched_at"])
    return out


def _holds(snapshot: dict, row: dict) -> bool:
    """Does the master ``row`` still hold exactly ``snapshot``'s values (every touched column; instants
    compared as instants)?"""
    want = _decode(snapshot)
    got = {c: row.get(c) for c in master.CIK_IDENTITY_COLUMNS}
    if isinstance(got["enriched_at"], str):
        got["enriched_at"] = datetime.fromisoformat(got["enriched_at"])
    return want == got


def _lock(conn: psycopg.Connection, security_id: UUID) -> dict:
    """Lock one master row (any tenant — the id is the key; the row's own tenant rides back) for the
    caller's transaction; ``LookupError`` if absent."""
    with conn.cursor() as cur:
        cur.execute("SELECT tenant_id FROM security_master WHERE id = %s", (security_id,))
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"security not found: {security_id}")
    locked = master.cik_identity_of(conn, security_id, tenant_id=row["tenant_id"], lock=True)
    if locked is None:  # pragma: no cover — the row vanished between the two reads
        raise LookupError(f"security not found: {security_id}")
    locked["tenant_id"] = row["tenant_id"]
    return locked


def adopt(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    new_cik: str,
    expected_cik: str,
    identity: SecurityIdentity,
    entity_name: str | None,
    basis: str,
    detail: dict[str, Any] | None = None,
) -> CikChange:
    """Re-point one name to its successor CIK and refresh its CIK-dependent identity (module docstring),
    recording the master's prior + new values of every touched column (read back after the write, so the
    row records what is actually stored). ``expected_cik`` is the CIK the detector saw on the master.
    ``RepairConflict`` when the master moved since (its CIK is no longer ``expected_cik``), already holds
    ``new_cik``, or another row of the tenant carries ``new_cik``; ``LookupError`` for an unknown security;
    ``ValueError`` for a malformed CIK or a blank basis. The caller owns the transaction."""
    if not basis.strip():
        raise ValueError("a CIK change needs a basis")
    before = _lock(conn, security_id)
    tenant = before["tenant_id"]
    who = before["ticker"] or security_id
    if before["cik"] == new_cik:
        raise RepairConflict(f"{who} already files under CIK {new_cik}")
    if before["cik"] != expected_cik:
        raise RepairConflict(
            f"{who}'s master changed since the pass (now CIK {before['cik'] or 'unset'}, the pass saw "
            f"{expected_cik}) — nothing adopted; the next pass re-judges it"
        )
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, ticker FROM security_master WHERE tenant_id = %s AND cik = %s AND id <> %s",
            (tenant, new_cik, security_id),
        )
        others = cur.fetchall()
    if others:
        names = ", ".join(str(r["ticker"] or r["id"]) for r in others)
        raise RepairConflict(
            f"the master already holds another row under CIK {new_cik} ({names}) — re-pointing {who} "
            "would leave two rows on one CIK; repair it by hand"
        )
    master.set_cik(conn, security_id, new_cik, tenant_id=tenant)
    master.enrich(conn, security_id, identity, source=f"submissions:CIK{new_cik}", tenant_id=tenant)
    name = (entity_name or "").strip()
    if name and name != (before["name"] or ""):
        master.set_name(conn, security_id, name, tenant_id=tenant)
    after = master.cik_identity_of(conn, security_id, tenant_id=tenant)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO security_cik_change
                   (tenant_id, security_id, action, prior_cik, new_cik, prior_values, new_values, basis, detail)
               VALUES (%s, %s, 'adopt', %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (
                tenant,
                security_id,
                before["cik"],
                after["cik"],  # type: ignore[index]
                Json(_encode(before)),
                Json(_encode(after)),  # type: ignore[arg-type]
                basis,
                Json(detail or {}),
            ),
        )
        return CikChange(**cur.fetchone())


def standing_change_for(conn: psycopg.Connection, security_id: UUID) -> CikChange | None:
    """The security's STANDING change — its newest adopt no undo reverses — or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT c.* FROM security_cik_change c
                 WHERE c.security_id = %s AND c.action = 'adopt' AND {NOT_UNDONE_SQL}
                 ORDER BY c.created_at DESC, c.id DESC LIMIT 1""",  # noqa: S608 — module constant
            (security_id,),
        )
        row = cur.fetchone()
    return CikChange(**row) if row else None


def undo(conn: psycopg.Connection, change_id: UUID, *, basis: str) -> CikChange:
    """Reverse one adopt EXACTLY (the module docstring's rules): write every touched column's prior value
    back verbatim and append an ``undo`` row naming the change. ``LookupError`` for an unknown change or an
    undo row; ``RepairConflict`` when it is already undone, a later change stands, or the master no longer
    holds its values. The caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM security_cik_change WHERE id = %s", (change_id,))
        row = cur.fetchone()
    if row is None or row["action"] != "adopt":
        raise LookupError(f"no CIK adopt with id {change_id}")
    change = CikChange(**row)
    now = _lock(conn, change.security_id)
    who = now["ticker"] or change.security_id
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM security_cik_change WHERE undoes = %s", (change_id,))
        if cur.fetchone() is not None:
            raise RepairConflict(f"this CIK change to {who} was already undone")
    standing = standing_change_for(conn, change.security_id)
    if standing is None or standing.id != change.id:
        raise RepairConflict(f"a later CIK change stands for {who} — undo that one first")
    if not _holds(change.new_values, now):
        raise RepairConflict(
            f"{who}'s master changed since this re-point (now CIK {now['cik'] or 'unset'}) — nothing "
            "undone; repair it by hand"
        )
    master.restore_cik_identity(
        conn, change.security_id, _decode(change.prior_values), tenant_id=change.tenant_id
    )
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO security_cik_change
                   (tenant_id, security_id, action, prior_cik, new_cik, prior_values, new_values, basis, detail, undoes)
               VALUES (%s, %s, 'undo', %s, %s, %s, %s, %s, '{}'::jsonb, %s)
               RETURNING *""",
            (
                change.tenant_id,
                change.security_id,
                change.new_cik,
                change.prior_cik,
                Json(change.new_values),
                Json(change.prior_values),
                basis,
                change.id,
            ),
        )
        return CikChange(**cur.fetchone())


def standing_changes(conn: psycopg.Connection) -> list[StandingCikChange]:
    """Every security's STANDING adopt (newest, no undo reverses it), joined to the master's current values
    — the Admin panel's applied list, each with its Undo (the inverse stays visible after the name leaves
    the detector's inventory, WB#1). Ticker order. Read-only."""
    cols = ", ".join(f"sm.{c} AS cur_{c}" for c in master.CIK_IDENTITY_COLUMNS)
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT * FROM (
                    SELECT DISTINCT ON (c.security_id) c.*, sm.ticker, {cols}
                      FROM security_cik_change c
                      JOIN security_master sm ON sm.id = c.security_id AND sm.tenant_id = c.tenant_id
                     WHERE c.action = 'adopt' AND {NOT_UNDONE_SQL}
                     ORDER BY c.security_id, c.created_at DESC, c.id DESC
                ) s ORDER BY s.ticker NULLS LAST, s.security_id""",  # noqa: S608 — module constants
        )
        rows = cur.fetchall()
    out: list[StandingCikChange] = []
    for r in rows:
        current = {c: r.pop(f"cur_{c}") for c in master.CIK_IDENTITY_COLUMNS}
        out.append(StandingCikChange(**r, current=_encode(current)))
    return out
