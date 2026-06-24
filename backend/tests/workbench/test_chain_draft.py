"""The narrative→chain resolver (Slice 5a) — exact membership decides; the model only suggests.

These pin INVARIANT #2 at the resolver: a name auto-places ONLY on a unique exact ticker/name master match;
a token/partial match (the homonym trap) falls to the operator's pick; an unknown name is ABSENT. No write,
no number — a placed name is still unscored until the operator extract→ratifies it (covered elsewhere).
"""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from ingest.edgar.fulltext import Filer
from workbench.chain_draft import (
    PlacementStatus,
    ProposedPlacement,
    ProposedSegment,
    resolve_discovered_chain,
    resolve_placements,
)
from workbench.discovery import DiscoveredUniverse


def _insert(db, ticker, *, name=None, cik=None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _seg(*placements: ProposedPlacement, label="Reactor developers", descriptor=None):
    return ProposedSegment(label=label, descriptor=descriptor, placements=list(placements))


def test_exact_ticker_match_autoplaces(db):
    """A model best-guess ticker that EXACTLY equals a master ticker → auto-place with the MASTER row's id
    (the usability path that carries clean proposals). The id is the master's, never the model's string.
    """
    oklo = _insert(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    chain = resolve_placements(
        db,
        [_seg(ProposedPlacement(name="Oklo Inc.", ticker="OKLO", prose="the reactor developer"))],
        tenant_id=DEFAULT_TENANT_ID,
    )
    (p,) = chain.placements
    assert p.status is PlacementStatus.PLACED
    assert p.security_id == oklo
    assert p.segment == "Reactor developers" and p.prose == "the reactor developer"


def test_exact_name_match_autoplaces_without_a_ticker(db):
    """No ticker, but the proposed name EXACTLY equals a master name (unique) → auto-place."""
    leu = _insert(db, "LEU", name="Centrus Energy Corp.")
    chain = resolve_placements(
        db, [_seg(ProposedPlacement(name="Centrus Energy Corp."))], tenant_id=DEFAULT_TENANT_ID
    )
    (p,) = chain.placements
    assert p.status is PlacementStatus.PLACED and p.security_id == leu


def test_bare_name_matching_several_rows_is_ambiguous_not_placed(db):
    """The homonym trap ('$48B Oklo Technologies'): a bare name that substring-matches MULTIPLE master rows
    is never auto-placed — it becomes an operator pick, each candidate carrying ticker + CIK to disambiguate.
    """
    _insert(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    _insert(db, "OKT", name="Oklo Technologies Inc.", cik="0009999999")
    chain = resolve_placements(
        db, [_seg(ProposedPlacement(name="Oklo"))], tenant_id=DEFAULT_TENANT_ID
    )
    (p,) = chain.placements
    assert p.status is PlacementStatus.AMBIGUOUS
    assert p.security_id is None
    assert {c.ticker for c in p.candidates} == {"OKLO", "OKT"}
    assert all(c.cik for c in p.candidates)  # CIK surfaced for sight-disambiguation


def test_lone_partial_match_is_ambiguous_not_placed(db):
    """A LONE substring/token match is NOT membership — it falls to the operator's pick, never auto-placed.
    Auto-place rests only on an EXACT ticker or EXACT name, never on a token-overlap judgment call.
    """
    _insert(db, "OKLO", name="Oklo Inc.")
    chain = resolve_placements(
        db, [_seg(ProposedPlacement(name="Oklo"))], tenant_id=DEFAULT_TENANT_ID
    )
    (p,) = chain.placements
    assert p.status is PlacementStatus.AMBIGUOUS and p.security_id is None
    assert [c.ticker for c in p.candidates] == ["OKLO"]


def test_absent_when_no_master_row(db):
    """A name with no master row at all → ABSENT ('suggested, not in your universe'), never guessed onto a
    ticker."""
    _insert(db, "OKLO", name="Oklo Inc.")
    chain = resolve_placements(
        db,
        [_seg(ProposedPlacement(name="Nonexistent Holdings", ticker="ZZZZ"))],
        tenant_id=DEFAULT_TENANT_ID,
    )
    (p,) = chain.placements
    assert p.status is PlacementStatus.ABSENT
    assert p.security_id is None and p.candidates == []


def test_unresolvable_ticker_falls_back_to_exact_name(db):
    """A best-guess ticker that matches NO master row doesn't block resolution — the unique exact NAME match
    still auto-places (the ticker is a key that simply missed, never a veto)."""
    leu = _insert(db, "LEU", name="Centrus Energy Corp.")
    chain = resolve_placements(
        db,
        [_seg(ProposedPlacement(name="Centrus Energy Corp.", ticker="BOGUS"))],
        tenant_id=DEFAULT_TENANT_ID,
    )
    (p,) = chain.placements
    assert p.status is PlacementStatus.PLACED and p.security_id == leu


def test_name_and_ticker_exact_match_different_rows_is_ambiguous(db):
    """The invariant-#2 line: when the exact name and the exact ticker resolve to DIFFERENT master rows, that
    contradiction is not a confident match — it falls to the operator's pick (both rows surfaced), never
    auto-placed (choosing ticker-over-name when they disagree would be a judgment call)."""
    oklo = _insert(db, "OKLO", name="Oklo Inc.")
    smr = _insert(db, "SMR", name="NuScale Power Corporation")
    chain = resolve_placements(
        db,
        [_seg(ProposedPlacement(name="Oklo Inc.", ticker="SMR"))],  # name -> OKLO, ticker -> SMR
        tenant_id=DEFAULT_TENANT_ID,
    )
    (p,) = chain.placements
    assert p.status is PlacementStatus.AMBIGUOUS
    assert p.security_id is None
    assert {c.security_id for c in p.candidates} == {oklo, smr}  # both surfaced for the pick


def test_preserves_segments_and_prose(db):
    """Structure is carried through: the resolved chain keeps every segment (label + descriptor) and each
    placement's segment + prose, whatever its resolution status."""
    _insert(db, "OKLO", name="Oklo Inc.")
    segments = [
        ProposedSegment(
            label="Reactor developers",
            descriptor="catalyst-rich",
            placements=[ProposedPlacement(name="Oklo Inc.", ticker="OKLO", prose="lead SMR dev")],
        ),
        ProposedSegment(
            label="Enrichment & fuel",
            placements=[ProposedPlacement(name="Mystery Fuel Co", prose="HALEU supplier")],
        ),
    ]
    chain = resolve_placements(db, segments, tenant_id=DEFAULT_TENANT_ID)
    assert [(s.label, s.descriptor) for s in chain.segments] == [
        ("Reactor developers", "catalyst-rich"),
        ("Enrichment & fuel", None),
    ]
    by_seg = {p.segment: p for p in chain.placements}
    assert by_seg["Reactor developers"].prose == "lead SMR dev"
    assert by_seg["Enrichment & fuel"].status is PlacementStatus.ABSENT  # not in the master


# --- Slice 4a: the EDGAR-first chain reconciler — the deterministic universe owns completeness ---


def _universe(*, placed=(), verify=()) -> DiscoveredUniverse:
    """Build a DiscoveredUniverse from ``(cik, ticker, name)`` triples; each carries a fresh ``security_id``
    (the discovery layer already resolved it by CIK — the reconciler trusts that, never re-checks the master).
    """
    u = DiscoveredUniverse()
    for cik, ticker, name in placed:
        u.placed[cik] = uuid.uuid4()
        u.filers[cik] = Filer(cik=cik, name=name, ticker=ticker, keywords={"kw"})
    for cik, ticker, name in verify:
        u.verify[cik] = uuid.uuid4()
        u.filers[cik] = Filer(cik=cik, name=name, ticker=ticker, keywords={"kw"})
    return u


def test_discovered_chain_places_matched_by_cik(db):
    """An organizer placement matching a discovered filer (exact ticker) is PLACED / VERIFY by that CIK's id —
    CIK-exact membership, the cleanest #2. Nothing is dropped, so there is no 'Discovered' bucket.
    """
    u = _universe(
        placed=[("0000000001", "OKLO", "Oklo Inc.")],
        verify=[("0000000002", "ALKS", "Alkermes plc")],
    )
    segs = [
        _seg(
            ProposedPlacement(name="Oklo Inc.", ticker="OKLO", prose="reactor dev"),
            ProposedPlacement(name="Alkermes plc", ticker="ALKS", prose="ketamine adjacent"),
            label="Developers",
        )
    ]
    chain = resolve_discovered_chain(db, segs, u)
    by_t = {p.ticker: p for p in chain.placements}
    assert by_t["OKLO"].status is PlacementStatus.PLACED
    assert by_t["OKLO"].security_id == u.placed["0000000001"]
    assert by_t["OKLO"].prose == "reactor dev"  # the organizer's layout prose is carried
    assert by_t["ALKS"].status is PlacementStatus.VERIFY  # single-BROAD stays lower-confidence
    assert by_t["ALKS"].security_id == u.verify["0000000002"]
    assert all(s.label != "Discovered" for s in chain.segments)


def test_dropped_discovered_cik_surfaces_in_discovered_bucket(db):
    """THE completeness guarantee (per-CIK, not a count heuristic): the organizer arranges only ONE of three
    discovered names; the two it silently drops — a single miss invisible among a plausible many — BOTH reappear
    in 'Discovered' by their CIK, with their tier preserved. No deterministically-found name is ever lost.
    """
    u = _universe(
        placed=[("0000000001", "OKLO", "Oklo Inc."), ("0000000003", "SMR", "NuScale Power")],
        verify=[("0000000002", "ALKS", "Alkermes plc")],
    )
    # organizer emits OKLO only — SMR (placed) and ALKS (verify) are dropped from the layout
    segs = [_seg(ProposedPlacement(name="Oklo Inc.", ticker="OKLO", prose="reactor"), label="Devs")]
    chain = resolve_discovered_chain(db, segs, u)

    placed_ids = {p.security_id for p in chain.placements}
    # every discovered CIK's id is present — none silently lost
    assert u.placed["0000000001"] in placed_ids
    assert u.placed["0000000003"] in placed_ids
    assert u.verify["0000000002"] in placed_ids

    disc = [p for p in chain.placements if p.segment == "Discovered"]
    assert {p.ticker for p in disc} == {"SMR", "ALKS"}  # exactly the two the organizer dropped
    assert {p.status for p in disc} == {PlacementStatus.PLACED, PlacementStatus.VERIFY}  # tier kept
    assert any(s.label == "Discovered" for s in chain.segments)  # the fallback segment exists


def test_unmatched_name_falls_to_master_resolver_and_empty_universe_adds_no_bucket(db):
    """A name EFTS did NOT discover (a tail-sweep / off-universe name) resolves via the existing master resolver
    (PLACED by exact ticker / ABSENT) — not the CIK path. An empty discovery adds no 'Discovered' bucket.
    """
    leu = _insert(db, "LEU", name="Centrus Energy Corp.")
    segs = [
        _seg(
            ProposedPlacement(name="Centrus Energy Corp.", ticker="LEU", prose="enrichment"),
            ProposedPlacement(name="Nonexistent Holdings", ticker="ZZZZ", prose="foreign"),
            label="Fuel",
        )
    ]
    chain = resolve_discovered_chain(db, segs, DiscoveredUniverse(), tenant_id=DEFAULT_TENANT_ID)
    by_t = {p.ticker: p for p in chain.placements}
    assert by_t["LEU"].status is PlacementStatus.PLACED and by_t["LEU"].security_id == leu
    assert by_t["ZZZZ"].status is PlacementStatus.ABSENT
    assert all(s.label != "Discovered" for s in chain.segments)  # nothing discovered -> no bucket
