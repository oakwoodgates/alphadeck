"""The recorded-night shapes and the trigger identity that more than one dashboard reads (``docs/DASHBOARDS.md``
rule 7): ``Night`` / ``CardNights`` / ``Scope`` (the calls log read as nights, built by the Trigger Log's router),
and ``copies`` (a card's triggers by identity, every copy of one, so a trigger shown at card level and member
level counts once). Built with the Trigger Log, shared by the Daily Digest and the Insider Tracker. Display only.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Literal
from uuid import UUID

from domain.call import CallCard, TriggerRef
from domain.enums import Kind

Role = Literal["entry_trigger", "risk_signal"]

# the identity of one trigger on a card (the role is part of it, so an entry trigger and a risk signal that
# somehow shared the other fields would never merge and hide one). The label is not: see the module docstring.
Identity = tuple[Role, UUID, Kind, date | None]


@dataclass(frozen=True)
class Night:
    """One recorded night of one card: its winning row (the highest ``seq``). ``card`` is ``None`` when the
    stored card no longer validates against the call model (counted, never read)."""

    asof: date
    seq: int
    reconstructed: bool
    card: CallCard | None


@dataclass(frozen=True)
class CardNights:
    """One counted card's recorded nights: ``before`` = the row in effect before the window opens (``None`` =
    the card has no earlier row), ``nights`` = the window's nights, oldest first."""

    thesis_id: UUID
    thesis_name: str
    before: Night | None
    nights: tuple[Night, ...]


@dataclass(frozen=True)
class Scope:
    """How many cards the as-of's counted set left out, for the quiet line (``baskets.CardsAsOf``)."""

    live_cards: int  # unarchived cards today
    not_yet_created: int  # created after the as-of: left out
    roster_fallback: int  # counted cards with no roster snapshot by then: today's roster stands in


def identity_of(role: Role, ref: TriggerRef) -> Identity:
    return (role, ref.security_id, ref.kind, ref.event_date)


def _refs(card: CallCard) -> Iterable[tuple[Role, TriggerRef]]:
    """Every trigger a card shows, in the order the first copy of an identity is taken: the card's fired
    triggers, then each armed member's and each watch member's own, then the risk signals."""
    for t in card.triggers_fired:
        yield "entry_trigger", t
    for m in (*card.armed_members, *card.watch_members):
        for t in m.triggers:
            yield "entry_trigger", t
    for t in card.risk_signals:
        yield "risk_signal", t


def copies(card: CallCard) -> dict[Identity, list[TriggerRef]]:
    """The card's triggers by identity, every copy of one (a member-level copy of a card-level trigger, or the
    same fire date under another label) in the order ``_refs`` lists them: the first stands for the identity.
    """
    out: dict[Identity, list[TriggerRef]] = {}
    for role, ref in _refs(card):
        out.setdefault(identity_of(role, ref), []).append(ref)
    return out
