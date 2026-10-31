"""The Read-Throughs dashboard's API (``docs/dashboards/read-through.md``): one GET over every card counted at the
as-of, listing each company the operator tagged with a context role, one row per (company, role). It READS the
cards that existed by the as-of with their rosters then (``dashboards/baskets.py``), the role companies' filing
rows through the shared reader (``dashboards/filings.py``), their trailing returns and SPY's through the
point-in-time view (the ``trailing_returns`` display member, run once per company on the request's one
``serve_view(asof)`` pair), their identity and which of them have a data flag that reaches their price readings
(``dashboards.baskets.price_flags``), and hands everything to the pure compute in ``dashboards/read_through.py``.
The whole table is computed once per (clock, origin, as-of) in the shared dashboard cache (``dashboards/cache.py``,
``docs/DASHBOARDS.md`` rule 7); tabs, sort and filters are the page's. Its wire models live here, not in
``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime
from typing import Any, Literal
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
from dashboards.filings import FilingKind, read_filing_rows
from dashboards.read_through import (
    BENCHMARK,
    FILING_DAYS,
    TAPE_WINDOWS,
    CardRef,
    ReadThroughInputs,
    Tape,
    catalyst_refs,
    identities,
    tape_of,
)
from dashboards.read_through import read_through as compute_read_through
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.market_time import countdown_origin, market_clock, serve_view
from securities import master
from signals.base import PointInTimeData
from signals.display import trailing_returns
from signals.horizons import display_bounds

router = APIRouter(prefix="/read-through")

SLUG = "read-through"


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class ReadThroughCardOut(_Wire):
    """A card a company holds the role on. ``ticker`` = its ticker there (the Cockpit's ``?name=`` key);
    ``links`` = its links there (``null`` = no link; Context / Bench by their labels); ``silent`` = it is in
    Context or Bench there (it never counts toward the call)."""

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: list[str | None]
    silent: bool


class ReadThroughFilingOut(_Wire):
    """The company's newest SEC filing in the last ``filing_days`` days: ``kind`` (``8k`` | ``stake`` | ``form4`` |
    ``form4a`` | ``revenue`` | ``filing``, any other form in the company's EDGAR index; never a split, a catalyst
    or a ratified or stored fact), ``form`` (the form as
    filed), its public ``date`` (``days_ago`` before the as-of), one neutral ``headline``, and ``url`` (the EDGAR
    filing index or the stored source; ``null`` = none known).
    """

    kind: FilingKind
    form: str
    date: date
    days_ago: int
    headline: str
    url: str | None
    accession: str | None


class ReadThroughCatalystOut(_Wire):
    """The nearest dated card-level catalyst on or after the countdown origin among the row's cards; ``days`` = its
    date minus the origin (0 = the origin day). The origin is the action day (the session after the night) on a
    live view and the as-of on a past one."""

    thesis_id: UUID
    thesis_name: str
    catalyst_id: UUID
    label: str
    kind: str | None
    when_date: date
    when_label: str | None
    days: int


class ReadThroughTapeOut(_Wire):
    """The company's tape. ``ret_7d`` / ``ret_30d`` = its trailing return in percent over the last 7 / 30 trading
    days; ``vs_spy_*`` = that minus SPY's over the same window, in percentage points; ``last_close`` = the date of
    its last close. ``gap`` = why the vs-SPY cells are blank: ``flagged`` (its data flags reach its price readings;
    ``reason`` = the flags' labels, and no return is shown), ``no_tape`` (no bars), ``no_spy`` (SPY has none) or
    ``ends_differ`` (its last close is not SPY's last close). ``null`` = shown, or only that window is short.
    """

    ret_7d: float | None
    ret_30d: float | None
    vs_spy_7d: float | None
    vs_spy_30d: float | None
    last_close: date | None
    gap: Literal["flagged", "no_tape", "no_spy", "ends_differ"] | None
    reason: str | None


class ReadThroughRowOut(_Wire):
    """One (company, role). ``key`` = ``<security_id>:<role>``; ``ticker`` = the one most of its cards use;
    ``business_type`` = the leaf derived on read from the master (the operator's re-tag wins; ``null`` = unknown).
    """

    key: str
    security_id: UUID
    ticker: str
    name: str | None
    role: ContextRole
    cards: list[ReadThroughCardOut]
    business_type: str | None
    last_filing: ReadThroughFilingOut | None
    next_catalyst: ReadThroughCatalystOut | None
    tape: ReadThroughTapeOut


class ReadThroughRoleCountOut(_Wire):
    role: ContextRole
    rows: int


class ReadThroughCardRefOut(_Wire):
    thesis_id: UUID
    thesis_name: str


class ReadThroughFlaggedOut(_Wire):
    """A role company whose data flags reach its price readings at the as-of: its row stays, its tape cells name
    ``reason`` (the flags' labels)."""

    security_id: UUID
    ticker: str
    reason: str


class ReadThroughSpyOut(_Wire):
    """SPY's own trailing returns at the as-of, the benchmark every vs-SPY cell subtracts."""

    ret_7d: float | None
    ret_30d: float | None
    end: date | None


class ReadThroughOut(_Wire):
    """The whole table for the as-of, in the default order (newest last filing first; no filing last, A to Z).
    ``cards_counted`` = the live cards that existed by the as-of; ``cards_with_roles`` of them hold at least one
    role, ``cards_without_roles`` hold none (listed: the page is also the to-do list for roles); ``role_tags`` =
    the roles set (one per card and company), ``companies`` = distinct role companies, ``by_role`` = the rows per
    role (a role with none included). ``unresolved`` = tickers of the role rows with no resolved security
    (``unresolved_rows``: counted, never a row). ``flagged`` = role companies whose data flags reach their price
    readings. ``tape_ends_differ`` = rows whose last close is not SPY's. ``roster_fallback_cards`` = counted cards
    with no roster snapshot by the as-of (today's roster stands in). ``clock``: ``record`` (the live read) or
    ``public`` (a past as-of: what was public by the end of that date). ``cache`` / ``compute_ms`` = whether the
    table came from the shared dashboard cache and how long it took when computed."""

    asof: date
    clock: Literal["record", "public"]
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    cards_with_roles: int
    cards_without_roles: list[ReadThroughCardRefOut]
    role_tags: int
    companies: int
    by_role: list[ReadThroughRoleCountOut]
    rows: list[ReadThroughRowOut]
    rows_with_filing: int
    unresolved: list[str]
    unresolved_rows: int
    flagged: list[ReadThroughFlaggedOut]
    tape_ends_differ: int
    spy: ReadThroughSpyOut | None
    benchmark: str
    tape_windows: list[int]
    filing_days: int
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------


def _tapes(
    conn: psycopg.Connection,
    sids: list[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[UUID, Tape | None]:
    """Each company's trailing returns, computed ONCE by the ``trailing_returns`` display member, from the same
    point-in-time view the Cockpit's ``/display-signals`` builds (``known_at`` + ``clock`` from ``serve_view``).
    """
    member = display_members(("trailing_returns",), "Read-Throughs")["trailing_returns"]
    return per_company(
        conn, sids, asof, known_at, clock, lambda pit, sid: tape_of(member(pit, sid, asof))
    )


def _spy(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> Tape | None:
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
    return tape_of(trailing_returns.compute(bars, asof))


def read_inputs(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> ReadThroughInputs:
    """The heavy read: the counted cards' rosters as of the as-of, then, for the companies that carry a role on
    any of them, their identity, a year of filing rows (the shared reader), their tape and their price flags.
    """
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    sids = resolved_ids([m for m in memberships if m.context_role])
    return ReadThroughInputs(
        live_cards=counted.live_count,
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        cards=tuple(CardRef(t.id, t.name) for t in counted.cards),
        memberships=memberships,
        catalysts=catalyst_refs(counted.cards),
        identity=identities(master.identity_for(conn, set(sids))) if sids else {},
        filings=read_filing_rows(conn, sids, asof, known_at, clock, days=FILING_DAYS),
        tapes=_tapes(conn, sids, asof, known_at, clock),
        spy=_spy(conn, asof, known_at, clock),
        flagged=price_flags(conn, sids, asof, known_at=known_at, clock=clock),
    )


def _payload(obj: Any) -> dict[str, Any]:
    return {f.name: getattr(obj, f.name) for f in fields(obj)}


@router.get("", response_model=ReadThroughOut)
def read_through(
    asof: date = Query(..., description="as-of date; nothing published after it is read"),
    conn: psycopg.Connection = Depends(get_conn),
) -> ReadThroughOut:
    """Every company tagged with a context role on a card counted at the as-of (payers, counter-cases, direct
    reads, participants, financiers, benchmarks), one row per company and role, with the card chips it sits on, its
    business type, the newest SEC filing in the last year, the nearest dated card catalyst and its 7 and
    30 trading-day return minus SPY's. A company whose data flags reach its price readings shows no tape and is
    named. Sorted by newest last filing. Display only; a past as-of reads what was public by the end of that
    date."""
    mclock = market_clock()
    known_at, clock = serve_view(asof, night=mclock.night)
    # F2: the next catalyst counts down from the origin (the action day on a live view)
    origin = countdown_origin(asof, mclock)
    # the clock and the origin are the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(
        conn,
        SLUG,
        (clock, origin),
        asof,
        lambda: compute_read_through(read_inputs(conn, asof, known_at, clock), asof, origin),
    )
    page = got.value
    return ReadThroughOut.model_validate(
        {
            **_payload(page),
            "clock": clock,
            "benchmark": BENCHMARK,
            "tape_windows": list(TAPE_WINDOWS),
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
