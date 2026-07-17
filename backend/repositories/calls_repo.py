from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Json

from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from repositories.mappers import call_to_row, row_to_call


def append(
    conn: psycopg.Connection,
    card: CallCard,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    *,
    ingest_fresh: bool | None = None,
    ingest_errors: int | None = None,
) -> UUID:
    """Append an assembled CallCard to the write-only accountability log, under ``tenant_id`` (the call of
    record lands in the thesis's tenant). NOT the read path — the API recomputes the card live from facts.
    The caller owns the transaction (commit/rollback).

    ``tenant_id`` defaults to the demo tenant (a test/seed convenience, like the ingest fns); the production
    write path (``call_for_thesis``) always passes ``thesis.tenant_id`` explicitly, so the call of record can
    never land in the wrong tenant on that path.

    ``ingest_fresh`` / ``ingest_errors`` are the run's INGEST-HEALTH provenance (cron R2b, migration 0023):
    was every name's back-half ingest clean, and how many errored. PROVENANCE only — the scoring reads never
    branch on them; they are stamped SEPARATELY from the card (never inside it, or a stale->fresh flip would
    fake a change in ``_canonical``). ``None`` = not supplied (a manual/legacy append).
    """
    row = call_to_row(card, tenant_id)
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO calls
                   (tenant_id, thesis_id, asof, state, verdict, card, ingest_fresh, ingest_errors)
               VALUES (%(tenant_id)s, %(thesis_id)s, %(asof)s, %(state)s, %(verdict)s, %(card)s,
                       %(ingest_fresh)s, %(ingest_errors)s)
               RETURNING id""",
            {
                **row,
                "card": Json(row["card"]),
                "ingest_fresh": ingest_fresh,
                "ingest_errors": ingest_errors,
            },
        )
        return cur.fetchone()["id"]


def _canonical(card: CallCard) -> str:
    """A deterministic, ORDER-INDEPENDENT serialization for the change-compare.

    The CallCard has lists whose order is not load-bearing for "did the call change" (triggers, members,
    a member's own triggers, provenance) — a pure reorder must NOT read as a change, or `record_if_changed`
    would re-append every run. So this recursively sorts dict keys AND list elements, and rounds floats so
    jsonb/IEEE repr noise (e.g. a `Provenance.detail` number round-tripped through jsonb) can't fake a diff.
    (A genuinely meaningful reorder — e.g. a member rank change — is always accompanied by a changed field,
    so sorting cannot mask it.)"""

    def norm(x: Any) -> Any:
        if isinstance(x, dict):
            return {k: norm(x[k]) for k in sorted(x)}
        if isinstance(x, list):
            return sorted(
                (norm(e) for e in x), key=lambda e: json.dumps(e, sort_keys=True, default=str)
            )
        if isinstance(x, float):
            return round(x, 9)
        return x

    return json.dumps(norm(card.model_dump(mode="json")), sort_keys=True, default=str)


def record_if_changed(
    conn: psycopg.Connection,
    card: CallCard,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    *,
    ingest_fresh: bool | None = None,
    ingest_errors: int | None = None,
) -> bool:
    """Append the call-of-record for ``(thesis, card.asof)`` ONLY if none exists for that as-of yet, or the
    latest logged one differs in substance (a canonical, order-independent compare). Returns ``True`` iff it
    appended. The caller owns the transaction.

    This is the cron's idempotent writer: a same-day re-run on UNCHANGED facts appends NOTHING (the table
    does not grow), while a GENUINE change (state / verdict / confidence / exit_by / provenance / members)
    appends EXACTLY ONE new versioned row (latest-append-per-asof wins on read). It is the only correct path
    because the ``calls`` log is immutable (the ``no_update`` trigger) and its ``(thesis_id, asof)`` index is
    non-unique — so an UPSERT is impossible; we read-compare-then-conditionally-append.

    ``ingest_fresh`` / ``ingest_errors`` (R2b) ride the WRITE only — they are NOT in ``_canonical``, so a
    stale->fresh flip on an otherwise-identical card does NOT append a spurious row (freshness is provenance
    of the run, not a change in the call). The stamp is the ingest health of the run that FIRST recorded this
    card version; a later re-run producing the identical card doesn't re-stamp (there's no new row).
    """
    prior = next((c for c in latest_for_thesis(conn, card.thesis_id) if c.asof == card.asof), None)
    if prior is not None and _canonical(prior) == _canonical(card):
        return False
    append(conn, card, tenant_id, ingest_fresh=ingest_fresh, ingest_errors=ingest_errors)
    return True


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
