"""The per-card theme breadth the dashboards share (``docs/DASHBOARDS.md`` rule 7): one
``signals.display.theme_breadth`` reading per card, with each company's closes read ONCE however many cards hold
it. Built for the Theme Leaderboard and Forming Quietly, which both show a card's breadth over its counting names.

The rules, in one place:

- **The same reading the Cockpit's basket header makes.** A member's series is ``theme_breadth.member_closes``
  (the one definition ``theme_breadth.breadth_for`` itself builds with), read through the same point-in-time view
  (``baskets.per_company``: the request's one ``known_at, clock = serve_view(asof)`` pair, invariant #1), and the
  reading is the pure ``theme_breadth.compute`` over the card's names in the order given. So a card's reading is
  what ``breadth_for`` returns over a view of just that card's names.
- **Each company once.** The closes are read per company, not per card: a company on N cards is read once.
- **Kept per company.** Inside a page compute a company's closes are a piece of the shared dashboard cache
  (``CLOSES_PIECE``), so a recompute after a basket edit reads no company and one after a company's change stamp
  reads that company alone. Outside a page compute every company is read and nothing is stored.
- **The caller picks the names.** Both pages hand in a card's counting names whose price history is not flagged
  (``baskets.price_flags``); a card with no such name has no reading (``None``).

Display only (rule 1): this module READS (the point-in-time view) and writes nothing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg

from dashboards.baskets import per_company
from signals.display import theme_breadth
from signals.display.base import DisplaySignal

# The per-company piece: one company's breadth closes, named by the lookback VALUE (the closes pieces'
# convention, ``closes.420d``). It carries the breadth's own prefix because the stored value is the breadth's own
# series (the closes in bar order, as ``theme_breadth.member_closes`` reads them), not the ``{day: close}`` map
# the ``closes.<N>d`` pieces hold: a page that reads ``closes.<N>d`` must never be handed this shape.
CLOSES_PIECE = f"theme-breadth.closes.{theme_breadth.LOOKBACK_DAYS}d"


def read_breadths(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    card_names: Mapping[UUID, Sequence[UUID]],
    *,
    workspace: UUID,
) -> dict[UUID, DisplaySignal | None]:
    """``{thesis_id: its theme-breadth reading}`` for each card in ``card_names`` (a card's names in the order
    its reading counts them): ``theme_breadth.compute`` over each name's ``theme_breadth.member_closes``, the
    reading ``theme_breadth.breadth_for`` returns over a view of just those names. A card with no names has no
    reading (``None``). Every company in any card is read once (``baskets.per_company``, a kept piece).
    """
    sids = list(dict.fromkeys(sid for names in card_names.values() for sid in names))
    closes: dict[UUID, tuple[float, ...]] = (
        per_company(
            conn,
            sids,
            asof,
            known_at,
            clock,
            # a tuple: the stored series is shared between cards, pages and recomputes
            lambda pit, sid: tuple(theme_breadth.member_closes(pit, sid)),
            workspace=workspace,
            piece=CLOSES_PIECE,
        )
        if sids
        else {}
    )
    return {
        thesis_id: theme_breadth.compute([list(closes[sid]) for sid in names]) if names else None
        for thesis_id, names in card_names.items()
    }
