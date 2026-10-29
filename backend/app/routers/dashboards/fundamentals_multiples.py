"""The Fundamentals Multiples dashboard's API (``docs/dashboards/fundamentals-multiples.md``): one card's revenue
series, name by name and link by link, at the as-of dial. It READS the cards that existed by the as-of
(``dashboards/baskets.py``), the picked card's roster as of the as-of (``thesis_repo.get_asof``), each resolved
company's as-of ``fundamentals_facts`` through the Cockpit's point-in-time view (``baskets.per_company``, on the
public clock) and the active data-quality set-asides (read only), then hands them to the pure windowing in
``dashboards/fundamentals_multiples.py``. The whole page is computed once per (card, clock, as-of) in the shared
dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7); the Context/Bench toggle is client side.
Its wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from collections import Counter
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
    memberships_of,
    per_company,
    picker_cards,
    resolved_ids,
)
from dashboards.cache import Cached, cached
from dashboards.fundamentals_multiples import (
    MIN_MEDIAN_NAMES,
    QUARTERS,
    REVENUE_METRIC,
    THIN_BELOW,
    CompanyRead,
    Multiples,
    MultiplesInputs,
    fundamentals_multiples,
)
from domain.market_time import serve_view
from repositories import fundamentals_set_aside_repo, thesis_repo
from signals.base import PointInTimeData

router = APIRouter(prefix="/fundamentals-multiples")

SLUG = "fundamentals-multiples"


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class MultiplesCardOut(_Wire):
    """A card in the picker: its name and its real links today (Context and Bench not counted)."""

    thesis_id: UUID
    name: str
    links: int


class MultiplesCardsOut(_Wire):
    """The picker: the cards counted at the as-of (the live cards that existed by then), A→Z. ``cache`` /
    ``compute_ms`` = whether the list came from the shared dashboard cache and how long it took when computed.
    """

    asof: date
    live_cards: int
    not_yet_created: int
    cards: list[MultiplesCardOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


class MultiplesBarOut(_Wire):
    """One quarterly revenue print: placed by its ``period_end`` (``quarter`` = its calendar quarter, ``2026-Q2``),
    dated by the filing that made it public (``filed``; ``accession`` and its EDGAR index ``url``). ``basis`` =
    ``native`` (a standalone quarter) or ``derived`` (the fiscal year minus nine months). ``first_knowable`` is set
    only when the value was public earlier than ``filed`` (a re-report), with its accession. ``yoy`` = percent
    change against the same period a year earlier (``prior_period_end``); ``null`` = no such row in the read.
    """

    quarter: str
    period_end: date
    value: float
    basis: str
    fiscal_period: str | None
    fiscal_year: int | None
    filed: date
    accession: str | None
    url: str | None
    first_knowable: date | None
    first_knowable_accession: str | None
    yoy: float | None
    prior_period_end: date | None


class MultiplesLastPrintOut(_Wire):
    """A name's newest print by period end (even one older than the axis): ``days_before`` = how many days before
    the as-of it was filed; ``yoy`` = its percent change on the same period a year earlier."""

    quarter: str
    period_end: date
    filed: date
    days_before: int
    yoy: float | None


class MultiplesCompanyOut(_Wire):
    """One roster company, once. ``company_key`` is the company's (``security_id``, or ``ticker:<T>`` with no
    resolved security); ``ticker`` = its ticker on the card (the Cockpit's ``?name=`` key). ``silent`` = it sits
    only in Context or Bench (never in a median). ``state``: ``series`` (five or more bars on the axis), ``thin``
    (one to four), ``stale`` (a series that ends before the axis: no bar), ``none`` (no revenue stored) or
    ``unresolved`` (no resolved security). ``periods`` = revenue period ends stored on or before the as-of;
    ``set_aside`` = revenue rows an active data-quality set-aside covers."""

    company_key: str
    ticker: str
    name: str | None
    security_id: UUID | None
    silent: bool
    state: Literal["series", "thin", "stale", "none", "unresolved"]
    periods: int
    bars: list[MultiplesBarOut]
    last_print: MultiplesLastPrintOut | None
    set_aside: int


class MultiplesMedianPointOut(_Wire):
    """One axis quarter of a median line: ``median`` (percent; ``null`` = not drawn, fewer than the needed names)
    and ``n`` = the names that had a year-over-year change that quarter."""

    quarter: str
    median: float | None
    n: int


class MultiplesMedianOut(_Wire):
    """A median line over the axis. ``counting`` = the counting names it is over; each point's ``n`` is how many
    of them contributed. A line over exactly one name is that name's own dots."""

    counting: int
    points: list[MultiplesMedianPointOut]


class MultiplesRowOut(_Wire):
    """A row of names. ``kind``: ``link`` (a real link today, chain order), ``old_link`` (a roster link that is not
    on the card today), ``no_link``, or ``silent`` (the Context or Bench names; ``label`` says which; no median).
    ``companies`` = company keys in roster order; ``descriptor`` = the link's descriptor when set.
    """

    key: str
    kind: Literal["link", "old_link", "no_link", "silent"]
    label: str
    descriptor: str | None
    companies: list[str]
    median: MultiplesMedianOut | None


class MultiplesCountsOut(_Wire):
    """The work: roster rows split resolved / unresolved / silent; ``companies`` = distinct roster companies, of
    which ``counting`` sit in a real, old or No-link row and ``silent_companies`` only in Context or Bench;
    ``with_series`` / ``without_series`` split them by whether any revenue period end is stored; ``thin`` and
    ``stale`` are of the ones with a series; ``set_aside`` = revenue rows covered by an active set-aside on the
    card's names."""

    links_today: int
    old_links: int
    roster_rows: int
    resolved_rows: int
    unresolved_rows: int
    silent_rows: int
    companies: int
    counting: int
    silent_companies: int
    with_series: int
    without_series: int
    thin: int
    stale: int
    set_aside: int


class MultiplesOut(_Wire):
    """One card's revenue series. ``axis`` = the calendar quarters drawn, oldest first (twelve ending at the newest
    quarter any roster name's latest print falls in; empty when no name has a print); ``card_line`` = the median
    over the card's counting names; ``rows`` in chain order (real links, then a roster link that is not on the card
    today, then No link, then Context, then Bench); ``companies`` = every roster company once, referenced by key
    from the rows. ``clock``: ``record`` (the live read) or ``public`` (a past as-of: what was public by the end of
    that date). ``roster_fallback`` = no roster snapshot by the as-of: today's roster stands in. ``quarters`` /
    ``thin_below`` / ``min_median_names`` = the rules the page states. ``cache`` / ``compute_ms`` = whether the page
    came from the shared dashboard cache and how long it took when computed."""

    asof: date
    thesis_id: UUID
    thesis_name: str
    clock: Literal["record", "public"]
    roster_fallback: bool
    axis: list[str]
    card_line: MultiplesMedianOut
    rows: list[MultiplesRowOut]
    companies: list[MultiplesCompanyOut]
    counts: MultiplesCountsOut
    quarters: int
    thin_below: int
    min_median_names: int
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
) -> Multiples:
    """The heavy read the cache holds, built into the page: the card's roster as of the as-of
    (``baskets.cards_as_of``'s rule for one card: the live view reads today's roster, a past view the latest
    snapshot by ``known_at``, today's roster standing in before the first one), then for each resolved company its
    as-of fundamentals rows and its identity, all on the request's ``known_at`` + ``clock``; and the active
    set-asides on the card's names."""
    if known_at is None:
        card, fallback = thesis_repo.get(conn, thesis_id), False
    else:
        fallback = not thesis_repo.snapshot_exists_asof(conn, thesis_id, known_at)
        card = thesis_repo.get_asof(conn, thesis_id, known_at)
    if card is None:
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    memberships = memberships_of([card])
    sids = resolved_ids(memberships)

    def read(pit: PointInTimeData, sid: UUID) -> CompanyRead:
        return CompanyRead(
            name=pit.security_name(sid),
            cik=pit.security_cik(sid),
            rows=tuple(pit.fundamentals_facts(sid)),
        )

    reads = per_company(conn, sids, asof, known_at, clock, read)
    # a set-aside is a judgment about a stored version, skipped by every read; this only COUNTS the active ones
    # on the card's revenue rows that were public by the as-of (read only)
    on_card = set(sids)
    aside = Counter(
        sa.security_id
        for sa in fundamentals_set_aside_repo.active(conn)
        if sa.security_id in on_card and sa.metric_key == REVENUE_METRIC and sa.valid_from <= asof
    )
    facts = {sid: CompanyRead(r.name, r.cik, r.rows, aside.get(sid, 0)) for sid, r in reads.items()}
    return fundamentals_multiples(
        MultiplesInputs(
            thesis_id=card.id,
            thesis_name=card.name,
            segments=tuple(card.segments),
            roster=memberships,
            facts=facts,
            roster_fallback=fallback,
        ),
        asof=asof,
    )


def _cards(
    conn: psycopg.Connection, asof: date, clock: Literal["record", "public"]
) -> Cached[CardsRead]:
    return cached(conn, SLUG, ("cards", clock), asof, lambda: read_cards(conn, asof))


@router.get("/cards", response_model=MultiplesCardsOut)
def multiples_cards(
    asof: date = Query(
        ..., description="as-of date; only the cards that existed by then are listed"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> MultiplesCardsOut:
    """The card picker: the live cards that existed by the as-of, A→Z, each with its link count today. Display
    only."""
    _, clock = serve_view(asof)
    got = _cards(conn, asof, clock)
    return MultiplesCardsOut.model_validate(
        {
            "asof": asof,
            "live_cards": got.value.live_cards,
            "not_yet_created": got.value.not_yet_created,
            "cards": [asdict(c) for c in got.value.cards],
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )


@router.get("/{thesis_id}", response_model=MultiplesOut)
def multiples_for_card(
    thesis_id: UUID,
    asof: date = Query(..., description="as-of date; nothing published after it is read"),
    conn: psycopg.Connection = Depends(get_conn),
) -> MultiplesOut:
    """One card's revenue series: its links in chain order as rows, each name a small chart of its last quarterly
    revenue prints (placed by period end, dated by the filing that made them public) with the year-over-year
    change as dots, and the median of those changes per link and for the card. Display only; a past as-of reads
    the roster as of that date and what was public by the end of it. 404 when the card is not counted at the
    as-of."""
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
    return MultiplesOut.model_validate(
        {
            **asdict(got.value),
            "clock": clock,
            "quarters": QUARTERS,
            "thin_below": THIN_BELOW,
            "min_median_names": MIN_MEDIAN_NAMES,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
