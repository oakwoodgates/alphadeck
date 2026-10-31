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


def _armed(asof, *, exit_by, arm_until):
    return _snap(
        asof, State.ARMED, armed=True, conv=Grade.CORE, exit_by=exit_by, arm_until=arm_until
    )


def test_close_reason_reads_the_clocks_on_the_next_session_rule():
    """F3: the arm lapses on the first night whose NEXT session is past its clock (the call path's own test), so
    the de-arm night can be ``arm_until``'s own night, or the Friday before a weekend end — still the entry
    window lapsing, never "dearmed_other". The same for the hold clock."""
    # arm_until Thu 2025-05-15: armed through Wed 05-14, de-armed on 05-15 itself
    eps = derive_episodes(
        [
            _armed(date(2025, 5, 14), exit_by=date(2025, 11, 3), arm_until=date(2025, 5, 15)),
            _snap(date(2025, 5, 15), State.WARMING, conv=Grade.CORE),
        ]
    )
    assert eps[0].close_reason == "arm_until_lapsed" and eps[0].dearm_date == date(2025, 5, 15)
    # arm_until Sat 2025-05-17: de-armed on Fri 05-16, whose next session (Mon 05-19) is past it
    eps = derive_episodes(
        [
            _armed(date(2025, 5, 15), exit_by=date(2025, 11, 3), arm_until=date(2025, 5, 17)),
            _snap(date(2025, 5, 16), State.WARMING, conv=Grade.CORE),
        ]
    )
    assert eps[0].close_reason == "arm_until_lapsed"
    # a de-arm whose next session is still inside both clocks is another reason
    eps = derive_episodes(
        [
            _armed(date(2025, 5, 14), exit_by=date(2025, 11, 3), arm_until=date(2025, 5, 16)),
            _snap(date(2025, 5, 15), State.WARMING, conv=Grade.CORE),
        ]
    )
    assert eps[0].close_reason == "dearmed_other"
    # exit_by Mon 2025-11-03: aged out on 11-03 itself
    eps = derive_episodes(
        [
            _armed(date(2025, 10, 31), exit_by=date(2025, 11, 3), arm_until=date(2025, 11, 10)),
            _snap(date(2025, 11, 3), State.INCUBATING),
        ]
    )
    assert eps[0].close_reason == "conviction_aged_out"


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
