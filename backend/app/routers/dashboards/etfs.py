"""The ETFs dashboard's API (``docs/dashboards/etfs.md``): one GET over the funds on every live card, at the as-of
dial. It READS the cards and their rosters as of the as-of (``dashboards/baskets.py``), which roster securities are
funds (``security_master.instrument_kind = 'etf'``), the funds' and the card companies' closes, the funds' display
readings and shares samples on the public clock, and which securities' data flags reach their price readings
(``dashboards.baskets.price_flags``), and hands them to the pure compute in ``dashboards/etfs.py``. The heavy read
is cached once per (clock, as-of) in the shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md``
rule 7); the window is sliced from it per request. No write, no LLM call and no network request: every panel reads
stored data. Its wire models live here, not in ``app/schemas_api.py`` (rule 8)."""

from __future__ import annotations

from dataclasses import asdict
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
from dashboards.etfs import (
    BENCHMARK,
    LOOKBACK_DAYS,
    MEMBERS,
    WINDOWS,
    EtfInputs,
    FundBundle,
    Window,
    etf_inputs,
    fund_readings,
)
from dashboards.etfs import etfs as compute_etfs
from db.session import DEFAULT_TENANT_ID
from domain.market_time import serve_view
from securities import master
from signals.base import PointInTimeData
from signals.horizons import display_bounds

router = APIRouter(prefix="/etfs")

SLUG = "etfs"

_ETF_SQL = "SELECT id FROM security_master WHERE tenant_id = %s AND id = ANY(%s) AND instrument_kind = 'etf'"


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class EtfReadingOut(_Wire):
    """One number and the reason it is missing: ``value`` null carries the display member's own note
    ("n/a: 140/200 bars")."""

    value: float | None
    note: str | None


class EtfCardRefOut(_Wire):
    """A card a fund sits on, with the fund's ticker there (the Cockpit's ``?name=`` key)."""

    thesis_id: UUID
    thesis_name: str
    ticker: str


class EtfIwmOut(_Wire):
    """IWM over the window's sessions: ``available`` false = no return, and ``reason`` says why."""

    available: bool
    reason: str | None
    ret: float | None


class EtfCompareRowOut(_Wire):
    """Panel 1, one (card, fund) pair. ``ret`` / ``companies_ret`` = the fund's and the card's companies'
    window returns (%), ``diff`` = fund minus companies in points (each ``null`` when it has none).
    ``companies`` = the card's companies (unresolved rows included), ``priced`` = those with at least one daily
    return in the window. ``bars`` / ``bars_needed`` explain a fund with no line (``note``). The two ``*_points``
    are the lines from 100, one per window session, ``null`` when absent."""

    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str
    fund_name: str | None
    ret: float | None
    companies_ret: float | None
    diff: float | None
    companies: int
    priced: int
    unresolved: int
    sessions_flat: int
    bars: int
    bars_needed: int
    note: str | None
    fund_points: list[float] | None
    company_points: list[float] | None


class EtfFlowRowOut(_Wire):
    """Panel 2, one fund: net creations minus redemptions over the trailing 7 and 30 days, in dollars and as a
    percent of shares (the ``etf_flow`` display member's own readings). ``filling_*`` = the sample series is
    younger than that window (history starts ``first_sample``)."""

    security_id: UUID
    ticker: str
    name: str | None
    cards: list[EtfCardRefOut]
    flow_1w_usd: EtfReadingOut
    flow_1w_pct: EtfReadingOut
    flow_1m_usd: EtfReadingOut
    flow_1m_pct: EtfReadingOut
    filling_1w: bool
    filling_1m: bool
    samples: int
    first_sample: date | None
    latest_sample: date | None
    source: str | None


class EtfSizeRowOut(_Wire):
    """Panel 3, one fund: approximate size (the latest shares sample times the close on or before its date; not
    the filed net assets) and the 20-day average dollar volume."""

    security_id: UUID
    ticker: str
    name: str | None
    cards: list[EtfCardRefOut]
    shares: float | None
    sample_date: date | None
    size_usd: float | None
    size_note: str | None
    adv_usd_20d: EtfReadingOut


class EtfTrendRowOut(_Wire):
    """Panel 4, one fund: the Cockpit's price readings (30-day return, distance from the 50- and 200-day
    averages, 20-day relative volume, position in the 52-week range, 0 low to 100 high)."""

    security_id: UUID
    ticker: str
    name: str | None
    cards: list[EtfCardRefOut]
    ret_30d: EtfReadingOut
    vs_50d: EtfReadingOut
    vs_200d: EtfReadingOut
    rvol20: EtfReadingOut
    range_pos: EtfReadingOut


class EtfCardNoFundOut(_Wire):
    thesis_id: UUID
    thesis_name: str


class EtfMultiCardFundOut(_Wire):
    security_id: UUID
    ticker: str
    name: str | None
    cards: list[EtfCardRefOut]


class EtfCoverageOut(_Wire):
    """Panel 5, card grain: ``cards_with_fund`` of ``cards_counted`` (``funds`` found), the cards with none
    (A to Z) and the funds on more than one card."""

    cards_counted: int
    cards_with_fund: int
    funds: int
    without: list[EtfCardNoFundOut]
    multi: list[EtfMultiCardFundOut]


class EtfFlaggedOut(_Wire):
    """A fund or company left out of every number that uses its price: its data flags reach its price readings
    at the as-of. ``reason`` = the flags' labels."""

    security_id: UUID
    ticker: str
    reason: str


class EtfsOut(_Wire):
    """The whole page for one window (only panel 1 depends on it). ``sessions`` = daily returns in the window
    (fewer than nominal when the tape is short), ``dates`` = its sessions. ``funds`` / ``companies`` = what was
    found (companies = the voting names of the cards that have a fund). ``flagged_funds`` / ``flagged_companies``
    are left out of the numbers that use their price. ``cache`` / ``compute_ms`` = whether the read came from the
    shared dashboard cache and how long it took when it ran."""

    asof: date
    window: Window
    sessions: int
    dates: list[date]
    session_source: Literal["benchmarks", "companies", "none"]
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    funds: int
    companies: int
    iwm: EtfIwmOut
    compare: list[EtfCompareRowOut]
    flows: list[EtfFlowRowOut]
    sizes: list[EtfSizeRowOut]
    trends: list[EtfTrendRowOut]
    coverage: EtfCoverageOut
    flagged_funds: list[EtfFlaggedOut]
    flagged_companies: list[EtfFlaggedOut]
    window_sessions: dict[str, int]
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------


def _etf_ids(conn: psycopg.Connection, sids: list[UUID]) -> set[UUID]:
    """The roster securities whose master row is an ETF (identity, not a fact)."""
    if not sids:
        return set()
    with conn.cursor() as cur:
        cur.execute(_ETF_SQL, (DEFAULT_TENANT_ID, sids))
        return {r["id"] for r in cur.fetchall()}


def _iwm(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[date, float]:
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
) -> EtfInputs:
    """The heavy read the cache holds: the counted cards' rosters as of the as-of, the funds among them (by
    kind), the funds' closes, readings and shares samples, the closes of the voting companies of the cards that
    have a fund, IWM's, and the price flags, all on the request's one ``serve_view`` pair."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    fund_ids = _etf_ids(conn, resolved_ids(memberships))
    fund_cards = {m.thesis_id for m in memberships if m.security_id in fund_ids}
    company_ids = sorted(
        {
            m.security_id
            for m in memberships
            if m.thesis_id in fund_cards
            and not m.silent
            and m.security_id is not None
            and m.security_id not in fund_ids
        },
        key=str,
    )
    members = display_members(MEMBERS, "ETFs")
    funds = per_company(
        conn,
        sorted(fund_ids, key=str),
        asof,
        known_at,
        clock,
        lambda pit, sid: _fund_bundle(pit, sid, asof, members),
    )
    closes = per_company(
        conn,
        company_ids,
        asof,
        known_at,
        clock,
        lambda pit, sid: {
            b["d"]: float(b["close"])
            for b in pit.price_history(sid, lookback_days=LOOKBACK_DAYS)
            if b.get("close") is not None
        },
    )
    identity = master.identity_for(conn, fund_ids)
    return etf_inputs(
        cards=[(t.id, t.name) for t in counted.cards],
        memberships=memberships,
        fund_ids=fund_ids,
        funds=funds,
        names={sid: (str(i["name"]) if i.get("name") else None) for sid, i in identity.items()},
        company_closes=closes,
        iwm=_iwm(conn, asof, known_at, clock),
        flagged=price_flags(
            conn, [*sorted(fund_ids, key=str), *company_ids], asof, known_at=known_at, clock=clock
        ),
        asof=asof,
        live_cards=counted.live_count,
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
    )


def _fund_bundle(pit: PointInTimeData, sid: UUID, asof: date, members) -> FundBundle:
    bars = pit.price_history(sid)
    signals = {n: m(pit, sid, asof) for n, m in members.items()}
    return FundBundle(
        closes={b["d"]: float(b["close"]) for b in bars if b.get("close") is not None},
        readings=fund_readings(signals, pit.fund_shares(sid), bars, asof),
    )


@router.get("", response_model=EtfsOut)
def etfs(
    asof: date = Query(..., description="as-of date; nothing published after it is read"),
    window: Window = Query(
        "1m", description="panel 1's look back in trading sessions: 1w | 1m | 3m"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> EtfsOut:
    """The funds on every live card from five angles: each fund against its card's companies over the window
    (equal weight, rebalanced daily, from 100), net money in and out, approximate size and trading, price
    trend, and which cards have a fund. A fund is a roster row whose security is an ETF, in any link; a card's
    companies are its voting rows with every fund left out. A name whose data flags reach its price readings is
    left out of the numbers that use its price and is listed. Display only; a past as-of reads what was public
    by the end of that date."""
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(conn, SLUG, (clock,), asof, lambda: read_inputs(conn, asof, known_at, clock))
    page = compute_etfs(got.value, asof=asof, window=window)
    return EtfsOut.model_validate(
        {
            **asdict(page),
            "window_sessions": dict(WINDOWS),
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
