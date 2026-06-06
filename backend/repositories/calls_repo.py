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
    """Every logged card for a thesis, oldest first — the full append-only history (accountability
    inspection), never the serve path (the API recomputes from facts).
    """
    with conn.cursor() as cur:
        cur.execute("SELECT card FROM calls WHERE thesis_id = %s ORDER BY seq", (thesis_id,))
        return [row_to_call(r) for r in cur.fetchall()]


def latest_for_thesis(conn: psycopg.Connection, thesis_id: UUID) -> list[CallCard]:
    """The call of record at each ``asof`` — one row per as-of, the latest append wins (a re-run after
    a fact correction supersedes the earlier row), newest as-of first. This is the deduped read a
    scoreboard wants; ``list_for_thesis`` keeps the full history. Never the serve path.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (asof) card FROM calls WHERE thesis_id = %s "
            "ORDER BY asof DESC, seq DESC",
            (thesis_id,),
        )
        return [row_to_call(r) for r in cur.fetchall()]
