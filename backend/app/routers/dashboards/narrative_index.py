"""The Narrative Index dashboard's API (``docs/dashboards/narrative-index.md``): each counted card's basket as one
equal-weighted line against SPY and IWM, at the as-of dial. It READS the cards and their rosters as of the as-of
(``dashboards/baskets.py``), every company's closes and the two benchmarks' on the public clock, each card's
creation instant and its latest recorded state, and hands them to the pure compute in
``dashboards/narrative_index.py``. Every card's 1Y series is computed once per (clock, toggle, as-of) in the
shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7); each window is sliced and
rebased from it per request. Its wire models live here, not in ``app/schemas_api.py`` (rule 8)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import fields
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import (
    cards_as_of,
    counted_rows,
    memberships_of,
    per_company,
    price_flags,
    resolved_ids,
)
from dashboards.cache import cached
from dashboards.narrative_index import (
    BENCHMARKS,
    WINDOWS,
    IndexInputs,
    NarrativeIndex,
    Window,
    card_detail,
    index_inputs,
)
from dashboards.narrative_index import narrative_index as compute_narrative_index
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_tz, serve_view
from repositories import thesis_repo
from signals.base import PointInTimeData
from signals.display import trailing_returns
from signals.horizons import display_bounds

router = APIRouter(prefix="/narrative-index")

SLUG = "narrative-index"
# calendar days of bars read per company and benchmark: the Cockpit's own 1Y-return lookback (~290 sessions
# for the 253 the 1Y window needs)
LOOKBACK_DAYS = trailing_returns.LOOKBACK_DAYS

State = Literal["incubating", "warming", "armed", "managing"]

# The latest recorded state per card at the as-of: the calls row in effect then (the log appends only on
# change, so the last row on or before the as-of is what stood that night; a night's highest seq wins).
_STATE_SQL = (
    "SELECT DISTINCT ON (thesis_id) thesis_id, state FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof DESC, seq DESC"
)


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class NarrativeBenchmarkOut(_Wire):
    """A benchmark over the window's sessions, rebased to 100 at the window start. ``available`` false = no
    line, and ``reason`` says why. ``sessions_flat`` = window sessions it had no bar for (carried flat).
    """

    symbol: str
    available: bool
    reason: str | None
    ret: float | None
    points: list[float] | None
    sessions_flat: int


class NarrativeFlaggedOut(_Wire):
    """A name left out of its card's line: its data flags reach its price readings at the as-of (the reason
    the Cockpit grays its price cells). ``reason`` = the flags' labels."""

    ticker: str
    reason: str


class NarrativeCardOut(_Wire):
    """One card's index over the window: ``points`` one per session of the page's ``dates``, from 100.
    ``ret`` = the window return (``null`` with no priced name), ``vs_spy`` = it minus SPY's, ``since_created``
    = from the close of ``created_base`` (the last session on or before the creation day) to the as-of, only
    when ``created_in_window``. ``names`` = the companies on the card (each once) plus rows with no resolved
    security; ``priced`` = names with at least one daily return in the window. ``flagged`` = the names left
    out of the line for a flagged price history (counted in ``names``, never in ``priced``). ``state`` = the
    latest recorded state at the as-of (``null`` = no recorded call)."""

    thesis_id: UUID
    thesis_name: str
    state: State | None
    created: date | None
    created_in_window: bool
    created_base: date | None
    names: int
    priced: int
    unresolved: int
    silent_left_out: int
    ret: float | None
    vs_spy: float | None
    since_created: float | None
    sessions_flat: int
    points: list[float]
    flagged: list[NarrativeFlaggedOut]


class NarrativeIndexOut(_Wire):
    """The whole page for one window, cards by name. ``sessions`` = daily returns in the window (fewer than
    the window's nominal count when the tape is short); ``dates`` = its sessions. ``session_source`` = where
    the session calendar came from (the SPY / IWM tapes, or the companies' bars when neither benchmark has a
    tape). The counts are the work shown on its quiet line; ``flagged_companies`` = the companies left out of
    every line for a flagged price history. ``cache`` / ``compute_ms`` = whether the 1Y series came from the
    shared dashboard cache and how long it took when it ran."""

    asof: date
    window: Window
    include_silent: bool
    sessions: int
    dates: list[date]
    session_source: Literal["benchmarks", "companies", "none"]
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    companies: int
    priced_companies: int
    silent_rows_left_out: int
    unresolved_rows: int
    flagged_companies: int
    benchmarks: list[NarrativeBenchmarkOut]
    cards: list[NarrativeCardOut]
    window_sessions: dict[str, int]
    cache: Literal["hit", "computed"]
    compute_ms: int


class NarrativeMemberOut(_Wire):
    """One name on the card: the daily returns it contributed in the window and the first / last session it
    did (``security_id`` null = a basket row with no resolved security, never priced). ``flag`` = why it is
    left out of the line (a flagged price history; ``null`` = its bars are read)."""

    ticker: str
    security_id: UUID | None
    sessions: int
    first: date | None
    last: date | None
    flag: str | None


class NarrativeCardDetailOut(_Wire):
    """One card enlarged: its window view, the benchmarks over the same ``dates``, how many names contributed
    the step into each session (``contributors``, the first session 0) and each name's window."""

    asof: date
    window: Window
    include_silent: bool
    sessions: int
    dates: list[date]
    card: NarrativeCardOut
    benchmarks: list[NarrativeBenchmarkOut]
    contributors: list[int]
    members: list[NarrativeMemberOut]
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


def _benchmarks(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[str, dict[date, float]]:
    """SPY's and IWM's ``{day: close}`` (``pit.benchmark_prices``; empty = no tape at the as-of)."""
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=display_bounds(),
        clock=clock,
    )
    return {
        symbol: {
            b["d"]: float(b["close"])
            for b in pit.benchmark_prices(symbol, lookback_days=LOOKBACK_DAYS)
            if b.get("close") is not None
        }
        for symbol in BENCHMARKS
    }


def _states(conn: psycopg.Connection, ids: list[UUID], asof: date) -> dict[UUID, str]:
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(_STATE_SQL, (ids, asof))
        return {r["thesis_id"]: str(r["state"]) for r in cur.fetchall()}


def read_inputs(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    include_silent: bool,
) -> IndexInputs:
    """The heavy read the cache holds: the counted cards' rosters as of the as-of, every counted company's
    closes and price flags (``price_flags``, on the same view), the benchmarks', each card's creation day and
    latest recorded state, built into every card's 1Y series."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    sids = resolved_ids(counted_rows(memberships, include_silent).rows)
    ids = [t.id for t in counted.cards]
    tz = market_tz()
    created = {
        tid: at.astimezone(tz).date() for tid, at in thesis_repo.created_at_for(conn, ids).items()
    }
    return index_inputs(
        cards=[(t.id, t.name) for t in counted.cards],
        memberships=memberships,
        closes=_closes(conn, sids, asof, known_at, clock),
        benchmarks=_benchmarks(conn, asof, known_at, clock),
        created=created,
        states=_states(conn, ids, asof),
        asof=asof,
        include_silent=include_silent,
        live_cards=counted.live_count,
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        flagged=price_flags(conn, sids, asof, known_at=known_at, clock=clock),
    )


def _payload(obj: Any) -> dict[str, Any]:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


def _page_dict(page: NarrativeIndex) -> dict[str, Any]:
    return {
        **_payload(page),
        "benchmarks": [_payload(b) for b in page.benchmarks],
        "cards": [{**_payload(c), "flagged": [_payload(f) for f in c.flagged]} for c in page.cards],
        "window_sessions": dict(WINDOWS),
    }


def _inputs(conn: psycopg.Connection, asof: date, include_silent: bool):
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    return cached(
        conn,
        SLUG,
        (clock, include_silent),
        asof,
        lambda: read_inputs(conn, asof, known_at, clock, include_silent),
    )


@router.get("", response_model=NarrativeIndexOut)
def narrative_index(
    asof: date = Query(..., description="as-of date; no price published after it is read"),
    window: Window = Query("1y", description="the look back in trading sessions: 1y | 90d | 30d"),
    include_silent: bool = Query(False, description="add Context and Bench names to each basket"),
    conn: psycopg.Connection = Depends(get_conn),
) -> NarrativeIndexOut:
    """Each card's basket as of the as-of as one equal-weighted line (rebalanced daily, from 100 at the window
    start) against SPY and IWM over the same sessions, with the card's creation day and the window return,
    the return vs SPY and the return since creation. A name whose data flags reach its price readings is left
    out of the line and listed on its card. Display only; a past as-of reads what was public by the end of
    that date."""
    got = _inputs(conn, asof, include_silent)
    page = compute_narrative_index(got.value, asof=asof, window=window)
    return NarrativeIndexOut.model_validate(
        {**_page_dict(page), "cache": got.status, "compute_ms": got.compute_ms}
    )


@router.get("/{thesis_id}", response_model=NarrativeCardDetailOut)
def narrative_index_card(
    thesis_id: UUID,
    asof: date = Query(..., description="as-of date; no price published after it is read"),
    window: Window = Query("1y", description="the look back in trading sessions: 1y | 90d | 30d"),
    include_silent: bool = Query(False, description="add Context and Bench names to the basket"),
    conn: psycopg.Connection = Depends(get_conn),
) -> NarrativeCardDetailOut:
    """One card enlarged: its index, SPY and IWM over the window, how many names contributed each session and
    each name's sessions in the window. Served off the same cached read as the grid. 404 when the card is not
    counted at the as-of."""
    got = _inputs(conn, asof, include_silent)
    detail = card_detail(got.value, thesis_id, asof=asof, window=window)
    if detail is None:
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    page = _page_dict(detail.page)
    return NarrativeCardDetailOut.model_validate(
        {
            "asof": page["asof"],
            "window": page["window"],
            "include_silent": page["include_silent"],
            "sessions": page["sessions"],
            "dates": page["dates"],
            "card": page["cards"][0],
            "benchmarks": page["benchmarks"],
            "contributors": list(detail.contributors),
            "members": [_payload(m) for m in detail.members],
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
