"""The Market Map dashboard's API (``docs/dashboards/market-map.md``): one GET over every live card, at the as-of
dial. It READS the cards (``cards_as_of``: archived excluded, each card's roster as of the as-of), reads which
companies' data flags reach their price readings (``dashboards.baskets.price_flags``), reads each remaining
company's bars, share counts and splits ONCE through the same point-in-time view the Cockpit builds, and hands the
result to the pure ``dashboards/market_map.py``. The heavy read is cached per as-of in the shared dashboard cache
(``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7). The payload is READINGS, not a layout: the page groups,
weighs, colors and lays out the tiles itself. Its wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

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
    cards_as_of,
    display_members,
    memberships_of,
    per_company,
    price_flags,
    resolved_ids,
)
from dashboards.cache import cached
from dashboards.market_map import (
    DOLLAR_VOLUME_BARS,
    WINDOWS,
    CompanyReading,
    MapInputs,
    benchmark_returns,
    company_cap,
    company_reading,
    pick_shares,
)
from dashboards.market_map import market_map as compute_market_map
from db.session import DEFAULT_TENANT_ID
from domain.market_time import serve_view
from domain.splits import listed_from_row
from securities import master
from signals.base import PointInTimeData
from signals.display.base import DisplayMember
from signals.horizons import display_bounds

router = APIRouter(prefix="/market-map")

SLUG = "market-map"
MEMBER = "trailing_returns"
BENCHMARKS: tuple[str, ...] = ("SPY", "IWM")


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class MapCardOut(_Wire):
    """A counted card that has a drawn company."""

    thesis_id: UUID
    name: str


class MapMembershipOut(_Wire):
    """One basket row of a drawn company: its card, the ticker it carries there (the Cockpit's ``?name=`` key),
    its link (``null`` = Unsegmented) and whether the row is Context or Bench (``silent``: it never counts
    toward a call; the page shows it only when asked)."""

    security_id: UUID
    thesis_id: UUID
    ticker: str
    link: str | None
    silent: bool


class MapCompanyOut(_Wire):
    """One company's readings at the as-of. ``returns`` holds the six windows (``ret_1d`` ... ``ret_90d``, ``ytd``,
    ``ret_1y``; percent, trading bars for the trailing ones), ``null`` where there is no reading, with the reason
    in ``gaps``. ``cap`` = the approximate market cap in USD with its band (``cap_tier``) and the share count it
    rests on (``cap_source``: ``workbench`` = ratified in the Workbench, ``cover`` = the latest count from a
    filing's cover page, not ratified); ``null`` with ``cap_gap`` / ``cap_note`` when there is none.
    ``dollar_volume`` = the mean of close x volume over the last 20 sessions. ``business_type`` / ``supersector``
    are today's identity; ``silent_only`` = every row of the company is Context or Bench."""

    security_id: UUID
    ticker: str
    name: str | None
    business_type: str | None
    supersector: str | None
    silent_only: bool
    returns: dict[str, float | None]
    gaps: dict[str, str]
    priced: bool
    cap: float | None
    cap_tier: Literal["nano", "micro", "small", "mid", "large"] | None
    cap_source: Literal["workbench", "cover"] | None
    cap_gap: str | None
    cap_note: str | None
    dollar_volume: float | None
    dollar_volume_note: str | None


class MapFlaggedOut(_Wire):
    """A company left off the map: its data flags reach its price readings at the as-of (the reason the Cockpit
    grays its price cells). ``reason`` = the flags' labels."""

    security_id: UUID
    ticker: str
    reason: str
    silent_only: bool


class MarketMapOut(_Wire):
    """The whole page in one payload. ``companies`` holds every drawable company's readings, ``memberships`` the
    cards and links each sits on; the page builds every grouping from them. ``spy`` / ``iwm`` = the benchmark's own
    returns for the same six windows (``null`` = no tape at the as-of). The counts are the work shown on the
    page's quiet line. ``cache`` / ``compute_ms`` = whether the heavy read was served from the shared dashboard
    cache and how long it took when it ran."""

    asof: date
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int
    silent_rows: int
    unresolved_rows: int
    unresolved_silent_rows: int
    companies_total: int
    cards: list[MapCardOut]
    memberships: list[MapMembershipOut]
    companies: list[MapCompanyOut]
    flagged: list[MapFlaggedOut]
    spy: dict[str, float | None] | None
    iwm: dict[str, float | None] | None
    windows: list[str]
    dollar_volume_bars: int
    cache: Literal["hit", "computed"]
    compute_ms: int


def _member() -> DisplayMember:
    return display_members((MEMBER,), "Market Map")[MEMBER]


def read_company(
    pit: PointInTimeData, sid: UUID, asof: date, member: DisplayMember
) -> CompanyReading:
    """One company from the point-in-time view: its as-of bars, the ``trailing_returns`` member, and the share
    count the cap rests on (the Workbench's, else the filing cover's; the cover read only when there is no
    Workbench count). The split list is read only when a count and a close are both on file."""
    bars = [b for b in pit.price_history(sid) if b.get("close") is not None]
    workbench = pit.shares_outstanding_facts(sid)
    fundamentals = pit.fundamentals_facts(sid) if not workbench else ()
    shares, source = pick_shares(workbench, fundamentals)
    close = (bars[-1]["d"], float(bars[-1]["close"])) if bars else None
    splits = (
        tuple(listed_from_row(r) for r in pit.split_events(sid))
        if shares is not None and close is not None
        else ()
    )
    return company_reading(
        bars, member(pit, sid, asof), asof, company_cap(close, shares, source, splits)
    )


def _readings(
    conn: psycopg.Connection,
    sids: list[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[UUID, CompanyReading]:
    member = _member()
    return per_company(
        conn, sids, asof, known_at, clock, lambda pit, sid: read_company(pit, sid, asof, member)
    )


def _benchmarks(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[str, dict[str, float | None] | None]:
    """SPY's and IWM's own returns: the member's pure ``compute`` (and the year to date) over the benchmark's bars,
    read exactly as a company's. ``None`` for a benchmark with no tape at the as-of."""
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=display_bounds(),
        clock=clock,
    )
    return {sym: benchmark_returns(pit.benchmark_prices(sym), asof) for sym in BENCHMARKS}


def read_inputs(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> MapInputs:
    """The heavy read the cache holds: the counted cards' rosters as of the as-of, the price flags (on the same
    view), every unflagged company's readings, the benchmarks' returns. A flagged company's price readings are
    never read (a scale break chains fake returns)."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    sids = resolved_ids(memberships)
    flagged = price_flags(conn, sids, asof, known_at=known_at, clock=clock)
    bench = _benchmarks(conn, asof, known_at, clock)
    return MapInputs(
        live_cards=counted.live_count,
        cards_counted=len(counted.cards),
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        memberships=memberships,
        readings=_readings(conn, [s for s in sids if s not in flagged], asof, known_at, clock),
        spy=bench["SPY"],
        iwm=bench["IWM"],
        flagged=flagged,
    )


@router.get("", response_model=MarketMapOut)
def market_map(
    asof: date = Query(
        ..., description="as-of date; no price or share count published after it is read"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> MarketMapOut:
    """Every company on the cards as a heat-map tile: per company the 1d / 7d / 30d / 90d / year-to-date / 1Y
    returns, the 20-day dollar volume and the approximate market cap with the share count it rests on, with the
    cards and links it sits on and SPY's and IWM's own returns. A company whose data flags reach its price
    readings is left out and listed. Display only; a past as-of reads what was public by the end of that date.
    """
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two reads
    got = cached(conn, SLUG, (clock,), asof, lambda: read_inputs(conn, asof, known_at, clock))
    inputs = got.value
    identity = master.identity_for(conn, set(inputs.readings))
    result = compute_market_map(inputs, identity, asof=asof)
    payload = {f.name: getattr(result, f.name) for f in fields(result)}
    return MarketMapOut.model_validate(
        {
            **payload,
            "spy": dict(result.spy) if result.spy is not None else None,
            "iwm": dict(result.iwm) if result.iwm is not None else None,
            "windows": list(WINDOWS),
            "dollar_volume_bars": DOLLAR_VOLUME_BARS,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
