"""§2.5 (core) + §3.3 (flip): the grade-aware structural DE-ARM at the assembler (R10/R11/R12).

The crux of the highest-risk chunk — the call assembler's risk-veto becomes GRADE-AWARE for a
``breakdown`` risk: a flip-breakdown de-arms only a flip entry, a core-breakdown only a core entry
(keyed on ``SignalEvent.dearm_grade``, a property — never a per-kind branch). Dilution (no dearm_grade)
stays grade-blind. These are hand-built assembler goldens; the detector math is proven separately
(``tests/signals/test_breakdown.py``) and the de-arm-in-replay in ``tests/replay/test_breakdown_dearm_replay.py``.

The de-arm is behind the v0 MASTER SWITCH ``breakdown_dearm_enabled`` (default OFF, so the flagship demo is
byte-for-byte unchanged). These tests run the ENABLED behavior (``_CFG`` = flag ON) — the goldens elsewhere
use the default (flag OFF).
"""

from __future__ import annotations

import uuid
from datetime import timedelta

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, State, Verdict
from domain.thesis import Position
from tests.calls.factories import (
    ASOF,
    SID,
    breakdown_event,
    breakout_event,
    dilution_event,
    insider_event,
    make_thesis,
)

_SID2 = uuid.UUID(int=0x3333)
# the de-arm capability turned ON (the flag is OFF by default — see the module docstring)
_CFG = DEFAULT_CONFIG.model_copy(update={"breakdown_dearm_enabled": True})


def test_core_hold_dearms_on_a_core_breakdown():
    """R10/R12: a CORE hold (core conviction + core confirmation -> core entry) DE-ARMS when a
    core-breakdown (a close below the 200d base) fires — even though its arm_until window hasn't lapsed.
    """
    thesis = make_thesis()
    armed = assemble_call(thesis, [insider_event(), breakout_event(grade=Grade.CORE)], ASOF, _CFG)
    assert armed.state is State.ARMED and armed.entry_grade is Grade.CORE

    dearmed = assemble_call(
        thesis,
        [
            insider_event(),
            breakout_event(grade=Grade.CORE),
            breakdown_event(dearm_grade=Grade.CORE),
        ],
        ASOF,
        _CFG,
    )
    assert (
        dearmed.state is State.WARMING
    )  # de-armed on the structural break (conviction still live)
    assert dearmed.armed_security_id is None and dearmed.armed_members == []
    assert dearmed.confidence is None  # a de-armed (not-armed) call carries no confidence bar
    assert (
        dearmed.key_conviction.turned and dearmed.key_confirmation.turned
    )  # both keys still turned
    assert dearmed.thesis_id == thesis.id  # the THESIS is never vetoed (§2)
    assert any(r.kind is Kind.BREAKDOWN for r in dearmed.risk_signals)  # honest risk evidence


def test_a_breakdown_concurrent_with_or_before_the_arm_does_not_dearm():
    """The POST-DATE rule (the fix): a breakdown NOT strictly after the arm — concurrent with the arming
    bar, or older — de-arms nothing. A structural exit is a give-back AFTER an entry, never a break already
    true when it armed (the UNH Aug-2025 bounce-inside-a-downtrend case that arms while below its 200d).
    """
    thesis = make_thesis()
    base = [insider_event(), breakout_event(grade=Grade.CORE)]  # both keys fire on ASOF
    concurrent = breakdown_event(dearm_grade=Grade.CORE, asof=ASOF)  # break dated AT the arm
    assert assemble_call(thesis, [*base, concurrent], ASOF, _CFG).state is State.ARMED
    older = breakdown_event(
        dearm_grade=Grade.CORE, asof=ASOF - timedelta(days=3)
    )  # break BEFORE arm
    assert assemble_call(thesis, [*base, older], ASOF, _CFG).state is State.ARMED


def test_dearm_expression_reads_as_signal_validity_never_a_sell():
    """#4/#5: the de-arm must read as a signal-validity / de-arm event (advisory), NEVER a sell
    instruction, and must not deepen the size/instrument prose."""
    card = assemble_call(
        make_thesis(),
        [
            insider_event(),
            breakout_event(grade=Grade.CORE),
            breakdown_event(dearm_grade=Grade.CORE),
        ],
        ASOF,
        _CFG,
    )
    expr = card.expression.lower()
    assert "de-arm" in expr  # it names itself a de-arm
    assert "not a sell" in expr  # and explicitly disclaims a sell instruction
    # no BARE sell/trim/exit order left over once the disclaimer is removed (#5)
    residue = expr.replace("not a sell", "")
    assert "sell" not in residue and "trim" not in residue
    assert "options" not in expr and "size" not in expr  # doesn't deepen the size/instrument prose


def test_flip_entry_dearms_on_a_flip_breakdown():
    """R11/R12: a FLIP entry (core conviction + a momentum-only breakout -> flip entry, the HIMS shape)
    de-arms FAST on a flip-breakdown (a close back below the 8-day base)."""
    thesis = make_thesis()
    events = [insider_event(grade=Grade.CORE), breakout_event(grade=Grade.FLIP, score=0.45)]
    armed = assemble_call(thesis, events, ASOF, _CFG)
    assert armed.state is State.ARMED and armed.entry_grade is Grade.FLIP

    dearmed = assemble_call(thesis, [*events, breakdown_event(dearm_grade=Grade.FLIP)], ASOF, _CFG)
    assert dearmed.state is State.WARMING and dearmed.armed_security_id is None


def test_flip_breakdown_does_not_dearm_a_core_hold():
    """THE grade-aware proof (R12): a flip-style FAST breakdown can NEVER shake a CORE hold — the grade
    mismatch means the veto ignores it, though it still surfaces as honest risk context."""
    card = assemble_call(
        make_thesis(),
        [
            insider_event(),
            breakout_event(grade=Grade.CORE),
            breakdown_event(dearm_grade=Grade.FLIP),
        ],
        ASOF,
        _CFG,
    )
    assert card.state is State.ARMED and card.entry_grade is Grade.CORE
    assert card.armed_security_id == SID
    assert any(r.kind is Kind.BREAKDOWN for r in card.risk_signals)  # present, but not a de-arm
    assert "de-arm" not in card.expression.lower()  # a core-entry expression, not the de-arm line


def test_core_breakdown_does_not_dearm_a_flip_entry():
    """The mirror (R12): a core STRUCTURAL breakdown does not de-arm a FLIP entry — the flip's own fast
    breakdown (the 8-day base) is its exit, not the 200d base."""
    card = assemble_call(
        make_thesis(),
        [
            insider_event(grade=Grade.CORE),
            breakout_event(grade=Grade.FLIP, score=0.45),
            breakdown_event(dearm_grade=Grade.CORE),
        ],
        ASOF,
        _CFG,
    )
    assert card.state is State.ARMED and card.entry_grade is Grade.FLIP
    assert card.armed_security_id == SID


def test_below_threshold_breakdown_dearms_nothing():
    """The veto gate is config-driven (like dilution's): a breakdown scored below risk_block_severity
    de-arms nothing, and raising the severity above the breakdown score tunes the de-arm OFF."""
    thesis = make_thesis()
    weak = assemble_call(
        thesis,
        [
            insider_event(),
            breakout_event(grade=Grade.CORE),
            breakdown_event(dearm_grade=Grade.CORE, score=0.3),
        ],
        ASOF,
        _CFG,
    )
    assert weak.state is State.ARMED  # 0.3 < risk_block_severity -> non-blocking

    strict = _CFG.model_copy(update={"risk_block_severity": 0.95})  # gate above the 0.8 break score
    tuned_off = assemble_call(
        thesis,
        [
            insider_event(),
            breakout_event(grade=Grade.CORE),
            breakdown_event(dearm_grade=Grade.CORE),
        ],
        ASOF,
        strict,
    )
    assert tuned_off.state is State.ARMED


def test_multi_member_flip_breakdown_dearms_only_the_flip_member():
    """Thesis-level, per-member (R12): a flip-breakdown on a FLIP member de-arms only IT; a co-present
    CORE hold on another name stays armed and headlines (the grade-aware veto is per-member)."""
    events = [
        insider_event(grade=Grade.CORE, security_id=SID),  # SID: a CORE hold
        breakout_event(grade=Grade.CORE, security_id=SID),
        insider_event(
            grade=Grade.CORE, security_id=_SID2
        ),  # SID2: a FLIP entry (momentum breakout)
        breakout_event(grade=Grade.FLIP, score=0.45, security_id=_SID2),
        breakdown_event(dearm_grade=Grade.FLIP, security_id=_SID2),  # de-arms SID2 only
    ]
    card = assemble_call(make_thesis(), events, ASOF, _CFG)
    assert card.state is State.ARMED
    assert card.armed_security_id == SID  # the core hold survives + headlines
    assert [m.security_id for m in card.armed_members] == [SID]  # SID2 de-armed, gone from the menu
    assert all(m.security_id != _SID2 for m in card.watch_members)  # has conviction -> not a watch


def test_breakdown_cannot_unhold_a_managing_position():
    """§4: the risk veto gates ENTRY timing — it cannot un-hold an open position. A core-breakdown on a
    HELD core name leaves the verdict MANAGING; the breakdown still rides risk_signals for the counter-case.
    """
    position = Position(entry_price=10.0, opened_on=ASOF, security_id=SID)
    card = assemble_call(
        make_thesis(position=position),
        [
            insider_event(),
            breakout_event(grade=Grade.CORE),
            breakdown_event(dearm_grade=Grade.CORE),
        ],
        ASOF,
        _CFG,
    )
    assert card.state is State.MANAGING
    assert [m.security_id for m in card.armed_members] == [SID]
    assert card.armed_members[0].verdict is Verdict.MANAGING
    assert any(r.kind is Kind.BREAKDOWN for r in card.risk_signals)


def test_dilution_stays_grade_blind_after_the_change():
    """Regression: dilution carries no dearm_grade, so it stays GRADE-BLIND — it withholds a core hold
    AND a flip entry alike (unchanged), and reads as a risk/timing block, not the de-arm line."""
    thesis = make_thesis()
    core = assemble_call(
        thesis,
        [insider_event(grade=Grade.CORE), breakout_event(grade=Grade.CORE), dilution_event()],
        ASOF,
        _CFG,
    )
    flip = assemble_call(
        thesis,
        [
            insider_event(grade=Grade.CORE),
            breakout_event(grade=Grade.FLIP, score=0.45),
            dilution_event(),
        ],
        ASOF,
        _CFG,
    )
    assert core.state is State.WARMING and flip.state is State.WARMING  # withheld at BOTH grades
    assert "de-arm" not in core.expression.lower()  # a risk/timing block, not a structural de-arm
    assert "risk" in core.expression.lower() or "withheld" in core.expression.lower()


def test_dearm_is_deterministic():
    """Same (thesis, events, asof, cfg) -> byte-identical CallCard, including the de-arm path."""
    thesis = make_thesis()
    events = [
        insider_event(),
        breakout_event(grade=Grade.CORE),
        breakdown_event(dearm_grade=Grade.CORE),
    ]
    a = assemble_call(thesis, events, ASOF, _CFG)
    b = assemble_call(thesis, events, ASOF, _CFG)
    assert a.model_dump_json() == b.model_dump_json()
