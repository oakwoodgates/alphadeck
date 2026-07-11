"""The operator-decisions log (decision capture) — the append-only writer + the derived-position read.

The log is the SOURCE OF TRUTH for a thesis's position: ``derived_position`` nets the non-voided
take/close events as-of BOTH time axes (``decision_date`` = valid time, ``recorded_at`` = transaction
time — the #1 discipline: a replayed past call never sees a later-logged fill), and
``effective_position`` applies the precedence rule — ANY decision rows make the log authoritative
(including "net closed → None"); a thesis with no rows falls back to the seed-era
``thesis.position_*`` columns (the HIMS demo). Rationale for the log-over-columns design: the promote
upsert overwrites those columns from a request that never carries them (a narrative edit would
silently close a stored position), and a position open/close is temporal — never UPDATE-in-place.

One open position per thesis (gate-1 v1): the position is the LATEST non-voided take/close event —
a take opens, a close closes; the API layer enforces take-only-when-flat / close-only-when-open.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.thesis import Position, Thesis

ACTIONS = ("take", "pass", "close", "void")


def append(
    conn: psycopg.Connection,
    *,
    thesis_id: UUID,
    action: str,
    decision_date: date,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    security_id: UUID | None = None,
    shares: float | None = None,
    price: float | None = None,
    reason: str | None = None,
    voids: UUID | None = None,
    call_state: str | None = None,
    call_verdict: str | None = None,
) -> dict[str, Any]:
    """Append ONE decision row and return it (the inserted row, dict — id/recorded_at included).
    Never updates or deletes (the table's no_update trigger enforces it); a mistake is corrected by a
    later ``action='void'`` append. The caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO operator_decision
                   (tenant_id, thesis_id, security_id, action, decision_date, shares, price,
                    reason, voids, call_state, call_verdict)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               RETURNING *""",
            (
                tenant_id,
                thesis_id,
                security_id,
                action,
                decision_date,
                shares,
                price,
                reason,
                voids,
                call_state,
                call_verdict,
            ),
        )
        return cur.fetchone()


def list_for_thesis(
    conn: psycopg.Connection, thesis_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
) -> list[dict[str, Any]]:
    """Every decision row for a thesis, newest first (the card's history strip + inspection).
    Raw rows (dicts) — the wire layer shapes them; voided rows ride along VISIBLY (the strip greys
    them; hiding a voided row would un-tell the story the log exists to tell)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM operator_decision WHERE thesis_id = %s AND tenant_id = %s "
            "ORDER BY seq DESC",
            (thesis_id, tenant_id),
        )
        return cur.fetchall()


def derived_position(
    conn: psycopg.Connection,
    thesis_id: UUID,
    *,
    asof: date,
    known_at: datetime | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> tuple[Position | None, bool]:
    """The log-derived position as-of: ``(position, any_rows)``.

    Nets the non-voided take/close events with ``decision_date <= asof`` AND ``recorded_at <=
    known_at`` (both axes — no lookahead): the latest such event decides — a take → an open
    ``Position(entry_price=price, opened_on=decision_date, security_id=row's name — None on a
    thesis-level take)``; a close → ``None``. ``any_rows`` is
    True when the thesis has ANY decision row at all (regardless of the as-of window) — the
    precedence signal ``effective_position`` uses, so a logged-then-closed position does NOT fall
    back to the stale seed columns. Voids recorded after ``known_at`` do not yet apply (a replay
    sees the log exactly as it stood)."""
    known = known_at or datetime.now(timezone.utc)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM operator_decision WHERE thesis_id = %s AND tenant_id = %s "
            "AND recorded_at <= %s ORDER BY seq",
            (thesis_id, tenant_id, known),
        )
        rows = cur.fetchall()
    if not rows:
        return None, False
    voided = {r["voids"] for r in rows if r["action"] == "void" and r["voids"] is not None}
    events = [
        r
        for r in rows
        if r["action"] in ("take", "close") and r["id"] not in voided and r["decision_date"] <= asof
    ]
    if not events:
        return None, True
    last = max(events, key=lambda r: (r["decision_date"], r["seq"]))
    if last["action"] == "close":
        return None, True
    return (
        Position(
            entry_price=float(last["price"]) if last["price"] is not None else None,
            opened_on=last["decision_date"],
            security_id=last["security_id"],
        ),
        True,
    )


def effective_position(
    conn: psycopg.Connection,
    thesis: Thesis,
    *,
    asof: date,
    known_at: datetime | None = None,
) -> Position | None:
    """The position the call machinery should see: the LOG when the thesis has any decision rows
    (authoritative, including net-closed → None), else the stored ``thesis.position_*`` columns
    (seed-era fallback — the HIMS demo predates the log). The thesis's own tenant scopes the read
    (same rule as ``call_for_thesis``: a loaded thesis's tenant_id is non-None)."""
    pos, any_rows = derived_position(
        conn, thesis.id, asof=asof, known_at=known_at, tenant_id=thesis.tenant_id
    )
    return pos if any_rows else thesis.position
