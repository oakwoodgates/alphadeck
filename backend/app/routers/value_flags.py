"""A user's FLAG on a platform value (V2 W5b; ``docs/VALUE_FLAGS.md``): "this shared share count or
cash/burn looks wrong (or is missing)", told to the steward. A flag never changes a value; only its user and the
steward ever see it.

- ``POST /value-flags`` — raise a flag on one value of a company on one of your own cards (201);
- ``GET  /value-flags`` — your own flags, open first then the newest closed, paged;
- ``POST /value-flags/{flag_id}/withdraw`` — withdraw your own open flag (never refunds the allowance).

The order of refusals on a raise: not signed in 401 (the router's gate); the steward 403 (``VALUE_FLAG_STEWARD``:
the steward corrects on the company page); a request that does not parse 422 (FastAPI, before anything is read: a
value type other than a share count or cash/burn, a note over ``PROSE_MAX``); a company unknown OR not on any of the
caller's own cards 404 with ONE body (``NOT_ON_YOUR_CARDS``, read in the caller's workspace only, before any flag
or allowance read, so it never says whether the company is on anyone else's card); an open flag by this user on
this value 409 (nothing spent); the day's ``flag`` allowance spent 429 (``allowance.over_message``). The request
carries only the company, the value type and an optional note: the server records the user and what was current.

The lock, the duplicate check, the charge and the insert run in ONE transaction (``platform_values.flags``), which
the route commits once; a refusal rolls it back, so a refused request spends nothing and two simultaneous identical
requests make one row, one unit and one Slack message. The message (``notify.flags``) is built from what the
transaction read and sent by a background task, after the commit and after the answer; Slack's failure never
changes the answer.

Mounted with ``_USER`` (``app/main.py``); raise and withdraw refuse the steward. No ``asof``: a flag is about the
value now. Request models carry the bounded types of ``domain/text_types.py``; response models carry no limits.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel

from app import allowance
from app.auth import CurrentUser, require_user
from app.deps import get_conn, get_workspace
from app.errors import (
    VALUE_FLAG_NOT_FOUND,
    VALUE_FLAG_SENTENCES,
    VALUE_FLAG_STEWARD,
)
from app.routers.workbench import _require_own_security
from domain.settings import get_settings
from domain.text_types import Prose
from notify import flags as flag_notify
from platform_values import flags
from repositories import platform_value_repo as pv_repo

router = APIRouter(prefix="/value-flags", tags=["value-flags"])

# The user's own list: a page of 1-100 flags (default 50), from an offset of 0-100,000.
FLAGS_LIMIT_DEFAULT = 50
FLAGS_LIMIT_MAX = 100
FLAGS_OFFSET_MAX = 100_000


class ValueFlagIn(BaseModel):
    """Raise a flag. Only these three fields are read: the user, what was current and the state of the flag are
    the server's (any other field in the body is ignored)."""

    security_id: UUID
    value_type: Literal["shares_outstanding", "cash_burn"]
    note: Prose | None = None


class ValueFlagOut(BaseModel):
    """One of the caller's flags, from the stored row. ``current_when_flagged``: what the server recorded as current
    when it was raised (``value`` = the value shown, ``awaiting`` = a value awaiting the steward, ``none`` = no value
    yet). ``outcome``: ``fixed`` / ``kept`` (the steward, with ``outcome_note`` for the user) or ``withdrawn``
    (the user); null while open. ``reopens``: the closed flag of yours the steward reopened as this one. Never who
    closed it. ``note`` and ``outcome_note`` are plain text, exactly as stored."""

    id: UUID
    security_id: UUID
    ticker: str | None
    name: str | None
    value_type: str
    note: str | None
    opened_at: datetime
    closed_at: datetime | None
    outcome: str | None
    outcome_note: str | None
    current_when_flagged: Literal["value", "awaiting", "none"]
    reopens: UUID | None


class ValueFlagListOut(BaseModel):
    """One page of the caller's own flags; ``total`` counts all of them."""

    items: list[ValueFlagOut]
    total: int


def _plain_user_only(user: CurrentUser = Depends(require_user)) -> CurrentUser:
    """A flag is a user's message to the steward: the steward corrects the value on the company page instead."""
    if user.is_super_admin:
        raise HTTPException(status_code=403, detail=VALUE_FLAG_STEWARD)
    return user


def _out(row: dict, company: dict | None = None) -> ValueFlagOut:
    company = company or {}
    return ValueFlagOut(
        id=row["id"],
        security_id=row["security_id"],
        ticker=row.get("ticker", company.get("ticker")),
        name=row.get("name", company.get("name")),
        value_type=row["value_type"],
        note=row["note"],
        opened_at=row["opened_at"],
        closed_at=row["closed_at"],
        outcome=row["outcome"],
        outcome_note=row["outcome_note"],
        current_when_flagged=flags.seen_word(row),
        reopens=row.get("reopens"),
    )


def _refusal(exc: flags.FlagError) -> HTTPException:
    if isinstance(exc, flags.FlagNotFound):
        return HTTPException(status_code=404, detail=VALUE_FLAG_NOT_FOUND)
    if isinstance(exc, flags.FlagAllowanceSpent):
        return HTTPException(status_code=429, detail=allowance.over_message("flag"))
    status = 422 if isinstance(exc, flags.FlagInvalid) else 409
    return HTTPException(status_code=status, detail=VALUE_FLAG_SENTENCES[exc.kind])


@router.post("", response_model=ValueFlagOut, status_code=201)
def raise_flag(
    body: ValueFlagIn,
    background: BackgroundTasks,
    user: CurrentUser = Depends(_plain_user_only),
    conn: psycopg.Connection = Depends(get_conn),
    workspace: UUID = Depends(get_workspace),
) -> ValueFlagOut:
    """Tell the steward that one of a company's shared values (its share count, or its cash and burn) looks wrong
    or is missing. It never changes the value. The company must be on one of your own cards. The server records
    what was current: the value shown, a value awaiting the steward, or no value yet. One open flag per value;
    each flag spends one unit of your daily flag allowance (a withdrawal does not give it back). **403** for the
    steward; **404** a company that is not on any of your cards (or no such company); **409** you already have an
    open flag on this value; **422** a value type other than a share count or cash/burn, or a note over 2,000
    characters; **429** the day's flags are spent. Answers the new flag (201)."""
    try:
        _require_own_security(
            conn, user, workspace, body.security_id
        )  # 404 before any flag or count read
        try:
            raised = flags.raise_flag(
                conn,
                user_id=user.id,
                security_id=body.security_id,
                value_type=body.value_type,
                note=body.note,
                charge=lambda: allowance.take(conn, user.id, "flag"),
            )
        except flags.FlagError as exc:
            conn.rollback()  # the unit (if taken) goes back with the refused row
            raise _refusal(exc) from exc
        company = pv_repo.companies(conn, [body.security_id]).get(body.security_id) or {}
        conn.commit()  # the row and its allowance unit, together
    finally:
        conn.rollback()  # a no-op after the commit; ends any read on a refusal
    text = flag_notify.flag_message(
        security_id=body.security_id,
        ticker=company.get("ticker"),
        name=company.get("name"),
        value_type=body.value_type,
        state=raised.state,
        note=raised.flag["note"],
        origin=get_settings().public_origin,
    )
    background.add_task(flag_notify.post_flag, text)  # after the answer; never changes it
    return _out(raised.flag, company)


@router.get("", response_model=ValueFlagListOut)
def list_my_flags(
    limit: int = Query(FLAGS_LIMIT_DEFAULT, ge=1, le=FLAGS_LIMIT_MAX),
    offset: int = Query(0, ge=0, le=FLAGS_OFFSET_MAX),
    user: CurrentUser = Depends(require_user),
    conn: psycopg.Connection = Depends(get_conn),
) -> ValueFlagListOut:
    """Your own flags on shared values: the open ones first (newest first), then the closed ones (newest closed
    first), with the steward's outcome and note. Nobody else's flags are ever listed or counted. ``offset`` /
    ``limit`` cut the page (``limit`` 1-100, ``offset`` 0-100,000); ``total`` counts all of yours. **422** a
    parameter out of bounds. Read-only."""
    try:
        page = flags.mine(conn, user.id, limit=limit, offset=offset)
        return ValueFlagListOut(items=[_out(r) for r in page.items], total=page.total)
    finally:
        conn.rollback()


@router.post("/{flag_id}/withdraw", response_model=ValueFlagOut)
def withdraw_flag(
    flag_id: UUID,
    user: CurrentUser = Depends(_plain_user_only),
    conn: psycopg.Connection = Depends(get_conn),
) -> ValueFlagOut:
    """Withdraw your own open flag. The allowance unit it spent is not given back; you may flag the value again
    (one more unit). **403** for the steward; **404** no flag of yours has this id; **409** it is already closed.
    Answers the withdrawn flag."""
    try:
        try:
            row = flags.withdraw(conn, flag_id, user_id=user.id)
        except flags.FlagError as exc:
            conn.rollback()
            raise _refusal(exc) from exc
        conn.commit()
        return _out(row)
    finally:
        conn.rollback()


__all__ = ["router"]
