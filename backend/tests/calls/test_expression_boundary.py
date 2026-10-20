from __future__ import annotations

import itertools
import re

from calls.assembler import _expression
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, State

# Invariant #4/#5 (CLAUDE.md, docs/INVARIANTS.md): the expression is advisory research context —
# setup class + timing + signal-validity — and must NEVER read as a position-size,
# instrument-selection, or order instruction. This is a regression lock, not a snapshot: it
# exhaustively covers every input `_expression` can be called with, so a future edit can't
# quietly reintroduce prescriptive language on any one branch.
_FORBIDDEN_TOKENS = (
    # size
    "small",
    "max size",
    "starter",
    "core size",
    "full size",
    "add to",
    "trim",
    # instrument
    "options",
    "spot",
    "calls",
    "puts",
    "warrants",
    # order
    "trail the stop",
    "take the gain",
    "do not hold",
    "sell",
    "buy",
    "build into",
    "enter ",
)

# The de-armed branch (three grade-specific strings since Q12) deliberately says "...not a sell" to
# AFFIRM the de-arm is a signal-validity event, never a sell instruction (#4/#5) — see
# test_breakdown_dearm.py::test_dearm_expression_reads_as_signal_validity_never_a_sell, which
# strips this same negation before checking for a residual bare "sell". Strip it here too so that
# compliant disclaimer prose doesn't trip the "sell"/"buy" tokens meant to catch a real
# reintroduced instruction (e.g. "sell half the position", "buy more").
_ALLOWED_NEGATION = re.compile(r"not a (?:sell|buy)\b", re.IGNORECASE)


def _forbidden_hits(expression: str) -> list[str]:
    scrubbed = _ALLOWED_NEGATION.sub("", expression).lower()
    return [token for token in _FORBIDDEN_TOKENS if token in scrubbed]


def test_expression_never_prescribes_size_instrument_or_orders():
    """Exhaustively call `_expression` over every (state, conviction_grade, entry_grade,
    risk_blocked, dearmed, momentum_only, conviction_on, confirmation_on, conviction_holdable)
    combination and assert none of the returned strings smuggles in a position-size,
    instrument-selection, or order instruction. `_expression` is display-only prose attached to
    an already-computed call; it must never be read as trading advice on size or instrument.
    """
    states = list(State)
    grades: list[Grade | None] = [None, Grade.FLIP, Grade.CORE]
    bools = (False, True)
    # Q12: the de-arm copy is chosen by which breakdowns de-armed — every set, incl. the empty one
    dearm_sets = [
        frozenset(),
        frozenset({Grade.FLIP}),
        frozenset({Grade.CORE}),
        frozenset({Grade.FLIP, Grade.CORE}),
    ]

    violations: list[str] = []
    for combo in itertools.product(
        states, grades, grades, bools, bools, bools, bools, bools, bools, dearm_sets
    ):
        (
            state,
            conviction_grade,
            entry_grade,
            risk_blocked,
            dearmed,
            momentum_only,
            conviction_on,
            confirmation_on,
            conviction_holdable,
            dearm_grades,
        ) = combo
        text = _expression(
            state,
            conviction_grade,
            entry_grade,
            risk_blocked,
            dearmed,
            momentum_only,
            conviction_on,
            confirmation_on,
            conviction_holdable,
            dearm_grades=dearm_grades,
        )
        hits = _forbidden_hits(text)
        if hits:
            violations.append(f"{hits} in {text!r} for combo={combo}")

    assert not violations, "forbidden size/instrument/order language in _expression:\n" + "\n".join(
        violations
    )


def test_approved_expression_copy_is_the_expected_text():
    """Pins the six reworded branches to the operator-approved copy (verbatim), so a future edit
    is a deliberate, reviewable change to this test — not a silent drift. Pairs with the
    exhaustive boundary test above (which guards the invariant); this one guards the wording."""
    assert _expression(State.ARMED, Grade.FLIP, None, False, False, False, True, True, False) == (
        "FLIP setup — a fast, attention-driven trigger with a short-lived edge that decays at "
        "the catalyst; not a structural, holdable base. Validity is brief (to the exit-by)."
    )
    assert _expression(State.ARMED, Grade.FLIP, None, False, False, False, True, True, True) == (
        "Provisional conviction — real but not yet binding, and the market is confirming. An "
        "early read, not a settled one; the setup firms as the case hardens (a binding deal, "
        "more catalysts)."
    )
    assert _expression(
        State.ARMED, Grade.CORE, Grade.FLIP, False, False, True, True, True, True
    ) == (
        "Core (structural) thesis, but the breakout is momentum-only — volume hasn't confirmed. "
        "It completes into a confirmed core setup only when a real volume breakout lands."
    )
    assert _expression(
        State.ARMED, Grade.CORE, Grade.CORE, False, False, False, True, True, True
    ) == (
        "CORE setup — a structural, volume-confirmed trigger with a durable edge through the "
        "exit-by. The signal is strongest among the basket's leaders (see the per-name tape)."
    )
    assert _expression(
        State.MANAGING, Grade.CORE, Grade.CORE, False, False, False, True, True, True
    ) == (
        "Position open (you logged the entry) — Alpha Deck is monitoring the thesis through "
        "the exit-by validity horizon; the counter-case flags a break."
    )
    assert _expression(State.WARMING, Grade.FLIP, None, False, False, False, True, True, False) == (
        "FLIP setup only — a fast, short-lived trigger; the structural core setup isn't "
        "confirmed yet."
    )


def _dearmed(grades: frozenset[Grade]) -> str:
    return _expression(
        State.WARMING,
        Grade.CORE,
        Grade.CORE,
        True,
        True,
        False,
        True,
        True,
        True,
        dearm_grades=grades,
    )


def test_approved_dearm_copy_is_the_expected_text():
    """Q12 — the three operator-approved de-arm strings (2026-10-18), verbatim, keyed on WHICH
    breakdowns de-armed the arm. The real-record cases live in test_dearm_copy.py."""
    assert _dearmed(frozenset({Grade.FLIP})) == (
        "De-armed on a fast breakdown — the close fell back below the 8-day breakout base, so the "
        "flip entry signal is no longer valid (a signal-validity event, not a sell). It re-arms if "
        "price reclaims that base while the breakout is still live, or on a fresh breakout."
    )
    assert _dearmed(frozenset({Grade.CORE})) == (
        "De-armed on a structural break — the close fell below the 200-day base, so the core entry "
        "signal is no longer valid (a signal-validity event, not a sell). It re-arms if price "
        "reclaims the 200-day base while the entry triggers are live, or on a fresh confirmation."
    )
    mixed = (
        "De-armed on a breakdown — armed names gave back their bases (see the counter-case); a "
        "signal-validity event, not a sell."
    )
    assert _dearmed(frozenset({Grade.FLIP, Grade.CORE})) == mixed
    # an empty set never comes out of assemble_call; a direct caller gets the grade-neutral copy
    assert _dearmed(frozenset()) == mixed


def test_the_dearm_copy_numbers_match_the_detector_dials():
    """The approved prose writes "8-day" and "200-day"; the detectors read these dials. A retune that
    moves either must fail here and send the copy back to the operator, never drift silently."""
    assert DEFAULT_CONFIG.breakout_base_window == 8  # breakdown.flip_score's base
    assert DEFAULT_CONFIG.breakdown_core_sma_window == 200  # breakdown.core_score's SMA
