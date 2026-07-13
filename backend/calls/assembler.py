from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta
from uuid import UUID

from calls.confidence import confidence
from calls.counter_case import deterministic_counter_case
from calls.grading import call_grade, grade_rank, weaker_grade
from domain.call import CallCard, KeyState, MemberCall, TriggerRef
from domain.config import CallConfig
from domain.enums import Grade, Kind, Role, State, Verdict
from domain.signal import SignalEvent
from domain.thesis import Catalyst, Thesis
from signals.common import entry_signal_is_live

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

    # Per-member risk scoping (M5 Part A): a severe risk withholds only the NAME it's on, never the theme.
    blocked_secs = {r.security_id for r in active_risk if r.score >= cfg.risk_block_severity}
    # The ACTIONABLE armed members = co-located AND not risk-blocked (conviction alone can arm when the
    # config doesn't require confirmation). Ranked for the menu + the headline; the headline is the top.
    arming_pool = armed_secs if cfg.arming_requires_confirmation else (armed_secs or conv_secs)
    ranked_actionable = rank_members(arming_pool - blocked_secs, live_entry, asof, cfg)
    # The security we grade for the thesis-level headline — and that the Board/Decision Queue show.
    armed_sec = ranked_actionable[0] if ranked_actionable else None

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

    # Hold dimension (§4) — keyed on the conviction's HORIZON, not its kind: a conviction whose
    # alpha-liveness reaches the hold threshold is hold-and-build (a small/flip-grade entry is a
    # STARTER); a short-horizon one is sentiment -> do-not-hold (a small entry is FLIP-only). So a
    # provisional-but-long catalyst holds while a fast insider flip doesn't, and the next signal kind
    # inherits correct behavior from its own horizon (no per-kind branch).
    conviction_holdable = any(
        (e.alpha_liveness_days or 0) >= cfg.conviction_hold_threshold_days
        for e in conviction_events
    )

    # Risk-veto (§2), per-member: the thesis arms iff some member is actionable; it's WITHHELD on risk when
    # co-located members exist but every one is risk-blocked (the veto holds timing, never the thesis).
    blocking_risks = [
        r
        for r in active_risk
        if r.score >= cfg.risk_block_severity and r.security_id in arming_pool
    ]
    can_arm = bool(ranked_actionable)
    risk_blocked = bool(arming_pool) and not can_arm and bool(blocking_risks)

    state = _state(thesis, live_entry, asof, can_arm, risk_blocked, cfg)

    # Graded confirmation (§3): a momentum-only (flip-grade) breakout still arms, but as a STARTER —
    # reduced confidence, a volume-gap counter-case, and a cautious expression. Volume stays central.
    momentum_only = (
        bool(confirmation_events) and confirmation_grade != Grade.CORE and state == State.ARMED
    )
    # A STARTER = any armed call whose entry grade is flip — i.e. EITHER key is weak (a momentum-only
    # breakout OR a provisional conviction). Drives the confidence cap so an enter-small call never reads
    # loud: the weak key has to pull confidence down even when the other key is strong (§7).
    is_starter = state == State.ARMED and entry_grade == Grade.FLIP

    missing = _missing(conviction_on, confirmation_on, blocking_risks)

    # Confidence is an ARMED-state metric (§7): the Armed card's bar. For a not-yet card
    # (Incubating/Warming) there is no entry to size, so it's None — never computed across the live
    # basket, which would noisy-OR unrelated breakouts (e.g. four separate names) into a false "high".
    # When armed it's scoped to the armed security's live triggers (grading is already scoped there),
    # so an unrelated live trigger on another name can't inflate it (a no-op for single-name HIMS).
    confidence_value = (
        confidence(
            [e for e in live_entry if e.security_id == armed_sec],
            [r for r in active_risk if r.security_id == armed_sec],
            cfg,
            is_starter=is_starter,
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

    # The Confirmation key's detail reflects the ACTUAL confirmation grade (not a hardcoded
    # "volume-backed"), so it can't overstate a momentum-only breakout or contradict the caveat above.
    confirmation_detail = (
        "Awaiting market confirmation."
        if not confirmation_on
        else (
            "The market is confirming (a volume-backed breakout)."
            if confirmation_grade == Grade.CORE
            else "Momentum-only — not yet volume-confirmed."
        )
    )
    if counter_case_fn is not None:
        counter_case = counter_case_fn(thesis, active_risk, missing, caveats)
    else:
        counter_case = deterministic_counter_case(thesis, active_risk, missing, caveats)

    # M5 Part A — the per-member ranked menu (reuses the scoped helpers per member; no new arming logic):
    # the actionable armed members, RANKED (freshness band on liveness runway, grade within), then the
    # confirmation-only "watch" tier ("moving, no conviction yet"). The headline above is armed_members[0].
    armed_members = [
        _member_call(sec, live_entry, active_risk, asof, cfg) for sec in ranked_actionable
    ]
    watch_secs = sorted(
        conf_secs - conv_secs,
        key=lambda s: (grade_rank(_confirmation_grade(s, live_entry, cfg)), s.int),
        reverse=True,
    )
    watch_members = [_member_call(sec, live_entry, active_risk, asof, cfg) for sec in watch_secs]

    # Per-member Managing attribution (§4): when the open position carries the held NAME (the take
    # row's security_id — absent on a thesis-level take and the seed-era stored columns, which
    # attribute nothing), the held member's call LEADS armed_members with the two ACTION fields
    # overridden: verdict = managing (the action is "manage", not "enter") and confidence = None
    # (an entry-sizing bar; the thesis-level Managing rule, applied per-member). Built by the same
    # scoped helper as every member, so its live grades/clocks/triggers ride along — computed
    # facts, unchanged. The held name never sits in the watch tier; the entry ranking below it is
    # untouched. No-lookahead is inherited: state is MANAGING only when the as-of-derived position
    # is open (§2), so a future-dated fill neither flips the state nor attributes a member.
    position = thesis.position
    if state is State.MANAGING and position is not None and position.security_id is not None:
        held = position.security_id
        held_call = _member_call(held, live_entry, active_risk, asof, cfg).model_copy(
            update={"verdict": Verdict.MANAGING, "confidence": None}
        )
        armed_members = [held_call] + [m for m in armed_members if m.security_id != held]
        watch_members = [m for m in watch_members if m.security_id != held]

    return CallCard(
        thesis_id=thesis.id,
        asof=asof,
        state=state,
        verdict=_verdict(state, conviction_grade, entry_grade, conviction_holdable),
        conviction_grade=conviction_grade,
        confirmation_grade=confirmation_grade,
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
            conviction_holdable,
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
            detail=confirmation_detail,
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
        armed_members=armed_members,
        watch_members=watch_members,
    )


def _live(e: SignalEvent, asof: date) -> bool:
    """A fired entry trigger counts only while inside its alpha-liveness window (``e.asof`` = its fire date)."""
    if e.alpha_liveness_days is None:
        raise ValueError("fired entry trigger is missing alpha_liveness_days")
    return entry_signal_is_live(e.asof, e.alpha_liveness_days, asof)


def _member_events(sec: UUID, live_entry: list[SignalEvent], kinds) -> list[SignalEvent]:
    return [e for e in live_entry if e.kind in kinds and e.security_id == sec]


def _confirmation_grade(sec: UUID, live_entry: list[SignalEvent], cfg: CallConfig) -> Grade | None:
    return call_grade(_member_events(sec, live_entry, cfg.confirmation_kinds))


def rank_members(
    secs: set[UUID], live_entry: list[SignalEvent], asof: date, cfg: CallConfig
) -> list[UUID]:
    """Rank armed members for the per-member menu (M5 Part A): a freshness BAND on liveness runway PRIMARY,
    grade WITHIN the band — separate axes as a deterministic tuple, never fused into one score (the
    through-line). "Runway" = the member's LIVENESS horizon (``exit_by - asof``, the conviction hold clock
    from ``_clock`` / ``alpha_liveness_days``) — NOT the dilution cash-runway. A member with fewer than
    ``cfg.headline_lapsing_soon_days`` of runway left is "lapsing-soon" and ranks below any "fresh" member
    *regardless of grade* (so a core arm about to lapse doesn't headline over a long-runway starter).
    Tuple, best-first: (is_fresh, entry-grade rank, is_own, runway_days, conviction score, id) — `is_own`
    (M5b) orders a name armed on its OWN conviction above one armed only on the theme conviction (the
    fallback), as a within-band tiebreak after grade; freshness stays primary (the within-band weighting
    is a RECALIBRATION dial).
    """

    def key(sec: UUID) -> tuple[bool, int, bool, int, float, int]:
        conv = _member_events(sec, live_entry, cfg.conviction_kinds)
        conf = _member_events(sec, live_entry, cfg.confirmation_kinds)
        exit_by = _clock(conv)
        # liveness runway in days; no liveness window (None) = effectively unbounded (date.max) -> "fresh"
        runway_days = ((exit_by or date.max) - asof).days
        is_fresh = runway_days >= cfg.headline_lapsing_soon_days
        conviction_score = max((e.score for e in conv), default=0)
        # own-above-theme (M5b): a name with its OWN conviction outranks a theme-armed one within the
        # same band + grade. Keyed on the conviction SOURCE (a property), not the kind (the through-line).
        is_own = bool(_member_events(sec, live_entry, cfg.own_conviction_kinds))
        return (
            is_fresh,
            grade_rank(weaker_grade(call_grade(conv), call_grade(conf))),
            is_own,
            runway_days,
            conviction_score,
            sec.int,
        )

    return sorted(secs, key=key, reverse=True)


def _member_call(
    sec: UUID,
    live_entry: list[SignalEvent],
    active_risk: list[SignalEvent],
    asof: date,
    cfg: CallConfig,
) -> MemberCall:
    """One basket member's own call (M5 Part A). An ARMED member (co-located + not risk-blocked) gets a
    verdict + confidence; a confirmation-only "watch" member gets its breakout grade + clock but no verdict.
    Reuses the same scoped helpers as the thesis-level call — no new arming logic."""
    conv = _member_events(sec, live_entry, cfg.conviction_kinds)
    conf = _member_events(sec, live_entry, cfg.confirmation_kinds)
    conviction_grade = call_grade(conv)
    confirmation_grade = call_grade(conf)
    # theme-armed (M5b): this member's conviction is the theme FALLBACK, not its own — a display flag
    # (the basis), not a behavior branch. Keyed on the conviction source, never re-coupled to behavior.
    theme_armed = any(e.kind is Kind.THEME_CONVICTION for e in conv) and not any(
        e.kind in cfg.own_conviction_kinds for e in conv
    )
    member_risk = [r for r in active_risk if r.security_id == sec]
    blocked = any(r.score >= cfg.risk_block_severity for r in member_risk)
    armed = bool(conv) and bool(conf) and not blocked

    exit_by = _clock(conv)
    entry_grade: Grade | None = None
    verdict: Verdict | None = None
    conf_value: float | None = None
    lapsing = False
    if armed:
        entry_grade = weaker_grade(conviction_grade, confirmation_grade)
        holdable = any(
            (e.alpha_liveness_days or 0) >= cfg.conviction_hold_threshold_days for e in conv
        )
        verdict = _verdict(State.ARMED, conviction_grade, entry_grade, holdable)
        conf_value = confidence(conv + conf, member_risk, cfg, is_starter=entry_grade == Grade.FLIP)
        # lapsing = the same freshness band the ranking uses (the dial), surfaced for the UI to flag
        lapsing = exit_by is not None and (exit_by - asof).days < cfg.headline_lapsing_soon_days

    return MemberCall(
        security_id=sec,
        verdict=verdict,
        conviction_grade=conviction_grade,
        confirmation_grade=confirmation_grade,
        entry_grade=entry_grade,
        confidence=conf_value,
        exit_by=exit_by,
        arm_until=_clock(conf),
        lapsing=lapsing,
        theme_armed=theme_armed,
        triggers=[
            TriggerRef(
                label=e.label,
                kind=e.kind,
                grade=e.grade,
                security_id=e.security_id,
                sources=list(e.provenance),
            )
            for e in (conv + conf)
        ],
    )


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


def _verdict(
    state: State,
    conviction_grade: Grade | None,
    entry_grade: Grade | None,
    conviction_holdable: bool,
) -> Verdict:
    if state == State.INCUBATING:
        return Verdict.WATCHING
    if state == State.MANAGING:
        return Verdict.MANAGING
    if state == State.ARMED:
        if conviction_grade == Grade.FLIP:
            # SIZE from grade (flip -> small); HOLD from horizon. A small entry on a hold-worthy
            # conviction is a STARTER (enter small, build); on a short-horizon one it's FLIP-only
            # (do-not-hold). Same archetype as a core-conviction + weak-confirmation starter — both
            # mean "enter small, build" — the difference (build into confirmation firming vs more
            # catalysts) lives in the expression/counter-case, not a separate verdict.
            return Verdict.STARTER_ENTRY if conviction_holdable else Verdict.FLIP_ONLY
        # core conviction (full size): a full core entry only when confirmation is volume-backed
        # (entry grade core); otherwise a STARTER that upgrades to core when volume confirms.
        return Verdict.CORE_ENTRY if entry_grade == Grade.CORE else Verdict.STARTER_ENTRY
    # Warming: a hold-worthy conviction is a real thesis waiting on confirmation (not_yet); a
    # short-horizon flip conviction is sentiment (flip_only).
    if conviction_grade == Grade.FLIP and not conviction_holdable:
        return Verdict.FLIP_ONLY
    return Verdict.NOT_YET


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
    conviction_holdable: bool,
) -> str:
    if state == State.MANAGING:
        return "Position open — manage to the exit-by; trail the stop or take the gain."
    if state == State.ARMED:
        if conviction_grade == Grade.FLIP:
            if not conviction_holdable:
                return "FLIP: small size, short-dated options; do not hold — exit at/just past the catalyst."
            return (
                "STARTER: a provisional conviction (real but not yet binding) with the market "
                "confirming — enter small; build as it firms (a binding deal, or more catalysts), not "
                "max size off one early step."
            )
        if momentum_only:
            return (
                "Core thesis, STARTER entry — the breakout is momentum-only (volume hasn't "
                "confirmed). Start small; build to core size only when a real volume breakout confirms."
            )
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
        if conviction_grade == Grade.FLIP and not conviction_holdable:
            return "FLIP only (small, short-dated); the structural core entry isn't confirmed yet."
        return "Not yet — hold for a volume-confirmed breakout before entering."
    return "Watching — banked idea, nothing to act on. No nag while incubating."
