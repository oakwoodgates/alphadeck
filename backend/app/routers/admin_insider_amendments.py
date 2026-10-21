"""The Admin "Insider amendments" surface (R8-A / R8-B) — the Form 4/A amendments the nightly Form 4 leg stored
and linked: the few only the operator can resolve, and the many the system applied — every action with a
visible inverse (WB#1).

What the ingest found (deterministic, never a model's reading — #3): each 4/A as filed, the original Form 4 it
amends, and its DISPOSITION (``domain.insider_amendments``). R8-B (operator decision 2026-10-20): a 4/A the
linker resolves to a definite original is the filer's own legal correction and is APPLIED BY THE SYSTEM —
listed quietly as "Applied automatically" (basis ``system:filer-correction``, or ``system:no-signal-effect``
when it changes no row a call signal reads), never paged, each with its evidence (the amendment's remarks and
footnotes, its Table I rows beside the original's) and its exact Undo. A partial / consolidation / addition /
restatement / field change replaces only the original rows it ACCOUNTS for (``match_rows``: same owner, same
date or share count) — an original buy/sell it does not mention STANDS. Only what the system cannot resolve is
OWED the operator — an AMBIGUOUS link (several differing candidate originals), or an UNRESOLVED row match (a
buy/sell whose date AND share count both changed: restated, or an added trade?) — and only when it touches a
buy/sell row inside the review horizon (``insider_amendment_review_horizon_days``); an older one is "Unresolved
— historical (no call reads these rows)", grayed and collapsed (an ambiguous one still linkable). The clerical
/ zero-row / unlinked ones change nothing.

What the operator can do here, and its inverse:

- **Keep original** -> a ``keep`` decision: the original stands, the amendment changes nothing (the AZTA
  power-of-attorney 4/A). On an applied-automatically amendment this IS the Undo of the system's application:
  the keep stands over it, and undoing the keep restores it.
- **Link to candidate** (an ambiguous amendment) -> a ``link`` decision naming the original; its disposition
  is computed against that original then and stored, and the amendment then waits for the operator's verdict.
- **Ratify void** / **Apply amendment** -> an ``apply`` decision, on an amendment that is still the
  operator's (an operator-linked one, or one the operator took back with an Undo).
- **Undo** -> a ``revoke`` row naming the operator's decision. A link is undone only after the verdict under it.

Owed items are ordered by URGENCY: the ones whose changed signal rows sit inside a signal's live read horizon
today come first. Every write is append-only, names its author (``basis``), and runs in one transaction under
a lock on the amendment. The change reaches calls at the next read (a live call reads ``known_at = now``) and
re-records a card only where the resolved rows actually changed. Auth stays deferred project-wide.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException, Path
from pydantic import BaseModel, Field

from app.deps import get_conn
from domain.insider_amendments import (
    ACTION_APPLY,
    ACTION_KEEP,
    ACTION_LINK,
    REVIEW_AUTO,
    REVIEW_DECIDED,
    REVIEW_HISTORICAL,
    REVIEW_OWED,
    REVIEW_QUEUED,
)
from domain.market_time import market_today
from domain.settings import get_settings
from repositories import insider_amendment_repo
from repositories.insider_amendment_repo import AmendmentConflict, AmendmentItem

router = APIRouter(prefix="/admin/insider-amendments", tags=["admin"])

BASIS_APPLY = "operator:apply"
BASIS_KEEP = "operator:keep"
BASIS_LINK = "operator:link"
BASIS_REVOKE = "operator:revoke"
_DEFAULT_REASON = {
    ACTION_APPLY: "ratified by the operator",
    ACTION_KEEP: "original kept by the operator",
    ACTION_LINK: "original picked by the operator",
    "revoke": "undone by the operator",
}
_ACCESSION = r"^\d{10}-\d{2}-\d{6}$"


class AdminInsiderTxnOut(BaseModel):
    """One Table I row — an amendment's as filed, or a stored original's."""

    seq: int | None = None
    insider_name: str | None = None
    txn_code: str | None = None
    txn_date: date | None = None
    shares: float | None = None
    price: float | None = None
    usd: float | None = None


class AdminInsiderOriginalOut(BaseModel):
    """A stored Form 4 the amendment touches, with its rows: ``role`` = ``original`` (what it amends),
    ``voided`` (a duplicate a 1->N void suppresses) or ``candidate`` (an ambiguous link's option).
    """

    accession: str
    role: str
    rows: list[AdminInsiderTxnOut] = Field(default_factory=list)


class AdminInsiderDecisionOut(BaseModel):
    """One operator decision (``apply`` / ``keep`` / ``link`` / ``revoke``); ``standing`` = not undone."""

    id: UUID
    action: str
    target_accession: str | None = None
    disposition: str | None = None
    reason: str
    basis: str
    recorded_at: datetime
    revokes: UUID | None = None
    standing: bool = False


class AdminInsiderAmendmentOut(BaseModel):
    """One Form 4/A and everything the operator needs to ratify it.

    ``disposition`` is what it changed (``domain.insider_amendments``); ``status`` its EFFECTIVE state —
    ``pending`` (not applied: an ambiguous link, an operator-linked one awaiting a verdict, one the operator
    took back, or a pre-R8-B link awaiting the nightly sweep; a pending void/duplicates SUPPRESSES its target
    meanwhile), ``applied`` (by the system, or ratified), ``kept`` (the original stands) or ``inert``.
    ``review`` (R8-B) is where it sits for the operator: ``owed`` / ``auto`` (applied automatically) /
    ``decided`` (a standing operator verdict) / ``historical`` (ambiguous, but no call reads the rows it
    touches) / ``queued`` (the next nightly sweep applies it) / ``inert``. ``suppressing`` = the original
    accession(s) it currently removes from every read; ``undo_id`` = the standing OPERATOR decision an Undo
    revokes (the verdict first, else the link) — ``None`` on an applied-automatically one, whose Undo is
    ``keep`` ("keep the original"). ``system_basis`` = the basis of the standing system application
    (``system:filer-correction`` / ``system:no-signal-effect``; ``None`` for a correction applied by its link
    alone). ``touches_buy`` = a code-P row on either side. ``filing_url`` = the 4/A's EDGAR filing index.

    ``signal_effect`` = its effect changes a row a call signal reads (a code a registered detector declares,
    compared with the owner's role / name and the 10b5-1 flag); ``auto_resolved`` = applied by the system
    (``review == "auto"``); ``in_window`` = the signal codes whose changed rows sit inside a signal's live read
    horizon today (it can move tonight's call); ``in_review`` = a changed signal row sits inside the review
    horizon today (an ambiguous link is owed only then). R8-B's row match (a partial / consolidation / addition
    / restatement / field change): ``kept_rows`` = the original rows (``"<accession>#<seq>"``) the amendment does
    NOT account for — they stand, the filer never retracted them; ``unresolved`` = a buy/sell whose date AND
    share count both changed (restated, or an added trade? — owed to the operator, the original standing).
    """

    security_id: UUID
    ticker: str | None = None
    accession: str
    filing_url: str | None = None
    filed: date | None = None
    accepted: datetime | None = None
    recorded_at: datetime
    owners: list[str] = Field(default_factory=list)
    date_of_original_submission: date | None = None
    remarks: str | None = None
    footnotes: list[str] = Field(default_factory=list)
    derivative_count: int = 0
    disposition: str
    method: str
    status: str
    verdict: str | None = None
    why: str = ""
    original_accessions: list[str] = Field(default_factory=list)
    voided_accessions: list[str] = Field(default_factory=list)
    candidate_accessions: list[str] = Field(default_factory=list)
    suppressing: list[str] = Field(default_factory=list)
    amendment_rows: list[AdminInsiderTxnOut] = Field(default_factory=list)
    originals: list[AdminInsiderOriginalOut] = Field(default_factory=list)
    decisions: list[AdminInsiderDecisionOut] = Field(default_factory=list)
    undo_id: UUID | None = None
    touches_buy: bool = False
    signal_effect: bool = True
    auto_resolved: bool = False
    in_window: list[str] = Field(default_factory=list)
    review: str = REVIEW_OWED
    system_basis: str | None = None
    in_review: bool = False
    unresolved: bool = False
    kept_rows: list[str] = Field(default_factory=list)


class AdminInsiderAmendmentsOut(BaseModel):
    """The "Insider amendments" panel (R8-B). ``owed`` is LOUD — only what the system cannot resolve and a call
    can read: an AMBIGUOUS link or an UNRESOLVED row match touching a buy/sell row inside the review horizon
    (plus an operator-linked one awaiting its verdict, or one the operator took back), the IN-WINDOW ones first. The rest are QUIET lists,
    still visible (#9 / WB#2): ``auto`` (applied automatically — the filer's own correction resolved to its
    original, or one that changes no signal row; each with its evidence and its Undo, Keep original),
    ``decided`` (a standing operator verdict, each undoable), ``historical`` (ambiguous or unresolved, but no
    call reads the rows it touches — the originals stand; an ambiguous one still linkable), ``queued`` (a pre-R8-B link the next nightly sweep
    applies; a void suppresses meanwhile) and ``inert`` (clerical / no Table I rows / no stored original).
    ``asof`` is the market day urgency and the review horizon were judged at; ``review_horizon_days`` the
    horizon in force (``None`` = unbounded). Read live from the tables."""

    owed: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    auto: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    decided: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    historical: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    queued: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    inert: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    total: int = 0
    asof: date | None = None
    review_horizon_days: int | None = None


class AdminInsiderReasonIn(BaseModel):
    """Why (optional — a default reason is stored)."""

    reason: str | None = Field(default=None, max_length=500)


class AdminInsiderLinkIn(BaseModel):
    """The candidate original the operator links an ambiguous amendment to."""

    target_accession: str = Field(pattern=_ACCESSION)
    reason: str | None = Field(default=None, max_length=500)


def _f(v: Any) -> float | None:
    return float(v) if v is not None and v != "" else None


def _txn(r: dict[str, Any]) -> AdminInsiderTxnOut:
    d = r.get("valid_from", r.get("txn_date"))
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return AdminInsiderTxnOut(
        seq=r.get("txn_seq"),
        insider_name=r.get("insider_name"),
        txn_code=r.get("txn_code"),
        txn_date=d,
        shares=_f(r.get("shares")),
        price=_f(r.get("price")),
        usd=(
            _f(r.get("usd"))
            if r.get("usd") is not None
            else ((_f(r.get("shares")) or 0.0) * (_f(r.get("price")) or 0.0))
        ),
    )


def _filing_url(issuer_cik: str | None, accession: str) -> str | None:
    if not issuer_cik or not re.match(_ACCESSION, accession):
        return None
    return f"{get_settings().sec_archives_base}/{int(issuer_cik)}/{accession.replace('-', '')}/"


def item_out(item: AmendmentItem) -> AdminInsiderAmendmentOut:
    """One repository item as the panel renders it."""
    eff = item.effective
    revoked = {
        str(d["revokes"]) for d in item.decisions if d["action"] == "revoke" and d["revokes"]
    }
    decisions = [
        AdminInsiderDecisionOut(
            id=d["id"],
            action=d["action"],
            target_accession=d.get("target_accession"),
            disposition=d.get("disposition"),
            reason=d["reason"],
            basis=d["basis"],
            recorded_at=d["recorded_at"],
            revokes=d.get("revokes"),
            standing=d["action"] != "revoke" and str(d["id"]) not in revoked,
        )
        for d in item.decisions
    ]
    roles: dict[str, str] = {}
    for a in eff.candidate_accessions:
        roles[a] = "candidate"
    for a in eff.voided_accessions:
        roles[a] = "voided"
    for a in eff.original_accessions:
        roles[a] = "original"
    originals = [
        AdminInsiderOriginalOut(
            accession=a, role=roles.get(a, "candidate"), rows=[_txn(r) for r in rows]
        )
        for a, rows in item.originals.items()
    ]
    return AdminInsiderAmendmentOut(
        security_id=item.security_id,
        ticker=item.ticker,
        accession=item.accession,
        filing_url=_filing_url(item.issuer_cik, item.accession),
        filed=item.filed,
        accepted=item.accepted,
        recorded_at=item.recorded_at,
        owners=[str(o.get("name") or o.get("cik") or "?") for o in item.owners],
        date_of_original_submission=item.date_of_original_submission,
        remarks=item.remarks,
        footnotes=[str(f.get("text") or "") for f in item.footnotes if f.get("text")],
        derivative_count=item.derivative_count,
        disposition=eff.disposition,
        method=eff.method,
        status=eff.status,
        verdict=eff.verdict,
        why=item.why,
        original_accessions=list(eff.original_accessions),
        voided_accessions=list(eff.voided_accessions),
        candidate_accessions=list(eff.candidate_accessions),
        suppressing=sorted(set(eff.suppress)),
        amendment_rows=[_txn(r) for r in item.rows],
        originals=originals,
        decisions=decisions,
        # the Undo of a SYSTEM application is Keep original (a keep stacked over it), never a revoke of the
        # system's record — so no revoke target is advertised for it (R8-B)
        undo_id=(None if item.review == REVIEW_AUTO else (eff.verdict_id or eff.link_decision_id)),
        touches_buy=item.touches_buy,
        signal_effect=item.signal_effect,
        auto_resolved=item.auto_resolved,
        in_window=list(item.in_window),
        review=item.review,
        system_basis=item.system_basis,
        in_review=item.in_review,
        unresolved=item.unresolved,
        kept_rows=list(item.kept_rows),
    )


_BUCKETS = {
    REVIEW_OWED: "owed",
    REVIEW_AUTO: "auto",
    REVIEW_DECIDED: "decided",
    REVIEW_HISTORICAL: "historical",
    REVIEW_QUEUED: "queued",
}


def amendments_out(
    items: list[AmendmentItem],
    *,
    asof: date | None = None,
    review_horizon_days: int | None = None,
) -> AdminInsiderAmendmentsOut:
    """Bucket the items for the panel by their R8-B ``review``: owed (loud, in-window first — ``items`` already
    orders by urgency) / auto / decided / historical / queued / inert (quiet)."""
    out = AdminInsiderAmendmentsOut(
        total=len(items), asof=asof, review_horizon_days=review_horizon_days
    )
    for it in items:
        getattr(out, _BUCKETS.get(it.review, "inert")).append(item_out(it))
    return out


def _commit(conn: psycopg.Connection, fn: Callable[[], object]) -> object:
    """One decision in its own transaction: commit on success; on ANY failure roll back and map the
    repository's errors to the operator-facing status (404 unknown, 409 conflict, 422 invalid)."""
    try:
        out = fn()
        conn.commit()
        return out
    except LookupError as e:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    except AmendmentConflict as e:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    except BaseException:
        conn.rollback()
        raise


def _one(conn: psycopg.Connection, security_id: UUID, accession: str) -> AdminInsiderAmendmentOut:
    for it in insider_amendment_repo.items(conn, security_ids=[security_id], asof=market_today()):
        if it.accession == accession:
            return item_out(it)
    raise HTTPException(
        status_code=404, detail=f"no Form 4/A {accession} for security {security_id}"
    )


@router.get("", response_model=AdminInsiderAmendmentsOut)
def list_amendments(conn: psycopg.Connection = Depends(get_conn)) -> AdminInsiderAmendmentsOut:
    """Every stored Form 4/A with its link, disposition, effective status and evidence, bucketed for the
    panel: owed loud (an ambiguous amendment only you can resolve that touches a buy or sale a call can read —
    the ones inside today's signal windows first); applied automatically / decided / unresolved — historical /
    queued / inert quiet. Read-only."""
    asof = market_today()
    horizon = insider_amendment_repo.review_horizon_days()
    try:
        return amendments_out(
            insider_amendment_repo.items(conn, asof=asof, review_days=horizon),
            asof=asof,
            review_horizon_days=horizon,
        )
    finally:
        conn.rollback()  # a read: end the transaction, hold no snapshot


def _verdict(
    conn: psycopg.Connection,
    security_id: UUID,
    accession: str,
    action: str,
    body: AdminInsiderReasonIn | None,
) -> AdminInsiderAmendmentOut:
    reason = (body.reason if body and body.reason else None) or _DEFAULT_REASON[action]
    basis = BASIS_APPLY if action == ACTION_APPLY else BASIS_KEEP
    _commit(
        conn,
        lambda: insider_amendment_repo.decide(
            conn, security_id, accession, action, reason=reason, basis=basis
        ),
    )
    return _one(conn, security_id, accession)


@router.post("/{security_id}/{accession}/apply", response_model=AdminInsiderAmendmentOut)
def apply_amendment(
    security_id: UUID,
    accession: str = Path(pattern=_ACCESSION),
    body: AdminInsiderReasonIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminInsiderAmendmentOut:
    """RATIFY a pending amendment that is the operator's (basis ``operator:apply``) — one linked by the
    operator, or one the operator took back: a void / duplicates keeps suppressing its target (now ratified);
    a partial / consolidation / addition / restatement replaces the original's rows with the amendment's. Undo
    with ``POST /admin/insider-amendments/decisions/{id}/revoke``. **404** unknown amendment, **409** nothing
    to ratify / the system already applied it / a decision already stands / ambiguous and not linked yet.
    """
    return _verdict(conn, security_id, accession, ACTION_APPLY, body)


@router.post("/{security_id}/{accession}/keep", response_model=AdminInsiderAmendmentOut)
def keep_original(
    security_id: UUID,
    accession: str = Path(pattern=_ACCESSION),
    body: AdminInsiderReasonIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminInsiderAmendmentOut:
    """KEEP THE ORIGINAL (basis ``operator:keep``): the amendment changes nothing — a pending void's
    suppression is lifted, and an amendment APPLIED AUTOMATICALLY by the system is overruled (this is that
    application's Undo: the keep stands over it, and undoing the keep with the revoke route restores it).
    **404** unknown amendment, **409** nothing to decide / an operator decision already stands."""
    return _verdict(conn, security_id, accession, ACTION_KEEP, body)


@router.post("/{security_id}/{accession}/link", response_model=AdminInsiderAmendmentOut)
def link_amendment_to_candidate(
    body: AdminInsiderLinkIn,
    security_id: UUID,
    accession: str = Path(pattern=_ACCESSION),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminInsiderAmendmentOut:
    """LINK an ambiguous amendment to one of its candidate originals (basis ``operator:link``). The
    disposition against that original is computed now and stored; a routed one is then pending ratification
    (a void suppresses meanwhile). Undo with the revoke route. **404** unknown amendment, **409** not
    ambiguous / already linked / not a candidate."""
    reason = body.reason or _DEFAULT_REASON[ACTION_LINK]
    _commit(
        conn,
        lambda: insider_amendment_repo.link_candidate(
            conn, security_id, accession, body.target_accession, reason=reason, basis=BASIS_LINK
        ),
    )
    return _one(conn, security_id, accession)


@router.post("/decisions/{decision_id}/revoke", response_model=AdminInsiderAmendmentOut)
def revoke_decision(
    decision_id: UUID,
    body: AdminInsiderReasonIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
) -> AdminInsiderAmendmentOut:
    """UNDO a standing decision — append a ``revoke`` row naming it (basis ``operator:revoke``); the
    amendment returns to the state before it (undoing a Keep original that stood over a system application
    restores that application). Revoking the SYSTEM's own application makes the amendment the operator's (owed;
    the nightly sweep never re-applies an amendment with a decision history). **404** unknown / already
    undone, **409** a verdict still stands under the link being undone."""
    reason = (body.reason if body and body.reason else None) or _DEFAULT_REASON["revoke"]
    row = _commit(
        conn,
        lambda: insider_amendment_repo.revoke(conn, decision_id, reason=reason, basis=BASIS_REVOKE),
    )
    return _one(conn, row["security_id"], row["amendment_accession"])  # type: ignore[index]
