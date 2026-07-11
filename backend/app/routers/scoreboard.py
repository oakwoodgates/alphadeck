from __future__ import annotations

from datetime import date, datetime, timezone

import psycopg
from fastapi import APIRouter, Depends, Query

from app.deps import get_conn
from app.schemas_api import (
    ScoreboardMetricOut,
    ScoreboardResponse,
    ScoreboardSummaryOut,
    _scoreboard_thesis_out,
)
from db.session import DEFAULT_TENANT_ID
from scoreboard.assemble import assemble_scoreboard
from securities import master

router = APIRouter(prefix="/scoreboard", tags=["scoreboard"])


@router.get("", response_model=ScoreboardResponse)
def get_scoreboard(
    asof: date = Query(..., description="score the record as-of this date (caps both axes)"),
    include_archived: bool = Query(
        True,
        description="archived theses ride the record by default (archiving stops accrual, "
        "never erases the record); false is the explicit, reversible filter",
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> ScoreboardResponse:
    """The Scoreboard (SCORE): the call-of-record scored as-of ``asof`` — a READ-ONLY pass over
    the immutable calls log, the operator-decision log, and realized asof-capped prices. The
    RECORD is the scoring source, never a recompute (replay re-derives history; this holds the
    platform to what it actually said). Aggregate metrics judge only matured, non-censored
    episodes and gate below ``min_n`` — an instrument, not a claim, until n accrues.
    """
    result = assemble_scoreboard(conn, asof=asof, include_archived=include_archived)
    theses_out = []
    for t in result.theses:
        # Resolve tickers/CIKs under the THESIS's tenant (the get_call precedent): episode names
        # + the trigger evidence's names, so provenance links attribute correctly per tenant.
        sids = (
            {e.episode.security_id for e in t.episodes}
            | {tr.security_id for e in t.episodes for tr in e.triggers_at_arm}
            | {s.security_id for s in t.operator_spans if s.security_id is not None}
        )
        tenant = t.tenant_id or DEFAULT_TENANT_ID
        ciks = master.ciks_for(conn, sids, tenant_id=tenant)
        tickers = master.tickers_for(conn, sids, tenant_id=tenant)
        theses_out.append(_scoreboard_thesis_out(t, ciks, tickers))
    summary = result.summary  # assemble_scoreboard always fills it
    return ScoreboardResponse(
        asof=result.asof,
        generated_at=datetime.now(timezone.utc).isoformat(),
        summary=ScoreboardSummaryOut(
            n_theses=result.n_theses,
            n_with_record=result.n_with_record,
            n_episodes=result.n_episodes,
            n_open=result.n_open,
            n_matured=result.n_matured,
            n_censored=result.n_censored,
            n_takes=result.n_takes,
            n_passes=result.n_passes,
            n_overrides=result.n_overrides,
            n_voided=result.n_voided,
            n_eligible=summary.n_eligible if summary else 0,
            record_began=summary.record_began if summary else None,
            banner=summary.banner if summary else "",
            min_n=summary.min_n if summary else 0,
            metrics=[
                ScoreboardMetricOut(
                    name=m.name,
                    claim=m.claim,
                    n=m.n,
                    insufficient_n=m.insufficient_n,
                    summary=m.summary,
                    detail=m.detail,
                    note=m.note,
                )
                for m in (summary.metrics if summary else [])
            ],
        ),
        theses=theses_out,
    )
