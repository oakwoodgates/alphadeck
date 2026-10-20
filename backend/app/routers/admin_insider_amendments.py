"""The Admin "Insider amendments" surface (R8-A) — the operator's ratification of the Form 4/A amendments the
nightly Form 4 leg stored and linked, every action with a visible inverse (WB#1).

What the ingest found (read, never auto-decided — #3): each 4/A as filed, the original Form 4 it amends, and
its DISPOSITION (``domain.insider_amendments``). The clean 1:1 corrections were applied automatically and are
listed quietly; the clerical / zero-row / unlinked ones change nothing and are a quiet count; everything
else is OWED a decision and listed loudly with its evidence — the amendment's remarks and footnotes, and
its Table I rows beside the original's.

What the operator can do here, and its inverse:

- **Ratify void** (a void / duplicates) or **Apply amendment** (a partial / consolidation / addition /
  restatement) -> an ``apply`` decision. A void SUPPRESSED the original while it was pending (the operator's
  condition — a cancelled buy never fires); ratifying keeps it suppressed.
- **Keep original** -> a ``keep`` decision: the original stands, the amendment changes nothing (the AZTA
  power-of-attorney 4/A). Also offered on an auto-applied correction, to overrule it.
- **Link to candidate** (an ambiguous amendment) -> a ``link`` decision naming the original; its disposition
  is computed against that original then and stored.
- **Undo** -> a ``revoke`` row naming the decision. A link is undone only after the verdict under it.

Resolved by the SYSTEM (the signal-effect rule, operator-approved 2026-10-18): a routed amendment that changes
no row a call signal reads is applied at ingest by an ``apply`` decision with basis ``system:no-signal-effect``
— listed quietly as "applied — no signal effect", never paged, and its **Undo** (the same revoke) returns it
to the operator queue. The pending list is ordered by URGENCY: the items whose changed signal rows sit inside
a signal's live read horizon today come first.

Every write is append-only, names the operator as its author (``basis``), and runs in one transaction under a
lock on the amendment. The change reaches calls at the next read (a live call reads ``known_at = now``) and
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
    AUTO_DISPOSITIONS,
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
    ``pending`` (owed a decision; a pending void/duplicates SUPPRESSES its target meanwhile), ``applied``
    (auto-applied correction, or ratified), ``kept`` (the original stands) or ``inert`` (nothing to do).
    ``suppressing`` = the original accession(s) it currently removes from every read; ``undo_id`` = the
    standing decision an Undo reverses (the verdict first, else the link). ``touches_buy`` = a code-P row on
    either side (the loudness hint). ``filing_url`` = the 4/A's EDGAR filing index.

    The signal-effect rule (2026-10-18): ``signal_effect`` = its effect changes a row a call signal reads (a
    code a registered detector declares, compared with the owner's role / name and the 10b5-1 flag);
    ``auto_resolved`` = the SYSTEM applied it because it has none (basis ``system:no-signal-effect`` — its Undo
    returns it to the operator queue); ``in_window`` = the signal codes whose changed rows sit inside a
    signal's live read horizon today (the detectors' own horizons) — it can move tonight's call."""

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


class AdminInsiderAmendmentsOut(BaseModel):
    """The "Insider amendments" panel. ``pending`` is LOUD (owed a decision — the IN-WINDOW ones first, those
    that can move tonight's call); ``decided`` (a standing operator decision, each undoable), ``auto``
    ("applied — no signal effect": resolved by the system because no call signal reads a row it changes, each
    with its Undo), ``applied`` (auto-applied 1:1 corrections, each overrulable with Keep original) and
    ``inert`` (clerical / no Table I rows / no stored original — nothing to do) are QUIET lists, still visible
    (#9 / WB#2). ``asof`` is the market day the in-window urgency was judged at. Read live from the tables.
    """

    pending: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    decided: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    auto: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    applied: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    inert: list[AdminInsiderAmendmentOut] = Field(default_factory=list)
    total: int = 0
    asof: date | None = None


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
        undo_id=eff.verdict_id or eff.link_decision_id,
        touches_buy=item.touches_buy,
        signal_effect=item.signal_effect,
        auto_resolved=item.auto_resolved,
        in_window=list(item.in_window),
    )


def amendments_out(
    items: list[AmendmentItem], *, asof: date | None = None
) -> AdminInsiderAmendmentsOut:
    """Bucket the items for the panel: pending (loud, in-window first — ``items`` already orders by urgency) /
    decided / auto (no signal effect) / applied / inert (quiet)."""
    out = AdminInsiderAmendmentsOut(total=len(items), asof=asof)
    for it in items:
        o = item_out(it)
        if o.status == "pending":
            out.pending.append(o)
        elif o.auto_resolved:
            out.auto.append(o)
        elif o.undo_id is not None:
            out.decided.append(o)
        elif o.disposition in AUTO_DISPOSITIONS and o.status == "applied":
            out.applied.append(o)
        else:
            out.inert.append(o)
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
    panel (pending loud, the ones inside today's signal windows first; decided / applied — no signal effect /
    applied / inert quiet). Read-only."""
    asof = market_today()
    try:
        return amendments_out(insider_amendment_repo.items(conn, asof=asof), asof=asof)
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
    """RATIFY a pending amendment (basis ``operator:apply``): a void / duplicates keeps suppressing its
    target (now ratified); a partial / consolidation / addition / restatement replaces the original's rows
    with the amendment's. Undo with ``POST /admin/insider-amendments/decisions/{id}/revoke``. **404** unknown
    amendment, **409** nothing to ratify / a decision already stands / ambiguous and not linked yet.
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
    suppression is lifted, an auto-applied correction is overruled. Undo with the revoke route. **404**
    unknown amendment, **409** nothing to decide / a decision already stands."""
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
    amendment returns to the state before it. **404** unknown / already undone, **409** a verdict still
    stands under the link being undone."""
    reason = (body.reason if body and body.reason else None) or _DEFAULT_REASON["revoke"]
    row = _commit(
        conn,
        lambda: insider_amendment_repo.revoke(conn, decision_id, reason=reason, basis=BASIS_REVOKE),
    )
    return _one(conn, row["security_id"], row["amendment_accession"])  # type: ignore[index]
