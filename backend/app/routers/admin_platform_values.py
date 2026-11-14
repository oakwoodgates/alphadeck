"""The steward's PLATFORM VALUES routes (V2 W2c, ``docs/PLATFORM_VALUES.md``): a company's share count and cash/burn,
one value per company for everyone, read and decided by the steward.

- ``GET  /admin/platform-values/awaiting`` — the flagged candidates waiting for the steward, newest first;
- ``GET  /admin/platform-values/{security_id}`` — the company's two values: state, versions, candidates, decisions;
- ``POST /admin/platform-values/candidates/{candidate_id}/approve`` — apply a flagged candidate's own value;
- ``POST /admin/platform-values/{security_id}/correct`` — set the value by hand, citing the filing;
- ``POST /admin/platform-values/candidates/{candidate_id}/dismiss`` — close a candidate with a reason;
- ``POST /admin/platform-values/decisions/{decision_id}/undo`` — undo an approve, correct or dismiss;
- ``POST /admin/platform-values/{security_id}/pin`` and ``/unpin`` — review every future filing value by hand.

Every write answers the company's new state. The writes are ``platform_values.steward``'s (the company write lock,
the correction rule, append-only rows, the steward's email); this router only maps its errors to the error
contract (``app.errors``: a fixed sentence and a reference; the steward also sees the detail, which names the date
and filing that block a refused value).

Steward only (mounted with ``require_super_admin`` in ``app/main.py``). It acts on the DATA tenant, never on the
caller's workspace (``tests/app/test_workspace_isolation.py`` greps this file for the workspace dependency). Request models
carry the bounded types of ``domain/text_types.py``; response models carry no limits.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field, ValidationError

from app.auth import CurrentUser, require_super_admin
from app.deps import get_conn
from app.errors import PLATFORM_VALUE_NOT_FOUND, PLATFORM_VALUE_SENTENCES, fail
from app.routers.workbench import COMPANY_BUSY
from db.write_lock import CompanyLockBusy
from domain.extraction import LocatedPassage
from domain.links import HttpUrlOut
from domain.platform_values import DerivedState
from domain.text_types import AdsRatio, Money, Prose, RequestDate, Shares, UrlText
from platform_values import state, steward
from platform_values.steward import (
    PlatformValueConflict,
    PlatformValueInvalid,
    StewardOutcome,
)
from repositories import platform_value_repo as repo

_log = logging.getLogger("alphadeck.platform_values")

router = APIRouter(prefix="/admin/platform-values", tags=["admin"])

FactTypeIn = Literal["shares_outstanding", "cash_burn"]
AWAITING_LIMIT_MAX = 500


# --- response models (no limits: built from stored rows) ---------------------------------------------------------


class PlatformDecisionOut(BaseModel):
    """One steward decision: ``action`` ``approve`` / ``correct`` / ``dismiss`` / ``undo``; ``candidate_id`` the
    candidate it closes (approve, dismiss; a correct may name one); ``fact_id`` the fact version it wrote (approve,
    correct); ``revokes`` the decision an undo undoes; ``decided_by`` the steward's email; ``undone`` = a later undo
    names it."""

    id: UUID
    action: str
    candidate_id: UUID | None = None
    fact_id: UUID | None = None
    revokes: UUID | None = None
    reason: str | None = None
    decided_by: str
    recorded_at: datetime
    undone: bool = False


class PlatformCandidateOut(BaseModel):
    """One extractor candidate (``extraction_candidate``): ``outcome`` ``flag`` (a value awaiting the steward,
    with its ``flags``) or ``nothing`` (no value found; ``reason`` says why). ``decision`` = the standing decision
    that closes it, or None (open). ``located_passages`` = the filing excerpts behind it; a stored passage that
    does not read as one is counted in ``passages_unreadable``, never dropped silently."""

    id: UUID
    security_id: UUID
    fact_type: str
    outcome: str
    reason: str | None = None
    flags: list[str] = Field(default_factory=list)
    source: str | None = None
    source_ref: HttpUrlOut = None
    event_date: date | None = None
    shares: float | None = None
    cash_usd: float | None = None
    quarterly_burn_usd: float | None = None
    statement_currency: str | None = None
    ads_ratio: int | None = None
    ads_ratio_status: str | None = None
    filing_accession: str | None = None
    filing_accepted: datetime | None = None
    note: str | None = None
    located_passages: list[LocatedPassage] = Field(default_factory=list)
    passages_unreadable: int = 0
    recorded_at: datetime
    decision: PlatformDecisionOut | None = None


class PlatformAppliedValueOut(BaseModel):
    """One stored fact version: ``valid_from`` the filing's date, ``ratified_by`` who set it (``auto``, an email,
    or ``operator`` for a carried V1 row), ``vouched`` confirm/override provenance. ``source_ref`` is ``None`` on
    the wire unless it is a plain http(s) URL (``domain.links``)."""

    id: UUID
    source: str | None = None
    source_ref: HttpUrlOut = None
    valid_from: date
    recorded_at: datetime
    ratified_by: str | None = None
    vouched: str | None = None
    note: str | None = None
    shares: float | None = None
    ads_ratio: int | None = None
    ads_ratio_status: str | None = None
    filing_accession: str | None = None
    filing_accepted: datetime | None = None
    cash_usd: float | None = None
    quarterly_burn_usd: float | None = None


class PlatformVersionOut(PlatformAppliedValueOut):
    """Every stored version, oldest first; ``set_aside`` = an undone approve or correct wrote it, so every read
    skips it."""

    set_aside: bool = False


class PlatformValueOut(BaseModel):
    """One value of a company. ``state``: ``no_value`` (``reason`` says why) / ``from_filing`` / ``awaiting_steward``
    / ``verified``; ``ratifier`` ``machine`` / ``carried`` / ``steward``; ``value`` the value the meters show;
    ``awaiting`` the flag waiting when there is no value; ``pending`` a newer flag beside a shown value; ``pinned``
    the steward reviews every future filing value by hand. ``candidates`` (newest 20) and ``decisions`` (newest 50)
    are newest first."""

    fact_type: str
    state: str
    reason: str | None = None
    ratifier: str | None = None
    pinned: bool = False
    value: PlatformAppliedValueOut | None = None
    awaiting: PlatformCandidateOut | None = None
    pending: PlatformCandidateOut | None = None
    versions: list[PlatformVersionOut] = Field(default_factory=list)
    candidates: list[PlatformCandidateOut] = Field(default_factory=list)
    decisions: list[PlatformDecisionOut] = Field(default_factory=list)


class CompanyValuesOut(BaseModel):
    """A company's two platform values (the steward's company page)."""

    security_id: UUID
    ticker: str | None = None
    name: str | None = None
    shares: PlatformValueOut
    cash_burn: PlatformValueOut


class PlatformAwaitingItemOut(BaseModel):
    """One company-and-type with a flagged candidate waiting for the steward: no value shown (``state``
    ``awaiting_steward``), or the candidate is newer than the value shown (``value``)."""

    security_id: UUID
    ticker: str | None = None
    name: str | None = None
    fact_type: str
    state: str
    candidate: PlatformCandidateOut
    value: PlatformAppliedValueOut | None = None


class PlatformAwaitingOut(BaseModel):
    """The steward's queue, newest candidate first: ``items`` (at most ``limit``) and ``total`` (all waiting)."""

    items: list[PlatformAwaitingItemOut] = Field(default_factory=list)
    total: int = 0


# --- request models (bounded: domain/text_types.py) --------------------------------------------------------------


class PlatformApproveIn(BaseModel):
    """Optional ``note`` (stored on the fact and the decision; else the candidate's own note is kept)."""

    note: Prose | None = None


class _CorrectBase(BaseModel):
    """The filing the value comes from (``source_ref``, required) and its date (``event_date``: on or after the
    value shown and not after today), an optional ``note``, and an optional ``candidate_id`` the correction
    closes (this company's and type's, still open)."""

    source_ref: UrlText
    event_date: RequestDate
    note: Prose | None = None
    candidate_id: UUID | None = None


class PlatformCorrectSharesIn(_CorrectBase):
    """A share count: the ordinary count from the cover, and for an ADS name the ratio (``known``) or ``unread``."""

    fact_type: Literal["shares_outstanding"]
    shares: Shares
    ads_ratio: AdsRatio | None = None
    ads_ratio_status: Literal["known", "unread"] | None = None


class PlatformCorrectCashBurnIn(_CorrectBase):
    """Cash on hand and the quarterly burn (positive = cash used; zero or negative = cash-generative)."""

    fact_type: Literal["cash_burn"]
    cash_usd: Money
    quarterly_burn_usd: Money


PlatformCorrectIn = Annotated[
    PlatformCorrectSharesIn | PlatformCorrectCashBurnIn, Field(discriminator="fact_type")
]


class PlatformDismissIn(BaseModel):
    """Why the candidate is dismissed (required, not blank)."""

    reason: Prose


class PlatformUndoIn(BaseModel):
    """Why (optional)."""

    reason: Prose | None = None


class PlatformPinIn(BaseModel):
    """Which value (``fact_type``) and why (optional)."""

    fact_type: FactTypeIn
    note: Prose | None = None


# --- row -> response ---------------------------------------------------------------------------------------------


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _decision(d: Mapping[str, Any]) -> PlatformDecisionOut:
    return PlatformDecisionOut(
        id=d["id"],
        action=d["action"],
        candidate_id=d.get("candidate_id"),
        fact_id=d.get("fact_id"),
        revokes=d.get("revokes"),
        reason=d.get("reason"),
        decided_by=d["decided_by"],
        recorded_at=d["recorded_at"],
        undone=bool(d.get("undone")),
    )


def _passages(raw: Any) -> tuple[list[LocatedPassage], int]:
    out: list[LocatedPassage] = []
    unreadable = 0
    for p in raw or []:
        try:
            out.append(LocatedPassage.model_validate(p))
        except ValidationError:
            unreadable += 1
    return out, unreadable


def _candidate(
    c: Mapping[str, Any], decisions: Mapping[UUID, Mapping[str, Any]] | None = None
) -> PlatformCandidateOut:
    passages, unreadable = _passages(c.get("located_passages"))
    standing = (decisions or {}).get(c["id"])
    return PlatformCandidateOut(
        id=c["id"],
        security_id=c["security_id"],
        fact_type=c["fact_type"],
        outcome=c["outcome"],
        reason=c.get("reason"),
        flags=list(c.get("flags") or []),
        source=c.get("source"),
        source_ref=c.get("source_ref"),
        event_date=c.get("event_date"),
        shares=_f(c.get("shares")),
        cash_usd=_f(c.get("cash_usd")),
        quarterly_burn_usd=_f(c.get("quarterly_burn_usd")),
        statement_currency=c.get("statement_currency"),
        ads_ratio=c.get("ads_ratio"),
        ads_ratio_status=c.get("ads_ratio_status"),
        filing_accession=c.get("filing_accession"),
        filing_accepted=c.get("filing_accepted"),
        note=c.get("note"),
        located_passages=passages,
        passages_unreadable=unreadable,
        recorded_at=c["recorded_at"],
        decision=_decision(standing) if standing is not None else None,
    )


def _value_fields(v: Mapping[str, Any]) -> dict[str, Any]:
    return dict(
        id=v["id"],
        source=v.get("source"),
        source_ref=v.get("source_ref"),
        valid_from=v["valid_from"],
        recorded_at=v["recorded_at"],
        ratified_by=v.get("ratified_by"),
        vouched=v.get("vouched"),
        note=v.get("note"),
        shares=_f(v.get("shares")),
        ads_ratio=v.get("ads_ratio"),
        ads_ratio_status=v.get("ads_ratio_status"),
        filing_accession=v.get("filing_accession"),
        filing_accepted=v.get("filing_accepted"),
        cash_usd=_f(v.get("cash_usd")),
        quarterly_burn_usd=_f(v.get("quarterly_burn_usd")),
    )


def _standing(decisions: list[Mapping[str, Any]]) -> dict[UUID, Mapping[str, Any]]:
    """Candidate id -> its standing decision (the newest closing decision no undo names), from the newest-first
    list the company read carries."""
    out: dict[UUID, Mapping[str, Any]] = {}
    for d in decisions:
        if d["action"] != "undo" and not d.get("undone") and d.get("candidate_id") is not None:
            out.setdefault(d["candidate_id"], d)
    return out


def _type_out(tv: state.TypeValues, conn: psycopg.Connection) -> PlatformValueOut:
    st: DerivedState = tv.state
    standing = _standing(tv.decisions)
    for cand in (st.awaiting, st.pending, *tv.candidates):
        # a candidate decided by a decision older than the newest 50 still shows its decision
        if cand is not None and cand["id"] not in standing:
            d = repo.standing_decision_for_candidate(conn, cand["id"])
            if d is not None:
                standing[cand["id"]] = d
    return PlatformValueOut(
        fact_type=tv.fact_type,
        state=st.state.value,
        reason=st.reason,
        ratifier=st.ratifier,
        pinned=st.pinned,
        value=PlatformAppliedValueOut(**_value_fields(st.value)) if st.value is not None else None,
        awaiting=_candidate(st.awaiting, standing) if st.awaiting is not None else None,
        pending=_candidate(st.pending, standing) if st.pending is not None else None,
        versions=[
            PlatformVersionOut(**_value_fields(v), set_aside=bool(v.get("set_aside")))
            for v in tv.versions
        ],
        candidates=[_candidate(c, standing) for c in tv.candidates],
        decisions=[_decision(d) for d in tv.decisions],
    )


def _company_out(conn: psycopg.Connection, security_id: UUID) -> CompanyValuesOut:
    cv = state.read_company(conn, security_id)
    return CompanyValuesOut(
        security_id=cv.security_id,
        ticker=cv.ticker,
        name=cv.name,
        shares=_type_out(cv.shares, conn),
        cash_burn=_type_out(cv.cash_burn, conn),
    )


def _read(conn: psycopg.Connection, security_id: UUID, user: CurrentUser) -> CompanyValuesOut:
    try:
        return _company_out(conn, security_id)
    except LookupError as e:
        raise fail(404, PLATFORM_VALUE_NOT_FOUND, e, user=user) from e
    finally:
        conn.rollback()  # a read: end the transaction, hold no snapshot


def _act(
    conn: psycopg.Connection, user: CurrentUser, fn: Callable[[], StewardOutcome]
) -> CompanyValuesOut:
    """Run one steward action (it owns its transaction and the company lock), map its errors to the error
    contract, then answer the company's new state."""
    conn.rollback()  # end the request's read transaction: the action starts its own after the lock
    try:
        out = fn()
    except LookupError as e:
        raise fail(404, PLATFORM_VALUE_NOT_FOUND, e, user=user) from e
    except PlatformValueConflict as e:
        raise fail(409, PLATFORM_VALUE_SENTENCES[e.kind], e, user=user) from e
    except PlatformValueInvalid as e:
        raise fail(422, PLATFORM_VALUE_SENTENCES[e.kind], e, user=user) from e
    except CompanyLockBusy as e:
        raise fail(409, COMPANY_BUSY, e, user=user) from e
    return _read(conn, out.security_id, user)


# --- the routes --------------------------------------------------------------------------------------------------


@router.get("/awaiting", response_model=PlatformAwaitingOut)
def list_awaiting(
    limit: int = Query(200, ge=1, le=AWAITING_LIMIT_MAX),
    conn: psycopg.Connection = Depends(get_conn),
) -> PlatformAwaitingOut:
    """The flagged candidates waiting for the steward, newest first: a company-and-type whose newest candidate is
    an undecided flag, with no value shown (``awaiting_steward``) or dated after the value shown (``value``).
    Decided candidates and flags older than the value shown are not listed. ``total`` counts all. Read-only.
    """
    try:
        items = state.awaiting(conn, limit=10**9)
        return PlatformAwaitingOut(
            items=[
                PlatformAwaitingItemOut(
                    security_id=i.security_id,
                    ticker=i.ticker,
                    name=i.name,
                    fact_type=i.fact_type,
                    state=i.state.state.value,
                    candidate=_candidate(i.candidate),
                    value=(
                        PlatformAppliedValueOut(**_value_fields(i.state.value))
                        if i.state.value is not None
                        else None
                    ),
                )
                for i in items[:limit]
            ],
            total=len(items),
        )
    finally:
        conn.rollback()


@router.get("/{security_id}", response_model=CompanyValuesOut)
def get_company_values(
    security_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """The company's share count and cash/burn: each value's state, every stored version (with ``set_aside``),
    the newest candidates and decisions, the pin. **404** unknown company. Read-only."""
    return _read(conn, security_id, user)


@router.post("/candidates/{candidate_id}/approve", response_model=CompanyValuesOut)
def approve_candidate(
    candidate_id: UUID,
    body: PlatformApproveIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """APPROVE a flagged candidate: its own value (filing, date, ADS fields) becomes the company's value, stamped
    with your email, and the candidate is closed. Undo with ``POST .../decisions/{id}/undo``. **404** unknown
    candidate; **409** already decided, a value with a later date is shown (the detail names it), or the company
    is busy; **422** a "nothing" candidate, a flag without a complete value, or a date after today.
    """
    b = body or PlatformApproveIn()
    return _act(conn, user, lambda: steward.approve(conn, candidate_id, by=user.email, note=b.note))


@router.post("/{security_id}/correct", response_model=CompanyValuesOut)
def correct_value(
    security_id: UUID,
    body: PlatformCorrectIn,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """CORRECT the company's value by hand, citing the filing (``source_ref``) and its date (``event_date``),
    stamped with your email (``source`` ``ratified``). It is written only when it will be shown: dated on or after
    the value shown and not after today (approved decision D1 (a)). With ``candidate_id`` it also closes that
    candidate. Undo with ``POST .../decisions/{id}/undo``. **404** unknown company or candidate; **409** a value
    with a later date is shown (the detail names its date and filing), the candidate is decided or not this
    company's, or the company is busy; **422** a date after today or an ADS ratio without the status ``known``.
    """
    if isinstance(body, PlatformCorrectSharesIn):
        values: dict[str, Any] = {
            "shares": body.shares,
            "ads_ratio": body.ads_ratio,
            "ads_ratio_status": body.ads_ratio_status,
        }
    else:
        values = {"cash_usd": body.cash_usd, "quarterly_burn_usd": body.quarterly_burn_usd}
    return _act(
        conn,
        user,
        lambda: steward.correct(
            conn,
            security_id,
            body.fact_type,
            values,
            source_ref=body.source_ref,
            event_date=body.event_date,
            note=body.note,
            candidate_id=body.candidate_id,
            by=user.email,
        ),
    )


@router.post("/candidates/{candidate_id}/dismiss", response_model=CompanyValuesOut)
def dismiss_candidate(
    candidate_id: UUID,
    body: PlatformDismissIn,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """DISMISS a candidate with a reason (required): it is closed; the value is unchanged. Undo with
    ``POST .../decisions/{id}/undo``. **404** unknown candidate; **409** already decided, or the company is busy;
    **422** a blank reason."""
    return _act(
        conn,
        user,
        lambda: steward.dismiss(conn, candidate_id, by=user.email, reason=body.reason),
    )


@router.post("/decisions/{decision_id}/undo", response_model=CompanyValuesOut)
def undo_decision(
    decision_id: UUID,
    body: PlatformUndoIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """UNDO an approve, correct or dismiss: the value it wrote is set aside (every read falls back to the value
    before) and its candidate is open again. Nothing is deleted. **404** unknown decision; **409** the decision is
    itself an undo (make a new decision), it is already undone, or the company is busy."""
    reason = body.reason if body else None
    return _act(conn, user, lambda: steward.undo(conn, decision_id, by=user.email, reason=reason))


@router.post("/{security_id}/pin", response_model=CompanyValuesOut)
def pin_value(
    security_id: UUID,
    body: PlatformPinIn,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """PIN a value: from now on a newer clean filing value waits for you as a flagged candidate instead of being
    applied. Undo with ``/unpin``. **404** unknown company; **409** already pinned, or the company is busy.
    """
    return _act(
        conn,
        user,
        lambda: steward.pin(conn, security_id, body.fact_type, by=user.email, note=body.note),
    )


@router.post("/{security_id}/unpin", response_model=CompanyValuesOut)
def unpin_value(
    security_id: UUID,
    body: PlatformPinIn,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """UNPIN a value (the pin's inverse): a newer clean filing value is applied again. **404** unknown company;
    **409** not pinned, or the company is busy."""
    return _act(
        conn,
        user,
        lambda: steward.unpin(conn, security_id, body.fact_type, by=user.email, note=body.note),
    )
