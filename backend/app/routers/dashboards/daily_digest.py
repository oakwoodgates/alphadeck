"""The Daily Digest dashboard's API (``docs/dashboards/daily-digest.md``): one GET, the morning page at the as-of dial.
It READS the cards counted at the as-of with their rosters then (``dashboards/baskets.py``), the ``calls`` log (the two
nights and the card in effect on each: plain SELECTs, never ``call_for_thesis``), the thesis children and the operator's
position as of the view's instant (``decisions_repo.effective_position``, a read), the Form 4 filings through the
shared reader (``dashboards/filings.py``, narrowed to the Form 4 kinds), the closes of the names on a row and IWM's
through the point-in-time view (``baskets.per_company``) and the data flags that reach a price reading, all on the
request's one ``serve_view(asof)`` pair, and hands them to the pure compute in ``dashboards/daily_digest.py``, where
every rule lives (each section is a source page's own pure function). The whole payload is computed once per (clock,
as-of) in the shared dashboard cache (``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7); the tape's "show every
armed name" toggle is the page's own. Its wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime, timedelta
from typing import Literal
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
from dashboards.daily_digest import (
    BENCHMARK,
    SLUG,
    Bars,
    DailyDigest,
    DigestInputs,
    HeldPosition,
    InEffect,
    NameTape,
    Tier,
    Tone,
    daily_digest,
    read_start,
)
from dashboards.filings import FORM4_KINDS, read_filing_rows
from dashboards.hot_board import LogRow, SinceGap, candidates
from dashboards.insider_tracker import RoleClass
from dashboards.trigger_log import CardNights, Night, Scope
from dashboards.what_changed import Change, Section, security_ids
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.enums import Kind, State
from domain.market_time import countdown_origin, market_clock, serve_view
from domain.splits import listed_from_row
from domain.thesis import Thesis
from repositories import decisions_repo
from securities import master
from signals.base import PointInTimeData
from signals.horizons import display_bounds

router = APIRouter(prefix="/daily-digest")

# The calls-log reads: plain SELECTs, all scoped to the counted cards' ids. "In effect at D" = the greatest (asof, seq)
# with asof <= D, one row per card (What Changed's read, here with the run's ingest stamp); the newest night is
# bounded by the as-of; the nights of section 4 are the winning row (highest seq) per card and night in (P, N]; the
# walk's rows are every row on or before the as-of, without the card blob but for its armed members' ids.
_LATEST_SQL = "SELECT max(asof) AS latest FROM calls WHERE thesis_id = ANY(%s) AND asof <= %s"
_PREVIOUS_SQL = "SELECT max(asof) AS previous FROM calls WHERE thesis_id = ANY(%s) AND asof < %s"
_IN_EFFECT_SQL = (
    "SELECT DISTINCT ON (thesis_id) thesis_id, asof, seq, reconstructed, ingest_fresh, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof DESC, seq DESC"
)
_NIGHTS_SQL = (
    "SELECT DISTINCT ON (thesis_id, asof) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof > %s AND asof <= %s ORDER BY thesis_id, asof, seq DESC"
)
_RUN_SQL = (
    "SELECT thesis_id, asof, seq, reconstructed, state, "
    "COALESCE(jsonb_path_query_array(card, '$.armed_members[*].security_id'), '[]'::jsonb) AS armed_ids "
    "FROM calls WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof DESC, seq DESC"
)
# The fill's logged shares: the position model carries none, so the take row the position came from is read for
# them: the latest non-voided take of that day on that name, as the log stood at the view's instant (a void logged
# after it does not apply yet, a fill logged after it is not there).
_SHARES_SQL = (
    "SELECT d.shares FROM operator_decision d WHERE d.thesis_id = %(thesis)s AND d.tenant_id = %(tenant)s "
    "AND d.action = 'take' AND d.decision_date = %(day)s AND d.security_id IS NOT DISTINCT FROM %(sid)s "
    "AND d.recorded_at <= COALESCE(%(known)s::timestamptz, clock_timestamp()) "
    "AND NOT EXISTS (SELECT 1 FROM operator_decision v WHERE v.action = 'void' AND v.voids = d.id "
    "AND v.recorded_at <= COALESCE(%(known)s::timestamptz, clock_timestamp())) "
    "ORDER BY d.seq DESC LIMIT 1"
)


# --- the wire models --------------------------------------------------------------------------------------------


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class DigestNightsOut(_Wire):
    """The night pair. ``latest`` = N, the newest recorded night across the counted cards on or before the as-of;
    ``previous`` = P, the newest before it (``null`` = none). ``sessions_between`` = the Mon-Fri sessions strictly
    after P through N; ``days_after_night`` = the calendar days from N to the as-of (0 = the as-of is the night).
    """

    asof: date
    latest: date | None
    previous: date | None
    sessions_between: int | None
    days_after_night: int | None


class DigestSourceOut(_Wire):
    """One place a trigger points at: ``source`` = the kind of source as stored ("form4", "price", "xbrl", a form),
    ``ref`` = its reference, ``url`` = a link to it by the Evidence Ledger's rule (``null`` = none known).
    """

    source: str
    ref: str
    url: str | None


class DigestLineOut(_Wire):
    """One line of a card: What Changed's own item. ``ticker`` / ``security_id`` are set for a name or a trigger line;
    ``label`` = the key's own label, or the trigger's; ``on`` = the trigger's event date or the clock's date;
    ``previous_on`` = a re-dated trigger's earlier date; ``days`` = calendar days from the countdown origin (the action
    day, the session after the newest night, on a live view) to the entry window's end. ``tone``: ``accent`` (a card
    that entered Armed, a name newly armed, an entry window closing within 3 days) or ``warm`` (a card that left
    Armed, an exit-by reached); ``null`` on every other line. ``source`` = a trigger or risk line's first source
    (``null`` = it states none).
    """

    section: Section
    change: Change
    security_id: UUID | None
    ticker: str | None
    from_state: State | None
    to_state: State | None
    label: str | None
    signal_kind: Kind | None
    on: date | None
    previous_on: date | None
    days: int | None
    tone: Tone | None
    source: DigestSourceOut | None


class DigestCardOut(_Wire):
    """One card with a change. ``tier``: ``armed`` (an Armed or Managing card, now or at P), ``warming``,
    ``incubating``, in that order. ``headline`` = the state move (or "new card"); ``lines`` = the rest in What Changed's
    order. ``ingest_fresh`` = ``false`` when the run that wrote the card's row did not ingest every name cleanly;
    ``reconstructed`` = the row was written by ``pipeline.backfill``."""

    thesis_id: UUID
    thesis_name: str
    tier: Tier
    state: State
    previous_state: State | None
    reconstructed: bool
    ingest_fresh: bool | None
    headline: DigestLineOut | None
    lines: list[DigestLineOut]


class DigestLastNightOut(_Wire):
    """Section 1. ``no_baseline`` = there is no earlier night to compare (no card, no line)."""

    no_baseline: bool
    cards_with_change: int
    lines: int
    cards: list[DigestCardOut]


class DigestSinceFillOut(_Wire):
    """The held name's as-traded return from the fill to the last close on or before the as-of. ``basis``:
    ``logged_price`` (the operator's logged fill price) or ``first_close`` (the first close on or after the fill date);
    ``note`` says why the logged price was not used when one was logged. ``bench_pct`` = IWM over the same dates.
    ``status``: ``priced``; ``unpriced`` / ``flagged`` / ``no_name`` / ``no_fill`` with the ``reason``. Returns are
    percent."""

    status: Literal["priced", "unpriced", "flagged", "no_name", "no_fill"]
    basis: Literal["logged_price", "first_close"] | None
    basis_price: float | None
    basis_date: date | None
    last_date: date | None
    last_close: float | None
    ret_pct: float | None
    bench_pct: float | None
    reason: str | None
    note: str | None


class DigestLastSessionOut(_Wire):
    """The name's as-traded return over the calendar's last session (the close before it to its close) and IWM's over
    the same two sessions, in percent. ``unpriced`` / ``flagged`` carry the ``reason`` and keep the row.
    """

    status: Literal["priced", "unpriced", "flagged"]
    reason: str | None
    session: date | None
    previous_session: date | None
    ret_pct: float | None
    bench_pct: float | None


class DigestHoldOut(_Wire):
    """The card's exit-by as sessions: ``sessions_left`` = the Mon-Fri sessions after the as-of through it (0 on its
    last day), or ``passed_sessions`` = the calendar's sessions after it through the as-of."""

    exit_by: date | None
    sessions_left: int | None
    passed_sessions: int | None


class DigestPositionOut(_Wire):
    """One Managing card. ``has_position`` is false when the as-of thesis read carries no position (a fill logged after
    the as-of is none then); ``security_id`` / ``ticker`` null = the position names no company. ``fill_usd`` = shares
    times the logged price, only when both were logged. ``risk_signals`` = the card's distinct risk signals,
    ``risk_new`` = those section 1 carries as new last night. ``kill_criteria`` = the operator's own words.
    """

    thesis_id: UUID
    thesis_name: str
    has_position: bool
    security_id: UUID | None
    ticker: str | None
    opened_on: date | None
    logged_price: float | None
    shares: float | None
    fill_usd: float | None
    since_fill: DigestSinceFillOut
    last_session: DigestLastSessionOut
    hold: DigestHoldOut
    risk_signals: int
    risk_new: int
    kill_criteria: list[str]
    reconstructed: bool
    ingest_fresh: bool | None


class DigestDueItemOut(_Wire):
    """One dated line of this week: a catalyst, or a card's entry window or exit-by (the Catalyst Calendar's items).
    ``today`` = dated the countdown origin; ``days`` = calendar days from the origin (the action day, the session
    after the night, on a live view; the as-of on a past one); ``state`` = the card's state in effect.
    """

    item: Literal["catalyst", "entry_window", "exit_by"]
    on: date
    days: int
    today: bool
    label: str
    kind: str | None
    when_label: str | None
    thesis_id: UUID
    thesis_name: str
    state: str | None


class DigestDueOut(_Wire):
    """Section 3: the Calendar's ``this_week`` bucket (``start`` to ``end`` inclusive, through the coming Sunday).
    ``passed_since`` = the dated catalysts that passed after P and are not yet pruned (``null`` = no P).
    """

    start: date
    end: date
    items: list[DigestDueItemOut]
    catalysts: int
    deadlines: int
    passed_since: int | None
    passed_total: int
    cards_without_calls: int


class DigestBuyCardOut(_Wire):
    thesis_id: UUID
    thesis_name: str
    ticker: str


class DigestOtherBuyerOut(_Wire):
    name: str
    filed: date


class DigestClusterOut(_Wire):
    """Two or more distinct buyers filed within 30 days of the row's filing date; ``buyers`` counts the row's own."""

    buyers: int
    others: list[DigestOtherBuyerOut]


class DigestTriggerOut(_Wire):
    """A recorded insider trigger that carries the filing's accession, on one card: ``recorded_on`` = the first night
    in (P, N] the record shows it; ``live`` = it is on the card in effect (``null`` = that card could not be read).
    """

    thesis_id: UUID
    thesis_name: str
    recorded_on: date
    live: bool | None


class DigestBuyRowOut(_Wire):
    """One Form 4 (or applied 4/A) holding an open-market purchase, filed in (P, N]. ``filed`` = the filing date;
    ``trade_date`` / ``filed_days_after`` as Insider Buying words them; ``filer`` / ``role`` = the first reporting owner
    and the filing's own role words (``null`` = not stated); ``shares`` / ``price`` / ``usd`` = the code-P lines' totals
    (``null`` = not stated); ``cluster`` = see ``DigestClusterOut``; ``triggers`` = the recorded insider triggers
    carrying this accession (empty = not a trigger, which says nothing about why)."""

    key: str
    security_id: UUID
    ticker: str
    accession: str | None
    form: str
    url: str | None
    filed: date
    trade_date: date | None
    filed_days_after: int | None
    cards: list[DigestBuyCardOut]
    filer: str | None
    role: str | None
    role_classes: list[RoleClass]
    co_filers: int
    shares: float | None
    price: float | None
    usd: float | None
    tags: list[str]
    headline: str
    cluster: DigestClusterOut | None
    triggers: list[DigestTriggerOut]


class DigestBuyCountsOut(_Wire):
    """What section 4 rests on. ``filings_read`` = the Form 4s and 4/As filed in (P, N] = ``purchases`` +
    ``routine_folded`` + ``amendments_no_purchase``."""

    names_read: int
    filings_read: int
    purchases: int
    routine_folded: int
    amendments_no_purchase: int
    names_with_purchases: int
    filer_unresolved: int
    cluster_rows: int
    trigger_rows: int
    nights_read: int
    nights_unreadable: int
    silent_rows_left_out: int
    unresolved_rows: int


class DigestInsiderOut(_Wire):
    """Section 4. The span is the FILING dates in (``span_start``, ``span_end``] = (P, N]; ``no_baseline`` = there is
    no P, so no span (both dates null)."""

    no_baseline: bool
    span_start: date | None
    span_end: date | None
    rows: list[DigestBuyRowOut]
    counts: DigestBuyCountsOut


class DigestSinceArmOut(_Wire):
    """The name's as-traded return from the first close on or after the arm night to its last close on or before the
    as-of, and IWM's over the same dates, in percent; ``gap`` says why ``ret_pct`` is blank."""

    entry_date: date | None
    entry_close: float | None
    last_date: date | None
    last_close: float | None
    ret_pct: float | None
    iwm_pct: float | None
    gap: SinceGap | None


class DigestTapeRowOut(_Wire):
    """One (card, armed name). ``ticker`` = the card's roster ticker (the Cockpit's ``?name=`` key). ``sessions_since`` =
    the sessions after the arm night through the as-of; ``sessions_left`` = the Mon-Fri sessions left in an open entry
    window; ``closed_ago`` = the sessions since a closed one. ``cut``: ``rise`` / ``fall`` = among the largest rises /
    falls of the last session."""

    key: str
    thesis_id: UUID
    thesis_name: str
    security_id: UUID
    ticker: str
    arm_night: date | None
    arm_reconstructed: bool
    sessions_since: int | None
    arm_until: date | None
    sessions_left: int | None
    closed_ago: int | None
    last_session: DigestLastSessionOut
    since_arm: DigestSinceArmOut
    cut: Literal["rise", "fall"] | None


class DigestTapeCountsOut(_Wire):
    rows: int
    names: int
    names_priced: int
    names_unpriced: int
    names_flagged: int
    since_arm_priced: int
    since_arm_blank: int
    rises_shown: int
    falls_shown: int


class DigestTapeOut(_Wire):
    """Section 5: every armed row, the last session's return largest to smallest, the rows with no return after them.
    ``session`` / ``previous_session`` = the calendar's last two sessions; ``iwm_pct`` = IWM over them (percent).
    """

    session: date | None
    previous_session: date | None
    iwm_pct: float | None
    calendar_source: Literal["benchmarks", "companies", "none"]
    calendar_sessions: int
    rows: list[DigestTapeRowOut]
    counts: DigestTapeCountsOut


class DigestKeyTurnedOut(_Wire):
    thesis_id: UUID
    thesis_name: str
    labels: list[str]


class DigestFormingOut(_Wire):
    """Section 6: counts only. ``key_turned`` = the Warming cards whose key turned last night, by name."""

    warming: int
    incubating: int
    key_turned: list[DigestKeyTurnedOut]


class DigestCountsOut(_Wire):
    """The work shown on the quiet line: counts of what was read and listed, never a statistic over returns.
    ``cards_recorded_on_night`` + ``cards_carried`` + ``cards_no_call`` = ``cards_counted``."""

    cards_counted: int
    live_cards: int
    not_yet_created: int
    roster_fallback: int
    cards_recorded_on_night: int
    cards_carried: int
    cards_no_call: int
    cards_armed: int
    cards_warming: int
    cards_managing: int
    cards_incubating: int
    cards_ingest_not_fresh: int
    cards_reconstructed: int


class DigestFlaggedOut(_Wire):
    """A name on the tape or a position whose price history is flagged: kept on screen, never priced."""

    security_id: UUID
    ticker: str
    reason: str


class DailyDigestOut(_Wire):
    """The whole page at the as-of. ``clock``: ``record`` (the live read) or ``public`` (a past as-of: what was public
    by the end of that date). There is no aggregate over any row's return. ``cache`` / ``compute_ms`` = whether the read
    came from the shared dashboard cache and how long it took when computed."""

    asof: date
    clock: Literal["record", "public"]
    nights: DigestNightsOut
    last_night: DigestLastNightOut
    positions: list[DigestPositionOut]
    due: DigestDueOut
    insider: DigestInsiderOut
    tape: DigestTapeOut
    forming: DigestFormingOut
    counts: DigestCountsOut
    flagged: list[DigestFlaggedOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) ----------------------------------------------------


def read_nights_pair(
    conn: psycopg.Connection, ids: list[UUID], asof: date
) -> tuple[date | None, date | None]:
    """``(N, P)``: the newest recorded night across ``ids`` on or before the as-of, and the newest before it."""
    if not ids:
        return None, None
    with conn.cursor() as cur:
        cur.execute(_LATEST_SQL, (ids, asof))
        latest = cur.fetchone()["latest"]
        if latest is None:
            return None, None
        cur.execute(_PREVIOUS_SQL, (ids, latest))
        return latest, cur.fetchone()["previous"]


def read_in_effect(
    conn: psycopg.Connection, ids: list[UUID], on: date | None
) -> dict[UUID, InEffect]:
    """Each card's recorded row in effect on ``on`` (the greatest ``(asof, seq)`` with ``asof <= on``); a card with no
    row by then is absent."""
    if not ids or on is None:
        return {}
    with conn.cursor() as cur:
        cur.execute(_IN_EFFECT_SQL, (ids, on))
        return {
            r["thesis_id"]: InEffect(
                asof=r["asof"],
                seq=r["seq"],
                reconstructed=r["reconstructed"],
                ingest_fresh=r["ingest_fresh"],
                card=CallCard.model_validate(r["card"]),
            )
            for r in cur.fetchall()
        }


def read_span_nights(
    conn: psycopg.Connection,
    cards: list[tuple[UUID, str]],
    previous: date | None,
    latest: date | None,
) -> list[CardNights]:
    """Each counted card's recorded nights in (P, N] (each night's highest ``seq``). A stored card that no longer
    validates reads as ``card=None`` (counted, never read)."""
    ids = [tid for tid, _ in cards]
    nights: dict[UUID, list[Night]] = {tid: [] for tid in ids}
    if ids and previous is not None and latest is not None:
        with conn.cursor() as cur:
            cur.execute(_NIGHTS_SQL, (ids, previous, latest))
            for r in cur.fetchall():
                try:
                    card: CallCard | None = CallCard.model_validate(r["card"])
                except ValidationError:
                    card = None
                nights[r["thesis_id"]].append(Night(r["asof"], r["seq"], r["reconstructed"], card))
    return [CardNights(tid, name, None, tuple(nights[tid])) for tid, name in cards]


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


def read_held(
    conn: psycopg.Connection, thesis: Thesis, asof: date, known_at: datetime | None
) -> HeldPosition | None:
    """The card's effective position as the as-of thesis read carries it: the decision log's take rows as they stood at
    the view's instant (``known_at``: a fill logged after it is no position then), the seed-era columns as the
    fallback (``decisions_repo.effective_position``, a read). Plus the fill's logged shares, which the position model
    does not carry."""
    pos = decisions_repo.effective_position(conn, thesis, asof=asof, known_at=known_at)
    if pos is None:
        return None
    shares = None
    if pos.opened_on is not None:
        with conn.cursor() as cur:
            cur.execute(
                _SHARES_SQL,
                {
                    "thesis": thesis.id,
                    "tenant": thesis.tenant_id or DEFAULT_TENANT_ID,
                    "day": pos.opened_on,
                    "sid": pos.security_id,
                    "known": known_at,
                },
            )
            row = cur.fetchone()
        if row is not None and row["shares"] is not None:
            shares = float(row["shares"])
    return HeldPosition(pos.security_id, pos.opened_on, pos.entry_price, shares)


def _bars(rows: list[dict]) -> Bars:
    """(date, close) ascending, positive closes only: a non-positive close is not a bar."""
    return tuple(
        (b["d"], float(b["close"]))
        for b in rows
        if b.get("close") is not None and float(b["close"]) > 0
    )


def read_name_tape(
    pit: PointInTimeData, sid: UUID, want_splits: bool, flag: str | None
) -> NameTape:
    """One company's closes through the point-in-time view (the view's price scale), and its split list when a
    position needs it. A company whose data flags reach its price readings reads no bar."""
    if flag is not None:
        return NameTape()
    return NameTape(
        bars=_bars([b for b in pit.price_history(sid) if b.get("close") is not None]),
        splits=(tuple(listed_from_row(r) for r in pit.split_events(sid)) if want_splits else ()),
    )


def read_digest(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    origin: date | None = None,
) -> DailyDigest:
    """The cached read: the counted cards and their rosters at the as-of, the night pair and the cards in effect on
    each, the Managing cards' positions, the Form 4 filings filed since P, the armed names' and the held names'
    closes, IWM's, and the data flags of the names on a row; then the pure compute. Reads only; the request's
    ``known_at`` + ``clock`` ride into every one of them."""
    got = cards_as_of(conn, asof, known_at)
    theses = got.cards
    names = {t.id: t.name for t in theses}
    ids = list(names)

    latest, previous = read_nights_pair(conn, ids, asof)
    now = read_in_effect(conn, ids, latest)
    baseline = read_in_effect(conn, ids, previous)

    # section 2: the Managing cards' positions as of the view's instant
    positions: dict[UUID, HeldPosition] = {}
    for t in theses:
        rec = now.get(t.id)
        if rec is not None and rec.card.state == State.MANAGING:
            held = read_held(conn, t, asof, known_at)
            if held is not None:
                positions[t.id] = held

    # section 5: the armed rows of the recorded cards in effect (closes only: no display member runs)
    armed = [
        c
        for c in candidates(names, {tid: r.recorded() for tid, r in now.items()})
        if c.kind == "armed"
    ]
    runs = read_runs(conn, sorted({c.thesis_id for c in armed}, key=str), asof)
    held_sids = {p.security_id for p in positions.values() if p.security_id is not None}
    tape_sids = sorted({c.security_id for c in armed} | held_sids, key=str)

    all_memberships = memberships_of(theses)
    roster_tickers: dict[tuple[UUID, UUID], str] = {}
    for m in all_memberships:
        if m.security_id is not None:
            roster_tickers.setdefault((m.thesis_id, m.security_id), m.ticker)

    window_days = display_bounds().get("fact_price_eod")
    window_start = None if window_days is None else asof - timedelta(days=window_days)
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        bounds=display_bounds(),
        clock=clock,
    )
    iwm_bars = _bars(pit.benchmark_prices(BENCHMARK, lookback_days=window_days))
    flagged = price_flags(conn, tape_sids, asof, known_at=known_at, clock=clock)
    tapes = per_company(
        conn,
        tape_sids,
        asof,
        known_at,
        clock,
        lambda p, sid: read_name_tape(p, sid, sid in held_sids, flagged.get(sid)),
    )

    # section 4: the voting names' Form 4 filings from P less the cluster window (Context and Bench never count)
    voting = counted_rows(all_memberships, False)
    filings = {}
    nights: list[CardNights] = [CardNights(t.id, t.name, None, ()) for t in theses]
    if previous is not None and latest is not None:
        voting_sids = resolved_ids(voting.rows)
        shown = {sid: display_ticker(cards) for sid, cards in companies_in(voting.rows)}
        filings = read_filing_rows(
            conn,
            voting_sids,
            asof,
            known_at,
            clock,
            days=(asof - read_start(previous)).days,
            tickers=shown,
            kinds=FORM4_KINDS,
        )
        nights = read_span_nights(conn, [(t.id, t.name) for t in theses], previous, latest)

    section_sids = security_ids([r.recorded_call() for r in [*now.values(), *baseline.values()]])
    tickers = master.tickers_for(conn, section_sids | set(tape_sids))
    ciks = master.ciks_for(
        conn, section_sids
    )  # the issuer CIK a trigger's source link is built from
    return daily_digest(
        DigestInputs(
            asof=asof,
            clock=clock,
            scope=Scope(got.live_count, got.not_yet_created, got.roster_fallback),
            theses=theses,
            latest=latest,
            previous=previous,
            now=now,
            baseline=baseline,
            tickers=tickers,
            positions=positions,
            roster_tickers=roster_tickers,
            memberships=voting.rows,
            silent_rows_left_out=voting.silent_rows_left_out,
            unresolved_rows=voting.unresolved_rows,
            filings=filings,
            nights=nights,
            runs=runs,
            tapes=tapes,
            iwm_bars=iwm_bars,
            flagged=flagged,
            window_start=window_start,
            ciks=ciks,
            origin=origin,
        )
    )


# --- the route --------------------------------------------------------------------------------------------------


@router.get("", response_model=DailyDigestOut)
def daily_digest_page(
    asof: date = Query(
        ..., description="as-of date; only what was recorded and public by then is read"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> DailyDigestOut:
    """The morning page at the as-of: what the newest recorded night changed against the one before, the Managing
    cards' positions, what is due this week, the insider buys filed since the earlier night, the armed names' last
    session and the count of what is forming. Every section is a cut of an existing page, read by that page's own
    rules. Display only: it reads the calls log and never the live call, notifies nothing and ranks nothing by promise.
    A past as-of reads the nights recorded on or before it and what was public by the end of that date; a fill logged
    after it is not a position then.
    """
    mclock = market_clock()
    known_at, clock = serve_view(asof, night=mclock.night)
    # F2: the countdowns (due this week, an entry window closing) count from the action day on a live view; the
    # origin is part of the key because it moves when the night does
    origin = countdown_origin(asof, mclock)
    got = cached(
        conn,
        SLUG,
        (clock, origin),
        asof,
        lambda: read_digest(conn, asof, known_at, clock, origin),
    )
    return DailyDigestOut.model_validate(
        {**_payload(got.value), "cache": got.status, "compute_ms": got.compute_ms}
    )


def _payload(digest: DailyDigest) -> dict:
    return {f.name: getattr(digest, f.name) for f in fields(digest)}
