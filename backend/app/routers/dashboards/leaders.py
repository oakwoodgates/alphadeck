"""The Leaders dashboard's API (``docs/dashboards/leaders.md``): one GET over every live card, at the as-of dial.
It READS the cards and their rosters as of the as-of (``dashboards/baskets.py``, the Rotation Map's own read),
runs seven display members once per company on the public clock, and hands the readings to the pure lists in
``dashboards/leaders.py``. The heavy read is cached per as-of in the shared dashboard cache
(``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7), so one cold compute serves every grouping and toggle.
Its wire models live here, not in ``app/schemas_api.py`` (rule 8)."""

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
    display_members,
    memberships_of,
    per_company,
    resolved_ids,
)
from dashboards.cache import cached
from dashboards.leaders import (
    CLUSTER_MIN_BUYERS,
    FULL_YEAR_BARS,
    GROUP_MIN_NAMES,
    INSIDER_MIN_BUYS,
    MEMBERS,
    MULTI_MIN_LISTS,
    NEAR_HIGH_PCT,
    RVOL_LOUD,
    CompanyReading,
    LeadersInputs,
    ListKey,
    company_reading,
)
from dashboards.leaders import leaders as compute_leaders
from domain.market_time import serve_view
from securities import master

router = APIRouter(prefix="/leaders")

SLUG = "leaders"


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class LeadersCardOut(_Wire):
    """A card a name sits on: its ticker there (the Cockpit's ``?name=`` key) and its links there (``null`` =
    Unsegmented)."""

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: list[str | None]


class LeadersNameOut(_Wire):
    """One company on at least one list (``lists``), each list's value beside it. ``pct_off_high`` = % off the
    52-week closing high (``high_date`` its print date; ``range_bars`` the bars the range stands on, under
    ``full_year_bars`` = not a full year); ``rs_vs`` = the benchmarks its RS is at a 13-week high against;
    ``contraction`` = the vcp member's newest-segment depth over its oldest; ``buys_30d`` / ``buyers_30d`` =
    open-market buys and distinct buyers in the last 30 days (``cluster`` = at least ``cluster_min_buyers``
    buyers); ``rvol20`` = RVOL 20D. ``null`` = no reading."""

    security_id: UUID
    ticker: str
    name: str | None
    cards: list[LeadersCardOut]
    lists: list[ListKey]
    pct_off_high: float | None
    high_52w: float | None
    high_date: date | None
    range_bars: int | None
    rs_vs: list[str]
    contraction: float | None
    buys_30d: int | None
    buyers_30d: int | None
    cluster: bool
    rvol20: float | None
    ret_7d: float | None
    ret_30d: float | None
    above_50d: bool | None


class LeadersListOut(_Wire):
    """One list: its names (``security_ids`` into ``names``, in its default order) and how many companies in
    view its display ``member`` gave no reading (not on the list for want of data, never silently dropped).
    """

    key: ListKey
    member: str
    security_ids: list[UUID]
    no_reading: int


class LeadersClusterNameOut(_Wire):
    """A name of an insider-cluster group: its open-market buys and distinct buyers in the last 30 days."""

    security_id: UUID
    ticker: str
    name: str | None
    cards: list[LeadersCardOut]
    buys_30d: int
    buyers_30d: int
    cluster: bool


class LeadersInsiderGroupOut(_Wire):
    """A group where at least ``group_min_names`` names had open-market buys in the last 30 days. Link view:
    ``link`` (``null`` = Unsegmented) + its card. Card view: the card. Business type / Sector view:
    ``business_type`` / ``supersector`` (``null`` = Unclassified). ``with_buys`` of its ``names`` (each counted
    once) bought, ``share`` = ``with_buys / names``; ``no_insider_data`` of them have no Form 4 ingested;
    ``buys`` = the group's buys."""

    key: str
    label: str
    link: str | None
    thesis_id: UUID | None
    thesis_name: str | None
    business_type: str | None
    supersector: str | None
    names: int
    with_buys: int
    no_insider_data: int
    share: float
    buys: int
    members: list[LeadersClusterNameOut]


class LeadersOut(_Wire):
    """The whole page in one view. ``names`` = every company on at least one list, in the several-lists order
    (the number of lists, then the 30D return); ``several`` = those on ``multi_min_lists`` or more; ``lists`` =
    each list's names in its default order and its no-reading count; ``insider_groups`` by names with buys,
    then share. The counts are the work shown on its quiet line; the rule dials ride along. ``cache`` /
    ``compute_ms`` = whether the heavy read was served from the shared dashboard cache and how long it took
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
    names: list[LeadersNameOut]
    several: list[UUID]
    lists: list[LeadersListOut]
    insider_groups: list[LeadersInsiderGroupOut]
    near_high_pct: float
    rvol_loud: float
    insider_min_buys: int
    cluster_min_buyers: int
    group_min_names: int
    multi_min_lists: int
    full_year_bars: int
    cache: Literal["hit", "computed"]
    compute_ms: int


def _readings(
    conn: psycopg.Connection,
    sids: list[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[UUID, CompanyReading]:
    """Each company's readings, computed ONCE, from the same point-in-time view the Cockpit's
    ``/display-signals`` builds (``known_at`` + ``clock`` from ``serve_view``: the public clock at a past
    as-of, the live read today)."""
    members = display_members(MEMBERS, "Leaders")
    return per_company(
        conn,
        sids,
        asof,
        known_at,
        clock,
        lambda pit, sid: company_reading({n: m(pit, sid, asof) for n, m in members.items()}),
    )


def read_inputs(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> LeadersInputs:
    """The heavy read the cache holds: the counted cards' rosters as of the as-of and every company's
    readings."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    return LeadersInputs(
        live_cards=counted.live_count,
        cards_counted=len(counted.cards),
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        memberships=memberships,
        readings=_readings(conn, resolved_ids(memberships), asof, known_at, clock),
    )


@router.get("", response_model=LeadersOut)
def leaders(
    asof: date = Query(..., description="as-of date; nothing published after it is read"),
    include_silent: bool = Query(False, description="add Context and Bench memberships"),
    group_by: GroupBy = Query(
        "link",
        description="the insider-cluster groups: link | business_type | sector | card",
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> LeadersOut:
    """Names across every card within 2% of their 52-week high, with relative strength at a 13-week high,
    coiling, with open-market insider buys in the last 30 days, or with RVOL 20D at 1.5 or more; the names on
    several of those lists; and the groups where at least two names had insider buys. One row per company.
    Display only; a past as-of reads what was public by the end of that date."""
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(conn, SLUG, (clock,), asof, lambda: read_inputs(conn, asof, known_at, clock))
    inputs = got.value
    identity = master.identity_for(conn, set(inputs.readings))
    result = compute_leaders(
        inputs, identity, asof=asof, group_by=group_by, include_silent=include_silent
    )
    payload = {f.name: getattr(result, f.name) for f in fields(result)}
    return LeadersOut.model_validate(
        {
            **payload,
            "near_high_pct": NEAR_HIGH_PCT,
            "rvol_loud": RVOL_LOUD,
            "insider_min_buys": INSIDER_MIN_BUYS,
            "cluster_min_buyers": CLUSTER_MIN_BUYERS,
            "group_min_names": GROUP_MIN_NAMES,
            "multi_min_lists": MULTI_MIN_LISTS,
            "full_year_bars": FULL_YEAR_BARS,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
