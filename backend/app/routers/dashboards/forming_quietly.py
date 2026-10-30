"""The Forming Quietly dashboard's API (``docs/dashboards/forming-quietly.md``): one GET over every live card, at the
as-of dial. It READS the cards counted at the as-of with their rosters then (``dashboards/baskets.py``), the ``calls``
log (the one recorded card in effect per card, and the rows the run walk reads for the Warming and Incubating ones),
the master's tickers and issuer CIKs, then for the Warming and Incubating cards' counting names only: the three display
members' readings through the same point-in-time view the Cockpit builds (``baskets.per_company``), one
``theme_breadth.breadth_for`` read per card and the card's EDGAR term-mention months
(``PointInTimeData.term_mentions``). The request's one ``serve_view(asof)`` pair is threaded into every read: the
roster, the data-flag read, the members' views, the breadth views and the term-month view. It never calls
``call_for_thesis``: the rows are the RECORD. The whole read is cached per (clock, as-of) in the shared dashboard cache
(``dashboards/cache.py``, ``docs/DASHBOARDS.md`` rule 7); the Incubating toggle, filters and sorts are the page's. Its
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
from dashboards.filings import source_url
from dashboards.forming_quietly import (
    MEMBERS,
    SLUG,
    FormingQuietly,
    FormingRow,
    LogRow,
    Reading,
    Recorded,
    RowState,
    adoption_slope,
    build_row,
    card_state_counts,
    forming,
    quiet_of,
    reading_of,
    row_cards,
)
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.enums import Grade, Kind, State, TermTier
from domain.market_time import serve_view
from domain.thesis import Thesis
from securities import master
from signals.base import PointInTimeData
from signals.display import theme_breadth
from signals.display.base import DisplayMember, DisplaySignal
from signals.horizons import display_bounds

router = APIRouter(prefix="/forming-quietly")

# The calls-log reads: plain SELECTs, all scoped to the counted cards' ids. "In effect at D" = the greatest
# (asof, seq) with asof <= D, one row per card (the What Changed read); the run walk's rows are every row on or
# before D without the card blob.
_IN_EFFECT_SQL = (
    "SELECT DISTINCT ON (thesis_id) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof DESC, seq DESC"
)
_RUN_SQL = (
    "SELECT thesis_id, asof, seq, reconstructed, state FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof DESC, seq DESC"
)


# --- the wire models --------------------------------------------------------------------------------------------


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class FormingSourceOut(_Wire):
    """One place a trigger points at: ``source`` = the kind of source as stored ("form4", "price", "xbrl", a form),
    ``ref`` = its reference, ``url`` = a link to it by the Evidence Ledger's rule (``null`` = none known).
    """

    source: str
    ref: str
    url: str | None


class FormingTriggerOut(_Wire):
    """A fired entry trigger on the recorded card. ``event_date`` = its FIRE date (the market event: an insider
    trade date, a breakout bar), not the night the platform recorded it."""

    label: str
    kind: Kind
    grade: Grade | None
    event_date: date | None
    sources: list[FormingSourceOut]


class FormingNameOut(_Wire):
    """A name behind the key that turned: its ticker on the card (the Cockpit's ``?name=`` key) and every trigger
    that fired on it."""

    security_id: UUID
    ticker: str
    triggers: list[FormingTriggerOut]


class FormingWatchOut(_Wire):
    """A confirmation-only member of the recorded call (moving, no conviction yet)."""

    security_id: UUID
    ticker: str


class FormingRiskOut(_Wire):
    """One risk signal on the recorded card and the name it fired on."""

    label: str
    kind: Kind
    security_id: UUID
    ticker: str


class FormingKeyOut(_Wire):
    """A key as the call recorded it: ``label`` and ``detail`` are the assembler's own words."""

    turned: bool
    label: str
    detail: str | None


class FormingFlaggedOut(_Wire):
    """A company left out of every reading for a flagged price history at the as-of: counted, never dropped."""

    security_id: UUID
    ticker: str
    reason: str


class FormingInsiderOut(_Wire):
    """A name with open-market insider buying in the last 30 days: the buys, the distinct buyers and whether that is
    a cluster (at least two distinct buyers)."""

    security_id: UUID
    ticker: str
    buys_30d: int
    buyers_30d: int
    cluster: bool


class FormingCoilingOut(_Wire):
    """A name whose base is coiling (the volatility-contraction member's headline); ``contraction`` = the newest
    segment's depth over the oldest's."""

    security_id: UUID
    ticker: str
    contraction: float | None


class FormingLeadingOut(_Wire):
    """A name whose relative strength is at a fresh 13-week high; ``vs`` = the benchmarks it leads."""

    security_id: UUID
    ticker: str
    vs: list[str]


class FormingBreadthOut(_Wire):
    """The card's theme breadth: ``above`` of ``counted`` names at or above their 50-day average (``pct`` of them),
    ``delta_pts`` against 20 trading days ago, ``thin`` names with too little history not counted. ``thrust`` is the
    member's own headline."""

    above: int | None
    counted: int
    thin: int
    pct: float | None
    delta_pts: float | None
    thrust: bool


class FormingQuietOut(_Wire):
    """One card's quiet signals over its counting roster (Context and Bench never count). ``names`` = the counting
    companies, ``read`` = those read (a flagged price history is counted in ``flagged``, never read),
    ``unresolved`` = roster rows with no resolved security. ``no_form4`` / ``coiling_no_reading`` /
    ``leading_no_reading`` = read names a member gave no reading for. ``lit`` = insider buying, coiling, leading or
    a breadth thrust is on."""

    names: int
    read: int
    unresolved: int
    flagged: list[FormingFlaggedOut]
    insider: list[FormingInsiderOut]
    no_form4: int
    coiling: list[FormingCoilingOut]
    coiling_no_reading: int
    leading: list[FormingLeadingOut]
    leading_no_reading: int
    breadth: FormingBreadthOut | None
    lit: bool


class FormingAdoptionOut(_Wire):
    """The card's EDGAR term-mention slope: ``recent`` documents (the three closed months ``recent_from`` to
    ``recent_to``, as first days) against ``prior`` (the three before), summed over the SIGNAL terms that have all six
    closed months stored (``terms_counted`` of ``terms_signal``; ``terms_partial`` have some, ``terms_unstored``
    none). ``counted`` false = not counted yet. ``percent`` is ``null`` without a nonzero prior span.
    ``months_stored`` = the six closed months any signal term has a row for; ``latest_partial`` = a counted term's
    latest closed month was read on or before its last day."""

    counted: bool
    percent: float | None
    recent: int | None
    prior: int | None
    recent_from: date | None
    recent_to: date | None
    prior_from: date | None
    prior_to: date | None
    terms_signal: int
    terms_counted: int
    terms_partial: int
    terms_unstored: int
    months_stored: int
    latest_partial: bool


class FormingRowOut(_Wire):
    """One card the record shows Warming or Incubating at the as-of. ``recorded_on`` = the night of the recorded call
    in effect; ``run_start`` = the first night of its current run of that state, ``days_in_state`` the calendar days
    from there to the as-of (``run_reconstructed`` = that night was rebuilt by ``pipeline.backfill``). The keys,
    ``missing`` lines and ``risks`` are the recorded call's own; ``names`` = the names behind the keys that turned;
    ``watch`` = its confirmation-only members."""

    key: str
    thesis_id: UUID
    thesis_name: str
    state: RowState
    recorded_on: date
    run_start: date | None
    run_nights: int
    days_in_state: int | None
    run_reconstructed: bool
    key_conviction: FormingKeyOut
    key_confirmation: FormingKeyOut
    names: list[FormingNameOut]
    missing: list[str]
    watch: list[FormingWatchOut]
    risks: list[FormingRiskOut]
    quiet: FormingQuietOut
    adoption: FormingAdoptionOut


class FormingQuietlyOut(_Wire):
    """Every Warming and Incubating card of the recorded calls in effect at the as-of, in the default order (the page
    derives the Incubating toggle, filters and sorts from this one read), and the counts its quiet line shows.
    ``cards_*`` = the counted cards by their recorded state (``cards_no_call`` = nothing recorded by then;
    ``cards_incubating_lit`` = Incubating cards with a quiet signal on); ``cards_with_term_months`` /
    ``cards_without_term_months`` = row cards with and without a stored signal-term month in the six closed months;
    ``names_read`` / ``names_flagged`` = distinct companies read and left out for a flagged price history;
    ``members`` = the display members run. ``clock``: ``record`` (the live read) or ``public`` (a past as-of: what
    was public by the end of that date). ``cache`` / ``compute_ms`` = whether the read came from the shared dashboard
    cache and how long it took when computed."""

    asof: date
    clock: Literal["record", "public"]
    live_cards: int
    cards_counted: int
    cards_not_yet_created: int
    roster_fallback_cards: int
    cards_armed: int
    cards_warming: int
    cards_managing: int
    cards_incubating: int
    cards_incubating_lit: int
    cards_no_call: int
    cards_with_term_months: int
    cards_without_term_months: int
    names_read: int
    names_flagged: int
    members: list[str]
    rows: list[FormingRowOut]
    flagged: list[FormingFlaggedOut]
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
    """Every log row on or before the as-of for the cards, newest first, narrowed to what the run walk reads."""
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
                )
            )
    return out


def read_reading(
    pit: PointInTimeData, sid: UUID, asof: date, members: dict[str, DisplayMember]
) -> Reading:
    """One company's reading from the point-in-time view: the three display members (a company whose data flags
    reach its price readings is never handed here: it is not read at all)."""
    return reading_of({name: m(pit, sid, asof) for name, m in members.items()})


def _pit(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
    basket: list[UUID] | None = None,
) -> PointInTimeData:
    """The point-in-time view the Cockpit's display signals build (``display_bounds()``, the request's pair)."""
    return PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        tenant_id=DEFAULT_TENANT_ID,
        basket=basket,
        bounds=display_bounds(),
        clock=clock,
    )


def _signal_terms(thesis: Thesis) -> list[str]:
    """The card's current SIGNAL terms (the operator's seeds; BROAD terms are never counted)."""
    return [e.term for e in thesis.term_set if e.tier == TermTier.SIGNAL]


def _counting_names(
    memberships: list[Membership], thesis_id: UUID
) -> tuple[list[tuple[UUID, str]], int, list[Membership]]:
    """A card's counting companies, each once, as ``(security_id, ticker on the card)``, in roster order; the number
    of roster rows with no resolved security; and the counted rows themselves (Context and Bench left out).
    """
    counted = counted_rows(
        [m for m in memberships if m.thesis_id == thesis_id], include_silent=False
    )
    seen: dict[UUID, str] = {}
    for m in counted.rows:
        assert m.security_id is not None
        seen.setdefault(m.security_id, m.ticker)
    return list(seen.items()), counted.unresolved_rows, counted.rows


def read_forming(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> FormingQuietly:
    """The heavy read the cache holds: a row per Warming and Incubating card of the recorded calls in effect, with
    its keys, missing lines, quiet signals and adoption slope. Reads only; the request's ``known_at`` + ``clock``
    ride into the roster read, the data-flag read, every company's view, every card's breadth view and the
    term-month view."""
    counted = cards_as_of(conn, asof, known_at)
    by_id = {t.id: t for t in counted.cards}
    names = {tid: t.name for tid, t in by_id.items()}
    recorded = read_recorded(conn, list(names), asof)
    ids = row_cards(names, recorded)
    runs = read_runs(conn, ids, asof)

    memberships = list(memberships_of(counted.cards))
    roster_tickers: dict[tuple[UUID, UUID], str] = {}
    for m in memberships:
        if m.security_id is not None:
            roster_tickers.setdefault((m.thesis_id, m.security_id), m.ticker)
    per_card = {tid: _counting_names(memberships, tid) for tid in ids}
    sids = sorted({sid for tid in ids for sid, _ in per_card[tid][0]}, key=str)

    flags = price_flags(conn, sids, asof, known_at=known_at, clock=clock)
    members = display_members(MEMBERS, "Forming Quietly")
    unflagged = [s for s in sids if s not in flags]
    read = per_company(
        conn,
        unflagged,
        asof,
        known_at,
        clock,
        lambda p, sid: read_reading(p, sid, asof, members),
    )
    readings: dict[UUID, Reading] = {**read, **{s: Reading(flag=flags[s]) for s in flags}}

    # every name a row shows by ticker: the card's roster ticker (the Cockpit's ?name= key), else the master's,
    # else the id
    shown_sids = {s for tid in ids for s in _shown(recorded[tid].card)} | set(sids)
    master_tickers = master.tickers_for(conn, shown_sids)
    ciks = master.ciks_for(
        conn, {t.security_id for tid in ids for t in recorded[tid].card.triggers_fired}
    )

    terms_pit = _pit(conn, asof, known_at, clock)
    rows: list[FormingRow] = []
    for tid in ids:
        thesis = by_id[tid]
        card_names, unresolved, _ = per_card[tid]
        card_sids = [s for s, _ in card_names if s not in flags]
        breadth: DisplaySignal | None = (
            theme_breadth.breadth_for(_pit(conn, asof, known_at, clock, card_sids), card_sids)
            if card_sids
            else None
        )

        def ticker_of(sid: UUID, tid: UUID = tid) -> str:
            return roster_tickers.get((tid, sid)) or master_tickers.get(sid) or str(sid)

        rows.append(
            build_row(
                tid,
                thesis.name,
                recorded[tid],
                runs.get(tid, []),
                quiet_of(card_names, readings, breadth, unresolved=unresolved),
                adoption_slope(terms_pit.term_mentions(tid), _signal_terms(thesis), asof),
                asof=asof,
                ticker_of=ticker_of,
                ciks=ciks,
                url_of=source_url,
            )
        )
    counting_rows = [m for tid in ids for m in per_card[tid][2]]
    return forming(
        rows,
        asof=asof,
        clock=clock,
        counts=card_state_counts(names, recorded),
        live_cards=counted.live_count,
        cards_counted=len(counted.cards),
        cards_not_yet_created=counted.not_yet_created,
        roster_fallback_cards=counted.roster_fallback,
        names_read=len(unflagged),
        flagged=flagged_in_view(counting_rows, flags),
    )


def _shown(card: CallCard) -> set[UUID]:
    """The securities a recorded card names outside its roster read (triggers, watch members, risk signals)."""
    out = {t.security_id for t in card.triggers_fired} | {t.security_id for t in card.risk_signals}
    return out | {m.security_id for m in card.watch_members}


# --- the route --------------------------------------------------------------------------------------------------


@router.get("", response_model=FormingQuietlyOut)
def forming_quietly(
    asof: date = Query(
        ..., description="as-of date; only what was recorded and public by then is read"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> FormingQuietlyOut:
    """Every card the recorded calls in effect at the as-of show Warming or Incubating, in the default order: the
    call's own keys and missing lines, the names behind the keys that turned, the quiet signals on the card's
    counting roster (insider buying, coiling, leading, breadth) and the card's EDGAR term-mention slope. Armed and
    Managing cards are counted, never rows. Display only: it reads the calls log and never the live call; the
    Incubating toggle, filters and sorts are the page's. A past as-of reads what was public by the end of that date.
    """
    known_at, clock = serve_view(asof)
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(conn, SLUG, (clock,), asof, lambda: read_forming(conn, asof, known_at, clock))
    return FormingQuietlyOut.model_validate(
        {**_payload(got.value), "cache": got.status, "compute_ms": got.compute_ms},
    )


def _payload(board: FormingQuietly) -> dict:
    return {f.name: getattr(board, f.name) for f in fields(board)}
