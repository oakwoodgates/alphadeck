from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from calls.confidence import confidence
from calls.counter_case import deterministic_counter_case
from calls.grading import call_grade
from domain.call import CallCard, KeyState, TriggerRef
from domain.config import CallConfig
from domain.enums import Grade, Role, State, Verdict
from domain.signal import SignalEvent
from domain.thesis import Catalyst, Thesis

# The LLM (M4b) supplies counter-case prose via this hook; it never sets state/verdict/grade/triggers.
CounterCaseFn = Callable[[Thesis, list[SignalEvent], list[str]], str]


def assemble_call(
    thesis: Thesis,
    events: list[SignalEvent],
    asof: date,
    cfg: CallConfig,
    counter_case_fn: CounterCaseFn | None = None,
) -> CallCard:
    """Pure, deterministic call-assembler: same (thesis, events, asof, cfg) -> same CallCard.

    This is the read path — the API recomputes the CallCard live at the requested ``asof``; the
    ``calls`` table only stores the result as an accountability record. See docs/CALL_LOGIC.md.
    """
    fired_entry = [e for e in events if e.role == Role.ENTRY_TRIGGER and e.fired]
    active_risk = [e for e in events if e.role == Role.RISK_SIGNAL and e.fired]

    # Two keys (CALL_LOGIC §2): a conviction trigger only warms; arming needs confirmation.
    conviction_on = any(e.kind in cfg.conviction_kinds for e in fired_entry)
    confirmation_on = any(e.kind in cfg.confirmation_kinds for e in fired_entry)

    grade = call_grade(fired_entry)

    # Risk-veto (§2): a severe risk signal withholds the Armed call on TIMING, never the thesis.
    blocking_risks = [r for r in active_risk if r.score >= cfg.risk_block_severity]

    both_keys = conviction_on and confirmation_on
    can_arm = (both_keys if cfg.arming_requires_confirmation else True) and grade is not None

    state = _state(thesis, fired_entry, can_arm, bool(blocking_risks), cfg)
    missing = _missing(conviction_on, confirmation_on, blocking_risks)
    exit_by = _exit_by(fired_entry, asof)

    if counter_case_fn is not None:
        counter_case = counter_case_fn(thesis, active_risk, missing)
    else:
        counter_case = deterministic_counter_case(thesis, active_risk, missing)

    return CallCard(
        thesis_id=thesis.id,
        asof=asof,
        state=state,
        verdict=_verdict(state, grade),
        grade=grade,
        expression=_expression(state, grade),
        exit_by=exit_by,
        catalyst_surface=_catalyst_surface(thesis.catalysts, exit_by),
        confidence=confidence(fired_entry, active_risk, cfg),
        key_conviction=KeyState(
            turned=conviction_on,
            label="Conviction",
            detail=(
                "A conviction entry trigger fired — the 'why now is real'."
                if conviction_on
                else "No conviction trigger yet."
            ),
        ),
        key_confirmation=KeyState(
            turned=confirmation_on,
            label="Confirmation",
            detail=(
                "The market is confirming (volume-backed breakout / relative strength)."
                if confirmation_on
                else "Awaiting market confirmation."
            ),
        ),
        triggers_fired=[
            TriggerRef(label=e.label, kind=e.kind, grade=e.grade, sources=list(e.provenance))
            for e in fired_entry
        ],
        missing=missing,
        counter_case=counter_case,
        safe_sleeve=None,
    )


def _state(
    thesis: Thesis,
    fired_entry: list[SignalEvent],
    can_arm: bool,
    blocked: bool,
    cfg: CallConfig,
) -> State:
    if thesis.position is not None:
        return State.MANAGING
    if can_arm and not blocked:
        return State.ARMED
    if len(fired_entry) >= cfg.warming_min_entry_triggers:
        return State.WARMING
    return State.INCUBATING


def _verdict(state: State, grade: Grade | None) -> Verdict:
    if state == State.INCUBATING:
        return Verdict.WATCHING
    if state == State.MANAGING:
        return Verdict.MANAGING
    if state == State.ARMED:
        return Verdict.CORE_ENTRY if grade == Grade.CORE else Verdict.FLIP_ONLY
    return Verdict.FLIP_ONLY if grade == Grade.FLIP else Verdict.NOT_YET


def _exit_by(fired_entry: list[SignalEvent], asof: date) -> date | None:
    halflives = [e.alpha_half_life_days for e in fired_entry if e.alpha_half_life_days is not None]
    if not halflives:
        return None
    return asof + timedelta(days=max(halflives))


def _catalyst_surface(catalysts: list[Catalyst], exit_by: date | None) -> list[Catalyst]:
    if exit_by is None:
        return []
    return [c for c in catalysts if c.when_date is not None and c.when_date <= exit_by]


def _missing(
    conviction_on: bool, confirmation_on: bool, blocking_risks: list[SignalEvent]
) -> list[str]:
    missing: list[str] = []
    if not conviction_on:
        missing.append("Conviction trigger (e.g. insider cluster / structural catalyst)")
    if not confirmation_on:
        missing.append("Volume-confirmed breakout (the confirmation key)")
    for r in blocking_risks:
        missing.append(f"Risk must clear before arming: {r.label}")
    return missing


def _expression(state: State, grade: Grade | None) -> str:
    if state == State.MANAGING:
        return "Position open — manage to the exit-by / half-life; trail the stop or take the gain."
    if state == State.ARMED and grade == Grade.CORE:
        return (
            "CORE: spot + options dated past exit-by; build into the leaders/shovels of the basket."
        )
    if grade == Grade.FLIP:
        return (
            "FLIP: small size, short-dated options; do not hold — exit at/just past the catalyst."
        )
    if state == State.WARMING:
        return "Not yet — hold for confirmation before any core entry; a flip is the only near-term play."
    return "Watching — banked idea, nothing to act on. No nag while incubating."
