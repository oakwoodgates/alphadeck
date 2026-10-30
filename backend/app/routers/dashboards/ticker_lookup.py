"""The Ticker Lookup dashboard's API (``docs/dashboards/ticker-lookup.md``): what the platform holds about ONE company
at the as-of. Two GETs: ``/resolve?t=`` turns a typed ticker into a ``security_id`` (the master's exact ticker, else
a former ticker on request), and ``/{security_id}?asof=`` returns the whole page. It READS the master identity, the
counted cards with their rosters as of the as-of (``dashboards/baskets.py``), the ``calls`` log rows of the cards the
company sits on, the company's readings through the same point-in-time view the Cockpit builds
(``baskets.per_company``: the request's one ``serve_view(asof)`` pair is threaded into every read), its filings
(the shared reader, ``dashboards/filings.py``) and the operator's decision log, then hands them to the pure compute
in ``dashboards/ticker_lookup.py``. The page is computed once per (company, clock, as-of) in the shared dashboard
cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7); the filings window toggle is the page's. Its wire
models live here, not in ``app/schemas_api.py`` (rule 8). Display only: SELECTs and point-in-time reads, no write,
no LLM, no ``call_for_thesis``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from psycopg.types.json import Json
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import cards_as_of, per_company, price_flags
from dashboards.cache import cached
from dashboards.filings import FilingKind, FoldGroup, read_filing_rows
from dashboards.ticker_lookup import (
    READ_DAYS,
    SLUG,
    TRIGGER_DAYS,
    CardMeta,
    DecisionRaw,
    IdentityInputs,
    Lookup,
    RecordedNight,
    cap_of,
    card_catalysts,
    cards_section,
    decision_rows,
    identity_of,
    in_effect,
    lookup,
    sits_on,
    tape_of,
    trigger_log,
)
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.market_time import market_tz, serve_view
from domain.splits import ListedSplit, listed_from_row
from domain.thesis import Thesis
from pipeline.backfill import thesis_existed_on
from repositories import thesis_repo
from securities import master
from signals.base import PointInTimeData
from signals.display import etf_flow, price_path, registered_display_members
from signals.display.base import DisplaySignal

router = APIRouter(prefix="/ticker-lookup")


# --- the reads (SELECT only) -------------------------------------------------------------------------------

# The calls log reads: plain SELECTs scoped to the cards the company sits on. A night's winning row is its
# highest ``seq``; "in effect" before the window is the greatest (asof, seq) with an earlier asof (the log appends
# only on change, so that row stood through the window's first nights).
_NIGHTS_SQL = (
    "SELECT DISTINCT ON (thesis_id, asof) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof >= %s AND asof <= %s ORDER BY thesis_id, asof, seq DESC"
)
_PRIOR_SQL = (
    "SELECT DISTINCT ON (thesis_id) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof < %s ORDER BY thesis_id, asof DESC, seq DESC"
)
# the operator's decision log: the rows naming the company, plus every void row (a void names the row it strikes,
# and may carry no security of its own). A past view counts a row only once it was logged by that view's instant
# (``serve_view``: the position stays as of the same instant); a live view (``known_at`` None) reads them all.
_DECISIONS_SQL = (
    "SELECT id, seq, thesis_id, security_id, action, decision_date, shares, price, reason, voids "
    "FROM operator_decision WHERE thesis_id = ANY(%s) AND (security_id = %s OR action = 'void') "
    "AND decision_date <= %s AND (%s::timestamptz IS NULL OR recorded_at <= %s) ORDER BY thesis_id, seq"
)


def _night(r: dict[str, Any]) -> RecordedNight:
    return RecordedNight(
        thesis_id=r["thesis_id"],
        asof=r["asof"],
        seq=r["seq"],
        reconstructed=r["reconstructed"],
        card=CallCard.model_validate(r["card"]),
    )


def read_nights(
    conn: psycopg.Connection, ids: list[UUID], start: date, asof: date
) -> tuple[dict[UUID, list[RecordedNight]], dict[UUID, RecordedNight]]:
    """``(nights, prior)``: each card's winning row per night dated in [``start``, ``asof``], and the row in
    effect the night before ``start``."""
    if not ids:
        return {}, {}
    nights: dict[UUID, list[RecordedNight]] = {}
    with conn.cursor() as cur:
        cur.execute(_NIGHTS_SQL, (ids, start, asof))
        for r in cur.fetchall():
            nights.setdefault(r["thesis_id"], []).append(_night(r))
        cur.execute(_PRIOR_SQL, (ids, start))
        prior = {r["thesis_id"]: _night(r) for r in cur.fetchall()}
    return nights, prior


def read_decisions(
    conn: psycopg.Connection,
    ids: list[UUID],
    sid: UUID,
    asof: date,
    known_at: datetime | None,
) -> list[DecisionRaw]:
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute(_DECISIONS_SQL, (ids, sid, asof, known_at, known_at))
        return [
            DecisionRaw(
                id=r["id"],
                seq=r["seq"],
                thesis_id=r["thesis_id"],
                security_id=r["security_id"],
                action=r["action"],
                decision_date=r["decision_date"],
                shares=float(r["shares"]) if r["shares"] is not None else None,
                price=float(r["price"]) if r["price"] is not None else None,
                reason=r["reason"],
                voids=r["voids"],
            )
            for r in cur.fetchall()
        ]


def read_archived(
    conn: psycopg.Connection, sid: UUID, asof: date, known_at: datetime | None
) -> list[Thesis]:
    """The archived cards that existed by the as-of AND hold the company in their roster then (the counted cards'
    rule: ``get_asof``, today's roster standing in before the first snapshot). The SQL only NARROWS which archived
    cards are loaded (an archived card whose live roster and every roster snapshot lack the company cannot hold it);
    ``sits_on`` on the roster as of the as-of is the page's rule."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT t.id FROM thesis t WHERE t.archived_at IS NOT NULL AND ("
            " EXISTS (SELECT 1 FROM basket_member b WHERE b.thesis_id = t.id AND b.security_id = %(sid)s)"
            " OR EXISTS (SELECT 1 FROM basket_snapshot s WHERE s.thesis_id = t.id"
            " AND s.members @> %(probe)s::jsonb)) ORDER BY t.name",
            {"sid": sid, "probe": Json([{"security_id": str(sid)}])},
        )
        ids = [r["id"] for r in cur.fetchall()]
    born = thesis_repo.created_at_for(conn, ids)
    tz = market_tz()
    out: list[Thesis] = []
    for tid in ids:
        # a missing created_at keeps the card (the counted cards' recall-first rule)
        if tid in born and not thesis_existed_on(born[tid], asof, tz):
            continue
        card = thesis_repo.get_asof(conn, tid, known_at)
        if card is not None:
            out.append(card)
    return out


def read_former_tickers(conn: psycopg.Connection, sid: UUID) -> tuple[str, ...]:
    """The company's former tickers (``security_ticker_history``), oldest first."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT old_ticker FROM security_ticker_history WHERE tenant_id = %s AND security_id = %s "
            "ORDER BY seen_at, id",
            (DEFAULT_TENANT_ID, sid),
        )
        return tuple(dict.fromkeys(r["old_ticker"] for r in cur.fetchall()))


def read_identity(conn: psycopg.Connection, sid: UUID) -> IdentityInputs | None:
    """The master's row for the company (``None`` = no such company)."""
    sec = master.get(conn, sid)
    if sec is None:
        return None
    ident = master.identity_for(conn, {sid}).get(sid, {})

    def text(key: str) -> str | None:
        v = ident.get(key)
        return str(v) if v else None

    return IdentityInputs(
        security_id=sid,
        ticker=sec.ticker,
        name=sec.name,
        cik=sec.cik,
        exchange=text("exchange"),
        business_type=text("business_type"),
        supersector=text("business_supersector"),
        royalty=bool(ident.get("royalty")),
        origin=text("origin"),
        instrument_kind=text("instrument_kind") or "equity",
        price_symbol=text("price_symbol"),
        former_tickers=read_former_tickers(conn, sid),
    )


@dataclass(frozen=True)
class CompanyRead:
    """What the point-in-time view gave for the company: the display members' readings (``asked`` = the member
    names that were read, in order), its bars, the latest close and shares fact and the split list the cap needs,
    and IWM's bars for the sparkline."""

    signals: dict[str, DisplaySignal | None]
    asked: list[str]
    bars: list[dict[str, Any]]
    iwm_bars: list[dict[str, Any]]
    close: tuple[date, float] | None
    shares: dict[str, Any] | None
    splits: tuple[ListedSplit, ...]


def read_company(pit: PointInTimeData, sid: UUID, asof: date, is_etf: bool) -> CompanyRead:
    """The company's readings from the point-in-time view: every registered display member (the ETF flow member
    only for an ETF), the bars, and the cap's inputs (the latest close, the latest shares fact, the split list).
    """
    signals: dict[str, DisplaySignal | None] = {}
    asked: list[str] = []
    for member in registered_display_members():
        if member.name == etf_flow.MEMBER_NAME and not is_etf:
            continue
        asked.append(member.name)
        signals[member.name] = member(pit, sid, asof)
    bars = [b for b in pit.price_history(sid) if b.get("close") is not None]
    close = (bars[-1]["d"], float(bars[-1]["close"])) if bars else None
    facts = pit.shares_outstanding_facts(sid)
    shares = None
    if facts:
        best = max(facts, key=lambda f: f["valid_from"])
        shares = {k: best.get(k) for k in ("shares", "valid_from", "ads_ratio", "ads_ratio_status")}
    return CompanyRead(
        signals=signals,
        asked=asked,
        bars=bars,
        iwm_bars=pit.benchmark_prices("IWM", lookback_days=price_path.LOOKBACK_DAYS),
        close=close,
        shares=shares,
        # the split list only matters to the cap (a count and a close both on file)
        splits=(
            tuple(listed_from_row(r) for r in pit.split_events(sid))
            if shares is not None and close is not None
            else ()
        ),
    )


def read_lookup(
    conn: psycopg.Connection,
    security_id: UUID,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> Lookup:
    """The heavy read the cache holds. ``known_at`` + ``clock`` are the request's ``serve_view(asof)`` pair,
    threaded into the counted cards' rosters, the price flags, the point-in-time view, the filings and the
    decision log alike."""
    me = read_identity(conn, security_id)
    if me is None:
        raise HTTPException(status_code=404, detail="no company with this id")
    flags = price_flags(conn, [security_id], asof, known_at=known_at, clock=clock)
    flag = flags.get(security_id)

    # the counted cards and their rosters are the same for every company at one as-of and clock: one cached read
    # (the page below is the per-company one), so only the first lookup after a run or a basket edit loads them
    counted = cached(
        conn, SLUG, ("cards", clock), asof, lambda: cards_as_of(conn, asof, known_at)
    ).value
    mine = [t for t in counted.cards if sits_on(t, security_id)]
    ids = [t.id for t in mine]
    window_start = asof - timedelta(days=TRIGGER_DAYS)
    nights, prior = read_nights(conn, ids, window_start, asof)
    effect = {tid: in_effect(nights.get(tid, ()), prior.get(tid)) for tid in ids}
    cards = cards_section(
        security_id,
        counted.cards,
        effect,
        read_archived(conn, security_id, asof, known_at),
        live_cards=counted.live_count,
        not_yet_created=counted.not_yet_created,
        roster_fallback=counted.roster_fallback,
    )
    metas = [
        CardMeta(
            t.id,
            t.name,
            next(m.ticker for m in t.basket if m.security_id == security_id),
        )
        for t in mine
    ]
    triggers = trigger_log(security_id, metas, nights, prior, window_start=window_start, cik=me.cik)

    read = per_company(
        conn,
        [security_id],
        asof,
        known_at,
        clock,
        lambda pit, sid: read_company(pit, sid, asof, me.instrument_kind == "etf"),
    )[security_id]
    tape = tape_of(read.signals, read.asked, bars=read.bars, iwm_bars=read.iwm_bars)
    cap = cap_of(flag, read.close, read.shares, read.splits)

    filing_rows = read_filing_rows(
        conn,
        [security_id],
        asof,
        known_at,
        clock,
        days=READ_DAYS,
        tickers={security_id: me.ticker} if me.ticker else None,
    ).get(security_id, ())
    decisions = decision_rows(
        read_decisions(conn, [t.id for t in counted.cards], security_id, asof, known_at),
        security_id,
        asof,
        {t.id: t.name for t in counted.cards},
    )
    return lookup(
        identity_of(me, cap, tape, flag),
        cards,
        triggers,
        tape,
        filing_rows,
        card_catalysts(counted.cards, security_id, asof),
        decisions,
    )


# --- the wire ----------------------------------------------------------------------------------------------


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


Unit = Literal["pct", "usd", "price", "count", "ratio"]


class TickerLookupResolveOut(_Wire):
    """A typed ticker resolved to its company. ``matched``: ``ticker`` (the master's current ticker) or
    ``former`` (a former ticker the company was renamed from; only when asked for)."""

    security_id: UUID
    ticker: str
    name: str | None
    matched: Literal["ticker", "former"]


class TickerLookupCapOut(_Wire):
    """The approximate market cap (latest close times the latest shares fact, the Peer Table's rule). ``gap`` is
    why it is blank (``flagged`` = a data flag reaches the price, the reason in ``note``); ``value`` is never zero
    for a blank."""

    value: float | None
    tier: str | None
    gap: str | None
    note: str | None
    close: float | None
    close_date: date | None
    shares: float | None
    shares_date: date | None
    ads_ratio: int | None
    split_factor: float | None


class TickerLookupIdentityOut(_Wire):
    """Who the company is: the master's identity (``edgar_url`` from its CIK; ``price_symbol`` only when the
    vendor symbol differs from the ticker; ``former_tickers`` oldest first), its approximate market cap, the 20-day
    dollar volume (from the tape, ``adv_note`` when blank) and ``flag``: the data flags reaching its price readings
    (never hidden)."""

    security_id: UUID
    ticker: str | None
    name: str | None
    cik: str | None
    edgar_url: str | None
    exchange: str | None
    business_type: str | None
    supersector: str | None
    royalty: bool
    origin: str | None
    instrument_kind: str
    price_symbol: str | None
    former_tickers: list[str]
    cap: TickerLookupCapOut
    adv_usd_20d: float | None
    adv_note: str | None
    flag: str | None


class TickerLookupLinkOut(_Wire):
    """One basket row of the company on a card: its link (``null`` = no link), the thesis-fit prose with who wrote
    it (``system_drafted`` or ``operator_edited``), whether the operator signed the name off and the operator's
    1-5 weight (display only, never a call input)."""

    link: str | None
    thesis_fit: str | None
    authored_by: str
    signed_off: bool
    weight: int | None


class TickerLookupPlaceOut(_Wire):
    """The company's part in the recorded call in effect: ``armed`` (verdict, grade, setup strength), ``watch``,
    ``triggers`` (live entry triggers; ``turns`` = the keys they turn), ``none``, ``not_counted`` (Context or
    Bench) or ``no_record`` (the card has no recorded call yet)."""

    kind: Literal["armed", "watch", "triggers", "none", "not_counted", "no_record"]
    verdict: str | None
    grade: str | None
    strength: float | None
    live_triggers: int
    turns: list[str]
    live_risks: int


class TickerLookupCardOut(_Wire):
    """One counted card the company sits on, its links merged. ``state`` / ``verdict`` / ``recorded_on`` = the
    recorded call in effect at the as-of (``null`` = none yet); ``silent`` = ``Context`` or ``Bench``.
    """

    thesis_id: UUID
    thesis_name: str
    ticker: str
    links: list[TickerLookupLinkOut]
    silent: str | None
    context_role: str | None
    signed_off: bool
    state: str | None
    verdict: str | None
    recorded_on: date | None
    reconstructed: bool
    place: TickerLookupPlaceOut


class TickerLookupCardNameOut(_Wire):
    thesis_id: UUID
    thesis_name: str


class TickerLookupExcludedOut(_Wire):
    thesis_id: UUID
    thesis_name: str
    reason: str | None


class TickerLookupCardsOut(_Wire):
    """The cards section: ``counted`` = the cards that existed by the as-of (``live_cards`` today, ``not_yet_created``
    left out, ``roster_fallback`` with today's roster standing in), the rows for the ones the company sits on, the
    archived cards that hold it and the counted cards that exclude it."""

    live_cards: int
    not_yet_created: int
    roster_fallback: int
    counted: int
    rows: list[TickerLookupCardOut]
    archived: list[TickerLookupCardNameOut]
    excluded: list[TickerLookupExcludedOut]


class TickerLookupSourceOut(_Wire):
    source: str
    ref: str
    url: str | None
    detail: list[str]


class TickerLookupTriggerOut(_Wire):
    """One trigger the recorded calls show on the company (identity: role, kind, fire date; the label is not part
    of it). ``recorded_on`` = the first night in the window the record shows it; ``label`` = its label that night
    and ``label_changes`` = how many distinct other labels the record showed for it from then through the
    as-of; ``recorded_after_days`` = how far that trails the fire date; ``turns`` = the key an entry trigger
    turns; ``live`` = still on the card in effect at the as-of.
    """

    thesis_id: UUID
    thesis_name: str
    ticker: str
    recorded_on: date
    reconstructed: bool
    event_date: date | None
    recorded_after_days: int | None
    role: Literal["entry_trigger", "risk_signal"]
    kind: str
    grade: str | None
    label: str
    label_changes: int
    turns: Literal["conviction", "confirmation"] | None
    sources: list[TickerLookupSourceOut]
    live: bool


class TickerLookupTriggersOut(_Wire):
    window_start: date
    events: list[TickerLookupTriggerOut]
    cards_read: int
    nights_read: int
    reconstructed_nights: int
    before_window_live: int


class TickerLookupFigureOut(_Wire):
    """One tape figure as the display member states it. ``value`` is ``null`` with the member's ``note`` when it
    could not compute it. ``level`` / ``on`` = the 52-week high or low it is measured from, and its day.
    """

    member: str
    key: str
    group: Literal["price", "returns", "range", "volume", "strength", "insider", "flow"]
    label: str
    value: float | None
    unit: Unit | None
    note: str | None
    tone: Literal["pos", "neg"] | None
    level: float | None
    on: date | None


class TickerLookupMemberGapOut(_Wire):
    member: str
    label: str
    note: str


class TickerLookupSparklineOut(_Wire):
    """The company's last closes and IWM's on the same sessions, both indexed to 100 at the first session both
    have a bar (``null`` = no bar that session)."""

    dates: list[date]
    stock: list[float | None]
    iwm: list[float | None]
    bars: int
    iwm_bars: int
    note: str | None


class TickerLookupTapeOut(_Wire):
    figures: list[TickerLookupFigureOut]
    leading: list[str]
    coiling: bool
    coiling_detail: str | None
    headline_notes: list[str]
    gaps: list[TickerLookupMemberGapOut]
    members_read: int
    members_without: int
    figures_blank: int
    sparkline: TickerLookupSparklineOut | None
    window_end: date | None


class TickerLookupFilingOut(_Wire):
    """One filing-derived row in the shared reader's shape (``dashboards/filings.py``)."""

    kind: FilingKind
    form: str
    date: date
    headline: str
    detail: list[str]
    url: str | None
    accession: str | None
    tags: list[str]
    routine: bool
    fold: FoldGroup | None
    usd: float | None
    ratifier: str | None
    ratified_on: date | None


class TickerLookupFilingDayOut(_Wire):
    date: date
    rows: list[TickerLookupFilingOut]


class TickerLookupKindCountOut(_Wire):
    kind: FilingKind
    rows: int


class TickerLookupNotListedOut(_Wire):
    fold: FoldGroup | None
    rows: int
    older_than: date | None


class TickerLookupFilingsOut(_Wire):
    """The filing days (newest first) for the last ``read_days`` days; ``rows`` counts every row in the read, listed
    or not (catalyst facts are under Catalysts); ``not_listed`` = routine rows the per-group cap left out.
    """

    read_days: int
    days: list[TickerLookupFilingDayOut]
    rows: int
    by_kind: list[TickerLookupKindCountOut]
    routine: int
    not_listed: list[TickerLookupNotListedOut]


class TickerLookupCardCatalystOut(_Wire):
    """A thesis-level catalyst of a card the company counts on (``null`` ``when_date`` = undated)."""

    thesis_id: UUID
    thesis_name: str
    label: str
    kind: str | None
    when_date: date | None
    when_label: str | None
    passed: bool


class TickerLookupDecisionOut(_Wire):
    """A take / pass / close the operator logged on the company (or the void row striking one). ``voided`` = a void
    row names it (shown struck through); shares and price are the operator's own fills."""

    id: UUID
    thesis_id: UUID
    thesis_name: str
    action: str
    decision_date: date
    shares: float | None
    price: float | None
    reason: str | None
    voided: bool
    voids: UUID | None


class TickerLookupOut(_Wire):
    """The whole page for one company. ``clock``: ``record`` (the live read) or ``public`` (a past as-of: what was
    public by the end of that date). ``catalyst_facts`` = the company's own catalyst facts (also rows of the
    filing reader, listed here once); ``card_catalysts`` = the thesis-level catalysts of the cards it counts on.
    ``cache`` / ``compute_ms`` = whether the page came from the shared dashboard cache and how long it took when
    computed."""

    asof: date
    clock: Literal["record", "public"]
    identity: TickerLookupIdentityOut
    cards: TickerLookupCardsOut
    triggers: TickerLookupTriggersOut
    tape: TickerLookupTapeOut
    filings: TickerLookupFilingsOut
    catalyst_facts: list[TickerLookupFilingOut]
    card_catalysts: list[TickerLookupCardCatalystOut]
    decisions: list[TickerLookupDecisionOut]
    decisions_voided: int
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the routes --------------------------------------------------------------------------------------------


@router.get("/resolve", response_model=TickerLookupResolveOut)
def ticker_lookup_resolve(
    t: str = Query(..., min_length=1, max_length=24, description="the ticker to resolve"),
    former: bool = Query(
        False, description="also match a FORMER ticker (a company renamed in place), exactly"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> TickerLookupResolveOut:
    """Resolve a typed ticker to its company: the master's exact ticker (upper-cased), else, with ``former=true``,
    the company a former ticker belonged to. 404 when nothing matches; the suggestions as you type come from the
    Workbench's company search. Display only."""
    ticker = t.strip().upper()
    sid = master.ids_for_tickers(conn, [ticker]).get(ticker) if ticker else None
    matched: Literal["ticker", "former"] = "ticker"
    if sid is None and former and ticker:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT security_id FROM security_ticker_history WHERE tenant_id = %s AND old_ticker = %s "
                "ORDER BY seen_at DESC, id LIMIT 1",
                (DEFAULT_TENANT_ID, ticker),
            )
            row = cur.fetchone()
        sid, matched = (row["security_id"] if row else None), "former"
    sec = master.get(conn, sid) if sid is not None else None
    if sec is None or sid is None:
        raise HTTPException(status_code=404, detail=f"no company with ticker {ticker}")
    return TickerLookupResolveOut(
        security_id=sid, ticker=sec.ticker or ticker, name=sec.name, matched=matched
    )


@router.get("/{security_id}", response_model=TickerLookupOut)
def ticker_lookup(
    security_id: UUID,
    asof: date = Query(..., description="as-of date; nothing public after it is read"),
    conn: psycopg.Connection = Depends(get_conn),
) -> TickerLookupOut:
    """Everything the platform holds about one company at the as-of: its identity and approximate market cap, the
    counted cards it sits on and its part in each (the recorded call in effect), the triggers the recorded calls
    show on it over the last 365 days, its tape (the display members' readings), its filings and catalyst facts,
    the catalysts of its cards and the operator's logged decisions on it. A past as-of reads what was public by the
    end of that date (a roster row added or a filing filed after it never appears). Display only; 404 for an
    unknown id."""
    known_at, clock = serve_view(asof)
    if master.get(conn, security_id) is None:
        raise HTTPException(status_code=404, detail="no company with this id")
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two reads
    got = cached(
        conn,
        SLUG,
        (str(security_id), clock),
        asof,
        lambda: read_lookup(conn, security_id, asof, known_at, clock),
    )
    return TickerLookupOut.model_validate(
        {
            **asdict(got.value),
            "asof": asof,
            "clock": clock,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
