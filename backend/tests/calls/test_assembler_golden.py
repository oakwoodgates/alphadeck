from __future__ import annotations

import re
import uuid
from datetime import date, timedelta
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
    catalyst_event,
    dilution_event,
    insider_event,
    make_thesis,
    theme_conviction_event,
)


def test_holdable_flip_catalyst_arms_a_starter_not_a_flip():
    """The class fix (#10 option A): the verdict reads the conviction's HORIZON, not its kind. A flip
    (provisional) catalyst with a long horizon + a confirmed breakout -> STARTER (enter small, build),
    NOT flip_only / do-not-hold."""
    card = assemble_call(
        make_thesis(),
        [catalyst_event(grade=Grade.FLIP, liveness=400), breakout_event()],
        ASOF,
        DEFAULT_CONFIG,
    )
    assert card.state is State.ARMED
    assert card.conviction_grade is Grade.FLIP  # still a provisional conviction (small size)
    assert card.verdict is Verdict.STARTER_ENTRY  # but hold-worthy -> starter, not flip_only
    assert "do not hold" not in card.expression.lower()


def test_verdict_keys_on_horizon_not_kind():
    """The mirror: same flip grade, SHORT horizon -> flip_only (do-not-hold). A flip catalyst and a fast
    flip insider agree at the same horizon — proving the hold dimension reads horizon, not kind."""
    short_catalyst = assemble_call(
        make_thesis(),
        [catalyst_event(grade=Grade.FLIP, liveness=20), breakout_event()],
        ASOF,
        DEFAULT_CONFIG,
    )
    flip_insider = assemble_call(
        make_thesis(), [insider_event(grade=Grade.FLIP), breakout_event()], ASOF, DEFAULT_CONFIG
    )
    assert short_catalyst.state is State.ARMED and short_catalyst.verdict is Verdict.FLIP_ONLY
    assert flip_insider.state is State.ARMED and flip_insider.verdict is Verdict.FLIP_ONLY


def test_insider_only_warms_but_does_not_arm():
    """Conviction warms; without confirmation the second key stays off (two-key model)."""
    card = assemble_call(make_thesis(), [insider_event()], ASOF, DEFAULT_CONFIG)
    assert card.state is State.WARMING
    assert card.verdict is Verdict.NOT_YET
    assert card.conviction_grade is Grade.CORE
    assert card.key_conviction.turned is True
    assert card.key_confirmation.turned is False
    assert any("breakout" in m.lower() for m in card.missing)
    # the hold clock = fire date + the CORE conviction horizon (graded, multi-month)
    assert card.exit_by == ASOF + timedelta(days=DEFAULT_CONFIG.insider_core_alpha_liveness_days)


def test_insider_plus_breakout_arms_core_entry():
    """Both keys turned -> Armed / core_entry, with exit-by and a filtered catalyst surface."""
    card = assemble_call(make_thesis(), [insider_event(), breakout_event()], ASOF, DEFAULT_CONFIG)
    assert card.state is State.ARMED
    assert card.verdict is Verdict.CORE_ENTRY  # the breakout fixture is volume-backed (core)
    assert card.conviction_grade is Grade.CORE and card.entry_grade is Grade.CORE
    assert card.key_conviction.turned and card.key_confirmation.turned
    assert card.missing == []
    # hold clock = insider fire 06-02 + the CORE conviction horizon; entry window = breakout + 10d
    assert card.exit_by == ASOF + timedelta(days=DEFAULT_CONFIG.insider_core_alpha_liveness_days)
    assert card.arm_until == date(
        2026, 6, 12
    )  # entry window: breakout fire 06-02 + 10d liveness window
    assert card.armed_security_id == SID  # the co-located security that armed
    # the longer (multi-month) core hold window now surfaces both dated catalysts (06-11 + 09-01)
    assert [c.when_date for c in card.catalyst_surface] == [date(2026, 6, 11), date(2026, 9, 1)]
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
    assert momentum.confidence <= DEFAULT_CONFIG.starter_confidence_cap
    assert "momentum-only" in momentum.counter_case.lower()
    assert "starter" in momentum.expression.lower() and "volume" in momentum.expression.lower()
    # the Confirmation KEY reflects the actual grade (no hardcoded "volume-backed") and agrees with the caveat:
    assert backed.confirmation_grade is Grade.CORE
    assert "volume-backed" in backed.key_confirmation.detail.lower()
    assert momentum.confirmation_grade is Grade.FLIP
    assert "momentum-only" in momentum.key_confirmation.detail.lower()
    assert "volume-backed" not in momentum.key_confirmation.detail.lower()


def test_provisional_conviction_starter_confidence_is_capped():
    """The confidence fix: a STARTER off a *provisional conviction* (a flip catalyst) + a STRONG
    (volume-backed) breakout must NOT read loud — the weak key has to pull confidence down even though
    the breakout is strong (else an enter-small call out-ranks steadier calls / inverts loudness).
    """
    card = assemble_call(
        make_thesis(),
        [
            catalyst_event(grade=Grade.FLIP, liveness=400),
            breakout_event(grade=Grade.CORE, score=0.9),
        ],
        ASOF,
        DEFAULT_CONFIG,
    )
    assert card.state is State.ARMED and card.verdict is Verdict.STARTER_ENTRY
    assert card.confidence is not None
    assert (
        card.confidence <= DEFAULT_CONFIG.starter_confidence_cap
    )  # capped despite the strong breakout
    # a full CORE entry (both keys strong) is NOT capped — the cap is a starter-only ceiling
    core = assemble_call(
        make_thesis(),
        [
            catalyst_event(grade=Grade.CORE, liveness=400),
            breakout_event(grade=Grade.CORE, score=0.9),
        ],
        ASOF,
        DEFAULT_CONFIG,
    )
    assert core.verdict is Verdict.CORE_ENTRY
    assert core.confidence > DEFAULT_CONFIG.starter_confidence_cap


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
    hold = ASOF + timedelta(days=DEFAULT_CONFIG.insider_core_alpha_liveness_days)
    for q in (date(2026, 6, 3), date(2026, 6, 5), date(2026, 6, 8)):
        card = assemble_call(thesis, events, q, DEFAULT_CONFIG)
        assert card.exit_by == hold, q  # conviction/hold clock: 06-02 + core horizon
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
    events = [insider_event(), breakout_event()]  # arm_until 06-12; exit_by = 06-02 + core horizon
    assert assemble_call(thesis, events, date(2026, 6, 5), DEFAULT_CONFIG).state is State.ARMED
    # confirmation aged past arm_until (06-12) with no fill -> lapse to Warming (conviction still live
    # on its multi-month core clock)
    warming = assemble_call(thesis, events, date(2026, 6, 13), DEFAULT_CONFIG)
    assert warming.state is State.WARMING
    assert warming.key_conviction.turned and not warming.key_confirmation.turned
    # conviction aged past its (core) hold horizon -> nothing live -> Incubating
    aged_out = ASOF + timedelta(days=DEFAULT_CONFIG.insider_core_alpha_liveness_days + 1)
    assert assemble_call(thesis, events, aged_out, DEFAULT_CONFIG).state is State.INCUBATING


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
    """extra='forbid': a typo'd detector field must error, not silently null the liveness window."""
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
            liveness=18,  # typo for alpha_liveness_days -> must raise
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


def test_nonpositive_liveness_rejected():
    """A 0/negative liveness window would push exit_by <= asof and collapse the catalyst surface."""
    with pytest.raises(ValidationError):
        insider_event(liveness=0)


_SID2 = uuid.UUID(int=0x3333)


def test_per_member_menu_ranks_fresh_starter_above_lapsing_core():
    """M5 Part A — the freshness BAND: a LAPSING core ranks BELOW a FRESH starter (runway over grade, on
    separate axes, not fused). Member A is a core arm with ~20d of liveness runway (< the lapsing threshold);
    member B is a flip starter with ~400d. B headlines; A is preserved at #2 (still core)."""
    events = [
        catalyst_event(grade=Grade.CORE, liveness=20, security_id=SID),  # A: core but LAPSING
        breakout_event(grade=Grade.CORE, security_id=SID),
        catalyst_event(grade=Grade.FLIP, liveness=400, security_id=_SID2),  # B: starter but FRESH
        breakout_event(grade=Grade.FLIP, security_id=_SID2),
    ]
    card = assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG)
    assert card.state is State.ARMED
    # the fresh starter (B) out-ranks the lapsing core (A) — runway band primary, grade within
    assert card.armed_security_id == _SID2
    assert [m.security_id for m in card.armed_members] == [_SID2, SID]
    assert card.armed_members[0].entry_grade is Grade.FLIP  # B: fresh starter, #1
    assert card.armed_members[0].lapsing is False
    assert card.armed_members[1].conviction_grade is Grade.CORE  # A: lapsing core, preserved at #2
    assert card.armed_members[1].verdict is Verdict.CORE_ENTRY
    assert card.armed_members[1].lapsing is True  # flagged lapsing (ranks below the fresh starter)


def test_single_member_thesis_yields_a_one_entry_menu():
    """Backward-compatible: a single-name thesis is a degenerate one-member menu, no watch tier."""
    card = assemble_call(
        make_thesis(), [insider_event(), breakout_event(grade=Grade.CORE)], ASOF, DEFAULT_CONFIG
    )
    assert card.state is State.ARMED
    assert card.armed_security_id == SID
    assert [m.security_id for m in card.armed_members] == [SID]
    assert card.armed_members[0].verdict is Verdict.CORE_ENTRY
    assert card.watch_members == []


def test_confirmation_only_member_is_a_watch_not_armed():
    """A member with a breakout but NO conviction is surfaced in the watch tier ("moving, no conviction
    yet") — visible, not actionable, never the headline, not in the armed/ranked set."""
    events = [
        insider_event(security_id=SID),  # SID: conviction + confirmation -> armed
        breakout_event(grade=Grade.CORE, security_id=SID),
        breakout_event(grade=Grade.CORE, security_id=_SID2),  # SID2: confirmation only -> watch
    ]
    card = assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG)
    assert card.armed_security_id == SID
    assert [m.security_id for m in card.armed_members] == [SID]
    assert [m.security_id for m in card.watch_members] == [_SID2]
    watch = card.watch_members[0]
    assert watch.verdict is None and watch.conviction_grade is None
    assert watch.confirmation_grade is Grade.CORE  # the breakout is real; just no conviction yet


def test_theme_conviction_arms_a_member_as_a_capped_starter():
    """M5b: a theme conviction (flip — the fallback) + the member's OWN volume-backed (core) breakout arms
    it as a disciplined STARTER, flagged ``theme_armed``; capped at the starter ceiling, with ``exit_by``
    = the theme's horizon. (The volume-backed eligibility gate lives in the broadcast; by the time the
    assembler sees a THEME_CONVICTION event the member is already eligible — here it behaves like any flip
    conviction, which is the point: no assembler branch on the theme kind.)"""
    card = assemble_call(
        make_thesis(),
        [theme_conviction_event(liveness=365), breakout_event(grade=Grade.CORE, score=0.9)],
        ASOF,
        DEFAULT_CONFIG,
    )
    assert card.state is State.ARMED and card.armed_security_id == SID
    assert (
        card.verdict is Verdict.STARTER_ENTRY
    )  # flip conviction + long horizon -> starter (hold-worthy)
    assert card.conviction_grade is Grade.FLIP and card.entry_grade is Grade.FLIP
    assert card.confidence is not None and card.confidence <= DEFAULT_CONFIG.starter_confidence_cap
    member = card.armed_members[0]
    assert member.security_id == SID and member.theme_armed is True
    assert member.exit_by == ASOF + timedelta(
        days=365
    )  # the theme horizon drives the member's hold clock


def test_own_conviction_outranks_theme_armed_within_a_band():
    """M5b ranking (Q1): within the same freshness band + grade, a name armed on its OWN conviction
    outranks one armed only on the THEME fallback. Both are FRESH flip starters with equal runway; the own
    name carries a WEAKER conviction_score (0.3) than the theme name (0.9), proving ``is_own`` dominates
    the conviction_score tiebreak (it sits earlier in the sort tuple) — not grade, runway, or score.
    """
    events = [
        catalyst_event(
            grade=Grade.FLIP, liveness=400, score=0.3, security_id=SID
        ),  # OWN, weak score
        breakout_event(grade=Grade.CORE, security_id=SID),
        theme_conviction_event(liveness=400, score=0.9, security_id=_SID2),  # THEME, strong score
        breakout_event(grade=Grade.CORE, security_id=_SID2),
    ]
    card = assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG)
    assert card.state is State.ARMED
    assert card.armed_security_id == SID  # own conviction headlines over the theme-armed name
    assert [m.security_id for m in card.armed_members] == [SID, _SID2]
    own, theme = card.armed_members
    assert own.theme_armed is False and theme.theme_armed is True
    assert own.entry_grade is Grade.FLIP and theme.entry_grade is Grade.FLIP  # same band + grade


def test_fresh_theme_starter_outranks_a_lapsing_own_core():
    """M5b ranking (Q1 = freshness wins): the freshness BAND stays primary, so a FRESH theme-armed starter
    headlines over a LAPSING own-conviction core — consistent with the M5a OKLO-over-lapsing-LEU doctrine.
    ``is_own`` is only a within-band tiebreak; it does NOT lift a lapsing own-core over a fresh theme name.
    """
    events = [
        catalyst_event(
            grade=Grade.CORE, liveness=20, security_id=SID
        ),  # OWN core but LAPSING (~20d)
        breakout_event(grade=Grade.CORE, security_id=SID),
        theme_conviction_event(liveness=400, security_id=_SID2),  # THEME starter but FRESH (~400d)
        breakout_event(grade=Grade.CORE, security_id=_SID2),
    ]
    card = assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG)
    assert (
        card.armed_security_id == _SID2
    )  # the fresh theme starter headlines over the lapsing own core
    assert [m.security_id for m in card.armed_members] == [_SID2, SID]
    fresh_theme, lapsing_core = card.armed_members
    assert fresh_theme.theme_armed is True and fresh_theme.lapsing is False
    assert lapsing_core.theme_armed is False and lapsing_core.lapsing is True
    assert lapsing_core.conviction_grade is Grade.CORE  # the lapsing own core is preserved at #2


def test_theme_armed_member_is_withheld_on_severe_risk():
    """The risk veto applies to a theme-armed member exactly like any other (per-member, keyed on
    security_id — no exemption): a severe risk on the name withholds its arm. The member is neither armed
    nor (since it now carries conviction) in the watch tier — it's risk-withheld on timing."""
    card = assemble_call(
        make_thesis(),
        [theme_conviction_event(), breakout_event(grade=Grade.CORE), dilution_event()],
        ASOF,
        DEFAULT_CONFIG,
    )
    assert card.state is State.WARMING  # the single member is risk-blocked -> nothing actionable
    assert card.armed_members == [] and card.armed_security_id is None
    assert (
        card.watch_members == []
    )  # it has conviction (the theme), so it isn't a watch member either


def test_assembler_has_no_magic_number_thresholds():
    """Lightweight lexical guard (the behavioral test above is the real one): no float literals in the assembler."""
    src = Path(assemble_call.__code__.co_filename).read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in src.splitlines())
    floats = re.findall(r"\b\d+\.\d+\b", code)
    assert floats == [], f"thresholds must come from CallConfig; found float literals: {floats}"
