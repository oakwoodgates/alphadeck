"""The narrative→chain drafter's RESOLVER (Slice 5a) — the exact-membership decider.

S5 (the LLM decomposition, Slice 5b) proposes value-chain segments and the names that sit in them. A
proposed name is a **discovery suggestion, never a decision** (INVARIANT #2): the model's name/ticker is a
key, never an id. This module runs every proposed name through THIS tenant's security master and decides:

- **PLACED** — a unique EXACT ticker match OR a unique EXACT name match → the master row's ``security_id``
  is assigned (auto-place as a drafted member). Exact membership, never a fuzzy judgment.
- **AMBIGUOUS** — several / partial / token-only matches, OR a ticker/name CONTRADICTION (the exact ticker
  and the exact name resolve to DIFFERENT rows) → the operator PICKS from the candidates (each shown with
  ticker + CIK so a homonym is disambiguated by sight). A lone substring match is **deliberately here, not
  PLACED** — a token overlap is the homonym-trap heuristic ("$48B Oklo Technologies"), and auto-place must
  never rest on a judgment call.
- **ABSENT** — no master row → surfaced as "suggested, not in your universe", never guessed onto a ticker.

It is **read-only** (it never ingests, never writes) and it sources **no number**: a PLACED name is still
UNSCORED until the operator runs the existing extract→ratify loop on it. The eventual persistence is the
operator's promote (which re-checks membership — `app/routers/workbench.py`); nothing here touches the spine.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal
from uuid import UUID

import psycopg
from pydantic import ValidationError

from db.session import DEFAULT_TENANT_ID
from domain.base import DomainModel
from domain.security import Security
from securities import master
from workbench.discovery import DiscoveredUniverse

# How many master rows to offer the operator when a proposed name is ambiguous (the pick list).
_CANDIDATE_LIMIT = 10

# The fallback bucket for an EDGAR-discovered, in-master name the organizer didn't arrange into a segment. The
# deterministic discovery layer OWNS completeness; this guarantees no discovered name is silently dropped by the
# organizer's (LLM) layout step — the per-CIK reconciliation in ``resolve_discovered_chain`` populates it.
_DISCOVERED_LABEL = "Discovered"
_DISCOVERED_DESCRIPTOR = (
    "Found by EDGAR full-text search — not arranged into a segment by the draft."
)


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
    VERIFY = "verify"  # EDGAR-discovered, in-master, single BROAD keyword → lower-confidence, never auto-mixed
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
    preserved (so the UI can show what the model proposed even when it didn't resolve).

    ``matched_terms`` are the discovery keyword(s) the name's CIK hit (provenance — INVARIANT #6, and the
    on-screen tell for a colliding seed per #9: a placed name shows WHY it surfaced). Empty for an off-universe
    name resolved by the master rather than discovered by a term. Never a number (#3 — a keyword string).

    ``discovery_source`` records WHERE the name came from (provenance — INVARIANT #6): ``"edgar"`` = matched a
    CIK in the EDGAR-discovered universe; ``"off_universe"`` = resolved OUTSIDE that universe, via the
    sweep-augmented context. **It is NOT a hard claim the tail-sweep's web-search sourced the name** (``decompose``
    may surface an off-universe name from its own knowledge) — it means only "off the deterministic universe,"
    never "tail-sweep found this." Display-only like ``matched_terms``: never a number (#3), never promoted onto a
    ``BasketMember`` (#2). Defaults ``"edgar"`` (the conservative no-pill state) and is set ``"off_universe"`` in
    exactly ONE place — the ``_match_discovered_cik`` fork — so a stray construction can never over-claim.

    ``sector`` / ``exchange`` / ``listing_status`` are machine-parsed IDENTITY carried from the master (enriched
    just-in-time from EDGAR submissions before resolution) — display-only like ``matched_terms``: never a number
    (#3), never promoted onto a ``BasketMember`` (#2). ``listing_status`` is a LISTING-PRESENCE heuristic
    (``"active"`` / ``"inactive"``), NOT a delisting verdict: a PLACED name whose master row reads ``"inactive"``
    is DOWNGRADED to AMBIGUOUS (never auto-placed) with its own row as the single pick — a frictionless rescue,
    surfaced with a HEDGED flag ("no current listing found in EDGAR"), so a false-inactive costs one extra click,
    never a silent drop (#9). ``None`` when the row is un-enriched (the honest fallback — no flag, no gate).
    """

    name: str
    ticker: str | None
    prose: str
    segment: str
    status: PlacementStatus
    security_id: UUID | None = None
    candidates: list[SecurityCandidate] = []
    matched_terms: list[str] = []
    discovery_source: Literal["edgar", "off_universe"] = "edgar"
    sector: str | None = None
    exchange: str | None = None
    listing_status: str | None = None


class ResolvedSegment(DomainModel):
    label: str
    descriptor: str | None = None


class ResolvedChain(DomainModel):
    """The decomposition after every proposed name is run through the master: the segments, and each
    placement tagged PLACED / VERIFY / AMBIGUOUS / ABSENT. STRUCTURE + names only — no score, no fact, no
    number. (VERIFY is the EDGAR-first reconciler's lower-confidence tier; ``resolve_placements`` never emits
    it.)
    """

    segments: list[ResolvedSegment] = []
    placements: list[ResolvedPlacement] = []


def _candidate(s: Security) -> SecurityCandidate:
    return SecurityCandidate(security_id=s.id, ticker=s.ticker, name=s.name, cik=s.cik)


def _conflict_candidates(
    conn: psycopg.Connection, ticker_id: UUID, name_rows: list[Security], *, tenant_id: UUID
) -> list[SecurityCandidate]:
    """The operator-pick set for a ticker/name CONTRADICTION: the ticker's row + the exact-name row(s),
    deduped — so the operator sees both companies (ticker + CIK) and decides which the narrative meant.
    """
    rows: dict[UUID, Security] = {}
    ticker_row = master.get(conn, ticker_id, tenant_id=tenant_id)
    if ticker_row is not None:
        rows[ticker_row.id] = ticker_row
    for c in name_rows:
        rows.setdefault(c.id, c)
    return [_candidate(s) for s in rows.values()]


def _carry_identity_and_gate(
    conn: psycopg.Connection, placements: list[ResolvedPlacement], *, tenant_id: UUID
) -> None:
    """Carry machine-parsed IDENTITY (sector / exchange / listing status) from the master onto each resolved
    placement (display-only), and apply the STATUS-GATE: a PLACED name whose master row reads ``"inactive"``
    (no current listing found in EDGAR) is DOWNGRADED to AMBIGUOUS — never auto-placed — with its own row as the
    single pick (a frictionless rescue; one click re-places it). ``listing_status`` rides the placement so the
    FE shows a HEDGED flag, never a hard "delisted" verdict — precision is the operator deleting a visible flag,
    never a silent drop (#9). DB-only (no network — the resolver stays pure); a placement whose row is
    un-enriched or absent keeps ``listing_status=None`` (no flag, no gate — the honest fallback)."""
    secs = master.get_many(
        conn, [p.security_id for p in placements if p.security_id is not None], tenant_id=tenant_id
    )
    for p in placements:
        s = secs.get(p.security_id) if p.security_id is not None else None
        if s is None:
            continue
        p.sector, p.exchange, p.listing_status = s.sector, s.exchange, s.status
        if p.status is PlacementStatus.PLACED and s.status == "inactive":
            p.status = PlacementStatus.AMBIGUOUS
            p.candidates = [_candidate(s)]
            p.security_id = None  # PLACED invariant: security_id set IFF placed


def _resolve_one(
    conn: psycopg.Connection,
    p: ProposedPlacement,
    segment: str,
    *,
    tenant_id: UUID,
    discovery_source: Literal["edgar", "off_universe"] = "edgar",
) -> ResolvedPlacement:
    # `discovery_source` rides in `base`, so every return below (PLACED / AMBIGUOUS / ABSENT) carries it — an
    # off-universe name is tagged regardless of how it resolved. Defaults "edgar" (the conservative no-pill
    # state); the discovered-chain's off-universe fork passes "off_universe" explicitly.
    base = {
        "name": p.name,
        "ticker": p.ticker,
        "prose": p.prose,
        "segment": segment,
        "discovery_source": discovery_source,
    }
    ticker = (p.ticker or "").strip().upper()
    name = p.name.strip()

    # The substring net by name — also the candidate pool when nothing resolves uniquely.
    candidates = master.search(conn, name, tenant_id=tenant_id, limit=_CANDIDATE_LIMIT)

    # Two independent EXACT, UNIQUE signals. `ids_for_tickers` is an exact lookup (one row per ticker), so a
    # ticker hit is unique by construction; a name hit is unique only if exactly one master name equals it
    # (two rows sharing it — e.g. a dual-class pair — is NOT unique, so by_name stays None → the pick).
    by_ticker = (
        master.ids_for_tickers(conn, [ticker], tenant_id=tenant_id).get(ticker) if ticker else None
    )
    name_exact = [c for c in candidates if (c.name or "").strip().upper() == name.upper()]
    by_name = name_exact[0].id if len(name_exact) == 1 else None

    # A ticker/name CONTRADICTION (both resolve, to DIFFERENT rows) is not a confident match — choosing one
    # would be a judgment call (we can't know which the model meant) — so it goes to the operator's pick,
    # never auto-placed (INVARIANT #2). Surface BOTH rows for the pick.
    if by_ticker is not None and by_name is not None and by_ticker != by_name:
        return ResolvedPlacement(
            **base,
            status=PlacementStatus.AMBIGUOUS,
            candidates=_conflict_candidates(conn, by_ticker, name_exact, tenant_id=tenant_id),
        )

    # They agree, or only one fired → auto-place that exact member.
    placed = by_ticker if by_ticker is not None else by_name
    if placed is not None:
        return ResolvedPlacement(**base, status=PlacementStatus.PLACED, security_id=placed)

    # No unique exact match: any rows → the operator PICKS (a token/partial match is NOT membership — the
    # homonym-trap heuristic); none → ABSENT.
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
    (auto-place); if BOTH fire and resolve to DIFFERENT rows, that contradiction → AMBIGUOUS, never
    auto-placed (choosing one would be a judgment call). Several / partial / token-only matches → AMBIGUOUS
    (the operator picks; ticker + CIK disambiguate a homonym). No master row → ABSENT. A PLACED name is
    always drafted, prunable, and UNSCORED until the operator extract→ratifies it.
    """
    return ResolvedChain(
        segments=[ResolvedSegment(label=s.label, descriptor=s.descriptor) for s in segments],
        placements=[
            _resolve_one(conn, p, s.label, tenant_id=tenant_id)
            for s in segments
            for p in s.placements
        ],
    )


def _discovered_lookup(universe: DiscoveredUniverse) -> tuple[dict[str, str], dict[str, str]]:
    """Build the ``TICKER -> cik`` and ``NAME(upper) -> cik`` indexes over the PLACEABLE discovered filers
    (``placed`` ∪ ``verify``) so an organizer placement can be matched back to the CIK that resolved it. Only
    placeable CIKs are indexed — a match therefore always carries a ``security_id`` (one of the two tiers).
    """
    by_ticker: dict[str, str] = {}
    by_name: dict[str, str] = {}
    for cik in (*universe.placed, *universe.verify):
        f = universe.filers.get(cik)
        if f is None:
            continue
        if f.ticker:
            by_ticker.setdefault(f.ticker.strip().upper(), cik)
        if f.name:
            by_name.setdefault(f.name.strip().upper(), cik)
    return by_ticker, by_name


def _match_discovered_cik(
    p: ProposedPlacement, by_ticker: dict[str, str], by_name: dict[str, str]
) -> str | None:
    """Match one organizer placement to a discovered CIK — exact ticker first (the strongest key), then exact
    name. Returns the CIK or ``None`` (a tail-sweep / off-universe name the master resolver then handles).
    """
    ticker = (p.ticker or "").strip().upper()
    if ticker and ticker in by_ticker:
        return by_ticker[ticker]
    name = p.name.strip().upper()
    return by_name.get(name)


def resolve_discovered_chain(
    conn: psycopg.Connection,
    segments: list[ProposedSegment],
    universe: DiscoveredUniverse,
    *,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> ResolvedChain:
    """Resolve the organizer's layout against the EDGAR-first discovered universe (the chain reconciler, Slice
    4a). The deterministic discovery layer OWNS COMPLETENESS; the organizer (LLM) owns only LAYOUT — so:

    - An organizer placement that matches a discovered CIK (exact ticker / name) is PLACED or VERIFY by that
      CIK's ``security_id`` (the cleanest INVARIANT #2 — CIK-exact membership), carrying the organizer's segment
      + prose. The CIK is recorded as EMITTED.
    - A placement that matches NO discovered CIK is a tail-sweep / off-universe name → the existing master
      resolver (``_resolve_one``: PLACED / AMBIGUOUS / ABSENT). The organizer never sources a number (#3).
    - **The completeness guarantee — per-CIK, not a count heuristic:** after the layout pass, EVERY in-master
      discovered CIK NOT emitted is appended to a synthetic 'Discovered' segment by its CIK. A single name the
      organizer silently dropped — invisible to an eyeball among a plausible-looking many — is caught
      structurally. The organizer's mistakes cost segment arrangement, never a lost name.

    Read-only — no write, no number; a PLACED/VERIFY name is still UNSCORED until the operator extract→ratifies.
    """
    by_ticker, by_name = _discovered_lookup(universe)
    emitted: set[str] = set()
    placements: list[ResolvedPlacement] = []
    for s in segments:
        for p in s.placements:
            cik = _match_discovered_cik(p, by_ticker, by_name)
            if cik is None:
                # No CIK in the EDGAR-discovered universe → off-universe (came via the sweep-augmented context).
                # Tag it so the master resolver's placement (PLACED / AMBIGUOUS / ABSENT) carries the origin.
                placements.append(
                    _resolve_one(
                        conn, p, s.label, tenant_id=tenant_id, discovery_source="off_universe"
                    )
                )
                continue
            emitted.add(cik)
            in_placed = cik in universe.placed
            f = universe.filers.get(cik)
            placements.append(
                ResolvedPlacement(
                    name=p.name,
                    ticker=p.ticker,
                    prose=p.prose,
                    segment=s.label,
                    status=PlacementStatus.PLACED if in_placed else PlacementStatus.VERIFY,
                    security_id=universe.placed[cik] if in_placed else universe.verify[cik],
                    matched_terms=(
                        sorted(f.keywords) if f else []
                    ),  # the term(s) that surfaced it (#9 tell)
                    discovery_source="edgar",  # matched an EDGAR-discovered CIK
                )
            )

    out_segments = [ResolvedSegment(label=s.label, descriptor=s.descriptor) for s in segments]

    # Per-CIK reconciliation: diff the resolved discovered set against what the organizer emitted. Any dropped
    # CIK (placed OR verify) lands in 'Discovered' by its CIK — completeness is the deterministic layer's, never
    # the organizer's to lose.
    dropped = [cik for cik in (*universe.placed, *universe.verify) if cik not in emitted]
    if dropped:
        out_segments.append(
            ResolvedSegment(label=_DISCOVERED_LABEL, descriptor=_DISCOVERED_DESCRIPTOR)
        )
        for cik in dropped:
            f = universe.filers.get(cik)
            in_placed = cik in universe.placed
            placements.append(
                ResolvedPlacement(
                    name=f.name if f else "",
                    ticker=f.ticker if f else None,
                    prose="",
                    segment=_DISCOVERED_LABEL,
                    status=PlacementStatus.PLACED if in_placed else PlacementStatus.VERIFY,
                    security_id=universe.placed[cik] if in_placed else universe.verify[cik],
                    matched_terms=(
                        sorted(f.keywords) if f else []
                    ),  # the term(s) that surfaced it (#9 tell)
                    discovery_source="edgar",  # an EDGAR-discovered CIK, by construction
                )
            )

    # Carry machine-parsed identity onto every placed row + apply the listing-status gate (DB-only; the network
    # enrichment ran before this in execute_draft, so the resolver stays pure). An inactive PLACED name is
    # downgraded to a frictionless AMBIGUOUS pick, never silently dropped (#9).
    _carry_identity_and_gate(conn, placements, tenant_id=tenant_id)
    return ResolvedChain(segments=out_segments, placements=placements)


def proposed_from_decomposition(raw: dict | None) -> list[ProposedSegment]:
    """Parse the LLM decomposition's tool output (``{"segments": [...]}``) into proposed segments,
    DEFENSIVELY (fail-open): a missing / non-dict / malformed payload yields ``[]`` (an empty draft, never an
    error), and a single malformed segment is skipped without losing the rest. The resolver then decides
    membership; this only shapes the input."""
    if not isinstance(raw, dict):
        return []
    out: list[ProposedSegment] = []
    for s in raw.get("segments", []):
        try:
            out.append(ProposedSegment.model_validate(s))
        except ValidationError:
            continue
    return out
