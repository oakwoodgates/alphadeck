from __future__ import annotations

from datetime import date, datetime, timezone

import psycopg
from fastapi import APIRouter, Depends, Query

from app.deps import get_conn
from app.schemas_api import (
    ScoreboardMetricOut,
    ScoreboardReplayResponse,
    ScoreboardReplayThesisOut,
    ScoreboardResponse,
    ScoreboardSummaryOut,
    _scoreboard_episode_out,
    _scoreboard_thesis_out,
)
from db.session import DEFAULT_TENANT_ID
from replay.metrics import MetricResult
from scoreboard.artifact import read_snapshot
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


def _metric_out(m: MetricResult) -> ScoreboardMetricOut:
    return ScoreboardMetricOut(
        name=m.name,
        claim=m.claim,
        n=m.n,
        insufficient_n=m.insufficient_n,
        summary=m.summary,
        detail=m.detail,
        note=m.note,
    )


@router.get("/replay", response_model=ScoreboardReplayResponse)
def get_scoreboard_replay(
    conn: psycopg.Connection = Depends(get_conn),
) -> ScoreboardReplayResponse:
    """The HISTORICAL (replayed) panel — replayed history served from the operator-kicked artifact
    (``python -m scoreboard.replay_snapshot``, dev venv only: replay needs the .[replay] extra the
    lean image deliberately lacks). A RECOMPUTE by construction — today's code + dials over
    historical facts, the not-bitemporal basket caveat riding the banner — NEVER the record and
    never merged with it: separate artifact, separate endpoint, metrics never pooled with the live
    summary. ``available=false`` when no artifact exists (or it fails validation — absence, not an
    outage). Read-only; the container's artifact mount is read-only besides.
    """
    snap = read_snapshot()
    if snap is None:
        return ScoreboardReplayResponse(available=False)
    theses_out = []
    for t in snap.theses:
        sids = {e.episode.security_id for e in t.episodes} | {
            tr.security_id for e in t.episodes for tr in e.triggers_at_arm
        }
        tenant = t.tenant_id or DEFAULT_TENANT_ID
        ciks = master.ciks_for(conn, sids, tenant_id=tenant)
        tickers = master.tickers_for(conn, sids, tenant_id=tenant)
        theses_out.append(
            ScoreboardReplayThesisOut(
                thesis_id=t.thesis_id,
                name=t.name,
                ticker=t.ticker,
                basket_size=t.basket_size,
                episodes=[_scoreboard_episode_out(e, ciks, tickers) for e in t.episodes],
            )
        )
    return ScoreboardReplayResponse(
        available=True,
        generated_at=snap.generated_at,
        window_start=snap.window_start,
        window_end=snap.window_end,
        known_at_pin=snap.known_at_pin,
        record_began=snap.record_began,
        window_overlaps_record=snap.window_overlaps_record,
        banner=snap.banner,
        min_n=snap.min_n,
        n_theses=snap.n_theses,
        n_episodes=snap.n_episodes,
        n_censored=snap.n_censored,
        n_eligible=snap.n_eligible,
        metrics=[_metric_out(m) for m in snap.metrics],
        theses=theses_out,
    )
