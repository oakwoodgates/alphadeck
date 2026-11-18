"""The USER'S READ of platform values (V2 W5a; ``docs/PLATFORM_VALUES.md``, "The user's read").

- ``GET /platform-values/theses/{thesis_id}`` — both values of every company on one of the caller's cards;
- ``GET /platform-values/{security_id}`` — both values of one company.

A company's share count and cash/burn are shared data: any signed-in user (the steward too) reads any company in
the master; an unknown id is 404. A card is the caller's own: another workspace's thesis is 404, through the same
ownership choke point every thesis route uses (``app.deps.get_thesis_or_404``). Each value carries its state in
the user's words (``platform_values.words``), who set it as a kind (never an email), and the caller's own flag
(``my_flag``). A value awaiting the steward carries its filing and passages and NO figure (approved B1 (b)).

No ``asof``: this is the CURRENT state. The static ``theses`` path is declared before ``{security_id}``. Read-only.
Mounted with the plain user gate (``_USER`` in ``app/main.py``). Response models carry no limits (built from stored
rows); every URL field is ``HttpUrlOut``, built through ``domain.links.source_url``.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Literal
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError

from app.auth import CurrentUser, require_user
from app.deps import get_conn, get_thesis_or_404
from app.errors import fail
from domain.extraction import LocatedPassage
from domain.links import HttpUrlOut, source_url
from domain.thesis import Thesis
from platform_values import user_view, words
from platform_values.user_view import AppliedValue, FilingRef, UserCompanyValues, UserValue

router = APIRouter(prefix="/platform-values", tags=["platform-values"])

# The fixed sentence for an id the master does not hold (the error contract, ``app/errors.py``).
COMPANY_NOT_FOUND = "There is no such company."


# --- response models (no limits: built from stored rows) ---------------------------------------------------------


class UserPaceOut(BaseModel):
    """A cash-and-burn value's year-to-date pace: per quarter (burn sign: positive = burning), its span in days, the
    months of runway it implies (None when it generates cash or no cash is on file), and the caveat sentence when
    last quarter is far off that pace. Display only."""

    ytd_quarterly_burn_usd: float
    ytd_days: int
    ytd_runway_months: float | None = None
    caveat: str | None = None


class UserAppliedValueOut(BaseModel):
    """The value the meters show. ``source_url`` the filing (None unless a safe http(s) URL); ``valid_from`` its
    date; ``set_at`` when it was set. A share count: ``shares`` (and the ADS ratio). Cash and burn: ``cash_usd``
    (of which ``investments_usd`` short-term investments), ``quarterly_burn_usd`` (positive = burning), in the
    stored ``currency`` (an ISO code; None = not recorded), labeled ``currency_symbol`` by the one rule, and the
    stored ``pace``. The ``*_usd`` names are historical: the figures are in ``currency``, never converted.
    """

    source: str | None = None
    source_url: HttpUrlOut = None
    valid_from: date
    filing_accepted: datetime | None = None
    set_at: datetime | None = None
    shares: float | None = None
    ads_ratio: int | None = None
    ads_ratio_status: str | None = None
    cash_usd: float | None = None
    quarterly_burn_usd: float | None = None
    investments_usd: float | None = None
    currency: str | None = None
    currency_symbol: str | None = None
    pace: UserPaceOut | None = None


class UserPendingOut(BaseModel):
    """A newer filing's value awaits the steward beside the value shown: that filing's date, acceptance and link,
    and the line to show. Never its figures."""

    event_date: date | None = None
    filing_accepted: datetime | None = None
    source_url: HttpUrlOut = None
    line: str


class UserPassageOut(BaseModel):
    """A located passage: the filing's own words around the match (``excerpt``), what matched (``anchor``), the
    section kind and the filing's link."""

    kind: str
    anchor: str
    excerpt: str
    url: HttpUrlOut = None


class UserAwaitingOut(BaseModel):
    """A value awaiting the steward, with nothing shown (approved B1 (b)): the filing's date, acceptance and link,
    the line to show and the located passages; no figure. ``passages_unreadable`` counts stored passages that do
    not read as one (never dropped silently)."""

    event_date: date | None = None
    filing_accepted: datetime | None = None
    source_url: HttpUrlOut = None
    line: str
    located_passages: list[UserPassageOut] = Field(default_factory=list)
    passages_unreadable: int = 0


class MyFlagOut(BaseModel):
    """The caller's own flag on this value: their open flag, else their newest closed one. ``outcome`` ``fixed`` /
    ``kept`` / ``withdrawn`` once closed, with the steward's ``outcome_note``."""

    id: UUID
    note: str | None = None
    opened_at: datetime
    closed_at: datetime | None = None
    outcome: str | None = None
    outcome_note: str | None = None


class UserValueOut(BaseModel):
    """One value of a company as a user reads it. ``state``: ``no_value`` (``reason`` says why) / ``from_filing`` /
    ``awaiting_steward`` / ``verified``; ``label`` its words; ``set_by`` who set the value shown (``machine`` /
    ``steward`` / ``carried``; None with no value); ``value`` the value shown; ``annual_line`` for an annual cover
    count; ``pending`` a newer filing awaiting the steward beside the value shown; ``awaiting`` the filing awaiting
    the steward when nothing is shown; ``my_flag`` the caller's own flag."""

    fact_type: Literal["shares_outstanding", "cash_burn"]
    state: Literal["no_value", "from_filing", "awaiting_steward", "verified"]
    reason: str | None = None
    label: str
    set_by: Literal["machine", "steward", "carried"] | None = None
    value: UserAppliedValueOut | None = None
    annual_line: str | None = None
    pending: UserPendingOut | None = None
    awaiting: UserAwaitingOut | None = None
    my_flag: MyFlagOut | None = None


class UserCompanyValuesOut(BaseModel):
    """A company's two platform values, current (no as-of). ``runway_not_applicable``: the sentence when a runway
    means nothing for this kind of company (a bank, insurer, broker, exchange, asset manager, lender or fund trust;
    V2 C1); its cash value then carries no ``pace``. None when runway applies."""

    security_id: UUID
    ticker: str | None = None
    name: str | None = None
    shares: UserValueOut
    cash_burn: UserValueOut
    runway_not_applicable: str | None = None


class UserThesisValuesOut(BaseModel):
    """Both values of every company on the card, in basket order (each company once)."""

    thesis_id: UUID
    members: list[UserCompanyValuesOut] = Field(default_factory=list)


# --- read -> response ---------------------------------------------------------------------------------------------


def _passages(
    raw: tuple[Mapping[str, Any], ...], cik: str | None
) -> tuple[list[UserPassageOut], int]:
    out: list[UserPassageOut] = []
    unreadable = 0
    for p in raw:
        try:
            lp = LocatedPassage.model_validate(p)
        except ValidationError:
            unreadable += 1
            continue
        out.append(
            UserPassageOut(
                kind=lp.kind,
                anchor=lp.anchor,
                excerpt=lp.excerpt,
                url=source_url(lp.source_ref, cik),
            )
        )
    return out, unreadable


def _applied(v: AppliedValue, cik: str | None) -> UserAppliedValueOut:
    return UserAppliedValueOut(
        source=v.source,
        source_url=source_url(v.source_ref, cik),
        valid_from=v.valid_from,
        filing_accepted=v.filing_accepted,
        set_at=v.set_at,
        shares=v.shares,
        ads_ratio=v.ads_ratio,
        ads_ratio_status=v.ads_ratio_status,
        cash_usd=v.cash_usd,
        quarterly_burn_usd=v.quarterly_burn_usd,
        investments_usd=v.investments_usd,
        currency=v.currency,
        currency_symbol=v.currency_symbol,
        pace=(
            UserPaceOut(
                ytd_quarterly_burn_usd=v.pace.ytd_quarterly_burn_usd,
                ytd_days=v.pace.ytd_days,
                ytd_runway_months=v.pace.ytd_runway_months,
                caveat=v.pace.caveat,
            )
            if v.pace is not None
            else None
        ),
    )


def _awaiting(a: FilingRef, cik: str | None) -> UserAwaitingOut:
    passages, unreadable = _passages(a.located_passages, cik)
    return UserAwaitingOut(
        event_date=a.event_date,
        filing_accepted=a.filing_accepted,
        source_url=source_url(a.source_ref, cik),
        line=a.line,
        located_passages=passages,
        passages_unreadable=unreadable,
    )


def _value(v: UserValue, cik: str | None) -> UserValueOut:
    return UserValueOut(
        fact_type=v.fact_type,
        state=v.state.value,
        reason=v.reason,
        label=v.label,
        set_by=v.set_by,
        value=_applied(v.value, cik) if v.value is not None else None,
        annual_line=v.annual_line,
        pending=(
            UserPendingOut(
                event_date=v.pending.event_date,
                filing_accepted=v.pending.filing_accepted,
                source_url=source_url(v.pending.source_ref, cik),
                line=v.pending.line,
            )
            if v.pending is not None
            else None
        ),
        awaiting=_awaiting(v.awaiting, cik) if v.awaiting is not None else None,
        my_flag=(
            MyFlagOut(
                id=v.my_flag.id,
                note=v.my_flag.note,
                opened_at=v.my_flag.opened_at,
                closed_at=v.my_flag.closed_at,
                outcome=v.my_flag.outcome,
                outcome_note=v.my_flag.outcome_note,
            )
            if v.my_flag is not None
            else None
        ),
    )


def company_out(cv: UserCompanyValues) -> UserCompanyValuesOut:
    """One company's read as the wire carries it (every link through ``source_url``: None unless safe)."""
    return UserCompanyValuesOut(
        security_id=cv.security_id,
        ticker=cv.ticker,
        name=cv.name,
        shares=_value(cv.shares, cv.cik),
        cash_burn=_value(cv.cash_burn, cv.cik),
        runway_not_applicable=None if cv.runway_applies else words.RUNWAY_NOT_APPLICABLE,
    )


# --- the routes (the static ``theses`` path first) ---------------------------------------------------------------


@router.get("/theses/{thesis_id}", response_model=UserThesisValuesOut)
def get_thesis_values(
    conn: psycopg.Connection = Depends(get_conn),
    thesis: Thesis = Depends(get_thesis_or_404),
    user: CurrentUser = Depends(require_user),
) -> UserThesisValuesOut:
    """The share count and cash/burn of every company on one of your cards, in basket order, each with its state
    and your own flag. Current: no as-of. Another workspace's card is 404."""
    ids = [m.security_id for m in thesis.basket if m.security_id is not None]
    try:
        got = user_view.values_for(conn, ids, user_id=user.id)
    finally:
        conn.rollback()  # a read: end the transaction, hold no snapshot
    return UserThesisValuesOut(
        thesis_id=thesis.id, members=[company_out(cv) for cv in got.values()]
    )


@router.get("/{security_id}", response_model=UserCompanyValuesOut)
def get_company_values(
    security_id: UUID,
    conn: psycopg.Connection = Depends(get_conn),
    user: CurrentUser = Depends(require_user),
) -> UserCompanyValuesOut:
    """A company's share count and cash/burn with their state, shared by every user, and your own flag on each.
    Current: no as-of. A value awaiting the steward carries its filing and passages, never its number.
    """
    try:
        got = user_view.values_for(conn, [security_id], user_id=user.id)
    finally:
        conn.rollback()  # a read: end the transaction, hold no snapshot
    cv = got.get(security_id)
    if cv is None:
        raise fail(
            404, COMPANY_NOT_FOUND, LookupError(f"no company with id {security_id}"), user=user
        )
    return company_out(cv)
