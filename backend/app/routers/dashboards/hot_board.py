"""The Hot Board dashboard's API (``docs/dashboards/hot-board.md``): one GET over every live card, at the as-of dial.
It READS the cards counted at the as-of with their rosters then (``dashboards/baskets.py``), the ``calls`` log (the
one recorded card in effect per card, and, for the Armed and Managing ones, the log rows the arm-night walk reads),
the master's tickers, names and issuer CIKs, then, for the names on a row only, the display members' readings through
the same point-in-time view the Cockpit builds (``baskets.per_company``: the request's one ``serve_view(asof)`` pair
is threaded into every read, the data-flag read and the benchmark's bars), and hands everything to the pure compute in
``dashboards/hot_board.py``, where every rule lives. It never calls ``call_for_thesis``: the rows are the RECORD.
The whole read is cached per (clock, as-of) in the shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md``
rule 7); the toggles, filters, sorts and heat are the page's. Its wire models live here, not in ``app/schemas_api.py``
(rule 8)."""

from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, timedelta
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
)
from dashboards.cache import cached
from dashboards.filings import source_url
from dashboards.hot_board import (
    BENCHMARK,
    CALENDAR_SESSIONS,
    MEMBERS,
    SLUG,
    ArmNight,
    Bars,
    Candidate,
    CompanyTape,
    HotBoard,
    KeyName,
    LogRow,
    Recorded,
    RowKind,
    SinceGap,
    arm_nights,
    assemble,
    candidates,
    card_state_counts,
    company_tape,
    earliest_arm_night,
)
from dashboards.peer_table import CompanyReads, Gap, cap_cell
from dashboards.series import session_calendar
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.enums import Grade, Kind, State, Verdict
from domain.market_cap import CapTier
from domain.market_time import serve_view
from domain.splits import listed_from_row
from securities import master
from signals.base import PointInTimeData
from signals.display import insider_flow, trailing_returns
from signals.display.base import DisplayMember, DisplaySignal
from signals.horizons import display_bounds

router = APIRouter(prefix="/hot-board")

# The calls-log reads: plain SELECTs, all scoped to the counted cards' ids. "In effect at D" = the greatest
# (asof, seq) with asof <= D, one row per card (the What Changed read); the walk's rows are every row on or before
# D, without the card blob but for the security ids of its armed members.
_IN_EFFECT_SQL = (
    "SELECT DISTINCT ON (thesis_id) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof DESC, seq DESC"
)
_RUN_SQL = (
    "SELECT thesis_id, asof, seq, reconstructed, state, "
    "COALESCE(jsonb_path_query_array(card, '$.armed_members[*].security_id'), '[]'::jsonb) AS armed_ids "
    "FROM calls WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof DESC, seq DESC"
)


# --- the wire models --------------------------------------------------------------------------------------------


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class HotCardRefOut(_Wire):
    thesis_id: UUID
    thesis_name: str


class HotSourceOut(_Wire):
    """One place a trigger points at: ``source`` = the kind of source as stored ("form4", "price", "xbrl", a form),
    ``ref`` = its reference, ``url`` = a link to it by the Evidence Ledger's rule (``null`` = none known).
    """

    source: str
    ref: str
    url: str | None


class HotTriggerOut(_Wire):
    """A fired entry trigger. ``event_date`` = its FIRE date (the market event: an insider trade date, a breakout
    bar), not the night the platform recorded it."""

    label: str
    kind: Kind
    grade: Grade | None
    event_date: date | None
    sources: list[HotSourceOut]


class HotCellOut(_Wire):
    """A number, or ``note`` = the reason it is blank (the display member's own words where it gave some). Blank
    is never zero."""

    value: float | None
    note: str | None


class HotTapeOut(_Wire):
    """One company's display-member readings at the as-of. ``flag`` = the data flags' labels when they reach its
    price readings (every price-derived cell is then blank and says so; the insider counts stay). ``vs_iwm`` = the
    30-trading-day return minus IWM's, in percentage points (``null`` when the two last closes are not the same
    day). ``rs_leading`` = relative strength at a fresh 13-week high (``rs_leading_vs`` = against which benchmark;
    ``null`` = no 13-week verdict yet). ``buys_30d`` / ``buyers_30d`` = open-market buys and distinct buyers in
    the last 30 days (``null`` = no Form 4 ingested). Units: percent for the returns and the two distances, RVOL as
    a ratio, dollar volume in USD. ``spark`` = the last 90 closes (``null`` = no bar; a thin tape is left-padded).
    """

    flag: str | None
    rvol20: HotCellOut
    vs_50d: HotCellOut
    off_high: HotCellOut
    vs_iwm: HotCellOut
    rs_leading: bool | None
    rs_leading_vs: list[str]
    ret_7d: HotCellOut
    ret_30d: HotCellOut
    buys_30d: int | None
    buyers_30d: int | None
    adv_usd_20d: HotCellOut
    spark: list[float | None]


class HotCapOut(_Wire):
    """The approximate market cap (USD) and its tier: the latest close times the latest shares fact, the
    Workbench's rule (the Peer Table's ``cap_cell``). ``gap`` says why it is blank."""

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


class HotClocksOut(_Wire):
    """A row's clocks at the as-of. ``arm_night`` = the first night of the member's current recorded run
    (``arm_reconstructed`` = that night was rebuilt by ``pipeline.backfill``); ``sessions_since`` = sessions after it
    through the as-of on IWM's bar calendar; ``sessions_left`` = Mon-Fri sessions after the as-of through
    ``arm_until`` (``null`` once it has passed, when ``closed_ago`` = the sessions since it ended); ``days_to_exit`` =
    calendar days from the as-of to ``exit_by`` (negative = passed)."""

    arm_night: date | None
    arm_reconstructed: bool
    sessions_since: int | None
    arm_until: date | None
    sessions_left: int | None
    closed_ago: int | None
    exit_by: date | None
    days_to_exit: int | None


class HotSinceArmOut(_Wire):
    """The name's as-traded return from the first close on or after the arm night (``entry_*``) to its last close
    on or before the as-of (``last_*``), and IWM's over the same dates. ``gap`` says why ``ret_pct`` is blank:
    ``no_arm_night`` (not an armed row), ``flagged``, ``before_window`` (the arm night is before the earliest bar
    the view reads) or ``no_bar``."""

    entry_date: date | None
    entry_close: float | None
    last_date: date | None
    last_close: float | None
    ret_pct: float | None
    iwm_pct: float | None
    gap: SinceGap | None


class HotRowOut(_Wire):
    """One (card, name). ``kind``: ``armed`` (the card's armed members: the default rows), ``warming`` (a name with a
    live entry trigger on a Warming card; ``keys`` names the key each trigger turns), ``watch`` (an Armed card's
    confirmation-only member) or ``managing`` (a Managing card's held and armed members). ``also_armed`` = the other
    cards on which the same security is an armed row. ``confidence`` = setup strength, an experimental relative
    indicator, not a probability. ``risk_kinds`` = one entry per risk signal on the card that names this security.
    ``ticker`` is the card's roster ticker (the Cockpit's ``?name=`` key), else the master's, else the security id.
    """

    key: str
    kind: RowKind
    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str
    name: str | None
    also_armed: list[HotCardRefOut]
    verdict: Verdict | None
    entry_grade: Grade | None
    confidence: float | None
    keys: list[KeyName]
    triggers: list[HotTriggerOut]
    risk_kinds: list[Kind]
    clocks: HotClocksOut
    entry_window_closing: bool
    lapsing: bool
    since_arm: HotSinceArmOut
    tape: HotTapeOut
    cap: HotCapOut


class HotFlaggedOut(_Wire):
    """A company on a row whose data flags reach its price readings at the as-of: its row stays, its price cells
    name ``reason`` (the flags' labels) and it has no heat."""

    security_id: UUID
    ticker: str
    reason: str


class HotBoardOut(_Wire):
    """Every candidate row of the cards' recorded calls in effect at the as-of (the page derives the toggles, filters,
    sorts and heat from this one read) and the counts its quiet line shows. ``cards_*`` = the counted cards by their
    recorded state (``cards_no_call`` = nothing recorded by then); ``calendar_*`` = IWM's bar calendar the
    ``sessions_since`` count reads; ``price_window_start`` = the earliest bar the view reads (``null`` = unbounded);
    ``no_ticker_rows`` = rows whose security has no ticker anywhere (shown under its id); ``flagged`` = the
    companies on a row with a flagged price history. ``clock``: ``record`` (the live read) or ``public`` (a past
    as-of: what was public by the end of that date). ``cache`` / ``compute_ms`` = whether the read came from the
    shared dashboard cache and how long it took when computed."""

    asof: date
    clock: Literal["record", "public"]
    benchmark: str
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    cards_armed: int
    cards_warming: int
    cards_managing: int
    cards_incubating: int
    cards_no_call: int
    calendar_source: Literal["benchmarks", "companies", "none"]
    calendar_sessions: int
    calendar_first: date | None
    calendar_last: date | None
    price_window_start: date | None
    no_ticker_rows: int
    rows: list[HotRowOut]
    flagged: list[HotFlaggedOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) ----------------------------------------------------


def read_recorded(conn: psycopg.Connection, ids: list[UUID], asof: date) -> dict[UUID, Recorded]:
    """The recorded card in effect at ``asof`` per card (the greatest ``(asof, seq)`` with ``asof <=`` the as-of);
    a card with no row by then is absent."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(_IN_EFFECT_SQL, (ids, asof))
        return {
            r["thesis_id"]: Recorded(
                asof=r["asof"],
                seq=r["seq"],
                reconstructed=r["reconstructed"],
                card=CallCard.model_validate(r["card"]),
            )
            for r in cur.fetchall()
        }


def read_runs(conn: psycopg.Connection, ids: list[UUID], asof: date) -> dict[UUID, list[LogRow]]:
    """Every log row on or before the as-of for the cards, newest first, narrowed to what the arm-night walk reads."""
    if not ids:
        return {}
    out: dict[UUID, list[LogRow]] = {}
    with conn.cursor() as cur:
        cur.execute(_RUN_SQL, (ids, asof))
        for r in cur.fetchall():
            out.setdefault(r["thesis_id"], []).append(
                LogRow(
                    asof=r["asof"],
                    seq=r["seq"],
                    reconstructed=r["reconstructed"],
                    state=State(r["state"]),
                    armed_ids=frozenset(UUID(s) for s in r["armed_ids"]),
                )
            )
    return out


def _benchmark_pit(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> PointInTimeData:
    return PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=display_bounds(),
        clock=clock,
    )


def _bars(rows: list[dict]) -> Bars:
    """(date, close) ascending, positive closes only: a non-positive close is not a bar."""
    return tuple(
        (b["d"], float(b["close"]))
        for b in rows
        if b.get("close") is not None and float(b["close"]) > 0
    )


def read_tape(
    pit: PointInTimeData,
    sid: UUID,
    asof: date,
    members: dict[str, DisplayMember],
    flag: str | None,
    since_from: date | None,
    iwm: DisplaySignal | None,
) -> CompanyTape:
    """One company's tape from the point-in-time view: the eight display members, its approximate cap (the Peer
    Table's inputs and rule) and its closes from its earliest arm night on. A company whose data flags reach its
    price readings has its price readings left unread (``flag`` names why); the insider member stays.
    """
    if flag is not None:
        insider = members[insider_flow.MEMBER_NAME](pit, sid, asof)
        return company_tape(
            {insider_flow.MEMBER_NAME: insider}, iwm, cap_cell(CompanyReads(flag=flag)), flag=flag
        )
    rows = [b for b in pit.price_history(sid) if b.get("close") is not None]
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
    bars = _bars(rows)
    return company_tape(
        {name: m(pit, sid, asof) for name, m in members.items()},
        iwm,
        cap,
        tuple(b for b in bars if since_from is not None and b[0] >= since_from),
    )


def read_board(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> HotBoard:
    """The heavy read the cache holds: the candidate rows of every counted card's recorded call, with their
    clocks, since-arm returns, tape readings and caps. Reads only; the request's ``known_at`` + ``clock`` ride into
    the roster read, the data-flag read, the benchmark's bars and every company's view."""
    counted = cards_as_of(conn, asof, known_at)
    names = {t.id: t.name for t in counted.cards}
    recorded = read_recorded(conn, list(names), asof)
    runs = read_runs(
        conn,
        [t for t, r in recorded.items() if r.card.state in (State.ARMED, State.MANAGING)],
        asof,
    )
    cands: list[Candidate] = candidates(names, recorded)
    arms: dict[tuple[UUID, UUID], ArmNight | None] = arm_nights(cands, runs)
    sids = sorted({c.security_id for c in cands}, key=str)
    since_from = earliest_arm_night(arms)

    memberships = memberships_of(counted.cards)
    roster_tickers: dict[tuple[UUID, UUID], str] = {}
    for m in memberships:
        if m.security_id is not None:
            roster_tickers.setdefault((m.thesis_id, m.security_id), m.ticker)

    window_days = display_bounds().get("fact_price_eod")
    pit = _benchmark_pit(conn, asof, known_at, clock)
    iwm_bars = _bars(pit.benchmark_prices(BENCHMARK, lookback_days=window_days))
    iwm_trailing = trailing_returns.compute(
        pit.benchmark_prices(BENCHMARK, lookback_days=trailing_returns.LOOKBACK_DAYS), asof
    )

    flags = price_flags(conn, sids, asof, known_at=known_at, clock=clock)
    members = display_members(MEMBERS, "Hot Board")
    tapes = per_company(
        conn,
        sids,
        asof,
        known_at,
        clock,
        lambda p, sid: read_tape(
            p, sid, asof, members, flags.get(sid), since_from.get(sid), iwm_trailing
        ),
    )
    calendar, source = session_calendar(
        (d for d, _ in iwm_bars),
        (d for t in tapes.values() for d, _ in t.bars),
        asof,
        CALENDAR_SESSIONS,
    )
    trigger_sids = {t.security_id for c in cands for t in c.triggers}
    return assemble(
        cands,
        asof=asof,
        clock=clock,
        arms=arms,
        tapes=tapes,
        roster_tickers=roster_tickers,
        master_tickers=master.tickers_for(conn, sids),
        company_names={
            sid: (str(v["name"]) if v.get("name") else None)
            for sid, v in master.identity_for(conn, set(sids)).items()
        },
        ciks=master.ciks_for(conn, trigger_sids),
        iwm_bars=iwm_bars,
        calendar=calendar,
        calendar_source=source,
        window_start=None if window_days is None else asof - timedelta(days=window_days),
        url_of=source_url,
        counts=card_state_counts(names, recorded),
        live_cards=counted.live_count,
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        cards_counted=len(counted.cards),
    )


# --- the route --------------------------------------------------------------------------------------------------


@router.get("", response_model=HotBoardOut)
def hot_board(
    asof: date = Query(
        ..., description="as-of date; only what was recorded and public by then is read"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> HotBoardOut:
    """Every name the recorded calls in effect at the as-of show Armed, one row each, with the tape beside it:
    the call's own fields, the arm night and the session clocks, the return since the arm night against IWM, the
    display members' readings and the approximate cap. Warming, watch and managing names ride along as candidate
    rows for the page's toggles. Display only: it reads the calls log and never the live call; the heat ranking is
    computed on the page from the rows it shows. A past as-of reads what was public by the end of that date.
    """
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(conn, SLUG, (clock,), asof, lambda: read_board(conn, asof, known_at, clock))
    return HotBoardOut.model_validate(
        {**_payload(got.value), "cache": got.status, "compute_ms": got.compute_ms},
    )


def _payload(board: HotBoard) -> dict:
    return {f.name: getattr(board, f.name) for f in fields(board)}
