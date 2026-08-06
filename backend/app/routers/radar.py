"""The SPAC Radar API (docs/temp/spac-radar-options.md, slices 1+2): the pull-only tape read +
the two reversible act-on writes. Read-time state derive (radar/state.py) — the DB stores facts
only. No LLM anywhere on this surface (#3); no ``calls/`` import (the radar is structurally
outside the call path)."""

from __future__ import annotations

from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn
from app.schemas_api import (
    RadarSpacOut,
    SpacAttachOut,
    SpacAttachRequest,
    SpacEventOut,
    SpacMatchOut,
)
from domain.enums import Authorship
from domain.thesis import BasketMember, Thesis
from radar import repo
from radar.state import StateEvent, deal_state
from repositories import thesis_repo
from securities import master

router = APIRouter(prefix="/radar", tags=["radar"])


@router.get("/spac", response_model=RadarSpacOut)
def spac_tape(
    days: int = Query(90, ge=1, le=365, description="tape window (filed within the last N days)"),
    limit: int = Query(200, ge=1, le=1000),
    conn: psycopg.Connection = Depends(get_conn),
) -> RadarSpacOut:
    """The SPAC Radar tape: blank-check transition filings (latest version per accession) newest
    first, each with its CIK's read-time deal state (derived over the FULL stored history, not just
    the window), the canonical master join (ticker), per-thesis term-set matches (#10
    recommendations), and which theses already hold the name (the reversible added-toggle)."""
    rows = repo.list_events(conn, days=days, limit=limit)
    ciks = sorted({r["cik"] for r in rows})
    history = repo.events_for_ciks(conn, ciks)
    by_cik: dict[str, list[StateEvent]] = {}
    for h in history:
        by_cik.setdefault(h["cik"], []).append(
            StateEvent(
                filed=h["filed"],
                form=h["form"],
                items=tuple(h["items"]) if h["items"] else None,
                accession=h["accession"],
            )
        )
    state_by_cik = {c: deal_state(evs) for c, evs in by_cik.items()}

    match_rows = repo.latest_matches(conn, [r["accession"] for r in rows])
    theses = thesis_repo.list_all(conn)
    thesis_name = {t.id: t.name for t in theses}
    holders_by_sid: dict[UUID, list[UUID]] = {}
    for t in theses:
        for m in t.basket:
            if m.security_id is not None:
                holders_by_sid.setdefault(m.security_id, []).append(t.id)
    matches_by_accession: dict[str, list[SpacMatchOut]] = {}
    for mr in match_rows:
        name = thesis_name.get(mr["thesis_id"])
        if name is None:  # archived/deleted thesis — a stale match never renders a ghost badge
            continue
        matches_by_accession.setdefault(mr["accession"], []).append(
            SpacMatchOut(
                thesis_id=mr["thesis_id"],
                thesis_name=name,
                signal_terms=mr["matched_signal"] or [],
                broad_terms=mr["matched_broad"] or [],
                truncated=mr["truncated"],
            )
        )
    ticker_by_sid = repo.tickers_for(conn, [r["security_id"] for r in rows if r["security_id"]])

    events = [
        SpacEventOut(
            cik=r["cik"],
            ticker=ticker_by_sid.get(r["security_id"]),
            company_name=r["company_name"],
            security_id=r["security_id"],
            form=r["form"],
            items=r["items"],
            filed=r["filed"],
            accession=r["accession"],
            url=r["source_ref"],
            deal_state=state_by_cik.get(r["cik"], "searching"),
            in_basket_of=holders_by_sid.get(r["security_id"], []) if r["security_id"] else [],
            matches=sorted(
                matches_by_accession.get(r["accession"], []),
                key=lambda m: (-len(m.signal_terms), -len(m.broad_terms), m.thesis_name),
            ),
        )
        for r in rows
    ]
    return RadarSpacOut(
        events=events,
        window_days=days,
        shells_known=len(repo.known_shell_ciks(conn)),
    )


def _resolve_attach_target(
    conn: psycopg.Connection, req: SpacAttachRequest
) -> tuple[Thesis, UUID, str]:
    """Shared attach/detach resolution: the thesis (404), the canonical master id (422 when the
    CIK has no master row), and its ticker (422 when unlisted — not directly investable here)."""
    thesis = thesis_repo.get(conn, req.thesis_id)
    if thesis is None:
        raise HTTPException(status_code=404, detail="thesis not found")
    cik10 = req.cik.zfill(10)
    sid = master.ids_for_ciks(conn, [cik10]).get(cik10)
    if sid is None:
        raise HTTPException(
            status_code=422,
            detail="CIK has no security-master row — add it via the Workbench name search",
        )
    ticker = repo.tickers_for(conn, [sid]).get(sid)
    if not ticker:
        raise HTTPException(
            status_code=422,
            detail="no listed ticker on the master row — not directly investable from the radar",
        )
    return thesis, sid, ticker


@router.post("/spac/attach", response_model=SpacAttachOut)
def spac_attach(
    req: SpacAttachRequest, conn: psycopg.Connection = Depends(get_conn)
) -> SpacAttachOut:
    """Add a radar name to a thesis basket — the operator's click, so ``operator_set`` (#10: the
    radar recommended, the operator decided). The member lands uncharacterized (role "—", no
    archetype, no segment — the finalize rail characterizes it, item F), with ``surfaced_terms``
    frozen from the stored term matches (factual provenance, never client-supplied). Idempotent:
    already-in-basket returns ``already`` and writes nothing. Reversible via detach (#1)."""
    thesis, sid, ticker = _resolve_attach_target(conn, req)
    if any(m.security_id == sid for m in thesis.basket):
        return SpacAttachOut(thesis_id=thesis.id, security_id=sid, ticker=ticker, already=True)
    member = BasketMember(
        ticker=ticker,
        role="—",
        security_id=sid,
        surfaced_terms=repo.matched_terms_for(conn, req.cik.zfill(10), thesis.id),
        authored_by=Authorship.OPERATOR_SET,
    )
    thesis_repo.upsert(conn, thesis.model_copy(update={"basket": [*thesis.basket, member]}))
    conn.commit()
    return SpacAttachOut(thesis_id=thesis.id, security_id=sid, ticker=ticker, added=True)


@router.post("/spac/detach", response_model=SpacAttachOut)
def spac_detach(
    req: SpacAttachRequest, conn: psycopg.Connection = Depends(get_conn)
) -> SpacAttachOut:
    """The attach inverse (#1 reversibility): remove the name from the basket, returning to the
    prior state — facts/prices stay bitemporal, a re-attach re-binds the same id. Not-in-basket
    is the idempotent no-op (``removed=False``)."""
    thesis, sid, ticker = _resolve_attach_target(conn, req)
    kept = [m for m in thesis.basket if m.security_id != sid]
    if len(kept) == len(thesis.basket):
        return SpacAttachOut(thesis_id=thesis.id, security_id=sid, ticker=ticker, removed=False)
    thesis_repo.upsert(conn, thesis.model_copy(update={"basket": kept}))
    conn.commit()
    return SpacAttachOut(thesis_id=thesis.id, security_id=sid, ticker=ticker, removed=True)
