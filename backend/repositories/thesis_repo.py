from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from domain.scopes import WorkspaceScope, workspace_filter
from domain.thesis import BasketMember, Catalyst, ExcludedName, KillCriterion, TermSetEntry, Thesis
from repositories import thesis_catalyst_repo
from repositories.mappers import (
    _row_to_basket_member,
    _row_to_catalyst,
    _row_to_kill,
    row_to_thesis,
    thesis_to_row,
)


class ThesisNotInWorkspace(LookupError):
    """``upsert`` was handed a thesis id that exists under ANOTHER workspace (the foreign-id overwrite guard). The
    promote route answers 404 "thesis not found": a caller never learns that another workspace's id exists.
    """


def get(conn: psycopg.Connection, thesis_id: UUID, *, workspace: WorkspaceScope) -> Thesis | None:
    """Load a Thesis (with its children) by id, or None. Raw rows never escape this package.

    ``workspace`` is REQUIRED: the caller's workspace id (a thesis in another workspace reads as None — the API's
    404), or ``domain.scopes.WORKSPACE_ANY`` for system code (the nightly, the CLIs, the replay harness).
    """
    found = _get_many(conn, [thesis_id], workspace=workspace)
    return found[0] if found else None


def _get_many(
    conn: psycopg.Connection, thesis_ids: Sequence[UUID], *, workspace: WorkspaceScope
) -> list[Thesis]:
    """Load the theses ``thesis_ids`` names (each with its children) in SIX queries however many ids there are:
    the thesis rows (filtered by ``workspace``), then the basket, evidence, catalysts, kill criteria and
    exclusions of the theses found, each ``ORDER BY thesis_id`` plus the order ``get`` always used, so every
    thesis's children keep their order. The single load path: ``get`` is this with one id, ``list_all`` with
    its ordered ids. An id that is missing, or under another workspace, is simply absent from the answer
    (``get``'s ``None``); the answer keeps the caller's order. Read-only; the caller owns the transaction.
    """
    ids = list(thesis_ids)
    if not ids:
        return []
    clause, params = workspace_filter(workspace)
    with conn.cursor() as cur:
        cur.execute(f"SELECT * FROM thesis WHERE id = ANY(%s){clause}", (ids, *params))
        theses = {t["id"]: t for t in cur.fetchall()}
        if not theses:
            return []
        found = list(theses)
        cur.execute(
            "SELECT * FROM basket_member WHERE thesis_id = ANY(%s) ORDER BY thesis_id, ordinal",
            (found,),
        )
        basket = cur.fetchall()
        cur.execute(
            "SELECT * FROM evidence WHERE thesis_id = ANY(%s) ORDER BY thesis_id, ordinal", (found,)
        )
        evidence = cur.fetchall()
        cur.execute(
            "SELECT * FROM catalyst WHERE thesis_id = ANY(%s) ORDER BY thesis_id, ordinal", (found,)
        )
        catalysts = cur.fetchall()
        cur.execute(
            "SELECT * FROM kill_criterion WHERE thesis_id = ANY(%s) ORDER BY thesis_id, ordinal",
            (found,),
        )
        kills = cur.fetchall()
        cur.execute(
            "SELECT * FROM thesis_exclusion WHERE thesis_id = ANY(%s) "
            "ORDER BY thesis_id, recorded_at, id",
            (found,),
        )
        exclusions = cur.fetchall()
    children: list[dict[UUID, list[dict[str, Any]]]] = []
    for rows in (basket, evidence, catalysts, kills, exclusions):
        by_thesis: dict[UUID, list[dict[str, Any]]] = {tid: [] for tid in found}
        for r in rows:
            by_thesis[r["thesis_id"]].append(r)
        children.append(by_thesis)
    return [
        row_to_thesis(
            theses[tid],
            children[0][tid],
            children[1][tid],
            children[2][tid],
            children[3][tid],
            children[4][tid],
        )
        for tid in ids
        if tid in theses
    ]


def get_asof(
    conn: psycopg.Connection,
    thesis_id: UUID,
    known_at: datetime | None,
    *,
    workspace: WorkspaceScope,
    loaded_thesis: Thesis | None = None,
) -> Thesis | None:
    """Load a Thesis with its basket ROSTER as it was known at ``known_at`` — the roster's point-in-time
    read (F12, the SECOND bitemporal leak). ``basket_member`` is full-replace / non-temporal, so a plain
    ``get`` reconstructs a PAST-dated recompute on TODAY's roster; ``basket_snapshot`` (migration 0043)
    versions the roster, one jsonb row per promote. The thesis BODY and its other children (evidence /
    catalysts / kills / exclusions) load live via ``get`` — only the roster is versioned — then the basket
    is REPLACED by the LATEST snapshot with ``taken_at <= known_at`` (NO LOOKAHEAD, #1: a snapshot taken
    after ``known_at`` is never read). ``known_at`` None -> now.

    PRE-SNAPSHOT FALLBACK: a thesis with NO qualifying snapshot (promoted before this table existed, or
    entirely before ``known_at``) keeps the live ``basket_member`` roster from ``get`` — the honest best
    available (F11 labels a past as-of view a recompute, and that label never claims the roster is
    period-accurate).

    BYTE-IDENTICAL LIVE PATH: ``get_asof(now)`` reproduces ``get`` exactly for any thesis with a current
    snapshot — the deploy seed writes one per existing thesis, and ``upsert`` writes one on every roster
    change — so the live/today call path (serve asof=today, the nightly record) is unchanged. Callers own
    the transaction. This is roster METADATA, never a call input (#3): the reassigned basket only re-scopes
    the PIT prefetch and the armed/watch grouping, exactly as the live roster did. ``workspace`` as ``get``.

    When ``loaded_thesis`` is supplied (matching ``thesis_id``), the caller has already loaded the thesis in
    the right workspace (e.g. ``get_thesis_or_404``), so we copy it and run only the single ``basket_snapshot``
    query instead of repeating the 6-query ``get``.
    """
    if loaded_thesis is not None and loaded_thesis.id == thesis_id:
        thesis = loaded_thesis.model_copy(deep=True)
    else:
        thesis = get(conn, thesis_id, workspace=workspace)
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


def snapshots_asof_for(
    conn: psycopg.Connection, thesis_ids: Sequence[UUID], known_at: datetime | None
) -> dict[UUID, list[BasketMember]]:
    """``thesis_id -> roster`` as ``get_asof`` reads it at ``known_at``, for many theses in ONE query. The SAME
    predicate and pick as ``get_asof`` (``taken_at <= COALESCE(known_at, now())``, the latest by ``taken_at DESC,
    id DESC``; no lookahead, #1) and the same member order and mapper, so each roster is byte-identical to
    ``get_asof(...).basket``. A thesis with NO qualifying snapshot is ABSENT from the answer: the caller keeps
    the live roster there (``get_asof``'s pre-snapshot fallback, and the label ``snapshot_exists_asof`` gives).
    Read-only; the caller owns the transaction."""
    ids = list(thesis_ids)
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (thesis_id) thesis_id, members FROM basket_snapshot "
            "WHERE thesis_id = ANY(%s) AND taken_at <= COALESCE(%s, now()) "
            "ORDER BY thesis_id, taken_at DESC, id DESC",
            (ids, known_at),
        )
        rows = cur.fetchall()
    return {
        r["thesis_id"]: [
            _row_to_basket_member(m) for m in sorted(r["members"], key=lambda b: b["ordinal"])
        ]
        for r in rows
    }


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
    ONE, stated once more in ``snapshot_taken_at_asof`` (which this delegates to): ``taken_at <=
    COALESCE(known_at, now())``, no lookahead (#1) — a snapshot taken after ``known_at`` is never read, by
    this or by ``get_asof``. Read-only; the caller owns the transaction."""
    return snapshot_taken_at_asof(conn, thesis_id, known_at) is not None


def snapshot_taken_at_asof(
    conn: psycopg.Connection, thesis_id: UUID, known_at: datetime | None
) -> datetime | None:
    """WHICH roster snapshot ``get_asof`` reads at ``known_at`` — its ``taken_at`` — or ``None`` when it
    falls back to the live roster. The label half of ``get_asof`` with the instant attached
    (``snapshot_exists_asof`` is this ``is not None``): the phantom-arm annotator reports, per re-assembled
    row, exactly which roster it ran on. The SAME predicate and the SAME pick as ``get_asof`` —
    ``taken_at <= COALESCE(known_at, now())``, ``ORDER BY taken_at DESC, id DESC`` — so the label can never
    name a different snapshot than the one the assembly read. Read-only; the caller owns the transaction.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT taken_at FROM basket_snapshot "
            "WHERE thesis_id = %s AND taken_at <= COALESCE(%s, now()) "
            "ORDER BY taken_at DESC, id DESC LIMIT 1",
            (thesis_id, known_at),
        )
        row = cur.fetchone()
    return row["taken_at"] if row is not None else None


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


def basket_sizes_asof(
    conn: psycopg.Connection, thesis_ids: Sequence[UUID], known_at: datetime | None
) -> dict[UUID, int]:
    """Batch form of ``basket_size_asof``: ``thesis_id -> roster count`` as known at ``known_at`` in
    ONE query. Uses the identical ``taken_at <= COALESCE(known_at, now())`` predicate and
    ``taken_at DESC, id DESC`` pick as ``basket_size_asof`` / ``get_asof`` (no lookahead, #1). Theses
    with no qualifying snapshot are absent so the caller falls back to the live roster count
    (``live_fallback``). Read-only; the caller owns the transaction."""
    ids = list(dict.fromkeys(thesis_ids))
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (thesis_id) thesis_id, jsonb_array_length(members) AS n "
            "FROM basket_snapshot "
            "WHERE thesis_id = ANY(%s) AND taken_at <= COALESCE(%s, now()) "
            "ORDER BY thesis_id, taken_at DESC, id DESC",
            (ids, known_at),
        )
        return {r["thesis_id"]: r["n"] for r in cur.fetchall()}


def _member_to_snapshot(ordinal: int, m: BasketMember) -> dict[str, Any]:
    """One basket member as a JSON-native snapshot object — the SAME key set and value types the deploy
    seed (migration 0043) builds from the ``basket_member`` columns, so ``md5(members::text)`` agrees
    across the seed and the on-promote write (jsonb normalizes key order). Values are JSON-native (UUID ->
    str) so ``Json`` can serialize them — the ``set_term_set`` ``model_dump(mode="json")`` idiom. Every
    field ``_row_to_basket_member`` reads is present, so ``get_asof`` round-trips a byte-identical member.

    ``context_role`` is the ONE key emitted only when SET (migration 0069). The dedup hashes
    ``md5(members::text)``, so a ``"context_role": null`` added to every object would change every thesis's
    hash and each thesis's next unchanged re-promote would append a redundant snapshot row. Omitting the unset
    key keeps a role-less roster's hash byte-identical to the one the seed and the earlier writes stored;
    ``_row_to_basket_member`` reads it with ``.get``, so a snapshot without the key hydrates as unset.
    """
    out: dict[str, Any] = {
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
    if m.context_role is not None:
        out["context_role"] = m.context_role.value
    return out


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


class CountingCatalystRemoval(RuntimeError):
    """V2 W6b, the WIPE GUARD: a new catalyst list would drop the calendar row of an entry that still counts
    toward the call (``fact_thesis_catalyst``, keyed by the row's id). Nothing was written. The message names
    each entry in plain words; the list route answers 409 with it (operator decision N10 (a): refused until the
    switch is off, never switched off silently)."""

    def __init__(self, labels: list[str]) -> None:
        self.labels = labels
        super().__init__(
            " ".join(
                f"“{label}” counts toward the call. Turn that off before removing it."
                for label in labels
            )
        )


def set_catalysts(
    conn: psycopg.Connection,
    thesis_id: UUID,
    catalysts: list[Catalyst],
    *,
    tenant_id: UUID,
    user_id: UUID | None = None,
) -> None:
    """Persist the thesis's narrative catalysts (the card's catalyst SURFACE — upcoming binary events,
    display objects; the per-security CONVICTION facts live in ``fact_catalyst`` via the ratify path).
    The SOLE writer of the ``catalyst`` child table — deliberately NOT part of ``upsert``, so a promote
    that doesn't carry catalysts structurally CANNOT wipe them (the ``set_term_set`` wipe-guard).
    Full-list replace (the operator edits the list as a whole); the caller owns the transaction.

    **Ids are kept (V2 W6b).** Each row is written with the id it carries, so a caller that sends back the ids it
    read keeps every row's identity across saves (the list route mints a fresh id only for a row sent without one,
    or with an id that is not on this card). An id that belongs to ANOTHER card raises ``ValueError``.

    **The wipe guard (V2 W6b).** A row is a counting entry's identity (``fact_thesis_catalyst.entry_id``). A list
    that would drop the row of an entry whose newest version counts raises ``CountingCatalystRemoval`` BEFORE
    anything is written; once the entry is switched off its row can be removed (its versions stay: history is
    never deleted). Every writer of the table comes through here (the list route, the SPAC Radar's "+ vote
    catalyst" through that route, the seed's boot write), so every one is guarded. A label edit on a counting row
    appends one label-only version through the store's sole writer (``thesis_catalyst_repo.append_version``, by
    ``user_id``), in the same transaction; its refusal (``CountingEntryError``) rolls the whole list write back.
    """
    ids = [c.id for c in catalysts]
    if len(set(ids)) != len(ids):
        raise ValueError("a catalyst id appears more than once in the list")
    current = {r["entry_id"]: r for r in thesis_catalyst_repo.current(conn, thesis_id)}
    counting = {eid for eid, r in current.items() if r["counts"]}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, label FROM catalyst WHERE thesis_id = %s ORDER BY ordinal", (thesis_id,)
        )
        existing = {r["id"]: r["label"] for r in cur.fetchall()}
        if ids:
            cur.execute(
                "SELECT 1 FROM catalyst WHERE id = ANY(%s) AND thesis_id <> %s LIMIT 1",
                (ids, thesis_id),
            )
            if cur.fetchone() is not None:
                raise ValueError("a catalyst id belongs to another card")
    kept = set(ids)
    dropped = [eid for eid in existing if eid in counting and eid not in kept]
    if dropped:
        raise CountingCatalystRemoval([existing[eid] for eid in dropped])
    with conn.transaction(), conn.cursor() as cur:
        cur.execute("DELETE FROM catalyst WHERE thesis_id = %s", (thesis_id,))
        for i, c in enumerate(catalysts):
            cur.execute(
                """INSERT INTO catalyst
                   (id, tenant_id, thesis_id, label, kind, when_date, when_label, ordinal)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                (c.id, tenant_id, thesis_id, c.label, c.kind, c.when_date, c.when_label, i),
            )
        for c in catalysts:
            prev = current.get(c.id)
            if c.id in counting and prev is not None and prev["label"] != c.label.strip():
                thesis_catalyst_repo.append_version(
                    conn,
                    thesis_id,
                    c.id,
                    counts=True,
                    grade=prev["grade"],
                    security_ids=prev["security_ids"],
                    citation=prev["citation"],
                    relevant_until=prev["relevant_until"],
                    label=c.label,
                    user_id=user_id,
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


def entries_for(
    conn: psycopg.Connection, thesis_ids: Iterable[UUID]
) -> dict[UUID, tuple[list[Catalyst], list[KillCriterion], list[ExcludedName]]]:
    """``thesis_id -> (catalysts, kill criteria, exclusions)`` as they stand NOW, in the order ``get`` loads them
    (three SELECTs for any number of cards; a card with none is present with empty lists, an unknown id is
    absent). Read-only. These lists are written by ``set_catalysts`` / ``set_kill_criteria`` /
    ``set_exclusions``, none of which writes a ``basket_snapshot``, so a reader that caches a roster by the
    snapshot generation reads them here, live, instead of from the cached ``Thesis``."""
    ids = list(thesis_ids)
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM thesis WHERE id = ANY(%s)", (ids,))
        known = [r["id"] for r in cur.fetchall()]
        cur.execute(
            "SELECT * FROM catalyst WHERE thesis_id = ANY(%s) ORDER BY thesis_id, ordinal", (ids,)
        )
        cats = cur.fetchall()
        cur.execute(
            "SELECT * FROM kill_criterion WHERE thesis_id = ANY(%s) ORDER BY thesis_id, ordinal",
            (ids,),
        )
        kills = cur.fetchall()
        cur.execute(
            "SELECT * FROM thesis_exclusion WHERE thesis_id = ANY(%s) "
            "ORDER BY thesis_id, recorded_at, id",
            (ids,),
        )
        excl = cur.fetchall()
    out: dict[UUID, tuple[list[Catalyst], list[KillCriterion], list[ExcludedName]]] = {
        i: ([], [], []) for i in known
    }
    for c in cats:
        out[c["thesis_id"]][0].append(_row_to_catalyst(c))
    for k in kills:
        out[k["thesis_id"]][1].append(_row_to_kill(k))
    for e in excl:
        out[e["thesis_id"]][2].append(
            ExcludedName(security_id=e["security_id"], ticker=e["ticker"], reason=e["reason"])
        )
    return out


def list_all(
    conn: psycopg.Connection, *, workspace: WorkspaceScope, include_archived: bool = False
) -> list[Thesis]:
    """Every thesis, each fully loaded, ordered by name. ARCHIVED theses are EXCLUDED by default —
    the Board's default list, the workbench picker, and the daily cron's walk all skip them without
    asking (an archived test basket stops accumulating calls-of-record; the Scoreboard's data stays
    clean). ``include_archived=True`` is the explicit, reversible filter (the Board's collapsed
    "Archived" section). The API projects these to lightweight summaries; at this scale a full load
    per thesis is fine (optimize if the universe grows).

    ``workspace`` is REQUIRED: the caller's workspace (the API) or ``WORKSPACE_ANY`` (the nightly's walk of every
    workspace, the CLIs)."""
    clause, params = workspace_filter(workspace)
    where = "TRUE" if include_archived else "archived_at IS NULL"
    with conn.cursor() as cur:
        cur.execute(f"SELECT id FROM thesis WHERE {where}{clause} ORDER BY name", params)
        ids = [r["id"] for r in cur.fetchall()]
    return _get_many(conn, ids, workspace=workspace)


def created_at_for(
    conn: psycopg.Connection, thesis_ids: Iterable[UUID], *, workspace: WorkspaceScope
) -> dict[UUID, datetime]:
    """``thesis_id -> created_at`` (an aware instant — the column is ``timestamptz``) for the ids given;
    an unknown id is simply absent. The domain ``Thesis`` deliberately does not carry ``created_at``
    (it is a row fact, not part of the spine the promote payload owns — ``upsert`` never names it, so
    it is stable for the life of the row). This narrow read exists for the ONE question that needs it:
    did the thesis EXIST on a past night (``pipeline.backfill``'s existence gate, and the repair script
    that shares its rule)? Read-only. ``workspace`` as ``list_all`` (an id in another workspace is absent).
    """
    ids = list(thesis_ids)
    if not ids:
        return {}
    clause, params = workspace_filter(workspace)
    with conn.cursor() as cur:
        cur.execute(f"SELECT id, created_at FROM thesis WHERE id = ANY(%s){clause}", (ids, *params))
        return {r["id"]: r["created_at"] for r in cur.fetchall()}


def lifespans(conn: psycopg.Connection, *, workspace: WorkspaceScope) -> list[dict]:
    """``{id, created_at, archived_at}`` for EVERY thesis, archived ones included (all tenants, like the
    cron's walk) — the per-(thesis, night) coverage read's roster (M1, ``pipeline/night_coverage.py``):
    which theses a given night's pass was due to record. Both columns are ``timestamptz`` (aware);
    ``archived_at`` is ``NULL`` for a live thesis. Read-only. ``workspace`` as ``list_all``."""
    clause, params = workspace_filter(workspace)
    with conn.cursor() as cur:
        cur.execute(
            f"SELECT id, created_at, archived_at FROM thesis WHERE TRUE{clause} ORDER BY id", params
        )
        return list(cur.fetchall())


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


def set_meta_index(conn: psycopg.Connection, thesis_id: UUID, on: bool) -> None:
    """Mark / unmark a thesis as a META INDEX card — the SOLE writer of ``meta_index`` (``upsert`` never names
    the column, so a full-replace promote can never flip it: the ``set_archived`` guard). On, none of the
    card's names count toward the call (``domain.thesis.voting_thesis``); off, it is a normal card again on the
    next read. Nothing is deleted either way. Idempotent. The caller owns the transaction."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE thesis SET meta_index = %s, updated_at = now() WHERE id = %s",
            (bool(on), thesis_id),
        )


def upsert(conn: psycopg.Connection, thesis: Thesis) -> None:
    """Insert or update a thesis. The basket is replaced (the promote payload owns it); evidence is
    APPEND-ONLY (new rows added, existing ones never modified or removed). Catalysts and kill
    criteria are NOT touched here — ``set_catalysts`` / ``set_kill_criteria`` are their sole writers
    (the ``set_term_set`` pattern: a promote that doesn't carry them structurally CANNOT wipe them;
    before the authoring surfaces existed, the old full-replace here wiped [] over [] silently on
    every narrative edit — the wipe-trap's third instance, caught before it had rows to lose).
    The caller owns the transaction (commit/rollback).

    THE FOREIGN-ID GUARD: when a thesis with this id already exists under a DIFFERENT tenant than
    ``thesis.tenant_id``, nothing is written and ``ThesisNotInWorkspace`` is raised (the promote answers 404), so a
    payload carrying another workspace's thesis id can never overwrite it.
    """
    row = thesis_to_row(thesis)
    tid = thesis.id
    tenant = row["tenant_id"]
    with conn.cursor() as cur:
        cur.execute("SELECT tenant_id FROM thesis WHERE id = %s FOR UPDATE", (tid,))
        existing = cur.fetchone()
        if existing is not None and existing["tenant_id"] != tenant:
            raise ThesisNotInWorkspace(f"thesis {tid} is not in workspace {tenant}")
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
                    segment, thesis_fit, conviction, surfaced_terms, authored_by, signed_off,
                    context_role)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
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
                    m.context_role.value if m.context_role is not None else None,
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
