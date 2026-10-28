"""A context role never reaches the call — through the REAL call seam (``pipeline.core.assemble_from_pit``), no DB.

The role says what part a company plays on a card; it is display metadata. These assemble the SAME thesis with
roles set on its members (a voting row and a Context row among them) and with none, over the in-memory
point-in-time view and the fixtures of ``test_silent_links_seam.py``, and pin that the two ``CallCard``s are
byte-identical — and that the comparison is not vacuous (the card arms, so there is a real call to compare).
"""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest

from domain.call import CallCard
from domain.config import DEFAULT_CONFIG
from domain.enums import ContextRole, State
from domain.thesis import BasketMember, Segment, Thesis
from pipeline.core import assemble_from_pit
from tests.pipeline.test_silent_links_seam import _BARS, _CATALYSTS, _END, _TICKER, L, V, X, Y, _Pit


def _thesis(roles: dict[UUID, ContextRole | None], *members: tuple[UUID, str | None]) -> Thesis:
    return Thesis(
        id=uuid.UUID(int=0x5152),
        name="Context roles seam",
        narrative="A context role is display metadata: it never changes the call.",
        segments=[Segment(label="Chain"), Segment(label="Discovered")],
        basket=[
            BasketMember(
                ticker=_TICKER[sid],
                role="r",
                security_id=sid,
                segment=seg,
                context_role=roles.get(sid),
            )
            for sid, seg in members
        ],
    )


def _card(roles: dict[UUID, ContextRole | None], *members: tuple[UUID, str | None]) -> CallCard:
    return assemble_from_pit(
        _Pit(_END, _BARS, _CATALYSTS), _thesis(roles, *members), _END, DEFAULT_CONFIG
    )


# X arms (breakout + CORE conviction), Y is quiet, L has the same breakout but sits in Context (silent), V is a
# lagging name that a real leader would give a sympathy confirmation.
_ROSTER = ((X, "Chain"), (Y, "Chain"), (L, "Context"), (V, "Chain"))


def test_the_comparison_is_not_vacuous_the_roster_arms_a_real_call():
    card = _card({}, *_ROSTER)
    assert card.state is State.ARMED and card.armed_security_id == X
    assert card.triggers_fired  # something fired, so equality below compares a real card


def test_roles_on_members_leave_the_card_byte_identical_to_the_card_without_them():
    plain = _card({}, *_ROSTER)
    tagged = _card(
        {
            X: ContextRole.PARTICIPANT,  # the voting row that arms the card
            Y: ContextRole.PAYER,  # a quiet voting row
            L: ContextRole.COUNTER_CASE,  # the Context row
            V: ContextRole.DIRECT_READ,
        },
        *_ROSTER,
    )
    assert tagged.model_dump_json() == plain.model_dump_json()
    assert tagged.model_dump() == plain.model_dump()
    assert tagged.state is State.ARMED  # still armed on X — the role did not move it


@pytest.mark.parametrize("role", list(ContextRole))
def test_every_one_of_the_six_roles_on_the_arming_name_changes_nothing(role):
    plain = _card({}, *_ROSTER)
    assert _card({X: role}, *_ROSTER).model_dump_json() == plain.model_dump_json()


@pytest.mark.parametrize("silent", ["Context", "Bench"])
def test_a_role_on_a_silent_row_does_not_make_it_vote(silent):
    """A role is allowed on a Context / Bench row, and that row still never counts toward the call."""
    with_role = _card({L: ContextRole.PAYER}, (L, silent), (Y, "Chain"))
    without = _card({}, (Y, "Chain"))
    assert with_role.model_dump_json() == without.model_dump_json()
    assert with_role.state is not State.ARMED


def test_a_role_on_a_discovered_row_leaves_that_names_vote_untouched():
    """Discovered counts toward the call; a role on it neither adds to nor removes from that."""
    plain = _card({}, (X, "Discovered"), (Y, "Chain"))
    tagged = _card({X: ContextRole.FINANCIER}, (X, "Discovered"), (Y, "Chain"))
    assert tagged.model_dump_json() == plain.model_dump_json()
    assert plain.state is State.ARMED and plain.armed_security_id == X


def test_the_card_carries_no_context_role_anywhere():
    """No CallCard field records it (a card born with a role is byte-identical to one born without)."""
    card = _card({X: ContextRole.PAYER, L: ContextRole.BENCHMARK}, *_ROSTER)
    assert "context_role" not in card.model_dump_json()
    assert "context_role" not in CallCard.model_fields


def test_the_seam_leaves_the_callers_roles_untouched():
    thesis = _thesis({X: ContextRole.PAYER, L: ContextRole.BENCHMARK}, *_ROSTER)
    before = thesis.model_dump()
    assemble_from_pit(_Pit(_END, _BARS, _CATALYSTS), thesis, _END, DEFAULT_CONFIG)
    assert thesis.model_dump() == before
    assert [m.context_role for m in thesis.basket] == [
        ContextRole.PAYER,
        None,
        ContextRole.BENCHMARK,
        None,
    ]
