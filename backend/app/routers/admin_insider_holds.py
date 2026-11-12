"""The Admin "Held insider rows" surface (bad dates, step 3) — the steward's queue for the Form 4 rows the ingest
HELD because the filing's transaction date cannot be true (``insider_txn_hold``, 0082), and the steward's
decisions about them (``insider_txn_hold_decision``, 0083), each with its visible inverse (WB#1).

Each held row sits in ONE bucket, judged by the same ladder the insider read runs
(``domain.insider_holds.hold_state``), so the panel never disagrees with what a call reads:

- ``owed`` — LOUD: no decision, not placed by the rule (a late filing, or one the rule cannot judge), not
  covered by the filer. Not counted until a steward decides.
- ``placed_by_rule`` — an on-time filing: counted from its filing night. A steward can still ratify a date or
  dismiss it (the decision outranks the rule).
- ``filer_corrected`` — the filer's own Form 4/A decides it (withdrawn, a duplicate, the row replaced, or the
  same trade restated): no action.
- ``decided`` / ``dismissed`` — a standing steward decision, each with its Undo.
- ``amendment_row`` — a held row of a Form 4/A: listed, no action (applying one is not built).

The SUGGESTED date is arithmetic on the filing's own fields (the written month and day, in the latest year on
or before the filing's market date — never a model), and ``agrees`` says whether it is on or after the filing's
period of report. It changes nothing until a steward approves it.

Every write is ONE append-only row, recorded now (on the public clock a steward decision counts from when it was
made — a past night's view is never rewritten), authored ``steward:<action>`` in ``basis``, the signed-in steward who
decided in ``detail.decided_by`` (user id and display name, never an email), and the evidence the steward saw
in ``detail``. Steward only (mounted with ``require_super_admin``); it acts on the DATA tenant,
never on the caller's workspace.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from app.auth import CurrentUser, require_super_admin
from app.deps import get_conn
from domain.insider_holds import (
    BUCKET_AMENDMENT_ROW,
    BUCKET_DECIDED,
    BUCKET_DISMISSED,
    BUCKET_FILER,
    BUCKET_OWED,
    BUCKET_PLACED,
    date_as_filed,
    reason_words,
)
from domain.settings import get_settings
from repositories import insider_hold_repo
from repositories.insider_hold_repo import HoldConflict, HoldItem

router = APIRouter(prefix="/admin/insider-holds", tags=["admin"])

_ACCESSION = r"^\d{10}-\d{2}-\d{6}$"


class AdminHeldRowOut(BaseModel):
    """The held Table I row as the filing reported it (``txn_date`` is not here: see ``date_as_filed``)."""

    insider_name: str | None = None
    insider_role: str | None = None
    txn_code: str | None = None
    shares: float | None = None
    price: float | None = None
    usd: float | None = None


class AdminHoldDeciderOut(BaseModel):
    """Who decided: the steward's user id and display name (never an email)."""

    user_id: UUID
    display_name: str


class AdminHoldDecisionOut(BaseModel):
    """One steward decision on a held row: ``action`` ``approve`` (counted at ``ratified_date``), ``dismiss``, or
    ``revoke`` (the Undo of the decision ``revokes`` names); ``recorded_at`` = when; ``reason`` = why;
    ``basis`` = its role (``steward:<action>``); ``decided_by`` = who (``null`` for a decision no signed-in user
    made)."""

    id: UUID
    action: str
    ratified_date: date | None = None
    reason: str
    recorded_at: datetime
    basis: str
    revokes: UUID | None = None
    decided_by: AdminHoldDeciderOut | None = None


class AdminInsiderHoldOut(BaseModel):
    """One held insider row and everything the steward needs to decide it.

    ``reason`` is the rule's code (``year_before_1934`` / ``year_after_filing_year`` / ``after_filing``) and
    ``reason_words`` the same in plain words. ``date_as_filed``, ``period_of_report_as_filed`` and
    ``signature_date_as_filed`` are the filing's own text, verbatim (any of them can itself be a typo: evidence,
    never a correction). ``filed`` / ``accepted`` = SEC's filing date and acceptance instant (``None`` when not
    known). ``bucket`` = where it sits (owed / placed_by_rule / filer_corrected / decided / dismissed /
    amendment_row); ``why`` = why the filer's own amendment decides it (``filer_corrected`` only);
    ``filing_night`` = the night it counts from when placed by the rule. ``suggested_date`` = the arithmetic
    suggestion (``None`` = none can be made) and ``agrees`` = it is on or after a readable period of report.
    ``decision`` = the standing steward decision (decided / dismissed); ``history`` = every decision about the
    row, Undos included, oldest first. ``filing_url`` = the filing on EDGAR.
    """

    hold_id: UUID
    security_id: UUID
    ticker: str | None = None
    company_name: str | None = None
    accession: str
    txn_seq: int
    form: str
    reason: str
    reason_words: str
    row: AdminHeldRowOut
    date_as_filed: str | None = None
    period_of_report_as_filed: str | None = None
    signature_date_as_filed: str | None = None
    filed: date | None = None
    accepted: datetime | None = None
    bucket: str
    why: str | None = None
    filing_night: date | None = None
    suggested_date: date | None = None
    agrees: bool = False
    decision: AdminHoldDecisionOut | None = None
    history: list[AdminHoldDecisionOut] = Field(default_factory=list)
    filing_url: str | None = None


class AdminInsiderHoldsOut(BaseModel):
    """The "Held insider rows" panel. ``owed`` is the only LOUD bucket (its count is ``counts.owed``); the rest
    are quiet lists, still visible (WB#2). ``counts`` = how many rows sit in each bucket; ``total`` = all held
    rows. Read live from the tables, judged as today's view reads."""

    owed: list[AdminInsiderHoldOut] = Field(default_factory=list)
    placed_by_rule: list[AdminInsiderHoldOut] = Field(default_factory=list)
    filer_corrected: list[AdminInsiderHoldOut] = Field(default_factory=list)
    decided: list[AdminInsiderHoldOut] = Field(default_factory=list)
    dismissed: list[AdminInsiderHoldOut] = Field(default_factory=list)
    amendment_row: list[AdminInsiderHoldOut] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    total: int = 0


class AdminHoldApproveIn(BaseModel):
    """The date to ratify (omit it to ratify the suggested date) and why (optional — a default is stored)."""

    ratified_date: date | None = None
    reason: str | None = Field(default=None, max_length=500)


class AdminHoldDismissIn(BaseModel):
    """Why the row is not a trade (required)."""

    reason: str = Field(min_length=1, max_length=500)


class AdminHoldRevokeIn(BaseModel):
    """Why (optional — a default is stored)."""

    reason: str | None = Field(default=None, max_length=500)


def _f(v: Any) -> float | None:
    try:
        return float(v) if v is not None and v != "" else None
    except (TypeError, ValueError):
        return None


def _filing_url(issuer_cik: str | None, accession: str) -> str | None:
    if not issuer_cik or not re.match(_ACCESSION, accession):
        return None
    return f"{get_settings().sec_archives_base}/{int(issuer_cik)}/{accession.replace('-', '')}/"


def _decider(detail: Any) -> AdminHoldDeciderOut | None:
    who = detail.get("decided_by") if isinstance(detail, dict) else None
    if not isinstance(who, dict) or not who.get("user_id"):
        return None
    return AdminHoldDeciderOut(user_id=who["user_id"], display_name=who.get("display_name") or "")


def _decision(d: Mapping[str, Any]) -> AdminHoldDecisionOut:
    return AdminHoldDecisionOut(
        id=d["id"],
        action=d["action"],
        ratified_date=d.get("ratified_date"),
        reason=d["reason"],
        recorded_at=d["recorded_at"],
        basis=d["basis"],
        revokes=d.get("revokes"),
        decided_by=_decider(d.get("detail")),
    )


def item_out(it: HoldItem) -> AdminInsiderHoldOut:
    """One repository item as the panel renders it."""
    h = it.hold
    r = h.get("row") if isinstance(h.get("row"), dict) else {}
    shares, price = _f(r.get("shares")), _f(r.get("price"))
    d = it.standing
    return AdminInsiderHoldOut(
        hold_id=h["id"],
        security_id=h["security_id"],
        ticker=it.ticker,
        company_name=it.name,
        accession=h["accession"],
        txn_seq=h["txn_seq"],
        form=h["form"],
        reason=h["reason"],
        reason_words=reason_words(h["reason"]),
        row=AdminHeldRowOut(
            insider_name=r.get("insider_name"),
            insider_role=r.get("insider_role"),
            txn_code=r.get("txn_code"),
            shares=shares,
            price=price,
            usd=(
                _f(r.get("usd"))
                if r.get("usd") is not None
                else (shares * price if shares is not None and price is not None else None)
            ),
        ),
        date_as_filed=date_as_filed(h),
        period_of_report_as_filed=h.get("period_of_report_as_filed"),
        signature_date_as_filed=h.get("signature_date_as_filed"),
        filed=h.get("filed"),
        accepted=h.get("accepted"),
        bucket=it.bucket,
        why=it.state.why,
        filing_night=it.state.day if it.bucket == BUCKET_PLACED else None,
        suggested_date=it.suggested,
        agrees=it.agrees,
        decision=_decision(d) if d is not None else None,
        history=[_decision(x) for x in it.decisions],
        filing_url=_filing_url(it.issuer_cik, h["accession"]),
    )


_LISTS = {
    BUCKET_OWED: "owed",
    BUCKET_PLACED: "placed_by_rule",
    BUCKET_FILER: "filer_corrected",
    BUCKET_DECIDED: "decided",
    BUCKET_DISMISSED: "dismissed",
    BUCKET_AMENDMENT_ROW: "amendment_row",
}


def holds_out(items: list[HoldItem]) -> AdminInsiderHoldsOut:
    """Bucket the items for the panel (``items`` is already ordered owed first, then ticker)."""
    out = AdminInsiderHoldsOut(total=len(items), counts={b: 0 for b in _LISTS.values()})
    for it in items:
        name = _LISTS[it.bucket]
        getattr(out, name).append(item_out(it))
        out.counts[name] += 1
    return out


def _commit(conn: psycopg.Connection, fn: Callable[[], object]) -> object:
    """One decision in its own transaction: commit on success; on ANY failure roll back and map the
    repository's errors to the steward-facing status (404 unknown, 409 conflict, 422 invalid)."""
    try:
        out = fn()
        conn.commit()
        return out
    except LookupError as e:
        conn.rollback()
        raise HTTPException(status_code=404, detail=str(e)) from e
    except HoldConflict as e:
        conn.rollback()
        raise HTTPException(status_code=409, detail=str(e)) from e
    except ValueError as e:
        conn.rollback()
        raise HTTPException(status_code=422, detail=str(e)) from e
    except BaseException:
        conn.rollback()
        raise


def _who(user: CurrentUser) -> dict[str, str]:
    """The signed-in steward a decision records (``detail.decided_by``) — id and display name, never the email."""
    return insider_hold_repo.decider(user.id, user.display_name)


def _one(conn: psycopg.Connection, hold_id: UUID) -> AdminInsiderHoldOut:
    try:
        return item_out(insider_hold_repo.one(conn, hold_id))
    except LookupError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    finally:
        conn.rollback()  # a read: end the transaction, hold no snapshot


@router.get("", response_model=AdminInsiderHoldsOut)
def list_holds(conn: psycopg.Connection = Depends(get_conn)) -> AdminInsiderHoldsOut:
    """Every held insider row with its evidence, bucketed for the panel: owed your decision (loud); placed by
    rule, corrected by the filer, decided, dismissed and held Form 4/A rows (quiet). Each row carries the date
    the filing wrote, its period of report and signature date, the suggested date and whether it agrees with
    the filing, and its standing decision. Read-only."""
    try:
        return holds_out(insider_hold_repo.items(conn))
    finally:
        conn.rollback()  # a read: end the transaction, hold no snapshot


@router.post("/{hold_id}/approve", response_model=AdminInsiderHoldOut)
def approve_hold(
    hold_id: UUID,
    body: AdminHoldApproveIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> AdminInsiderHoldOut:
    """RATIFY a date (basis ``steward:approve``): the held row counts from that date, for every read made from
    now on (a past night's view is not rewritten). Omit ``ratified_date`` to ratify the suggested date. Undo
    with ``POST /admin/insider-holds/decisions/{id}/revoke``. **404** unknown held row, **409** a decision
    already stands / the filer's own Form 4/A decides it / a Form 4/A row, **422** no date and no suggestion,
    a date that fails the year rule, or one after the filing's market date (or none is known)."""
    b = body or AdminHoldApproveIn()
    _commit(
        conn,
        lambda: insider_hold_repo.approve(
            conn, hold_id, b.ratified_date, b.reason, decided_by=_who(user)
        ),
    )
    return _one(conn, hold_id)


@router.post("/{hold_id}/dismiss", response_model=AdminInsiderHoldOut)
def dismiss_hold(
    hold_id: UUID,
    body: AdminHoldDismissIn,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> AdminInsiderHoldOut:
    """DISMISS the held row as not a trade (basis ``steward:dismiss``): it is not counted, for every read made
    from now on. A reason is required. Undo with the revoke route. **404** unknown held row, **409** a decision
    already stands / the filer's own Form 4/A decides it / a Form 4/A row, **422** no reason."""
    _commit(
        conn,
        lambda: insider_hold_repo.dismiss(conn, hold_id, body.reason, decided_by=_who(user)),
    )
    return _one(conn, hold_id)


@router.post("/decisions/{decision_id}/revoke", response_model=AdminInsiderHoldOut)
def revoke_hold_decision(
    decision_id: UUID,
    body: AdminHoldRevokeIn | None = Body(default=None),
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> AdminInsiderHoldOut:
    """UNDO a steward decision (basis ``steward:revoke``): the held row returns to the state before it (the
    placement rule, or owed). **404** unknown decision, **409** the decision is itself an Undo, or was already
    undone."""
    reason = body.reason if body else None
    result = _commit(
        conn, lambda: insider_hold_repo.revoke(conn, decision_id, reason, decided_by=_who(user))
    )
    _, hold_id = result  # type: ignore[misc]
    return _one(conn, hold_id)
