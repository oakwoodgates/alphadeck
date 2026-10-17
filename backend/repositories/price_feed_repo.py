"""The price-feed REPAIR log (migration 0046, T2 + Q8) — the operator's ratified repairs of a name's price
feed, each with an EXACT inverse (WB#1).

Two append-only tables, two pairs of verbs:

- ``price_symbol_change`` — ``apply_change`` (an Adopt of a verified successor, or a Set of a symbol the
  operator typed) and ``undo_change``. ``security_master.price_symbol`` is UPDATE-in-place identity with no
  history of its own (0032), so every change row records the master's PRIOR and NEW values of each column
  it touched — ``price_symbol``, ``price_symbol_basis``, and (an Adopt that refreshed it) ``name`` — and an
  undo puts exactly the prior values back, appending an ``undo`` row that names the change it reversed.
- ``price_feed_ack`` — ``acknowledge`` ("keep this unpriceable name, unpriced") and ``revoke``.

EXACTNESS RULES (why an undo can be trusted to restore what was there):

1. Every write locks the master row ``FOR UPDATE`` first, so two clicks on one name serialize.
2. An undo applies only to the security's STANDING change — the newest adopt/set that no undo reverses —
   and only while the master still holds exactly that change's NEW values. If anything else moved the
   master since (the ``--adopt`` CLI, the finalize self-heal, a later change), the undo REFUSES
   (``RepairConflict``) rather than clobber a value it did not write.
3. The restore writes the stored prior values VERBATIM (``master.restore_price_symbol`` / ``set_name``) —
   no ticker-coercion, no re-derivation — so ``(price_symbol, price_symbol_basis, name)`` come back
   byte-for-byte.

Value-free by design (the repository discipline): it stores and returns decisions; it judges nothing. Which
names are renamed / unpriceable is the tape monitor's (``pipeline/tape_health.py``); whether a symbol is a
good successor is the probe's (``pipeline/rename_probe.py``) and the operator's. The caller owns the
transaction (commit after a successful write; roll back on an exception).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json
from pydantic import BaseModel

from securities import master

ACTIONS_CHANGE = ("adopt", "set")  # 'undo' is written only by `undo_change`

# THE STANDING PREDICATE, stated once: change `c` still stands iff no undo row reverses it.
NOT_UNDONE_SQL = "NOT EXISTS (SELECT 1 FROM price_symbol_change u WHERE u.undoes = c.id)"
# THE ACTIVE PREDICATE for an acknowledgment: no revoke row references it.
ACK_ACTIVE_SQL = "NOT EXISTS (SELECT 1 FROM price_feed_ack r WHERE r.revokes = a.id)"


class RepairConflict(Exception):
    """A repair the current state does not allow (nothing to change, a later change stands, the master
    moved since the change). The API maps it to **409**; the message is operator-facing."""


class PriceSymbolChange(BaseModel):
    """One ``price_symbol_change`` row, as stored."""

    id: UUID
    tenant_id: UUID
    security_id: UUID
    action: str
    prior_symbol: str | None = None
    prior_basis: str | None = None
    new_symbol: str | None = None
    new_basis: str | None = None
    name_changed: bool = False
    prior_name: str | None = None
    new_name: str | None = None
    basis: str
    detail: dict[str, Any] = {}
    undoes: UUID | None = None
    created_at: datetime


class StandingChange(PriceSymbolChange):
    """A standing change joined to the master's CURRENT values and ticker — what the Admin panel renders
    (``undoable`` = the master still holds this change's new values, so an undo would be exact)."""

    ticker: str | None = None
    current_symbol: str | None = None
    current_basis: str | None = None
    current_name: str | None = None

    @property
    def undoable(self) -> bool:
        return _holds(self, self.current_symbol, self.current_basis, self.current_name)


class PriceFeedAck(BaseModel):
    """One ``price_feed_ack`` row, as stored (an ``acknowledge`` or a ``revoke``)."""

    id: UUID
    tenant_id: UUID
    security_id: UUID
    action: str
    reason: str
    basis: str
    detail: dict[str, Any] = {}
    revokes: UUID | None = None
    created_at: datetime


def _holds(
    change: PriceSymbolChange, symbol: str | None, basis: str | None, name: str | None
) -> bool:
    """Does the master (``symbol`` / ``basis`` / ``name``) still hold exactly ``change``'s NEW values?"""
    if (symbol, basis) != (change.new_symbol, change.new_basis):
        return False
    return not change.name_changed or name == change.new_name


def _lock_master(conn: psycopg.Connection, security_id: UUID) -> dict:
    """Lock one master row for the duration of the caller's transaction; ``LookupError`` if absent."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, tenant_id, ticker, name, price_symbol, price_symbol_basis "
            "FROM security_master WHERE id = %s FOR UPDATE",
            (security_id,),
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"security not found: {security_id}")
    return row


def _stored_symbol(symbol: str | None, ticker: str | None) -> str | None:
    """The value ``master.set_price_symbol`` would STORE for ``symbol`` (upper-cased; ``None`` when it
    equals the ticker — the column holds only the exception)."""
    if symbol is None:
        return None
    s = symbol.strip().upper()
    return None if s == (ticker or "").strip().upper() else s


def apply_change(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    action: str,
    symbol: str,
    basis: str,
    new_name: str | None = None,
    detail: dict[str, Any] | None = None,
) -> PriceSymbolChange:
    """Point one name's price leg at ``symbol`` (an ``adopt`` or a ``set``) through the EXISTING writer
    ``master.set_price_symbol(basis=...)``, optionally refreshing the master's display ``name``, and record
    the change with the master's prior + new values (read back after the write, so the row records what is
    actually stored). ``RepairConflict`` when it would change nothing; ``LookupError`` for an unknown
    security; ``ValueError`` for an unknown action or a blank symbol/basis. The caller owns the
    transaction."""
    if action not in ACTIONS_CHANGE:
        raise ValueError(f"action must be one of {ACTIONS_CHANGE}, got {action!r}")
    if not symbol or not symbol.strip() or not basis.strip():
        raise ValueError("a price-symbol change needs a symbol and a basis")
    before = _lock_master(conn, security_id)
    tenant = before["tenant_id"]
    target = _stored_symbol(symbol, before["ticker"])
    rename = bool(new_name and new_name.strip() and new_name.strip() != (before["name"] or ""))
    if target == before["price_symbol"] and not rename:
        shown = target or before["ticker"]
        raise RepairConflict(f"{before['ticker'] or security_id} is already priced under {shown}")
    master.set_price_symbol(conn, security_id, symbol.strip(), basis=basis, tenant_id=tenant)
    if rename:
        master.set_name(conn, security_id, new_name.strip(), tenant_id=tenant)  # type: ignore[union-attr]
    with conn.cursor() as cur:
        cur.execute(
            "SELECT name, price_symbol, price_symbol_basis FROM security_master WHERE id = %s",
            (security_id,),
        )
        after = cur.fetchone()
        cur.execute(
            """INSERT INTO price_symbol_change
                   (tenant_id, security_id, action, prior_symbol, prior_basis, new_symbol, new_basis,
                    name_changed, prior_name, new_name, basis, detail)
               VALUES (%(tenant)s, %(sid)s, %(action)s, %(ps)s, %(pb)s, %(ns)s, %(nb)s,
                       %(nc)s, %(pn)s, %(nn)s, %(basis)s, %(detail)s)
               RETURNING *""",
            {
                "tenant": tenant,
                "sid": security_id,
                "action": action,
                "ps": before["price_symbol"],
                "pb": before["price_symbol_basis"],
                "ns": after["price_symbol"],
                "nb": after["price_symbol_basis"],
                "nc": rename,
                "pn": before["name"] if rename else None,
                "nn": after["name"] if rename else None,
                "basis": basis,
                "detail": Json(detail or {}),
            },
        )
        return PriceSymbolChange(**cur.fetchone())


def standing_change_for(conn: psycopg.Connection, security_id: UUID) -> PriceSymbolChange | None:
    """The security's STANDING change — its newest adopt/set that no undo reverses — or ``None``."""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT c.* FROM price_symbol_change c
                 WHERE c.security_id = %s AND c.action IN ('adopt', 'set') AND {NOT_UNDONE_SQL}
                 ORDER BY c.created_at DESC, c.id DESC LIMIT 1""",  # noqa: S608 — module constant
            (security_id,),
        )
        row = cur.fetchone()
    return PriceSymbolChange(**row) if row else None


def undo_change(conn: psycopg.Connection, change_id: UUID, *, basis: str) -> PriceSymbolChange:
    """Reverse one adopt/set EXACTLY (see the module docstring's rules): restore the prior
    ``price_symbol`` / ``price_symbol_basis`` (and ``name``, when the change refreshed it) verbatim, and
    append an ``undo`` row naming the change. ``LookupError`` for an unknown change or an undo row;
    ``RepairConflict`` when it is already undone, a later change stands, or the master no longer holds its
    values. The caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM price_symbol_change WHERE id = %s", (change_id,))
        row = cur.fetchone()
    if row is None or row["action"] == "undo":
        raise LookupError(f"no adopt/set change with id {change_id}")
    change = PriceSymbolChange(**row)
    now = _lock_master(conn, change.security_id)
    who = now["ticker"] or change.security_id
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM price_symbol_change WHERE undoes = %s", (change_id,))
        if cur.fetchone() is not None:
            raise RepairConflict(f"this change to {who} was already undone")
    standing = standing_change_for(conn, change.security_id)
    if standing is None or standing.id != change.id:
        raise RepairConflict(f"a later price-symbol change stands for {who} — undo that one first")
    if not _holds(change, now["price_symbol"], now["price_symbol_basis"], now["name"]):
        raise RepairConflict(
            f"{who}'s master changed since this repair (now price symbol "
            f"{now['price_symbol'] or 'unset'}, basis {now['price_symbol_basis'] or 'unset'}) — nothing "
            "undone; repair it by hand"
        )
    master.restore_price_symbol(
        conn,
        change.security_id,
        change.prior_symbol,
        change.prior_basis,
        tenant_id=change.tenant_id,
    )
    if change.name_changed:
        master.set_name(conn, change.security_id, change.prior_name, tenant_id=change.tenant_id)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO price_symbol_change
                   (tenant_id, security_id, action, prior_symbol, prior_basis, new_symbol, new_basis,
                    name_changed, prior_name, new_name, basis, detail, undoes)
               VALUES (%(tenant)s, %(sid)s, 'undo', %(ps)s, %(pb)s, %(ns)s, %(nb)s,
                       %(nc)s, %(pn)s, %(nn)s, %(basis)s, '{}'::jsonb, %(undoes)s)
               RETURNING *""",
            {
                "tenant": change.tenant_id,
                "sid": change.security_id,
                "ps": change.new_symbol,
                "pb": change.new_basis,
                "ns": change.prior_symbol,
                "nb": change.prior_basis,
                "nc": change.name_changed,
                "pn": change.new_name,
                "nn": change.prior_name,
                "basis": basis,
                "undoes": change.id,
            },
        )
        return PriceSymbolChange(**cur.fetchone())


def standing_changes(conn: psycopg.Connection) -> list[StandingChange]:
    """Every security's STANDING change (newest adopt/set no undo reverses), joined to the master's
    current values — the Admin panel's "applied repairs" list, each with its Undo. Ticker order. Read-only.
    """
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT * FROM (
                    SELECT DISTINCT ON (c.security_id) c.*, sm.ticker,
                           sm.price_symbol AS current_symbol, sm.price_symbol_basis AS current_basis,
                           sm.name AS current_name
                      FROM price_symbol_change c
                      JOIN security_master sm ON sm.id = c.security_id AND sm.tenant_id = c.tenant_id
                     WHERE c.action IN ('adopt', 'set') AND {NOT_UNDONE_SQL}
                     ORDER BY c.security_id, c.created_at DESC, c.id DESC
                ) s ORDER BY s.ticker NULLS LAST, s.security_id""",  # noqa: S608 — module constant
        )
        return [StandingChange(**r) for r in cur.fetchall()]


def acknowledge(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    reason: str,
    basis: str,
    detail: dict[str, Any] | None = None,
) -> tuple[PriceFeedAck, bool]:
    """Acknowledge one unpriceable name ("keep it, unpriced"). IDEMPOTENT: when an active acknowledgment
    already exists it is returned with ``False`` and nothing is written (a double-click appends one row,
    not two); else the new row with ``True``. ``LookupError`` for an unknown security; ``ValueError`` for a
    blank reason / basis. The caller owns the transaction."""
    if not reason.strip() or not basis.strip():
        raise ValueError("an acknowledgment needs a reason and a basis")
    before = _lock_master(conn, security_id)
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT a.* FROM price_feed_ack a
                 WHERE a.security_id = %s AND a.action = 'acknowledge' AND {ACK_ACTIVE_SQL}
                 ORDER BY a.created_at DESC, a.id DESC LIMIT 1""",  # noqa: S608 — module constant
            (security_id,),
        )
        existing = cur.fetchone()
        if existing is not None:
            return PriceFeedAck(**existing), False
        cur.execute(
            """INSERT INTO price_feed_ack (tenant_id, security_id, action, reason, basis, detail)
               VALUES (%s, %s, 'acknowledge', %s, %s, %s) RETURNING *""",
            (before["tenant_id"], security_id, reason.strip(), basis, Json(detail or {})),
        )
        return PriceFeedAck(**cur.fetchone()), True


def revoke(conn: psycopg.Connection, ack_id: UUID, *, reason: str, basis: str) -> PriceFeedAck:
    """Withdraw one ACTIVE acknowledgment by appending a ``revoke`` row that names it (the name is loud
    again from the next pass). ``LookupError`` when no active acknowledgment has that id (unknown, already
    revoked, or itself a revoke) — the DB's trigger + unique index refuse the same shapes a second time.
    The caller owns the transaction."""
    if not reason.strip() or not basis.strip():
        raise ValueError("a revoke needs a reason and a basis")
    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO price_feed_ack
                    (tenant_id, security_id, action, reason, basis, detail, revokes)
                SELECT a.tenant_id, a.security_id, 'revoke', %(reason)s, %(basis)s, '{{}}'::jsonb, a.id
                  FROM price_feed_ack a
                 WHERE a.id = %(id)s AND a.action = 'acknowledge' AND {ACK_ACTIVE_SQL}
                RETURNING *""",  # noqa: S608 — ACK_ACTIVE_SQL is a module constant
            {"id": ack_id, "reason": reason.strip(), "basis": basis},
        )
        row = cur.fetchone()
    if row is None:
        raise LookupError(f"no active acknowledgment with id {ack_id}")
    return PriceFeedAck(**row)


def active_acks(conn: psycopg.Connection) -> list[PriceFeedAck]:
    """Every ACTIVE acknowledgment (one per security at most — ``acknowledge`` is idempotent under the row
    lock), oldest first. Read-only."""
    with conn.cursor() as cur:
        cur.execute(
            f"""SELECT a.* FROM price_feed_ack a
                 WHERE a.action = 'acknowledge' AND {ACK_ACTIVE_SQL}
                 ORDER BY a.created_at, a.id""",  # noqa: S608 — module constant
        )
        return [PriceFeedAck(**r) for r in cur.fetchall()]


def acknowledged_security_ids(conn: psycopg.Connection) -> set[UUID]:
    """The security ids carrying an ACTIVE acknowledgment — what the nightly pass marks quiet (Q8) and
    whose vendor 404 it excuses (T3). Read-only."""
    return {a.security_id for a in active_acks(conn)}
