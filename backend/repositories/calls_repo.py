from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.types.json import Json

from domain.call import CallCard
from repositories.mappers import call_to_row, row_to_call


def append(conn: psycopg.Connection, card: CallCard) -> UUID:
    """Append an assembled CallCard to the write-only accountability log. NOT the read path — the API
    recomputes the card live from facts. The caller owns the transaction (commit/rollback).
    """
    row = call_to_row(card)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO calls (tenant_id, thesis_id, asof, state, verdict, card)
               VALUES (%(tenant_id)s, %(thesis_id)s, %(asof)s, %(state)s, %(verdict)s, %(card)s)
               RETURNING id""",
            {**row, "card": Json(row["card"])},
        )
        return cur.fetchone()["id"]


def list_for_thesis(conn: psycopg.Connection, thesis_id: UUID) -> list[CallCard]:
    """Every logged card for a thesis, oldest first — accountability inspection / a future scoreboard,
    never the serve path (the API recomputes from facts).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT card FROM calls WHERE thesis_id = %s ORDER BY seq", (thesis_id,))
        return [row_to_call(r) for r in cur.fetchall()]
