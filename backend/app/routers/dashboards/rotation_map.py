"""The Rotation Map dashboard's API (``docs/dashboards/rotation-map.md``): one GET over every live card, at the
as-of dial. It READS the cards (``thesis_repo.list_all``, archived excluded; each card's roster as of the
as-of via ``get_asof``), runs four display members once per company on the public clock, reads which
companies' data flags reach their price readings (``dashboards.baskets.price_flags``, on the same view), and
hands the result to the pure grouping in ``dashboards/rotation_map.py``. The heavy read is cached per as-of in the
shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7), so one cold compute serves
every grouping and toggle. Its wire models live here, not in ``app/schemas_api.py`` (rule 8)."""

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
    price_flags,
    resolved_ids,
)
from dashboards.cache import cached
from dashboards.rotation_map import (
    CROSS_DAYS,
    MEMBERS,
    MIN_PRICED,
    CompanyTape,
    RotationInputs,
    company_tape,
    returns_of,
)
from dashboards.rotation_map import rotation_map as compute_rotation_map
from db.session import DEFAULT_TENANT_ID
from domain.market_time import serve_view
from securities import master
from signals.base import PointInTimeData
from signals.display import trailing_returns
from signals.display.base import DisplayMember
from signals.horizons import display_bounds

router = APIRouter(prefix="/rotation-map")

SLUG = "rotation-map"


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class RotationWindowOut(_Wire):
    """One return window of a group: the median over the names priced for it (``null`` when fewer than
    ``min_priced`` are), ``n`` priced, ``up`` of them with a positive return, ``above_spy`` of them beating
    SPY's own return (``null`` without one). ``tone`` marks a median in the top / bottom 10% of the groups in
    this view."""

    key: str
    median: float | None
    n: int
    up: int
    above_spy: int | None
    tone: Literal["pos", "neg"] | None


class RotationNameCardOut(_Wire):
    """A card a name sits on in this group: its ticker there (the Cockpit's ``?name=`` key) and its links
    there (``null`` = Unsegmented)."""

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: list[str | None]


class RotationNameOut(_Wire):
    """One company in a group. ``cross`` is its golden / death cross when it printed within ``cross_days``
    of the as-of. ``flag`` = why it is left out of every aggregate (a flagged price history; it then carries
    no reading); ``null`` = read."""

    security_id: UUID
    ticker: str
    cards: list[RotationNameCardOut]
    returns: dict[str, float | None]
    above_50d: bool | None
    rvol20: float | None
    cross: Literal["golden", "death"] | None
    cross_date: date | None
    rs_leading: bool | None
    flag: str | None


class RotationFlaggedOut(_Wire):
    """A company in view left out of every aggregate: its data flags reach its price readings at the as-of
    (the reason the Cockpit grays its price cells). ``reason`` = the flags' labels."""

    security_id: UUID
    ticker: str
    reason: str


class RotationGroupOut(_Wire):
    """One row of the map. Link view: ``link`` (``null`` = Unsegmented) + its card. Card view: the card.
    Business type / Sector view: ``business_type`` / ``supersector`` (``null`` = Unclassified). ``names`` =
    the companies in the group, each counted once; ``flagged`` = of those, left out of every aggregate for a
    flagged price history."""

    key: str
    link: str | None
    thesis_id: UUID | None
    thesis_name: str | None
    business_type: str | None
    supersector: str | None
    names: int
    flagged: int
    windows: list[RotationWindowOut]
    above_50d: int
    above_50d_n: int
    rvol20_median: float | None
    rvol20_n: int
    golden: int
    death: int
    rs_leaders: int
    rs_n: int
    members: list[RotationNameOut]


class RotationMapOut(_Wire):
    """The whole page in one view, groups in the default order (the 7d median, highest first, "—" last).
    The counts are the work shown on its quiet line. ``spy`` = SPY's own trailing returns (``null`` = no
    SPY tape at the as-of). ``flagged`` = the companies in view left out of every aggregate for a flagged
    price history, by ticker. ``cache`` / ``compute_ms`` = whether the heavy read was served from the shared
    dashboard cache and how long it took when it ran."""

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
    spy: dict[str, float | None] | None
    groups: list[RotationGroupOut]
    flagged: list[RotationFlaggedOut]
    min_priced: int
    cross_days: int
    cache: Literal["hit", "computed"]
    compute_ms: int


def _members() -> dict[str, DisplayMember]:
    return display_members(MEMBERS, "Rotation Map")


def _tapes(
    conn: psycopg.Connection,
    sids: list[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[UUID, CompanyTape]:
    """Each company's readings, computed ONCE, from the same point-in-time view the Cockpit's
    ``/display-signals`` builds (``known_at`` + ``clock`` from ``serve_view``: the public clock at a past
    as-of, the live read today)."""
    members = _members()
    return per_company(
        conn,
        sids,
        asof,
        known_at,
        clock,
        lambda pit, sid: company_tape({n: m(pit, sid, asof) for n, m in members.items()}),
    )


def _spy(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[str, float | None] | None:
    """SPY's own trailing returns: the trailing_returns member's pure ``compute`` over the benchmark's bars,
    read exactly as the member reads a company's. ``None`` when there is no SPY tape at the as-of.
    """
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=display_bounds(),
        clock=clock,
    )
    bars = pit.benchmark_prices("SPY", lookback_days=trailing_returns.LOOKBACK_DAYS)
    sig = trailing_returns.compute(bars, asof)
    return None if sig is None else returns_of(sig)


def read_inputs(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> RotationInputs:
    """The heavy read the cache holds: the counted cards' rosters as of the as-of, every company's readings
    and price flags (``price_flags``, on the same view), SPY's returns."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    sids = resolved_ids(memberships)
    return RotationInputs(
        live_cards=counted.live_count,
        cards_counted=len(counted.cards),
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        memberships=memberships,
        tapes=_tapes(conn, sids, asof, known_at, clock),
        spy=_spy(conn, asof, known_at, clock),
        flagged=price_flags(conn, sids, asof, known_at=known_at, clock=clock),
    )


@router.get("", response_model=RotationMapOut)
def rotation_map(
    asof: date = Query(..., description="as-of date; no price published after it is read"),
    group_by: GroupBy = Query("link", description="the rows: link | business_type | sector | card"),
    include_silent: bool = Query(
        False, description="add Context and Bench memberships (their own rows in the Link view)"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> RotationMapOut:
    """Which links, business types, sectors or cards have price moving: per group, the median 1d / 7d /
    30d / 90d / 1Y return with how many names are up, the share above the 50-day SMA, the median 20-day
    RVOL, golden / death crosses in the last 30 days and RS leaders, with each group's names. A name whose
    data flags reach its price readings is left out of every aggregate and listed. Display only; a past
    as-of reads what was public by the end of that date."""
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(conn, SLUG, (clock,), asof, lambda: read_inputs(conn, asof, known_at, clock))
    inputs = got.value
    identity = (
        master.identity_for(conn, set(inputs.tapes))
        if group_by in ("business_type", "sector")
        else {}
    )
    result = compute_rotation_map(
        inputs, identity, asof=asof, group_by=group_by, include_silent=include_silent
    )
    payload = {f.name: getattr(result, f.name) for f in fields(result)}
    return RotationMapOut.model_validate(
        {
            **payload,
            "spy": dict(result.spy) if result.spy is not None else None,
            "min_priced": MIN_PRICED,
            "cross_days": CROSS_DAYS,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
