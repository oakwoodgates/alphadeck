from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID, uuid4

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_conn, get_thesis_or_404, get_workspace
from app.schemas_api import (
    CallCardResponse,
    CatalystList,
    DecisionIn,
    DecisionOut,
    DisplaySignalsResponse,
    ExclusionList,
    KillCriterionList,
    MemberDisplaySignalsOut,
    MetaIndexIn,
    RecordedCallSummary,
    ThesisDetail,
    ThesisSummary,
)
from domain.market_time import (
    MarketClock,
    market_clock,
    market_today,
    serve_decisions_asof,
    serve_view,
)
from domain.scopes import DATA_TENANT_ID
from domain.text_types import AsofDate
from domain.thesis import Catalyst, ExcludedName, KillCriterion, Thesis
from pipeline.call_for_thesis import call_for_thesis
from repositories import calls_repo, decisions_repo, thesis_repo
from securities import master
from signals.base import PointInTimeData
from signals.display import registered_display_members, relative_strength, theme_breadth
from signals.horizons import display_bounds

router = APIRouter(prefix="/theses", tags=["theses"])


@router.get("", response_model=list[ThesisSummary], response_model_exclude_unset=True)
def list_theses(
    include_archived: bool = Query(
        False, description="include archived theses (the Board's explicit, reversible filter)"
    ),
    with_call: bool = Query(
        False,
        description="attach each thesis's nightly RECORD in effect at `asof` as `call` (requires `asof`)",
    ),
    asof: AsofDate | None = Query(
        None,
        description="the night whose record `with_call` reads (required with `with_call=true`)",
    ),
    conn: psycopg.Connection = Depends(get_conn),
    workspace: UUID = Depends(get_workspace),
) -> list[ThesisSummary]:
    """List theses. Archived ones are EXCLUDED by default — the workbench picker and every default
    consumer skip them without asking; the Board passes ``include_archived=true`` and renders them
    in a collapsed section (visible + restorable, never vanished).

    ``with_call=true`` (with ``asof``) attaches ``call``: the nightly call of RECORD in effect at ``asof``
    (the ``calls`` row with the greatest ``(asof, seq)`` on or before it; ``null`` = none), read in one
    query and never recomputed: its decision fields and its fired triggers (each ticker resolved through
    the master in one batch). Its ``record_asof`` is earlier than ``asof`` when the pass wrote no row
    for that night. The Board's live read is ``GET /theses/{id}/call``. Without ``with_call`` the
    response carries no ``call`` key. A read: it writes nothing."""
    if with_call and asof is None:
        raise HTTPException(status_code=422, detail="with_call=true requires asof (YYYY-MM-DD)")
    theses = thesis_repo.list_all(conn, workspace=workspace, include_archived=include_archived)
    if not with_call:
        return [ThesisSummary.from_thesis(t) for t in theses]
    assert asof is not None
    recorded = calls_repo.in_effect_for(conn, [t.id for t in theses], asof)
    # ONE ticker query per tenant for the batch (in practice one), never per card: the armed security and every
    # recorded trigger's security.
    sids_by_tenant: dict[UUID, set[UUID]] = {}
    for t in theses:
        rec = recorded.get(t.id)
        if rec is None:
            continue
        sids = sids_by_tenant.setdefault(t.tenant_id, set())
        if rec.card.armed_security_id is not None:
            sids.add(rec.card.armed_security_id)
        sids.update(tr.security_id for tr in rec.card.triggers_fired)
    tickers: dict[UUID, str | None] = {}
    for tenant_id, sids in sids_by_tenant.items():
        tickers.update(master.tickers_for(conn, sids, tenant_id=DATA_TENANT_ID))
    out: list[ThesisSummary] = []
    for t in theses:
        rec = recorded.get(t.id)
        summary = ThesisSummary.from_thesis(t)
        summary.call = (
            RecordedCallSummary.from_recorded(
                record_asof=rec.asof,
                seq=rec.seq,
                reconstructed=rec.reconstructed,
                recorded_at=rec.recorded_at,
                card=rec.card,
                tickers=tickers,
            )
            if rec is not None
            else None
        )
        out.append(summary)
    return out


@router.post(
    "/{thesis_id}/archive", response_model=ThesisSummary, response_model_exclude_unset=True
)
def archive_thesis(
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisSummary:
    """ARCHIVE, never delete (board hygiene): the thesis leaves the default list and the daily
    cron's walk (its calls-of-record stop accumulating — the Scoreboard's data stays clean), but the
    spine, the calls log, and the decision log all stay. Fully reversible via unarchive."""
    thesis_repo.set_archived(conn, thesis.id, True)
    conn.commit()
    return ThesisSummary.from_thesis(thesis_repo.get(conn, thesis.id, workspace=thesis.tenant_id))


@router.post(
    "/{thesis_id}/unarchive", response_model=ThesisSummary, response_model_exclude_unset=True
)
def unarchive_thesis(
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisSummary:
    """Restore an archived thesis whole — back onto the Board and into the cron's nightly walk."""
    thesis_repo.set_archived(conn, thesis.id, False)
    conn.commit()
    return ThesisSummary.from_thesis(thesis_repo.get(conn, thesis.id, workspace=thesis.tenant_id))


@router.put(
    "/{thesis_id}/meta-index", response_model=ThesisSummary, response_model_exclude_unset=True
)
def put_meta_index(
    body: MetaIndexIn,
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisSummary:
    """Mark a card as a META INDEX card (``true``) or a normal card again (``false``). On, none of its names
    count toward the call: it stays Incubating and never warms, arms, notifies or enters the Decision Queue.
    Its links stay real links for the dashboards and its names are still ingested nightly. Nothing is
    deleted either way, and the next read reflects the switch. The sole writer of the flag: a promote
    never touches it."""
    thesis_repo.set_meta_index(conn, thesis.id, body.meta_index)
    conn.commit()
    return ThesisSummary.from_thesis(thesis_repo.get(conn, thesis.id, workspace=thesis.tenant_id))


@router.get("/{thesis_id}", response_model=ThesisDetail)
def get_thesis(
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisDetail:
    return read_thesis_detail(conn, thesis, today=market_today())


def read_thesis_detail(conn: psycopg.Connection, thesis: Thesis, *, today: date) -> ThesisDetail:
    """The CURRENT thesis metadata and position behind ``GET /theses/{id}``; ``today`` is the market day the
    position's decisions are read as of (the route passes ``market_today()``)."""
    # Thread the decisions-log-derived position onto the thesis — the SAME source of truth the call
    # path uses (``call_for_thesis``) — so ``position.security_id`` is populated for an attributed
    # take. Without it the read path built the position from only the seed columns (which carry no
    # name), leaving the per-name panel's "Position · this name" block dead on real data.
    # ``effective_position`` falls back to the stored seed position when there are no decision rows,
    # so nothing regresses; ``known_at=None`` reads decisions as of current knowledge (no-lookahead
    # #1 holds — a fill can't be dated in the future).
    position = decisions_repo.effective_position(conn, thesis, asof=today, known_at=None)
    return ThesisDetail.from_thesis(thesis.model_copy(update={"position": position}))


@router.get("/{thesis_id}/call", response_model=CallCardResponse)
def get_call(
    asof: AsofDate = Query(..., description="as-of date; the call uses no data knowable after it"),
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
    # ``known_at`` + ``clock`` (invariant #1, judged on the PUBLIC clock — operator decision 2026-10-21): a
    # scrubbed-back ``asof`` is a HISTORY view — each fact counts from when it became PUBLIC (its filing /
    # acceptance, its settled session; a correction from its own publication) up to the end of that night
    # (EDGAR's next open, 06:00 ET on the next weekday), so the recompute shows how today's rules judge what
    # was public then — a filing published after the
    # as-of is as invisible as a fact DATED after it, and a fact we happened to ingest late (an outage, a
    # name onboarded later) is not hidden for it. A live ``asof`` threads ``(None, "record")`` (the unchanged
    # live read). See ``domain.market_time.serve_view`` / ``db.public_clock``. LIVE = on or after the current
    # night (F1): the default view is the night the pass last recorded, read live — and on that view the
    # operator's decisions read up to the ACTION day, so a fill logged this morning shows before tonight's pass
    # (``serve_decisions_asof``; decisions only, the facts follow the night). One clock read for both halves.
    return read_call(conn, thesis, asof, mclock=market_clock())


def read_call(
    conn: psycopg.Connection,
    thesis: Thesis,
    asof: date,
    *,
    mclock: MarketClock,
    view: tuple[datetime | None, Literal["record", "public"]] | None = None,
) -> CallCardResponse:
    """The read-only call and its wire shaping behind ``GET /theses/{id}/call``; ``view`` is the clock the facts
    are read on (omitted: ``serve_view`` for the as-of and the clock's night, what the route uses).
    """
    known_at, clock = view if view is not None else serve_view(asof, night=mclock.night)
    card = call_for_thesis(
        conn,
        thesis.id,
        asof,
        known_at=known_at,
        clock=clock,
        record=False,
        decisions_asof=serve_decisions_asof(asof, mclock),
        workspace=thesis.tenant_id,  # the owned thesis's workspace (get_thesis_or_404 checked the caller's)
    )
    sec_ids = (
        {t.security_id for t in card.triggers_fired}
        | {r.security_id for r in card.risk_signals}
        | {
            m.security_id for m in card.armed_members
        }  # the per-member menu needs each member's ticker
        | {m.security_id for m in card.watch_members}
    )
    # Resolve tickers/CIKs in the DATA tenant — the master is shared data (domain.scopes, docs/TENANT_SCOPES.md),
    # one master for every workspace.
    cik_for = master.ciks_for(conn, sec_ids, tenant_id=DATA_TENANT_ID)
    ticker_for = master.tickers_for(conn, sec_ids, tenant_id=DATA_TENANT_ID)
    return CallCardResponse.from_card(card, cik_for, ticker_for)


@router.get("/{thesis_id}/display-signals", response_model=DisplaySignalsResponse)
def get_display_signals(
    asof: AsofDate = Query(..., description="as-of date; indicators use no data knowable after it"),
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
    return read_display_signals(conn, thesis, asof, view=serve_view(asof))


def read_display_signals(
    conn: psycopg.Connection,
    thesis: Thesis,
    asof: date,
    *,
    view: tuple[datetime | None, Literal["record", "public"]],
) -> DisplaySignalsResponse:
    """Canonical display computation on the supplied clock and current displayed roster."""
    sids: list[UUID] = []
    for m in thesis.basket:
        if m.security_id is not None and m.security_id not in sids:
            sids.append(m.security_id)
    # the resolved basket = the PIT's prefetch scope; ``display_bounds()`` = the registry-derived read
    # bounds over every display member + the two thesis-level readers (signals/horizons.py).
    # ``known_at`` + ``clock`` (#1, the PUBLIC clock): a scrubbed-back ``asof`` reads each fact as it was
    # public by that market day's end, exactly like the call above — the tape context beside a past card is
    # the tape as it was public then; a live ``asof`` threads ``(None, "record")`` (the unchanged live read).
    known_at, clock = view
    pit = PointInTimeData(
        conn,
        asof=asof,
        known_at=known_at,
        data_tenant=DATA_TENANT_ID,
        workspace=thesis.tenant_id,
        basket=sids,
        bounds=display_bounds(),
        clock=clock,
    )
    ticker_for = master.tickers_for(conn, set(sids), tenant_id=DATA_TENANT_ID)
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
    # §1.1 — the thesis-level theme-breadth thrust (DISPLAY-only, #4/#7): one aggregate over the same
    # resolved members, re-derived from the same as-of bitemporal prices; ``None`` for an empty basket.
    breadth = theme_breadth.breadth_for(pit, sids)
    # §1.3 — the supersector RS rollup (DISPLAY-only, #4): "which theme is leading the market". The
    # member -> BusinessSupersector map is IDENTITY, resolved HERE (off the display seam, which cannot
    # read the master) and passed in as an opaque grouping label; ``None`` when no benchmark tape is
    # ingested yet (RS is uncomputable) or the basket is empty.
    identity = master.identity_for(conn, set(sids), tenant_id=DATA_TENANT_ID)
    supersector_by_sid = {
        sid: (str(info["business_supersector"]) if info.get("business_supersector") else None)
        for sid, info in identity.items()
    }
    sector_rs = relative_strength.sector_rs_for(pit, sids, supersector_by_sid)
    return DisplaySignalsResponse(
        thesis_id=thesis.id, asof=asof, breadth=breadth, sector_rs=sector_rs, members=members
    )


@router.put("/{thesis_id}/catalysts", response_model=ThesisDetail)
def put_catalysts(
    body: CatalystList,
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
    return ThesisDetail.from_thesis(thesis_repo.get(conn, thesis.id, workspace=thesis.tenant_id))


@router.put("/{thesis_id}/kill-criteria", response_model=ThesisDetail)
def put_kill_criteria(
    body: KillCriterionList,
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisDetail:
    """Author the thesis's kill criteria — the documented "what would kill this", read by the
    deterministic counter-case (the card stops saying "no documented counter-case"). Full-list
    replace via the sole writer; same structural wipe-guard as the catalysts."""
    kills = [KillCriterion(id=uuid4(), text=k.text) for k in body]
    thesis_repo.set_kill_criteria(conn, thesis.id, kills, tenant_id=thesis.tenant_id)
    conn.commit()
    return ThesisDetail.from_thesis(thesis_repo.get(conn, thesis.id, workspace=thesis.tenant_id))


@router.put("/{thesis_id}/exclusions", response_model=ThesisDetail)
def put_exclusions(
    body: ExclusionList,
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
) -> ThesisDetail:
    """Persist the thesis's durable exclusion set (#7) — the operator's NO per name, with the
    optional "rejected because X". Full-list replace via the sole writer (the term_set structural
    guard, fourth application: a promote never touches the table, so a narrative edit can't wipe
    the pruning). THE #9 LINE: discovery never filters on this — a re-draft still surfaces every
    name; the EDITOR seeds these as visibly-grayed, one-click-reversible state."""
    # bound #2, fail-closed (the promote route's own guard): a caller-supplied security_id must be
    # an exact member of this tenant's master — never a junk row behind an FK error
    for e in body:
        if not master.exists(conn, e.security_id, tenant_id=DATA_TENANT_ID):
            raise HTTPException(
                status_code=404,
                detail=f"exclusion {e.ticker or e.security_id} references a security not in "
                "this tenant's master",
            )
    excl = [ExcludedName(security_id=e.security_id, ticker=e.ticker, reason=e.reason) for e in body]
    thesis_repo.set_exclusions(conn, thesis.id, excl, tenant_id=thesis.tenant_id)
    conn.commit()
    return ThesisDetail.from_thesis(thesis_repo.get(conn, thesis.id, workspace=thesis.tenant_id))


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
    if body.decision_date > market_today():
        raise HTTPException(
            status_code=422,
            detail="decision_date cannot be in the future — the log records decisions already made",
        )
    open_pos = decisions_repo.effective_position(conn, thesis, asof=market_today())
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
    the strip grays them, never hides them (pruning hides, it never vanishes)."""
    return read_decisions(conn, thesis)


def read_decisions(conn: psycopg.Connection, thesis: Thesis) -> list[DecisionOut]:
    """Canonical current operator log; intentionally not a historical fact read."""
    rows = decisions_repo.list_for_thesis(conn, thesis.id, tenant_id=thesis.tenant_id)
    voided = {r["voids"] for r in rows if r["action"] == "void" and r["voids"] is not None}
    return [_decision_out(r, voided=r["id"] in voided) for r in rows]
