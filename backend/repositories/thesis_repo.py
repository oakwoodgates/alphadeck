from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from domain.thesis import BasketMember, Catalyst, ExcludedName, KillCriterion, TermSetEntry, Thesis
from repositories.mappers import _row_to_basket_member, row_to_thesis, thesis_to_row


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
        cur.execute(
            "SELECT * FROM thesis_exclusion WHERE thesis_id = %s ORDER BY recorded_at, id",
            (thesis_id,),
        )
        exclusions = cur.fetchall()
    return row_to_thesis(t, basket, evidence, catalysts, kills, exclusions)


def get_asof(conn: psycopg.Connection, thesis_id: UUID, known_at: datetime | None) -> Thesis | None:
    """Load a Thesis with its basket ROSTER as it was known at ``known_at`` — the roster's point-in-time
    read (F12, the SECOND bitemporal leak). ``basket_member`` is full-replace / non-temporal, so a plain
    ``get`` reconstructs a PAST-dated recompute on TODAY's roster; ``basket_snapshot`` (migration 0043)
    versions the roster, one jsonb row per promote. The thesis BODY and its other children (evidence /
    catalysts / kills / exclusions) load live via ``get`` — only the roster is versioned — then the basket
    is REPLACED by the LATEST snapshot with ``taken_at <= known_at`` (NO LOOKAHEAD, #1: a snapshot taken
    after ``known_at`` is never read). ``known_at`` None -> now.

    PRE-SNAPSHOT FALLBACK: a thesis with NO qualifying snapshot (promoted before this table existed, or
    entirely before ``known_at``) keeps the live ``basket_member`` roster from ``get`` — the honest best
    available (a separate F11 label change carries the "recompute ran on the live roster" note).

    BYTE-IDENTICAL LIVE PATH: ``get_asof(now)`` reproduces ``get`` exactly for any thesis with a current
    snapshot — the deploy seed writes one per existing thesis, and ``upsert`` writes one on every roster
    change — so the live/today call path (serve asof=today, the nightly record) is unchanged. Callers own
    the transaction. This is roster METADATA, never a call input (#3): the reassigned basket only re-scopes
    the PIT prefetch and the armed/watch grouping, exactly as the live roster did."""
    thesis = get(conn, thesis_id)
    if thesis is None:
        return None
    with conn.cursor() as cur:
        cur.execute(
            "SELECT members FROM basket_snapshot "
            "WHERE thesis_id = %s AND taken_at <= COALESCE(%s, now()) "
            "ORDER BY taken_at DESC, id DESC LIMIT 1",
            (thesis_id, known_at),
        )
        row = cur.fetchone()
    if row is not None:
        # jsonb comes back as list[dict]; sort by the stored ordinal (mirrors get's ORDER BY ordinal)
        # and rebuild through the SAME mapper get uses, so the reconstructed members are byte-identical.
        members = sorted(row["members"], key=lambda b: b["ordinal"])
        thesis.basket = [_row_to_basket_member(b) for b in members]
    return thesis


def snapshot_exists_asof(
    conn: psycopg.Connection, thesis_id: UUID, known_at: datetime | None
) -> bool:
    """Did this thesis have a roster snapshot AT ``known_at``? — the LABEL half of ``get_asof`` (F4).

    ``get_asof`` returns a Thesis either way: with a qualifying snapshot it reconstructs the roster as it
    was known then, without one it FALLS BACK to the live ``basket_member`` roster. That fallback is honest
    but invisible from the outside — the caller gets an ordinary Thesis and cannot tell which of the two it
    holds. The replay harness has to be able to SAY which, because a window that predates the snapshot
    table is a labeled counterfactual on membership, not a replay of the roster.

    A narrow sibling read rather than a changed ``get_asof`` signature: that function sits on the single
    live assembly funnel (serve / cron / backfill / pipeline.run), and widening its return type to carry a
    label no live caller wants would put a replay-only concern on the live path. THE PREDICATE IS THE SAME
    ONE, stated once more here and nowhere else: ``taken_at <= COALESCE(known_at, now())``, no lookahead
    (#1) — a snapshot taken after ``known_at`` is never read, by this or by ``get_asof``. Read-only; the
    caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT 1 FROM basket_snapshot "
            "WHERE thesis_id = %s AND taken_at <= COALESCE(%s, now()) LIMIT 1",
            (thesis_id, known_at),
        )
        return cur.fetchone() is not None


def basket_size_asof(
    conn: psycopg.Connection, thesis_id: UUID, known_at: datetime | None, *, live_fallback: int
) -> int:
    """The roster COUNT as it was known at ``known_at`` — the COUNT-ONLY point-in-time read for the
    Scoreboard (F12). Reads ``jsonb_array_length`` of the latest snapshot with ``taken_at <= known_at``
    (the SAME no-lookahead predicate as ``get_asof``, #1) — it never hydrates the thesis. ``get_asof`` (a
    FULL load: basket + evidence + catalysts + kills + exclusions) is for the assembly funnel that needs
    the whole thesis; the Scoreboard loops every thesis and needs only the number, so a second full ``get``
    per thesis would double the per-thesis DB load for a count. ``live_fallback`` is returned when no
    qualifying snapshot exists (pre-F12, or a ``known_at`` before the first snapshot) — the caller passes
    ``len(thesis.basket)``, the live roster it already holds. ``known_at`` None -> now. Read-only; the
    caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT jsonb_array_length(members) AS n FROM basket_snapshot "
            "WHERE thesis_id = %s AND taken_at <= COALESCE(%s, now()) "
            "ORDER BY taken_at DESC, id DESC LIMIT 1",
            (thesis_id, known_at),
        )
        row = cur.fetchone()
    return row["n"] if row is not None else live_fallback


def _member_to_snapshot(ordinal: int, m: BasketMember) -> dict[str, Any]:
    """One basket member as a JSON-native snapshot object — the SAME key set and value types the deploy
    seed (migration 0043) builds from the ``basket_member`` columns, so ``md5(members::text)`` agrees
    across the seed and the on-promote write (jsonb normalizes key order). Values are JSON-native (UUID ->
    str) so ``Json`` can serialize them — the ``set_term_set`` ``model_dump(mode="json")`` idiom. Every
    field ``_row_to_basket_member`` reads is present, so ``get_asof`` round-trips a byte-identical member.
    """
    return {
        "ordinal": ordinal,
        "ticker": m.ticker,
        "role": m.role,
        "security_id": str(m.security_id) if m.security_id is not None else None,
        "detail": m.detail,
        "segment": m.segment,
        "thesis_fit": m.thesis_fit,
        "conviction": m.conviction,
        "surfaced_terms": list(m.surfaced_terms),
        "authored_by": m.authored_by.value,
        "signed_off": m.signed_off,
    }


def _write_basket_snapshot(cur: psycopg.Cursor, thesis: Thesis, tenant_id: UUID) -> None:
    """Append a ``basket_snapshot`` row for the roster ``upsert`` just wrote — SKIPPED when its
    ``content_hash`` equals the thesis's latest snapshot. The Workbench full-replaces the basket on EVERY
    interactive edit (a narrative-only re-promote resends an unchanged roster), so without the dedup the
    table would grow on every save (the idempotency convention: count the table, not the read). Atomic with
    the upsert (same cursor/transaction). ``content_hash`` is ``md5(members::text)`` computed by Postgres
    over the SAME logical jsonb the deploy seed uses, so seed and write agree and a re-promote of an
    unchanged roster dedups against the seed."""
    members = [_member_to_snapshot(i, m) for i, m in enumerate(thesis.basket)]
    cur.execute(
        """
        INSERT INTO basket_snapshot (id, tenant_id, thesis_id, taken_at, members, content_hash)
        SELECT gen_random_uuid(), %(tenant)s, %(tid)s, now(), m.members, md5(m.members::text)
        FROM (SELECT %(members)s::jsonb AS members) m
        WHERE md5(m.members::text) IS DISTINCT FROM (
            SELECT content_hash FROM basket_snapshot
            WHERE thesis_id = %(tid)s ORDER BY taken_at DESC, id DESC LIMIT 1
        )
        """,
        {"tenant": tenant_id, "tid": thesis.id, "members": Json(members)},
    )


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


def set_surfaced_terms(
    conn: psycopg.Connection, thesis_id: UUID, terms_by_security: dict[UUID, list[str]]
) -> None:
    """Freeze per-member ``surfaced_terms`` (the discovery-term provenance) for the given members — the
    backfill CLI's narrow writer (``pipeline.backfill_surfaced_terms``, its SOLE caller; the promote path
    persists the field through ``upsert``). ``terms_by_security`` maps a member's ``security_id`` to its
    frozen term list.

    The ``set_term_set`` idiom: a per-member UPDATE-in-place that touches ONLY ``surfaced_terms`` — no
    DELETE/INSERT, so ordinals, authorship, and every other member field are structurally untouched (no
    ordinal churn, no wipe risk). A ``security_id`` with no matching row updates nothing (an unplaced /
    since-removed member — the caller counts, never guesses). The caller owns the transaction."""
    with conn.cursor() as cur:
        for sid, terms in terms_by_security.items():
            cur.execute(
                "UPDATE basket_member SET surfaced_terms = %s "
                "WHERE thesis_id = %s AND security_id = %s",
                (terms, thesis_id, sid),  # psycopg adapts list[str] <-> text[]
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


def set_exclusions(
    conn: psycopg.Connection, thesis_id: UUID, exclusions: list[ExcludedName], *, tenant_id: UUID
) -> None:
    """Persist the thesis's durable exclusion set (#7) — the operator's NO per name, with the
    optional why. The SOLE writer of ``thesis_exclusion`` (``upsert`` never names the table — the
    term_set structural guard, fourth application: a promote can't wipe the operator's pruning).
    Full-list replace: the editor sends the CURRENT set (its session decisions ∪ the carried-forward
    prior exclusions it didn't re-decide). Discovery NEVER reads this table (#9 — recall sacred; the
    editor applies it as visible, reversible grayed state). The caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM thesis_exclusion WHERE thesis_id = %s", (thesis_id,))
        for e in exclusions:
            cur.execute(
                """INSERT INTO thesis_exclusion (tenant_id, thesis_id, security_id, ticker, reason)
                   VALUES (%s, %s, %s, %s, %s)
                   ON CONFLICT (thesis_id, security_id) DO NOTHING""",
                (tenant_id, thesis_id, e.security_id, e.ticker, e.reason),
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


def created_at_for(conn: psycopg.Connection, thesis_ids: Iterable[UUID]) -> dict[UUID, datetime]:
    """``thesis_id -> created_at`` (an aware instant — the column is ``timestamptz``) for the ids given;
    an unknown id is simply absent. The domain ``Thesis`` deliberately does not carry ``created_at``
    (it is a row fact, not part of the spine the promote payload owns — ``upsert`` never names it, so
    it is stable for the life of the row). This narrow read exists for the ONE question that needs it:
    did the thesis EXIST on a past night (``pipeline.backfill``'s existence gate, and the repair script
    that shares its rule)? Read-only."""
    ids = list(thesis_ids)
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT id, created_at FROM thesis WHERE id = ANY(%s)", (ids,))
        return {r["id"]: r["created_at"] for r in cur.fetchall()}


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
                   (tenant_id, thesis_id, ordinal, ticker, role, security_id, detail,
                    segment, thesis_fit, conviction, surfaced_terms, authored_by, signed_off)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (
                    tenant,
                    tid,
                    i,
                    m.ticker,
                    m.role,
                    m.security_id,
                    m.detail,
                    m.segment,
                    m.thesis_fit,
                    m.conviction,
                    m.surfaced_terms,  # psycopg adapts list[str] <-> text[]
                    m.authored_by.value,
                    m.signed_off,
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
        # F12 — freeze the roster just written as a point-in-time snapshot (deduped when unchanged),
        # ATOMIC with this upsert (same cursor/transaction). get_asof(known_at) reads it back so a
        # past-dated recompute runs on the roster as it was known then, not today's. Roster metadata
        # only — never a call input (#3); no CallCard field changes.
        _write_basket_snapshot(cur, thesis, tenant)
