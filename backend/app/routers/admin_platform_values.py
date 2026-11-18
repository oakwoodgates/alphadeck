"""The steward's PLATFORM VALUES routes (V2 W2c, ``docs/PLATFORM_VALUES.md``): a company's share count and cash/burn,
one value per company for everyone, read and decided by the steward.

- ``GET  /admin/platform-values/awaiting`` — the flagged candidates waiting for the steward, newest first;
- ``GET  /admin/platform-values/queue`` — the same, one row per company, with its card count, in queue order;
- ``GET  /admin/platform-values/{security_id}`` — the company's two values: state, versions, candidates, decisions;
- ``POST /admin/platform-values/candidates/{candidate_id}/approve`` — apply a flagged candidate's own value;
- ``POST /admin/platform-values/{security_id}/correct`` — set the value by hand, citing the filing;
- ``POST /admin/platform-values/candidates/{candidate_id}/dismiss`` — close a candidate with a reason;
- ``POST /admin/platform-values/decisions/{decision_id}/undo`` — undo an approve, correct or dismiss;
- ``POST /admin/platform-values/{security_id}/pin`` and ``/unpin`` — review every future filing value by hand;
- ``POST /admin/platform-values/{security_id}/extract`` — extract now: read the company's latest filings (W4d);
- ``POST /admin/platform-values/flags/{flag_id}/close`` — close a user's flag as fixed (naming the decision) or kept
  (with a note the user reads) (W5c);
- ``POST /admin/platform-values/flags/{flag_id}/reopen`` — the inverse of a close: a new open flag naming it (W5c).

The company read carries the company's users' flags (open ones, and a page of closed ones), each with the flagging
user's email (decision B4 (a)); the queue lists a company with an open user flag even when no candidate waits, with
the number of open flags only.

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
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Body, Depends, Query
from pydantic import BaseModel, Field, ValidationError

from app.auth import CurrentUser, require_super_admin
from app.deps import get_conn, get_platform_sec_client
from app.errors import (
    PLATFORM_VALUE_FUND,
    PLATFORM_VALUE_NO_CIK,
    PLATFORM_VALUE_NOT_FOUND,
    PLATFORM_VALUE_SENTENCES,
    VALUE_FLAG_NOT_FOUND,
    VALUE_FLAG_SENTENCES,
    fail,
)
from app.routers.workbench import COMPANY_BUSY
from db.write_lock import CompanyLockBusy
from domain.burn_pace import stored_pace
from domain.cash_currency import currency_symbol
from domain.extraction import LocatedPassage
from domain.links import HttpUrlOut
from domain.platform_values import DerivedState
from domain.scopes import DATA_TENANT_ID
from domain.security import Security
from domain.settings import get_settings
from domain.text_types import (
    AdsRatio,
    BurnDays,
    CurrencyCode,
    Money,
    MoneyPart,
    Prose,
    RequestDate,
    Shares,
    UrlText,
)
from ingest.edgar.client import EdgarClient
from platform_values import extraction, flags, state, steward
from platform_values.cards import CardCount, card_counts
from platform_values.machine import MachineOutcome
from platform_values.steward import (
    PlatformValueConflict,
    PlatformValueInvalid,
    StewardOutcome,
)
from repositories import platform_extraction_repo as extraction_repo
from repositories import platform_value_repo as repo
from securities import master

_log = logging.getLogger("alphadeck.platform_values")

router = APIRouter(prefix="/admin/platform-values", tags=["admin"])

FactTypeIn = Literal["shares_outstanding", "cash_burn"]
AWAITING_LIMIT_MAX = 500
QUEUE_LIMIT_DEFAULT = 50
QUEUE_LIMIT_MAX = 200
QUEUE_OFFSET_MAX = 100_000
NO_CARDS = CardCount(cards=0, pen_only=False)
ATTEMPTS_SHOWN = 10  # the company read's recent extraction attempts
# the company read's page of CLOSED user flags (newest closed first); every open one is always listed
FLAGS_CLOSED_LIMIT_DEFAULT = flags.CLOSED_SHOWN
FLAGS_CLOSED_LIMIT_MAX = 100
FLAGS_CLOSED_OFFSET_MAX = 100_000


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


class PlatformPaceOut(BaseModel):
    """A cash-and-burn value's year-to-date pace (V2 extractor correctness PR 3, ``domain.burn_pace``), read from its
    pace columns (0209; else, before 0209, its note): the pace per quarter (burn sign: positive = burning), its span in days, the months of runway that pace
    implies (None when it generates cash or no cash is on file), and the caveat sentence when last quarter is far off
    that pace (None on a steady reading). Display only."""

    ytd_quarterly_burn_usd: float
    ytd_days: int
    ytd_runway_months: float | None = None
    caveat: str | None = None


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
    # the investment part inside ``cash_usd`` as the extractor read it (0208); None = no split recorded
    investments_usd: float | None = None
    ads_ratio: int | None = None
    ads_ratio_status: str | None = None
    filing_accession: str | None = None
    filing_accepted: datetime | None = None
    note: str | None = None
    pace: PlatformPaceOut | None = (
        None  # the burn's year-to-date pace (0209 columns, else the note)
    )
    located_passages: list[LocatedPassage] = Field(default_factory=list)
    passages_unreadable: int = 0
    recorded_at: datetime
    decision: PlatformDecisionOut | None = None


class PlatformAppliedValueOut(BaseModel):
    """One stored fact version: ``valid_from`` the filing's date, ``ratified_by`` who set it (``auto``, an email,
    or ``operator`` for a carried V1 row), ``vouched`` confirm/override provenance. ``source_ref`` is ``None`` on
    the wire unless it is a plain http(s) URL (``domain.links``). A cash/burn version's ``currency`` is its
    statement's ISO code (the ``*_usd`` figures are in it) and ``investments_usd`` the investment part inside
    ``cash_usd`` (0208); None = not recorded, never "USD" or 0 (a V1 row carries neither)."""

    id: UUID
    source: str | None = None
    source_ref: HttpUrlOut = None
    valid_from: date
    recorded_at: datetime
    ratified_by: str | None = None
    vouched: str | None = None
    note: str | None = None
    pace: PlatformPaceOut | None = (
        None  # the burn's year-to-date pace (0209 columns, else the note)
    )
    shares: float | None = None
    ads_ratio: int | None = None
    ads_ratio_status: str | None = None
    filing_accession: str | None = None
    filing_accepted: datetime | None = None
    cash_usd: float | None = None
    quarterly_burn_usd: float | None = None
    currency: str | None = None
    investments_usd: float | None = None


class PlatformVersionOut(PlatformAppliedValueOut):
    """Every stored version, oldest first; ``set_aside`` = an undone approve or correct wrote it, so every read
    skips it."""

    set_aside: bool = False


class PlatformPinEventOut(BaseModel):
    """The newest pin event of a value: ``action`` ``pin`` or ``unpin``, ``decided_by`` the steward's email,
    ``note`` why (optional), ``recorded_at`` when."""

    action: str
    decided_by: str
    note: str | None = None
    recorded_at: datetime


class PlatformValueOut(BaseModel):
    """One value of a company. ``state``: ``no_value`` (``reason`` says why) / ``from_filing`` / ``awaiting_steward``
    / ``verified``; ``ratifier`` ``machine`` / ``carried`` / ``steward``; ``value`` the value the meters show;
    ``awaiting`` the flag waiting when there is no value; ``pending`` a newer flag beside a shown value; ``pinned``
    the steward reviews every future filing value by hand; ``pin`` the newest pin event (who pinned or unpinned,
    when and why; None = never pinned). ``candidates`` (newest 20) and ``decisions`` (newest 50) are newest first.
    """

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
    pin: PlatformPinEventOut | None = None


class PlatformExtractionAttemptOut(BaseModel):
    """One background-extraction attempt of the company (``platform_extraction_attempt``): ``run_kind`` who ran it
    (``nightly``, ``onboarding``, ``backfill``, ``steward``, ``get_data``); ``status`` ``done`` (the machine writer
    ran: ``shares_reason`` / ``cash_reason`` its reason per value type), ``error`` (``error``: one line, why),
    ``busy`` (another writer held the company; nothing written) or ``skipped`` (``detail``, e.g. no CIK).
    """

    id: UUID
    recorded_at: datetime
    run_kind: str
    status: str
    shares_reason: str | None = None
    cash_reason: str | None = None
    error: str | None = None
    detail: str | None = None


class PlatformUserFlagOut(BaseModel):
    """One user's flag on a value of this company, as the steward reads it (W5c). Steward routes only: it names
    the user (``user_email``, decision B4 (a)).

    - ``value_type`` ``shares_outstanding`` / ``cash_burn``; ``note`` the user's words, plain text as stored;
      ``opened_at`` when it was raised (for a reopen row: when the steward reopened it).
    - ``current_when_flagged``: what the server recorded as current when it was raised: ``value`` (``seen_value``,
      the stored version, ``set_aside`` if an undo has since set it aside), ``awaiting`` (``seen_candidate``, the
      candidate that was awaiting the steward) or ``none`` (no value yet). None of the two when the row is gone.
    - ``reopens`` the closed flag this row reopens and ``reopened_by`` the steward who reopened it (0211);
      ``reopened_as`` the row that later reopened THIS one.
    - Closed: ``closed_at``, ``closed_by`` (a steward's email, or ``user`` for the user's own withdrawal),
      ``outcome`` ``fixed`` / ``kept`` / ``withdrawn``, ``outcome_note`` (the steward's note to the user, plain
      text). ``fixed_decision`` the approve or correct a ``fixed`` close names; ``fixed_decision_undone`` = that
      decision has since been undone (the closed row is never rewritten: reopen the flag if the fix no longer
      stands).
    - ``can_reopen``; ``reopen_refusal`` the fixed sentence the reopen route would answer (None when it can).
    """

    id: UUID
    value_type: str
    user_email: str | None = None
    note: str | None = None
    opened_at: datetime
    current_when_flagged: Literal["value", "awaiting", "none"]
    seen_value: PlatformVersionOut | None = None
    seen_candidate: PlatformCandidateOut | None = None
    reopens: UUID | None = None
    reopened_by: str | None = None
    reopened_as: UUID | None = None
    closed_at: datetime | None = None
    closed_by: str | None = None
    outcome: str | None = None
    outcome_note: str | None = None
    fixed_decision: PlatformDecisionOut | None = None
    fixed_decision_undone: bool = False
    can_reopen: bool = False
    reopen_refusal: str | None = None


class PlatformUserFlagsOut(BaseModel):
    """The company's users' flags: every ``open`` one (oldest first) and one page of ``closed`` ones (newest closed
    first, ``closed_offset`` / ``closed_limit``); ``closed_total`` counts every closed one, so an old closed flag
    (and its Reopen) stays reachable by paging."""

    open: list[PlatformUserFlagOut] = Field(default_factory=list)
    closed: list[PlatformUserFlagOut] = Field(default_factory=list)
    closed_total: int = 0
    closed_offset: int = 0
    closed_limit: int = FLAGS_CLOSED_LIMIT_DEFAULT


class CompanyValuesOut(BaseModel):
    """A company's two platform values (the steward's company page). ``attempts``: the company's newest
    background-extraction attempts (at most 10, newest first; W4d), whoever ran them. ``flags``: its users' flags
    (W5c)."""

    security_id: UUID
    ticker: str | None = None
    name: str | None = None
    shares: PlatformValueOut
    cash_burn: PlatformValueOut
    attempts: list[PlatformExtractionAttemptOut] = Field(default_factory=list)
    flags: PlatformUserFlagsOut = Field(default_factory=PlatformUserFlagsOut)


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


class PlatformQueueValueOut(BaseModel):
    """One value of a queue row that waits for the steward: ``state`` (``awaiting_steward``, or the state of the
    value shown when a newer flag is pending beside it), the flagged ``candidate``, and ``value`` the value shown
    (set only when the candidate is pending beside it)."""

    state: str
    candidate: PlatformCandidateOut
    value: PlatformAppliedValueOut | None = None


class PlatformQueueItemOut(BaseModel):
    """One company with anything waiting (one row per company): ``cards`` how many live cards hold it, across every
    workspace (a number only, never whose); ``pen_only`` every row it sits in is a Discovered pen; ``waiting_since``
    the oldest of its waiting candidates and its open user flags; ``shares`` / ``cash_burn`` the candidate that
    waits (None = none for that value); ``open_flags`` how many open user flags it has (W5c; a number only, never
    whose or what note). A company with open user flags and no waiting candidate has both values None.
    """

    security_id: UUID
    ticker: str | None = None
    name: str | None = None
    cards: int = 0
    pen_only: bool = False
    waiting_since: datetime
    shares: PlatformQueueValueOut | None = None
    cash_burn: PlatformQueueValueOut | None = None
    open_flags: int = 0


class PlatformQueueOut(BaseModel):
    """The steward's queue page: ``items`` in queue order (pen-only last, then most cards, then the longest wait,
    then the id); ``total`` every company waiting and ``pen_only_total`` the pen-only ones, whatever the page and
    ``pen``."""

    items: list[PlatformQueueItemOut] = Field(default_factory=list)
    total: int = 0
    pen_only_total: int = 0


ExtractValueOutcome = Literal[
    "applied",  # a newer clean filing value was written (``fact_id``)
    "flagged",  # a flagged value awaits the steward (``candidate_id``; ``written`` False = the same one already did)
    "pinned",  # the company is pinned: a clean value awaits the steward instead of being applied
    "nothing_found",  # the filings gave no value of this type (``reason`` the extractor's)
    "already_on_file",  # this filing's value is already stored: nothing written
    "older_than_on_file",  # dated on or before the value shown: nothing written
    "after_today",  # dated after today, so never shown: nothing written
    "failed",  # the fetch or the write failed (``reason`` the attempt's one line): nothing written
    "skipped",  # nothing could be read (``reason``, e.g. no CIK): nothing fetched
]


class PlatformExtractValueOut(BaseModel):
    """What one "Extract now" did for ONE value type. ``outcome`` (above); ``reason`` the extractor's reason for a
    ``nothing_found``, the attempt's one-line error for ``failed``, the detail for ``skipped``; ``written`` = a row
    was appended for this type now; ``fact_id`` / ``candidate_id`` the fact or candidate that stands for it.
    """

    outcome: ExtractValueOutcome
    reason: str | None = None
    written: bool = False
    fact_id: UUID | None = None
    candidate_id: UUID | None = None


class PlatformExtractAttemptOut(BaseModel):
    """The attempt one "Extract now" recorded (one ``platform_extraction_attempt`` row, ``run_kind`` ``steward``):
    ``status`` ``done`` / ``error`` / ``skipped`` (a busy company answers 409 instead), ``error`` its one line,
    ``live_fetches`` the SEC requests it made, and the outcome per value type."""

    attempt_id: UUID | None = None
    status: Literal["done", "error", "skipped"]
    error: str | None = None
    detail: str | None = None
    live_fetches: int | None = None
    shares: PlatformExtractValueOut
    cash_burn: PlatformExtractValueOut


class PlatformExtractOut(BaseModel):
    """The answer of "Extract now": what the attempt did (``attempt``) and the company's values after it
    (``company``, the same shape every steward write answers)."""

    attempt: PlatformExtractAttemptOut
    company: CompanyValuesOut


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
    """Cash on hand and the quarterly burn (positive = cash used; zero or negative = cash-generative), in the
    statement's ``currency`` (an ISO code; omitted = not recorded), and the ``investments_usd`` part of the cash
    (short-term investments; never negative, never above the cash; omitted = no split recorded)."""

    fact_type: Literal["cash_burn"]
    cash_usd: Money
    quarterly_burn_usd: Money
    currency: CurrencyCode | None = None
    investments_usd: MoneyPart | None = None
    # the year-to-date pace (scaled to a quarter, burn sign) and its span, both or neither (0209); omitted = the
    # correction states no pace, and none is stored (an old pace in the note is dropped too)
    ytd_quarterly_burn_usd: Money | None = None
    ytd_burn_days: BurnDays | None = None


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


class PlatformFlagCloseIn(BaseModel):
    """Close a user's flag (W5c). ``outcome`` ``fixed``: ``decision_id`` names the steward's approve or correct of
    the SAME company and value type that fixed it, still standing; ``note`` optional. ``kept``: the value stands;
    ``note`` (required, not blank) tells the user why; no ``decision_id``."""

    outcome: Literal["fixed", "kept"]
    note: Prose | None = None
    decision_id: UUID | None = None


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
        investments_usd=_f(c.get("investments_usd")),
        ads_ratio=c.get("ads_ratio"),
        ads_ratio_status=c.get("ads_ratio_status"),
        filing_accession=c.get("filing_accession"),
        filing_accepted=c.get("filing_accepted"),
        note=c.get("note"),
        pace=_pace(c),
        located_passages=passages,
        passages_unreadable=unreadable,
        recorded_at=c["recorded_at"],
        decision=_decision(standing) if standing is not None else None,
    )


def _pace(row: Mapping[str, Any]) -> PlatformPaceOut | None:
    """The year-to-date pace of a cash-and-burn row (a candidate or a stored version), or None."""
    # the caveat in the value's own currency: a version's stored ``currency``, a candidate's ``statement_currency``
    # and flags (``domain.cash_currency``)
    symbol = currency_symbol(
        row.get("currency") or row.get("statement_currency"),
        source=row.get("source"),
        flags=row.get("flags"),
    )
    pace = stored_pace(row, symbol=symbol)
    if pace is None:
        return None
    return PlatformPaceOut(
        ytd_quarterly_burn_usd=pace.ytd_quarterly_burn_usd,
        ytd_days=pace.ytd_days,
        ytd_runway_months=pace.ytd_runway_months,
        caveat=pace.caveat,
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
        pace=_pace(v),
        shares=_f(v.get("shares")),
        ads_ratio=v.get("ads_ratio"),
        ads_ratio_status=v.get("ads_ratio_status"),
        filing_accession=v.get("filing_accession"),
        filing_accepted=v.get("filing_accepted"),
        cash_usd=_f(v.get("cash_usd")),
        quarterly_burn_usd=_f(v.get("quarterly_burn_usd")),
        currency=v.get("currency"),
        investments_usd=_f(v.get("investments_usd")),
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
        pin=(
            PlatformPinEventOut(
                action=tv.pin["action"],
                decided_by=tv.pin["decided_by"],
                note=tv.pin.get("note"),
                recorded_at=tv.pin["recorded_at"],
            )
            if tv.pin is not None
            else None
        ),
    )


def _attempt(a: Mapping[str, Any]) -> PlatformExtractionAttemptOut:
    return PlatformExtractionAttemptOut(
        id=a["id"],
        recorded_at=a["recorded_at"],
        run_kind=a["run_kind"],
        status=a["status"],
        shares_reason=a.get("shares_reason"),
        cash_reason=a.get("cash_reason"),
        error=a.get("error"),
        detail=a.get("detail"),
    )


def _flags_out(
    conn: psycopg.Connection, cv: state.CompanyValues, *, closed_offset: int, closed_limit: int
) -> PlatformUserFlagsOut:
    """The company's users' flags, each resolved to readable fields from the company read already made (its
    versions, candidates and decisions); a seen candidate or a linked decision older than that read's window is
    fetched by id (the company page only, never the queue)."""
    cf = flags.for_company(
        conn, cv.security_id, closed_limit=closed_limit, closed_offset=closed_offset
    )
    versions: dict[UUID, Mapping[str, Any]] = {}
    candidates: dict[UUID, Mapping[str, Any]] = {}
    decisions: dict[UUID, Mapping[str, Any]] = {}
    standing: dict[UUID, Mapping[str, Any]] = {}
    for tv in (cv.shares, cv.cash_burn):
        versions.update({v["id"]: v for v in tv.versions})
        for c in (tv.state.awaiting, tv.state.pending, *tv.candidates):
            if c is not None:
                candidates[c["id"]] = c
        decisions.update({d["id"]: d for d in tv.decisions})
        standing.update(_standing(tv.decisions))

    def seen_candidate(cid: UUID) -> PlatformCandidateOut | None:
        c = candidates.get(cid) or repo.candidate(conn, cid)
        if c is None:  # pragma: no cover - a foreign key holds it
            return None
        d = standing.get(cid) or repo.standing_decision_for_candidate(conn, cid)
        return _candidate(c, {cid: d} if d is not None else None)

    def one(row: Mapping[str, Any]) -> PlatformUserFlagOut:
        seen_fact, seen_cand = row.get("seen_fact_id"), row.get("seen_candidate_id")
        version = versions.get(seen_fact) if seen_fact is not None else None
        did = row.get("fixed_decision_id")
        decision = (decisions.get(did) or repo.decision(conn, did)) if did is not None else None
        refusal = flags.reopen_refusal(dict(row), cf)
        return PlatformUserFlagOut(
            id=row["id"],
            value_type=row["value_type"],
            user_email=row.get("user_email"),
            note=row.get("note"),
            opened_at=row["opened_at"],
            current_when_flagged=flags.seen_word(dict(row)),
            seen_value=(
                PlatformVersionOut(
                    **_value_fields(version), set_aside=bool(version.get("set_aside"))
                )
                if version is not None
                else None
            ),
            seen_candidate=seen_candidate(seen_cand) if seen_cand is not None else None,
            reopens=row.get("reopens"),
            reopened_by=row.get("reopened_by"),
            reopened_as=cf.reopened_as.get(row["id"]),
            closed_at=row.get("closed_at"),
            closed_by=row.get("closed_by"),
            outcome=row.get("outcome"),
            outcome_note=row.get("outcome_note"),
            fixed_decision=_decision(decision) if decision is not None else None,
            fixed_decision_undone=bool(decision is not None and decision.get("undone")),
            can_reopen=refusal is None,
            reopen_refusal=VALUE_FLAG_SENTENCES[refusal] if refusal is not None else None,
        )

    return PlatformUserFlagsOut(
        open=[one(r) for r in cf.open],
        closed=[one(r) for r in cf.closed],
        closed_total=cf.closed_total,
        closed_offset=closed_offset,
        closed_limit=closed_limit,
    )


def _company_out(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    flags_offset: int = 0,
    flags_limit: int = FLAGS_CLOSED_LIMIT_DEFAULT,
) -> CompanyValuesOut:
    cv = state.read_company(conn, security_id)
    attempts = extraction_repo.latest_attempts(conn, [security_id], per_company=ATTEMPTS_SHOWN)
    return CompanyValuesOut(
        security_id=cv.security_id,
        ticker=cv.ticker,
        name=cv.name,
        shares=_type_out(cv.shares, conn),
        cash_burn=_type_out(cv.cash_burn, conn),
        attempts=[_attempt(a) for a in attempts.get(security_id, [])],
        flags=_flags_out(conn, cv, closed_offset=flags_offset, closed_limit=flags_limit),
    )


def _read(
    conn: psycopg.Connection,
    security_id: UUID,
    user: CurrentUser,
    *,
    flags_offset: int = 0,
    flags_limit: int = FLAGS_CLOSED_LIMIT_DEFAULT,
) -> CompanyValuesOut:
    try:
        return _company_out(conn, security_id, flags_offset=flags_offset, flags_limit=flags_limit)
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


def _flag_act(
    conn: psycopg.Connection, user: CurrentUser, fn: Callable[[], Mapping[str, Any]]
) -> CompanyValuesOut:
    """Run one steward action on a user's flag (it owns its transaction), map its refusals to the error contract
    (``VALUE_FLAG_SENTENCES``: a fixed sentence and a reference; the steward also sees the detail), then answer the
    flag's company's new state."""
    conn.rollback()  # end the request's read transaction: the action starts its own
    try:
        row = fn()
    except flags.FlagNotFound as e:
        raise fail(404, VALUE_FLAG_NOT_FOUND, e, user=user) from e
    except flags.FlagInvalid as e:
        raise fail(422, VALUE_FLAG_SENTENCES[e.kind], e, user=user) from e
    except flags.FlagConflict as e:
        raise fail(409, VALUE_FLAG_SENTENCES[e.kind], e, user=user) from e
    except CompanyLockBusy as e:
        raise fail(409, COMPANY_BUSY, e, user=user) from e
    return _read(conn, row["security_id"], user)


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
        items = state.awaiting(conn, limit=None)
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


def _queue_value(item: state.AwaitingItem | None) -> PlatformQueueValueOut | None:
    if item is None:
        return None
    value = item.state.value
    return PlatformQueueValueOut(
        state=item.state.state.value,
        candidate=_candidate(item.candidate),
        value=PlatformAppliedValueOut(**_value_fields(value)) if value is not None else None,
    )


@dataclass(frozen=True)
class _QueueRow:
    """One company of the queue: its waiting candidates (``awaiting``, None = none waits) and its open user flags
    (None = none). ``waiting_since`` is the oldest of both (the W5 plan's Q6)."""

    security_id: UUID
    ticker: str | None
    name: str | None
    awaiting: state.AwaitingCompany | None
    open_flags: flags.OpenFlags | None

    @property
    def waiting_since(self) -> datetime:
        since = [self.awaiting.waiting_since] if self.awaiting is not None else []
        if self.open_flags is not None:
            since.append(self.open_flags.oldest_opened_at)
        return min(since)


def _queue_rows(conn: psycopg.Connection) -> list[_QueueRow]:
    """Every company waiting for the steward: a waiting candidate (``state.awaiting_companies``) OR an open user
    flag (``flags.open_by_company``, one grouped statement), unioned by company. A flag-only company's ticker and
    name come from ONE master read (made even when there is none, so the statement count never varies).
    """
    companies = {c.security_id: c for c in state.awaiting_companies(conn)}
    open_flags = flags.open_by_company(conn)
    names = repo.companies(conn, [sid for sid in open_flags if sid not in companies])
    rows = []
    for sid in dict.fromkeys([*companies, *open_flags]):
        c = companies.get(sid)
        master = names.get(sid) or {}
        rows.append(
            _QueueRow(
                security_id=sid,
                ticker=c.ticker if c is not None else master.get("ticker"),
                name=c.name if c is not None else master.get("name"),
                awaiting=c,
                open_flags=open_flags.get(sid),
            )
        )
    return rows


def _queue_order(
    companies: list[_QueueRow], counts: Mapping[UUID, CardCount]
) -> list[tuple[_QueueRow, CardCount]]:
    """The queue's order (approved B3 (a), B5 (a); unchanged by W5c's flags, the W5 plan's Q6): pen-only companies
    last; then most cards first; then the longest wait first (a candidate's or an open user flag's, whichever is
    older); then the id, so the order is total. A company on no live card counts 0 cards, not pen-only: it sorts
    just before the pen-only block (a user may flag a company held only on an archived card)."""
    rows = [(c, counts.get(c.security_id, NO_CARDS)) for c in companies]
    rows.sort(key=lambda r: (r[1].pen_only, -r[1].cards, r[0].waiting_since, str(r[0].security_id)))
    return rows


@router.get("/queue", response_model=PlatformQueueOut)
def list_queue(
    limit: int = Query(QUEUE_LIMIT_DEFAULT, ge=1, le=QUEUE_LIMIT_MAX),
    offset: int = Query(0, ge=0, le=QUEUE_OFFSET_MAX),
    pen: Literal["after", "only", "exclude"] = Query("after"),
    conn: psycopg.Connection = Depends(get_conn),
) -> PlatformQueueOut:
    """THE STEWARD'S QUEUE: one row per company with a share count or cash/burn waiting for you (the same open
    flagged candidates as ``/awaiting``) or with an open user flag (``open_flags``, a number only; W5c), with how
    many live cards hold it across every workspace (a number only). Order: companies only in a Discovered pen last,
    then most cards first, then the longest wait first (the oldest waiting candidate or open user flag). ``pen``:
    ``after`` (default) lists the pen-only companies after the rest, ``only`` lists only them, ``exclude`` leaves
    them out. ``offset`` / ``limit`` cut the page (``limit`` 1-200, ``offset`` 0-100,000); ``total`` and
    ``pen_only_total`` count every company waiting, whatever the page. **422** a parameter out of bounds.
    Read-only."""
    try:
        companies = _queue_rows(conn)
        ordered = _queue_order(companies, card_counts(conn, [c.security_id for c in companies]))
        pen_only_total = sum(1 for _, n in ordered if n.pen_only)
        if pen == "only":
            ordered = [r for r in ordered if r[1].pen_only]
        elif pen == "exclude":
            ordered = [r for r in ordered if not r[1].pen_only]
        return PlatformQueueOut(
            items=[
                PlatformQueueItemOut(
                    security_id=c.security_id,
                    ticker=c.ticker,
                    name=c.name,
                    cards=n.cards,
                    pen_only=n.pen_only,
                    waiting_since=c.waiting_since,
                    shares=_queue_value(c.awaiting.shares) if c.awaiting is not None else None,
                    cash_burn=(
                        _queue_value(c.awaiting.cash_burn) if c.awaiting is not None else None
                    ),
                    open_flags=c.open_flags.count if c.open_flags is not None else 0,
                )
                for c, n in ordered[offset : offset + limit]
            ],
            total=len(companies),
            pen_only_total=pen_only_total,
        )
    finally:
        conn.rollback()


@router.post("/flags/{flag_id}/close", response_model=CompanyValuesOut)
def close_flag(
    flag_id: UUID,
    body: PlatformFlagCloseIn,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """CLOSE a user's open flag, stamped with your email. ``fixed``: name the approve or correct that fixed it
    (``decision_id``: this company's, this value's, not undone; checked under the company write lock, so an undo
    cannot slip in between); ``kept``: the value stands, and ``note`` (required) tells the user why. The user reads
    the outcome and the note on their own flag; no Slack message. Undo with ``POST .../flags/{id}/reopen``.
    Answers the company's new state. **404** unknown flag; **409** already closed (or withdrawn), a decision that
    is not a standing approve or correct of this company's value, or the company is busy; **422** ``kept`` with a
    blank note or a decision, ``fixed`` with no decision, a note over 2,000 characters."""
    wait_s = get_settings().ingest_lock_request_wait_s
    return _flag_act(
        conn,
        user,
        lambda: flags.steward_close(
            conn,
            flag_id,
            outcome=body.outcome,
            note=body.note,
            decision_id=body.decision_id,
            by=user.email,
            wait_s=wait_s,
        ),
    )


@router.post("/flags/{flag_id}/reopen", response_model=CompanyValuesOut)
def reopen_flag(
    flag_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """REOPEN a flag you closed (the close's inverse, decision B3 (a)): a NEW open flag naming it, with the same
    user, value, note and what was current, stamped with your email; the closed one stays in the history as it
    was. No body. No Slack message; the user's allowance is untouched. Answers the company's new state. **404**
    unknown flag; **409** the flag is open, already reopened, withdrawn by its user, or the user has a newer open
    flag on the value."""

    def act() -> Mapping[str, Any]:
        try:
            row = flags.reopen(conn, flag_id, reopened_by=user.email)
            conn.commit()
            return row
        except BaseException:
            conn.rollback()
            raise

    return _flag_act(conn, user, act)


@router.get("/{security_id}", response_model=CompanyValuesOut)
def get_company_values(
    security_id: UUID,
    flags_offset: int = Query(0, ge=0, le=FLAGS_CLOSED_OFFSET_MAX),
    flags_limit: int = Query(FLAGS_CLOSED_LIMIT_DEFAULT, ge=1, le=FLAGS_CLOSED_LIMIT_MAX),
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
) -> CompanyValuesOut:
    """The company's share count and cash/burn: each value's state, every stored version (with ``set_aside``),
    the newest candidates and decisions, the pin, and its users' flags (``flags``: every open one, and the page of
    closed ones that ``flags_offset`` (0-100,000) / ``flags_limit`` (1-100, default 20) cut, newest closed first).
    **404** unknown company; **422** a parameter out of bounds. Read-only."""
    return _read(conn, security_id, user, flags_offset=flags_offset, flags_limit=flags_limit)


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
    company's, or the company is busy; **422** a date after today, an ADS ratio without the status ``known``, a
    currency that is not an ISO code, or an investment part below zero or above the cash.
    """
    if isinstance(body, PlatformCorrectSharesIn):
        values: dict[str, Any] = {
            "shares": body.shares,
            "ads_ratio": body.ads_ratio,
            "ads_ratio_status": body.ads_ratio_status,
        }
    else:
        values = {
            "cash_usd": body.cash_usd,
            "quarterly_burn_usd": body.quarterly_burn_usd,
            "currency": body.currency,
            "investments_usd": body.investments_usd,
            "ytd_quarterly_burn_usd": body.ytd_quarterly_burn_usd,
            "ytd_burn_days": body.ytd_burn_days,
        }
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


# --- extract now (W4d) -------------------------------------------------------------------------------------------

# Attempts the gate reads to say why the company is (or is not) due: enough for any retry streak.
_GATE_ATTEMPTS = 20


def _work_item(conn: psycopg.Connection, sec: Security, cik: str, kind: str) -> extraction.WorkItem:
    """The company as ``extract_one`` takes it. ``reason`` / ``note`` are the gate's own answer (``extract_one``
    reads neither: a steward's click runs whether or not the company is due); a company the gate says is not due
    carries ``retry`` with the note ``steward click (<the gate's note>)``, since ``WorkItem`` has no steward
    reason. The inputs key is the stored filing index's, as the work list builds it; ``extract_one`` replaces it
    with the one the submissions index gives once it has read that."""
    security_id = sec.id
    now = datetime.now(timezone.utc)
    s = get_settings()
    filings = extraction.filings_from_rows(
        extraction_repo.periodic_filings(conn, [security_id]).get(security_id, [])
    )
    attempts = [
        extraction.AttemptRow(a["status"], a["inputs_key"], a["recorded_at"])
        for a in extraction_repo.latest_attempts(
            conn, [security_id], per_company=_GATE_ATTEMPTS
        ).get(security_id, [])
    ]
    due = extraction.due(
        attempts,
        filings,
        cik=cik,
        now=now,
        hold_h=s.platform_extraction_hold_h,
        retry_max=s.platform_extraction_retry_max,
    )
    count = card_counts(conn, [security_id]).get(security_id, NO_CARDS)
    return extraction.WorkItem(
        security_id=security_id,
        ticker=sec.ticker,
        cik=cik,
        instrument_kind=kind,
        cards=count.cards,
        pen_only=count.pen_only,
        reason=due.reason or "retry",
        note=due.note if due.reason else f"steward click ({due.note})",
        inputs_key=extraction.inputs_key(cik, [f for f in filings if f.public_at <= now]),
    )


def _extract_value(
    conn: psycopg.Connection, result: extraction.AttemptResult, fact_type: str
) -> PlatformExtractValueOut:
    """One value type's outcome of the attempt, in the summary's words."""
    if result.status == "error":
        return PlatformExtractValueOut(outcome="failed", reason=result.error)
    if result.status == "skipped":
        return PlatformExtractValueOut(outcome="skipped", reason=result.detail)
    o: MachineOutcome | None = result.outcomes.get(fact_type)
    if o is None:  # pragma: no cover - the writer answers both types on every done attempt
        return PlatformExtractValueOut(outcome="failed", reason="no outcome recorded")
    reason = None
    if o.reason == "nothing_found" and o.candidate_id is not None:
        cand = repo.candidate(conn, o.candidate_id)
        reason = cand.get("reason") if cand is not None else None
    return PlatformExtractValueOut(
        outcome=o.reason,
        reason=reason,
        written=o.written,
        fact_id=o.fact_id,
        candidate_id=o.candidate_id,
    )


@router.post("/{security_id}/extract", response_model=PlatformExtractOut)
def extract_now(
    security_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_super_admin),
    client: EdgarClient = Depends(get_platform_sec_client),
) -> PlatformExtractOut:
    """EXTRACT NOW: read this company's latest SEC filings and hand the result to the machine writer, through
    background extraction's own per-company step (``platform_values.extraction.extract_one``, ``run_kind``
    ``steward``). No body. The step fetches FIRST with the company free (the submissions index, then the
    extraction, cache first, on the recurring client), only then takes the company lock to write, and appends
    exactly one attempt row (``platform_extraction_attempt``), whatever the outcome.

    Per value type, the machine writer's usual rule: a clean value from a filing newer than the value shown is
    applied (even over a verified value, unless the company is pinned: then it waits for you); a flagged value and
    a "nothing found" are recorded as candidates; a value already on file writes nothing. ``attempt`` says what
    happened per type; ``company`` is the company's values after it, with the new attempt first in ``attempts``.

    **An extraction failure is not an error status**: SEC unreachable, a document missing, no SEC User-Agent set:
    the answer is 200 with ``attempt.status`` ``error`` and its one-line reason (the attempt row is the record;
    nothing else is written, so a value awaiting you still waits). **404** unknown company, or a company with no
    CIK (nothing fetched); **409** another writer held the company for the whole wait (one ``busy`` attempt row,
    no value written); **422** a fund (its share count is not extracted). Steward only.
    """
    try:
        sec = master.get(conn, security_id, tenant_id=DATA_TENANT_ID)
        if sec is None:
            raise fail(
                404, PLATFORM_VALUE_NOT_FOUND, LookupError(f"no company {security_id}"), user=user
            )
        if not sec.cik:
            raise fail(404, PLATFORM_VALUE_NO_CIK, LookupError("no CIK"), user=user)
        kind = sec.instrument_kind or "equity"
        if not extraction.extractable({"instrument_kind": kind}):
            raise fail(422, PLATFORM_VALUE_FUND, ValueError(f"instrument_kind={kind}"), user=user)
        item = _work_item(conn, sec, sec.cik, kind)
    finally:
        conn.rollback()  # end the read: no transaction stays open across the SEC call
    wait_s = get_settings().ingest_lock_request_wait_s
    result = extraction.extract_one(conn, item, client=client, wait_s=wait_s, run_kind="steward")
    if result.status == "busy":
        raise fail(409, COMPANY_BUSY, CompanyLockBusy(security_id, None, wait_s), user=user)
    attempt = PlatformExtractAttemptOut(
        attempt_id=result.attempt_id,
        status=result.status,  # done, error or skipped: busy answered 409 above
        error=result.error,
        detail=result.detail,
        live_fetches=result.live_fetches,
        shares=_extract_value(conn, result, "shares_outstanding"),
        cash_burn=_extract_value(conn, result, "cash_burn"),
    )
    return PlatformExtractOut(attempt=attempt, company=_read(conn, security_id, user))
