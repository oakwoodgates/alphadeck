from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import ValidationError

from app.deps import get_conn, get_current_tenant
from app.schemas_api import (
    PromoteThesisRequest,
    RatifiedFactOut,
    RatifyFactRequest,
    ScoredMemberOut,
    SecurityMatchOut,
    ThesisDetail,
    WorkbenchScored,
)
from domain.enums import Authorship
from domain.extraction import ExtractedFact
from domain.thesis import Thesis
from ingest.cash_burn import ingest_cash_burn
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import extract_for_security
from ingest.revenue_mix import ingest_revenue_mix
from ingest.shares import ingest_shares_outstanding
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


@router.get("/securities", response_model=list[SecurityMatchOut])
def search_securities(
    q: str = Query("", description="ticker or name fragment; a discovery net over the master"),
    limit: int = Query(10, ge=1, le=50),
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> list[SecurityMatchOut]:
    """Search the current tenant's security master for names to PLACE into a basket (the authoring
    typeahead, Slice 4b). A DISCOVERY NET (INVARIANT #2): exact master rows for the operator to pick from
    — never a fuzzy decision, never an ingest (no ``allow_live``). No match -> ``[]``; the operator's pick
    carries the exact ``security_id``. Tenant from the deployment resolver (which universe to author in).
    """
    matches = master.search(conn, q, tenant_id=tenant_id, limit=limit)
    return [
        SecurityMatchOut(security_id=m.id, ticker=m.ticker, name=m.name, cik=m.cik) for m in matches
    ]


@router.get("/securities/{security_id}/extract", response_model=list[ExtractedFact])
def extract_scoring_facts(
    security_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> list[ExtractedFact]:
    """Auto-EXTRACT candidate scoring facts for a security from its latest SEC 10-Q/10-K (Slice hybrid-1) —
    the three-tier hybrid: AUTO pre-fills the clean facts, FLAG carries the raw value + a detected risk + the
    located passage (the operator ratifies the composition), HUMAN (purity) is LOCATED only and never
    auto-valued. An EXPLICIT operator action (cache-first, live SEC), never fired on a render. The extractor
    never DECIDES — the operator confirms (hybrid-2). Requires ``ALPHADECK_USER_AGENT`` (SEC etiquette).
    """
    cik = master.ciks_for(conn, {security_id}, tenant_id=tenant_id).get(security_id)
    if not cik:
        raise HTTPException(status_code=404, detail="no CIK for this security — resolve it first")
    try:
        return extract_for_security(EdgarClient(allow_live=True), cik)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — SEC unreachable / no UA / parse hiccup -> a clear 502, not a 500
        raise HTTPException(status_code=502, detail=f"extraction failed: {exc}") from exc


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
            # Authorship is STAMPED here, not taken from the body: this is the human authoring path, so
            # every placement is `operator_set` (any incoming value — incl. a stale `system_drafted` — is
            # coerced). `system_drafted` is reserved for the S5 drafter's own write path; `operator_edited`
            # (a diff against a stored draft) also lands with S5, when drafts exist to edit. (INVARIANT #1.)
            basket=[
                m.model_copy(update={"authored_by": Authorship.OPERATOR_SET}) for m in req.basket
            ],
            segments=req.segments,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    thesis_repo.upsert(conn, thesis)
    conn.commit()
    return ThesisDetail.from_thesis(thesis)


@router.post("/facts", response_model=RatifiedFactOut)
def ratify_fact(
    req: RatifyFactRequest,
    conn: psycopg.Connection = Depends(get_conn),
    tenant_id: UUID = Depends(get_current_tenant),
) -> RatifiedFactOut:
    """Ratify an extracted candidate -> write the scoring fact (hybrid-2a) — the app's first fact-WRITE. The
    operator confirms/edits a candidate (AUTO as-is, FLAG the composition, HUMAN purity the value); this
    persists it via the existing ``ingest_*`` so the meter re-derives on the next scored read.

    WRITE-SIDE TENANT DISCIPLINE: the security must be in the CURRENT tenant's master (fail-closed) — the
    tenant is the deployment resolver's, but the ``security_id`` is caller-supplied, so a foreign/unknown id
    must not write a junk fact. ``source`` is preserved (the candidate's basis, e.g. ``10-k-segment``);
    ``ratified_by`` is stamped "operator"; the fact is append-only (a re-ratify is a new row, latest-wins).
    """
    if not master.exists(conn, req.security_id, tenant_id=tenant_id):
        raise HTTPException(status_code=404, detail="security not in this tenant's master")
    common = dict(
        source=req.source,
        source_ref=req.source_ref,
        event_date=req.event_date,
        note=req.note,
        ratified_by="operator",  # the human ratify path — stamped, not taken from the body
        tenant_id=tenant_id,
    )
    if req.fact_type == "revenue_mix":
        fid = ingest_revenue_mix(
            conn, req.security_id, segment_label=req.segment_label, mix_pct=req.mix_pct, **common
        )
    elif req.fact_type == "shares_outstanding":
        fid = ingest_shares_outstanding(conn, req.security_id, shares=req.shares, **common)
    else:  # cash_burn
        fid = ingest_cash_burn(
            conn,
            req.security_id,
            cash_usd=req.cash_usd,
            quarterly_burn_usd=req.quarterly_burn_usd,
            **common,
        )
    conn.commit()
    return RatifiedFactOut(fact_id=fid, fact_type=req.fact_type)
