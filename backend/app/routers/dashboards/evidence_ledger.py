"""The Evidence Ledger dashboard's API (``docs/dashboards/evidence-ledger.md``): one card's provenance as a dated
list, at the as-of dial. It READS the cards that existed by the as-of (``dashboards/baskets.py``), the picked card's
roster as of the as-of (``thesis_repo.get_asof``), the recorded call in effect from the ``calls`` log (a plain
SELECT, What Changed's "in effect at D" rule; never ``call_for_thesis``), the ratified and stored facts through the
shared filing reader (``dashboards/filings.py``: both clocks on the request's one ``serve_view(asof)`` pair) and the
roster snapshots that date a context role, then hands them to the pure compute in ``dashboards/evidence_ledger.py``.
The heavy read is computed once per (card, clock, as-of) in the shared dashboard cache (``dashboards/cache.py``,
``docs/DASHBOARDS.md`` rule 7); the card's own entries (catalysts, kill criteria, evidence links) load live on every
request, because they are not snapshotted. Its wire models live here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, ValidationError

from app.deps import get_conn
from dashboards.baskets import (  # noqa: F401  (CardsRead: the picker's read; tests stub read_cards)
    CardsRead,
    memberships_of,
    picker_cards,
    resolved_ids,
)
from dashboards.cache import Cached, cached
from dashboards.evidence_ledger import (
    READ_DAYS,
    Authority,
    LedgerBase,
    LedgerKind,
    RecordedNight,
    RosterSnapshot,
    card_entries,
    evidence_ledger,
    ledger_base,
)
from dashboards.filings import read_filing_rows
from domain.call import CallCard
from domain.enums import ContextRole
from domain.market_time import serve_view
from repositories import thesis_repo
from securities import master

router = APIRouter(prefix="/evidence-ledger")

SLUG = "evidence-ledger"

# The recorded call in effect: the greatest (asof, seq) with asof <= D, the What Changed dashboard's rule. The log
# appends only on change, so the last row on or before D is what stood that night. A plain SELECT, scoped to one
# card.
_RECORDED_SQL = (
    "SELECT asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = %s AND asof <= %s ORDER BY asof DESC, seq DESC LIMIT 1"
)
# The roster snapshots taken by the as-of (the SAME predicate as ``thesis_repo.get_asof``), narrowed in SQL to the
# role-tagged members: ``_member_to_snapshot`` writes ``context_role`` only when it is set.
_ROLE_SNAPSHOTS_SQL = (
    "SELECT taken_at, COALESCE(("
    "SELECT jsonb_agg(jsonb_build_object('security_id', e->>'security_id', 'context_role', e->>'context_role')) "
    "FROM jsonb_array_elements(members) AS e WHERE e ? 'context_role'"
    "), '[]'::jsonb) AS members "
    "FROM basket_snapshot WHERE thesis_id = %s AND taken_at <= COALESCE(%s, now()) "
    "ORDER BY taken_at, id"
)


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class EvidenceLedgerCardOut(_Wire):
    """A card in the picker: its name and its real links today (Context and Bench not counted)."""

    thesis_id: UUID
    name: str
    links: int


class EvidenceLedgerCardsOut(_Wire):
    """The picker: the cards counted at the as-of (the live cards that existed by then), A→Z. ``cache`` /
    ``compute_ms`` = whether the list came from the shared dashboard cache and how long it took when computed.
    """

    asof: date
    live_cards: int
    not_yet_created: int
    cards: list[EvidenceLedgerCardOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


class EvidenceLedgerSourceOut(_Wire):
    """A place a row points at. ``source`` = the kind as stored (``form4``, ``price``, ``xbrl``, a form), ``ref``
    = its reference (an accession, a price reference), ``url`` = a link (``null`` = none known), ``detail`` = the
    computation's inputs as stored, one ``key: value`` line each."""

    source: str
    ref: str | None
    url: str | None
    detail: list[str]


class EvidenceLedgerRowOut(_Wire):
    """One piece of evidence. ``authority`` = WHO put it there, never a judgment of it: ``operator`` (a person
    ratified or set it), ``system`` (a computation on public data), ``automatic`` (an ingest leg stored it, nobody
    ratified it), ``card`` (an entry the operator maintains on the card). ``kind``: ``trigger`` | ``risk_signal``
    (the recorded card's) | ``ratified`` (a ratified or stored fact) | ``catalyst_fact`` | ``card_catalyst`` |
    ``kill`` | ``evidence`` | ``role``. ``date`` = the day it counts under (``null`` = standing). ``form`` = the
    row's own kind word. ``silent`` = Context / Bench when its company sits there. ``role`` = the context role
    (a role row). ``by`` / ``on`` = who ratified or set it and the day. ``edgar`` = a source links to EDGAR.
    ``reconstructed`` = from a night ``pipeline.backfill`` wrote (reported, never scored)."""

    key: str
    authority: Authority
    kind: LedgerKind
    label: str
    date: date | None
    security_id: UUID | None
    ticker: str | None
    form: str | None
    silent: str | None
    role: ContextRole | None
    by: str | None
    on: date | None
    sources: list[EvidenceLedgerSourceOut]
    detail: list[str]
    edgar: bool
    reconstructed: bool


class EvidenceLedgerDayOut(_Wire):
    date: date
    rows: list[EvidenceLedgerRowOut]


class EvidenceLedgerCountOut(_Wire):
    key: str
    rows: int


class EvidenceLedgerTalliesOut(_Wire):
    """Every row, whatever the page's filters. ``trace_*`` = how the fired signals (triggers and risk signals)
    trace: an EDGAR accession, a price reference only, another source, or no source stated. ``facts_ratified`` /
    ``facts_stored`` = the fact rows a person ratified / nobody did."""

    rows: int
    by_authority: list[EvidenceLedgerCountOut]
    by_kind: list[EvidenceLedgerCountOut]
    signals: int
    trace_accession: int
    trace_price: int
    trace_other: int
    trace_none: int
    facts_ratified: int
    facts_stored: int
    catalysts_dated: int
    catalysts_undated: int
    roles_dated: int
    roles_undated: int


class EvidenceLedgerNightOut(_Wire):
    """The recorded call the triggers come from: the night it was recorded for and whether it was reconstructed."""

    asof: date
    reconstructed: bool


class EvidenceLedgerNameOut(_Wire):
    """A roster company (each once) with its rows; a name with none is still listed. ``key`` = its
    ``security_id`` (``ticker:<T>`` for a row with no resolved security)."""

    key: str
    ticker: str
    security_id: UUID | None
    role: ContextRole | None
    silent: str | None
    rows: int


class EvidenceLedgerOut(_Wire):
    """One card's ledger at the as-of. ``clock``: ``record`` (the live read) or ``public`` (a past as-of: what was
    public by the end of that date). ``night`` = the recorded call the triggers come from (``null`` = none
    recorded by the as-of); ``night_unreadable`` = a row was found but its card did not read. ``standing`` = the
    rows with no date; ``groups`` = the dated rows, newest day first. ``facts_days`` = how far back the stored
    facts are read. ``roster_fallback`` = no roster snapshot by the as-of: today's roster stands in.
    ``unresolved`` = tickers of roster rows with no resolved security (they can carry no fact: counted, never
    listed as one). ``cache`` / ``compute_ms`` = whether the heavy read came from the shared dashboard cache and
    how long it took when computed (the card's own entries are read live either way)."""

    asof: date
    thesis_id: UUID
    thesis_name: str
    clock: Literal["record", "public"]
    roster_fallback: bool
    night: EvidenceLedgerNightOut | None
    night_unreadable: bool
    basket_rows: int
    silent_rows: int
    unresolved: list[str]
    facts_days: int
    names: list[EvidenceLedgerNameOut]
    standing: list[EvidenceLedgerRowOut]
    groups: list[EvidenceLedgerDayOut]
    tallies: EvidenceLedgerTalliesOut
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------

# the picker is the shared basket read (``baskets.picker_cards``); ``read_cards`` is its name here (the tests
# stub it on this module)
read_cards = picker_cards


def read_night(
    conn: psycopg.Connection, thesis_id: UUID, asof: date
) -> tuple[RecordedNight | None, bool]:
    """``(the recorded call in effect at asof, unreadable)``: ``(None, False)`` = no call recorded by then;
    ``(None, True)`` = a row was found but its card did not validate against the call model (said on the page,
    never a 500 and never skipped silently)."""
    with conn.cursor() as cur:
        cur.execute(_RECORDED_SQL, (thesis_id, asof))
        row = cur.fetchone()
    if row is None:
        return None, False
    try:
        card = CallCard.model_validate(row["card"])
    except ValidationError:
        return None, True
    return RecordedNight(row["asof"], row["reconstructed"], card), False


def read_role_snapshots(
    conn: psycopg.Connection, thesis_id: UUID, known_at: datetime | None
) -> list[RosterSnapshot]:
    """The card's roster snapshots taken by ``known_at`` (``None`` = now), oldest first, each narrowed to its
    role-tagged members. A snapshot taken after ``known_at`` is never read (invariant #1)."""
    with conn.cursor() as cur:
        cur.execute(_ROLE_SNAPSHOTS_SQL, (thesis_id, known_at))
        return [RosterSnapshot(r["taken_at"], r["members"]) for r in cur.fetchall()]


def read_inputs(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> LedgerBase:
    """The heavy read the cache holds: the card's roster as of the as-of (``baskets.cards_as_of``'s rule for one
    card: the live view reads today's roster, a past view the latest snapshot by ``known_at``, today's roster
    standing in before the first one), the recorded call in effect, the ratified and stored facts of every roster
    company (Context and Bench included: the page tags them) for the shared reader's year, and the roster
    snapshots that date a role."""
    if known_at is None:
        card, fallback = thesis_repo.get(conn, thesis_id), False
    else:
        fallback = not thesis_repo.snapshot_exists_asof(conn, thesis_id, known_at)
        card = thesis_repo.get_asof(conn, thesis_id, known_at)
    if card is None:
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    memberships = memberships_of([card])
    # the ticker a row shows is the card's own (the Cockpit's ?name= key), not the master's
    tickers = {m.security_id: m.ticker for m in reversed(memberships) if m.security_id is not None}
    night, unreadable = read_night(conn, thesis_id, asof)
    trigger_sids = (
        {t.security_id for t in (*night.card.triggers_fired, *night.card.risk_signals)}
        if night
        else set()
    )
    for sid, ticker in master.tickers_for(conn, trigger_sids - set(tickers)).items():
        if ticker:
            tickers[sid] = ticker
    return ledger_base(
        thesis_id=card.id,
        thesis_name=card.name,
        memberships=memberships,
        night=night,
        night_unreadable=unreadable,
        tickers=tickers,
        ciks=master.ciks_for(conn, trigger_sids),
        rows_by_sid=read_filing_rows(
            conn,
            resolved_ids(memberships),
            asof,
            known_at,
            clock,
            days=READ_DAYS,
            tickers=tickers,
        ),
        snapshots=read_role_snapshots(conn, thesis_id, known_at),
        roster_fallback=fallback,
    )


def _cards(
    conn: psycopg.Connection, asof: date, clock: Literal["record", "public"]
) -> Cached[CardsRead]:
    return cached(conn, SLUG, ("cards", clock), asof, lambda: read_cards(conn, asof))


@router.get("/cards", response_model=EvidenceLedgerCardsOut)
def evidence_ledger_cards(
    asof: date = Query(
        ..., description="as-of date; only the cards that existed by then are listed"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> EvidenceLedgerCardsOut:
    """The card picker: the live cards that existed by the as-of, A→Z, each with its link count today. Display
    only."""
    _, clock = serve_view(asof)
    got = _cards(conn, asof, clock)
    return EvidenceLedgerCardsOut.model_validate(
        {
            "asof": asof,
            "live_cards": got.value.live_cards,
            "not_yet_created": got.value.not_yet_created,
            "cards": [asdict(c) for c in got.value.cards],
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )


@router.get("/{thesis_id}", response_model=EvidenceLedgerOut)
def evidence_ledger_for_card(
    thesis_id: UUID,
    asof: date = Query(
        ..., description="as-of date; no call recorded for a later night, no fact public after it"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> EvidenceLedgerOut:
    """Everything one card's call and facts rest on, in one list: the recorded call's triggers and risk signals
    with their sources, the ratified and stored facts, the card's catalysts, kill criteria and evidence links and
    its context roles, each row marked by who put it there (a person, a computation, an ingest leg, the card).
    Display only; a past as-of reads the call recorded for that night or the last one before it and the facts
    public by the end of that date (a fact ratified after it never appears). The card's own entries are read live.
    404 when the card is not counted at the as-of."""
    known_at, clock = serve_view(asof)
    if not any(c.thesis_id == thesis_id for c in _cards(conn, asof, clock).value.cards):
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    # the clock is part of the key: the same date read live today and on the public clock tomorrow are two
    # reads (a new run clears the cache anyway; this holds even on a night with no run)
    got = cached(
        conn,
        SLUG,
        (str(thesis_id), clock),
        asof,
        lambda: read_inputs(conn, thesis_id, asof, known_at, clock),
    )
    # the card's own entries are not snapshotted: read them now, not from the cache
    live = thesis_repo.get(conn, thesis_id)
    if live is None:
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    page = evidence_ledger(got.value, card_entries(live), asof=asof)
    base = page.base
    return EvidenceLedgerOut.model_validate(
        {
            "asof": asof,
            "thesis_id": base.thesis_id,
            "thesis_name": base.thesis_name,
            "clock": clock,
            "roster_fallback": base.roster_fallback,
            "night": (
                {"asof": base.night.asof, "reconstructed": base.night.reconstructed}
                if base.night
                else None
            ),
            "night_unreadable": base.night_unreadable,
            "basket_rows": base.basket_rows,
            "silent_rows": base.silent_rows,
            "unresolved": list(base.unresolved),
            "facts_days": READ_DAYS,
            "names": [{**asdict(n.company), "rows": n.rows} for n in page.names],
            "standing": [asdict(r) for r in page.standing],
            "groups": [{"date": g.date, "rows": [asdict(r) for r in g.rows]} for g in page.groups],
            "tallies": asdict(page.tallies),
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
