"""The meta index card switch at the one call seam (pure domain; no DB).

A card marked meta index has NO voting names: ``voting_thesis`` returns an empty basket for it, so the call is
the call of an empty roster. Only the copy the call reads is emptied — the card's own basket and links are
untouched (the dashboards and the ingest read them). Off, ``voting_thesis`` is exactly what it was.
"""

from __future__ import annotations

import uuid

from domain.thesis import BasketMember, Segment, Thesis, voting_thesis


def _thesis(*, meta_index: bool) -> Thesis:
    return Thesis(
        id=uuid.UUID(int=0x3E7A),
        name="Meta index switch",
        narrative="A broad list for watching where money moves.",
        segments=[Segment(label="Makers"), Segment(label="Buyers")],
        basket=[
            BasketMember(ticker="MIXA", role="r", segment="Makers", security_id=uuid.UUID(int=1)),
            BasketMember(ticker="MIXB", role="r", segment="Buyers", security_id=uuid.UUID(int=2)),
            BasketMember(ticker="MIXC", role="r", segment="Context", security_id=uuid.UUID(int=3)),
            BasketMember(ticker="MIXD", role="r", segment=None, security_id=uuid.UUID(int=4)),
        ],
        meta_index=meta_index,
    )


def test_the_switch_defaults_off():
    t = Thesis(id=uuid.uuid4(), name="n", narrative="x")
    assert t.meta_index is False


def test_a_meta_index_card_has_no_voting_names():
    t = _thesis(meta_index=True)
    voting = voting_thesis(t)
    assert voting.basket == []
    assert voting.meta_index is True and voting.id == t.id


def test_the_switch_empties_only_the_copy_the_call_reads():
    t = _thesis(meta_index=True)
    before = [m.model_copy() for m in t.basket]
    voting = voting_thesis(t)
    assert t.basket == before  # the card's own roster is untouched
    assert voting.segments == t.segments  # its links stay real links
    assert [m.ticker for m in t.basket] == ["MIXA", "MIXB", "MIXC", "MIXD"]


def test_off_the_voting_roster_is_unchanged():
    """Off is the rule as it was: only the silent rows go, the rest keep their order."""
    t = _thesis(meta_index=False)
    assert [m.ticker for m in voting_thesis(t).basket] == ["MIXA", "MIXB", "MIXD"]


def test_turning_the_switch_off_restores_the_voting_names():
    on = _thesis(meta_index=True)
    off = on.model_copy(update={"meta_index": False})
    assert voting_thesis(on).basket == []
    assert voting_thesis(off).basket == voting_thesis(_thesis(meta_index=False)).basket
