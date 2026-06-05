from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

from calls.confidence import confidence
from calls.counter_case import deterministic_counter_case
from calls.grading import call_grade, weaker_grade
from domain.call import CallCard, KeyState, TriggerRef
from domain.config import CallConfig
from domain.enums import Grade, Role, State, Verdict
from domain.signal import SignalEvent
from domain.thesis import Catalyst, Thesis

# The LLM (M4b) supplies counter-case prose via this hook; it never sets state/verdict/grade/triggers.
CounterCaseFn = Callable[[Thesis, list[SignalEvent], list[str], list[str]], str]


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
    # NOTE: keys are evaluated at the THESIS level across the whole basket (matches the mockup's
    # "group breakout"). Whether a conviction and its confirmation must co-locate on the same
    # security_id is a deliberate M3 design decision, surfaced in review — not settled here.
    conviction_on = any(e.kind in cfg.conviction_kinds for e in fired_entry)
    confirmation_on = any(e.kind in cfg.confirmation_kinds for e in fired_entry)

    # Two grades, kept distinct (CALL_LOGIC §4): the CONVICTION grade is the thesis quality (the
    # conviction key); the ENTRY grade is the weaker of the two keys and is what drives the verdict
    # the operator acts on — so a core thesis whose volume hasn't confirmed reads as a STARTER entry,
    # never a bare "core entry" (which invites over-committing). Conviction is shown separately so the
    # thesis's core quality isn't lost; starter is the upgrade path to a full core entry.
    conviction_events = [e for e in fired_entry if e.kind in cfg.conviction_kinds]
    confirmation_events = [e for e in fired_entry if e.kind in cfg.confirmation_kinds]
    conviction_grade = call_grade(conviction_events)
    confirmation_grade = call_grade(confirmation_events)
    entry_grade = weaker_grade(conviction_grade, confirmation_grade)

    # Risk-veto (§2): a severe risk signal withholds the Armed call on TIMING, never the thesis.
    blocking_risks = [r for r in active_risk if r.score >= cfg.risk_block_severity]

    both_keys = conviction_on and confirmation_on
    can_arm = (both_keys if cfg.arming_requires_confirmation else True) and entry_grade is not None
    risk_blocked = can_arm and bool(
        blocking_risks
    )  # armable, but a severe risk withholds it on timing

    state = _state(thesis, fired_entry, can_arm, bool(blocking_risks), cfg)

    # Graded confirmation (§3): a volume-backed breakout is CORE-quality; a momentum thrust on weak
    # volume still arms but as a flip-grade (STARTER) entry — surfaced as the starter verdict, reduced
    # confidence, a volume-gap counter-case, and a cautious expression. Volume stays central.
    momentum_only = (
        bool(confirmation_events) and confirmation_grade != Grade.CORE and state == State.ARMED
    )

    missing = _missing(conviction_on, confirmation_on, blocking_risks)
    exit_by = _exit_by(fired_entry, asof)

    caveats = (
        [
            "Confirmation is momentum-only, not volume-backed — the market hasn't put real "
            "participation behind this yet."
        ]
        if momentum_only
        else []
    )
    if counter_case_fn is not None:
        counter_case = counter_case_fn(thesis, active_risk, missing, caveats)
    else:
        counter_case = deterministic_counter_case(thesis, active_risk, missing, caveats)

    return CallCard(
        thesis_id=thesis.id,
        asof=asof,
        state=state,
        verdict=_verdict(state, conviction_grade, entry_grade),
        conviction_grade=conviction_grade,
        entry_grade=entry_grade,
        expression=_expression(state, conviction_grade, entry_grade, risk_blocked, momentum_only),
        exit_by=exit_by,
        catalyst_surface=_catalyst_surface(thesis.catalysts, exit_by),
        confidence=confidence(fired_entry, active_risk, cfg, momentum_only=momentum_only),
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


def _verdict(state: State, conviction_grade: Grade | None, entry_grade: Grade | None) -> Verdict:
    if state == State.INCUBATING:
        return Verdict.WATCHING
    if state == State.MANAGING:
        return Verdict.MANAGING
    if state == State.ARMED:
        if conviction_grade == Grade.FLIP:
            return Verdict.FLIP_ONLY  # a flip thesis: small, short-dated, do-not-hold
        # core thesis (hold-and-build): a full core entry only when confirmation is volume-backed
        # (entry grade core); otherwise a STARTER entry that upgrades to core when volume confirms.
        return Verdict.CORE_ENTRY if entry_grade == Grade.CORE else Verdict.STARTER_ENTRY
    return Verdict.FLIP_ONLY if conviction_grade == Grade.FLIP else Verdict.NOT_YET


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


def _expression(
    state: State,
    conviction_grade: Grade | None,
    entry_grade: Grade | None,
    risk_blocked: bool,
    momentum_only: bool,
) -> str:
    if state == State.MANAGING:
        return "Position open — manage to the exit-by / half-life; trail the stop or take the gain."
    if state == State.ARMED:
        if momentum_only:
            return (
                "Core thesis, STARTER entry — the breakout is momentum-only (volume hasn't "
                "confirmed). Start small; build to core size only when a real volume breakout confirms."
            )
        if conviction_grade == Grade.FLIP:
            return "FLIP: small size, short-dated options; do not hold — exit at/just past the catalyst."
        return "CORE: spot + options past exit-by; build into the leaders/shovels of the basket."
    if risk_blocked:
        # both keys are in and the grade qualifies, but a severe risk withholds the entry on TIMING
        return (
            "Entry withheld on risk/timing — the keys are in, but a severe risk signal must clear "
            "before arming (see the counter-case)."
        )
    if state == State.WARMING:
        if conviction_grade == Grade.FLIP:
            return "FLIP only (small, short-dated); the structural core entry isn't confirmed yet."
        return "Not yet — hold for a volume-confirmed breakout before any core entry."
    return "Watching — banked idea, nothing to act on. No nag while incubating."
