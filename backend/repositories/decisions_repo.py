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

ONE CLOCK on the transaction axis: ``recorded_at`` is stamped by Postgres, so an unpinned read takes
its ``recorded_at <=`` bound from Postgres too (``clock_timestamp()``), never ``datetime.now()`` on
the app host — see ``derived_position`` for the two-clock trap that rule closes.
"""

from __future__ import annotations

from datetime import date, datetime
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
    sees the log exactly as it stood).

    ONE CLOCK FOR THE TRANSACTION AXIS (the two-clock trap — a fixed flake, do not re-introduce).
    ``known_at=None`` means "the log as it stands", and that bound is supplied by the DATABASE
    (``clock_timestamp()``), never by ``datetime.now()`` on the app host. ``recorded_at`` is stamped
    by the Postgres clock (``DEFAULT now()``, migration 0019), so defaulting the bound to the HOST
    clock compared two independent clocks: when the DB clock ran fractionally ahead, a row appended
    milliseconds earlier read as *not yet recorded* and vanished from its own log. That is not
    hypothetical — measured on the dev box, the DB clock ran ~0.2-0.6 ms ahead of the host while the
    whole append-then-read window was only ~0.5-1.5 ms, and the filter is exact to the microsecond;
    it surfaced once as a full-suite-only flake in the pinned-``known_at`` decisions test, and it
    would have bitten ``POST /decisions`` the same way (a close 422-ing "no open position to close"
    because the take it should see is invisible). ``clock_timestamp()`` (the statement instant), NOT
    ``now()`` (the TRANSACTION-start instant): any row this statement's snapshot can see was
    committed before the statement ran, so its ``recorded_at`` is necessarily <=
    ``clock_timestamp()`` — a read in a long-open transaction can't lose a concurrently-committed
    append. An explicitly PASSED ``known_at`` is untouched: replay still pins the transaction axis
    exactly where the caller says."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT * FROM operator_decision WHERE thesis_id = %s AND tenant_id = %s "
            "AND recorded_at <= COALESCE(%s::timestamptz, clock_timestamp()) ORDER BY seq",
            (thesis_id, tenant_id, known_at),
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
