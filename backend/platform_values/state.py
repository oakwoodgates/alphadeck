"""THE STATE READ of a platform value (V2 W2a): for a company and a value type (a share count, cash/burn), the
value the meters show and its state — no value yet (with the reason) / from the filing / awaiting the steward /
verified — plus a newer candidate awaiting the steward beside a shown value (``docs/PLATFORM_VALUES.md``).

The value is read EXACTLY as the meters read it: a point-in-time view on the data tenant (record clock, as of
today's market date, known now), the same accessor (``shares_outstanding_facts`` / ``cash_burn_facts``; set-aside
versions already skipped), then the one pick (``domain.platform_values.pick_current``). The candidate, its standing
decision and the pin come from ``repositories.platform_value_repo``; the state is ``derive_state``'s, nowhere
else's. A candidate is never a value (``DerivedState.meter_value``).

Read-only, no lock: a writer (the machine, W2b; the steward, W2c) re-reads the state through ``read_state`` inside
the company write lock before it writes. ``read_states`` is the same read for a set of companies in a fixed number
of queries (the steward's queue, W4a); ``read_state`` is ``read_states`` for one. Nothing on the call path imports
this package.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from uuid import UUID

import psycopg

from domain.market_time import market_today
from domain.platform_values import (
    FACT_TYPES,
    DerivedState,
    FactType,
    ValueState,
    derive_state,
    pick_current,
)
from domain.scopes import DATA_TENANT_ID
from repositories import platform_value_repo as repo

# How many candidates and decisions a company read carries per type (newest first).
CANDIDATES_SHOWN = 20
DECISIONS_SHOWN = 50


def winners(
    conn: psycopg.Connection,
    security_id: UUID,
    fact_type: FactType,
    *,
    asof: date | None = None,
    known_at: datetime | None = None,
) -> list[Mapping[str, Any]]:
    """The per-filing rows the meters read for (company, type): one per ``source_ref`` (its newest version, set-aside
    versions skipped), data tenant, record clock, as of ``asof`` (default today's market date) and ``known_at``
    (default now). ``pick_current`` over these is the value shown; ``correction_wins`` judges a steward's new
    value against them."""
    from signals.base import PointInTimeData  # the concrete live view (the meters' own accessor)

    pit = PointInTimeData(
        conn,
        asof=asof or market_today(),
        known_at=known_at,
        workspace=DATA_TENANT_ID,  # a DATA-only view: the two tables live in the data tenant
        basket=[security_id],
    )
    if fact_type == "shares_outstanding":
        return pit.shares_outstanding_facts(security_id)
    return pit.cash_burn_facts(security_id)


def read_states(
    conn: psycopg.Connection,
    security_ids: Iterable[UUID],
    fact_type: FactType,
    *,
    asof: date | None = None,
) -> dict[UUID, DerivedState]:
    """The state of one value type for a SET of companies, ``{security_id: DerivedState}`` (one entry per id, an
    unknown id reads ``no_value`` / ``not-extracted``), in a FIXED number of queries whatever the set's size: one
    point-in-time view over the whole set (the meters' accessor, prefetched in one read), the newest candidate per
    company, their standing decisions and the newest pin event per company; then ``pick_current`` and
    ``derive_state`` per company, exactly as for one (``tests/platform_values/test_state_batched.py``). Read-only.
    """
    from signals.base import PointInTimeData  # the concrete live view (the meters' own accessor)

    ids = list(dict.fromkeys(security_ids))
    if not ids:
        return {}
    day = asof or market_today()
    pit = PointInTimeData(conn, asof=day, workspace=DATA_TENANT_ID, basket=ids)
    facts = (
        pit.shares_outstanding_facts if fact_type == "shares_outstanding" else pit.cash_burn_facts
    )
    cands = repo.latest_candidates(conn, ids, fact_type)
    standing = repo.standing_decisions_for_candidates(conn, [c["id"] for c in cands.values()])
    pins = repo.latest_pins(conn, ids, fact_type)
    out: dict[UUID, DerivedState] = {}
    for sid in ids:
        cand = cands.get(sid)
        decision = standing.get(cand["id"]) if cand is not None else None
        pin = pins.get(sid)
        out[sid] = derive_state(
            picked=pick_current(facts(sid), not_after=day),
            candidate=cand,
            candidate_decision=decision["action"] if decision is not None else None,
            pinned=pin is not None and pin["action"] == "pin",
        )
    return out


def read_state(
    conn: psycopg.Connection,
    security_id: UUID,
    fact_type: FactType,
    *,
    asof: date | None = None,
) -> DerivedState:
    """The state of one platform value now (``asof`` defaults to today's market date): the picked value (what the
    meters show; never a value dated after ``asof``), the newest candidate and its standing decision, the pin —
    through ``derive_state``. ``read_states`` for one company, so there is one way to read a state. Read-only.
    """
    return read_states(conn, [security_id], fact_type, asof=asof)[security_id]


@dataclass(frozen=True)
class TypeValues:
    """One value type of a company: its ``state`` plus the steward's working set — every stored ``versions`` (oldest
    first, each with ``set_aside``), the newest ``candidates`` (newest first), ``decisions`` (newest first, each
    with ``undone``) and the newest ``pin`` event (``pin`` / ``unpin``, who, when, the note; None = never pinned).
    """

    fact_type: FactType
    state: DerivedState
    versions: list[dict]
    candidates: list[dict]
    decisions: list[dict]
    pin: dict | None = None


@dataclass(frozen=True)
class CompanyValues:
    """Both platform values of one company (the steward's company page, W2c)."""

    security_id: UUID
    ticker: str | None
    name: str | None
    shares: TypeValues
    cash_burn: TypeValues


def read_company(
    conn: psycopg.Connection, security_id: UUID, *, asof: date | None = None
) -> CompanyValues:
    """Both values of one company with their versions, candidates and decisions. ``LookupError`` for a company the
    master does not hold (data tenant). Read-only."""
    master = repo.companies(conn, [security_id]).get(security_id)
    if master is None:
        raise LookupError(f"no company with id {security_id}")
    day = asof or market_today()
    by_type = {
        ft: TypeValues(
            fact_type=ft,
            state=read_state(conn, security_id, ft, asof=day),
            versions=repo.versions(conn, security_id, ft),
            candidates=repo.candidates_for(conn, security_id, ft, limit=CANDIDATES_SHOWN),
            decisions=repo.decisions_for(conn, security_id, ft, limit=DECISIONS_SHOWN),
            pin=repo.latest_pin(conn, security_id, ft),
        )
        for ft in FACT_TYPES
    }
    return CompanyValues(
        security_id=security_id,
        ticker=master["ticker"],
        name=master["name"],
        shares=by_type["shares_outstanding"],
        cash_burn=by_type["cash_burn"],
    )


@dataclass(frozen=True)
class AwaitingItem:
    """One company-and-type with a flagged candidate the steward has not decided: either no value is shown
    (``state.state`` is ``awaiting_steward``) or the candidate is newer than the value shown (``state.pending``).
    """

    security_id: UUID
    ticker: str | None
    name: str | None
    fact_type: FactType
    candidate: dict
    state: DerivedState


def awaiting(
    conn: psycopg.Connection, *, limit: int | None = 200, asof: date | None = None
) -> list[AwaitingItem]:
    """The steward's queue, newest candidate first, at most ``limit`` (None = all): every company-and-type whose
    newest candidate is an undecided flag that the state says still waits (awaiting, or pending beside an older
    value). A flag older than the value shown is not listed. The states come from ``read_states``, one batch per
    value type, so the number of queries does not grow with the list. Read-only."""
    day = asof or market_today()
    # the raw list can hold flags the state drops (older than the value shown), so it is read whole and cut here
    rows = repo.awaiting(conn, limit=None)
    states: dict[str, dict[UUID, DerivedState]] = {}
    for ft in FACT_TYPES:
        ids = [r["security_id"] for r in rows if r["fact_type"] == ft]
        states[ft] = read_states(conn, ids, ft, asof=day)
    out: list[AwaitingItem] = []
    for row in rows:
        fact_type: FactType = row["fact_type"]
        st = states[fact_type][row["security_id"]]
        shown = st.awaiting if st.state is ValueState.AWAITING_STEWARD else st.pending
        if shown is None or shown["id"] != row["id"]:
            continue
        cand = {k: v for k, v in row.items() if k not in ("ticker", "name")}
        out.append(
            AwaitingItem(row["security_id"], row["ticker"], row["name"], fact_type, cand, st)
        )
        if limit is not None and len(out) >= limit:
            break
    return out


@dataclass(frozen=True)
class AwaitingCompany:
    """One company with anything waiting for the steward (the queue's row, approved B3 (a)): its waiting share
    count and/or cash/burn (``awaiting``'s items; None = that value waits for nothing). ``waiting_since`` is the
    older of the two waiting candidates' ``recorded_at``."""

    security_id: UUID
    ticker: str | None
    name: str | None
    shares: AwaitingItem | None
    cash_burn: AwaitingItem | None

    @property
    def waiting_since(self) -> datetime:
        return min(
            i.candidate["recorded_at"] for i in (self.shares, self.cash_burn) if i is not None
        )


def awaiting_companies(
    conn: psycopg.Connection, *, asof: date | None = None
) -> list[AwaitingCompany]:
    """``awaiting`` grouped into one row per company (both values on it), in no promised order: the caller orders
    it (the steward's queue: by card count, then wait). Read-only."""
    by_company: dict[UUID, dict[str, Any]] = {}
    for item in awaiting(conn, limit=None, asof=asof):
        row = by_company.setdefault(
            item.security_id,
            {"security_id": item.security_id, "ticker": item.ticker, "name": item.name},
        )
        row["shares" if item.fact_type == "shares_outstanding" else "cash_burn"] = item
    return [
        AwaitingCompany(
            security_id=r["security_id"],
            ticker=r["ticker"],
            name=r["name"],
            shares=r.get("shares"),
            cash_burn=r.get("cash_burn"),
        )
        for r in by_company.values()
    ]


__all__ = [
    "CANDIDATES_SHOWN",
    "DECISIONS_SHOWN",
    "winners",
    "read_states",
    "read_state",
    "TypeValues",
    "CompanyValues",
    "read_company",
    "AwaitingItem",
    "awaiting",
    "AwaitingCompany",
    "awaiting_companies",
]
