"""The narrative→chain resolver (Slice 5a) — exact membership decides; the model only suggests.

These pin INVARIANT #2 at the resolver: a name auto-places ONLY on a unique exact ticker/name master match;
a token/partial match (the homonym trap) falls to the operator's pick; an unknown name is ABSENT. No write,
no number — a placed name is still unscored until the operator extract→ratifies it (covered elsewhere).
"""

from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID
from workbench.chain_draft import (
    PlacementStatus,
    ProposedPlacement,
    ProposedSegment,
    resolve_placements,
)


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
