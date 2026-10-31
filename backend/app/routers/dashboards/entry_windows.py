"""The Entry Windows dashboard's API (``docs/dashboards/entry-windows.md``): one GET, each Armed card's entry window
and hold horizon on one date axis, at the as-of dial. It READS the cards that existed by the as-of
(``dashboards/baskets.py``), the ``calls`` log (every row at or before the as-of without its card blob, then the
winning card of each night of each current run: plain SELECTs, never ``call_for_thesis``), the master's tickers and
CIKs, the data flags that reach a price reading (``baskets.price_flags``), the armed names' closes and IWM's and
SPY's through the point-in-time view (both halves of ``serve_view(asof)``), and hands them to the pure compute in
``dashboards/entry_windows.py``. The whole page is computed once per (clock, as-of) in the shared dashboard cache
(``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7); the Managing toggle and the sorts are the client's. Its
wire models live here, not in ``app/schemas_api.py`` (rule 8)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import CHUNK, cards_as_of, price_flags
from dashboards.cache import cached
from dashboards.entry_windows import (
    BENCHMARKS,
    EntryWindows,
    NightRow,
    armed_run,
    entry_windows,
    lookback_days,
)
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.enums import Grade, Kind
from domain.market_time import countdown_origin, market_clock, serve_view
from securities import master
from signals.base import PointInTimeData
from signals.horizons import MARGIN_DAYS

router = APIRouter(prefix="/entry-windows")

SLUG = "entry-windows"

# Every calls row of the cards at or before the as-of, WITHOUT the card blob. The log appends only on change, so
# the rows carry the nights; a night with several rows is read at its highest seq (the compute's rule).
_NIGHTS_SQL = (
    "SELECT thesis_id, asof, seq, state, reconstructed FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof, seq"
)
# The winning rows of the run nights, read by their (thesis, night, seq) key: the only reads of the card blob.
_CARDS_SQL = (
    "SELECT thesis_id, asof, card FROM calls WHERE (thesis_id, asof, seq) IN "
    "(SELECT * FROM unnest(%s::uuid[], %s::date[], %s::bigint[]))"
)


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class EntryWindowsSourceOut(_Wire):
    """A place a trigger points at. ``source`` = the kind as stored (``form4``, ``price``, ``xbrl``, a form),
    ``ref`` = its reference (an accession, a price reference), ``url`` = a link (``null`` = none known; a price
    reference stays text)."""

    source: str
    ref: str
    url: str | None


class EntryWindowsTriggerOut(_Wire):
    """A fired trigger on an armed name. ``event_date`` is the trigger's FIRE date (the market event), not the
    arm night."""

    label: str
    kind: Kind
    grade: Grade | None
    event_date: date | None
    sources: list[EntryWindowsSourceOut]


class EntryWindowsMemberOut(_Wire):
    """An armed name. ``arm_night`` = the first night of the run on which the recorded card names it armed;
    ``since`` = its as-traded return in percent from the first close on or after that night (``entry_on``) to the
    last close on or before the as-of, ``above`` = that last close is above the entry close. ``flag`` = left out
    for a flagged price history (the flags' labels); ``since`` null without a flag = no bar on or after its arm
    night."""

    security_id: UUID
    ticker: str | None
    arm_night: date
    entry_on: date | None
    since: float | None
    above: bool | None
    flag: str | None
    triggers: list[EntryWindowsTriggerOut]


class EntryWindowsCatalystOut(_Wire):
    """A dated catalyst of the recorded card inside the window. ``past`` = its date is before the as-of."""

    label: str
    kind: str | None
    when_date: date
    past: bool


class EntryWindowsSinceOut(_Wire):
    """A row's tape since its arm night, over its armed names: ``priced`` of ``names`` are in the median
    (percent, as traded); the rest are ``flagged`` (a flagged price history) or ``no_bar``. ``above`` = priced
    names above their arm-night close. ``iwm`` / ``spy`` = the benchmarks over the same span (``null`` = no bar).
    """

    names: int
    priced: int
    flagged: int
    no_bar: int
    above: int
    median: float | None
    iwm: float | None
    spy: float | None


class EntryWindowsSparkOut(_Wire):
    """The armed names' equal-weighted line and IWM's, from 100 at ``dates[0]``, one point per session
    (``iwm`` null = IWM has no steps over these sessions)."""

    dates: list[date]
    line: list[float]
    iwm: list[float] | None


class EntryWindowsMemberClockOut(_Wire):
    """An armed name whose own flag is true, with its own entry clock. ``headline`` = the name whose clocks the card
    carries (``armed_members[0]``). ``sessions_left`` = the Mon-Fri days after the as-of through the NAME's own
    ``arm_until`` (while that window is open), ``closed_ago`` = the IWM sessions since it closed (``0`` on its last
    day); both ``null`` unless ``entry_window_closing``."""

    security_id: UUID
    ticker: str | None
    headline: bool
    entry_window_closing: bool
    lapsing: bool
    arm_until: date | None
    sessions_left: int | None
    closed_ago: int | None


class EntryWindowsRowOut(_Wire):
    """One Armed (``state`` ``armed``) or Managing card. ``arm_night`` = the first night of the card's current
    recorded Armed run; ``in_effect`` = the recorded night whose card the row shows; ``reconstructed`` = the arm
    night's row was written by ``pipeline.backfill`` (reported, never scored). ``arm_until`` / ``exit_by`` = the
    entry window and the hold horizon (``null`` = no clock). ``sessions_into`` = the IWM sessions after the arm
    night through the as-of; ``sessions_left`` = the Mon-Fri days after the as-of through ``arm_until`` (while the
    window is open); ``closed_ago`` = the IWM sessions since it closed (``0`` on its last day); ``hold_days`` =
    calendar days from the countdown origin to ``exit_by`` (negative = passed; the origin is the action day, the
    session after the night, on a live view and the as-of on a past one). Each flag is true when ANY armed member's own
    flag is true; ``member_clocks`` lists those members with their own entry clock, since the figures above count to
    the card's (the headline name's) clocks.
    """

    thesis_id: UUID
    thesis_name: str
    state: Literal["armed", "managing"]
    arm_night: date
    in_effect: date
    reconstructed: bool
    reconstructed_nights: int
    run_nights: int
    arm_until: date | None
    exit_by: date | None
    confidence: float | None
    entry_window_closing: bool
    lapsing: bool
    sessions_into: int | None
    sessions_left: int | None
    closed_ago: int | None
    hold_days: int | None
    member_clocks: list[EntryWindowsMemberClockOut]
    catalysts: list[EntryWindowsCatalystOut]
    members: list[EntryWindowsMemberOut]
    since: EntryWindowsSinceOut
    spark: EntryWindowsSparkOut | None


class EntryWindowsFlaggedOut(_Wire):
    """A name left out of every number for a flagged price history (the reason the Cockpit grays its price
    cells)."""

    security_id: UUID
    ticker: str
    reason: str


class EntryWindowsCountsOut(_Wire):
    """The work shown on the quiet line. The state counts add up to ``cards_counted``: ``armed`` + ``managing``
    (rows) + ``managing_without_arm_night`` + ``warming`` + ``incubating`` + ``no_call``."""

    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    armed: int
    managing: int
    managing_without_arm_night: int
    warming: int
    incubating: int
    no_call: int
    reconstructed_arm_nights: int
    armed_names: int
    priced_names: int
    unpriced_names: int


class EntryWindowsOut(_Wire):
    """The whole page at the as-of. ``clock`` = how the as-of was read: ``record`` (the live read) or ``public``
    (a past date: what was public by the end of it). ``axis_start`` / ``axis_end`` = the one date axis (``null`` with no row);
    ``session_source`` = where the session calendar came from (IWM's bars, the armed names' bars when IWM has
    none, or nothing); ``calendar_last`` = its last session. Rows come by card name. ``cache`` / ``compute_ms`` =
    whether the page came from the shared dashboard cache and how long it took when computed."""

    asof: date
    clock: Literal["record", "public"]
    axis_start: date | None
    axis_end: date | None
    session_source: Literal["benchmarks", "companies", "none"]
    calendar_sessions: int
    calendar_last: date | None
    counts: EntryWindowsCountsOut
    flagged: list[EntryWindowsFlaggedOut]
    rows: list[EntryWindowsRowOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------


def _nights(
    conn: psycopg.Connection, ids: Sequence[UUID], asof: date
) -> dict[UUID, list[NightRow]]:
    if not ids:
        return {}
    out: dict[UUID, list[NightRow]] = defaultdict(list)
    with conn.cursor() as cur:
        cur.execute(_NIGHTS_SQL, (list(ids), asof))
        for r in cur.fetchall():
            out[r["thesis_id"]].append(
                NightRow(r["asof"], r["seq"], str(r["state"]), r["reconstructed"])
            )
    return dict(out)


def _recorded(
    conn: psycopg.Connection, picks: Sequence[tuple[UUID, date, int]]
) -> dict[tuple[UUID, date], CallCard]:
    """The card of each ``(thesis, night, seq)`` pick."""
    if not picks:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            _CARDS_SQL,
            ([p[0] for p in picks], [p[1] for p in picks], [p[2] for p in picks]),
        )
        return {
            (r["thesis_id"], r["asof"]): CallCard.model_validate(r["card"]) for r in cur.fetchall()
        }


def _view(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    days: int,
    basket: Sequence[UUID] = (),
) -> PointInTimeData:
    """A point-in-time view whose price window reaches back ``days`` (plus the horizon registry's own margin):
    the arm night can be older than the Cockpit's display window, and a bar floor that cut it would read the
    first bar AFTER the arm night as its entry."""
    return PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        basket=basket,
        bounds={"fact_price_eod": days + MARGIN_DAYS},
        clock=clock,
    )


def _closes(
    conn: psycopg.Connection,
    sids: Sequence[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    days: int,
) -> dict[UUID, dict[date, float]]:
    """Each company's ``{day: close}`` up to the as-of, ``CHUNK`` companies per view."""
    out: dict[UUID, dict[date, float]] = {}
    for i in range(0, len(sids), CHUNK):
        chunk = list(sids[i : i + CHUNK])
        pit = _view(conn, asof, known_at, clock, days, chunk)
        for sid in chunk:
            out[sid] = {
                b["d"]: float(b["close"])
                for b in pit.price_history(sid, lookback_days=days)
                if b.get("close") is not None
            }
    return out


def _bench(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    days: int,
) -> dict[str, dict[date, float]]:
    pit = _view(conn, asof, known_at, clock, days)
    return {
        symbol: {
            b["d"]: float(b["close"])
            for b in pit.benchmark_prices(symbol, lookback_days=days)
            if b.get("close") is not None
        }
        for symbol in BENCHMARKS
    }


def read_page(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    origin: date | None = None,
) -> EntryWindows:
    """The heavy read the cache holds: the counted cards, their runs, the armed names' tape and the page built
    from them. ``known_at`` + ``clock`` are the request's one ``serve_view(asof)`` pair; ``origin`` is where the
    countdowns count from (F2)."""
    counted = cards_as_of(conn, asof, known_at)
    ids = [t.id for t in counted.cards]
    nights = _nights(conn, ids, asof)
    runs = {tid: run for tid, rows in nights.items() if (run := armed_run(rows)) is not None}
    recorded = _recorded(
        conn, [(tid, n.asof, n.seq) for tid, run in runs.items() for n in run.nights]
    )
    # the armed names of each card in effect (the page's rows), and the names its triggers fired on
    armed = list(
        dict.fromkeys(
            m.security_id
            for tid, run in runs.items()
            for m in recorded[(tid, run.last)].armed_members
        )
    )
    fired = {
        t.security_id
        for tid, run in runs.items()
        for m in recorded[(tid, run.last)].armed_members
        for t in m.triggers
    }
    flagged = price_flags(conn, armed, asof, known_at=known_at, clock=clock)
    days = lookback_days((run.first for run in runs.values()), asof)
    priced = [sid for sid in armed if sid not in flagged]
    return entry_windows(
        [(t.id, t.name) for t in counted.cards],
        nights,
        recorded,
        master.tickers_for(conn, armed),
        master.ciks_for(conn, [*armed, *fired]),
        _closes(conn, priced, asof, known_at, clock, days) if priced else {},
        flagged,
        _bench(conn, asof, known_at, clock, days) if runs else {},
        asof=asof,
        live_cards=counted.live_count,
        not_yet_created=counted.not_yet_created,
        origin=origin,
    )


@router.get("", response_model=EntryWindowsOut)
def entry_windows_page(
    asof: date = Query(
        ..., description="as-of date; nothing recorded or published after it is read"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> EntryWindowsOut:
    """Each Armed card's entry window (to ``arm_until``) and hold horizon (to ``exit_by``) from its arm night, on
    one date axis with the as-of marked, the IWM sessions into and left in the window, the dated catalysts inside
    it, and the armed names' as-traded return since the arm night against IWM and SPY. Managing cards are rows too
    (the page hides them behind a toggle). Display only; a past as-of reads the recorded call in effect then and
    what was public by the end of that date."""
    mclock = market_clock()
    known_at, clock = serve_view(asof, night=mclock.night)
    # F2: the hold days and the catalysts' passed flag count from the countdown origin (the action day when live)
    origin = countdown_origin(asof, mclock)
    # the clock and the origin are the key: the same date read live today and on the public clock tomorrow are two
    # reads, and the origin moves when the night does
    got = cached(
        conn,
        SLUG,
        (clock, origin),
        asof,
        lambda: read_page(conn, asof, known_at, clock, origin),
    )
    return EntryWindowsOut.model_validate(
        {**asdict(got.value), "clock": clock, "cache": got.status, "compute_ms": got.compute_ms}
    )
