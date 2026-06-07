from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from uuid import UUID

from calls.confidence import confidence
from calls.counter_case import deterministic_counter_case
from calls.grading import call_grade, grade_rank, weaker_grade
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

    This is the read path — the API recomputes the CallCard live at the requested ``asof`` from the
    dated signal stream (re-derived from the bitemporal facts, not a persisted firing layer); the
    ``calls`` table only stores the result as an accountability record. See docs/CALL_LOGIC.md.
    """
    fired_entry = [e for e in events if e.role == Role.ENTRY_TRIGGER and e.fired]
    active_risk = [e for e in events if e.role == Role.RISK_SIGNAL and e.fired]

    # Date-aware liveness (§2): a fired entry trigger counts only while inside its alpha-liveness window.
    # ``e.asof`` is the trigger's FIRE date (the event date), so an Armed call stays sticky across a
    # consolidation (the breakout firing is still live) and lapses once that firing ages out.
    live_entry = [e for e in fired_entry if _live(e, asof)]

    # Two keys + CO-LOCATION (§2): a conviction trigger only warms; arming needs a confirmation on
    # the SAME security. Group/sector ("group breakout") confirmation across a basket is a separate,
    # explicitly-labeled mode (M5) — not folded into this single-name arm.
    conv_secs = {e.security_id for e in live_entry if e.kind in cfg.conviction_kinds}
    conf_secs = {e.security_id for e in live_entry if e.kind in cfg.confirmation_kinds}
    armed_secs = conv_secs & conf_secs
    conviction_on = bool(conv_secs)
    confirmation_on = bool(conf_secs)
    both_keys = bool(armed_secs)

    # The security that arms — and that we grade (not the whole basket): the co-located one with the
    # strongest entry grade. When confirmation isn't required (config), conviction alone can arm.
    armed_sec = _arming_security(armed_secs, live_entry, cfg)
    if armed_sec is None and not cfg.arming_requires_confirmation:
        armed_sec = _arming_security(conv_secs, live_entry, cfg)

    # Two grades, kept distinct (§4): the CONVICTION grade is the thesis quality; the ENTRY grade is
    # the weaker of the two keys and drives the verdict the operator acts on. Graded on the armed
    # security when armed, else across the live basket (so a Warming card still shows core conviction).
    scope = armed_sec
    conviction_events = [
        e
        for e in live_entry
        if e.kind in cfg.conviction_kinds and (scope is None or e.security_id == scope)
    ]
    confirmation_events = [
        e
        for e in live_entry
        if e.kind in cfg.confirmation_kinds and (scope is None or e.security_id == scope)
    ]
    conviction_grade = call_grade(conviction_events)
    confirmation_grade = call_grade(confirmation_events)
    entry_grade = weaker_grade(conviction_grade, confirmation_grade)

    # Risk-veto (§2): a severe risk signal withholds the Armed call on TIMING, never the thesis.
    blocking_risks = [r for r in active_risk if r.score >= cfg.risk_block_severity]

    can_arm = (
        both_keys if cfg.arming_requires_confirmation else conviction_on
    ) and entry_grade is not None
    risk_blocked = can_arm and bool(blocking_risks)

    state = _state(thesis, live_entry, asof, can_arm, bool(blocking_risks), cfg)

    # Graded confirmation (§3): a momentum-only (flip-grade) breakout still arms, but as a STARTER —
    # reduced confidence, a volume-gap counter-case, and a cautious expression. Volume stays central.
    momentum_only = (
        bool(confirmation_events) and confirmation_grade != Grade.CORE and state == State.ARMED
    )

    missing = _missing(conviction_on, confirmation_on, blocking_risks)

    # Confidence is an ARMED-state metric (§7): the Armed card's bar. For a not-yet card
    # (Incubating/Warming) there is no entry to size, so it's None — never computed across the live
    # basket, which would noisy-OR unrelated breakouts (e.g. four separate names) into a false "high".
    # When armed it's scoped to the armed security's live triggers (grading is already scoped there),
    # so an unrelated live trigger on another name can't inflate it (a no-op for single-name HIMS).
    confidence_value = (
        confidence(
            [e for e in live_entry if e.security_id == armed_sec],
            active_risk,
            cfg,
            momentum_only=momentum_only,
        )
        if state == State.ARMED and armed_sec is not None
        else None
    )

    # Two fire-date-anchored clocks (§6). exit_by = the HOLD horizon (the conviction key) and drives
    # the catalyst surface; arm_until = the ENTRY window (the confirmation key) — the arm lapses once
    # asof passes it. Both anchored to each trigger's fire date, so they don't slide as asof advances.
    exit_by = _clock(conviction_events)
    arm_until = _clock(confirmation_events)

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
        armed_security_id=armed_sec if state == State.ARMED else None,
        expression=_expression(
            state,
            conviction_grade,
            entry_grade,
            risk_blocked,
            momentum_only,
            conviction_on,
            confirmation_on,
        ),
        exit_by=exit_by,
        arm_until=arm_until,
        catalyst_surface=_catalyst_surface(thesis.catalysts, exit_by),
        confidence=confidence_value,
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
                "The market is confirming (a volume-backed breakout)."
                if confirmation_on
                else "Awaiting market confirmation."
            ),
        ),
        triggers_fired=[
            TriggerRef(
                label=e.label,
                kind=e.kind,
                grade=e.grade,
                security_id=e.security_id,
                sources=list(e.provenance),
            )
            for e in live_entry
        ],
        risk_signals=[
            TriggerRef(
                label=r.label,
                kind=r.kind,
                grade=None,
                security_id=r.security_id,
                sources=list(r.provenance),
            )
            for r in active_risk
        ],
        missing=missing,
        counter_case=counter_case,
        safe_sleeve=None,
    )


def _live(e: SignalEvent, asof: date) -> bool:
    """A fired entry trigger counts only while inside its alpha-liveness window (``e.asof`` = its fire date)."""
    if e.alpha_liveness_days is None:
        return True
    return asof <= e.asof + timedelta(days=e.alpha_liveness_days)


def _arming_security(
    secs: set[UUID], live_entry: list[SignalEvent], cfg: CallConfig
) -> UUID | None:
    """The security that arms: the candidate with the strongest entry grade (deterministic tiebreak)."""
    if not secs:
        return None

    def entry_grade_for(sec: UUID) -> Grade | None:
        conv = [e for e in live_entry if e.kind in cfg.conviction_kinds and e.security_id == sec]
        conf = [e for e in live_entry if e.kind in cfg.confirmation_kinds and e.security_id == sec]
        return weaker_grade(call_grade(conv), call_grade(conf))

    def conviction_score(sec: UUID) -> float:
        return max(
            (
                e.score
                for e in live_entry
                if e.kind in cfg.conviction_kinds and e.security_id == sec
            ),
            default=0,
        )

    return max(secs, key=lambda s: (grade_rank(entry_grade_for(s)), conviction_score(s), s.int))


def _state(
    thesis: Thesis,
    live_entry: list[SignalEvent],
    asof: date,
    can_arm: bool,
    blocked: bool,
    cfg: CallConfig,
) -> State:
    position = thesis.position
    if position is not None and (position.opened_on is None or position.opened_on <= asof):
        return State.MANAGING
    if can_arm and not blocked:
        return State.ARMED
    if len(live_entry) >= cfg.warming_min_entry_triggers:
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


def _clock(events: list[SignalEvent]) -> date | None:
    """The latest (fire_date + alpha-liveness window) over a set of triggers — None if none carry one."""
    ends = [
        e.asof + timedelta(days=e.alpha_liveness_days)
        for e in events
        if e.alpha_liveness_days is not None
    ]
    return max(ends) if ends else None


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
    conviction_on: bool,
    confirmation_on: bool,
) -> str:
    if state == State.MANAGING:
        return "Position open — manage to the exit-by; trail the stop or take the gain."
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
        if confirmation_on and not conviction_on:
            # the market moved but there's no conviction trigger — a breakout alone isn't a reason
            return (
                "The market's moving (confirmation in) but there's no conviction trigger yet — "
                "watching the theme, not acting. A breakout alone isn't a reason to enter."
            )
        if conviction_grade == Grade.FLIP:
            return "FLIP only (small, short-dated); the structural core entry isn't confirmed yet."
        return "Not yet — hold for a volume-confirmed breakout before any core entry."
    return "Watching — banked idea, nothing to act on. No nag while incubating."
