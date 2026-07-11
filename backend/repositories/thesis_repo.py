from __future__ import annotations

from uuid import UUID

import psycopg
from psycopg.types.json import Json

from domain.thesis import Catalyst, KillCriterion, TermSetEntry, Thesis
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


def set_catalysts(
    conn: psycopg.Connection, thesis_id: UUID, catalysts: list[Catalyst], *, tenant_id: UUID
) -> None:
    """Persist the thesis's narrative catalysts (the card's catalyst SURFACE — upcoming binary events,
    display objects; the per-security CONVICTION facts live in ``fact_catalyst`` via the ratify path).
    The SOLE writer of the ``catalyst`` child table — deliberately NOT part of ``upsert``, so a promote
    that doesn't carry catalysts structurally CANNOT wipe them (the ``set_term_set`` wipe-guard).
    Full-list replace (the operator edits the list as a whole); the caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM catalyst WHERE thesis_id = %s", (thesis_id,))
        for i, c in enumerate(catalysts):
            cur.execute(
                """INSERT INTO catalyst
                   (id, tenant_id, thesis_id, label, kind, when_date, when_label, ordinal)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (c.id, tenant_id, thesis_id, c.label, c.kind, c.when_date, c.when_label, i),
            )


def set_kill_criteria(
    conn: psycopg.Connection, thesis_id: UUID, kills: list[KillCriterion], *, tenant_id: UUID
) -> None:
    """Persist the thesis's kill criteria (the counter-case's documented "what would kill this").
    The SOLE writer of the ``kill_criterion`` child table — same structural wipe-guard as
    ``set_catalysts``. Full-list replace; the caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM kill_criterion WHERE thesis_id = %s", (thesis_id,))
        for i, k in enumerate(kills):
            cur.execute(
                """INSERT INTO kill_criterion (id, tenant_id, thesis_id, text, ordinal)
                   VALUES (%s, %s, %s, %s, %s)""",
                (k.id, tenant_id, thesis_id, k.text, i),
            )


def list_all(conn: psycopg.Connection, *, include_archived: bool = False) -> list[Thesis]:
    """Every thesis, each fully loaded, ordered by name. ARCHIVED theses are EXCLUDED by default —
    the Board's default list, the workbench picker, and the daily cron's walk all skip them without
    asking (an archived test basket stops accumulating calls-of-record; the Scoreboard's data stays
    clean). ``include_archived=True`` is the explicit, reversible filter (the Board's collapsed
    "Archived" section). The API projects these to lightweight summaries; at this scale a full load
    per thesis is fine (optimize if the universe grows)."""
    where = "" if include_archived else "WHERE archived_at IS NULL"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id FROM thesis {where} ORDER BY name")
        ids = [r["id"] for r in cur.fetchall()]
    return [thesis for thesis in (get(conn, i) for i in ids) if thesis is not None]


def set_archived(conn: psycopg.Connection, thesis_id: UUID, archived: bool) -> None:
    """Archive (never delete) / restore a thesis — the SOLE writer of ``archived_at`` (``upsert``
    never names the column, so a promote can neither archive nor resurrect). Idempotent: archiving
    an archived thesis re-stamps the time; restoring a live one is a no-op NULL. The spine, the
    calls log, and the decision log all stay — reversible, nothing vanishes. The caller owns the
    transaction."""
    stamp = "now()" if archived else "NULL"
    with conn.cursor() as cur:
        cur.execute(
            f"UPDATE thesis SET archived_at = {stamp}, updated_at = now() WHERE id = %s",
            (thesis_id,),
        )


def upsert(conn: psycopg.Connection, thesis: Thesis) -> None:
    """Insert or update a thesis. The basket is replaced (the promote payload owns it); evidence is
    APPEND-ONLY (new rows added, existing ones never modified or removed). Catalysts and kill
    criteria are NOT touched here — ``set_catalysts`` / ``set_kill_criteria`` are their sole writers
    (the ``set_term_set`` pattern: a promote that doesn't carry them structurally CANNOT wipe them;
    before the authoring surfaces existed, the old full-replace here wiped [] over [] silently on
    every narrative edit — the wipe-trap's third instance, caught before it had rows to lose).
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
                    segment, thesis_fit, conviction, authored_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    tenant,
                    tid,
                    i,
                    m.ticker,
                    m.role,
                    m.archetype.value if m.archetype else None,
                    m.security_id,
                    m.detail,
                    m.segment,
                    m.thesis_fit,
                    m.conviction,
                    m.authored_by.value,
                ),
            )
        # evidence is append-only: add new rows, never modify/remove existing ones
        for i, e in enumerate(thesis.evidence):
            cur.execute(
                """INSERT INTO evidence (id, tenant_id, thesis_id, kind, label, ref, date_label, ordinal)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                   ON CONFLICT (id) DO NOTHING""",
                (e.id, tenant, tid, e.kind, e.label, e.ref, e.date_label, i),
            )
