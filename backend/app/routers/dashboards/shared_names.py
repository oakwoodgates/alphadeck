"""The Shared Names dashboard's API (``docs/dashboards/shared-names.md``): one GET, a "now" view of the
companies on more than one live card. It READS the live cards (``thesis_repo.list_all``, archived excluded by
its default) and the master's company names, then hands both to the pure compute in
``dashboards/shared_names.py``. Its wire models live here, not in ``app/schemas_api.py`` (``docs/DASHBOARDS.md``
rule 8), so cutting the dashboard deletes cleanly."""

from __future__ import annotations

from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.shared_names import shared_names as compute_shared_names
from domain.enums import ContextRole
from repositories import thesis_repo
from securities import master

router = APIRouter(prefix="/shared-names")


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class SharedNameCardRefOut(_Wire):
    thesis_id: UUID
    thesis_name: str


class SharedNameChipOut(_Wire):
    """One card a company sits on. ``links`` = its links there (basket order; ``null`` = no link);
    ``silent_link`` = "Context" / "Bench" when that is its membership there; ``counts`` = it has a
    non-silent row on that card. ``ticker`` = its ticker on that card (the Cockpit's ``?name=`` key).
    """

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: list[str | None]
    silent_link: str | None
    context_role: ContextRole | None
    counts: bool


class SharedNameRowOut(_Wire):
    """A company on 2+ cards. ``opposite_sides``: it counts on one card and is tagged counter-case on a
    different card."""

    security_id: UUID
    ticker: str
    name: str | None
    card_count: int
    opposite_sides: bool
    cards: list[SharedNameChipOut]


class SharedNamePairOut(_Wire):
    card_a: SharedNameCardRefOut
    card_b: SharedNameCardRefOut
    shared_count: int
    tickers: list[str]
    security_ids: list[UUID]


class SharedNamesOut(_Wire):
    """The whole page. The counts are the work shown on its quiet line: every basket row on the live cards,
    the Context / Bench rows a ``counting_only`` read left out, and the rows with no resolved security
    (counted, not matched)."""

    counting_only: bool
    live_cards: int
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int
    companies: int
    names: list[SharedNameRowOut]
    pairs: list[SharedNamePairOut]


@router.get("", response_model=SharedNamesOut)
def shared_names(
    counting_only: bool = Query(
        False, description="drop Context and Bench memberships before both sections are computed"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> SharedNamesOut:
    """The companies on more than one live card (matched by security), the cards each sits on, and every
    pair of cards that shares a company. A "now" view of today's baskets; archived cards are excluded.
    """
    theses = thesis_repo.list_all(conn)
    names = master.names_for(
        conn, {m.security_id for t in theses for m in t.basket if m.security_id is not None}
    )
    return SharedNamesOut.model_validate(
        compute_shared_names(theses, names, counting_only=counting_only)
    )
