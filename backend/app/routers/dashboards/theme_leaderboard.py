"""The Theme Leaderboard dashboard's API (``docs/dashboards/theme-leaderboard.md``): one GET over every live card, at the
as-of dial. It READS the cards counted at the as-of with their rosters then (``dashboards/baskets.py``), the ``calls``
log (the one recorded card in effect per card), the counting names' display-member readings through the same
point-in-time view the Cockpit builds (``baskets.per_company``), one theme-breadth read per card and each card's
term-mention months, and hands everything to the pure compute in ``dashboards/theme_leaderboard.py``, where every
rule lives. The request's one ``serve_view(asof)`` pair is threaded into every read: the roster, the data-flag read,
IWM's bars, each company's view, each card's breadth view and the term-mention view. It never calls
``call_for_thesis``: the keys are the RECORD. The whole read is cached per (clock, as-of) in the shared dashboard cache
(``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7); the filters, sorts, percentiles and score are the page's. Its
wire models live here, not in ``app/schemas_api.py`` (rule 8)."""

from __future__ import annotations

from dataclasses import fields
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import (
    Membership,
    cards_as_of,
    counted_rows,
    display_members,
    flagged_in_view,
    memberships_of,
    per_company,
    price_flags,
)
from dashboards.cache import cached
from dashboards.peer_table import CompanyReads, cap_cell, metric_of
from dashboards.theme_leaderboard import (
    BENCHMARK,
    MEMBERS,
    MIN_PRICED,
    PRIOR_MONTHS,
    RECENT_MONTHS,
    SLUG,
    AdoptionGap,
    CardRead,
    CompanyTape,
    Counts,
    Leaderboard,
    Recorded,
    TapeGap,
    assemble,
    company_tape,
    names_of,
    recorded_of,
)
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.enums import State, TermTier
from domain.market_time import serve_view
from domain.splits import listed_from_row
from domain.thesis import TermSetEntry
from signals.base import PointInTimeData
from signals.display import insider_flow, theme_breadth, trailing_returns
from signals.display.base import DisplayMember, DisplaySignal
from signals.horizons import display_bounds

router = APIRouter(prefix="/theme-leaderboard")

# The calls-log read: a plain SELECT scoped to the counted cards' ids. "In effect at D" = the greatest (asof, seq)
# with asof <= D, one row per card (the What Changed and Hot Board read).
_IN_EFFECT_SQL = (
    "SELECT DISTINCT ON (thesis_id) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof DESC, seq DESC"
)


# --- the wire models --------------------------------------------------------------------------------------------


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class LeaderboardTapeOut(_Wire):
    """The tape part: ``median`` = the median 30-day return of the card's priced counting names, over ``k`` of them;
    ``value`` = that minus IWM's 30-day return, in percentage points. ``gap`` says why ``value`` is blank: ``thin``
    (fewer than ``min_priced`` priced names) or ``no_iwm`` (IWM has no 30-day return at the as-of).
    """

    value: float | None
    median: float | None
    k: int
    gap: TapeGap | None


class LeaderboardMedianOut(_Wire):
    """A median return (percent) over ``k`` priced names; ``null`` below ``min_priced``."""

    median: float | None
    k: int


class LeaderboardBreadthOut(_Wire):
    """The breadth part: ``pct`` = the share of the counting names above their 50-day SMA, ``above`` of ``counted``
    (``thin`` = names with too little history, shown but not counted). ``null`` = no name counted. ``above_200`` of
    ``n_200`` = the share above the 200-day SMA, read per name."""

    pct: float | None
    above: int
    counted: int
    thin: int
    above_200: int
    n_200: int


class LeaderboardKeysOut(_Wire):
    """The keys the card's recorded call in effect has turned: ``turned`` = 0, 1 or 2 (Managing counts as 2);
    ``null`` for all three = no recorded call by the as-of."""

    turned: int | None
    conviction: bool | None
    confirmation: bool | None


class LeaderboardAdoptionOut(_Wire):
    """The adoption part: ``slope_pct`` = documents mentioning the card's SIGNAL terms in the last three closed
    calendar months (``recent_*``) against the three before (``prior_*``), a signed percent; the spans are the
    months' first days. ``terms_counted`` of ``terms`` = the current SIGNAL terms whose six closed months are all
    stored (the slope rests on those). ``gap``: ``no_terms``, ``not_counted_yet`` (no term has all six months) or
    ``no_base`` (no documents in the earlier three)."""

    slope_pct: float | None
    recent_docs: int | None
    prior_docs: int | None
    recent_from: date | None
    recent_to: date | None
    prior_from: date | None
    prior_to: date | None
    terms: int
    terms_counted: int
    gap: AdoptionGap | None


class LeaderboardBuyerOut(_Wire):
    """A counting name with open-market buyers in the last 30 days: the card's ticker and the distinct buyers."""

    name: str
    buyers: int


class LeaderboardInsidersOut(_Wire):
    """The insiders part: distinct open-market buyers in 30 days summed across the counting names with a Form 4
    reading (``read`` of them); ``null`` = none has one. ``names`` = those with at least one buyer.
    """

    buyers: int | None
    read: int
    names: list[LeaderboardBuyerOut]


class LeaderboardCapMixOut(_Wire):
    """The counting names by approximate-cap tier at the as-of (the Peer Table's cap rule); ``blank`` = no cap."""

    nano: int
    micro: int
    small: int
    mid: int
    large: int
    blank: int


class LeaderboardCardOut(_Wire):
    """One counted card. ``state`` = the recorded card in effect's state (``null`` = no recorded call by the as-of);
    ``recorded_asof`` = its night (``reconstructed`` = written by the backfill). ``counting`` = distinct counting
    names (Context and Bench never), ``priced`` of them with a close, ``flagged`` left out of every price reading
    for a flagged price history. ``rs_leaders`` of ``rs_n`` = names whose relative strength is at a 13-week high.
    ``armed_names`` = the recorded card's armed members when it is Armed."""

    thesis_id: UUID
    thesis_name: str
    state: State | None
    recorded_asof: date | None
    reconstructed: bool
    counting: int
    priced: int
    flagged: int
    tape: LeaderboardTapeOut
    ret_7d: LeaderboardMedianOut
    breadth: LeaderboardBreadthOut
    keys: LeaderboardKeysOut
    adoption: LeaderboardAdoptionOut
    insiders: LeaderboardInsidersOut
    rs_leaders: int
    rs_n: int
    cap_mix: LeaderboardCapMixOut
    armed_names: int | None


class LeaderboardFlaggedOut(_Wire):
    """A counting company left out of every price reading: its data flags reach its price readings at the as-of
    (the reason the Cockpit grays its price cells). ``reason`` = the flags' labels."""

    security_id: UUID
    ticker: str
    reason: str


class LeaderboardOut(_Wire):
    """The whole page in one view: every counted card in the default order (the score among every card counted,
    highest first, a card with no score last). The five parts' values are the page's inputs: it recomputes the
    percentiles and the score for the cards it shows. The counts are the quiet line. ``iwm_ret_30d`` = IWM's own
    30-day return (``null`` = none at the as-of). ``cache`` / ``compute_ms`` = whether the heavy read was served from
    the shared dashboard cache and how long it took when it ran."""

    asof: date
    clock: Literal["record", "public"]
    benchmark: str
    iwm_ret_30d: float | None
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    basket_rows: int
    silent_rows_left_out: int
    unresolved_rows: int
    names: int
    names_flagged: int
    cards: list[LeaderboardCardOut]
    flagged: list[LeaderboardFlaggedOut]
    min_priced: int
    recent_months: int
    prior_months: int
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
            r["thesis_id"]: recorded_of(
                r["asof"], r["reconstructed"], CallCard.model_validate(r["card"])
            )
            for r in cur.fetchall()
        }


def _view(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    basket: list[UUID] | None = None,
) -> PointInTimeData:
    return PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        basket=basket,
        bounds=display_bounds(),
        clock=clock,
    )


def read_iwm(pit: PointInTimeData, asof: date) -> float | None:
    """IWM's own 30-trading-day return (percent) through the trailing-returns member's pure ``compute`` over the
    benchmark's bars, read exactly as the member reads a company's; ``None`` without a reading."""
    sig = trailing_returns.compute(
        pit.benchmark_prices(BENCHMARK, lookback_days=trailing_returns.LOOKBACK_DAYS), asof
    )
    m = metric_of(sig, "ret_30d")
    return None if m is None else m.value


def read_tape(
    pit: PointInTimeData,
    sid: UUID,
    asof: date,
    members: dict[str, DisplayMember],
    flag: str | None,
) -> CompanyTape:
    """One company's tape from the point-in-time view: the four display members and its approximate cap (the Peer
    Table's inputs and rule). A company whose data flags reach its price readings has its price members left
    unread (``flag`` names why); the insider member stays."""
    if flag is not None:
        insider = members[insider_flow.MEMBER_NAME](pit, sid, asof)
        return company_tape(
            {insider_flow.MEMBER_NAME: insider}, cap_cell(CompanyReads(flag=flag)), flag
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
    return company_tape({name: m(pit, sid, asof) for name, m in members.items()}, cap)


def read_breadths(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    card_names: dict[UUID, list[UUID]],
) -> dict[UUID, DisplaySignal | None]:
    """One theme-breadth read per card (``theme_breadth.breadth_for``, the read the Cockpit's basket header makes)
    over the card's counting names whose price history is not flagged: a view of just those names. A card with none
    has no reading."""
    out: dict[UUID, DisplaySignal | None] = {}
    for thesis_id, sids in card_names.items():
        out[thesis_id] = (
            theme_breadth.breadth_for(_view(conn, asof, known_at, clock, sids), sids)
            if sids
            else None
        )
    return out


def signal_terms(term_set: list[TermSetEntry]) -> tuple[str, ...]:
    """A card's current SIGNAL terms (BROAD is never counted), stripped, blanks dropped, de-duplicated
    case-insensitively with the first spelling kept: ``pipeline.term_mentions.signal_terms``' rule over the card's
    domain term set."""
    out: dict[str, str] = {}
    for entry in term_set:
        term = entry.term.strip()
        if entry.tier == TermTier.SIGNAL and term and term.casefold() not in out:
            out[term.casefold()] = term
    return tuple(out.values())


def read_board(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> Leaderboard:
    """The heavy read the cache holds: every counted card's row, off the counting names' tapes, one breadth read per
    card, the recorded card in effect and the card's term-mention months. Reads only; the request's ``known_at`` +
    ``clock`` ride into the roster read, the data-flag read, IWM's bars, every company's view, every card's breadth
    view and the term-mention view."""
    counted = cards_as_of(conn, asof, known_at)
    memberships = memberships_of(counted.cards)
    rows = counted_rows(memberships, include_silent=False)
    by_card: dict[UUID, list[Membership]] = {t.id: [] for t in counted.cards}
    for m in rows.rows:
        by_card[m.thesis_id].append(m)
    sids = sorted({m.security_id for m in rows.rows if m.security_id is not None}, key=str)

    flags = price_flags(conn, sids, asof, known_at=known_at, clock=clock)
    members = display_members(MEMBERS, "Theme Leaderboard")
    tapes = per_company(
        conn,
        sids,
        asof,
        known_at,
        clock,
        lambda pit, sid: read_tape(pit, sid, asof, members, flags.get(sid)),
    )
    iwm = read_iwm(_view(conn, asof, known_at, clock), asof)
    recorded = read_recorded(conn, [t.id for t in counted.cards], asof)
    breadths = read_breadths(
        conn,
        asof,
        known_at,
        clock,
        {tid: [sid for sid, _ in names_of(ms) if sid not in flags] for tid, ms in by_card.items()},
    )
    mentions_pit = _view(conn, asof, known_at, clock)
    terms = {t.id: signal_terms(t.term_set) for t in counted.cards}
    reads = [
        CardRead(
            thesis_id=t.id,
            name=t.name,
            members=tuple(by_card[t.id]),
            terms=terms[t.id],
            recorded=recorded.get(t.id),
            breadth=breadths.get(t.id),
            # a card with no SIGNAL term has nothing to read
            mentions=tuple(mentions_pit.term_mentions(t.id)) if terms[t.id] else (),
        )
        for t in counted.cards
    ]
    return assemble(
        asof=asof,
        clock=clock,
        reads=reads,
        tapes=tapes,
        flagged=flagged_in_view(rows.rows, flags),
        iwm_ret_30d=iwm,
        counts=Counts(
            live_cards=counted.live_count,
            cards_not_yet_created=counted.not_yet_created,
            roster_fallback_cards=counted.roster_fallback,
            basket_rows=rows.basket_rows,
            silent_rows_left_out=rows.silent_rows_left_out,
            unresolved_rows=rows.unresolved_rows,
        ),
    )


# --- the route --------------------------------------------------------------------------------------------------


@router.get("", response_model=LeaderboardOut)
def theme_leaderboard(
    asof: date = Query(
        ..., description="as-of date; only what was recorded and public by then is read"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> LeaderboardOut:
    """Every counted card with the five parts a stated composite ranks it by: the median 30-day return of its priced
    counting names minus IWM's, the share of those names above their 50-day SMA, the keys its recorded call has
    turned, the slope of its EDGAR term-mention documents (the last three closed months against the three before)
    and the distinct open-market insider buyers across those names in 30 days. Display only: it reads the calls log
    and never the live call; the percentiles and the score are computed on the page from the cards it shows. A past
    as-of reads what was public by the end of that date.
    """
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(conn, SLUG, (clock,), asof, lambda: read_board(conn, asof, known_at, clock))
    return LeaderboardOut.model_validate(
        {
            **{f.name: getattr(got.value, f.name) for f in fields(got.value)},
            "min_priced": MIN_PRICED,
            "recent_months": RECENT_MONTHS,
            "prior_months": PRIOR_MONTHS,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        },
    )
