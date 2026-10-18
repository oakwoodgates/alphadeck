"""The Q13 push-policy RULE (``notify.policy.classify``) — the contract, stated with direct inputs.

Real recorded instances of each class live in ``tests/pipeline/test_push_policy.py``; this file pins the
rule itself, row by row, so a change to it is a visible diff here.
"""

from __future__ import annotations

import pytest

from notify.policy import LOUD_KINDS, QUIET_KINDS, classify


@pytest.mark.parametrize(
    ("from_state", "to_state", "expected"),
    [
        # entering Armed from anywhere stays LOUD (unchanged by Q13)
        ("warming", "armed", "armed"),
        ("incubating", "armed", "armed"),
        ("managing", "armed", "armed"),
        # out of or short of Armed: recorded, never pushed (unchanged — a de-arm has never pushed, and
        # Incubating never nags)
        ("armed", "warming", "record_only"),
        ("armed", "incubating", "record_only"),
        ("armed", "managing", "record_only"),
        ("incubating", "warming", "record_only"),
        ("warming", "incubating", "record_only"),
    ],
)
def test_moves_into_and_out_of_armed_are_classified_as_before(from_state, to_state, expected):
    assert classify(from_state=from_state, to_state=to_state) == expected


@pytest.mark.parametrize(
    ("from_grade", "to_grade", "from_armed", "to_armed", "expected"),
    [
        ("flip", "core", 3, 3, "upgrade"),  # grade up
        ("flip", "flip", 1, 3, "upgrade"),  # names up
        ("flip", "core", 2, 4, "upgrade"),  # both up
        ("core", "flip", 4, 4, "downgrade"),  # grade down
        ("core", "core", 5, 3, "downgrade"),  # names down
        ("core", "flip", 4, 2, "downgrade"),  # both down
        ("flip", "core", 2, 1, "mixed"),  # grade up, names down — QUIET, both stated
        ("core", "flip", 2, 5, "mixed"),  # grade down, names up — QUIET, both stated
        ("flip", "flip", 3, 3, "changed"),  # a verdict move at the same grade and count
        (None, "flip", 0, 1, "upgrade"),  # an absent grade ranks lowest
    ],
)
def test_armed_to_armed_is_classified_on_grade_and_armed_name_count(
    from_grade, to_grade, from_armed, to_armed, expected
):
    got = classify(
        from_state="armed",
        to_state="armed",
        from_grade=from_grade,
        to_grade=to_grade,
        from_armed=from_armed,
        to_armed=to_armed,
    )
    assert got == expected


def test_an_unknown_count_never_moves_and_only_upgrades_and_new_arms_are_loud():
    assert classify(from_state="armed", to_state="armed", from_grade="flip", to_grade="flip") == (
        "changed"
    )
    assert LOUD_KINDS == {"armed", "upgrade"}
    assert QUIET_KINDS == {"downgrade", "mixed", "changed"}
    assert not LOUD_KINDS & QUIET_KINDS
