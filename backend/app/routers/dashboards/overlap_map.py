"""The Overlap Map dashboard's API (``docs/dashboards/overlap-map.md``): one GET over the cards counted at the
as-of dial, the companies they share, and the bubble layout. It READS the counted cards with their rosters then
(``dashboards.baskets.cards_as_of``) and the master's identity (``securities.master.identity_for``), and hands
both to the pure compute in ``dashboards/overlap_map.py``. The whole payload, layout included, is held in the
shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7) per (clock, Context / ETFs /
Bench toggle, as-of). Cards are referenced by index inside ``companies`` and ``pairs``. Its wire models live
here, not in ``app/schemas_api.py`` (rule 8)."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn, get_workspace
from dashboards.baskets import cards_as_of
from dashboards.cache import cached
from dashboards.overlap_map import OverlapMapResult
from dashboards.overlap_map import overlap_map as compute_overlap_map
from domain.market_time import serve_view
from domain.text_types import AsofDate
from securities import master

router = APIRouter(prefix="/overlap-map")

SLUG = "overlap-map"


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class OverlapCardOut(_Wire):
    """A counted card. ``companies`` = its companies in view (each once). ``x`` / ``y`` / ``r`` = its bubble in the
    box (``box_w`` x ``box_h``); ``x`` and ``y`` are ``null`` for a card sharing no company with any other
    (``shares_nothing``), which the page lists in its own strip; ``r`` is its size either way."""

    thesis_id: UUID
    name: str
    companies: int
    x: float | None
    y: float | None
    r: float
    shares_nothing: bool


class OverlapPairOut(_Wire):
    """Two cards (indexes into ``cards``, ``a`` < ``b``) sharing ``shared`` companies in view."""

    a: int
    b: int
    shared: int


class OverlapGroupsOut(_Wire):
    """The groups when cards are joined at ``threshold`` or more shared companies: the size of each group of 2+
    cards (largest first) and how many cards have no line."""

    threshold: int
    sizes: list[int]
    no_line: int


class OverlapCompanyCardOut(_Wire):
    """One company's place on one card. ``card`` = an index into ``cards``; ``ticker`` = its ticker there (the
    Cockpit's ``?name=`` key); ``links`` = its links there (basket order; ``null`` = no link); ``silent`` = every
    row of it there is Context, ETFs or Bench; ``context_role`` = the operator's role tag there, if set.
    """

    card: int
    ticker: str
    links: list[str | None]
    silent: bool
    context_role: str | None


class OverlapCompanyOut(_Wire):
    """A company in view, once. ``supersector`` / ``business_type`` = today's master (``null`` = unclassified)."""

    security_id: UUID
    ticker: str
    name: str | None
    supersector: str | None
    business_type: str | None
    rows: list[OverlapCompanyCardOut]


class OverlapMapOut(_Wire):
    """The whole page. The counts are the work shown on its quiet line: the live cards, the cards counted at the
    as-of (``cards_not_yet_created`` were created after it; ``roster_fallback_cards`` have no roster snapshot by
    then, so today's roster stands in), every basket row on those cards, the Context / ETFs / Bench rows left out,
    the rows in view with no resolved security (counted, never matched), the companies in view and those on 2+
    cards. ``cache`` / ``compute_ms`` = whether the payload came from the shared dashboard cache and how long it
    took when computed."""

    asof: date
    include_silent: bool
    clock: Literal["record", "public"]
    box_w: float
    box_h: float
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int
    companies_in_view: int
    companies_shared: int
    cards: list[OverlapCardOut]
    pairs: list[OverlapPairOut]
    groups: list[OverlapGroupsOut]
    companies: list[OverlapCompanyOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


def read_overlap(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    include_silent: bool,
    *,
    workspace: UUID,
) -> OverlapMapResult:
    """The read the cache holds: the counted cards with their rosters at the as-of, the master's identity of
    every company on them, and the layout (``dashboards/overlap_map.py``)."""
    counted = cards_as_of(conn, asof, known_at, workspace=workspace)
    sids = {m.security_id for t in counted.cards for m in t.basket if m.security_id is not None}
    identity = master.identity_for(conn, sids)
    return compute_overlap_map(
        counted.cards,
        identity,
        include_silent=include_silent,
        live_cards=counted.live_count,
        not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
    )


@router.get("", response_model=OverlapMapOut)
def overlap_map(
    asof: AsofDate = Query(
        ..., description="as-of date; only the cards that existed by then, with their rosters then"
    ),
    include_silent: bool = Query(
        False, description="add Context, ETFs and Bench memberships (left out by default)"
    ),
    conn: psycopg.Connection = Depends(get_conn),
    workspace: UUID = Depends(get_workspace),
) -> OverlapMapOut:
    """Where the cards overlap: each counted card as a bubble, the companies every pair shares (matched by
    security; a company on several links of one card counts once), the groups at each threshold of 1 to 5 shared
    companies, and every company with its place on each card and today's sector and business type. Structure only:
    no prices, no calls. Display only; a past as-of reads each card's roster then."""
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(
        conn,
        SLUG,
        (clock, include_silent),
        asof,
        lambda: read_overlap(conn, asof, known_at, include_silent, workspace=workspace),
        workspace=workspace,
    )
    return OverlapMapOut.model_validate(
        {
            **asdict(got.value),
            "asof": asof,
            "clock": clock,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
