"""A USER'S FLAG on a platform value (V2 W5b; ``docs/VALUE_FLAGS.md``).

A plain user who thinks a company's share count or cash/burn is wrong, or missing, tells the steward. A flag never
changes a value; only its user and the steward ever see it. Every row is written here, through
``repositories.value_flag_repo`` (the one place that queries the table).

- ``raise_flag``: the caller has already checked the company is on one of the user's own cards. Then, in the
  caller's ONE transaction: the per-user-and-value lock (``lock_value``, an advisory TRANSACTION lock, so two
  simultaneous requests run one after the other); the duplicate check (an open flag by this user on this value:
  ``FlagConflict``, before any charge); the allowance charge (the ``charge`` callable, which takes a unit inside the
  same transaction: ``FlagAllowanceSpent`` when none is left); the state read (``platform_values.state``: the
  server records what was current: the applied fact's id, the candidate awaiting the steward, or neither for "no
  value yet"; an applied value with a newer candidate pending is the applied value); the insert. Nothing commits
  here: the route commits the unit and the row together, so they cannot diverge, and a refusal rolls both back.
  The partial unique index still guards the row (a ``UniqueViolation`` becomes the same ``FlagConflict``).
- ``withdraw``: the user's own OPEN flag only; another user's id is ``FlagNotFound`` exactly like an unknown id.
  Never refunds the allowance.
- ``mine``: the user's own flags (the user id in SQL), open first then the newest closed, one page.
- The steward's side (W5c's routes): ``steward_close`` (the route's entry: it takes the COMPANY WRITE LOCK, the
  one every steward decision takes, then runs ``close`` and commits, so an undo of the linked decision cannot land
  between the check and the close), ``close`` (``fixed`` names a standing approve or correct of the SAME company
  and value type, checked in this transaction; ``kept`` needs a note that is not blank after trimming), ``reopen``
  (a NEW open row copying the original's user, company, value and what it saw, stamped with the steward who
  reopened it; never an open, already reopened or user-withdrawn flag, nor over the user's newer open flag),
  ``reopen_refusal`` (why a listed flag cannot be reopened), ``for_company`` and ``open_by_company``.

A ``fixed`` close whose decision is undone LATER is never rewritten (0202's closed-once trigger refuses it): the
link stays, the company read marks the decision undone, and the flag can be reopened.

Not on the call path: nothing under ``calls/`` or ``signals/`` imports this module.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal
from uuid import UUID

import psycopg
from psycopg import errors as pg_errors
from psycopg.pq import TransactionStatus

from db.write_lock import acquire_company_lock
from domain.platform_values import DerivedState, FactType, ValueState, is_steward_email
from platform_values import state
from repositories import platform_value_repo as pv_repo
from repositories import value_flag_repo as repo

# The advisory-lock namespace of a flag (the two-int form's first key): "ADF" (Alpha Deck Flag). The company write
# lock is 0x414443 and the fact gate / run lock 0x41444B (db/write_lock.py, pipeline/run_lock.py).
FLAG_LOCK_NAMESPACE = 0x414446
# How many closed flags the steward's company read shows by default (newest first; paged).
CLOSED_SHOWN = 20

Outcome = Literal["fixed", "kept"]
Seen = Literal["value", "awaiting", "none"]


class FlagError(Exception):
    """A refused flag action. ``kind`` names the refusal (``app.errors.VALUE_FLAG_SENTENCES``)."""

    def __init__(self, kind: str, detail: str = "") -> None:
        super().__init__(detail or kind)
        self.kind = kind


class FlagNotFound(FlagError):
    """No such flag (or, for a user, not theirs): 404."""

    def __init__(self, detail: str = "") -> None:
        super().__init__("not_found", detail)


class FlagConflict(FlagError):
    """The flag's state refuses the action: 409."""


class FlagInvalid(FlagError):
    """The request cannot be stored: 422."""


class FlagAllowanceSpent(FlagError):
    """The user's daily flag allowance is spent: 429."""

    def __init__(self) -> None:
        super().__init__("allowance_spent")


@dataclass(frozen=True)
class RaisedFlag:
    """A new flag (the stored row) and the state it was raised against (for the steward's message)."""

    flag: dict
    state: DerivedState


@dataclass(frozen=True)
class FlagPage:
    items: list[dict]
    total: int


@dataclass(frozen=True)
class CompanyFlags:
    """A company's flags for the steward: every open one (oldest first) and one page of closed ones (newest first),
    each with the flagging user's email (decision B4 (a))."""

    open: list[dict]
    closed: list[dict]
    closed_total: int
    # {closed flag id: the id of the row that reopened it}, for the closed flags on this page
    reopened_as: dict[UUID, UUID] = field(default_factory=dict)


@dataclass(frozen=True)
class OpenFlags:
    """A company's open flags in the steward's queue: how many, per value, and since when."""

    count: int
    shares: int
    cash_burn: int
    oldest_opened_at: datetime


def lock_key(user_id: UUID, security_id: UUID, value_type: str) -> int:
    """The second advisory key for (user, company, value): a stable signed int4 from a hash of the three. Two
    different flags sharing a key would only wait for each other, never interfere."""
    digest = hashlib.sha256(f"{user_id}:{security_id}:{value_type}".encode()).digest()
    return int.from_bytes(digest[:4], "big", signed=True)


def lock_value(conn: psycopg.Connection, user_id: UUID, security_id: UUID, value_type: str) -> None:
    """Take the per-user-and-value advisory TRANSACTION lock (released by the commit or the rollback). A second
    request for the same user and value waits here (bounded by the connection's lock timeout) until the first one
    has committed, then sees its row."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT pg_advisory_xact_lock(%s, %s)",
            (FLAG_LOCK_NAMESPACE, lock_key(user_id, security_id, value_type)),
        )


def seen_of(st: DerivedState) -> tuple[UUID | None, UUID | None]:
    """What a flag records as current: ``(seen_fact_id, seen_candidate_id)``. An applied value (from the filing or
    verified) is the value, even with a newer candidate pending; a value awaiting the steward is its candidate; no
    value yet is neither."""
    if st.state in (ValueState.FROM_FILING, ValueState.VERIFIED) and st.value is not None:
        return st.value.get("id"), None
    if st.state is ValueState.AWAITING_STEWARD and st.awaiting is not None:
        return None, st.awaiting.get("id")
    return None, None


def seen_word(row: dict) -> Seen:
    """``value`` / ``awaiting`` / ``none``: what was current when the flag was raised."""
    if row.get("seen_fact_id") is not None:
        return "value"
    if row.get("seen_candidate_id") is not None:
        return "awaiting"
    return "none"


def raise_flag(
    conn: psycopg.Connection,
    *,
    user_id: UUID,
    security_id: UUID,
    value_type: FactType,
    note: str | None,
    charge: Callable[[], bool],
) -> RaisedFlag:
    """Raise one flag (see the module docstring for the order). The caller checked the own-card rule first and
    commits on success; on any exception the caller rolls back (the unit and the row go together). ``charge`` takes
    one allowance unit in this transaction and answers whether it could."""
    lock_value(conn, user_id, security_id, value_type)
    if repo.open_flag(conn, user_id, security_id, value_type) is not None:
        raise FlagConflict("already_open")
    if not charge():
        raise FlagAllowanceSpent()
    st = state.read_state(conn, security_id, value_type)
    seen_fact_id, seen_candidate_id = seen_of(st)
    try:
        with conn.transaction():  # a savepoint: the index's refusal leaves the transaction usable
            row = repo.insert(
                conn,
                user_id=user_id,
                security_id=security_id,
                value_type=value_type,
                note=(note or None),
                seen_fact_id=seen_fact_id,
                seen_candidate_id=seen_candidate_id,
            )
    except (
        pg_errors.UniqueViolation
    ) as exc:  # defensive: the lock makes this unreachable for one value
        raise FlagConflict("already_open") from exc
    return RaisedFlag(flag=row, state=st)


def withdraw(conn: psycopg.Connection, flag_id: UUID, *, user_id: UUID) -> dict:
    """Withdraw the user's own open flag and return it (with its company's ticker and name). ``FlagNotFound`` for
    an id that is not theirs or does not exist (one answer for both); ``FlagConflict('already_closed')`` for their
    closed one. Never refunds. No commit."""
    if repo.withdraw(conn, flag_id, user_id) is not None:
        row = repo.for_user(conn, flag_id, user_id)
        assert row is not None  # withdrawn above, in this transaction
        return row
    if repo.for_user(conn, flag_id, user_id) is None:
        raise FlagNotFound()
    raise FlagConflict("already_closed")


def mine(conn: psycopg.Connection, user_id: UUID, *, limit: int, offset: int) -> FlagPage:
    """The user's own flags (open first, then the newest closed), one page, and how many there are."""
    return FlagPage(
        items=repo.mine(conn, user_id, limit=limit, offset=offset),
        total=repo.count_mine(conn, user_id),
    )


# --- the steward's side (W5c's routes) ------------------------------------------------------------------------------


def _check_fixing_decision(conn: psycopg.Connection, flag: dict, decision_id: UUID) -> None:
    """The decision behind a ``fixed`` close: a STANDING approve or correct (no undo names it) of the flag's own
    company and value type, in the data tenant."""
    d = pv_repo.decision(conn, decision_id)
    if (
        d is None
        or d["security_id"] != flag["security_id"]
        or d["fact_type"] != flag["value_type"]
        or d["action"] not in ("approve", "correct")
        or d["undone"]
    ):
        raise FlagConflict("wrong_decision", f"decision {decision_id}")


def close(
    conn: psycopg.Connection,
    flag_id: UUID,
    *,
    outcome: Outcome,
    note: str | None,
    decision_id: UUID | None,
    closed_by: str,
) -> dict:
    """The steward closes an open flag as ``fixed`` (naming the decision that fixed it) or ``kept`` (with a note the
    user reads). The flag row is locked first, then the decision checked, in this transaction. No commit.
    """
    flag = repo.lock_row(conn, flag_id)
    if flag is None:
        raise FlagNotFound()
    if flag["closed_at"] is not None:
        raise FlagConflict("already_closed")
    text = (note or "").strip() or None
    if outcome == "kept":
        if decision_id is not None:
            raise FlagInvalid("decision_on_kept")
        if text is None:
            raise FlagInvalid("blank_note")
    elif outcome == "fixed":
        if decision_id is None:
            raise FlagInvalid("fixed_needs_decision")
        _check_fixing_decision(conn, flag, decision_id)
    else:  # pragma: no cover - the route's Literal refuses it first
        raise FlagInvalid("bad_outcome")
    row = repo.close(
        conn,
        flag_id,
        outcome=outcome,
        closed_by=closed_by,
        outcome_note=text,
        fixed_decision_id=decision_id if outcome == "fixed" else None,
    )
    if row is None:  # pragma: no cover - the row lock above makes this unreachable
        raise FlagConflict("already_closed")
    return row


def steward_close(
    conn: psycopg.Connection,
    flag_id: UUID,
    *,
    outcome: Outcome,
    note: str | None,
    decision_id: UUID | None,
    by: str,
    wait_s: float,
) -> dict:
    """The steward's close, as the route runs it: on a connection with NO open transaction, read the flag's
    company, take that company's WRITE LOCK (``db.write_lock``, the lock every steward decision and undo takes,
    waiting up to ``wait_s``), then in ONE transaction lock the flag row, check it (and, for ``fixed``, that the
    decision still stands), close it and commit; release the lock after the commit. An undo of the linked decision
    therefore runs wholly before (the close is refused: ``wrong_decision``) or wholly after (the close stands; the
    read then marks the decision undone). ``CompanyLockBusy`` when the wait runs out, with nothing written. On any
    failure the transaction is rolled back."""
    if not is_steward_email(by):
        raise ValueError("a steward's close records the steward's email")
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError("a steward's close needs a connection with no open transaction")
    found = repo.flag(conn, flag_id)
    conn.rollback()  # end the read before the lock: the check runs again under it
    if found is None:
        raise FlagNotFound()
    with acquire_company_lock(conn, found["security_id"], wait_s=wait_s):
        try:
            row = close(
                conn, flag_id, outcome=outcome, note=note, decision_id=decision_id, closed_by=by
            )
            conn.commit()
            return row
        except BaseException:
            conn.rollback()
            raise


def reopen(conn: psycopg.Connection, flag_id: UUID, *, reopened_by: str) -> dict:
    """The inverse of the steward's close (decision B3 (a)): a NEW open flag naming the closed one. Its user,
    company, value, what it saw and the user's note are COPIED from the original; nothing comes from the caller but
    ``reopened_by``, the steward's email (0211). Refuses an open flag, one already reopened, one its USER withdrew,
    and a value the user has since flagged again. No Slack message, no allowance. No commit."""
    if not is_steward_email(reopened_by):
        raise ValueError("a reopen records the steward's email")
    original = repo.lock_row(conn, flag_id)
    if original is None:
        raise FlagNotFound()
    if original["closed_at"] is None:
        raise FlagConflict("not_closed")
    if original["outcome"] == "withdrawn":
        raise FlagConflict("withdrawn_by_user")
    if repo.is_reopened(conn, flag_id):
        raise FlagConflict("already_reopened")
    lock_value(conn, original["user_id"], original["security_id"], original["value_type"])
    if repo.any_open(
        conn,
        original["user_id"],
        original["security_id"],
        original["value_type"],
        original["value_ref"],
    ):
        raise FlagConflict("user_has_open_flag")
    try:
        with conn.transaction():
            return repo.insert(
                conn,
                user_id=original["user_id"],
                security_id=original["security_id"],
                value_type=original["value_type"],
                note=original["note"],
                seen_fact_id=original["seen_fact_id"],
                seen_candidate_id=original["seen_candidate_id"],
                reopens=original["id"],
                reopened_by=reopened_by,
            )
    except pg_errors.UniqueViolation as exc:  # a racing reopen of the same flag
        raise FlagConflict("already_reopened") from exc


def for_company(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    closed_limit: int = CLOSED_SHOWN,
    closed_offset: int = 0,
) -> CompanyFlags:
    """A company's flags for the steward's company page (steward routes only: it names users)."""
    closed = repo.company_closed(conn, security_id, limit=closed_limit, offset=closed_offset)
    return CompanyFlags(
        open=repo.company_open(conn, security_id),
        closed=closed,
        closed_total=repo.company_closed_count(conn, security_id),
        reopened_as=repo.reopened_as(conn, [r["id"] for r in closed]),
    )


def reopen_refusal(row: dict, company: CompanyFlags) -> str | None:
    """Why the steward cannot reopen ``row`` (one of ``company``'s listed flags), as ``reopen``'s refusal kind
    (``not_closed``, ``withdrawn_by_user``, ``already_reopened``, ``user_has_open_flag``), or None when it can.
    Read from the lists already fetched: every open flag of the company is in ``company.open``."""
    if row["closed_at"] is None:
        return "not_closed"
    if row["outcome"] == "withdrawn":
        return "withdrawn_by_user"
    if row["id"] in company.reopened_as:
        return "already_reopened"
    key = (row["user_id"], row["value_type"], row["value_ref"])
    if any((o["user_id"], o["value_type"], o["value_ref"]) == key for o in company.open):
        return "user_has_open_flag"
    return None


def open_by_company(
    conn: psycopg.Connection, security_ids: Iterable[UUID] | None = None
) -> dict[UUID, OpenFlags]:
    """``{security_id: OpenFlags}`` for every company with an open flag (or only the given ones), in ONE statement
    (the steward's queue). A company with none is absent."""
    if security_ids is not None:
        security_ids = list(security_ids)
        if not security_ids:
            return {}
    return {
        r["security_id"]: OpenFlags(
            count=int(r["n"]),
            shares=int(r["shares"]),
            cash_burn=int(r["cash_burn"]),
            oldest_opened_at=r["oldest"],
        )
        for r in repo.open_counts(conn, security_ids)
    }


__all__ = [
    "CLOSED_SHOWN",
    "FLAG_LOCK_NAMESPACE",
    "CompanyFlags",
    "FlagAllowanceSpent",
    "FlagConflict",
    "FlagError",
    "FlagInvalid",
    "FlagNotFound",
    "FlagPage",
    "OpenFlags",
    "RaisedFlag",
    "close",
    "for_company",
    "lock_key",
    "lock_value",
    "mine",
    "open_by_company",
    "raise_flag",
    "reopen",
    "reopen_refusal",
    "seen_of",
    "seen_word",
    "steward_close",
    "withdraw",
]
