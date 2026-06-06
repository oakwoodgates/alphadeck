from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn
from app.schemas_api import CallCardResponse, ThesisDetail, ThesisSummary
from pipeline.call_for_thesis import call_for_thesis
from repositories import thesis_repo

router = APIRouter(prefix="/theses", tags=["theses"])


@router.get("", response_model=list[ThesisSummary])
def list_theses(conn: psycopg.Connection = Depends(get_conn)) -> list[ThesisSummary]:
    return [ThesisSummary.from_thesis(t) for t in thesis_repo.list_all(conn)]


@router.get("/{thesis_id}", response_model=ThesisDetail)
def get_thesis(thesis_id: UUID, conn: psycopg.Connection = Depends(get_conn)) -> ThesisDetail:
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise HTTPException(status_code=404, detail="thesis not found")
    return ThesisDetail.from_thesis(thesis)


@router.get("/{thesis_id}/call", response_model=CallCardResponse)
def get_call(
    thesis_id: UUID,
    asof: date = Query(..., description="as-of date; the call uses no data knowable after it"),
    conn: psycopg.Connection = Depends(get_conn),
) -> CallCardResponse:
    """Recompute the CallCard live at ``asof`` (the read path; never reads the calls log back). The
    signal stream is re-derived from the bitemporal facts, so this is deterministic for a given asof.
    """
    try:
        card = call_for_thesis(conn, thesis_id, asof)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail="thesis not found") from exc
    conn.commit()  # persist the accountability append (the only write on this read path)
    return CallCardResponse.from_card(card)
