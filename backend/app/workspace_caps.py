"""Workspace caps (V2 W7c, docs/AUTH.md §Workspace caps): the ONE rule for "may this workspace add this?".

A workspace is a platform cost: the platform ingests data for every company on any live card, and the discovery
loop reads every term on a card. Three caps on ``tenant`` (migration 0203) bound it, each NULL = no cap:

- ``cap_live_cards``     — live (non-archived) cards in the workspace;
- ``cap_companies``      — DISTINCT companies (``security_id``) over every ``basket_member`` row of the workspace's
  live cards: silent links (Context, ETFs, Bench) and the Discovered pen included, a company on two cards counted
  once;
- ``cap_terms_per_card`` — search terms (``thesis.term_set`` entries) on one card.

New workspaces get the ``Settings.workspace_cap_*_default`` values (``pipeline.users``); the steward changes one
workspace's caps with ``python -m pipeline.users caps``. There is no route for it.

THE RULE (``refused``): a request is refused only when a count would end ABOVE its cap AND above the count before the
request. So a workspace already over a cap (the founding workspace has far more than the defaults, and keeps NULL caps
unless the operator sets them) still reads, edits, removes, archives and re-promotes; a save that keeps or shrinks a
card's basket or terms is never refused. Only a request that ADDS past a cap is.

ONE LOCK: ``check`` takes a per-workspace advisory TRANSACTION lock before it counts, and the route writes and commits
in that same transaction, so two racing adds cannot both pass the last slot (the second one waits, then counts the
first one's committed row). The lock is released by the commit or the rollback.

A refusal is a 409 whose ``detail`` is one plain sentence naming the cap, the count and the limit; it is raised BEFORE
any write, so nothing is written and nothing is dropped: the whole request is refused (recall is sacred, invariant
#9). The steward is not exempt: caps belong to a workspace, not a caller (the steward's own workspace keeps NULL caps).

Not in ``domain/``: this module reads the database and is no call input, and ``app/`` is outside the editions engine
fingerprint.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

import psycopg
from fastapi import HTTPException

# The advisory-lock namespace for the per-workspace caps lock ("ADW"); the second key is ``hashtext`` of the
# workspace id. Distinct from the company write lock, the run locks and the flag lock (db/write_lock.py,
# pipeline/run_lock.py, platform_values/flags.py).
CAPS_LOCK_NAMESPACE = 0x414457

# The status of every cap refusal: the request conflicts with the workspace's current state.
CAP_STATUS = 409


class Adding(StrEnum):
    """What the request adds, for the sentence the user reads."""

    SAVE = "save"  # a promote (the Workbench editor's save, or a new card)
    RESTORE = "restore"  # an unarchive
    FUND = "fund"  # POST /radar/funds/attach
    COMPANY = "company"  # POST /radar/spac/attach
    TERMS = "terms"  # PUT .../terms/edit, POST .../terms


@dataclass(frozen=True)
class Caps:
    live_cards: int | None
    companies: int | None
    terms_per_card: int | None


@dataclass(frozen=True)
class Usage:
    """A workspace's counts: live cards, and the distinct companies across them."""

    live_cards: int
    companies: int


def refused(*, cap: int | None, before: int, after: int) -> bool:
    """THE rule: refuse only a count that ends above its cap AND above where it was. NULL = no cap."""
    return cap is not None and after > cap and after > before


def read_caps(conn: psycopg.Connection, workspace: UUID) -> Caps:
    """The workspace's three caps (all NULL for an unknown tenant: nothing to enforce)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT cap_live_cards, cap_companies, cap_terms_per_card FROM tenant WHERE id = %s",
            (workspace,),
        )
        row = cur.fetchone()
    if row is None:
        return Caps(None, None, None)
    return Caps(row["cap_live_cards"], row["cap_companies"], row["cap_terms_per_card"])


def lock(conn: psycopg.Connection, workspace: UUID) -> None:
    """Take the workspace's caps lock for the rest of this transaction (waits, bounded by the lock timeout)."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(%s, hashtext(%s::text))",
            (CAPS_LOCK_NAMESPACE, str(workspace)),
        )


def _live_cards(conn: psycopg.Connection, workspace: UUID) -> set[UUID]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM thesis WHERE tenant_id = %s AND archived_at IS NULL", (workspace,)
        )
        return {r["id"] for r in cur.fetchall()}


def _companies(
    conn: psycopg.Connection, workspace: UUID, *, excluding_thesis: UUID | None
) -> set[UUID]:
    """Distinct companies over the workspace's live cards' basket rows, optionally without one card."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT bm.security_id FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id "
            "WHERE t.tenant_id = %s AND t.archived_at IS NULL AND bm.security_id IS NOT NULL "
            "AND t.id IS DISTINCT FROM %s",
            (workspace, excluding_thesis),
        )
        return {r["security_id"] for r in cur.fetchall()}


def _card_companies(conn: psycopg.Connection, workspace: UUID, thesis_id: UUID) -> set[UUID]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT bm.security_id FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id "
            "WHERE t.id = %s AND t.tenant_id = %s AND bm.security_id IS NOT NULL",
            (thesis_id, workspace),
        )
        return {r["security_id"] for r in cur.fetchall()}


def _card_terms(conn: psycopg.Connection, workspace: UUID, thesis_id: UUID) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT coalesce(jsonb_array_length(term_set), 0) AS n FROM thesis "
            "WHERE id = %s AND tenant_id = %s",
            (thesis_id, workspace),
        )
        row = cur.fetchone()
    return row["n"] if row is not None else 0


def usage(conn: psycopg.Connection, workspace: UUID) -> Usage:
    """The workspace's live cards and distinct companies on them (read-only; the CLI prints it)."""
    return Usage(
        live_cards=len(_live_cards(conn, workspace)),
        companies=len(_companies(conn, workspace, excluding_thesis=None)),
    )


def _plural(n: int, word: str, words: str | None = None) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {words or word + 's'}"


def _cards_sentence(adding: Adding, cap: int, before: int, after: int) -> str:
    if adding is Adding.RESTORE:
        return (
            f"Restoring this card would make {_plural(after, 'live card')}; this workspace's limit is "
            f"{cap}. Archive another card first."
        )
    return (
        f"This workspace can have at most {_plural(cap, 'live card')} and has {before}. "
        "Archive a card to start another."
    )


def _companies_sentence(adding: Adding, cap: int, before: int, after: int) -> str:
    put = f"would put {_plural(after, 'company', 'companies')} on your live cards"
    limit = f"this workspace's limit is {cap}."
    if adding is Adding.RESTORE:
        return f"Restoring this card {put}; {limit}"
    if adding is Adding.FUND:
        return f"Adding this fund {put}; {limit}"
    if adding is Adding.COMPANY:
        return f"Adding this company {put}; {limit}"
    return f"Saving {put}; {limit} Remove {after - max(cap, before)} or archive a card."


def _terms_sentence(cap: int, before: int, after: int, *, at_least: bool) -> str:
    would = f"would have at least {after}" if at_least else f"would have {after}"
    return (
        f"A card can carry at most {_plural(cap, 'search term')}; this one {would}. "
        f"Remove {after - max(cap, before)}."
    )


def check(
    conn: psycopg.Connection,
    workspace: UUID,
    *,
    thesis_id: UUID,
    adding: Adding,
    live_after: bool | None = None,
    company_ids_after: Collection[UUID] | None = None,
    terms_after: int | None = None,
    at_least: bool = False,
) -> None:
    """Take the workspace's caps lock, then refuse (409, a plain sentence) when this request would raise a count
    past its cap. The caller writes and commits in THIS transaction afterwards (the lock holds until then).

    ``thesis_id`` is the card being written (a new card's id is not stored yet). ``live_after``: whether the card
    is live after the write (None = as it is now: a promote of an existing card never changes it). ``company_ids_after``:
    the card's companies after the write (None = unchanged). ``terms_after``: the card's term count after the write
    (None = terms untouched); ``at_least`` words a lower bound (``POST .../terms`` before its model runs).
    """
    lock(conn, workspace)
    caps = read_caps(conn, workspace)
    if caps.live_cards is None and caps.companies is None and caps.terms_per_card is None:
        return
    live = _live_cards(conn, workspace)
    live_now = thesis_id in live
    live_then = live_now if live_after is None else live_after
    if caps.live_cards is not None:
        before = len(live)
        after = before - int(live_now) + int(live_then)
        if refused(cap=caps.live_cards, before=before, after=after):
            raise HTTPException(CAP_STATUS, _cards_sentence(adding, caps.live_cards, before, after))
    if caps.companies is not None:
        others = _companies(conn, workspace, excluding_thesis=thesis_id)
        stored = _card_companies(conn, workspace, thesis_id)
        own_now = stored if live_now else set()
        # a restore brings the card's stored companies back; an archived card's companies never count
        own_then = (
            (set(company_ids_after) if company_ids_after is not None else stored)
            if live_then
            else set()
        )
        before = len(others | own_now)
        after = len(others | own_then)
        if refused(cap=caps.companies, before=before, after=after):
            raise HTTPException(
                CAP_STATUS, _companies_sentence(adding, caps.companies, before, after)
            )
    if caps.terms_per_card is not None and terms_after is not None:
        before = _card_terms(conn, workspace, thesis_id)
        if refused(cap=caps.terms_per_card, before=before, after=terms_after):
            raise HTTPException(
                CAP_STATUS,
                _terms_sentence(caps.terms_per_card, before, terms_after, at_least=at_least),
            )
