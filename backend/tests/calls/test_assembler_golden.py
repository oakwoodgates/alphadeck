from __future__ import annotations

import re
from datetime import date
from pathlib import Path

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, State, Verdict
from tests.calls.factories import (
    ASOF,
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
    assert card.grade is Grade.CORE
    assert card.key_conviction.turned is True
    assert card.key_confirmation.turned is False
    assert any("breakout" in m.lower() for m in card.missing)
    assert card.exit_by == date(2026, 6, 20)  # asof + 18d insider half-life


def test_insider_plus_breakout_arms_core_entry():
    """Both keys turned -> Armed / core_entry, with exit-by and a filtered catalyst surface."""
    card = assemble_call(make_thesis(), [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG)
    assert card.state is State.ARMED
    assert card.verdict is Verdict.CORE_ENTRY
    assert card.grade is Grade.CORE
    assert card.key_conviction.turned and card.key_confirmation.turned
    assert card.missing == []
    assert card.exit_by == date(2026, 6, 20)  # asof + max(18, 10)
    assert len(card.catalyst_surface) == 1  # only the dated event inside the half-life window
    assert card.catalyst_surface[0].when_date == date(2026, 6, 11)
    assert len(card.triggers_fired) == 2
    assert card.triggers_fired[0].sources[0].ref  # provenance link is present


def test_severe_dilution_blocks_arming_on_timing_not_thesis():
    """Risk-veto: a severe risk signal withholds Armed on timing, penalizes confidence, never vetoes the thesis."""
    thesis = make_thesis()
    armed = assemble_call(thesis, [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG)
    blocked = assemble_call(
        thesis, [insider_event(), breakout_event(), dilution_event()], ASOF, DEFAULT_CONFIG
    )
    assert armed.state is State.ARMED
    assert blocked.state is State.WARMING  # Armed withheld
    assert blocked.key_conviction.turned and blocked.key_confirmation.turned  # keys still turned
    assert blocked.confidence < armed.confidence  # risk penalizes confidence
    assert "risk" in blocked.counter_case.lower()  # risk signal feeds the counter-case
    assert blocked.thesis_id == thesis.id  # the thesis itself is never vetoed


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


def test_assembler_has_no_magic_number_thresholds():
    """Acceptance #3: every threshold comes from CallConfig, never a literal in the assembler."""
    src = Path(assemble_call.__code__.co_filename).read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    floats = re.findall(r"\b\d+\.\d+\b", code)
    assert floats == [], f"thresholds must come from CallConfig; found float literals: {floats}"
