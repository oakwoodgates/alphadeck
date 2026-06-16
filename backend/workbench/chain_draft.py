"""The narrative→chain drafter's RESOLVER (Slice 5a) — the exact-membership decider.

S5 (the LLM decomposition, Slice 5b) proposes value-chain segments and the names that sit in them. A
proposed name is a **discovery suggestion, never a decision** (INVARIANT #2): the model's name/ticker is a
key, never an id. This module runs every proposed name through THIS tenant's security master and decides:

- **PLACED** — a unique EXACT ticker match OR a unique EXACT name match → the master row's ``security_id``
  is assigned (auto-place as a drafted member). Exact membership, never a fuzzy judgment.
- **AMBIGUOUS** — several / partial / token-only matches → the operator PICKS from the candidates (each
  shown with ticker + CIK so a homonym is disambiguated by sight). A lone substring match is **deliberately
  here, not PLACED** — a token overlap is the homonym-trap heuristic ("$48B Oklo Technologies"), and
  auto-place must never rest on a judgment call.
- **ABSENT** — no master row → surfaced as "suggested, not in your universe", never guessed onto a ticker.

It is **read-only** (it never ingests, never writes) and it sources **no number**: a PLACED name is still
UNSCORED until the operator runs the existing extract→ratify loop on it. The eventual persistence is the
operator's promote (which re-checks membership — `app/routers/workbench.py`); nothing here touches the spine.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

import psycopg

from db.session import DEFAULT_TENANT_ID
from domain.base import DomainModel
from domain.security import Security
from securities import master

# How many master rows to offer the operator when a proposed name is ambiguous (the pick list).
_CANDIDATE_LIMIT = 10


class ProposedPlacement(DomainModel):
    """One name the decomposition proposes for a segment.

    ``ticker`` is the model's BEST GUESS — used only as a key to look up an EXACT master row, NEVER trusted
    as the id (a wrong guess simply fails to match and the name falls to the operator's pick). ``prose`` is
    the drafted thesis-fit reasoning — a display string carried through; it is never a fact and never stored
    here.
    """

    name: str
    ticker: str | None = None
    prose: str = ""


class ProposedSegment(DomainModel):
    """A proposed value-chain link and the names the model placed in it (structure only — no score)."""

    label: str
    descriptor: str | None = None
    placements: list[ProposedPlacement] = []


class PlacementStatus(StrEnum):
    PLACED = "placed"  # a unique EXACT master member → security_id assigned (auto-place)
    AMBIGUOUS = "ambiguous"  # several / partial matches → the operator PICKS (membership decides)
    ABSENT = "absent"  # no master row → "suggested, not in your universe", never placed


class SecurityCandidate(DomainModel):
    """A master row offered for the operator to pick (ticker + CIK shown so a homonym is disambiguated)."""

    security_id: UUID
    ticker: str
    name: str | None = None
    cik: str | None = None


class ResolvedPlacement(DomainModel):
    """A proposed name after resolution against the master. ``security_id`` is set IFF ``PLACED``;
    ``candidates`` is non-empty IFF ``AMBIGUOUS``. The model's ``name`` / ``ticker`` / ``prose`` are
    preserved (so the UI can show what the model proposed even when it didn't resolve)."""

    name: str
    ticker: str | None
    prose: str
    segment: str
    status: PlacementStatus
    security_id: UUID | None = None
    candidates: list[SecurityCandidate] = []


class ResolvedSegment(DomainModel):
    label: str
    descriptor: str | None = None


class ResolvedChain(DomainModel):
    """The decomposition after every proposed name is run through the master: the segments, and each
    placement tagged PLACED / AMBIGUOUS / ABSENT. STRUCTURE + names only — no score, no fact, no number.
    """

    segments: list[ResolvedSegment] = []
    placements: list[ResolvedPlacement] = []


def _candidate(s: Security) -> SecurityCandidate:
    return SecurityCandidate(security_id=s.id, ticker=s.ticker, name=s.name, cik=s.cik)


def _resolve_one(
    conn: psycopg.Connection, p: ProposedPlacement, segment: str, *, tenant_id: UUID
) -> ResolvedPlacement:
    base = {"name": p.name, "ticker": p.ticker, "prose": p.prose, "segment": segment}
    ticker = (p.ticker or "").strip().upper()
    name = p.name.strip()

    # 1. unique EXACT ticker match — `ids_for_tickers` is an exact lookup and returns one row per ticker,
    #    so a hit IS unique by construction. The model's ticker decides nothing on its own: only a master
    #    row carrying exactly that ticker does.
    if ticker:
        sid = master.ids_for_tickers(conn, [ticker], tenant_id=tenant_id).get(ticker)
        if sid is not None:
            return ResolvedPlacement(**base, status=PlacementStatus.PLACED, security_id=sid)

    # 2. the substring net by name, then a UNIQUE exact NAME match within it (case-insensitive). Two rows
    #    sharing the exact name (e.g. a dual-class pair) is NOT unique → it falls through to the pick.
    candidates = master.search(conn, name, tenant_id=tenant_id, limit=_CANDIDATE_LIMIT)
    exact = [c for c in candidates if (c.name or "").strip().upper() == name.upper()]
    if len(exact) == 1:
        return ResolvedPlacement(**base, status=PlacementStatus.PLACED, security_id=exact[0].id)

    # 3. any rows but no unique exact match → the operator PICKS (a token/partial match is NOT membership);
    #    no rows at all → ABSENT.
    if candidates:
        return ResolvedPlacement(
            **base,
            status=PlacementStatus.AMBIGUOUS,
            candidates=[_candidate(c) for c in candidates],
        )
    return ResolvedPlacement(**base, status=PlacementStatus.ABSENT)


def resolve_placements(
    conn: psycopg.Connection,
    segments: list[ProposedSegment],
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> ResolvedChain:
    """Resolve every proposed name against THIS tenant's master (INVARIANT #2: the model is a discovery NET;
    exact master membership DECIDES). Read-only — never ingests, never writes, sources no number.

    Per name: a unique EXACT ticker match OR a unique EXACT name match → PLACED with the master row's id
    (auto-place; the exact TICKER takes precedence — it is the operator's primary carrier). Several /
    partial / token-only matches → AMBIGUOUS (the operator picks; ticker + CIK disambiguate a homonym). No
    master row → ABSENT. A PLACED name is always drafted, prunable, and UNSCORED until the operator
    extract→ratifies it — so a mismatched model guess is caught at review, never silently scored.
    """
    return ResolvedChain(
        segments=[ResolvedSegment(label=s.label, descriptor=s.descriptor) for s in segments],
        placements=[
            _resolve_one(conn, p, s.label, tenant_id=tenant_id)
            for s in segments
            for p in s.placements
        ],
    )
