from __future__ import annotations

from domain.enums import Grade
from domain.signal import SignalEvent

_GRADE_RANK: dict[Grade, int] = {Grade.FLIP: 1, Grade.CORE: 2}


def grade_rank(grade: Grade | None) -> int:
    if grade is None:
        return 0
    return _GRADE_RANK[grade]


def call_grade(fired_entry_triggers: list[SignalEvent]) -> Grade | None:
    """The highest-grade fired entry trigger among a set (CALL_LOGIC §3)."""
    graded = [e.grade for e in fired_entry_triggers if e.grade is not None]
    if not graded:
        return None
    return max(graded, key=grade_rank)


def weaker_grade(a: Grade | None, b: Grade | None) -> Grade | None:
    """The weaker (lower) of two grades — the entry grade is the weaker of the two keys (§4)."""
    if a is None:
        return b
    if b is None:
        return a
    return a if grade_rank(a) <= grade_rank(b) else b
