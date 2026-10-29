"""The Rotation Quadrant dashboard's API (``docs/dashboards/rotation-quadrant.md``): each group's strength vs SPY
and that strength's momentum, with an 8-week tail, at the as-of dial. It READS the cards and their rosters as of
the as-of (``dashboards/baskets.py``), every company's closes and SPY's on the public clock, the master's business
types and sectors and the price flags, and hands them to the pure compute in ``dashboards/rotation_quadrant.py``
(the line math is the Narrative Index's, ``dashboards/series.py``). Every grouping's groups are computed once per
(clock, toggle, as-of) in the shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7);
each view picks one grouping per request. Its wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import (
    GroupBy,
    cards_as_of,
    counted_rows,
    memberships_of,
    per_company,
    price_flags,
    resolved_ids,
)
from dashboards.cache import cached
from dashboards.rotation_quadrant import (
    AGO_SESSIONS,
    BENCHMARK,
    LEVEL_SESSIONS,
    MIN_PRICED,
    MOMENTUM_SESSIONS,
    PRICED_SESSIONS,
    TAIL_POINTS,
    TAIL_STEP,
    QuadrantInputs,
    RotationQuadrant,
    quadrant_inputs,
)
from dashboards.rotation_quadrant import rotation_quadrant as compute_rotation_quadrant
from db.session import DEFAULT_TENANT_ID
from domain.market_time import serve_view
from securities import master
from signals.base import PointInTimeData
from signals.display import trailing_returns
from signals.horizons import display_bounds

router = APIRouter(prefix="/rotation-quadrant")

SLUG = "rotation-quadrant"
# calendar days of bars read per company and for SPY: the Cockpit's own 1Y-return lookback (the Narrative
# Index's; ~290 sessions for the 253 the year's read needs)
LOOKBACK_DAYS = trailing_returns.LOOKBACK_DAYS

QuadrantWire = Literal["leading", "weakening", "lagging", "improving"]


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class QuadrantTailPointOut(_Wire):
    """One tail point: x / y on ``session`` (``null`` = not measurable there; ``session`` null = before the
    first session read) and its quadrant."""

    session: date | None
    x: float | None
    y: float | None
    quadrant: QuadrantWire | None


class QuadrantNameCardOut(_Wire):
    """A card a name sits on in this group: its ticker there (the Cockpit's ``?name=`` key) and its links
    there (``null`` = Unsegmented)."""

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: list[str | None]


class QuadrantNameOut(_Wire):
    """One company in a group. ``priced`` = a daily return into one of the last ``priced_sessions`` sessions.
    ``flag`` = why it is left out of the line (a flagged price history; ``null`` = its bars are read).
    """

    security_id: UUID
    ticker: str
    cards: list[QuadrantNameCardOut]
    priced: bool
    flag: str | None


class QuadrantFlaggedOut(_Wire):
    """A name left out of its group's line: its data flags reach its price readings at the as-of (the reason
    the Cockpit grays its price cells). ``reason`` = the flags' labels."""

    ticker: str
    reason: str


class QuadrantGroupOut(_Wire):
    """One group. Link view: ``link`` (``null`` = Unsegmented) + its card. Card view: the card. Business type /
    Sector view: ``business_type`` / ``supersector`` (``null`` = Unclassified). ``names`` = the companies in the
    group, each once (flagged ones included); ``priced`` of them have a daily return in the last
    ``priced_sessions`` sessions. ``status``: ``plotted`` (x and y today), ``too_small`` (fewer than
    ``min_priced`` priced names: not measured), ``no_history`` (too little history for today's x or y; ``x``
    still shows when it alone is measurable). ``x`` = RS level, ``y`` = RS momentum, ``quadrant`` today,
    ``quadrant_ago`` ``ago_sessions`` sessions back; ``tail`` = ``tail_points`` points every ``tail_step``
    sessions, oldest first (empty when not measured). ``sessions_flat`` = sessions in the span the measures
    read that no name could contribute to (the line carried flat)."""

    key: str
    link: str | None
    thesis_id: UUID | None
    thesis_name: str | None
    business_type: str | None
    supersector: str | None
    names: int
    priced: int
    status: Literal["plotted", "too_small", "no_history"]
    x: float | None
    y: float | None
    quadrant: QuadrantWire | None
    quadrant_ago: QuadrantWire | None
    tail: list[QuadrantTailPointOut]
    sessions_flat: int
    members: list[QuadrantNameOut]
    flagged: list[QuadrantFlaggedOut]


class RotationQuadrantOut(_Wire):
    """The whole page in one view, groups in the default order (quadrant: improving, leading, weakening,
    lagging, then x highest first; no quadrant last). ``sessions`` = daily returns read
    (``first_session`` → ``last_session``); ``session_source`` = where the calendar came from (SPY's tape, or
    the companies' bars when SPY has none). ``spy_available`` false = no SPY line, so nothing is measured. The
    counts are the work shown on its quiet line; the measure windows ride along. ``cache`` / ``compute_ms`` =
    whether the groups came from the shared dashboard cache and how long they took when computed."""

    asof: date
    group_by: Literal["link", "business_type", "sector", "card"]
    include_silent: bool
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int
    sessions: int
    first_session: date | None
    last_session: date | None
    session_source: Literal["benchmarks", "companies", "none"]
    spy_available: bool
    companies: int
    priced_companies: int
    flagged_companies: int
    groups_plotted: int
    groups_too_small: int
    groups_no_history: int
    groups: list[QuadrantGroupOut]
    level_sessions: int
    momentum_sessions: int
    tail_step: int
    tail_points: int
    ago_sessions: int
    priced_sessions: int
    min_priced: int
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------


def _closes(
    conn: psycopg.Connection,
    sids: Sequence[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[UUID, dict[date, float]]:
    """Each company's ``{day: close}`` up to the as-of, from the same point-in-time view the Cockpit's
    ``/display-signals`` builds (``known_at`` + ``clock`` from ``serve_view``)."""
    return per_company(
        conn,
        sids,
        asof,
        known_at,
        clock,
        lambda pit, sid: {
            b["d"]: float(b["close"])
            for b in pit.price_history(sid, lookback_days=LOOKBACK_DAYS)
            if b.get("close") is not None
        },
    )


def _spy(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[date, float]:
    """SPY's ``{day: close}`` (``pit.benchmark_prices``; empty = no tape at the as-of)."""
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=display_bounds(),
        clock=clock,
    )
    return {
        b["d"]: float(b["close"])
        for b in pit.benchmark_prices(BENCHMARK, lookback_days=LOOKBACK_DAYS)
        if b.get("close") is not None
    }


def read_inputs(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    include_silent: bool,
) -> QuadrantInputs:
    """The heavy read the cache holds: the counted cards' rosters as of the as-of, every counted company's
    closes, identity and price flags (``price_flags``, on the same view), SPY's closes, built into every
    grouping's groups."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    sids = resolved_ids(counted_rows(memberships, include_silent).rows)
    return quadrant_inputs(
        memberships=memberships,
        closes=_closes(conn, sids, asof, known_at, clock),
        spy=_spy(conn, asof, known_at, clock),
        identity=master.identity_for(conn, set(sids)) if sids else {},
        asof=asof,
        include_silent=include_silent,
        live_cards=counted.live_count,
        cards_counted=len(counted.cards),
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        flagged=price_flags(conn, sids, asof, known_at=known_at, clock=clock),
    )


def _payload(obj: Any) -> dict[str, Any]:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def _page_dict(page: RotationQuadrant) -> dict[str, Any]:
    return {
        **_payload(page),
        "groups": [
            {
                **_payload(g),
                "tail": [_payload(p) for p in g.tail],
                "members": [
                    {**_payload(m), "cards": [_payload(c) for c in m.cards]} for m in g.members
                ],
                "flagged": [_payload(f) for f in g.flagged],
            }
            for g in page.groups
        ],
    }


@router.get("", response_model=RotationQuadrantOut)
def rotation_quadrant(
    asof: date = Query(..., description="as-of date; no price published after it is read"),
    group_by: GroupBy = Query(
        "link", description="the groups: link | business_type | sector | card"
    ),
    include_silent: bool = Query(
        False, description="add Context and Bench memberships (their own groups in the Link view)"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> RotationQuadrantOut:
    """Each group's equal-weighted line against SPY's, as two numbers: x = RS level (the line ÷ SPY's, vs its own
    65-session average; 100 = at it) and y = RS momentum (x vs x 20 sessions ago; 100 = flat), with the quadrant
    (leading, weakening, lagging, improving), the quadrant 20 sessions ago and an 8-week tail every 5 sessions.
    A group needs 3 priced names; a name whose data flags reach its price readings is left out of the line and
    listed on its group. Display only; a past as-of reads what was public by the end of that date.
    """
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(
        conn,
        SLUG,
        (clock, include_silent),
        asof,
        lambda: read_inputs(conn, asof, known_at, clock, include_silent),
    )
    page = compute_rotation_quadrant(got.value, asof=asof, group_by=group_by)
    return RotationQuadrantOut.model_validate(
        {
            **_page_dict(page),
            "level_sessions": LEVEL_SESSIONS,
            "momentum_sessions": MOMENTUM_SESSIONS,
            "tail_step": TAIL_STEP,
            "tail_points": TAIL_POINTS,
            "ago_sessions": AGO_SESSIONS,
            "priced_sessions": PRICED_SESSIONS,
            "min_priced": MIN_PRICED,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
