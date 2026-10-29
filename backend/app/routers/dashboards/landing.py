"""The Price & Volume Landing dashboard's API (``docs/dashboards/landing.md``): one GET over every live card, at
the as-of dial. It READS the cards and their rosters as of the as-of (``dashboards/baskets.py``, the Rotation
Map's own read), reads each company's price bars once on the public clock, reads which companies' data flags
reach their price readings (``dashboards.baskets.price_flags``, on the same view), and hands the readings to
the pure grouping in ``dashboards/landing.py``. The heavy read is cached per as-of in the shared dashboard cache
(``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7), so one cold compute serves every grouping and
toggle. Its wire models live here, not in ``app/schemas_api.py`` (rule 8)."""

from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import (
    GroupBy,
    cards_as_of,
    memberships_of,
    per_company,
    price_flags,
    resolved_ids,
)
from dashboards.cache import cached
from dashboards.landing import (
    LOOKBACK_DAYS,
    MIN_PRICED,
    RISING_MULT,
    WINDOWS,
    CompanyLanding,
    LandingInputs,
    company_landing,
)
from dashboards.landing import landing as compute_landing
from domain.market_time import serve_view
from securities import master
from signals.display import sma

router = APIRouter(prefix="/landing")

SLUG = "landing"

QuadrantOut = Literal["accumulation", "thin_rally", "distribution", "rest", "flat"]


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class LandingCardOut(_Wire):
    """A card a name sits on in this group: its ticker there (the Cockpit's ``?name=`` key) and its links
    there (``null`` = Unsegmented)."""

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: list[str | None]


class LandingNameWindowOut(_Wire):
    """One name's reading for one window (``null`` = no reading). ``range_pos`` = where the close sits in the
    window's high-low range (0 = the low, 100 = the high); ``ret`` = the % return over the window;
    ``vol_ratio`` = the window's volume / the prior window of the same length; ``quadrant`` = the return's
    sign x volume rising (``vol_ratio`` above ``rising_mult``) or not; ``up_down`` = up-close-day volume /
    down-close-day volume."""

    key: str
    ret: float | None
    range_pos: float | None
    vol_ratio: float | None
    quadrant: QuadrantOut | None
    up_down: float | None


class LandingNameOut(_Wire):
    """One company in a group. A name ``not_priced`` (``no_tape``: no close by the as-of; ``stale_tape``: its
    last close is more than ``stale_gap_days`` before it) reads no window. ``flag`` = why it is left out of
    every aggregate (a flagged price history; it then reads no window and is neither priced nor
    ``not_priced``); ``null`` = read."""

    security_id: UUID
    ticker: str
    cards: list[LandingCardOut]
    priced: bool
    not_priced: Literal["no_tape", "stale_tape"] | None
    last_bar: date | None
    windows: list[LandingNameWindowOut]
    flag: str | None


class LandingFlaggedOut(_Wire):
    """A company in view left out of every aggregate: its data flags reach its price readings at the as-of
    (the reason the Cockpit grays its price cells). ``reason`` = the flags' labels."""

    security_id: UUID
    ticker: str
    reason: str


class LandingSplitOut(_Wire):
    """How many of the group's names landed in each quadrant in this window; ``unread`` = names with no
    quadrant reading."""

    accumulation: int
    thin_rally: int
    distribution: int
    rest: int
    flat: int
    unread: int


class LandingGroupWindowOut(_Wire):
    """One window of a group: the median range position over the ``range_n`` names with one (``null`` when
    fewer than ``min_priced``), the quadrant split, and the median up/down-day volume over ``up_down_n``
    names (same rule)."""

    key: str
    range_median: float | None
    range_n: int
    split: LandingSplitOut
    up_down_median: float | None
    up_down_n: int


class LandingGroupOut(_Wire):
    """One row. Link view: ``link`` (``null`` = Unsegmented) + its card. Card view: the card. Business type /
    Sector view: ``business_type`` / ``supersector`` (``null`` = Unclassified). ``names`` = the companies in
    the group, each counted once; ``priced`` of them have a recent close; ``flagged`` of them are left out of
    every aggregate for a flagged price history."""

    key: str
    link: str | None
    thesis_id: UUID | None
    thesis_name: str | None
    business_type: str | None
    supersector: str | None
    names: int
    priced: int
    flagged: int
    windows: list[LandingGroupWindowOut]
    members: list[LandingNameOut]


class LandingWindowDefOut(_Wire):
    """A window: its key and its length in trading bars."""

    key: str
    bars: int


class LandingOut(_Wire):
    """The whole page in one view, groups in the default order (the 7D median range position, highest
    first, "—" last). The counts are the work shown on its quiet line; the rule dials ride along. ``flagged`` =
    the companies in view left out of every aggregate for a flagged price history, by ticker. ``cache``
    / ``compute_ms`` = whether the heavy read was served from the shared dashboard cache and how long it took
    when it ran."""

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
    companies: int
    priced_companies: int
    no_tape_companies: int
    stale_tape_companies: int
    groups: list[LandingGroupOut]
    flagged: list[LandingFlaggedOut]
    windows: list[LandingWindowDefOut]
    min_priced: int
    rising_mult: float
    stale_gap_days: int
    cache: Literal["hit", "computed"]
    compute_ms: int


def _readings(
    conn: psycopg.Connection,
    sids: list[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[UUID, CompanyLanding]:
    """Each company's readings, computed ONCE from the same point-in-time view the Cockpit's
    ``/display-signals`` builds (``known_at`` + ``clock`` from ``serve_view``: the public clock at a past
    as-of, the live read today)."""
    return per_company(
        conn,
        sids,
        asof,
        known_at,
        clock,
        lambda pit, sid: company_landing(pit.price_history(sid, lookback_days=LOOKBACK_DAYS), asof),
    )


def read_inputs(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> LandingInputs:
    """The heavy read the cache holds: the counted cards' rosters as of the as-of, every company's readings
    and its price flags (``price_flags``, on the same view)."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    sids = resolved_ids(memberships)
    return LandingInputs(
        live_cards=counted.live_count,
        cards_counted=len(counted.cards),
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        memberships=memberships,
        readings=_readings(conn, sids, asof, known_at, clock),
        flagged=price_flags(conn, sids, asof, known_at=known_at, clock=clock),
    )


@router.get("", response_model=LandingOut)
def landing(
    asof: date = Query(..., description="as-of date; no price published after it is read"),
    group_by: GroupBy = Query("link", description="the rows: link | business_type | sector | card"),
    include_silent: bool = Query(
        False, description="add Context and Bench memberships (their own rows in the Link view)"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> LandingOut:
    """Where price closed in each window's range, and whether volume backed the move, by link, business
    type, sector or card: per group and per window (1D / 7D / 30D / 90D trading bars), the median range
    position, the split of names by return x volume (accumulation, thin rally, distribution, rest, flat)
    and the median up/down-day volume, with each group's names. A name whose data flags reach its price
    readings is left out of every aggregate and listed. Display only; a past as-of reads what was public by
    the end of that date."""
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(conn, SLUG, (clock,), asof, lambda: read_inputs(conn, asof, known_at, clock))
    inputs = got.value
    identity = (
        master.identity_for(conn, set(inputs.readings))
        if group_by in ("business_type", "sector")
        else {}
    )
    result = compute_landing(
        inputs, identity, asof=asof, group_by=group_by, include_silent=include_silent
    )
    payload = {f.name: getattr(result, f.name) for f in fields(result)}
    return LandingOut.model_validate(
        {
            **payload,
            "windows": [{"key": k, "bars": n} for k, n in WINDOWS],
            "min_priced": MIN_PRICED,
            "rising_mult": RISING_MULT,
            "stale_gap_days": sma.STALE_GAP_DAYS,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
