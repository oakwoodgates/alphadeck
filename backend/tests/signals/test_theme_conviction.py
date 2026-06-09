from __future__ import annotations

import uuid
from datetime import date

from domain.config import DEFAULT_CONFIG
from domain.enums import Archetype, Grade, Kind
from domain.thesis import BasketMember, Thesis
from signals import theme_conviction
from tests.calls.factories import breakout_event, catalyst_event

_SID_A = uuid.UUID(int=0xA1)
_SID_B = uuid.UUID(int=0xB2)
_TID = uuid.UUID(int=0x7777)
_ASOF = date(2026, 6, 5)


def _fact(
    grade="flip",
    valid_from=date(2026, 1, 15),
    horizon_end=date(2027, 1, 15),
    ref="theme:nuclear",
):
    return {
        "grade": grade,
        "label": "small-scale-nuclear theme conviction",
        "source": "ratified",
        "source_ref": ref,
        "valid_from": valid_from,
        "horizon_end": horizon_end,
    }


def _thesis(*security_ids):
    return Thesis(
        id=_TID,
        name="Small-scale nuclear",
        narrative="a theme",
        ticker=None,
        basket=[
            BasketMember(
                ticker=f"T{i}", role="member", archetype=Archetype.HIGH_BETA, security_id=s
            )
            for i, s in enumerate(security_ids)
        ],
    )


def test_strongest_live_fact_picks_recent_and_drops_expired():
    recent = _fact(valid_from=date(2026, 2, 1), ref="r")
    older = _fact(valid_from=date(2026, 1, 1), ref="o")
    picked = theme_conviction.strongest_live_fact([older, recent], _ASOF, DEFAULT_CONFIG)
    assert picked["source_ref"] == "r"  # the operator's latest live ratification
    # an expired theme conviction (its horizon ended before asof) is dropped (rule 3 — no zombies)
    expired = _fact(valid_from=date(2024, 1, 1), horizon_end=date(2025, 1, 1))
    assert theme_conviction.strongest_live_fact([expired], _ASOF, DEFAULT_CONFIG) is None
    assert theme_conviction.strongest_live_fact([], _ASOF, DEFAULT_CONFIG) is None


def test_broadcast_with_no_fact_emits_nothing():
    thesis = _thesis(_SID_A)
    events = [breakout_event(grade=Grade.CORE, security_id=_SID_A)]
    assert theme_conviction.broadcast(thesis, events, None, _ASOF, DEFAULT_CONFIG) == []


def test_volume_backed_member_with_no_own_conviction_is_theme_armed():
    """Rule 4 + the floor (rule 7): a member with a LIVE volume-backed (CORE) breakout and NO own
    conviction in the stream gets a flip theme event (whether it never had own conviction, or its own
    lapsed — both are simply absent here, which is exactly the floor)."""
    thesis = _thesis(_SID_A)
    out = theme_conviction.broadcast(
        thesis,
        [breakout_event(grade=Grade.CORE, security_id=_SID_A)],
        _fact(),
        _ASOF,
        DEFAULT_CONFIG,
    )
    assert len(out) == 1
    ev = out[0]
    assert ev.security_id == _SID_A and ev.kind is Kind.THEME_CONVICTION
    assert (
        ev.grade is Grade.FLIP
    )  # rule 2: capped at starter — a theme conviction never mints a core
    assert (
        ev.alpha_liveness_days == (date(2027, 1, 15) - date(2026, 1, 15)).days
    )  # the theme horizon
    assert ev.provenance[0].source == "ratified" and ev.provenance[0].ref == "theme:nuclear"
    assert ev.asof == date(2026, 1, 15)  # dated at the conviction's event date, not the query asof


def test_momentum_only_breakout_is_not_enough():
    """Rule 4: a momentum-only (flip) breakout is NOT volume-backed -> the theme cannot arm the member."""
    thesis = _thesis(_SID_A)
    out = theme_conviction.broadcast(
        thesis,
        [breakout_event(grade=Grade.FLIP, security_id=_SID_A)],
        _fact(),
        _ASOF,
        DEFAULT_CONFIG,
    )
    assert out == []


def test_no_confirmation_no_theme_arm():
    """A member with no confirmation at all is not armed by the theme (both keys must be live)."""
    assert theme_conviction.broadcast(_thesis(_SID_A), [], _fact(), _ASOF, DEFAULT_CONFIG) == []


def test_own_conviction_wins_and_the_other_member_falls_back():
    """Rule 5: a member with its OWN live conviction is NOT theme-broadcast (own wins). In the same
    thesis, a member with only a core breakout (no own) IS theme-armed (rule 7, the floor) — one call.
    """
    thesis = _thesis(_SID_A, _SID_B)
    events = [
        catalyst_event(grade=Grade.FLIP, security_id=_SID_A),  # A carries its OWN conviction
        breakout_event(grade=Grade.CORE, security_id=_SID_A),
        breakout_event(
            grade=Grade.CORE, security_id=_SID_B
        ),  # B: a core breakout, no own conviction
    ]
    out = theme_conviction.broadcast(thesis, events, _fact(), _ASOF, DEFAULT_CONFIG)
    assert [e.security_id for e in out] == [
        _SID_B
    ]  # only B falls back to the theme; A keeps its own


def test_unresolved_member_is_skipped():
    """An unresolved basket member (security_id None) is never broadcast onto — no None-keyed event."""
    assert theme_conviction.broadcast(_thesis(None), [], _fact(), _ASOF, DEFAULT_CONFIG) == []


def test_broadcast_only_reaches_basket_members():
    """Rule 4: the theme can't reach a name outside the curated basket — a core breakout on a non-member
    security is never theme-armed (broadcast iterates the basket only)."""
    outsider = uuid.UUID(int=0xDEAD)
    out = theme_conviction.broadcast(
        _thesis(_SID_A),
        [breakout_event(grade=Grade.CORE, security_id=outsider)],
        _fact(),
        _ASOF,
        DEFAULT_CONFIG,
    )
    assert out == []
