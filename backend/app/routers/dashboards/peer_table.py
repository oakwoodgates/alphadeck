"""The Peer Table dashboard's API (``docs/dashboards/peer-table.md``): the names across the cards counted at the
as-of side by side, one scope at a time (a business type, a supersector, one card, or every card). It READS the cards
that existed by the as-of with their rosters then (``dashboards/baskets.py``), the master identity of the companies on
them, and, for the companies in the picked scope only, each one's facts and display-member readings through the same
point-in-time view the Cockpit builds (``baskets.per_company``: the request's one ``serve_view(asof)`` pair is
threaded into every read), SPY's trailing returns and which companies have a data flag that reaches their price
readings (``dashboards.baskets.price_flags``), and hands everything to the pure compute in
``dashboards/peer_table.py``. The scope lists are computed once per (clock, as-of) and each scope's table once per
(scope, key, clock, as-of) in the shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7); sort
and filters are the page's. Its wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime
from typing import Any, Literal, NamedTuple
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
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
from dashboards.cache import Cached, cached
from dashboards.peer_table import (
    BENCHMARK,
    COLUMNS,
    CardRef,
    Column,
    CompanyReads,
    Gap,
    GroupKind,
    PeerInputs,
    PeerTable,
    ScopeBase,
    ScopeKind,
    Scopes,
    identities,
    scope_present,
    scope_sids,
    scopes,
)
from dashboards.peer_table import peer_table as compute_peer_table
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.market_cap import CapTier
from domain.market_time import serve_view
from domain.splits import listed_from_row
from securities import master
from signals.base import PointInTimeData
from signals.display import insider_flow, range52w, rvol, sma, trailing_returns, volume_regime
from signals.display.base import DisplayMember, DisplaySignal
from signals.horizons import display_bounds

router = APIRouter(prefix="/peer-table")

SLUG = "peer-table"

# the six display members the table reads, by registered name (a renamed member fails loud, never reads as a blank)
MEMBER_NAMES = (
    range52w.MEMBER_NAME,
    sma.MEMBER_NAME,
    rvol.MEMBER_NAME,
    volume_regime.MEMBER_NAME,
    trailing_returns.MEMBER_NAME,
    insider_flow.MEMBER_NAME,
)


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


# --- the scope lists ------------------------------------------------------------------------------------------


class PeerKeyOptionOut(_Wire):
    """A business-type leaf or a supersector present on the counted rosters (``unclassified`` = no business type),
    with the distinct companies on it."""

    key: str
    companies: int


class PeerCardOptionOut(_Wire):
    """A counted card in the picker: its real links today (Context and Bench not counted) and its distinct
    companies on the roster at the as-of."""

    thesis_id: UUID
    name: str
    links: int
    companies: int


class PeerScopesOut(_Wire):
    """What can be picked at the as-of: the business-type leaves and supersectors present on the live rosters
    (biggest first, Unclassified last) and the counted cards (A to Z), each with its companies. ``default_scope`` /
    ``default_key`` = the leaf to open on (the first with two or more companies); ``null`` with no company at all.
    ``cards_counted`` = the live cards that existed by the as-of; ``roster_fallback_cards`` = counted cards with no
    roster snapshot by then (today's roster stands in). ``companies`` = distinct resolved companies on them;
    ``unresolved_rows`` = roster rows with no resolved security (never a company). ``clock``: ``record`` (the live
    read) or ``public`` (a past as-of: what was public by the end of that date). ``cache`` / ``compute_ms`` = whether
    the lists came from the shared dashboard cache and how long they took when computed."""

    asof: date
    clock: Literal["record", "public"]
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    companies: int
    unresolved_rows: int
    business_types: list[PeerKeyOptionOut]
    supersectors: list[PeerKeyOptionOut]
    cards: list[PeerCardOptionOut]
    default_scope: ScopeKind | None
    default_key: str | None
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the table ------------------------------------------------------------------------------------------------


class PeerCellOut(_Wire):
    """A number cell: ``value`` is stated, or ``gap`` says why it is blank (``flagged`` | ``no_price`` |
    ``no_reading`` | ``no_shares`` | ``ads_unread`` | ``no_cap`` | ``no_revenue`` | ``no_prior_year`` | ``no_burn`` |
    ``no_convert`` | ``no_mix`` | ``no_form4`` | ``no_spy`` | ``ends_differ``) and ``note`` is the reader's own
    words (a display member's "n/a: 120/200 bars", the flags' labels). Blank is never zero. The unit is the
    column's: 52-week-high and the two averages in percent, RVOL as a ratio, dollar volume in USD, overhang in
    percent of shares."""

    value: float | None
    gap: Gap | None
    note: str | None


class PeerCapOut(_Wire):
    """The approximate market cap (USD) and its tier: the latest close times the latest shares fact, the
    Workbench's rule (``ads_ratio_status`` known divides the ordinary count by ``ads_ratio``, unread withholds the
    cap, none counts 1:1), with ``split_factor`` = the multiplier put on the count when a split in force sits
    between the count's date and the close's. ``shares`` = the ordinary count on file."""

    value: float | None
    tier: CapTier | None
    gap: Gap | None
    note: str | None
    close: float | None
    close_date: date | None
    shares: float | None
    shares_date: date | None
    ads_ratio: int | None
    split_factor: float | None


class PeerRevenueOut(_Wire):
    """The latest period's revenue (USD) from the XBRL series, with its period end."""

    value: float | None
    period_end: date | None
    gap: Gap | None


class PeerYoyOut(_Wire):
    """The change in percent against the row for the same period end a year earlier (within 10 days) in the same
    read, with the prior figure it rests on."""

    value: float | None
    prior_value: float | None
    prior_period_end: date | None
    gap: Gap | None


class PeerRunwayOut(_Wire):
    """Months of cash at the current burn (cash / (quarterly burn / 3)); ``cash_positive`` when the burn is zero or
    negative (no finite figure). ``as_of`` = the fact's date."""

    months: float | None
    cash_positive: bool
    cash_usd: float | None
    burn_usd: float | None
    as_of: date | None
    gap: Gap | None


class PeerPurityOut(_Wire):
    """The largest revenue-mix line, in percent, with its segment label."""

    value: float | None
    segment: str | None
    gap: Gap | None


class PeerSpyOut(_Wire):
    """The 30-trading-day return minus SPY's, in percentage points (``value``), the two returns it is made of and
    the date of the company's last close."""

    value: float | None
    ret_pct: float | None
    spy_pct: float | None
    last_close: date | None
    gap: Gap | None
    note: str | None


class PeerInsiderOut(_Wire):
    """Open-market buys in the member's 90-day window as the Cockpit's column screens them: count, dollars and
    distinct buyers. ``buys`` 0 is a stated zero; ``gap`` ``no_form4`` = no Form 4 ingested (a blank).
    """

    buys: int | None
    usd: float | None
    buyers: int | None
    gap: Gap | None
    note: str | None


class PeerCardChipOut(_Wire):
    """A card the company sits on: its ticker there (the Cockpit's ``?name=`` key), its links there (``null`` = no
    link; Context / Bench by their labels), whether it is silent there and its context role there.
    """

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: list[str | None]
    silent: bool
    role: ContextRole | None


class PeerCompanyOut(_Wire):
    """One company, once. ``key`` = its ``security_id``. ``business_type`` / ``supersector`` = today's identity
    (``null`` = unclassified), ``royalty`` = the royalty/streaming overlay. ``roles`` = the distinct context roles
    it holds; ``silent_only`` = every row is Context or Bench; ``workbench_facts`` = it holds a shares, cash/burn,
    revenue-mix or convertible-note fact; ``flag`` = why its price-derived cells are blank (its row stays).
    """

    key: str
    security_id: UUID
    ticker: str
    name: str | None
    business_type: str | None
    supersector: str | None
    royalty: bool
    cards: list[PeerCardChipOut]
    roles: list[ContextRole]
    silent_only: bool
    rows: int
    workbench_facts: bool
    flag: str | None
    cap: PeerCapOut
    revenue: PeerRevenueOut
    yoy: PeerYoyOut
    runway: PeerRunwayOut
    overhang: PeerCellOut
    purity: PeerPurityOut
    off_high: PeerCellOut
    sma50: PeerCellOut
    sma200: PeerCellOut
    rvol20: PeerCellOut
    adv: PeerCellOut
    vs_spy: PeerSpyOut
    insider: PeerInsiderOut


class PeerMedianOut(_Wire):
    """A median and the n it rests on (``value`` null with ``n`` 0)."""

    value: float | None
    n: int


class PeerGroupStatsOut(_Wire):
    """What a group row says, each figure with its n: the names; the median approximate cap; the names above the
    50-day average of those with a 50-day reading; the median 30-day return minus SPY's; the median YoY; the names
    with a runway figure. Medians only, equal weight, no score."""

    names: int
    cap: PeerMedianOut
    above_50d: int
    priced_50d: int
    vs_spy: PeerMedianOut
    yoy: PeerMedianOut
    runway: int


class PeerGroupOut(_Wire):
    """A group header row. ``kind``: ``supersector`` | ``business_type`` (the leaf) | ``link`` | ``old_link`` (a
    roster link no longer on the card) | ``no_link`` | ``silent`` (Context or Bench); ``label`` = the key (the page
    words it). ``depth`` 1 = a leaf under a supersector (the all-cards scope). ``members`` = the keys of the
    companies listed under it, names A to Z (a supersector header lists none: its leaves do). ``stats`` = every name
    in the group; ``stats_counting`` = the names that count (not silent-only)."""

    key: str
    label: str
    kind: GroupKind
    depth: int
    members: list[str]
    stats: PeerGroupStatsOut
    stats_counting: PeerGroupStatsOut


class PeerBlankOut(_Wire):
    gap: Gap
    n: int


class PeerCoverageOut(_Wire):
    """A column's coverage: ``stated`` of ``total`` names, the blanks by reason (most first)."""

    column: Column
    stated: int
    total: int
    blanks: list[PeerBlankOut]


class PeerUnresolvedOut(_Wire):
    """A roster row with no resolved security: counted and listed, never a row."""

    ticker: str
    thesis_id: UUID
    thesis_name: str


class PeerFlaggedOut(_Wire):
    """A company in scope whose data flags reach its price readings at the as-of: its row stays, its price-derived
    cells name ``reason`` (the flags' labels)."""

    security_id: UUID
    ticker: str
    reason: str


class PeerSpyRefOut(_Wire):
    """SPY's own 30-trading-day return at the as-of, the benchmark every vs-SPY cell subtracts."""

    ret_30d: float | None
    end: date | None


class PeerTableOut(_Wire):
    """One scope's table. ``scope`` + ``key``: the picked scope (``key`` null for ``all``); ``scope_name`` = the
    card's name in a card scope. ``companies`` = every company in scope once, in the default order (group order,
    names A to Z inside); ``groups`` list their keys. ``coverage`` / ``coverage_counting`` = every column's coverage
    over every company / over the names that count. ``scope_cards`` = counted cards holding at least one company in
    scope; ``rows`` = their roster rows in view; ``silent_only`` = companies whose every row is Context or Bench.
    ``unresolved`` = roster rows with no resolved security (a card scope: that card's; else every counted card's).
    ``flagged`` = companies in scope with a data flag that reaches their price readings. ``clock``: ``record`` (the
    live read) or ``public`` (a past as-of). ``cache`` / ``compute_ms`` = whether the table came from the shared
    dashboard cache and how long it took when computed."""

    asof: date
    clock: Literal["record", "public"]
    scope: ScopeKind
    key: str | None
    scope_name: str | None
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    scope_cards: int
    rows: int
    silent_only: int
    companies: list[PeerCompanyOut]
    groups: list[PeerGroupOut]
    unresolved: list[PeerUnresolvedOut]
    unresolved_rows: int
    flagged: list[PeerFlaggedOut]
    coverage: list[PeerCoverageOut]
    coverage_counting: list[PeerCoverageOut]
    spy: PeerSpyRefOut | None
    benchmark: str
    columns: list[Column]
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) ----------------------------------------------------


class ScopeRead(NamedTuple):
    """What the ``("scopes", clock)`` cache entry holds: the roster-level read and the lists derived from it."""

    base: ScopeBase
    found: Scopes


def read_base(conn: psycopg.Connection, asof: date, known_at: datetime | None) -> ScopeBase:
    """The cheap roster-level read: the live cards that existed by the as-of with their rosters then, and the
    master identity of every resolved company on them (one SELECT)."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    sids = resolved_ids(memberships)
    return ScopeBase(
        live_cards=counted.live_count,
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        cards=tuple(
            CardRef(t.id, t.name, tuple(s.label for s in t.segments)) for t in counted.cards
        ),
        memberships=memberships,
        identity=identities(master.identity_for(conn, set(sids))) if sids else {},
    )


def _scope_read(conn: psycopg.Connection, asof: date, known_at: datetime | None) -> ScopeRead:
    base = read_base(conn, asof, known_at)
    return ScopeRead(base, scopes(base, asof))


def read_company(
    pit: PointInTimeData,
    sid: UUID,
    asof: date,
    members: dict[str, DisplayMember],
    flag: str | None,
) -> CompanyReads:
    """One company's readings from the point-in-time view. A company whose data flags reach its price readings
    has its price-derived readings left unread (``flag`` names why); the insider member stays, as the plan keeps
    it."""
    close: tuple[date, float] | None = None
    bars: list[dict[str, Any]] = []
    if flag is None:
        bars = [b for b in pit.price_history(sid) if b.get("close") is not None]
        if bars:
            close = (bars[-1]["d"], float(bars[-1]["close"]))
    shares_facts = pit.shares_outstanding_facts(sid)
    shares = None
    if shares_facts:
        best = max(shares_facts, key=lambda f: f["valid_from"])
        shares = {k: best.get(k) for k in ("shares", "valid_from", "ads_ratio", "ads_ratio_status")}
    cash_facts = pit.cash_burn_facts(sid)
    price = flag is None

    def read(name: str) -> DisplaySignal | None:
        return members[name](pit, sid, asof)

    return CompanyReads(
        flag=flag,
        close=close,
        shares=shares,
        # the split list only matters to the cap (a count and a close both on file)
        splits=(
            tuple(listed_from_row(r) for r in pit.split_events(sid))
            if shares is not None and close is not None
            else ()
        ),
        revenue=tuple(
            {
                "period_end": r["period_end"],
                "value": float(r["value"]),
                "valid_from": r["valid_from"],
            }
            for r in pit.fundamentals_facts(sid)
            if r["metric_key"] == "revenue"
        ),
        cash_burn=max(cash_facts, key=lambda f: f["valid_from"]) if cash_facts else None,
        dilution=tuple(pit.dilution_facts(sid)),
        mix=tuple(pit.revenue_mix_facts(sid)),
        range_52w=read(range52w.MEMBER_NAME) if price else None,
        sma=read(sma.MEMBER_NAME) if price else None,
        rvol=read(rvol.MEMBER_NAME) if price else None,
        volume=read(volume_regime.MEMBER_NAME) if price else None,
        returns=read(trailing_returns.MEMBER_NAME) if price else None,
        insider=read(insider_flow.MEMBER_NAME),
    )


def _spy(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> DisplaySignal | None:
    """SPY's trailing returns by the SAME pure rule the member runs (``trailing_returns.compute``), over SPY's
    as-of bars (``pit.benchmark_prices``); ``None`` = no SPY bars at the as-of."""
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=display_bounds(),
        clock=clock,
    )
    bars = pit.benchmark_prices(BENCHMARK, lookback_days=trailing_returns.LOOKBACK_DAYS)
    return trailing_returns.compute(bars, asof)


def read_reads(
    conn: psycopg.Connection,
    sids: list[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> tuple[dict[UUID, CompanyReads], DisplaySignal | None]:
    """The heavy read, for the companies in scope only: which of them have a price-reaching data flag, then each
    company's readings (``baskets.per_company``, chunked, the request's ``known_at`` + ``clock``), and SPY's.
    """
    members = display_members(MEMBER_NAMES, "Peer Table")
    flagged = price_flags(conn, sids, asof, known_at=known_at, clock=clock)
    reads = per_company(
        conn,
        sids,
        asof,
        known_at,
        clock,
        lambda pit, sid: read_company(pit, sid, asof, members, flagged.get(sid)),
    )
    return reads, _spy(conn, asof, known_at, clock)


def read_table(
    conn: psycopg.Connection,
    base: ScopeBase,
    scope: ScopeKind,
    key: str | None,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> PeerTable:
    reads, spy = read_reads(conn, scope_sids(base, scope, key), asof, known_at, clock)
    return compute_peer_table(PeerInputs(base, reads, spy), scope, key, asof)


def _payload(obj: Any) -> dict[str, Any]:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


# --- the routes -----------------------------------------------------------------------------------------------


def _scopes_cached(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> Cached[ScopeRead]:
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    return cached(conn, SLUG, ("scopes", clock), asof, lambda: _scope_read(conn, asof, known_at))


@router.get("/scopes", response_model=PeerScopesOut)
def peer_table_scopes(
    asof: date = Query(
        ..., description="as-of date; only the cards that existed by then, with their rosters then"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> PeerScopesOut:
    """What the Peer Table can be scoped to at the as-of: the business-type leaves and supersectors present on the
    counted cards' rosters (each with its distinct companies) and the counted cards, plus the leaf to open on.
    Display only; a past as-of reads the rosters as they were by the end of that date."""
    known_at, clock = serve_view(asof)
    got = _scopes_cached(conn, asof, known_at, clock)
    return PeerScopesOut.model_validate(
        {
            **_payload(got.value.found),
            "clock": clock,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )


@router.get("", response_model=PeerTableOut)
def peer_table(
    asof: date = Query(..., description="as-of date; nothing published after it is read"),
    scope: ScopeKind = Query(..., description="business_type | supersector | card | all"),
    key: str | None = Query(
        None,
        description="the scope's pick: a business-type leaf, a supersector, or a card id "
        "(``unclassified`` = none); ignored for ``all``",
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> PeerTableOut:
    """The names of one scope side by side, each company once: approximate market cap and tier, latest revenue and
    its year-over-year change, runway, convert overhang, revenue purity, the tape (off the 52-week high, the
    50- and 200-day averages, RVOL, dollar volume, the 30-day return against SPY) and the insider buys of the
    90-day window, grouped by business type, link or card, with every blank counted by column and reason. A
    company whose data flags reach its price readings keeps its row and its price cells name the reason. Display
    only; a past as-of reads what was public by the end of that date (a fact public after it never reaches a
    cell). 404 when the scope's key is not present at the as-of."""
    known_at, clock = serve_view(asof)
    if scope != "all" and key is None:
        raise HTTPException(status_code=422, detail=f"scope {scope} needs a key")
    read = _scopes_cached(conn, asof, known_at, clock).value
    if not scope_present(read.found, scope, key):
        raise HTTPException(status_code=404, detail="scope not present at this as-of")
    pick = None if scope == "all" else key
    got = cached(
        conn,
        SLUG,
        (scope, pick or "", clock),
        asof,
        lambda: read_table(conn, read.base, scope, pick, asof, known_at, clock),
    )
    page = got.value
    unresolved = [_payload(u) for u in page.unresolved]
    return PeerTableOut.model_validate(
        {
            **_payload(page),
            "clock": clock,
            "unresolved": unresolved,
            "unresolved_rows": len(unresolved),
            "benchmark": BENCHMARK,
            "columns": list(COLUMNS),
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
