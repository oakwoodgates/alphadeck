"""The Chain Map dashboard's API (``docs/dashboards/chain-map.md``): one card as a picture, at the as-of dial. It
READS the cards that existed by the as-of (``dashboards/baskets.py``), the picked card's roster as of the as-of
(``thesis_repo.get_asof``), each roster company's identity (the master), its 50-day side (the ``sma_position``
display member, on the public clock) and its filing-derived facts (the shared reader ``dashboards/filings.py``),
and the price flags, then hands them to the pure placement in ``dashboards/chain_map.py``. The whole page is
computed once per (card, clock, as-of) in the shared dashboard cache (``dashboards/cache.py``,
``docs/DASHBOARDS.md`` rule 7); the Context/Bench toggle is client side. Its wire models live here, not in
``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import (  # noqa: F401  (CardsRead: the picker's read; tests stub read_cards)
    CardsRead,
    display_members,
    memberships_of,
    per_company,
    picker_cards,
    price_flags,
    resolved_ids,
)
from dashboards.cache import Cached, cached
from dashboards.chain_map import (
    FILING_DAYS,
    ChainMap,
    CompanyFacts,
    MapInputs,
    chain_map,
    pct_vs_50d,
    roster_rows,
)
from dashboards.filings import read_filing_rows
from domain.enums import Authorship, ContextRole
from domain.market_time import serve_view
from repositories import thesis_repo
from securities import master

router = APIRouter(prefix="/chain-map")

SLUG = "chain-map"


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class ChainMapCardOut(_Wire):
    """A card in the picker: its name and its real links today (Context and Bench not counted)."""

    thesis_id: UUID
    name: str
    links: int


class ChainMapCardsOut(_Wire):
    """The picker: the cards counted at the as-of (the live cards that existed by then), A→Z. ``cache`` /
    ``compute_ms`` = whether the list came from the shared dashboard cache and how long it took when computed.
    """

    asof: date
    live_cards: int
    not_yet_created: int
    cards: list[ChainMapCardOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


class ChainMapFilingOut(_Wire):
    """A company's newest SEC filing in the last ``filing_days`` days (an 8-K, 13D/G, Form 4 or 4/A, a 10-Q / 10-K
    revenue print, or any other form in the EDGAR index; never a split, a catalyst or a ratified or stored fact):
    its public ``date``
    (``age_days`` before the as-of), the ``form`` as filed and its one-line ``headline``.
    """

    date: date
    age_days: int
    form: str
    headline: str


class ChainMapNodeOut(_Wire):
    """One company in one place. ``key`` is unique on the page; ``company_key`` is the company's (``security_id``,
    or ``ticker:<T>`` with no resolved security). ``ticker`` = its ticker on the card (the Cockpit's ``?name=``
    key). ``links`` = every link it sits in on the card (``null`` = no link). ``role`` = its context role (a tag on
    a chain node, the reason it stands on a rail). ``business_type`` = the master's leaf (the operator's re-tag
    winning). ``side_50d`` / ``pct_vs_50d`` = where it sits against its 50-day average (``null`` = no reading,
    or a flagged price history). ``last_filing`` = its newest SEC filing in the year (``null`` = none stored).
    ``flag`` = why its price readings are left out. ``thesis_fit`` / ``authored_by`` = the description on the
    row this node stands for and who wrote it (``null`` when there is none); ``matched_terms`` = the discovery
    terms that surfaced the name."""

    key: str
    company_key: str
    ticker: str
    name: str | None
    security_id: UUID | None
    links: list[str | None]
    role: ContextRole | None
    business_type: str | None
    side_50d: Literal["above", "below", "at"] | None
    pct_vs_50d: float | None
    last_filing: ChainMapFilingOut | None
    flag: str | None
    thesis_fit: str | None
    authored_by: Authorship | None
    matched_terms: list[str]


class ChainMapColumnOut(_Wire):
    """A column. ``kind``: ``link`` (a real link today, chain order), ``old_link`` (a roster link that is not on
    the card today), ``no_link``, or ``silent`` (the Context or Bench names with no role; ``label`` says which,
    the page shows them behind a toggle). ``descriptor`` = the link's descriptor when set."""

    key: str
    kind: Literal["link", "old_link", "no_link", "silent"]
    label: str
    descriptor: str | None
    nodes: list[ChainMapNodeOut]


class ChainMapRailOut(_Wire):
    """A rail: ``left`` (what pays and funds: payer, then financier) or ``right`` (what reads across: direct read,
    counter-case, participant, benchmark). ``nodes`` = the role-tagged companies with no row in a real link, by
    role then roster order."""

    side: Literal["left", "right"]
    label: str
    nodes: list[ChainMapNodeOut]


class ChainMapEdgeOut(_Wire):
    """One rail node's edge to the chain's bracket (the whole chain, never a link or a name): the node, its role
    and the rail it stands on."""

    node_key: str
    role: ContextRole
    side: Literal["left", "right"]


class ChainMapRoleCountOut(_Wire):
    role: ContextRole
    names: int


class ChainMapCountsOut(_Wire):
    """The work: ``companies`` = distinct roster companies; ``on_rails`` / ``untagged_silent`` = of those, the
    ones on a rail and the ones in a Context or Bench column; ``roles`` = companies per role, rail order, only
    roles that are set; ``with_side`` / ``without_side`` and ``with_filing`` / ``without_filing`` split the
    companies (an unresolved or flagged one has no side)."""

    links_today: int
    old_links: int
    roster_rows: int
    resolved_rows: int
    unresolved_rows: int
    silent_rows: int
    companies: int
    on_rails: int
    untagged_silent: int
    roles: list[ChainMapRoleCountOut]
    with_side: int
    without_side: int
    with_filing: int
    without_filing: int


class ChainMapFlaggedOut(_Wire):
    """A company on the card whose data flags reach its price readings at the as-of (the reason the Cockpit grays
    its price cells): drawn grayed with no 50-day side. ``reason`` = the flags' labels."""

    security_id: UUID
    ticker: str
    reason: str


class ChainMapOut(_Wire):
    """One card's picture. ``columns`` in chain order (real links, then a roster link that is not on the card
    today, then No link, then the silent Context/Bench columns); ``rails`` = the left rail, then the right (either
    may be empty); ``edges`` = one per rail node. ``clock``: ``record`` (the live read) or ``public`` (a past
    as-of: what was public by the end of that date). ``roster_fallback`` = no roster snapshot by the as-of: today's
    roster stands in. ``filing_days`` = how far back the last-filing glyph looks. ``cache`` / ``compute_ms`` =
    whether the page came from the shared dashboard cache and how long it took when computed."""

    asof: date
    thesis_id: UUID
    thesis_name: str
    clock: Literal["record", "public"]
    roster_fallback: bool
    columns: list[ChainMapColumnOut]
    rails: list[ChainMapRailOut]
    edges: list[ChainMapEdgeOut]
    flagged: list[ChainMapFlaggedOut]
    counts: ChainMapCountsOut
    filing_days: int
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------

# the picker is the shared basket read (``baskets.picker_cards``); ``read_cards`` is its name here (the tests
# stub it on this module)
read_cards = picker_cards


def read_page(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> ChainMap:
    """The heavy read the cache holds, built into the page: the card's roster as of the as-of
    (``baskets.cards_as_of``'s rule for one card: the live view reads today's roster, a past view the latest
    snapshot by ``known_at``, today's roster standing in before the first one), then for each resolved company its
    identity, its 50-day side (a flagged company's bars are never read) and its filing-derived rows for the year,
    all on the request's ``known_at`` + ``clock``."""
    if known_at is None:
        card, fallback = thesis_repo.get(conn, thesis_id), False
    else:
        fallback = not thesis_repo.snapshot_exists_asof(conn, thesis_id, known_at)
        card = thesis_repo.get_asof(conn, thesis_id, known_at)
    if card is None:
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    memberships = memberships_of([card])
    sids = resolved_ids(memberships)
    flags = price_flags(conn, sids, asof, known_at=known_at, clock=clock)
    identity = master.identity_for(conn, sids)
    sma = display_members(["sma_position"], "Chain Map")["sma_position"]
    sides = per_company(
        conn,
        [sid for sid in sids if sid not in flags],
        asof,
        known_at,
        clock,
        lambda pit, sid: pct_vs_50d(sma(pit, sid, asof)),
    )
    filings = read_filing_rows(conn, sids, asof, known_at, clock, days=FILING_DAYS)
    facts = {
        sid: CompanyFacts(
            name=_text(identity.get(sid, {}).get("name")),
            business_type=_text(identity.get(sid, {}).get("business_type")),
            pct_vs_50d=sides.get(sid),
            filings=filings.get(sid, ()),
        )
        for sid in sids
    }
    return chain_map(
        MapInputs(
            thesis_id=card.id,
            thesis_name=card.name,
            segments=tuple(card.segments),
            roster=roster_rows(memberships, card.basket),
            facts=facts,
            flagged=flags,
            roster_fallback=fallback,
        ),
        asof=asof,
    )


def _text(v: object) -> str | None:
    """A master identity string (``None`` and the empty string are both "not known")."""
    return str(v) if v else None


def _cards(
    conn: psycopg.Connection, asof: date, clock: Literal["record", "public"]
) -> Cached[CardsRead]:
    return cached(conn, SLUG, ("cards", clock), asof, lambda: read_cards(conn, asof))


@router.get("/cards", response_model=ChainMapCardsOut)
def chain_map_cards(
    asof: date = Query(
        ..., description="as-of date; only the cards that existed by then are listed"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> ChainMapCardsOut:
    """The card picker: the live cards that existed by the as-of, A→Z, each with its link count today. Display
    only."""
    _, clock = serve_view(asof)
    got = _cards(conn, asof, clock)
    return ChainMapCardsOut.model_validate(
        {
            "asof": asof,
            "live_cards": got.value.live_cards,
            "not_yet_created": got.value.not_yet_created,
            "cards": [asdict(c) for c in got.value.cards],
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )


@router.get("/{thesis_id}", response_model=ChainMapOut)
def chain_map_for_card(
    thesis_id: UUID,
    asof: date = Query(..., description="as-of date; nothing published after it is read"),
    conn: psycopg.Connection = Depends(get_conn),
) -> ChainMapOut:
    """One card as a picture: its links in chain order as columns with the roster's companies inside (each with
    its business type, its side of the 50-day average and the age of its newest SEC filing), and the
    companies with a context role standing on two rails beside the chain, each with one edge to the chain as a
    whole. A company whose data flags reach its price readings is drawn grayed with no 50-day side. Display only;
    a past as-of reads the roster as of that date and what was public by the end of it. 404 when the card is not
    counted at the as-of."""
    known_at, clock = serve_view(asof)
    if not any(c.thesis_id == thesis_id for c in _cards(conn, asof, clock).value.cards):
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(
        conn,
        SLUG,
        (str(thesis_id), clock),
        asof,
        lambda: read_page(conn, thesis_id, asof, known_at, clock),
    )
    return ChainMapOut.model_validate(
        {
            **asdict(got.value),
            "clock": clock,
            "filing_days": FILING_DAYS,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
