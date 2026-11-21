"""THE CARD COUNT (V2 W4a): for a set of companies, how many live cards hold each one, and whether it sits only in
a Discovered pen. ``docs/PLATFORM_VALUES.md`` ("The card count").

This is the ONE read in the platform-value work that crosses workspaces: it looks at every workspace's baskets.
It therefore answers exactly two numbers per company and nothing else:

- ``cards``: how many live (non-archived) cards hold the company in any basket row, any link, the Discovered pen
  and the silent links (Context, ETFs, Bench) included (approved D5 (a)). One card holding the company in two rows
  counts once;
- ``pen_only``: every one of those rows is the Discovered link (approved B5 (a)). A row with no link, or in any
  other link, makes it False.

No thesis id, thesis name, workspace id, user or link label leaves this function, and it logs nothing: the SQL
aggregates inside the database, and only ``(security_id, cards, pen_only)`` comes back. A company on no live card
is ABSENT from the answer (the caller decides what that means: the queue shows it as 0 cards).

Steward-side only. Its callers are fixed by ``tests/app/test_card_count_guard.py``: today the steward-only router
``app/routers/admin_platform_values.py`` (the queue's order); W3's backfill may reuse it for its order (add it to
that guard in the same change). Read-only; one statement whatever the number of ids.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import NamedTuple
from uuid import UUID

import psycopg

# The Discovered pen's link label, as the chain draft writes it (``workbench/chain_draft.py``).
DISCOVERED = "Discovered"


class CardCount(NamedTuple):
    """Two numbers for one company: ``cards`` (live cards holding it) and ``pen_only`` (only in a Discovered pen)."""

    cards: int
    pen_only: bool


def card_counts(conn: psycopg.Connection, security_ids: Iterable[UUID]) -> dict[UUID, CardCount]:
    """``{security_id: CardCount}`` for every id on at least one live card, across every workspace, in ONE query.
    An id on no live card is absent. Archived cards never count. Read-only."""
    ids = list(dict.fromkeys(security_ids))
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT bm.security_id, count(DISTINCT bm.thesis_id) AS cards, "
            "bool_and(bm.segment IS NOT DISTINCT FROM %(pen)s) AS pen_only "
            "FROM basket_member bm JOIN thesis t ON t.id = bm.thesis_id "
            "WHERE t.archived_at IS NULL AND bm.security_id = ANY(%(ids)s) "
            "GROUP BY bm.security_id",
            {"pen": DISCOVERED, "ids": ids},
        )
        rows = cur.fetchall()
    return {
        r["security_id"]: CardCount(cards=int(r["cards"]), pen_only=bool(r["pen_only"]))
        for r in rows
    }


__all__ = ["DISCOVERED", "CardCount", "card_counts"]
