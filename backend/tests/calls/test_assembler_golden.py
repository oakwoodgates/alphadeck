from __future__ import annotations

import re
import uuid
from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG, CallConfig
from domain.enums import Grade, Kind, Role, State, Verdict
from domain.signal import SignalEvent
from tests.calls.factories import (
    ASOF,
    SID,
    breakout_event,
    dilution_event,
    insider_event,
    make_thesis,
)


def test_insider_only_warms_but_does_not_arm():
    """Conviction warms; without confirmation the second key stays off (two-key model)."""
    card = assemble_call(make_thesis(), [insider_event()], ASOF, DEFAULT_CONFIG)
    assert card.state is State.WARMING
    assert card.verdict is Verdict.NOT_YET
    assert card.conviction_grade is Grade.CORE
    assert card.key_conviction.turned is True
    assert card.key_confirmation.turned is False
    assert any("breakout" in m.lower() for m in card.missing)
    assert card.exit_by == date(2026, 6, 20)  # asof + 18d insider half-life


def test_insider_plus_breakout_arms_core_entry():
    """Both keys turned -> Armed / core_entry, with exit-by and a filtered catalyst surface."""
    card = assemble_call(make_thesis(), [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG)
    assert card.state is State.ARMED
    assert card.verdict is Verdict.CORE_ENTRY  # the breakout fixture is volume-backed (core)
    assert card.conviction_grade is Grade.CORE and card.entry_grade is Grade.CORE
    assert card.key_conviction.turned and card.key_confirmation.turned
    assert card.missing == []
    assert card.exit_by == date(2026, 6, 20)  # hold clock: insider fire 06-02 + 18d half-life
    assert card.arm_until == date(2026, 6, 12)  # entry window: breakout fire 06-02 + 10d half-life
    assert card.armed_security_id == SID  # the co-located security that armed
    assert len(card.catalyst_surface) == 1  # only the dated event inside the hold window
    assert card.catalyst_surface[0].when_date == date(2026, 6, 11)
    assert len(card.triggers_fired) == 2
    assert card.triggers_fired[0].sources[0].ref  # provenance link is present


def test_momentum_only_confirmation_arms_but_is_caveated():
    """Graded confirmation: a momentum-only (flip-grade) breakout still arms a core conviction, but
    honestly — reduced confidence, a volume-gap counter-case, and a cautious expression."""
    thesis = make_thesis()
    backed = assemble_call(
        thesis, [insider_event(), breakout_event(grade=Grade.CORE, score=0.8)], ASOF, DEFAULT_CONFIG
    )
    momentum = assemble_call(
        thesis,
        [insider_event(), breakout_event(grade=Grade.FLIP, score=0.45)],
        ASOF,
        DEFAULT_CONFIG,
    )
    # volume-backed -> full CORE entry; momentum-only -> STARTER entry (the weaker key drives the verdict)
    assert backed.verdict is Verdict.CORE_ENTRY
    assert backed.conviction_grade is Grade.CORE and backed.entry_grade is Grade.CORE
    assert momentum.state is State.ARMED and momentum.verdict is Verdict.STARTER_ENTRY
    assert momentum.conviction_grade is Grade.CORE  # the thesis stays core (hold-and-build)
    assert momentum.entry_grade is Grade.FLIP  # but the action is a starter, not a core entry
    assert momentum.confidence < backed.confidence  # the volume gap reads as lower confidence
    assert momentum.confidence <= DEFAULT_CONFIG.momentum_only_confidence_cap
    assert "momentum-only" in momentum.counter_case.lower()
    assert "starter" in momentum.expression.lower() and "volume" in momentum.expression.lower()


def test_severe_dilution_blocks_arming_on_timing_not_thesis():
    """Risk-veto: a severe risk signal withholds Armed on timing (-> Warming), never vetoes the thesis.
    A withheld call is not armed, so it shows no confidence bar (confidence is an Armed-state metric).
    """
    thesis = make_thesis()
    armed = assemble_call(thesis, [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG)
    blocked = assemble_call(
        thesis, [insider_event(), breakout_event(), dilution_event()], ASOF, DEFAULT_CONFIG
    )
    assert armed.state is State.ARMED and armed.confidence is not None
    assert blocked.state is State.WARMING  # Armed withheld
    assert blocked.key_conviction.turned and blocked.key_confirmation.turned  # keys still turned
    assert blocked.confidence is None  # a withheld (not-armed) call carries no confidence bar
    assert "risk" in blocked.counter_case.lower()  # risk signal feeds the counter-case
    # the expression explains the real blocker (timing/risk), not a missing confirmation
    assert "withheld" in blocked.expression.lower() or "risk" in blocked.expression.lower()
    assert blocked.thesis_id == thesis.id  # the thesis itself is never vetoed


# --- Pre-M3a fixes: date-aware sticky state, co-location guard, two fire-date-anchored clocks ---


def test_armed_is_sticky_across_the_entry_window():
    """A fixed dated firing stream stays Armed as the query asof advances through the entry window —
    the assembler trusts the dated firing and never re-evaluates a detector, so there is no flicker.
    """
    thesis = make_thesis()
    events = [insider_event(), breakout_event()]  # both fired on factories.ASOF (2026-06-02)
    for q in (date(2026, 6, 2), date(2026, 6, 5), date(2026, 6, 8), date(2026, 6, 12)):
        assert assemble_call(thesis, events, q, DEFAULT_CONFIG).state is State.ARMED, q


def test_clocks_are_anchored_to_fire_date_not_query_asof():
    """The two clocks anchor to each trigger's fire date, so they don't slide as the query asof moves."""
    thesis = make_thesis()
    events = [insider_event(), breakout_event()]  # fire date 06-02; half-lives 18 / 10
    for q in (date(2026, 6, 3), date(2026, 6, 5), date(2026, 6, 8)):
        card = assemble_call(thesis, events, q, DEFAULT_CONFIG)
        assert card.exit_by == date(2026, 6, 20), q  # conviction/hold clock: 06-02 + 18
        assert card.arm_until == date(2026, 6, 12), q  # confirmation/entry clock: 06-02 + 10


def test_risk_signals_surface_on_the_card_with_provenance():
    """A fired risk signal (e.g. dilution) surfaces on the card with its provenance — the counter-case's
    linkable evidence. This non-blocking one (score < block severity) rides alongside an Armed call.
    """
    card = assemble_call(
        make_thesis(),
        [insider_event(), breakout_event(), dilution_event(score=0.3)],
        ASOF,
        DEFAULT_CONFIG,
    )
    assert card.state is State.ARMED  # 0.3 < risk_block_severity -> non-blocking
    assert len(card.risk_signals) == 1
    rs = card.risk_signals[0]
    assert rs.kind is Kind.DILUTION_RISK and rs.grade is None and rs.security_id == SID
    assert rs.sources and rs.sources[0].ref
    # a non-blocking risk still penalizes the Armed card's confidence (the penalty lives on the path
    # where confidence exists — an armed call)
    no_risk = assemble_call(
        make_thesis(), [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG
    )
    assert no_risk.confidence is not None and card.confidence is not None
    assert card.confidence < no_risk.confidence


def test_warming_on_confirmation_without_conviction_is_honest():
    """A breakout with no conviction warms but can't arm — and the expression names the right missing
    key (conviction), not a breakout (the HIMS-shaped default would be wrong here, e.g. nuclear)."""
    card = assemble_call(make_thesis(), [breakout_event()], ASOF, DEFAULT_CONFIG)
    assert card.state is State.WARMING
    assert card.key_confirmation.turned and not card.key_conviction.turned
    assert card.armed_security_id is None
    assert "conviction" in card.expression.lower()  # not "hold for a volume-confirmed breakout"
    assert any("conviction" in m.lower() for m in card.missing)


def test_cross_name_does_not_arm_without_co_location():
    """Co-location guard: conviction on security A + a breakout on security B does NOT arm the thesis."""
    other = uuid.UUID(int=0x9999)
    breakout_elsewhere = breakout_event().model_copy(update={"security_id": other})
    card = assemble_call(make_thesis(), [insider_event(), breakout_elsewhere], ASOF, DEFAULT_CONFIG)
    assert card.state is State.WARMING  # conviction warms; the confirmation is on a different name
    assert card.armed_security_id is None


def test_confidence_is_scoped_to_the_armed_security():
    """P1.2: confidence is scored on the ARMED name's live triggers, not the whole basket — a live
    trigger on another name must not inflate the armed name's confidence."""
    thesis = make_thesis()
    base = assemble_call(thesis, [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG)
    assert base.state is State.ARMED and base.armed_security_id == SID

    # a live conviction trigger on a DIFFERENT name (it neither arms that name nor changes which name
    # is armed); confidence must be unchanged because it's scoped to the armed name's triggers.
    off_name = insider_event().model_copy(
        update={"security_id": uuid.UUID(int=0x9999), "score": 0.95}
    )
    widened = assemble_call(
        thesis, [insider_event(), breakout_event(), off_name], ASOF, DEFAULT_CONFIG
    )
    assert widened.state is State.ARMED and widened.armed_security_id == SID
    assert widened.confidence == base.confidence  # the off-name trigger was excluded


def test_confidence_is_none_unless_armed():
    """Confidence is an Armed-state metric (§7): None for Incubating/Warming, so a not-yet card never
    shows a confidence bar — and a multi-name basket's breakouts can't noisy-OR into a false 'high'.
    """
    thesis = make_thesis()
    assert assemble_call(thesis, [], ASOF, DEFAULT_CONFIG).confidence is None  # incubating
    warming = assemble_call(thesis, [insider_event()], ASOF, DEFAULT_CONFIG)
    assert warming.state is State.WARMING and warming.confidence is None
    armed = assemble_call(thesis, [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG)
    assert armed.state is State.ARMED and armed.confidence is not None


def test_arm_lapses_per_key_then_thesis_ages_out():
    """Per-key lapse: the arm holds on the confirmation's clock, then warms, then ages out entirely."""
    thesis = make_thesis()
    events = [insider_event(), breakout_event()]  # exit_by 06-20, arm_until 06-12
    assert assemble_call(thesis, events, date(2026, 6, 5), DEFAULT_CONFIG).state is State.ARMED
    # confirmation aged past arm_until (06-12) with no fill -> lapse to Warming (conviction still live)
    warming = assemble_call(thesis, events, date(2026, 6, 13), DEFAULT_CONFIG)
    assert warming.state is State.WARMING
    assert warming.key_conviction.turned and not warming.key_confirmation.turned
    # conviction aged past exit_by (06-20) -> nothing live -> Incubating
    assert (
        assemble_call(thesis, events, date(2026, 6, 21), DEFAULT_CONFIG).state is State.INCUBATING
    )


def test_no_entry_triggers_is_incubating_and_quiet():
    card = assemble_call(make_thesis(), [], ASOF, DEFAULT_CONFIG)
    assert card.state is State.INCUBATING
    assert card.verdict is Verdict.WATCHING
    assert card.exit_by is None


def test_assembler_is_deterministic():
    """Same (thesis, events, asof, cfg) -> byte-identical CallCard."""
    thesis = make_thesis()
    events = [insider_event(), breakout_event()]
    a = assemble_call(thesis, events, ASOF, DEFAULT_CONFIG)
    b = assemble_call(thesis, events, ASOF, DEFAULT_CONFIG)
    assert a.model_dump_json() == b.model_dump_json()


def test_thresholds_are_config_driven_not_hardcoded():
    """Behavioral guard (stronger than the lexical scan): the arming gate + risk veto read from CallConfig."""
    # turning off the confirmation requirement lets conviction-alone arm
    on_conviction = assemble_call(
        make_thesis(), [insider_event()], ASOF, CallConfig(arming_requires_confirmation=False)
    )
    assert on_conviction.state is State.ARMED

    # raising the block severity above the dilution score un-blocks the otherwise-armed call
    events = [insider_event(), breakout_event(), dilution_event()]
    assert assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG).state is State.WARMING
    lenient = CallConfig(risk_block_severity=0.95)
    assert assemble_call(make_thesis(), events, ASOF, lenient).state is State.ARMED


def test_strict_schema_rejects_unknown_signal_field():
    """extra='forbid': a typo'd detector field must error, not silently null the half-life."""
    with pytest.raises(ValidationError):
        SignalEvent(
            detector="insider_conviction",
            security_id=uuid.uuid4(),
            role=Role.ENTRY_TRIGGER,
            kind=Kind.INSIDER,
            grade=Grade.CORE,
            score=0.8,
            fired=True,
            label="x",
            half_life=18,  # typo for alpha_half_life_days -> must raise
            asof=ASOF,
        )


def test_risk_signal_must_not_carry_grade():
    """Taxonomy contract: risk signals are ungraded."""
    with pytest.raises(ValidationError):
        SignalEvent(
            detector="dilution_clock",
            security_id=uuid.uuid4(),
            role=Role.RISK_SIGNAL,
            kind=Kind.DILUTION_RISK,
            grade=Grade.CORE,  # risk signals are ungraded -> must raise
            score=0.9,
            fired=True,
            label="x",
            asof=ASOF,
        )


def test_nonpositive_half_life_rejected():
    """A 0/negative half-life would push exit_by <= asof and collapse the catalyst surface."""
    with pytest.raises(ValidationError):
        insider_event(half_life=0)


def test_assembler_has_no_magic_number_thresholds():
    """Lightweight lexical guard (the behavioral test above is the real one): no float literals in the assembler."""
    src = Path(assemble_call.__code__.co_filename).read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    floats = re.findall(r"\b\d+\.\d+\b", code)
    assert floats == [], f"thresholds must come from CallConfig; found float literals: {floats}"
