"""The Filing Stream dashboard's API (``docs/dashboards/filing-stream.md``): one card's names' filing-derived facts
as a dated stream, newest day first, at the as-of dial. It READS the cards that existed by the as-of
(``dashboards/baskets.py``), the picked card's roster as of the as-of (``thesis_repo.get_asof``) and every roster
company's rows through the shared filing reader (``dashboards/filings.py``: the point-in-time view plus the Form
4/A SELECT, both on the request's ``serve_view(asof)`` pair), then hands them to the pure compute in
``dashboards/filing_stream.py``. The 365-day rows are computed once per (card, clock, as-of) in the shared
dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7); the window is sliced per request. Its
wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from enum import IntEnum
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import (  # noqa: F401  (CardsRead: the picker's read; tests stub read_cards)
    CardsRead,
    memberships_of,
    picker_cards,
    resolved_ids,
)
from dashboards.cache import Cached, cached
from dashboards.filing_stream import (
    READ_DAYS,
    WINDOWS,
    StreamInputs,
    filing_stream,
    stream_inputs,
)
from dashboards.filings import FilingKind, read_filing_rows
from domain.enums import ContextRole
from domain.market_time import serve_view
from repositories import thesis_repo

router = APIRouter(prefix="/filing-stream")

SLUG = "filing-stream"


class FilingStreamDays(IntEnum):
    """The window: the last 30, 90 or 365 days."""

    D30 = 30
    D90 = 90
    D365 = 365


LinkKind = Literal["link", "old_link", "no_link", "silent"]


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class FilingStreamCardOut(_Wire):
    """A card in the picker: its name and its real links today (Context and Bench not counted)."""

    thesis_id: UUID
    name: str
    links: int


class FilingStreamCardsOut(_Wire):
    """The picker: the cards counted at the as-of (the live cards that existed by then), A→Z. ``cache`` /
    ``compute_ms`` = whether the list came from the shared dashboard cache and how long it took when computed.
    """

    asof: date
    live_cards: int
    not_yet_created: int
    cards: list[FilingStreamCardOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


class FilingStreamRowOut(_Wire):
    """One filing-derived fact. ``kind``: ``8k`` | ``form4`` | ``form4a`` | ``stake`` | ``revenue`` | ``split`` |
    ``dilution`` | ``catalyst`` | ``ratified``. ``form`` = the form as filed or the row's own label; ``date`` = the
    public date it counts from; ``headline`` = one neutral line; ``detail`` = the lines the expanded row shows;
    ``url`` = the filing's EDGAR index or the stored source (``null`` = none known); ``tags`` = quiet field
    predicates; ``routine`` = a fold candidate (a Form 4 holding no code-P purchase); ``usd`` = the row's dollar
    size when it has one (a Form 4's purchases minus sales, a dilution's principal). ``links`` / ``role`` /
    ``silent`` = the company's place on the card at the as-of (``null`` in ``links`` = no link)."""

    key: str
    security_id: UUID
    ticker: str
    kind: FilingKind
    form: str
    date: date
    headline: str
    detail: list[str]
    url: str | None
    accession: str | None
    tags: list[str]
    routine: bool
    usd: float | None
    links: list[str | None]
    role: ContextRole | None
    silent: bool


class FilingStreamDayOut(_Wire):
    """One day of the stream, its rows in stream order; ``routine`` = how many of them the fold covers."""

    date: date
    routine: int
    rows: list[FilingStreamRowOut]


class FilingStreamKindOut(_Wire):
    kind: FilingKind
    rows: int


class FilingStreamCountsOut(_Wire):
    """What the window holds, folded rows included. ``form4_purchases`` = Form 4s holding a code-P purchase;
    ``routine`` = the rows the fold covers."""

    rows: int
    by_kind: list[FilingStreamKindOut]
    form4_purchases: int
    routine: int


class FilingStreamLinkOut(_Wire):
    """A link a page can filter by (``names`` = the companies on it today's roster, each once)."""

    key: str
    label: str
    kind: LinkKind
    names: int


class FilingStreamNameOut(_Wire):
    """A roster company (each once) with its rows in the window; a name with none is still listed.
    ``key`` = its ``security_id`` (``ticker:<T>`` for a row with no resolved security)."""

    key: str
    ticker: str
    security_id: UUID | None
    links: list[str | None]
    role: ContextRole | None
    silent: bool
    rows: int


class FilingStreamOut(_Wire):
    """One card's stream for one window, newest day first. ``clock``: ``record`` (the live read) or ``public``
    (a past as-of: what was public by the end of that date). ``roster_fallback`` = no roster snapshot by the
    as-of: today's roster stands in. ``unresolved`` = tickers of basket rows with no resolved security (they can
    carry no facts: counted, never in the stream). ``cache`` / ``compute_ms`` = whether the 365-day rows came
    from the shared dashboard cache and how long they took when computed."""

    asof: date
    thesis_id: UUID
    thesis_name: str
    days: int
    window_start: date
    clock: Literal["record", "public"]
    roster_fallback: bool
    links_today: int
    basket_rows: int
    silent_rows: int
    unresolved: list[str]
    links: list[FilingStreamLinkOut]
    roles: list[ContextRole]
    names: list[FilingStreamNameOut]
    groups: list[FilingStreamDayOut]
    counts: FilingStreamCountsOut
    windows: list[int]
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------

# the picker is the shared basket read (``baskets.picker_cards``); ``read_cards`` is its name here (the tests
# stub it on this module)
read_cards = picker_cards


def read_inputs(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> StreamInputs:
    """The heavy read the cache holds: the card's roster as of the as-of (``baskets.cards_as_of``'s rule for one
    card: the live view reads today's roster, a past view the latest snapshot by ``known_at``, today's roster
    standing in before the first one), then every roster company's rows for the 365-day window (Context and
    Bench included: the toggle is sliced on the page) through the shared reader."""
    if known_at is None:
        card, fallback = thesis_repo.get(conn, thesis_id), False
    else:
        fallback = not thesis_repo.snapshot_exists_asof(conn, thesis_id, known_at)
        card = thesis_repo.get_asof(conn, thesis_id, known_at)
    if card is None:
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    memberships = memberships_of([card])
    # the ticker a row shows is the card's own (the Cockpit's ?name= key), not the master's
    tickers = {m.security_id: m.ticker for m in reversed(memberships) if m.security_id is not None}
    return stream_inputs(
        thesis_id=card.id,
        thesis_name=card.name,
        segments=[s.label for s in card.segments],
        memberships=memberships,
        rows_by_sid=read_filing_rows(
            conn,
            resolved_ids(memberships),
            asof,
            known_at,
            clock,
            days=READ_DAYS,
            tickers=tickers,
        ),
        roster_fallback=fallback,
    )


def _cards(
    conn: psycopg.Connection, asof: date, clock: Literal["record", "public"]
) -> Cached[CardsRead]:
    return cached(conn, SLUG, ("cards", clock), asof, lambda: read_cards(conn, asof))


@router.get("/cards", response_model=FilingStreamCardsOut)
def filing_stream_cards(
    asof: date = Query(
        ..., description="as-of date; only the cards that existed by then are listed"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> FilingStreamCardsOut:
    """The card picker: the live cards that existed by the as-of, A→Z, each with its link count today. Display
    only."""
    _, clock = serve_view(asof)
    got = _cards(conn, asof, clock)
    return FilingStreamCardsOut.model_validate(
        {
            "asof": asof,
            "live_cards": got.value.live_cards,
            "not_yet_created": got.value.not_yet_created,
            "cards": [asdict(c) for c in got.value.cards],
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )


@router.get("/{thesis_id}", response_model=FilingStreamOut)
def filing_stream_for_card(
    thesis_id: UUID,
    asof: date = Query(..., description="as-of date; no filing public after it is read"),
    days: FilingStreamDays = Query(
        FilingStreamDays.D90, description="the window: the last 30 | 90 | 365 days"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> FilingStreamOut:
    """One card's names' filing-derived facts, newest day first: 8-Ks with their items, Form 4s with the filer's
    own words, Form 4/As, 13D/G stakes, revenue prints, splits, dilution and the operator's ratified facts, each
    row opening to its EDGAR source. Only facts the ingest stores are listed (not every filing). Display only; a
    past as-of reads what was public by the end of that date (a fact public after it never appears). 404 when
    the card is not counted at the as-of."""
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
        lambda: read_inputs(conn, thesis_id, asof, known_at, clock),
    )
    page = filing_stream(got.value, asof=asof, days=int(days))
    inputs = page.inputs
    return FilingStreamOut.model_validate(
        {
            "asof": asof,
            "thesis_id": inputs.thesis_id,
            "thesis_name": inputs.thesis_name,
            "days": page.days,
            "window_start": page.window_start,
            "clock": clock,
            "roster_fallback": inputs.roster_fallback,
            "links_today": inputs.links_today,
            "basket_rows": inputs.basket_rows,
            "silent_rows": inputs.silent_rows,
            "unresolved": list(inputs.unresolved),
            "links": [asdict(link) for link in inputs.links],
            "roles": list(inputs.roles),
            "names": [{**asdict(n.company), "rows": n.rows} for n in page.names],
            "groups": [
                {
                    "date": g.date,
                    "routine": g.routine,
                    "rows": [
                        {
                            **asdict(r.row),
                            "key": r.key,
                            "links": list(r.links),
                            "role": r.role,
                            "silent": r.silent,
                        }
                        for r in g.rows
                    ],
                }
                for g in page.groups
            ],
            "counts": asdict(page.counts),
            "windows": list(WINDOWS),
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
