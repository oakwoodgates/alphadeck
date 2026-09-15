from __future__ import annotations

import itertools
import re

from calls.assembler import _expression
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

# The de-armed branch (untouched by this change) deliberately says "...not a sell" to AFFIRM the
# de-arm is a signal-validity event, never a sell instruction (#4/#5) — see
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

    violations: list[str] = []
    for combo in itertools.product(
        states, grades, grades, bools, bools, bools, bools, bools, bools
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
