from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn, get_thesis_or_404
from app.schemas_api import (
    CallCardResponse,
    CatalystIn,
    DecisionIn,
    DecisionOut,
    DisplaySignalsResponse,
    ExclusionIn,
    KillCriterionIn,
    MemberDisplaySignalsOut,
    ThesisDetail,
    ThesisSummary,
)
from domain.thesis import Catalyst, ExcludedName, KillCriterion, Thesis
from pipeline.call_for_thesis import call_for_thesis
from repositories import calls_repo, decisions_repo, thesis_repo
from securities import master
from signals.base import PointInTimeData
from signals.display import registered_display_members

router = APIRouter(prefix="/theses", tags=["theses"])


@router.get("", response_model=list[ThesisSummary])
def list_theses(
    include_archived: bool = Query(
        False, description="include archived theses (the Board's explicit, reversible filter)"
    ),
    conn: psycopg.Connection = Depends(get_conn),
) -> list[ThesisSummary]:
    """List theses. Archived ones are EXCLUDED by default — the workbench picker and every default
    consumer skip them without asking; the Board passes ``include_archived=true`` and renders them
    in a collapsed section (visible + restorable, never vanished)."""
    return [
        ThesisSummary.from_thesis(t)
        for t in thesis_repo.list_all(conn, include_archived=include_archived)
    ]


@router.post("/{thesis_id}/archive", response_model=ThesisSummary)
def archive_thesis(
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisSummary:
    """ARCHIVE, never delete (board hygiene): the thesis leaves the default list and the daily
    cron's walk (its calls-of-record stop accumulating — the Scoreboard's data stays clean), but the
    spine, the calls log, and the decision log all stay. Fully reversible via unarchive."""
    thesis_repo.set_archived(conn, thesis.id, True)
    conn.commit()
    return ThesisSummary.from_thesis(thesis_repo.get(conn, thesis.id))


@router.post("/{thesis_id}/unarchive", response_model=ThesisSummary)
def unarchive_thesis(
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisSummary:
    """Restore an archived thesis whole — back onto the Board and into the cron's nightly walk."""
    thesis_repo.set_archived(conn, thesis.id, False)
    conn.commit()
    return ThesisSummary.from_thesis(thesis_repo.get(conn, thesis.id))


@router.get("/{thesis_id}", response_model=ThesisDetail)
def get_thesis(
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisDetail:
    # NB: intentionally NO docstring — it would become the operation `description` and drift the
    # OpenAPI contract; this fix only POPULATES an existing wire field (zero-diff by design).
    # Thread the decisions-log-derived position onto the thesis — the SAME source of truth the call
    # path uses (``call_for_thesis``) — so ``position.security_id`` is populated for an attributed
    # take. Without it the read path built the position from only the seed columns (which carry no
    # name), leaving the per-name panel's "Position · this name" block dead on real data.
    # ``effective_position`` falls back to the stored seed position when there are no decision rows,
    # so nothing regresses; ``known_at=None`` reads decisions as of current knowledge (no-lookahead
    # #1 holds — a fill can't be dated in the future).
    thesis.position = decisions_repo.effective_position(
        conn, thesis, asof=date.today(), known_at=None
    )
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


@router.get("/{thesis_id}/display-signals", response_model=DisplaySignalsResponse)
def get_display_signals(
    asof: date = Query(..., description="as-of date; indicators use no data knowable after it"),
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> DisplaySignalsResponse:
    """Read-only per-name DISPLAY indicators (SMA position/flips, …), re-derived at ``asof`` from
    the same bitemporal facts the detectors read — quiet tape context beside the call, never an
    input to it (a display signal has no role; it cannot arm, veto, or grade). Computed on read and
    never persisted, so a refetch / as-of scrub writes nothing and the call-of-record log stays
    untouched. Covers every resolved basket member; a member with no computable indicator (e.g. no
    ingested bars yet) shows with ``signals: []`` — an honest empty, never a dropped row.
    """
    pit = PointInTimeData(conn, asof=asof, tenant_id=thesis.tenant_id)
    sids: list[UUID] = []
    for m in thesis.basket:
        if m.security_id is not None and m.security_id not in sids:
            sids.append(m.security_id)
    ticker_for = master.tickers_for(conn, set(sids), tenant_id=thesis.tenant_id)
    members = [
        MemberDisplaySignalsOut(
            security_id=sid,
            ticker=ticker_for.get(sid),
            signals=[
                sig
                for member in registered_display_members()
                if (sig := member(pit, sid, asof)) is not None
            ],
        )
        for sid in sids
    ]
    return DisplaySignalsResponse(thesis_id=thesis.id, asof=asof, members=members)


@router.put("/{thesis_id}/catalysts", response_model=ThesisDetail)
def put_catalysts(
    body: list[CatalystIn],
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisDetail:
    """Author the thesis's catalyst SURFACE — the upcoming binary events the card renders between
    entry and exit-by (display objects; the per-name conviction FACTS go through the ratify path).
    Full-list replace via the sole writer (``set_catalysts`` — the structural wipe-guard: a promote
    never touches this table). Operator authority (#4: the operator authors the events; the platform
    times them)."""
    cats = [
        Catalyst(
            id=uuid4(), label=c.label, kind=c.kind, when_date=c.when_date, when_label=c.when_label
        )
        for c in body
    ]
    thesis_repo.set_catalysts(conn, thesis.id, cats, tenant_id=thesis.tenant_id)
    conn.commit()
    return ThesisDetail.from_thesis(thesis_repo.get(conn, thesis.id))


@router.put("/{thesis_id}/kill-criteria", response_model=ThesisDetail)
def put_kill_criteria(
    body: list[KillCriterionIn],
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisDetail:
    """Author the thesis's kill criteria — the documented "what would kill this", read by the
    deterministic counter-case (the card stops saying "no documented counter-case"). Full-list
    replace via the sole writer; same structural wipe-guard as the catalysts."""
    kills = [KillCriterion(id=uuid4(), text=k.text) for k in body]
    thesis_repo.set_kill_criteria(conn, thesis.id, kills, tenant_id=thesis.tenant_id)
    conn.commit()
    return ThesisDetail.from_thesis(thesis_repo.get(conn, thesis.id))


@router.put("/{thesis_id}/exclusions", response_model=ThesisDetail)
def put_exclusions(
    body: list[ExclusionIn],
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisDetail:
    """Persist the thesis's durable exclusion set (#7) — the operator's NO per name, with the
    optional "rejected because X". Full-list replace via the sole writer (the term_set structural
    guard, fourth application: a promote never touches the table, so a narrative edit can't wipe
    the pruning). THE #9 LINE: discovery never filters on this — a re-draft still surfaces every
    name; the EDITOR seeds these as visibly-greyed, one-click-reversible state."""
    # bound #2, fail-closed (the promote route's own guard): a caller-supplied security_id must be
    # an exact member of this tenant's master — never a junk row behind an FK error
    for e in body:
        if not master.exists(conn, e.security_id, tenant_id=thesis.tenant_id):
            raise HTTPException(
                status_code=404,
                detail=f"exclusion {e.ticker or e.security_id} references a security not in "
                "this tenant's master",
            )
    excl = [ExcludedName(security_id=e.security_id, ticker=e.ticker, reason=e.reason) for e in body]
    thesis_repo.set_exclusions(conn, thesis.id, excl, tenant_id=thesis.tenant_id)
    conn.commit()
    return ThesisDetail.from_thesis(thesis_repo.get(conn, thesis.id))


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
