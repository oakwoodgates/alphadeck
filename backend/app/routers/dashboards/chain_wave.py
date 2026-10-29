"""The Chain Wave dashboard's API (``docs/dashboards/chain-wave.md``): one card's links in chain order, week by week
against SPY, with its counting names and one row per context role, at the as-of dial. It READS the cards that
existed by the as-of (``dashboards/baskets.py``), the picked card's roster as of the as-of (``thesis_repo.get_asof``),
every company's closes and SPY's on the public clock and the price flags, and hands them to the pure compute in
``dashboards/chain_wave.py`` (the line math is the shared ``dashboards/series.py``). Every row's 52 weeks are
computed once per (card, clock, as-of) in the shared dashboard cache (``dashboards/cache.py``,
``docs/DASHBOARDS.md`` rule 7); the window and the Context/Bench toggle are sliced per request. Its wire models live
here, not in ``app/schemas_api.py`` (rule 8).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import date, datetime
from enum import IntEnum
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.baskets import (
    cards_that_existed,
    memberships_of,
    per_company,
    price_flags,
    resolved_ids,
)
from dashboards.cache import Cached, cached
from dashboards.chain_wave import (
    BENCHMARK,
    READOUT_WEEKS,
    THIN_BELOW,
    WINDOWS,
    CardChoice,
    WaveInputs,
    card_choices,
    wave_inputs,
)
from dashboards.chain_wave import chain_wave as compute_chain_wave
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.market_time import market_tz, serve_view
from repositories import thesis_repo
from signals.base import PointInTimeData
from signals.display import trailing_returns
from signals.horizons import display_bounds

router = APIRouter(prefix="/chain-wave")

SLUG = "chain-wave"
# calendar days of bars read per company and for SPY: the Cockpit's own 1Y-return lookback (the Narrative Index's
# and the Rotation Quadrant's), comfortably past the 53 weeks the 52 columns read
LOOKBACK_DAYS = trailing_returns.LOOKBACK_DAYS


class ChainWaveWeeks(IntEnum):
    """The window: the last 13, 26 or 52 week columns."""

    W13 = 13
    W26 = 26
    W52 = 52


Gap = Literal["no_session", "not_started", "no_return", "no_spy"]


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class ChainWaveCardOut(_Wire):
    """A card in the picker: its name and its real links today (Context and Bench not counted)."""

    thesis_id: UUID
    name: str
    links: int


class ChainWaveCardsOut(_Wire):
    """The picker: the cards counted at the as-of (the live cards that existed by then), A→Z. ``cache`` /
    ``compute_ms`` = whether the list came from the shared dashboard cache and how long it took when computed.
    """

    asof: date
    live_cards: int
    not_yet_created: int
    cards: list[ChainWaveCardOut]
    cache: Literal["hit", "computed"]
    compute_ms: int


class ChainWaveWeekOut(_Wire):
    """One week column: its Monday, its first and last session (``close``, the week's close) and how many sessions
    it holds (0 = none). ``to_date`` = the as-of's week, with the as-of before its Friday."""

    monday: date
    first_session: date | None
    close: date | None
    sessions: int
    to_date: bool


class ChainWaveCellOut(_Wire):
    """One week of a row. ``diff`` = the row's weekly return minus SPY's, in percentage points (``null`` = "—",
    ``gap`` says why: ``no_session`` no session that week, ``not_started`` the line had not started by last week's
    close, ``no_return`` no name contributed a daily return that week, ``no_spy`` SPY had no return that week).
    ``ret`` / ``spy`` = the two weekly returns in percent; ``names`` = names contributing a daily return that week.
    On the SPY row ``ret`` is SPY's own return and ``diff`` is null."""

    diff: float | None
    ret: float | None
    spy: float | None
    names: int
    gap: Gap | None


class ChainWaveMemberOut(_Wire):
    """One company on a row, each once. ``key`` = its ``security_id`` (``ticker:<T>`` for a basket row with no
    resolved security). ``ticker`` = its ticker on the card (the Cockpit's ``?name=`` key). ``flag`` = why it is
    left out (a flagged price history); ``priced`` = a daily return in one of the window's weeks. ``cells`` = its
    own weekly return vs SPY over the window; empty when never read (flagged, or no resolved security).
    """

    key: str
    ticker: str
    security_id: UUID | None
    flag: str | None
    priced: bool
    cells: list[ChainWaveCellOut]


class ChainWaveRowOut(_Wire):
    """One row. ``kind``: ``link`` (a real link today, chain order), ``old_link`` (a roster link that is not on the
    card today), ``no_link``, ``silent`` (Context / Bench, only with the toggle), ``counting`` (every company that
    counts toward the call) or ``role`` (``role``: one context role, Context and Bench names included). ``label`` =
    the link's label (``role`` rows: the role's value). ``names`` = companies, each once (flagged and unresolved
    included); ``priced`` = of those, priced in the window; ``thin`` = fewer than ``thin_below`` priced.
    """

    key: str
    kind: Literal["link", "old_link", "no_link", "silent", "counting", "role"]
    label: str
    role: ContextRole | None
    names: int
    priced: int
    thin: bool
    cells: list[ChainWaveCellOut]
    members: list[ChainWaveMemberOut]


class ChainWaveReadoutOut(_Wire):
    """Only when the card has payers: over the last ``weeks`` week columns (from the close ``start`` to the last
    session ``end``), the payers' line and the counting names' line, each minus SPY's, in pp (``null`` = "—").
    """

    weeks: int
    start: date | None
    end: date | None
    payers: float | None
    counting: float | None


class ChainWaveFlaggedOut(_Wire):
    """A company on the card left out of every line: its data flags reach its price readings at the as-of (the
    reason the Cockpit grays its price cells). ``reason`` = the flags' labels."""

    security_id: UUID
    ticker: str
    reason: str


class ChainWaveOut(_Wire):
    """One card's grid for one window: ``week_columns`` oldest first; ``spy`` = SPY's own weekly return per week;
    ``rows`` in page order (the chain in today's link order, then Counting names, then one row per role set:
    payers, counter-case, direct read, participant, financier, benchmark). ``sessions`` = daily returns read
    (``first_session`` → ``last_session``); ``session_source`` = where the calendar came from (SPY's tape, or the
    companies' bars when SPY has none). ``roster_fallback`` = no roster snapshot by the as-of: today's roster stands
    in. ``cache`` / ``compute_ms`` = whether the 52 weeks came from the shared dashboard cache and how long they
    took when computed."""

    asof: date
    thesis_id: UUID
    thesis_name: str
    weeks: int
    include_silent: bool
    roster_fallback: bool
    links_today: int
    basket_rows: int
    silent_rows: int
    unresolved_rows: int
    sessions: int
    first_session: date | None
    last_session: date | None
    session_source: Literal["benchmarks", "companies", "none"]
    spy_available: bool
    week_columns: list[ChainWaveWeekOut]
    spy: list[ChainWaveCellOut]
    rows: list[ChainWaveRowOut]
    readout: ChainWaveReadoutOut | None
    flagged: list[ChainWaveFlaggedOut]
    roles_set: int
    windows: list[int]
    thin_below: int
    readout_weeks: int
    cache: Literal["hit", "computed"]
    compute_ms: int


# --- the reads (SELECT only, through the point-in-time view) -----------------------------------------------


@dataclass(frozen=True)
class CardsRead:
    live_cards: int
    not_yet_created: int
    cards: tuple[CardChoice, ...]


def read_cards(conn: psycopg.Connection, asof: date) -> CardsRead:
    """The live cards that existed by the as-of (``baskets.cards_that_existed``), each with its link count."""
    live = thesis_repo.list_all(conn)
    created = thesis_repo.created_at_for(conn, [t.id for t in live])
    kept, later = cards_that_existed(live, created, asof, market_tz())
    return CardsRead(len(live), later, card_choices(kept))


def _closes(
    conn: psycopg.Connection,
    sids: Sequence[UUID],
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[UUID, dict[date, float]]:
    """Each company's ``{day: close}`` up to the as-of, from the same point-in-time view the Cockpit's
    ``/display-signals`` builds (``known_at`` + ``clock`` from ``serve_view``)."""
    return per_company(
        conn,
        sids,
        asof,
        known_at,
        clock,
        lambda pit, sid: {
            b["d"]: float(b["close"])
            for b in pit.price_history(sid, lookback_days=LOOKBACK_DAYS)
            if b.get("close") is not None
        },
    )


def _spy(
    conn: psycopg.Connection,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> dict[date, float]:
    """SPY's ``{day: close}`` (``pit.benchmark_prices``; empty = no tape at the as-of)."""
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
        for b in pit.benchmark_prices(BENCHMARK, lookback_days=LOOKBACK_DAYS)
        if b.get("close") is not None
    }


def read_inputs(
    conn: psycopg.Connection,
    thesis_id: UUID,
    asof: date,
    known_at: datetime | None,
    clock: Literal["record", "public"],
) -> WaveInputs:
    """The heavy read the cache holds: the card's roster as of the as-of (``baskets.cards_as_of``'s rule for one
    card: the live view reads today's roster, a past view the latest snapshot by ``known_at``, today's roster
    standing in before the first one), every company's closes and price flags (Context and Bench included: the
    role rows read them and the toggle is sliced per request), SPY's closes, built into every row's 52 weeks.
    """
    if known_at is None:
        card, fallback = thesis_repo.get(conn, thesis_id), False
    else:
        fallback = not thesis_repo.snapshot_exists_asof(conn, thesis_id, known_at)
        card = thesis_repo.get_asof(conn, thesis_id, known_at)
    if card is None:
        raise HTTPException(status_code=404, detail="card not counted at this as-of")
    memberships = memberships_of([card])
    sids = resolved_ids(memberships)
    return wave_inputs(
        thesis_id=card.id,
        thesis_name=card.name,
        segments=[s.label for s in card.segments],
        memberships=memberships,
        closes=_closes(conn, sids, asof, known_at, clock),
        spy=_spy(conn, asof, known_at, clock),
        asof=asof,
        roster_fallback=fallback,
        flagged=price_flags(conn, sids, asof, known_at=known_at, clock=clock),
    )


def _cards(
    conn: psycopg.Connection, asof: date, clock: Literal["record", "public"]
) -> Cached[CardsRead]:
    return cached(conn, SLUG, ("cards", clock), asof, lambda: read_cards(conn, asof))


@router.get("/cards", response_model=ChainWaveCardsOut)
def chain_wave_cards(
    asof: date = Query(
        ..., description="as-of date; only the cards that existed by then are listed"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> ChainWaveCardsOut:
    """The card picker: the live cards that existed by the as-of, A→Z, each with its link count today. Display
    only."""
    _, clock = serve_view(asof)
    got = _cards(conn, asof, clock)
    return ChainWaveCardsOut.model_validate(
        {
            "asof": asof,
            "live_cards": got.value.live_cards,
            "not_yet_created": got.value.not_yet_created,
            "cards": [asdict(c) for c in got.value.cards],
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )


@router.get("/{thesis_id}", response_model=ChainWaveOut)
def chain_wave(
    thesis_id: UUID,
    asof: date = Query(..., description="as-of date; no price published after it is read"),
    weeks: ChainWaveWeeks = Query(ChainWaveWeeks.W26, description="the window: 13 | 26 | 52 weeks"),
    include_silent: bool = Query(
        False, description="add Context and Bench as the last two chain rows"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> ChainWaveOut:
    """One card's links in chain order, week by week: each cell is the link's equal-weighted weekly return
    (rebalanced daily) minus SPY's, in percentage points, so a move traveling along the chain shows as a
    diagonal. Below the chain, its counting names and one row per context role (payers first), each with its
    companies' own weekly cells. A name whose data flags reach its price readings is left out of every line and
    listed. Display only; a past as-of reads what was public by the end of that date. 404 when the card is not
    counted at the as-of."""
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
    page = compute_chain_wave(got.value, asof=asof, weeks=int(weeks), include_silent=include_silent)
    return ChainWaveOut.model_validate(
        {
            **asdict(page),
            "windows": list(WINDOWS),
            "thin_below": THIN_BELOW,
            "readout_weeks": READOUT_WEEKS,
            "cache": got.status,
            "compute_ms": got.compute_ms,
        }
    )
