"""The Idea Timeline dashboard's API (``docs/dashboards/idea-timeline.md``): one GET, a "now" view of each live
card's recorded state over time. It READS the live cards (``thesis_repo.list_all``, archived excluded by its
default) and four plain SELECTs over the live ids (the ``calls`` rows without their card blob, the latest
row's two clocks, the exclude / revoke annotations, the logged decisions), then hands them to the pure compute
in ``dashboards/idea_timeline.py`` with ``today = market_today()``. Its wire models live here, not in
``app/schemas_api.py`` (``docs/DASHBOARDS.md`` rule 8), so cutting the dashboard deletes cleanly."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from app.deps import get_conn
from dashboards.idea_timeline import AnnotationRow, CallRow, Clocks, DecisionRow
from dashboards.idea_timeline import idea_timeline as compute_idea_timeline
from domain.market_time import market_today
from repositories import thesis_repo

router = APIRouter(prefix="/idea-timeline")


# ---------------------------------------------------------------------------------------------------------
# the reads (SELECT only; every one scoped to the live ids)
# ---------------------------------------------------------------------------------------------------------


def read_calls(conn: psycopg.Connection, ids: Sequence[UUID]) -> list[CallRow]:
    """Every ``calls`` row for the cards, WITHOUT the card blob."""
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, seq, thesis_id, asof, state, verdict, reconstructed FROM calls "
            "WHERE thesis_id = ANY(%s) ORDER BY thesis_id, asof, seq",
            (list(ids),),
        )
        return [CallRow(**r) for r in cur.fetchall()]


def read_clocks(conn: psycopg.Connection, ids: Sequence[UUID]) -> dict[UUID, Clocks]:
    """The LATEST calls row's ``exit_by`` / ``arm_until`` per card (the only read of the card blob, two
    keys of it)."""
    if not ids:
        return {}
    with conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT ON (thesis_id) thesis_id, (card->>'exit_by')::date AS exit_by, "
            "(card->>'arm_until')::date AS arm_until FROM calls WHERE thesis_id = ANY(%s) "
            "ORDER BY thesis_id, asof DESC, seq DESC",
            (list(ids),),
        )
        return {r["thesis_id"]: Clocks(r["exit_by"], r["arm_until"]) for r in cur.fetchall()}


def read_annotations(conn: psycopg.Connection, ids: Sequence[UUID]) -> list[AnnotationRow]:
    """The ``exclude`` annotations on the cards' calls and the ``revoke`` rows that may withdraw them (the
    compute decides which excludes are still active)."""
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, call_id, action, reason, revokes FROM call_annotation "
            "WHERE thesis_id = ANY(%s) AND action IN ('exclude', 'revoke') ORDER BY created_at, id",
            (list(ids),),
        )
        return [AnnotationRow(**r) for r in cur.fetchall()]


def read_decisions(conn: psycopg.Connection, ids: Sequence[UUID]) -> list[DecisionRow]:
    """Every ``operator_decision`` row for the cards, void rows included (they gray the rows they name)."""
    if not ids:
        return []
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, seq, thesis_id, security_id, action, decision_date, reason, voids "
            "FROM operator_decision WHERE thesis_id = ANY(%s) ORDER BY thesis_id, seq",
            (list(ids),),
        )
        return [DecisionRow(**r) for r in cur.fetchall()]


# ---------------------------------------------------------------------------------------------------------
# the wire
# ---------------------------------------------------------------------------------------------------------


class _Wire(BaseModel):
    # read straight off the compute's frozen dataclasses
    model_config = ConfigDict(from_attributes=True)


class IdeaTimelineHatchOut(_Wire):
    """A stretch of a segment whose nights' winning rows are ``reconstructed`` (reported, never scored) or
    carry an active ``exclude`` annotation (``excluded``: set aside on the Scoreboard, ``reason`` its
    reason). ``end`` is exclusive."""

    kind: Literal["reconstructed", "excluded"]
    start: date
    end: date
    nights: int
    reason: str | None


class IdeaTimelineMultiRowNightOut(_Wire):
    asof: date
    rows: int


class IdeaTimelineSegmentOut(_Wire):
    """One run of the same recorded state. ``verdict`` = its first night's; ``end`` is exclusive (the next
    run's first night, or today for the current run); ``days`` = ``end - start`` in calendar days.
    """

    state: str
    verdict: str
    start: date
    end: date
    days: int
    current: bool
    nights: int
    hatches: list[IdeaTimelineHatchOut]
    multi_row_nights: list[IdeaTimelineMultiRowNightOut]


class IdeaTimelineCatalystOut(_Wire):
    label: str
    kind: str | None
    when_date: date
    when_label: str | None
    past: bool


class IdeaTimelineDecisionOut(_Wire):
    """A take / close / pass at its ``decision_date``; ``voided`` = a later void row names it (shown grayed,
    never hidden)."""

    id: UUID
    action: Literal["take", "close", "pass"]
    decision_date: date
    security_id: UUID | None
    ticker: str | None
    reason: str | None
    voided: bool


class IdeaTimelineRowOut(_Wire):
    """One live card. ``current_state`` is ``null`` for a card with no recorded call yet (no segments)."""

    thesis_id: UUID
    thesis_name: str
    current_state: str | None
    current_verdict: str | None
    days_in_state: int | None
    total_days_armed: int
    times_armed: int
    first_recorded: date | None
    last_recorded: date | None
    recorded_calls: int
    nights: int
    segments: list[IdeaTimelineSegmentOut]
    catalysts: list[IdeaTimelineCatalystOut]
    undated_catalysts: int
    decisions: list[IdeaTimelineDecisionOut]
    exit_by: date | None
    arm_until: date | None


class IdeaTimelineOut(_Wire):
    """The whole page. The axis runs from the earliest recorded night to today, then on to the latest future
    marker, capped at ``future_cap`` (today + 90 days). The counts are the work shown on its quiet line.
    """

    today: date
    axis_start: date
    axis_end: date
    future_cap: date
    live_cards: int
    cards_without_calls: int
    recorded_calls: int
    nights: int
    multi_row_nights: int
    reconstructed_nights: int
    excluded_nights: int
    first_recorded: date | None
    last_recorded: date | None
    rows: list[IdeaTimelineRowOut]


@router.get("", response_model=IdeaTimelineOut)
def idea_timeline(conn: psycopg.Connection = Depends(get_conn)) -> IdeaTimelineOut:
    """Each live card's recorded state over time (the calls log as runs of one state), with its dated
    catalysts, the logged decisions and the latest call's exit-by and entry window. A "now" view; archived
    cards are excluded.
    """
    theses = thesis_repo.list_all(conn)
    ids = [t.id for t in theses]
    return IdeaTimelineOut.model_validate(
        compute_idea_timeline(
            theses,
            read_calls(conn, ids),
            read_annotations(conn, ids),
            read_decisions(conn, ids),
            read_clocks(conn, ids),
            today=market_today(),
        )
    )
