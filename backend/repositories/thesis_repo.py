from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.types.json import Json

from domain.thesis import TermSetEntry, Thesis
from repositories.mappers import row_to_thesis, thesis_to_row


def get(conn: psycopg.Connection, thesis_id: UUID) -> Thesis | None:
    """Load a Thesis (with its children) by id, or None. Raw rows never escape this package."""
    with conn.cursor() as cur:
        cur.execute("SELECT * FROM thesis WHERE id = %s", (thesis_id,))
        t = cur.fetchone()
        if t is None:
            return None
        cur.execute(
            "SELECT * FROM basket_member WHERE thesis_id = %s ORDER BY ordinal", (thesis_id,)
        )
        basket = cur.fetchall()
        cur.execute("SELECT * FROM evidence WHERE thesis_id = %s ORDER BY ordinal", (thesis_id,))
        evidence = cur.fetchall()
        cur.execute("SELECT * FROM catalyst WHERE thesis_id = %s ORDER BY ordinal", (thesis_id,))
        catalysts = cur.fetchall()
        cur.execute(
            "SELECT * FROM kill_criterion WHERE thesis_id = %s ORDER BY ordinal", (thesis_id,)
        )
        kills = cur.fetchall()
    return row_to_thesis(t, basket, evidence, catalysts, kills)


def set_term_set(conn: psycopg.Connection, thesis_id: UUID, term_set: list[TermSetEntry]) -> None:
    """Persist the thesis's tiered discovery term set (the SIGNAL/BROAD keywords discovery reads). The SOLE
    writer of ``thesis.term_set`` — a NARROW single-column UPDATE that touches nothing else.

    Deliberately NOT part of ``upsert``: ``upsert`` never names the ``term_set`` column, so a ``promote`` that
    omits the term set CANNOT blank it (a STRUCTURAL wipe-guard, not a remembered read-merge). Overwrites the
    whole set — the ``/terms`` producer regenerates wholesale, so a re-run cleanly supersedes. The caller owns
    the transaction (commit/rollback)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE thesis SET term_set = %s, updated_at = now() WHERE id = %s",
            (Json([e.model_dump(mode="json") for e in term_set]), thesis_id),
        )


def list_all(conn: psycopg.Connection) -> list[Thesis]:
    """Every thesis, each fully loaded, ordered by name. The API projects these to lightweight
    summaries; at this scale a full load per thesis is fine (optimize if the universe grows)."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM thesis ORDER BY name")
        ids = [r["id"] for r in cur.fetchall()]
    return [thesis for thesis in (get(conn, i) for i in ids) if thesis is not None]


def upsert(conn: psycopg.Connection, thesis: Thesis) -> None:
    """Insert or update a thesis. Definitional children (basket / catalysts / kill-criteria) are
    replaced; evidence is APPEND-ONLY (new rows added, existing ones never modified or removed).
    The caller owns the transaction (commit/rollback).
    """
    row = thesis_to_row(thesis)
    tid = thesis.id
    tenant = row["tenant_id"]
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO thesis (id, tenant_id, parent_id, name, narrative, ticker,
                position_entry_price, position_current_price, position_opened_on, segments)
            VALUES (%(id)s, %(tenant_id)s, %(parent_id)s, %(name)s, %(narrative)s, %(ticker)s,
                %(position_entry_price)s, %(position_current_price)s, %(position_opened_on)s, %(segments)s)
            ON CONFLICT (id) DO UPDATE SET
                parent_id = EXCLUDED.parent_id,
                name = EXCLUDED.name,
                narrative = EXCLUDED.narrative,
                ticker = EXCLUDED.ticker,
                position_entry_price = EXCLUDED.position_entry_price,
                position_current_price = EXCLUDED.position_current_price,
                position_opened_on = EXCLUDED.position_opened_on,
                segments = EXCLUDED.segments,
                updated_at = now()
            """,
            row,
        )
        cur.execute("DELETE FROM basket_member WHERE thesis_id = %s", (tid,))
        for i, m in enumerate(thesis.basket):
            cur.execute(
                """INSERT INTO basket_member
                   (tenant_id, thesis_id, ordinal, ticker, role, archetype, security_id, detail,
                    segment, thesis_fit, authored_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    tenant,
                    tid,
                    i,
                    m.ticker,
                    m.role,
                    m.archetype.value,
                    m.security_id,
                    m.detail,
                    m.segment,
                    m.thesis_fit,
                    m.authored_by.value,
                ),
            )
        cur.execute("DELETE FROM catalyst WHERE thesis_id = %s", (tid,))
        for i, c in enumerate(thesis.catalysts):
            cur.execute(
                """INSERT INTO catalyst
                   (id, tenant_id, thesis_id, label, kind, when_date, when_label, ordinal)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (c.id, tenant, tid, c.label, c.kind, c.when_date, c.when_label, i),
            )
        cur.execute("DELETE FROM kill_criterion WHERE thesis_id = %s", (tid,))
        for i, k in enumerate(thesis.kill_criteria):
            cur.execute(
                """INSERT INTO kill_criterion (id, tenant_id, thesis_id, text, ordinal)
                   VALUES (%s, %s, %s, %s, %s)""",
                (k.id, tenant, tid, k.text, i),
            )
        # evidence is append-only: add new rows, never modify/remove existing ones
        for i, e in enumerate(thesis.evidence):
            cur.execute(
                """INSERT INTO evidence (id, tenant_id, thesis_id, kind, label, ref, date_label, ordinal)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (e.id, tenant, tid, e.kind, e.label, e.ref, e.date_label, i),
            )
