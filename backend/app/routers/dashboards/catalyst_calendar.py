"""The Catalyst Calendar dashboard's API (``docs/dashboards/catalyst-calendar.md``): one GET, a "now" view of every
live card's dated catalysts and card deadlines. It READS the live cards (``thesis_repo.list_all``, archived
excluded by its default) and one plain SELECT over the live ids (each card's latest ``calls`` row: its state and
two clocks), then hands them to the pure compute in ``dashboards/catalyst_calendar.py`` with
``today = market_today()``. Its wire models live here, not in ``app/schemas_api.py`` (``docs/DASHBOARDS.md``
rule 8), so cutting the dashboard deletes cleanly."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.catalyst_calendar import LatestCall
from dashboards.catalyst_calendar import catalyst_calendar as compute_catalyst_calendar
from domain.market_time import market_today
from repositories import thesis_repo

router = APIRouter(prefix="/catalyst-calendar")


# ---------------------------------------------------------------------------------------------------------
# the read (SELECT only; scoped to the live ids)
# ---------------------------------------------------------------------------------------------------------


def read_latest_calls(conn: psycopg.Connection, ids: Sequence[UUID]) -> dict[UUID, LatestCall]:
    """Each card's LATEST ``calls`` row: its state and ``arm_until`` / ``exit_by`` (the only read of the card
    blob, two keys of it). A card with no recorded call is absent."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (thesis_id) thesis_id, state, (card->>'arm_until')::date AS arm_until, "
            "(card->>'exit_by')::date AS exit_by FROM calls WHERE thesis_id = ANY(%s) "
            "ORDER BY thesis_id, asof DESC, seq DESC",
            (list(ids),),
        )
        return {
            r["thesis_id"]: LatestCall(r["state"], r["arm_until"], r["exit_by"])
            for r in cur.fetchall()
        }


# ---------------------------------------------------------------------------------------------------------
# the wire
# ---------------------------------------------------------------------------------------------------------


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class CatalystCalendarItemOut(_Wire):
    """One dated line: a catalyst (``item = catalyst``) or one of a card's two deadlines from its latest call
    (``entry_window`` = "entry window ends" at ``arm_until``, ``exit_by`` = "exit-by"). ``days`` = calendar
    days from today (0 = today, negative = passed). ``kind`` / ``when_label`` / ``catalyst_id`` are the
    catalyst's own (``null`` on a deadline). ``state`` = the card's latest recorded state (``null`` = no
    recorded call yet)."""

    item: Literal["catalyst", "entry_window", "exit_by"]
    on: date
    days: int
    label: str
    kind: str | None
    when_label: str | None
    catalyst_id: UUID | None
    thesis_id: UUID
    thesis_name: str
    state: str | None


class CatalystCalendarBucketOut(_Wire):
    """One stretch of the upcoming calendar, ``start`` to ``end`` inclusive (``end`` is ``null`` for "later").
    A week starts on Monday. Items by date, then card name."""

    key: Literal["this_week", "next_week", "next_30_days", "days_31_90", "later"]
    label: str
    start: date
    end: date | None
    items: list[CatalystCalendarItemOut]


class CatalystCalendarUndatedOut(_Wire):
    catalyst_id: UUID
    label: str
    kind: str | None
    when_label: str | None


class CatalystCalendarUndatedGroupOut(_Wire):
    """One card's catalysts with no date, in the card's own order (a ``when_label`` is never turned into a
    date)."""

    thesis_id: UUID
    thesis_name: str
    state: str | None
    count: int
    catalysts: list[CatalystCalendarUndatedOut]


class CatalystCalendarOut(_Wire):
    """The whole page: the five upcoming buckets (empty ones included), the passed catalysts (newest first;
    a passed deadline is never listed) and the undated catalysts per card (most first). The counts are the
    work shown on its quiet line."""

    today: date
    live_cards: int
    cards_without_calls: int
    cards_without_catalysts: int
    catalysts: int
    dated: int
    upcoming_catalysts: int
    passed_catalysts: int
    undated: int
    deadlines: int
    buckets: list[CatalystCalendarBucketOut]
    passed: list[CatalystCalendarItemOut]
    undated_groups: list[CatalystCalendarUndatedGroupOut]


@router.get("", response_model=CatalystCalendarOut)
def catalyst_calendar(conn: psycopg.Connection = Depends(get_conn)) -> CatalystCalendarOut:
    """Every live card's dated catalysts and its latest call's entry window and exit-by, bucketed from today,
    with the passed catalysts and the undated ones per card. A "now" view; archived cards are excluded.
    """
    theses = thesis_repo.list_all(conn)
    return CatalystCalendarOut.model_validate(
        compute_catalyst_calendar(
            theses,
            read_latest_calls(conn, [t.id for t in theses]),
            today=market_today(),
        )
    )
