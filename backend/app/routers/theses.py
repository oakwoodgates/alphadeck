from __future__ import annotations

from datetime import date
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn
from app.schemas_api import CallCardResponse, ThesisDetail, ThesisSummary
from pipeline.call_for_thesis import call_for_thesis
from repositories import thesis_repo
from securities import master

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
    """Recompute the CallCard live at ``asof`` — a READ-ONLY path. The signal stream is re-derived
    from the bitemporal facts (no persisted firing layer), so a given ``asof`` is deterministic and a
    refetch / as-of-slider scrub / poll writes nothing. The accountability ``calls`` log is written
    by the batch ``pipeline.run`` (the official call of record), never by this GET.
    """
    # Load the thesis first — both for the 404 and for its tenant (auth deferred, so the tenant is
    # intrinsic to the thesis). call_for_thesis re-loads it and threads the same tenant into every fact read.
    thesis = thesis_repo.get(conn, thesis_id)
    if thesis is None:
        raise HTTPException(status_code=404, detail="thesis not found")
    card = call_for_thesis(conn, thesis_id, asof, record=False)
    sec_ids = (
        {t.security_id for t in card.triggers_fired}
        | {r.security_id for r in card.risk_signals}
        | {
            m.security_id for m in card.armed_members
        }  # the per-member menu needs each member's ticker
        | {m.security_id for m in card.watch_members}
    )
    # Resolve tickers/CIKs under the THESIS's tenant — security_master is per-tenant, so a production
    # thesis's names resolve from production's master, not the demo default.
    cik_for = master.ciks_for(conn, sec_ids, tenant_id=thesis.tenant_id)
    ticker_for = master.tickers_for(conn, sec_ids, tenant_id=thesis.tenant_id)
    return CallCardResponse.from_card(card, cik_for, ticker_for)
