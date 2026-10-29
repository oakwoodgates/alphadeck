"""The Cross Radar dashboard's API (``docs/dashboards/cross-radar.md``): one GET over every live card, at the as-of
dial. It READS the cards and their rosters as of the as-of (``dashboards/baskets.py``, the Rotation Map's own
read), walks each company's price history once on the public clock (with its ``sma_position`` and
``relative_strength`` display members), and hands the result to the pure grouping in
``dashboards/cross_radar.py``. The heavy read is cached per as-of in the shared dashboard cache
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
    display_members,
    memberships_of,
    per_company,
    resolved_ids,
)
from dashboards.cache import cached
from dashboards.cross_radar import (
    CLUSTER_MIN_NAMES,
    CLUSTER_MIN_SHARE_PCT,
    FIRST_MOVER_MIN_PRICED,
    HISTORY_SESSIONS,
    MEMBERS,
    NEAR_BAND_PCT,
    NEAR_LOOKBACK,
    RECENT_SESSIONS,
    CompanyCrosses,
    RadarInputs,
    company_crosses,
    recent_dates,
    timeline_sessions,
)
from dashboards.cross_radar import cross_radar as compute_cross_radar
from db.session import DEFAULT_TENANT_ID
from domain.market_time import serve_view
from securities import master
from signals.base import PointInTimeData
from signals.display import sma
from signals.horizons import display_bounds

router = APIRouter(prefix="/cross-radar")

SLUG = "cross-radar"


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class CrossCardOut(_Wire):
    """A card a name sits on in this group: its ticker there (the Cockpit's ``?name=`` key) and its links
    there (``null`` = Unsegmented)."""

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: list[str | None]


class CrossGroupOut(_Wire):
    """One group. Link view: ``link`` (``null`` = Unsegmented) + its card. Card view: the card. Business type
    / Sector view: ``business_type`` / ``supersector`` (``null`` = Unclassified). ``names`` = the companies in
    the group, each counted once; ``priced`` of them have a 200-day SMA at the as-of."""

    key: str
    label: str
    link: str | None
    thesis_id: UUID | None
    thesis_name: str | None
    business_type: str | None
    supersector: str | None
    names: int
    priced: int
    not_priced: int


class CrossNameOut(_Wire):
    """A name's recent cross: its date and how many sessions before the as-of it printed."""

    security_id: UUID
    ticker: str
    cards: list[CrossCardOut]
    d: date
    sessions_ago: int
    rs_leading: bool | None


class CrossClusterOut(_Wire):
    """A group where ``count`` of its ``priced`` names crossed ``direction`` within ``recent_sessions`` (at
    least ``cluster_min_names`` and ``cluster_min_share_pct``% of them), all within ``within`` sessions, with
    ``rs_leaders`` of them at a 13-week relative-strength high."""

    group: CrossGroupOut
    direction: Literal["golden", "death"]
    count: int
    priced: int
    share: float
    within: int
    rs_leaders: int
    names: list[CrossNameOut]


class CrossFirstMoverOut(_Wire):
    """A group where exactly one priced name crossed ``direction`` within ``recent_sessions``. The group's
    ``rest`` priced names: ``closing_in`` (near-crossing the same way), ``already`` (on the far side with no
    recent cross: above the 200d for golden, below for death) and ``not_yet``."""

    group: CrossGroupOut
    direction: Literal["golden", "death"]
    leader: CrossNameOut
    rest: int
    closing_in: int
    already: int
    not_yet: int


class CrossNearNameOut(_Wire):
    """A name whose 50d sits within ``near_band_pct``% of its 200d, the gap smaller than ``near_lookback``
    sessions before, with no cross within ``recent_sessions``. ``gap_pct`` = (SMA50 / SMA200 − 1) %;
    ``change`` = ``gap_pct − gap_pct_then``, in points. The tape only, never a forecast."""

    security_id: UUID
    ticker: str
    cards: list[CrossCardOut]
    toward: Literal["golden", "death"]
    gap_pct: float
    gap_pct_then: float
    change: float


class CrossNearGroupOut(_Wire):
    group: CrossGroupOut
    names: list[CrossNearNameOut]


class CrossMarkOut(_Wire):
    """One cross on the timeline: ``pos`` is its column on ``sessions`` (0 = the oldest)."""

    d: date
    direction: Literal["golden", "death", "up", "down"]
    sessions_ago: int
    pos: int


class CrossTimelineNameOut(_Wire):
    """One company of a timeline row. ``crosses`` = every 50d × 200d cross of the last ``history_sessions``
    sessions, newest first (``readable_sessions`` of them could be read: a name with a shorter tape reads a
    shorter history); ``price_200d`` = its price × 200d crosses when that layer is on. A name ``not_priced``
    (``short_tape`` / ``stale_tape``) reads no crosses."""

    security_id: UUID
    ticker: str
    cards: list[CrossCardOut]
    priced: bool
    not_priced: Literal["short_tape", "stale_tape"] | None
    bars: int
    last_bar: date | None
    readable_sessions: int
    crosses: list[CrossMarkOut]
    price_200d: list[CrossMarkOut]
    gap_pct: float | None
    posture: Literal["above_rising", "above_falling", "below_rising", "below_falling", "other"]
    rs_leading: bool | None


class CrossPostureOut(_Wire):
    """The group's names by ``sma_position`` posture; ``other`` = every other posture and every name not
    priced."""

    above_rising: int
    above_falling: int
    below_rising: int
    below_falling: int
    other: int


class CrossTimelineRowOut(_Wire):
    """One group's crosses over the last ``history_sessions`` sessions (``golden`` / ``death`` count every
    cross), the price × 200d counts when that layer is on, and its posture bar."""

    group: CrossGroupOut
    golden: int
    death: int
    price_up: int
    price_down: int
    posture: CrossPostureOut
    members: list[CrossTimelineNameOut]


class CrossRadarOut(_Wire):
    """The whole page in one view, each list in its default order (clusters by share then count; first
    movers newest cross first; near-cross by the smallest gap; the timeline by total crosses). The counts are
    the work shown on its quiet line; the rule dials ride along. ``sessions`` = the timeline's axis.
    ``cache`` / ``compute_ms`` = whether the heavy read was served from the shared dashboard cache and how
    long it took when it ran."""

    asof: date
    group_by: Literal["link", "business_type", "sector", "card"]
    include_silent: bool
    price_200d: bool
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int
    companies: int
    priced_companies: int
    short_tape_companies: int
    stale_tape_companies: int
    sessions: list[date]
    clusters: list[CrossClusterOut]
    first_movers: list[CrossFirstMoverOut]
    near_cross: list[CrossNearGroupOut]
    timeline: list[CrossTimelineRowOut]
    recent_sessions: int
    history_sessions: int
    cluster_min_names: int
    cluster_min_share_pct: int
    first_mover_min_priced: int
    near_band_pct: float
    near_lookback: int
    stale_gap_days: int
    cache: Literal["hit", "computed"]
    compute_ms: int


def _readings(
    conn: psycopg.Connection,
    sids: list[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> tuple[dict[UUID, CompanyCrosses], set[date]]:
    """Each company's cross history and readings, computed ONCE from the same point-in-time view the Cockpit's
    ``/display-signals`` builds (``known_at`` + ``clock`` from ``serve_view``), plus every session a priced
    company printed in its last ``HISTORY_SESSIONS`` bars (for the timeline's axis)."""
    members = display_members(MEMBERS, "Cross Radar")
    seen: set[date] = set()

    def read(pit: PointInTimeData, sid: UUID) -> CompanyCrosses:
        bars = pit.price_history(sid, lookback_days=sma.LOOKBACK_DAYS)
        sigs = {n: m(pit, sid, asof) for n, m in members.items()}
        got = company_crosses(bars, asof, sigs["sma_position"], sigs["relative_strength"])
        if got.priced:
            seen.update(recent_dates(bars))
        return got

    return per_company(conn, sids, asof, known_at, clock, read), seen


def _spy_dates(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> list[date]:
    """SPY's last ``HISTORY_SESSIONS`` sessions up to the as-of, read like the members read a benchmark."""
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=display_bounds(),
        clock=clock,
    )
    return recent_dates(pit.benchmark_prices("SPY", lookback_days=sma.LOOKBACK_DAYS))


def read_inputs(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> RadarInputs:
    """The heavy read the cache holds: the counted cards' rosters as of the as-of, every company's cross
    history and readings, the timeline's sessions."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    readings, seen = _readings(conn, resolved_ids(memberships), asof, known_at, clock)
    return RadarInputs(
        live_cards=counted.live_count,
        cards_counted=len(counted.cards),
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        memberships=memberships,
        readings=readings,
        sessions=timeline_sessions(_spy_dates(conn, asof, known_at, clock), seen),
    )


@router.get("", response_model=CrossRadarOut)
def cross_radar(
    asof: date = Query(..., description="as-of date; no price published after it is read"),
    group_by: GroupBy = Query(
        "link", description="the groups: link | business_type | sector | card"
    ),
    include_silent: bool = Query(
        False, description="add Context and Bench memberships (their own groups in the Link view)"
    ),
    price_200d: bool = Query(
        False,
        description="add price-crosses-the-200d ticks to the timeline (never counted in a cluster)",
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> CrossRadarOut:
    """Golden and death crosses (the 50-day SMA crossing the 200-day) by link, business type, sector or card:
    clusters (several names crossing the same way within 10 sessions), first movers (exactly one), names
    within 2% of a cross and narrowing, and every cross of the last 90 sessions with each group's posture.
    Display only; a past as-of reads what was public by the end of that date."""
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
    result = compute_cross_radar(
        inputs,
        identity,
        asof=asof,
        group_by=group_by,
        include_silent=include_silent,
        price_200d=price_200d,
    )
    payload = {f.name: getattr(result, f.name) for f in fields(result)}
    return CrossRadarOut.model_validate(
        {
            **payload,
            "recent_sessions": RECENT_SESSIONS,
            "history_sessions": HISTORY_SESSIONS,
            "cluster_min_names": CLUSTER_MIN_NAMES,
            "cluster_min_share_pct": CLUSTER_MIN_SHARE_PCT,
            "first_mover_min_priced": FIRST_MOVER_MIN_PRICED,
            "near_band_pct": NEAR_BAND_PCT,
            "near_lookback": NEAR_LOOKBACK,
            "stale_gap_days": sma.STALE_GAP_DAYS,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
