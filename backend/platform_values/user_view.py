"""THE USER'S READ of platform values (V2 W5a; ``docs/PLATFORM_VALUES.md``, "The user's read").

``values_for(conn, security_ids, *, user_id)`` answers, for a set of companies, each value's state as a plain user
may read it, and the caller's own flag on each value. It is the one read every user screen and agent tool uses
(the Workbench's Shared values block, the Cockpit's lines, ``get_triage``: W5d / W5e).

What a user receives (approved B1 (b), 2026-11-16):

- a value shown (``from_filing`` / ``verified``): the applied figures, who set it as a KIND (``machine`` /
  ``steward`` / ``carried``; never the steward's email), the filing and its date; an annual cover count's annual
  line; a cash value's composition, stored currency and pace; and, when a NEWER filing's value awaits the steward,
  the pending line: that filing's date and reference only, never its figures;
- a value awaiting the steward: the filing's reference and date and its located passages, and NO figure, for a
  share count and for cash (the steward has not checked the parked number; the user reads the filing instead);
- no value yet: the reason;
- for a company a runway means nothing for (V2 C1: a bank, insurer, broker, exchange, asset manager, lender or fund
  trust, ``securities.business_type.runway_applies``, the one rule): ``runway_applies`` False and NO pace on its cash
  value (the pace's months and caveat are runway readings); the cash and burn themselves are unchanged.

The state comes from ``platform_values.state.read_states`` (the one reader), one batch per value type, so the
number of statements does not grow with the number of companies: the master (1), two state batches, the caller's
flags (1). It is CURRENT: no as-of (the state describes today; a dated view must not explain an old meter with it).

``my_flag`` is the caller's own flag on the value (``value_flag``, migration 0202): their open one, else their
newest closed one. The query names the caller's id in its WHERE clause, so another user's flag is never read; it
reads only 0202's columns. Read-only, no lock. Display only: nothing on the call path imports it.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from domain.burn_pace import RunwayPace, stored_pace
from domain.cash_currency import currency_symbol
from domain.platform_values import FACT_TYPES, DerivedState, FactType, ValueState
from domain.scopes import DATA_TENANT_ID
from platform_values import words
from platform_values.state import read_states
from platform_values.words import SetBy
from securities.business_type import runway_applies

# The value types a user flags here (0202's ``value_type``; a catalyst flag is never a share count's or cash's).
_FLAG_VALUE_TYPES: tuple[str, ...] = FACT_TYPES

# The caller's flag per (company, value type): the open one first, else the newest closed one. ONLY 0202's columns.
_MY_FLAGS_SQL = (
    "SELECT DISTINCT ON (security_id, value_type) "
    "id, security_id, value_type, note, opened_at, closed_at, outcome, outcome_note "
    "FROM value_flag "
    "WHERE user_id = %s AND security_id = ANY(%s) AND value_type = ANY(%s) AND value_ref = '' "
    "ORDER BY security_id, value_type, (closed_at IS NULL) DESC, closed_at DESC NULLS FIRST, "
    "opened_at DESC, id DESC"
)


@dataclass(frozen=True)
class MyFlag:
    """The caller's own flag on one value (``value_flag``): open while ``closed_at`` is None; ``outcome``
    ``fixed`` / ``kept`` / ``withdrawn`` once closed, with the steward's ``outcome_note``."""

    id: UUID
    note: str | None
    opened_at: datetime
    closed_at: datetime | None
    outcome: str | None
    outcome_note: str | None


@dataclass(frozen=True)
class AppliedValue:
    """The value the meters show. ``source_ref`` is the stored reference (the answer turns it into a safe URL).
    Share count: ``shares`` and the ADS fields. Cash and burn: ``cash_usd`` / ``quarterly_burn_usd`` /
    ``investments_usd`` in the stored ``currency`` (None = not recorded), ``currency_symbol`` the ONE rule's label
    (``domain.cash_currency``), ``pace`` the stored year-to-date pace (``domain.burn_pace.stored_pace``).
    """

    source: str | None
    source_ref: str | None
    valid_from: date
    filing_accepted: datetime | None
    set_at: datetime | None
    shares: float | None = None
    ads_ratio: int | None = None
    ads_ratio_status: str | None = None
    cash_usd: float | None = None
    quarterly_burn_usd: float | None = None
    investments_usd: float | None = None
    currency: str | None = None
    currency_symbol: str | None = None
    pace: RunwayPace | None = None


@dataclass(frozen=True)
class FilingRef:
    """A candidate as a user sees it (B1 (b)): its filing's reference, date and acceptance, the user's line, and
    (awaiting only) the located passages. Never a figure, a flag name or the candidate's note."""

    source_ref: str | None
    event_date: date | None
    filing_accepted: datetime | None
    line: str
    located_passages: tuple[Mapping[str, Any], ...] = ()


@dataclass(frozen=True)
class UserValue:
    """One value of one company, as a user reads it."""

    fact_type: FactType
    state: ValueState
    reason: str | None
    label: str
    set_by: SetBy | None
    value: AppliedValue | None
    annual_line: str | None
    pending: FilingRef | None
    awaiting: FilingRef | None
    my_flag: MyFlag | None


@dataclass(frozen=True)
class UserCompanyValues:
    """Both values of one company. ``cik`` is for the answer's EDGAR link (an accession-shaped reference)."""

    security_id: UUID
    ticker: str | None
    name: str | None
    cik: str | None
    shares: UserValue
    cash_burn: UserValue
    runway_applies: bool = True


def _f(v: Any) -> float | None:
    return float(v) if v is not None else None


def _applied(fact_type: FactType, row: Mapping[str, Any], *, pace: bool = True) -> AppliedValue:
    common: dict[str, Any] = dict(
        source=row.get("source"),
        source_ref=row.get("source_ref"),
        valid_from=row["valid_from"],
        filing_accepted=row.get("filing_accepted"),
        set_at=row.get("recorded_at"),
    )
    if fact_type == "shares_outstanding":
        return AppliedValue(
            **common,
            shares=_f(row.get("shares")),
            ads_ratio=row.get("ads_ratio"),
            ads_ratio_status=row.get("ads_ratio_status"),
        )
    symbol = currency_symbol(row.get("currency"), source=row.get("source"))
    return AppliedValue(
        **common,
        cash_usd=_f(row.get("cash_usd")),
        quarterly_burn_usd=_f(row.get("quarterly_burn_usd")),
        investments_usd=_f(row.get("investments_usd")),
        currency=row.get("currency"),
        currency_symbol=symbol,
        pace=stored_pace(row, symbol=symbol) if pace else None,
    )


def _filing(cand: Mapping[str, Any], line: str, *, passages: bool) -> FilingRef:
    """The candidate's filing only: reference, date, acceptance (and its passages). The figures, flags and note
    are never copied (B1 (b))."""
    return FilingRef(
        source_ref=cand.get("source_ref"),
        event_date=cand.get("event_date"),
        filing_accepted=cand.get("filing_accepted"),
        line=line,
        located_passages=tuple(cand.get("located_passages") or ()) if passages else (),
    )


def _user_value(
    fact_type: FactType, st: DerivedState, flag: MyFlag | None, *, runway: bool = True
) -> UserValue:
    shown = st.meter_value
    kind: SetBy | None = st.ratifier if shown is not None else None
    pending = (
        _filing(st.pending, words.pending_line(st.pending.get("event_date")), passages=False)
        if shown is not None and st.pending is not None
        else None
    )
    awaiting = (
        _filing(
            st.awaiting,
            words.awaiting_line(fact_type, st.awaiting.get("event_date")),
            passages=True,
        )
        if st.state is ValueState.AWAITING_STEWARD and st.awaiting is not None
        else None
    )
    annual = (
        words.annual_line(shown.get("source"), shown["valid_from"])
        if shown is not None and fact_type == "shares_outstanding"
        else None
    )
    return UserValue(
        fact_type=fact_type,
        state=st.state,
        reason=st.reason,
        label=words.state_label(st.state, st.reason, kind),
        set_by=kind,
        value=_applied(fact_type, shown, pace=runway) if shown is not None else None,
        annual_line=annual,
        pending=pending,
        awaiting=awaiting,
        my_flag=flag,
    )


def _companies(conn: psycopg.Connection, ids: list[UUID]) -> dict[UUID, dict]:
    """``{id: {ticker, name, cik, sector, instrument_kind}}`` from the master (data tenant) for the ids that exist;
    one statement. ``sector`` and ``instrument_kind`` feed the runway rule only."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT id, ticker, name, cik, sector, instrument_kind FROM security_master"
            " WHERE id = ANY(%s) AND tenant_id = %s",
            (ids, DATA_TENANT_ID),
        )
        return {r["id"]: r for r in cur.fetchall()}


def my_flags(
    conn: psycopg.Connection, security_ids: Iterable[UUID], *, user_id: UUID
) -> dict[tuple[UUID, str], MyFlag]:
    """``{(security_id, value_type): the caller's flag}``: their open flag on the value, else their newest closed
    one; absent when they never flagged it. ONE statement, filtered to ``user_id`` in SQL."""
    ids = list(dict.fromkeys(security_ids))
    if not ids:
        return {}
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_MY_FLAGS_SQL, (user_id, ids, list(_FLAG_VALUE_TYPES)))
        rows = cur.fetchall()
    return {
        (r["security_id"], r["value_type"]): MyFlag(
            id=r["id"],
            note=r["note"],
            opened_at=r["opened_at"],
            closed_at=r["closed_at"],
            outcome=r["outcome"],
            outcome_note=r["outcome_note"],
        )
        for r in rows
    }


def values_for(
    conn: psycopg.Connection, security_ids: Iterable[UUID], *, user_id: UUID
) -> dict[UUID, UserCompanyValues]:
    """Both values of each company the master holds, as ``user_id`` reads them, in the order given (an id the
    master does not hold is absent; a repeated id answers once). A fixed number of statements whatever the set's
    size. Read-only."""
    ids = list(dict.fromkeys(security_ids))
    if not ids:
        return {}
    master = _companies(conn, ids)
    known = [i for i in ids if i in master]
    if not known:
        return {}
    states = {ft: read_states(conn, known, ft) for ft in FACT_TYPES}
    flags = my_flags(conn, known, user_id=user_id)
    out: dict[UUID, UserCompanyValues] = {}
    for sid in known:
        m = master[sid]
        applies = runway_applies(
            sector=m["sector"], ticker=m["ticker"], instrument_kind=m["instrument_kind"]
        )
        by_type = {
            ft: _user_value(ft, states[ft][sid], flags.get((sid, ft)), runway=applies)
            for ft in FACT_TYPES
        }
        out[sid] = UserCompanyValues(
            security_id=sid,
            ticker=m["ticker"],
            name=m["name"],
            cik=m["cik"],
            shares=by_type["shares_outstanding"],
            cash_burn=by_type["cash_burn"],
            runway_applies=applies,
        )
    return out


__all__ = [
    "AppliedValue",
    "FilingRef",
    "MyFlag",
    "UserCompanyValues",
    "UserValue",
    "my_flags",
    "values_for",
]
