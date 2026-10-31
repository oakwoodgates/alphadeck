"""The What Changed dashboard's API (``docs/dashboards/what-changed.md``): one GET, a "now" view of what moved
on the live cards between two recorded nights. It READS the live cards (``thesis_repo.list_all``, archived
excluded by its default), the ``calls`` log (T, the baseline night, and the one row in effect per card on each
of the two nights) and the master's tickers, then hands them to the pure compute in
``dashboards/what_changed.py``, where every diff rule lives. Its wire models live here, not in
``app/schemas_api.py`` (``docs/DASHBOARDS.md`` rule 8), so cutting the dashboard deletes cleanly."""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.what_changed import (
    Change,
    RecordedCall,
    Section,
    SinceMode,
    SinceOutOfRange,
    baseline_date,
    parse_since,
    security_ids,
)
from dashboards.what_changed import what_changed as compute_what_changed
from domain.call import CallCard
from domain.enums import Kind, State
from domain.market_time import countdown_origin, market_clock
from repositories import thesis_repo
from securities import master

router = APIRouter(prefix="/what-changed")

# The calls log reads: plain SELECTs, all scoped to the live cards' ids. "In effect at D" = the greatest
# (asof, seq) with asof <= D, one row per card: the log appends only on change, so the last row on or before
# D is what stood that night, and a night with several rows is read at its highest seq.
_SPAN_SQL = "SELECT min(asof) AS first, max(asof) AS latest FROM calls WHERE thesis_id = ANY(%s)"
_PREVIOUS_SQL = "SELECT max(asof) AS previous FROM calls WHERE thesis_id = ANY(%s) AND asof < %s"
_IN_EFFECT_SQL = (
    "SELECT DISTINCT ON (thesis_id) thesis_id, asof, seq, reconstructed, card FROM calls "
    "WHERE thesis_id = ANY(%s) AND asof <= %s ORDER BY thesis_id, asof DESC, seq DESC"
)


def _span(conn: psycopg.Connection, ids: list[UUID]) -> tuple[date | None, date | None]:
    """``(first, latest)``: the first and the newest recorded night across ``ids`` (``None`` = no rows)."""
    with conn.cursor() as cur:
        cur.execute(_SPAN_SQL, (ids,))
        row = cur.fetchone()
    return row["first"], row["latest"]


def _previous_night(conn: psycopg.Connection, ids: list[UUID], latest: date) -> date | None:
    with conn.cursor() as cur:
        cur.execute(_PREVIOUS_SQL, (ids, latest))
        return cur.fetchone()["previous"]


def _in_effect(conn: psycopg.Connection, ids: list[UUID], on: date) -> dict[UUID, RecordedCall]:
    with conn.cursor() as cur:
        cur.execute(_IN_EFFECT_SQL, (ids, on))
        return {
            r["thesis_id"]: RecordedCall(
                asof=r["asof"],
                seq=r["seq"],
                reconstructed=r["reconstructed"],
                card=CallCard.model_validate(r["card"]),
            )
            for r in cur.fetchall()
        }


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class WhatChangedCardRefOut(_Wire):
    thesis_id: UUID
    thesis_name: str


class WhatChangedItemOut(_Wire):
    """One line. ``section`` = its group on the page; ``change`` = what moved. ``security_id`` / ``ticker`` are
    set for a name or a trigger line (``null`` = a card-level line; ``ticker`` is ``null`` when the master has
    none). ``label`` = the key's own label, or the trigger's. ``on`` = the trigger's event date, or the clock's
    date (``arm_until`` / ``exit_by``); ``previous_on`` = a re-dated trigger's event date on the baseline night
    (``null`` on every other line); ``days`` = calendar days from the countdown origin (the action day, the session
    after the newest night) to ``arm_until``.
    ``reconstructed`` = the card's newest row was written by ``pipeline.backfill``."""

    section: Section
    change: Change
    thesis_id: UUID
    thesis_name: str
    reconstructed: bool
    security_id: UUID | None
    ticker: str | None
    from_state: State | None
    to_state: State | None
    label: str | None
    signal_kind: Kind | None
    on: date | None
    previous_on: date | None
    days: int | None


class WhatChangedOut(_Wire):
    """The whole page. ``latest`` = the newest recorded night across the live cards (``null`` = no recorded
    call yet); ``baseline`` = the night compared against (``null`` = no earlier night, so every recorded card
    is new). ``first_recorded`` bounds the date picker. ``unrecorded`` = live cards with no recorded call yet,
    counted on the quiet line. Items come in section order, then card name."""

    since: SinceMode
    since_date: date | None
    first_recorded: date | None
    latest: date | None
    baseline: date | None
    live_cards: int
    cards_with_change: int
    unrecorded: list[WhatChangedCardRefOut]
    items: list[WhatChangedItemOut]


@router.get("", response_model=WhatChangedOut)
def what_changed(
    since: str = Query(
        "last_night",
        pattern=r"^(last_night|7d|\d{4}-\d{2}-\d{2})$",
        description=(
            "the baseline night: last_night (the previous recorded night), 7d (7 calendar days before "
            "the newest night) or a YYYY-MM-DD date between the first recorded night and the newest"
        ),
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> WhatChangedOut:
    """What moved on the live cards between the baseline night and the newest recorded night (states, keys,
    armed and watch names, triggers and risk signals), plus the clocks coming due on the newest one. A "now"
    view of the recorded calls; archived cards are excluded. A picked date after the newest recorded night,
    or before the first, is a 422.
    """
    try:
        choice = parse_since(since)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    theses = thesis_repo.list_all(conn)
    ids = [t.id for t in theses]
    first, latest = _span(conn, ids) if ids else (None, None)
    previous = (
        _previous_night(conn, ids, latest)
        if latest is not None and choice.mode == "last_night"
        else None
    )
    try:
        baseline_on = baseline_date(choice, latest=latest, previous=previous, first=first)
    except SinceOutOfRange as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    now = _in_effect(conn, ids, latest) if latest is not None else {}
    baseline = _in_effect(conn, ids, baseline_on) if baseline_on is not None else {}
    # F2: a "now" view is always live, so the entry-window countdown counts from the action day (the session after
    # the current night). `latest` can trail the night when a night recorded no change: the page is live all the same.
    mclock = market_clock()
    origin = countdown_origin(mclock.night, mclock)
    result = compute_what_changed(
        {t.id: t.name for t in theses},
        now,
        baseline,
        master.tickers_for(conn, security_ids([*now.values(), *baseline.values()])),
        latest=latest,
        baseline_on=baseline_on,
        origin=origin,
    )
    return WhatChangedOut(
        since=choice.mode, since_date=choice.on, first_recorded=first, **asdict(result)
    )
