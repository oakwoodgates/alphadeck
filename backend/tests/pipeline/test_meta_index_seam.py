"""The meta index card through the REAL call seam (``pipeline.core.assemble_from_pit``) — no DB.

A meta index card's call is exactly the call of a card whose voting basket is empty: a real breakout with a
core conviction, which arms the card when the switch is off, gives Incubating with no triggers when it is on.
Driven over the in-memory point-in-time view of ``tests/pipeline/test_silent_links_seam.py`` (the real
detectors, the theme broadcast, the rotation read, the split-night hold and ``assemble_call``), with the same
NON-VACUITY twin: the same fixture with the switch off DOES arm.
"""

from __future__ import annotations

import uuid
from typing import Any
from uuid import UUID

from domain.call import CallCard
from domain.config import DEFAULT_CONFIG
from domain.enums import Kind, State
from domain.thesis import BasketMember, Segment, Thesis
from pipeline.core import assemble_from_pit
from tests.pipeline.test_silent_links_seam import (
    _BARS,
    _CATALYSTS,
    _END,
    _TICKER,
    L,
    V,
    X,
    Y,
    _Pit,
    _sids_on,
)


def _thesis(members: list[tuple[UUID, str | None]], *, meta_index: bool) -> Thesis:
    return Thesis(
        id=uuid.UUID(int=0x3E7B),
        name="Meta index seam",
        narrative="None of a meta index card's names count toward the call.",
        segments=[Segment(label="Chain"), Segment(label="Discovered")],
        basket=[
            BasketMember(ticker=_TICKER[sid], role="r", security_id=sid, segment=seg)
            for sid, seg in members
        ],
        meta_index=meta_index,
    )


def _card(
    members: list[tuple[UUID, str | None]],
    *,
    meta_index: bool,
    bars: dict[UUID, list[dict[str, Any]]] | None = None,
) -> CallCard:
    pit = _Pit(_END, bars or _BARS, _CATALYSTS)
    return assemble_from_pit(pit, _thesis(members, meta_index=meta_index), _END, DEFAULT_CONFIG)


_ROSTER: list[tuple[UUID, str | None]] = [(X, "Chain"), (Y, "Chain"), (L, "Discovered"), (V, None)]


def test_non_vacuity_with_the_switch_off_the_breakout_arms_the_card():
    card = _card(_ROSTER, meta_index=False)
    assert card.state is State.ARMED
    assert card.armed_security_id in {X, L}
    assert any(t.kind is Kind.TECHNICAL_BREAKOUT for t in card.triggers_fired)


def test_with_the_switch_on_the_same_fixture_is_incubating_with_no_triggers():
    card = _card(_ROSTER, meta_index=True)
    assert card.state is State.INCUBATING
    assert card.armed_security_id is None
    assert not card.triggers_fired and not card.risk_signals
    assert not card.armed_members and not card.watch_members
    assert not card.key_conviction.turned and not card.key_confirmation.turned
    assert not _sids_on(card)
    assert not any(_TICKER[sid] in line for sid, _ in _ROSTER for line in card.missing)


def test_a_meta_index_card_is_byte_identical_to_the_card_with_an_empty_roster():
    flagged = _card(_ROSTER, meta_index=True)
    empty = _card([], meta_index=False)
    assert flagged.model_dump_json() == empty.model_dump_json()


def test_turning_the_switch_off_again_restores_the_normal_call():
    """Reversible: off after on is the same card as never on — nothing about the switch is remembered."""
    assert (
        _card(_ROSTER, meta_index=False).model_dump_json()
        == _thesis_off_after_on_card().model_dump_json()
    )


def _thesis_off_after_on_card() -> CallCard:
    on = _thesis(_ROSTER, meta_index=True)
    off = on.model_copy(update={"meta_index": False})
    return assemble_from_pit(_Pit(_END, _BARS, _CATALYSTS), off, _END, DEFAULT_CONFIG)
