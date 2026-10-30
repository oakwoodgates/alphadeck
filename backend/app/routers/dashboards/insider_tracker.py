"""The Insider Buying dashboard's API (``docs/dashboards/insider-tracker.md``): one GET, every open-market insider
purchase (Form 4 code P) across the counted cards' names in a window, one row per Form 4, at the as-of dial. It READS
the cards that existed by the as-of with their rosters then (``dashboards/baskets.py``), every counted name's filings
through the shared reader (``dashboards/filings.py``: the point-in-time view plus the Form 4/A SELECT, both on the
request's one ``serve_view(asof)`` pair), the ``calls`` log's nights for the window (one plain SELECT; never
``call_for_thesis``), and, for the names with a purchase only, the data flags that reach a price reading and the
as-traded bars, shares facts and splits through the same point-in-time view, plus IWM's bars. It then hands them to the
pure compute in ``dashboards/insider_tracker.py``. The whole read is computed once per (window, Context-and-Bench
toggle, clock, as-of) in the shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7);
grouping, filters and sorts are the page's own. Its wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict
from datetime import date, datetime
from enum import IntEnum
from typing import Literal, NamedTuple
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from app.deps import get_conn
from dashboards.baskets import (
    cards_as_of,
    companies_in,
    counted_rows,
    display_ticker,
    memberships_of,
    per_company,
    price_flags,
    resolved_ids,
)
from dashboards.cache import cached
from dashboards.filings import read_filing_rows
from dashboards.insider_tracker import (
    CLUSTER_WINDOW_DAYS,
    DEFAULT_WINDOW,
    InsiderTracker,
    RoleClass,
    TrackerInputs,
    insider_tracker,
    purchase_security_ids,
    window_start,
)
from dashboards.peer_table import CapCell, CompanyReads, cap_cell
from dashboards.trigger_log import BENCHMARK, CardNights, Night, Scope, SinceStatus
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.market_time import serve_view
from domain.splits import listed_from_row
from signals.base import PointInTimeData
from signals.horizons import display_bounds

router = APIRouter(prefix="/insider-tracker")

SLUG = "insider-tracker"

# The ``calls`` log read: a plain SELECT scoped to the counted cards. A night with several rows reads its highest
# ``seq`` (the row that stood); the window is inclusive at both ends and never reaches past the as-of.
_WINDOW_SQL = (
    "SELECT DISTINCT ON (thesis_id, asof) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof >= %s AND asof <= %s ORDER BY thesis_id, asof, seq DESC"
)


class InsiderTrackerDays(IntEnum):
    """The window: the last 30 or 90 days."""

    D30 = 30
    D90 = 90


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class InsiderTrackerCardOut(_Wire):
    """A card the name sits on: ``ticker`` = the ticker the Cockpit's ``?name=`` key wants on that card; ``silent`` =
    ``Context`` or ``Bench`` when the name sits there only silently (it never counts toward the call).
    """

    thesis_id: UUID
    thesis_name: str
    ticker: str
    silent: str | None


class InsiderTrackerOtherBuyerOut(_Wire):
    """Another buyer in a cluster: the name and the filing date nearest the row's."""

    name: str
    filed: date


class InsiderTrackerClusterOut(_Wire):
    """Two or more distinct buyers on one name filed within 30 days of the row's filing date: ``buyers`` counts the
    row's own buyer; ``others`` names the rest."""

    buyers: int
    others: list[InsiderTrackerOtherBuyerOut]


class InsiderTrackerTriggerOut(_Wire):
    """A recorded insider trigger that carries the filing's accession on one card: ``recorded_on`` = the first night in
    the window the record shows it; ``live`` = it is on the card in effect at the as-of (``null`` = that card could
    not be read)."""

    thesis_id: UUID
    thesis_name: str
    recorded_on: date
    live: bool | None


class InsiderTrackerSinceOut(_Wire):
    """The name's as-traded return from the first close on or after the filing date to the last close on or before the
    as-of, with IWM on the same two dates. ``priced`` = both bars exist; ``unpriced`` = no bar since the filing;
    ``flagged`` = the name's price history is on the flagged-price list (``reason``, never priced). ``bench_ret`` is
    ``null`` when IWM has no bar on one of those two dates. Returns are fractions (0.05 = +5%)."""

    status: SinceStatus
    reason: str | None
    entry_date: date | None
    end_date: date | None
    ret: float | None
    bench_ret: float | None


class InsiderTrackerCapOut(_Wire):
    """The approximate market cap at the as-of (the Peer Table's rule) and its tier (``nano`` | ``micro`` | ``small`` |
    ``mid`` | ``large``, a filter dimension only). ``gap`` says why it is blank (``flagged`` | ``no_price`` |
    ``no_shares`` | ``ads_unread`` | ``no_cap``)."""

    value: float | None
    tier: str | None
    gap: str | None
    note: str | None


class InsiderTrackerRowOut(_Wire):
    """One Form 4 (or applied 4/A) holding an open-market purchase. ``filed`` = the filing date (the public clock);
    ``week`` = the Monday of its week; ``trade_date`` = the earliest purchase's trade date and ``filed_days_after`` =
    the days from it to the filing when the filing is later; ``filer`` / ``role`` = the first reporting owner and the
    filing's own role words as the reader gives them (``null`` = not stated) and ``co_filers`` = the "and N more"
    count; ``role_classes`` = the role words as officer, director, 10% owner (a role can be several) or other;
    ``shares`` / ``price`` / ``usd`` = the code-P lines' total, shares-weighted price and dollars (``null`` = not
    stated; the dollars are the purchases', not net of sales in the same filing); ``tags`` / ``headline`` / ``detail``
    = the reader's own field predicates and words, unchanged; ``cluster`` = see ``InsiderTrackerClusterOut``
    (``null`` = none); ``triggers`` = the recorded insider triggers carrying this accession (empty = not a trigger);
    ``silent_only`` = every card the name sits on holds it in Context or Bench."""

    key: str
    security_id: UUID
    ticker: str
    accession: str | None
    form: str
    url: str | None
    filed: date
    week: date
    trade_date: date | None
    filed_days_after: int | None
    cards: list[InsiderTrackerCardOut]
    silent_only: bool
    filer: str | None
    role: str | None
    role_classes: list[RoleClass]
    co_filers: int
    shares: float | None
    price: float | None
    usd: float | None
    tags: list[str]
    headline: str
    detail: list[str]
    cluster: InsiderTrackerClusterOut | None
    triggers: list[InsiderTrackerTriggerOut]
    since: InsiderTrackerSinceOut
    cap: InsiderTrackerCapOut


class InsiderTrackerNameOut(_Wire):
    """A name set aside from the tape, never from the list: ``reason`` = the flags' labels."""

    security_id: UUID
    ticker: str
    reason: str | None


class InsiderTrackerCountsOut(_Wire):
    """The work shown on the quiet line: counts of what was read and listed, never a statistic over the rows' returns.
    ``filings_read`` = the Form 4s and 4/As filed in the window = ``purchases`` + ``routine_folded`` (a Form 4 with no
    purchase) + ``amendments_no_purchase`` (a 4/A whose applied rows hold none); ``cluster_rows`` / ``trigger_rows`` =
    rows in a cluster / carried by a recorded insider trigger; ``filer_unresolved`` = rows whose filer the filing does
    not name; ``cap_blank`` = rows whose cap is blank; ``names_*`` = distinct names."""

    cards_counted: int
    live_cards: int
    not_yet_created: int
    roster_fallback: int
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int
    names_read: int
    filings_read: int
    purchases: int
    routine_folded: int
    amendments_no_purchase: int
    names_with_purchases: int
    filer_unresolved: int
    cluster_rows: int
    cluster_names: int
    trigger_rows: int
    nights_read: int
    nights_unreadable: int
    names_unpriced: int
    names_flagged: int
    cap_blank: int


class InsiderTrackerOut(_Wire):
    """The whole page at the as-of. ``clock``: ``record`` (the live read) or ``public`` (a past as-of: what was
    public by the end of that date). The window is ``window_start`` to ``asof``, inclusive, by FILING date; ``rows``
    are newest filing first, then dollar size, then ticker. ``include_silent`` = whether Context and Bench names are
    read. There is no aggregate over the rows (the Track Record page slices outcomes by market-cap tier). ``cache`` /
    ``compute_ms`` = whether the read came from the shared dashboard cache and how long it took when computed.
    """

    asof: date
    window_days: int
    window_start: date
    include_silent: bool
    clock: Literal["record", "public"]
    rows: list[InsiderTrackerRowOut]
    counts: InsiderTrackerCountsOut
    flagged: list[InsiderTrackerNameOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------


def read_nights(
    conn: psycopg.Connection, cards: Sequence[tuple[UUID, str]], asof: date, window_days: int
) -> list[CardNights]:
    """Each counted card's recorded nights in the window (``asof`` in ``[as-of - days, as-of]``, each night's highest
    ``seq``). A row dated after the as-of is never read; a stored card that no longer validates reads as
    ``card=None`` (counted, never read). No row before the window is read: a filing in the window was filed after
    it opened, so nothing recorded earlier can carry it."""
    ids = [tid for tid, _ in cards]
    nights: dict[UUID, list[Night]] = {tid: [] for tid in ids}
    if ids:
        with conn.cursor() as cur:
            cur.execute(_WINDOW_SQL, (ids, window_start(asof, window_days), asof))
            for r in cur.fetchall():
                try:
                    card: CallCard | None = CallCard.model_validate(r["card"])
                except ValidationError:
                    card = None
                nights[r["thesis_id"]].append(Night(r["asof"], r["seq"], r["reconstructed"], card))
    return [CardNights(tid, name, None, tuple(nights[tid])) for tid, name in cards]


class Tape(NamedTuple):
    """One purchase name's bars and its approximate cap at the as-of."""

    closes: dict[date, float]
    cap: CapCell


def read_tape(pit: PointInTimeData, sid: UUID, flag: str | None) -> Tape:
    """One company's closes (the view's price scale, positive or not: the compute drops a non-bar) and its approximate
    cap by the Peer Table's inputs and rule (the latest close, the latest shares fact, the splits between them). A
    company whose data flags reach its price readings reads no bar: the cap names the flag."""
    if flag is not None:
        return Tape({}, cap_cell(CompanyReads(flag=flag)))
    rows = [b for b in pit.price_history(sid) if b.get("close") is not None]
    closes = {b["d"]: float(b["close"]) for b in rows}
    close = (rows[-1]["d"], float(rows[-1]["close"])) if rows else None
    shares_facts = pit.shares_outstanding_facts(sid)
    shares = None
    if shares_facts:
        best = max(shares_facts, key=lambda f: f["valid_from"])
        shares = {k: best.get(k) for k in ("shares", "valid_from", "ads_ratio", "ads_ratio_status")}
    cap = cap_cell(
        CompanyReads(
            close=close,
            shares=shares,
            # the split list only matters to the cap (a count and a close both on file)
            splits=(
                tuple(listed_from_row(r) for r in pit.split_events(sid))
                if shares is not None and close is not None
                else ()
            ),
        )
    )
    return Tape(closes, cap)


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


def read_tracker(
    conn: psycopg.Connection,
    asof: date,
    window_days: int,
    include_silent: bool,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> InsiderTracker:
    """The cached read: the counted cards and their rosters at the as-of, every counted name's filings from
    ``CLUSTER_WINDOW_DAYS`` before the window through the as-of, the recorded nights of the window, then, for the
    names with a purchase in the window only, the data flags, the tape and the cap, and IWM's tape.
    """
    got = cards_as_of(conn, asof, known_at)
    counted = counted_rows(memberships_of(got.cards), include_silent)
    sids = resolved_ids(counted.rows)
    # the ticker a name shows under is the one most of its cards use (the Cockpit's ?name= key)
    tickers = {sid: display_ticker(cards) for sid, cards in companies_in(counted.rows)}
    filings = read_filing_rows(
        conn, sids, asof, known_at, clock, days=window_days + CLUSTER_WINDOW_DAYS, tickers=tickers
    )
    buying = purchase_security_ids(filings, asof, window_days)
    flagged = price_flags(conn, buying, asof, known_at=known_at, clock=clock)
    tapes = per_company(
        conn, buying, asof, known_at, clock, lambda pit, sid: read_tape(pit, sid, flagged.get(sid))
    )
    return insider_tracker(
        TrackerInputs(
            asof=asof,
            window_days=window_days,
            include_silent=include_silent,
            scope=Scope(got.live_count, got.not_yet_created, got.roster_fallback),
            basket_rows=counted.basket_rows,
            silent_rows_left_out=counted.silent_rows_left_out,
            unresolved_rows=counted.unresolved_rows,
            memberships=counted.rows,
            filings=filings,
            nights=read_nights(conn, [(t.id, t.name) for t in got.cards], asof, window_days),
            closes={sid: t.closes for sid, t in tapes.items()},
            bench=_bench(conn, asof, known_at, clock, window_days),
            flagged=flagged,
            caps={sid: t.cap for sid, t in tapes.items()},
        )
    )


@router.get("", response_model=InsiderTrackerOut)
def insider_tracker_page(
    asof: date = Query(
        ..., description="as-of date; no filing, night or bar public after it is read"
    ),
    days: InsiderTrackerDays = Query(
        InsiderTrackerDays(DEFAULT_WINDOW),
        description="the window by filing date: the last 30 | 90 days",
    ),
    silent: bool = Query(
        False,
        description="also read the names the cards hold in Context and Bench (tagged; off by default)",
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> InsiderTrackerOut:
    """Every open-market insider purchase (Form 4 code P) across the counted cards' names in the window, newest
    filing first, one row per Form 4: the filer and the role as the filing gives them, the shares, price and dollar
    size, whether other buyers filed on the same name within 30 days, whether a recorded insider trigger carries the
    filing's accession, the name's as-traded return since the filing against IWM and its approximate cap and tier.
    A row is dated by the day the filing was filed, never by the trade. Display only, with no aggregate over the rows.
    A past as-of reads the filings and the bars public by the end of that date and the nights recorded on or before
    it."""
    known_at, clock = serve_view(asof)
    window = int(days)
    got = cached(
        conn,
        SLUG,
        (window, silent, clock),
        asof,
        lambda: read_tracker(conn, asof, window, silent, known_at, clock),
    )
    return InsiderTrackerOut.model_validate(
        {**asdict(got.value), "clock": clock, "cache": got.status, "compute_ms": got.compute_ms}
    )
