from __future__ import annotations

from datetime import date
from typing import Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn, get_thesis_or_404
from app.schemas_api import CallCardResponse, DecisionIn, DecisionOut, ThesisDetail, ThesisSummary
from domain.thesis import Thesis
from pipeline.call_for_thesis import call_for_thesis
from repositories import calls_repo, decisions_repo, thesis_repo
from securities import master

router = APIRouter(prefix="/theses", tags=["theses"])


@router.get("", response_model=list[ThesisSummary])
def list_theses(conn: psycopg.Connection = Depends(get_conn)) -> list[ThesisSummary]:
    return [ThesisSummary.from_thesis(t) for t in thesis_repo.list_all(conn)]


@router.get("/{thesis_id}", response_model=ThesisDetail)
def get_thesis(thesis: Thesis = Depends(get_thesis_or_404)) -> ThesisDetail:
    return ThesisDetail.from_thesis(thesis)


@router.get("/{thesis_id}/call", response_model=CallCardResponse)
def get_call(
    asof: date = Query(..., description="as-of date; the call uses no data knowable after it"),
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> CallCardResponse:
    """Recompute the CallCard live at ``asof`` — a READ-ONLY path. The signal stream is re-derived
    from the bitemporal facts (no persisted firing layer), so a given ``asof`` is deterministic and a
    refetch / as-of-slider scrub / poll writes nothing. The accountability ``calls`` log is written
    by the batch ``pipeline.run`` (the official call of record), never by this GET.
    """
    # The thesis (loaded by get_thesis_or_404) carries its own tenant — call_for_thesis re-loads it and
    # threads that tenant into every fact read; we reuse thesis.tenant_id for the ticker/CIK resolution below.
    card = call_for_thesis(conn, thesis.id, asof, record=False)
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


def _decision_out(row: dict[str, Any], *, voided: bool) -> DecisionOut:
    """Row → wire (numeric columns arrive as Decimal; the timestamp goes out ISO)."""
    return DecisionOut(
        id=row["id"],
        action=row["action"],
        decision_date=row["decision_date"],
        security_id=row["security_id"],
        shares=float(row["shares"]) if row["shares"] is not None else None,
        price=float(row["price"]) if row["price"] is not None else None,
        reason=row["reason"],
        voids=row["voids"],
        call_state=row["call_state"],
        call_verdict=row["call_verdict"],
        recorded_at=row["recorded_at"].isoformat(),
        voided=voided,
    )


@router.post("/{thesis_id}/decisions", response_model=DecisionOut)
def post_decision(
    body: DecisionIn,
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> DecisionOut:
    """APPEND one operator decision (take / pass / close / void) to the decision-capture log.

    Advisory only (#5): this LOGS a fill or pass the operator made elsewhere — nothing routes, nothing
    blocks. A take against a not-yet verdict is logged with the platform's stance riding the row; that
    record IS the v1 gate (the UI shows friction copy — the disagreement is written down, never
    enforced). One open position per thesis (v1): take requires flat, close requires open. A mistake
    is corrected by ``void`` (an append pointing at the mistaken row — reversibility, never a delete).
    The append feeds the Managing state on the next call read (the position derives from this log).
    """
    if body.decision_date > date.today():
        raise HTTPException(
            status_code=422,
            detail="decision_date cannot be in the future — the log records decisions already made",
        )
    open_pos = decisions_repo.effective_position(conn, thesis, asof=date.today())
    if body.action == "take" and open_pos is not None:
        raise HTTPException(
            status_code=422,
            detail="an open position already exists for this thesis (one per thesis in v1) — "
            "close it before logging a new take",
        )
    if body.action == "close" and open_pos is None:
        raise HTTPException(status_code=422, detail="no open position to close on this thesis")
    if body.action == "void":
        if body.voids is None:
            raise HTTPException(
                status_code=422, detail="void requires `voids` — the id of the row to void"
            )
        rows = decisions_repo.list_for_thesis(conn, thesis.id, tenant_id=thesis.tenant_id)
        target = next((r for r in rows if r["id"] == body.voids), None)
        if target is None:
            raise HTTPException(status_code=422, detail="voids: no such decision on this thesis")
        if target["action"] == "void":
            raise HTTPException(
                status_code=422,
                detail="a void cannot be voided — append a fresh take/pass/close instead",
            )
        if any(r["action"] == "void" and r["voids"] == body.voids for r in rows):
            raise HTTPException(status_code=422, detail="that decision is already voided")
    # the platform's stance at logging time — display denormalization only (attribution re-derives
    # from the calls-log join); None on a thesis with no call-of-record yet
    latest = calls_repo.latest_for_thesis(conn, thesis.id)
    row = decisions_repo.append(
        conn,
        thesis_id=thesis.id,
        tenant_id=thesis.tenant_id,
        action=body.action,
        decision_date=body.decision_date,
        security_id=body.security_id,
        shares=body.shares,
        price=body.price,
        reason=body.reason,
        voids=body.voids,
        call_state=latest[0].state.value if latest else None,
        call_verdict=latest[0].verdict.value if latest else None,
    )
    conn.commit()
    return _decision_out(row, voided=False)


@router.get("/{thesis_id}/decisions", response_model=list[DecisionOut])
def list_decisions(
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> list[DecisionOut]:
    """The thesis's decision log, newest first. Voided rows ride along FLAGGED (``voided: true``) —
    the strip greys them, never hides them (pruning hides, it never vanishes)."""
    rows = decisions_repo.list_for_thesis(conn, thesis.id, tenant_id=thesis.tenant_id)
    voided = {r["voids"] for r in rows if r["action"] == "void" and r["voids"] is not None}
    return [_decision_out(r, voided=r["id"] in voided) for r in rows]
