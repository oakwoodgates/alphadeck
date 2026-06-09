from __future__ import annotations

import uuid
from datetime import date

from domain.enums import Grade, State, Verdict
from replay.episodes import derive_episodes
from replay.schema import CallSnapshot, MemberRow

_TID = uuid.UUID(int=0x1)
_SID = uuid.UUID(int=0x2)


def _snap(asof, state, *, armed=False, conv=None, exit_by=None, arm_until=None):
    members = []
    verdict = Verdict.WATCHING
    if armed:
        verdict = Verdict.CORE_ENTRY
        members = [
            MemberRow(
                security_id=_SID,
                tier="armed",
                verdict=verdict,
                conviction_grade=conv,
                entry_grade=Grade.CORE,
                confidence=0.7,
                exit_by=exit_by,
                arm_until=arm_until,
            )
        ]
    return CallSnapshot(
        thesis_id=_TID,
        asof=asof,
        state=state,
        verdict=(
            verdict if armed else (Verdict.NOT_YET if state is State.WARMING else Verdict.WATCHING)
        ),
        conviction_grade=conv,
        armed_security_id=_SID if armed else None,
        exit_by=exit_by,
        arm_until=arm_until,
        members=members,
    )


def test_rearm_yields_two_episodes_with_close_reasons():
    """Warm -> Arm -> (confirmation lapses) Warm -> Arm again -> (aged out) Incubating = TWO episodes; the
    re-arm is a fresh decision, and the close reasons read from the de-arm snapshot + the clocks."""
    snaps = [
        _snap(date(2025, 5, 1), State.WARMING, conv=Grade.CORE),
        _snap(date(2025, 5, 2), State.WARMING, conv=Grade.CORE),
        _snap(
            date(2025, 5, 5),
            State.ARMED,
            armed=True,
            conv=Grade.CORE,
            exit_by=date(2025, 11, 1),
            arm_until=date(2025, 5, 15),
        ),
        _snap(
            date(2025, 5, 6),
            State.ARMED,
            armed=True,
            conv=Grade.CORE,
            exit_by=date(2025, 11, 1),
            arm_until=date(2025, 5, 15),
        ),
        _snap(
            date(2025, 5, 20), State.WARMING, conv=Grade.CORE
        ),  # confirmation lapsed -> arm_until_lapsed
        _snap(
            date(2025, 6, 1),
            State.ARMED,
            armed=True,
            conv=Grade.CORE,
            exit_by=date(2025, 12, 1),
            arm_until=date(2025, 6, 11),
        ),
        _snap(date(2025, 12, 5), State.INCUBATING),  # everything aged out -> conviction_aged_out
    ]
    eps = derive_episodes(snaps)
    assert len(eps) == 2
    first, second = eps
    assert first.arm_date == date(2025, 5, 5) and first.last_armed_date == date(2025, 5, 6)
    assert first.close_reason == "arm_until_lapsed" and first.dearm_date == date(2025, 5, 20)
    assert first.warm_date == date(2025, 5, 1)  # the contiguous warming run before the arm
    assert first.is_headline and first.verdict is Verdict.CORE_ENTRY
    assert second.arm_date == date(2025, 6, 1) and second.close_reason == "conviction_aged_out"


def test_window_end_close_reason():
    """A run that reaches the timeline end closes as window_end with no de-arm date."""
    snaps = [
        _snap(
            date(2026, 1, 1),
            State.ARMED,
            armed=True,
            conv=Grade.CORE,
            exit_by=date(2026, 6, 1),
            arm_until=date(2026, 1, 11),
        ),
        _snap(
            date(2026, 1, 2),
            State.ARMED,
            armed=True,
            conv=Grade.CORE,
            exit_by=date(2026, 6, 1),
            arm_until=date(2026, 1, 11),
        ),
    ]
    eps = derive_episodes(snaps)
    assert len(eps) == 1
    assert eps[0].close_reason == "window_end" and eps[0].dearm_date is None


def test_never_armed_thesis_yields_no_episodes():
    """Warming-forever (a sector breakout with no conviction) is a non-event, not a false arm: 0 episodes."""
    snaps = [
        _snap(date(2026, 6, 1), State.WARMING, conv=None),
        _snap(date(2026, 6, 2), State.WARMING, conv=None),
        _snap(date(2026, 6, 3), State.INCUBATING),
    ]
    assert derive_episodes(snaps) == []
