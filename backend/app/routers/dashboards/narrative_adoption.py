"""The Narrative Adoption dashboard's API (``docs/dashboards/narrative-adoption.md``): how many filings mention one
card's SIGNAL terms each month over three years, by who files them, with the card's basket line against SPY on
the same axis, at the as-of dial. It READS the cards that existed by the as-of (``dashboards/baskets.py``), the
picked card's roster as of the as-of, its stored monthly term counts through the point-in-time view
(``PointInTimeData.term_mentions``: ``valid_from`` = the month's last day, so a past as-of ends the curve at the
last month closed by then), the roster's closes and SPY's, and the term-mentions job's newest run artifact, then
hands them to the pure compute in ``dashboards/narrative_adoption.py``. One card's page is computed once per
(card, clock, newest job run, as-of) in the shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md``
rule 7): the cache's own generation token knows the nightly pass and basket edits, so the weekly job's run is
part of this page's key. Its wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import (  # noqa: F401  (CardsRead: the picker's read; tests stub read_cards)
    CHUNK,
    CardsRead,
    counted_rows,
    memberships_of,
    picker_cards,
    price_flags,
    resolved_ids,
)
from dashboards.cache import Cached, cached
from dashboards.narrative_adoption import (
    BENCHMARK,
    MONTHS,
    PRICE_LOOKBACK_DAYS,
    Adoption,
    narrative_adoption,
    run_identity,
    run_summary,
)
from db.session import DEFAULT_TENANT_ID
from domain.market_time import serve_view
from pipeline.term_mentions import latest_run
from repositories import thesis_repo
from signals.base import PointInTimeData
from signals.horizons import display_bounds

router = APIRouter(prefix="/narrative-adoption")

SLUG = "narrative-adoption"


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class AdoptionCardOut(_Wire):
    """A card in the picker: its name and its real links today (Context and Bench not counted)."""

    thesis_id: UUID
    name: str
    links: int


class AdoptionCardsOut(_Wire):
    """The picker: the cards counted at the as-of (the live cards that existed by then), A→Z. ``cache`` /
    ``compute_ms`` = whether the list came from the shared dashboard cache and how long it took when computed.
    """

    asof: date
    live_cards: int
    not_yet_created: int
    cards: list[AdoptionCardOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


class AdoptionTermShareOut(_Wire):
    """One term's documents in a month (the month's top terms)."""

    term: str
    documents: int


class AdoptionMonthOut(_Wire):
    """One axis month (``month`` = its first day), summed over the card's SIGNAL terms that have a stored
    count that month. ``counted`` false = no term has one: every number is ``null`` (never zero), and
    ``closed`` says whether the month had ended by the as-of (the as-of's own month has not). A document
    matching two terms counts twice in the sums. ``terms_counted`` = how many terms the sums rest on.
    ``filers`` = distinct CIKs summed over terms, "at least" when ``filers_sampled``; ``new_filers`` = the
    terms' own new filers summed (``null`` = none has one). ``read_early`` = a count was read before the month
    closed (partial until the next weekly run). ``top_terms`` = the three terms with the most documents.
    """

    month: date
    closed: bool
    counted: bool
    terms_counted: int
    documents: int | None
    reports: int | None
    events: int | None
    issuance: int | None
    other: int | None
    filers: int | None
    filers_sampled: bool
    new_filers: int | None
    read_early: bool
    top_terms: list[AdoptionTermShareOut]


class AdoptionTermOut(_Wire):
    """One SIGNAL term, counted alone. ``counted`` false = no month stored yet ("not counted yet", never
    zero). The windows end at the page's last counted month: ``last_3_documents`` / ``last_12_documents`` sum
    the months stored (``months_last_3`` / ``months_last_12`` say how many of the 3 / 12), ``null`` = none
    stored. ``filers`` = distinct CIKs in the last month. ``first_month`` = the first stored month with a
    document; ``first_at_start`` = it is also the earliest stored month, so the term may be older.
    """

    term: str
    counted: bool
    months_stored: int
    last_month_documents: int | None
    last_3_documents: int | None
    months_last_3: int
    last_12_documents: int | None
    months_last_12: int
    filers: int | None
    filers_sampled: bool
    first_month: date | None
    first_at_start: bool


class AdoptionFlaggedOut(_Wire):
    """A name left out of the card's line: its data flags reach its price readings at the as-of."""

    ticker: str
    reason: str


class AdoptionOverlayOut(_Wire):
    """The card's equal-weighted basket line and SPY over ``dates``, both 100 on the first. ``null`` line = none
    (``card_reason`` / ``spy_reason`` say why). ``names`` = companies on the card (each once) plus rows with
    no resolved security; ``priced`` = those that contributed a daily return; ``flagged`` = names left out for a
    flagged price history (counted in ``names``, never in ``priced``)."""

    dates: list[date]
    card: list[float] | None
    spy: list[float] | None
    card_reason: str | None
    spy_reason: str | None
    session_source: Literal["benchmarks", "companies", "none"]
    names: int
    priced: int
    unresolved: int
    silent_left_out: int
    flagged: list[AdoptionFlaggedOut]


class AdoptionCountsOut(_Wire):
    """The work: the card's current SIGNAL terms and how many have a stored month, the BROAD terms never
    counted, stored terms no longer in the term set (``retired_terms``, ignored), and the axis months by
    state (counted, counted for every term, "at least" filers, read before they closed, not yet closed).
    """

    signal_terms: int
    terms_counted: int
    terms_not_counted: int
    broad_terms_left_out: int
    retired_terms: int
    months_total: int
    months_counted: int
    months_complete: int
    months_sampled: int
    months_read_early: int
    months_not_closed: int
    last_counted_month: date | None


class AdoptionRunOut(_Wire):
    """The term-mentions job's newest applied run, from its artifact: ``schedule`` weekly (re-reads the last two
    months) or backfill (the whole window), the months it covered, and what it could not read. The totals are
    the job's (every card); ``card_*`` are this card's share (a failed or deferred month is absent from the
    curve, never a zero)."""

    schedule: str
    run_kind: str
    status: str
    finished_on: date | None
    months_from: str
    months_to: str
    requests: int
    failed_requests: int
    deferred_months: int
    card_failed_requests: int
    card_deferred_months: int


class AdoptionOut(_Wire):
    """One card's page. ``months`` = the ``months_window`` calendar months ending with the as-of's month, oldest
    first; ``terms`` = the table, sorted by last-month documents; ``overlay`` = the Narrative Index line.
    ``clock``: ``record`` (the live read) or ``public`` (a past as-of: what was public by the end of that
    date). ``roster_fallback`` = no roster snapshot by the as-of: today's roster stands in for the line (the
    terms are today's too: a term set has no history). ``last_run`` = the job's newest run (``null`` = none on
    record). ``cache`` / ``compute_ms`` = whether the page came from the shared dashboard cache and how long it
    took when computed."""

    asof: date
    thesis_id: UUID
    thesis_name: str
    clock: Literal["record", "public"]
    roster_fallback: bool
    months_window: int
    months: list[AdoptionMonthOut]
    terms: list[AdoptionTermOut]
    overlay: AdoptionOverlayOut
    counts: AdoptionCountsOut
    last_run: AdoptionRunOut | None
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------

# the picker is the shared basket read (``baskets.picker_cards``); ``read_cards`` is its name here (the tests
# stub it on this module)
read_cards = picker_cards


def read_last_run() -> dict[str, Any] | None:
    """The newest applied run artifact of ``pipeline.term_mentions`` (``None`` = none on record); a file read,
    no database. A module-level name so the tests can stub it."""
    return latest_run()


def _price_bounds() -> dict[str, int | None]:
    """The display PIT's bounds with the price window widened to the axis: the display bound (630 days) would
    stop the line short of 36 months."""
    return {**display_bounds(), "fact_price_eod": PRICE_LOOKBACK_DAYS}


def _closes(
    conn: psycopg.Connection,
    sids: Sequence[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[UUID, dict[date, float]]:
    """Each company's ``{day: close}`` up to the as-of over the axis' window, through the point-in-time view
    (``known_at`` + ``clock`` from ``serve_view``), ``CHUNK`` companies per view."""
    bounds = _price_bounds()
    out: dict[UUID, dict[date, float]] = {}
    for i in range(0, len(sids), CHUNK):
        chunk = list(sids[i : i + CHUNK])
        pit = PointInTimeData(
            conn,
            asof=asof,
            known_at=known_at,
            tenant_id=DEFAULT_TENANT_ID,
            basket=chunk,
            bounds=bounds,
            clock=clock,
        )
        for sid in chunk:
            out[sid] = {
                b["d"]: float(b["close"])
                for b in pit.price_history(sid, lookback_days=PRICE_LOOKBACK_DAYS)
                if b.get("close") is not None
            }
    return out


def read_page(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> tuple[Adoption, bool]:
    """The heavy read the cache holds, built into the page: ``(the page, roster_fallback)``. The card's roster
    as of the as-of (``baskets.cards_as_of``'s rule for one card: the live view reads today's roster, a past
    view the latest snapshot by ``known_at``, today's roster standing in before the first one), its stored
    term counts and SPY's bars on the request's ``known_at`` + ``clock``, the roster's closes and price flags
    on the same pair."""
    if known_at is None:
        card, fallback = thesis_repo.get(conn, thesis_id), False
    else:
        fallback = not thesis_repo.snapshot_exists_asof(conn, thesis_id, known_at)
        card = thesis_repo.get_asof(conn, thesis_id, known_at)
    if card is None:
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    memberships = memberships_of([card])
    sids = resolved_ids(counted_rows(memberships, include_silent=False).rows)
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=_price_bounds(),
        clock=clock,
    )
    spy = {
        b["d"]: float(b["close"])
        for b in pit.benchmark_prices(BENCHMARK, lookback_days=PRICE_LOOKBACK_DAYS)
        if b.get("close") is not None
    }
    page = narrative_adoption(
        thesis_id=card.id,
        thesis_name=card.name,
        asof=asof,
        term_set=[e.model_dump(mode="json") for e in card.term_set],
        rows=pit.term_mentions(thesis_id),
        roster=memberships,
        closes=_closes(conn, sids, asof, known_at, clock),
        spy_closes=spy,
        flagged=price_flags(conn, sids, asof, known_at=known_at, clock=clock),
    )
    return page, fallback


def _cards(
    conn: psycopg.Connection, asof: date, clock: Literal["record", "public"]
) -> Cached[CardsRead]:
    return cached(conn, SLUG, ("cards", clock), asof, lambda: read_cards(conn, asof))


@router.get("/cards", response_model=AdoptionCardsOut)
def adoption_cards(
    asof: date = Query(
        ..., description="as-of date; only the cards that existed by then are listed"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdoptionCardsOut:
    """The card picker: the live cards that existed by the as-of, A→Z, each with its link count today. Display
    only."""
    _, clock = serve_view(asof)
    got = _cards(conn, asof, clock)
    return AdoptionCardsOut.model_validate(
        {
            "asof": asof,
            "live_cards": got.value.live_cards,
            "not_yet_created": got.value.not_yet_created,
            "cards": [asdict(c) for c in got.value.cards],
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )


@router.get("/{thesis_id}", response_model=AdoptionOut)
def adoption_for_card(
    thesis_id: UUID,
    asof: date = Query(..., description="as-of date; nothing published after it is read"),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdoptionOut:
    """One card's narrative adoption: the filings that mention its SIGNAL terms per month over 36 months, split
    into reports, 8-Ks, new-issuance paper and the rest, with the filers and the new filers per month, a table
    of each term, and the card's equal-weighted basket line against SPY on the same axis. A month with no stored
    count is absent, never zero. Display only; a past as-of reads the roster as of that date and ends the curve
    at the last month that had closed by then. 404 when the card is not counted at the as-of."""
    known_at, clock = serve_view(asof)
    if not any(c.thesis_id == thesis_id for c in _cards(conn, asof, clock).value.cards):
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    run = read_last_run()
    # the clock is part of the key (the same date read live today and on the public clock tomorrow are two
    # reads), and so is the newest job run (the weekly job is a "new run" for this page: the cache's own token
    # follows the nightly pass and basket edits only)
    got = cached(
        conn,
        SLUG,
        (str(thesis_id), clock, run_identity(run)),
        asof,
        lambda: read_page(conn, thesis_id, asof, known_at, clock),
    )
    page, fallback = got.value
    return AdoptionOut.model_validate(
        {
            "asof": asof,
            "thesis_id": page.thesis_id,
            "thesis_name": page.thesis_name,
            "clock": clock,
            "roster_fallback": fallback,
            "months_window": MONTHS,
            "months": [asdict(m) for m in page.months],
            "terms": [asdict(t) for t in page.terms],
            "overlay": asdict(page.overlay),
            "counts": asdict(page.counts),
            "last_run": (
                asdict(summary)
                if (summary := run_summary(run, page.thesis_id, page.thesis_name))
                else None
            ),
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
