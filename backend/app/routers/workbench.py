from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from app.deps import get_conn, get_current_tenant
from app.schemas_api import (
    PromoteThesisRequest,
    ScoredMemberOut,
    ThesisDetail,
    WorkbenchScored,
)
from domain.thesis import Thesis
from repositories import thesis_repo
from securities import master
from signals.base import PointInTimeData
from workbench.scoring import score_thesis

router = APIRouter(prefix="/workbench", tags=["workbench"])


@router.get("/theses/{thesis_id}/scored", response_model=WorkbenchScored)
def get_scored(
    thesis_id: UUID,
    asof: date = Query(..., description="as-of date; the scores use no data knowable after it"),
    conn: psycopg.Connection = Depends(get_conn),
) -> WorkbenchScored:
    """Re-derive the per-name Workbench scores live at ``asof`` — a READ-ONLY path (Option B; nothing
    persists). Mirrors the call endpoint: load the thesis (404 + its tenant), thread ``thesis.tenant_id``
    into every scoring fact read so a production thesis scores off production's facts."""
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise HTTPException(status_code=404, detail="thesis not found")
    pit = PointInTimeData(conn, asof=asof, tenant_id=thesis.tenant_id)
    scored = score_thesis(pit, thesis)
    sec_ids = {m.security_id for m in scored}
    cik_for = master.ciks_for(conn, sec_ids, tenant_id=thesis.tenant_id)
    ticker_for = master.tickers_for(conn, sec_ids, tenant_id=thesis.tenant_id)
    return WorkbenchScored(
        thesis_id=thesis.id,
        asof=asof,
        segments=list(thesis.segments),
        members=[ScoredMemberOut.from_scored(m, cik_for, ticker_for) for m in scored],
    )


@router.post("/theses", response_model=ThesisDetail)
def promote(
    req: PromoteThesisRequest,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> ThesisDetail:
    """Promote a structured thesis to the Board (Incubating) — the app's FIRST mutation. Create (``id``
    null) or update (``id`` set); the value-chain structure (segments + placements + authorship) persists
    via ``thesis_repo.upsert`` (the existing operational save path). The tenant comes from the deployment
    resolver, NOT the body. Scores are never sent and never persist — they re-derive on read."""
    try:
        thesis = Thesis(  # the Slice-1 segment-consistency validator runs here
            id=req.id or uuid4(),
            tenant_id=tenant_id,
            name=req.name,
            narrative=req.narrative,
            ticker=req.ticker,
            basket=req.basket,
            segments=req.segments,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    thesis_repo.upsert(conn, thesis)
    conn.commit()
    return ThesisDetail.from_thesis(thesis)
