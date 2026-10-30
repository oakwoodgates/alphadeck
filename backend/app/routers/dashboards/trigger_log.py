"""The Trigger Log dashboard's API (``docs/dashboards/trigger-log.md``): one GET, every entry trigger and risk
signal the recorded calls show firing across the cards in a window, at the as-of dial. It READS the cards that
existed by the as-of with their rosters then (``dashboards/baskets.py``), the ``calls`` log for the window (two
plain SELECTs: each night's winning row, and the one row in effect before the window opens; never
``call_for_thesis``), the master's tickers and issuer CIKs, the data flags that reach a price reading and the
as-traded bars of the names a trigger fired on plus IWM's, all through the request's one ``serve_view(asof)``
pair, then hands them to the pure compute in ``dashboards/trigger_log.py``. The whole read is computed once per
(window, clock, as-of) in the shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7);
grouping, filters and sorts are the page's own. Its wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime
from enum import IntEnum
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from app.deps import get_conn
from dashboards.baskets import cards_as_of, per_company, price_flags
from dashboards.cache import cached
from dashboards.trigger_log import (
    BENCHMARK,
    CardNights,
    Night,
    Role,
    Scope,
    SinceStatus,
    TriggerLog,
    collect,
    trigger_log,
    window_start,
)
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.enums import Grade, Kind, State
from domain.market_time import serve_view
from securities import master
from signals.base import PointInTimeData
from signals.horizons import display_bounds

router = APIRouter(prefix="/trigger-log")

SLUG = "trigger-log"

# The ``calls`` log reads: plain SELECTs scoped to the counted cards. A night with several rows reads its
# highest ``seq`` (the row that stood); the window is inclusive at both ends, and the second read is the one row
# in effect before it opens (the greatest ``(asof, seq)`` with ``asof`` before the window's first night).
_WINDOW_SQL = (
    "SELECT DISTINCT ON (thesis_id, asof) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof >= %s AND asof <= %s ORDER BY thesis_id, asof, seq DESC"
)
_BEFORE_SQL = (
    "SELECT DISTINCT ON (thesis_id) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof < %s ORDER BY thesis_id, asof DESC, seq DESC"
)


class TriggerLogDays(IntEnum):
    """The window: the last 30, 90 or 180 days."""

    D30 = 30
    D90 = 90
    D180 = 180


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class TriggerLogSourceOut(_Wire):
    """A place a trigger points at. ``source`` = the kind as stored (``form4``, ``price``, ``xbrl``), ``ref`` =
    its reference, ``url`` = a link (an accession through the EDGAR index with the issuer's CIK, or a stored
    URL; ``null`` = none known), ``bar_date`` = the bar a ``price:`` reference names (text, never a link),
    ``detail`` = the computation's inputs as stored, one ``key: value`` line each."""

    source: str
    ref: str | None
    url: str | None
    bar_date: date | None
    detail: list[str]


class TriggerLogSinceOut(_Wire):
    """The name's as-traded return from the first close on or after the recorded night to the last close on or
    before the as-of, with IWM on the same two dates. ``priced`` = both bars exist; ``unpriced`` = no bar since the
    recorded night; ``flagged`` = the name's price history is on the flagged-price list (``reason``, never
    priced). ``bench_ret`` is ``null`` when IWM has no bar on one of those two dates. Returns are fractions (0.05 = +5%).
    """

    status: SinceStatus
    reason: str | None
    entry_date: date | None
    end_date: date | None
    ret: float | None
    bench_ret: float | None


class TriggerLogRowOut(_Wire):
    """One trigger on one card, dated by the first night in the window the record shows it. ``event_date`` = the
    fire date (``null`` = undated); ``recorded_days_after`` = days from the fire date to the recorded night when
    the fire date is earlier; ``week`` = the Monday of the recorded night's week; ``turns`` = the key an entry
    trigger turns (``null`` for a risk signal or a kind neither key takes); ``from_state`` / ``to_state`` = the
    card's recorded state on the previous recorded night and on this one (``new_card`` = no earlier row);
    ``live`` = the same trigger is on the card in effect at the as-of (``null`` = that card could not be read);
    ``newest`` = the recorded night is the as-of's own night; ``sessions_since`` = the sessions the since-return
    spans on IWM's calendar (``null`` = none); ``reconstructed`` = written by ``pipeline.backfill`` (reported,
    never scored)."""

    key: str
    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str | None
    role: Role
    kind: Kind
    grade: Grade | None
    turns: Literal["conviction", "confirmation"] | None
    label: str
    event_date: date | None
    recorded_on: date
    week: date
    recorded_days_after: int | None
    reconstructed: bool
    from_state: State | None
    to_state: State
    new_card: bool
    live: bool | None
    newest: bool
    sessions_since: int | None
    since: TriggerLogSinceOut
    sources: list[TriggerLogSourceOut]


class TriggerLogNameOut(_Wire):
    """A name the page set aside from the tape, never from the list: ``reason`` = the flags' labels for a flagged
    one, ``null`` for an unpriced one. ``ticker`` is ``null`` when neither the roster nor the master has one.
    """

    security_id: UUID
    ticker: str | None
    reason: str | None


class TriggerLogSessionsOut(_Wire):
    """The session calendar the sessions-since counts on: IWM's bar dates ending at the as-of (``source`` =
    ``benchmarks``; ``companies`` / ``none`` when IWM has no bar)."""

    source: Literal["benchmarks", "companies", "none"]
    count: int
    first: date | None
    last: date | None


class TriggerLogCountsOut(_Wire):
    """The work shown on the quiet line: counts of what was read and listed, never a statistic over the rows'
    returns. ``cards_quiet`` = cards with a call by the as-of but no night in the window; ``carried_live`` = live
    triggers first recorded before the window (not listed); ``names_*`` = distinct names."""

    cards_counted: int
    cards_with_nights: int
    cards_quiet: int
    cards_no_call: int
    live_cards: int
    not_yet_created: int
    roster_fallback: int
    nights_read: int
    nights_reconstructed: int
    nights_unreadable: int
    entry_rows: int
    risk_rows: int
    carried_live: int
    names_unpriced: int
    names_flagged: int
    rows_no_ticker: int


class TriggerLogOut(_Wire):
    """The whole page at the as-of. ``clock``: ``record`` (the live read) or ``public`` (a past as-of: what was
    public by the end of that date). The window is ``window_start`` to ``asof``, inclusive; ``rows`` are
    newest recorded night first, then ticker. There is no aggregate over the rows (the Track Record page slices
    outcomes by market-cap tier). ``cache`` / ``compute_ms`` = whether the read came from the shared dashboard
    cache and how long it took when computed."""

    asof: date
    window_days: int
    window_start: date
    clock: Literal["record", "public"]
    rows: list[TriggerLogRowOut]
    counts: TriggerLogCountsOut
    sessions: TriggerLogSessionsOut
    unpriced: list[TriggerLogNameOut]
    flagged: list[TriggerLogNameOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------


def _night(row: dict) -> Night:
    """One ``calls`` row; a stored card that no longer validates reads as ``card=None`` (counted, not read)."""
    try:
        card: CallCard | None = CallCard.model_validate(row["card"])
    except ValidationError:
        card = None
    return Night(row["asof"], row["seq"], row["reconstructed"], card)


def read_nights(
    conn: psycopg.Connection, cards: Sequence[tuple[UUID, str]], asof: date, window_days: int
) -> list[CardNights]:
    """Each counted card's recorded nights: the window's nights (``asof`` in ``[as-of - days, as-of]``, each
    night's highest ``seq``) and the one row in effect before it. A row dated after the as-of is never read.
    """
    ids = [tid for tid, _ in cards]
    start = window_start(asof, window_days)
    before: dict[UUID, Night] = {}
    nights: dict[UUID, list[Night]] = {tid: [] for tid in ids}
    if ids:
        with conn.cursor() as cur:
            cur.execute(_WINDOW_SQL, (ids, start, asof))
            for r in cur.fetchall():
                nights[r["thesis_id"]].append(_night(r))
            cur.execute(_BEFORE_SQL, (ids, start))
            for r in cur.fetchall():
                before[r["thesis_id"]] = _night(r)
    return [CardNights(tid, name, before.get(tid), tuple(nights[tid])) for tid, name in cards]


def _closes(
    conn: psycopg.Connection,
    sids: Sequence[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    window_days: int,
) -> dict[UUID, dict[date, float]]:
    """Each company's as-traded ``{day: close}`` over the window up to the as-of, from the same point-in-time
    view the Cockpit's ``/display-signals`` builds (``known_at`` + ``clock`` from ``serve_view``).
    """
    return per_company(
        conn,
        sids,
        asof,
        known_at,
        clock,
        lambda pit, sid: {
            b["d"]: float(b["close"])
            for b in pit.price_history(sid, lookback_days=window_days)
            if b.get("close") is not None
        },
    )


def _bench(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    window_days: int,
) -> dict[date, float]:
    """IWM's ``{day: close}`` over the window (``pit.benchmark_prices``; empty = no tape at the as-of)."""
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
        for b in pit.benchmark_prices(BENCHMARK, lookback_days=window_days)
        if b.get("close") is not None
    }


def read_log(
    conn: psycopg.Connection,
    asof: date,
    window_days: int,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> TriggerLog:
    """The cached read: the counted cards and their rosters at the as-of, each card's recorded nights in the
    window, then the tape of the names a trigger fired on (a flagged name is not priced) and IWM's.
    """
    got = cards_as_of(conn, asof, known_at)
    collected = collect(read_nights(conn, [(t.id, t.name) for t in got.cards], asof, window_days))
    sids = sorted({e.security_id for e in collected.events}, key=str)
    flagged = price_flags(conn, sids, asof, known_at=known_at, clock=clock)
    return trigger_log(
        collected,
        asof=asof,
        window_days=window_days,
        scope=Scope(got.live_count, got.not_yet_created, got.roster_fallback),
        # the ticker a row shows is the card's own at the as-of (the Cockpit's ?name= key), the master's else
        roster_tickers={
            t.id: {m.security_id: m.ticker for m in reversed(t.basket) if m.security_id is not None}
            for t in got.cards
        },
        master_tickers=master.tickers_for(conn, sids),
        ciks=master.ciks_for(conn, sids),
        closes=_closes(
            conn, [s for s in sids if s not in flagged], asof, known_at, clock, window_days
        ),
        bench=_bench(conn, asof, known_at, clock, window_days),
        flagged=flagged,
    )


@router.get("", response_model=TriggerLogOut)
def trigger_log_page(
    asof: date = Query(
        ..., description="as-of date; no night recorded after it and no bar public after it is read"
    ),
    days: TriggerLogDays = Query(
        TriggerLogDays.D90, description="the window: the last 30 | 90 | 180 days"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> TriggerLogOut:
    """Every entry trigger and risk signal the recorded calls show firing across the cards in the window, newest
    recorded night first: each trigger once (the same trigger on many nights is one row, dated the first night
    the record shows it), with its fire date, role and key, source, the card's state that night beside the night
    before, whether it is still live, the sessions since and the name's as-traded return since against IWM.
    Display only, with no aggregate over the rows. A past as-of reads the nights recorded on or before it and
    the bars public by the end of that date."""
    known_at, clock = serve_view(asof)
    window = int(days)
    got = cached(
        conn, SLUG, (window, clock), asof, lambda: read_log(conn, asof, window, known_at, clock)
    )
    return TriggerLogOut.model_validate(
        {**asdict(got.value), "clock": clock, "cache": got.status, "compute_ms": got.compute_ms}
    )
