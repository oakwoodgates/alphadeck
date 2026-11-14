"""THE STEWARD'S ACTIONS on a platform value (V2 W2c): approve a candidate, correct a value, dismiss a candidate,
undo a decision, pin, unpin (``docs/PLATFORM_VALUES.md``).

ONE MODULE, ONE SHAPE. Every action:

1. reads what it acts on (a candidate, a decision: append-only rows, so the company they name cannot change) and
   ends that read;
2. takes the company write lock (``db.write_lock.acquire_company_lock``, the request wait
   ``Settings.ingest_lock_request_wait_s``): ``CompanyLockBusy`` when another writer or a repair holds it, with
   nothing written;
3. RE-READS the state under the lock and checks its rule;
4. APPENDS (never updates): the fact (approve, correct; through ``ingest_shares_outstanding`` /
   ``ingest_cash_burn``, stamped ``ratified_by=<the steward's email>``), then the decision (or the pin event);
5. stamps the company changed in the SAME transaction (``db.change_stamp.mark_changed``), commits, releases.

The steward's email (``by``) is the signed-in user's, never a request body's. A value is written only when it
will be SHOWN: the correction rule (``domain.platform_values.correction_wins``, approved decision D1 (a)) runs
under the lock before the write, and the meter's own pick is re-read after it; a value that would not be shown is
REFUSED with the date that blocks it, never silently stored.

THE TRANSACTION IS THE ACTION'S. It must start with no open transaction on ``conn`` (``RuntimeError`` otherwise):
the write's ``recorded_at`` is its transaction's start, which then falls after the lock was taken, so the new row
is the company's newest write (the tie-break of D1 (a)). On any failure the transaction is rolled back.

THE ERRORS (``app.routers.admin_platform_values`` maps them to statuses):

- ``LookupError`` — no such company, candidate or decision (404);
- ``PlatformValueConflict`` — the state refuses it (409): ``already_decided``, ``older_than_shown`` (with the
  ``blocking`` row), ``not_shown``, ``wrong_candidate``, ``undo_of_undo``, ``already_undone``,
  ``already_pinned``, ``not_pinned``;
- ``PlatformValueInvalid`` (a ``ValueError``) — the request cannot be stored (422): ``after_today``,
  ``nothing`` (a "nothing" candidate has no value), ``no_value`` (a flag without the value), ``bad_ads``,
  ``bad_values``, ``blank_reason``;
- ``db.write_lock.CompanyLockBusy`` — busy (409).

Display only: neither table is a call input, so no action here can move a call, a verdict or a grade. Company
catalysts are not here (approved decision D5: W6).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, TypeVar
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus

from db.change_stamp import mark_changed
from db.write_lock import acquire_company_lock
from domain import market_time
from domain.platform_values import (
    TABLE_OF,
    FactType,
    correction_wins,
    is_steward_email,
    pick_current,
)
from domain.scopes import DATA_TENANT_ID
from ingest.cash_burn import ingest_cash_burn
from ingest.shares import cik_of_source_ref, ingest_shares_outstanding, shares_publication
from platform_values import state
from repositories import platform_value_repo as repo

# A steward's correction carries this ``source`` unless its caller names the candidate's basis (the Workbench
# confirm keeps the basis it was shown, e.g. ``10-q-cover``).
CORRECTED_SOURCE = "ratified"
# An approved candidate with no recorded basis (``extraction_candidate.source`` is nullable).
CANDIDATE_SOURCE_FALLBACK = "extractor"

# The values each type takes (``correct``'s ``values``).
_VALUE_KEYS: dict[str, frozenset[str]] = {
    "shares_outstanding": frozenset({"shares", "ads_ratio", "ads_ratio_status"}),
    "cash_burn": frozenset({"cash_usd", "quarterly_burn_usd"}),
}
_REQUIRED: dict[str, tuple[str, ...]] = {
    "shares_outstanding": ("shares",),
    "cash_burn": ("cash_usd", "quarterly_burn_usd"),
}


class PlatformValueConflict(Exception):
    """The value's state refuses the action (409). ``kind`` names why; ``blocking`` is the row that would still
    be shown (``older_than_shown`` / ``not_shown``), else None. The message carries the detail (dates, filing).
    """

    def __init__(self, kind: str, message: str, *, blocking: Mapping[str, Any] | None = None):
        super().__init__(message)
        self.kind = kind
        self.blocking = blocking


class PlatformValueInvalid(ValueError):
    """The request cannot be stored (422). ``kind`` names why; the message carries the detail."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class StewardOutcome:
    """What one action wrote: the company and type it acted on, the decision (approve / correct / dismiss /
    undo), the fact version (approve / correct) and the pin event (pin / unpin) — each None when not written.
    """

    security_id: UUID
    fact_type: FactType
    decision_id: UUID | None = None
    fact_id: UUID | None = None
    pin_id: UUID | None = None


# --- the shared frame --------------------------------------------------------------------------------------------

_T = TypeVar("_T")


def _require_idle(conn: psycopg.Connection) -> None:
    if conn.info.transaction_status != TransactionStatus.IDLE:
        raise RuntimeError(
            "a steward action needs a connection with no open transaction (commit or roll back first): "
            "its write must start after the company lock is taken"
        )


def _require_email(by: str) -> None:
    if not is_steward_email(by):
        raise ValueError("a steward action records the steward's email")


def _wait_s(wait_s: float | None) -> float:
    if wait_s is not None:
        return wait_s
    from domain.settings import get_settings

    return get_settings().ingest_lock_request_wait_s


def _locked(
    conn: psycopg.Connection, security_id: UUID, wait_s: float | None, body: Callable[[], _T]
) -> _T:
    """Run ``body`` under the company lock in one transaction: commit on success, roll back on any failure, release
    after the commit (``CompanyLockBusy`` from the lock itself, with nothing written)."""
    lock = acquire_company_lock(conn, security_id, wait_s=_wait_s(wait_s))
    with lock:
        try:
            out = body()
            mark_changed(conn, [security_id])  # the stamp, in the action's own transaction
            conn.commit()
            return out
        except BaseException:
            conn.rollback()
            raise


def _today() -> date:
    return market_time.market_today()


def _company_exists(conn: psycopg.Connection, security_id: UUID) -> None:
    if security_id not in repo.companies(conn, [security_id]):
        raise LookupError(f"no company with id {security_id}")


def _candidate(conn: psycopg.Connection, candidate_id: UUID) -> dict:
    cand = repo.candidate(conn, candidate_id)
    if cand is None:
        raise LookupError(f"no candidate with id {candidate_id}")
    return cand


def _open(conn: psycopg.Connection, cand: Mapping[str, Any]) -> None:
    """Refuse a candidate a standing decision already closes."""
    standing = repo.standing_decision_for_candidate(conn, cand["id"])
    if standing is not None:
        raise PlatformValueConflict(
            "already_decided",
            f"candidate {cand['id']} already has a standing {standing['action']} "
            f"(decision {standing['id']}, by {standing['decided_by']}); undo it first",
        )


def _describe(row: Mapping[str, Any]) -> str:
    return f"the value dated {row['valid_from'].isoformat()} from {row.get('source_ref')}"


def _check_shown(
    conn: psycopg.Connection,
    security_id: UUID,
    fact_type: FactType,
    *,
    source_ref: str,
    event_date: date,
    today: date,
) -> None:
    """The correction rule (D1 (a)) against the per-filing rows the meter reads now, under the lock."""
    winners = state.winners(conn, security_id, fact_type, asof=today)
    verdict = correction_wins(winners, {"source_ref": source_ref, "valid_from": event_date}, today)
    if verdict.reason == "after_today":
        raise PlatformValueInvalid(
            "after_today",
            f"the value is dated {event_date.isoformat()}, after today ({today.isoformat()}); "
            "a value dated after today is never shown",
        )
    if not verdict.wins:
        assert verdict.blocking is not None
        raise PlatformValueConflict(
            "older_than_shown",
            f"the value is dated {event_date.isoformat()}, before {_describe(verdict.blocking)}, "
            "which would still be shown",
            blocking=verdict.blocking,
        )


def _verify_shown(
    conn: psycopg.Connection, security_id: UUID, fact_type: FactType, fact_id: UUID, today: date
) -> None:
    """After the write, inside the same transaction: the meter's own pick must be the new row, else refuse (the
    caller's rollback discards it). Belt and braces for the rule above: never a stored value nobody sees.
    """
    picked = pick_current(state.winners(conn, security_id, fact_type, asof=today), not_after=today)
    if picked is None or picked.get("id") != fact_id:
        raise PlatformValueConflict(
            "not_shown",
            "the value would not be shown"
            + (f": {_describe(picked)} is shown instead" if picked is not None else ""),
            blocking=picked,
        )


def _default_cache_dir() -> Path | None:
    from ingest.edgar.client import EdgarClient  # the EDGAR cache on disk; never a request

    return EdgarClient(allow_live=False).cache_dir


def _write_fact(
    conn: psycopg.Connection,
    security_id: UUID,
    fact_type: FactType,
    values: Mapping[str, Any],
    *,
    source: str,
    source_ref: str,
    event_date: date,
    note: str | None,
    by: str,
    vouched: str | None,
    filing_accession: str | None = None,
    filing_accepted: Any = None,
    cache_dir: Path | None = None,
) -> UUID:
    common = dict(
        source=source,
        source_ref=source_ref,
        event_date=event_date,
        note=note,
        ratified_by=by,  # the steward's email (the 0206 vocabulary)
        vouched=vouched,
        tenant_id=DATA_TENANT_ID,  # platform data: one value for every workspace
    )
    if fact_type == "shares_outstanding":
        shares = float(values["shares"])
        if filing_accession is None and filing_accepted is None:
            # F9: the filing whose cover carries the count, and its acceptance (cache only; none = written now)
            from securities import master

            cik = master.ciks_for(conn, {security_id}, tenant_id=DATA_TENANT_ID).get(security_id)
            filing_accession, filing_accepted = shares_publication(
                cache_dir if cache_dir is not None else _default_cache_dir(),
                cik or cik_of_source_ref(source_ref),
                shares,
                event_date,
            )
        return ingest_shares_outstanding(
            conn,
            security_id,
            shares=shares,
            ads_ratio=values.get("ads_ratio"),
            ads_ratio_status=values.get("ads_ratio_status"),
            filing_accession=filing_accession,
            filing_accepted=filing_accepted,
            **common,
        )
    return ingest_cash_burn(
        conn,
        security_id,
        cash_usd=float(values["cash_usd"]),
        quarterly_burn_usd=float(values["quarterly_burn_usd"]),
        **common,
    )


def _check_values(fact_type: FactType, values: Mapping[str, Any]) -> None:
    unknown = set(values) - _VALUE_KEYS[fact_type]
    missing = [k for k in _REQUIRED[fact_type] if values.get(k) is None]
    if unknown or missing:
        raise PlatformValueInvalid(
            "bad_values",
            f"a {fact_type} value takes {sorted(_VALUE_KEYS[fact_type])}"
            + (f"; unknown: {sorted(unknown)}" if unknown else "")
            + (f"; missing: {missing}" if missing else ""),
        )
    if fact_type == "shares_outstanding":
        ratio, status = values.get("ads_ratio"), values.get("ads_ratio_status")
        if status not in (None, "known", "unread") or (status == "known") != (ratio is not None):
            raise PlatformValueInvalid(
                "bad_ads",
                "an ADS ratio goes with ads_ratio_status 'known', and 'known' needs a ratio; "
                "'unread' carries no ratio",
            )


# --- the six actions ---------------------------------------------------------------------------------------------


def approve(
    conn: psycopg.Connection,
    candidate_id: UUID,
    *,
    by: str,
    note: str | None = None,
    wait_s: float | None = None,
    cache_dir: Path | None = None,
) -> StewardOutcome:
    """APPROVE a flagged candidate: write its OWN values as the company's value (its ``source``, filing, date,
    ADS fields and filing acceptance), stamped with the steward's email, plus an ``approve`` decision that closes
    the candidate. ``note`` is the fact's note (else the candidate's).

    Refused: a "nothing" candidate or a flag missing its value (422); a candidate already decided, or one a newer
    value on file would still outrank (409, naming that value's date and filing); a date after today (422).
    """
    _require_email(by)
    _require_idle(conn)
    cand = _candidate(conn, candidate_id)
    conn.rollback()  # end the read before the lock
    sid, fact_type = cand["security_id"], cand["fact_type"]

    def body() -> StewardOutcome:
        current = _candidate(conn, candidate_id)
        if current["outcome"] == "nothing":
            raise PlatformValueInvalid(
                "nothing",
                f"candidate {candidate_id} records that no value was found ({current.get('reason')}); "
                "there is nothing to approve",
            )
        values = {k: current.get(k) for k in _VALUE_KEYS[fact_type]}
        missing = [k for k in _REQUIRED[fact_type] if values.get(k) is None]
        if missing:
            raise PlatformValueInvalid(
                "no_value",
                f"candidate {candidate_id} has no {', '.join(missing)}; correct the value instead",
            )
        _open(conn, current)
        today = _today()
        _check_shown(
            conn,
            sid,
            fact_type,
            source_ref=current["source_ref"],
            event_date=current["event_date"],
            today=today,
        )
        fid = _write_fact(
            conn,
            sid,
            fact_type,
            values,
            source=current.get("source") or CANDIDATE_SOURCE_FALLBACK,
            source_ref=current["source_ref"],
            event_date=current["event_date"],
            note=note if note is not None else current.get("note"),
            by=by,
            vouched="confirmed",  # the candidate's own value, as shown
            filing_accession=current.get("filing_accession"),
            filing_accepted=current.get("filing_accepted"),
            cache_dir=cache_dir,
        )
        _verify_shown(conn, sid, fact_type, fid, today)
        did = repo.append_decision(
            conn,
            security_id=sid,
            fact_type=fact_type,
            action="approve",
            candidate_id=candidate_id,
            fact_id=fid,
            reason=note,
            decided_by=by,
        )
        return StewardOutcome(sid, fact_type, decision_id=did, fact_id=fid)

    return _locked(conn, sid, wait_s, body)


def correct(
    conn: psycopg.Connection,
    security_id: UUID,
    fact_type: FactType,
    values: Mapping[str, Any],
    *,
    source_ref: str,
    event_date: date,
    by: str,
    note: str | None = None,
    candidate_id: UUID | None = None,
    source: str = CORRECTED_SOURCE,
    vouched: str | None = None,
    wait_s: float | None = None,
    cache_dir: Path | None = None,
) -> StewardOutcome:
    """CORRECT the company's value: the steward's own values, citing the filing (``source_ref``, required) and
    its date (``event_date`` -> ``valid_from``), stamped with the steward's email, plus a ``correct`` decision.
    With ``candidate_id`` the decision also closes that candidate (it must be this company's and type's, and
    open).

    ``values``: shares ``{shares, ads_ratio?, ads_ratio_status?}``; cash/burn ``{cash_usd, quarterly_burn_usd}``.
    THE RULE (D1 (a)): the value wins when its date is on or after the shown value's (another filing's) and not
    after today; else it is refused with the date that blocks it (``after_today`` 422, ``older_than_shown``
    409). ``source`` defaults to ``ratified``; ``vouched`` is confirm/override provenance (never a score input).
    """
    _require_email(by)
    if not source_ref or not source_ref.strip():
        raise PlatformValueInvalid("bad_values", "a correction cites its filing (source_ref)")
    _check_values(fact_type, values)
    _require_idle(conn)
    _company_exists(conn, security_id)
    conn.rollback()  # end the read before the lock

    def body() -> StewardOutcome:
        if candidate_id is not None:
            cand = _candidate(conn, candidate_id)
            if cand["security_id"] != security_id or cand["fact_type"] != fact_type:
                raise PlatformValueConflict(
                    "wrong_candidate",
                    f"candidate {candidate_id} is not this company's {fact_type} candidate",
                )
            _open(conn, cand)
        today = _today()
        _check_shown(
            conn, security_id, fact_type, source_ref=source_ref, event_date=event_date, today=today
        )
        fid = _write_fact(
            conn,
            security_id,
            fact_type,
            values,
            source=source,
            source_ref=source_ref,
            event_date=event_date,
            note=note,
            by=by,
            vouched=vouched,
            cache_dir=cache_dir,
        )
        _verify_shown(conn, security_id, fact_type, fid, today)
        did = repo.append_decision(
            conn,
            security_id=security_id,
            fact_type=fact_type,
            action="correct",
            candidate_id=candidate_id,
            fact_id=fid,
            reason=note,
            decided_by=by,
        )
        return StewardOutcome(security_id, fact_type, decision_id=did, fact_id=fid)

    return _locked(conn, security_id, wait_s, body)


def dismiss(
    conn: psycopg.Connection,
    candidate_id: UUID,
    *,
    by: str,
    reason: str,
    wait_s: float | None = None,
) -> StewardOutcome:
    """DISMISS a candidate: a ``dismiss`` decision closes it; no fact changes. ``reason`` is required (422 when
    blank); 409 when a decision already stands."""
    _require_email(by)
    if reason is None or not reason.strip():
        raise PlatformValueInvalid("blank_reason", "a dismissal needs a reason")
    _require_idle(conn)
    cand = _candidate(conn, candidate_id)
    conn.rollback()
    sid, fact_type = cand["security_id"], cand["fact_type"]

    def body() -> StewardOutcome:
        _open(conn, _candidate(conn, candidate_id))
        did = repo.append_decision(
            conn,
            security_id=sid,
            fact_type=fact_type,
            action="dismiss",
            candidate_id=candidate_id,
            reason=reason.strip(),
            decided_by=by,
        )
        return StewardOutcome(sid, fact_type, decision_id=did)

    return _locked(conn, sid, wait_s, body)


def undo(
    conn: psycopg.Connection,
    decision_id: UUID,
    *,
    by: str,
    reason: str | None = None,
    wait_s: float | None = None,
) -> StewardOutcome:
    """UNDO a decision: an ``undo`` row names it. An undone approve or correct has its fact version set aside
    (``db.set_aside``: every read skips it and falls back to the version before); an undone approve, correct or
    dismiss reopens its candidate. 409 for an undo (it cannot be undone: make a new decision) and for a decision
    already undone."""
    _require_email(by)
    _require_idle(conn)
    target = repo.decision(conn, decision_id)
    if target is None:
        raise LookupError(f"no decision with id {decision_id}")
    conn.rollback()
    sid, fact_type = target["security_id"], target["fact_type"]

    def body() -> StewardOutcome:
        current = repo.decision(conn, decision_id)
        assert current is not None  # append-only: it cannot have gone
        if current["action"] == "undo":
            raise PlatformValueConflict(
                "undo_of_undo",
                f"decision {decision_id} is itself an undo; make a new decision instead",
            )
        if current["undone"]:
            raise PlatformValueConflict(
                "already_undone", f"decision {decision_id} is already undone"
            )
        did = repo.append_decision(
            conn,
            security_id=sid,
            fact_type=fact_type,
            action="undo",
            revokes=decision_id,
            reason=reason.strip() if reason and reason.strip() else None,
            decided_by=by,
        )
        return StewardOutcome(sid, fact_type, decision_id=did)

    return _locked(conn, sid, wait_s, body)


def _pin_event(
    conn: psycopg.Connection,
    security_id: UUID,
    fact_type: FactType,
    *,
    action: str,
    by: str,
    note: str | None,
    wait_s: float | None,
) -> StewardOutcome:
    _require_email(by)
    if fact_type not in TABLE_OF:
        raise PlatformValueInvalid("bad_values", f"not a platform value type: {fact_type}")
    _require_idle(conn)
    _company_exists(conn, security_id)
    conn.rollback()

    def body() -> StewardOutcome:
        pinned = repo.is_pinned(conn, security_id, fact_type)
        if action == "pin" and pinned:
            raise PlatformValueConflict("already_pinned", f"this company's {fact_type} is pinned")
        if action == "unpin" and not pinned:
            raise PlatformValueConflict("not_pinned", f"this company's {fact_type} is not pinned")
        pid = repo.append_pin(
            conn,
            security_id=security_id,
            fact_type=fact_type,
            action=action,
            note=note.strip() if note and note.strip() else None,
            decided_by=by,
        )
        return StewardOutcome(security_id, fact_type, pin_id=pid)

    return _locked(conn, security_id, wait_s, body)


def pin(
    conn: psycopg.Connection,
    security_id: UUID,
    fact_type: FactType,
    *,
    by: str,
    note: str | None = None,
    wait_s: float | None = None,
) -> StewardOutcome:
    """PIN the company's value: from now on the machine records a newer clean filing value as a flagged candidate
    for the steward instead of applying it (W2b). 409 when already pinned."""
    return _pin_event(conn, security_id, fact_type, action="pin", by=by, note=note, wait_s=wait_s)


def unpin(
    conn: psycopg.Connection,
    security_id: UUID,
    fact_type: FactType,
    *,
    by: str,
    note: str | None = None,
    wait_s: float | None = None,
) -> StewardOutcome:
    """UNPIN the company's value (the pin's inverse): the machine applies a newer clean filing value again. 409
    when not pinned."""
    return _pin_event(conn, security_id, fact_type, action="unpin", by=by, note=note, wait_s=wait_s)


__all__ = [
    "CORRECTED_SOURCE",
    "CANDIDATE_SOURCE_FALLBACK",
    "PlatformValueConflict",
    "PlatformValueInvalid",
    "StewardOutcome",
    "approve",
    "correct",
    "dismiss",
    "undo",
    "pin",
    "unpin",
]
