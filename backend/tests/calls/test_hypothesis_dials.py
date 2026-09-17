"""The four DORMANT backtest hypothesis dials (H1 legs 1-3, H3).

Two different claims are proved here.

**DORMANT — the defaults ARE today.** Each default is pinned by name, and a card built with all four dials
spelled out explicitly is byte-identical to one built with ``DEFAULT_CONFIG``, so a typo'd default cannot
pass as "unchanged". (The broader byte-identity claim is carried by the rest of the suite continuing to
pass unmodified: this slice edits no golden and no existing assertion.)

**They do what they say.** One behavioral test per dial, driven by the REAL detector rather than a
hand-built ``SignalEvent`` that merely resembles one: every revenue-acceleration event below comes out of
``signals.revenue_acceleration.score``, so it carries its true detector name, its true ``xbrl`` provenance
and its true liveness. That matters because two of these dials are keyed on exactly those fields — a
fabricated event would let the classification pass for the wrong reason.

The revenue series is the module's own canonical eight-quarter ladder (the one
``tests/signals/test_revenue_acceleration.py`` and ``tests/replay/test_revenue_acceleration_replay.py``
both pin), shifted forward two years so the inflection is live at ``factories.ASOF``. The GROWTH SHAPE —
which is the detector's contract — is unchanged; only the calendar moves, so the companion factory events
(all dated ``ASOF``) co-locate on the same member instead of firing two years apart.
"""

from __future__ import annotations

import inspect
import json
import re
from datetime import date, timedelta

import pytest

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash
from domain.enums import Grade, State
from domain.signal import Provenance
from signals import breakdown, registered_detectors
from signals import revenue_acceleration as ra
from signals.conviction_source import COMPUTED_CONVICTION_SOURCES, is_computed_conviction
from tests.calls.factories import (
    ASOF,
    SID,
    breakout_event,
    catalyst_event,
    insider_event,
    make_thesis,
)

# The canonical ladder, +2 years: YoY 0.30 -> 0.25 (accel -0.05) -> 0.35 (accel +0.10); the flip lands on
# the 2025-12-31 quarter, filed 2026-02-15 — 107 days before ASOF, inside the 180d liveness.
_FILED = {
    date(2024, 3, 31): date(2024, 5, 16),
    date(2024, 6, 30): date(2024, 8, 15),
    date(2024, 9, 30): date(2024, 11, 14),
    date(2024, 12, 31): date(2025, 2, 15),
    date(2025, 3, 31): date(2025, 5, 15),
    date(2025, 6, 30): date(2025, 8, 14),
    date(2025, 9, 30): date(2025, 11, 13),
    date(2025, 12, 31): date(2026, 2, 15),  # THE inflection quarter's filing
}
_REV = {
    date(2024, 3, 31): 100.0,
    date(2024, 6, 30): 100.0,
    date(2024, 9, 30): 100.0,
    date(2024, 12, 31): 100.0,
    date(2025, 3, 31): 120.0,  # g=0.20
    date(2025, 6, 30): 130.0,  # g=0.30
    date(2025, 9, 30): 125.0,  # g=0.25 -> accel -0.05
    date(2025, 12, 31): 135.0,  # g=0.35 -> accel +0.10  => FLIP
}


def _series() -> list[dict]:
    return [
        {
            "metric_key": "revenue",
            "period_end": pe,
            "value": v,
            "valid_from": _FILED[pe],
            "accession": f"acc-{pe.isoformat()}",
            "fiscal_period": "Q4" if pe.month == 12 else "Q?",
            "fiscal_year": pe.year,
        }
        for pe, v in _REV.items()
    ]


def _revenue_accel(cfg: CallConfig):
    """The REAL detector's output on the canonical ladder — real name, real xbrl provenance, real liveness."""
    ev = ra.score(_series(), SID, ASOF, cfg)
    assert (
        ev is not None and ev.fired
    ), "the canonical ladder must fire; the fixture IS the contract"
    return ev


def _explicit_defaults() -> CallConfig:
    return DEFAULT_CONFIG.model_copy(
        update={
            "revenue_accel_grade": Grade.CORE,
            "revenue_accel_key": "conviction",
            "computed_conviction_requires_core_confirmation": False,
            "breakdown_dearm_scope": "all",
        }
    )


def _bars(closes: list[float], end: date = ASOF) -> list[dict]:
    """``tests/signals/test_breakdown.py``'s helper, verbatim: ascending consecutive-day EOD bars ending
    at ``end``, close only — the one field both breakdown detectors read."""
    start = end - timedelta(days=len(closes) - 1)
    return [{"d": start + timedelta(days=i), "close": c} for i, c in enumerate(closes)]


# The two ladders ``tests/signals/test_breakdown.py`` already pins, reused verbatim so these tests exercise
# the DIAL and never re-litigate the price rule. A tape invented here would prove nothing about the dial if
# it failed to trip the detector at all — which is exactly what a first draft of this file did.
_CORE_BREAK = [100.0] * 205 + [120.0] * 10 + [90.0]  # base -> run above the 200d -> break below it
_FLIP_BREAK = [100.0] * 20 + [115.0] + [95.0]  # base 100 -> breakout 115 -> fall back below it


class _Pit:
    """A price view over one fixed tape. ``detect_core`` / ``detect_flip`` differ only in the lookback they
    request, and both ladders are short enough to serve whole, so one tape per test is enough."""

    def __init__(self, closes: list[float]) -> None:
        self._bars = _bars(closes)

    def price_history(self, security_id, lookback_days=None):
        return list(self._bars)


# --- DORMANT: the defaults ARE today ---------------------------------------------------------------


def test_the_four_dials_default_to_todays_behavior():
    """The DORMANT contract, by name. Flipping a default is a LIVE behavior change and must fail here
    rather than ship quietly inside a backtest slice."""
    assert DEFAULT_CONFIG.revenue_accel_grade is Grade.CORE
    assert DEFAULT_CONFIG.revenue_accel_key == "conviction"
    assert DEFAULT_CONFIG.computed_conviction_requires_core_confirmation is False
    assert DEFAULT_CONFIG.breakdown_dearm_scope == "all"


def test_explicit_defaults_produce_a_byte_identical_card():
    events = [_revenue_accel(DEFAULT_CONFIG), breakout_event()]
    a = assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG)
    b = assemble_call(make_thesis(), events, ASOF, _explicit_defaults())
    assert a.model_dump_json() == b.model_dump_json()


def test_the_computed_screen_still_arms_on_its_own_today():
    """The baseline the three H1 legs are measured against: today, a revenue re-acceleration plus a
    MOMENTUM-ONLY breakout arms at core conviction. Every assertion below is a departure from this.
    """
    card = assemble_call(
        make_thesis(),
        [_revenue_accel(DEFAULT_CONFIG), breakout_event(grade=Grade.FLIP)],
        ASOF,
        DEFAULT_CONFIG,
    )
    assert card.state is State.ARMED
    assert card.conviction_grade is Grade.CORE


# --- H1 leg 1: revenue_accel_grade ------------------------------------------------------------------


def test_the_grade_dial_demotes_the_detector_and_its_score():
    cfg = DEFAULT_CONFIG.model_copy(update={"revenue_accel_grade": Grade.FLIP})
    assert _revenue_accel(cfg).grade is Grade.FLIP
    assert _revenue_accel(cfg).score == pytest.approx(
        0.5
    )  # catalyst_conviction's pair, not a new number
    assert _revenue_accel(DEFAULT_CONFIG).grade is Grade.CORE
    assert _revenue_accel(DEFAULT_CONFIG).score == pytest.approx(0.9)


def test_demoting_the_grade_demotes_the_calls_conviction_grade():
    cfg = DEFAULT_CONFIG.model_copy(update={"revenue_accel_grade": Grade.FLIP})
    card = assemble_call(make_thesis(), [_revenue_accel(cfg), breakout_event()], ASOF, cfg)
    assert card.state is State.ARMED  # it still arms — the GRADE is the hypothesis, not a gate
    assert card.conviction_grade is Grade.FLIP


def test_demoting_the_grade_leaves_the_horizon_alone():
    """R8 decouples this detector's liveness from its grade, which is what keeps H1 leg 1 and H5's
    horizon sweep separable: demoting to flip must NOT shorten exit_by the way an insider flip does.
    """
    cfg = DEFAULT_CONFIG.model_copy(update={"revenue_accel_grade": Grade.FLIP})
    assert (
        _revenue_accel(cfg).alpha_liveness_days
        == _revenue_accel(DEFAULT_CONFIG).alpha_liveness_days
        == DEFAULT_CONFIG.revenue_accel_alpha_liveness_days
    )


# --- H1 leg 2: revenue_accel_key --------------------------------------------------------------------


def test_reclassifying_as_confirmation_stops_it_arming_alone():
    """The hypothesis: a computed screen supplies Key 2, not Key 1. With a breakout as the only other
    trigger the member now holds TWO confirmations and no conviction — so it cannot arm."""
    cfg = DEFAULT_CONFIG.model_copy(update={"revenue_accel_key": "confirmation"})
    card = assemble_call(make_thesis(), [_revenue_accel(cfg), breakout_event()], ASOF, cfg)
    assert card.state is not State.ARMED
    assert card.conviction_grade is None  # nothing turns Key 1 any more


def test_reclassified_it_still_confirms_a_real_conviction():
    """...and it is not merely discarded: paired with an insider buy it supplies the confirmation key."""
    cfg = DEFAULT_CONFIG.model_copy(update={"revenue_accel_key": "confirmation"})
    card = assemble_call(make_thesis(), [insider_event(), _revenue_accel(cfg)], ASOF, cfg)
    assert card.state is State.ARMED
    assert card.confirmation_grade is Grade.CORE


def test_the_other_catalyst_detectors_are_NOT_moved():
    """Exactly why the dial is keyed on the DETECTOR and not on ``Kind.CATALYST``: an operator-ratified
    catalyst is a different claim about the world and must keep turning Key 1."""
    cfg = DEFAULT_CONFIG.model_copy(update={"revenue_accel_key": "confirmation"})
    card = assemble_call(make_thesis(), [catalyst_event(), breakout_event()], ASOF, cfg)
    assert card.state is State.ARMED
    assert card.conviction_grade is not None


# --- H1 leg 3: computed_conviction_requires_core_confirmation ---------------------------------------


def test_a_computed_conviction_needs_a_volume_backed_confirmation():
    cfg = DEFAULT_CONFIG.model_copy(update={"computed_conviction_requires_core_confirmation": True})
    momentum_only = assemble_call(
        make_thesis(), [_revenue_accel(cfg), breakout_event(grade=Grade.FLIP)], ASOF, cfg
    )
    assert momentum_only.state is not State.ARMED  # a flip breakout is no longer enough
    volume_backed = assemble_call(
        make_thesis(), [_revenue_accel(cfg), breakout_event(grade=Grade.CORE)], ASOF, cfg
    )
    assert volume_backed.state is State.ARMED


def test_a_named_actor_conviction_is_untouched_by_the_requirement():
    """Ratified and name-specific convictions unchanged: an insider cluster still arms on a momentum-only
    breakout, because the hypothesis is about what a SCREEN is worth, not a blanket higher bar."""
    cfg = DEFAULT_CONFIG.model_copy(update={"computed_conviction_requires_core_confirmation": True})
    card = assemble_call(
        make_thesis(), [insider_event(), breakout_event(grade=Grade.FLIP)], ASOF, cfg
    )
    assert card.state is State.ARMED


def test_a_computed_conviction_alongside_a_named_one_is_untouched():
    cfg = DEFAULT_CONFIG.model_copy(update={"computed_conviction_requires_core_confirmation": True})
    card = assemble_call(
        make_thesis(),
        [_revenue_accel(cfg), insider_event(), breakout_event(grade=Grade.FLIP)],
        ASOF,
        cfg,
    )
    assert card.state is State.ARMED


def test_the_member_menu_and_the_headline_agree_under_the_requirement():
    """The requirement is applied at the thesis level AND in ``_member_call``. Applied to only one, the
    per-member menu would list an armed name the headline says is not armed."""
    cfg = DEFAULT_CONFIG.model_copy(update={"computed_conviction_requires_core_confirmation": True})
    card = assemble_call(
        make_thesis(), [_revenue_accel(cfg), breakout_event(grade=Grade.FLIP)], ASOF, cfg
    )
    assert card.state is not State.ARMED
    assert card.armed_members == []


# --- the "computed" classification ------------------------------------------------------------------


def test_the_real_detector_event_classifies_as_computed():
    assert is_computed_conviction(_revenue_accel(DEFAULT_CONFIG)) is True


def test_a_named_actor_event_does_not_classify_as_computed():
    assert is_computed_conviction(insider_event()) is False
    assert is_computed_conviction(catalyst_event()) is False


def test_a_mixed_provenance_event_is_not_computed():
    """ "Every source computed", not "any" — the conservative direction for a classifier whose only
    consumer makes arming HARDER. A screen corroborated by a filing is not purely computed."""
    ev = _revenue_accel(DEFAULT_CONFIG)
    mixed = ev.model_copy(
        update={"provenance": [*ev.provenance, Provenance(source="form4", ref="acc-x")]}
    )
    assert is_computed_conviction(mixed) is False


def test_computed_sources_is_the_single_declaration():
    assert COMPUTED_CONVICTION_SOURCES == frozenset({"xbrl"})


def test_every_conviction_emitting_detector_is_classified():
    """FLAG-E's registry sweep, in the ``signals/horizons.py`` discipline ("a reader with no declaration
    fails a TEST"): a NEW detector that can supply a Key-1 conviction must be deliberately classified as
    computed or not, or this fails and names it.

    The emitter set is read off the REGISTRY plus each module's source, so it cannot fall behind the code;
    the classification is spelled out, because deriving it from the thing it checks would be vacuous.
    """
    classification = {
        "insider_conviction": False,  # form4 — a named insider spent their own money
        "catalyst_conviction": False,  # an operator-ratified fact_catalyst row
        "corporate_catalyst": False,  # 8-k — the issuer disclosed a specific event
        "activist_stake": False,  # 13d — a named holder crossed 5% with intent
        "revenue_acceleration": True,  # xbrl — a screen over reported quarterly revenue
    }
    conviction_names = {k.name for k in DEFAULT_CONFIG.conviction_kinds}
    # Match the EMISSION site (`kind=Kind.X`) with an exact name capture, never a substring of the module's
    # prose: `signals/insider_sell.py` discusses `Kind.INSIDER` in a comment explaining that it is NOT one,
    # and a looser scan duly reported the risk detector as a conviction emitter.
    emitters = {
        d.name
        for d in registered_detectors()
        if set(re.findall(r"\bkind=Kind\.(\w+)", inspect.getsource(inspect.getmodule(d.detect))))
        & conviction_names
    }
    unclassified = emitters - set(classification)
    assert not unclassified, (
        f"unclassified conviction-emitting detector(s): {sorted(unclassified)} — declare each one in "
        f"signals/conviction_source.py's COMPUTED_CONVICTION_SOURCES and in this map"
    )
    assert classification["revenue_acceleration"] is True
    assert set(classification) & emitters  # the sweep actually found something


# --- H3: breakdown_dearm_scope ----------------------------------------------------------------------


def test_scope_core_only_suppresses_the_flip_breakdown():
    """``core_only`` no-ops ``detect_flip`` so the event never ENTERS the stream — no de-arm, and no
    counter-case or confidence haircut either (the master switch's own rule)."""
    core_only = DEFAULT_CONFIG.model_copy(update={"breakdown_dearm_scope": "core_only"})
    pit = _Pit(_FLIP_BREAK)
    assert breakdown.detect_flip(pit, SID, ASOF, DEFAULT_CONFIG) is not None  # it DOES fire today
    assert breakdown.detect_flip(pit, SID, ASOF, core_only) is None


def test_scope_never_means_off():
    """Two switches, two questions: ``breakdown_dearm_enabled`` is WHETHER, ``breakdown_dearm_scope`` is
    WHICH. ``core_only`` must still emit the CORE de-arm, or the two dials would overlap."""
    core_only = DEFAULT_CONFIG.model_copy(update={"breakdown_dearm_scope": "core_only"})
    off = DEFAULT_CONFIG.model_copy(update={"breakdown_dearm_enabled": False})
    core_pit, flip_pit = _Pit(_CORE_BREAK), _Pit(_FLIP_BREAK)
    assert breakdown.detect_core(core_pit, SID, ASOF, core_only) is not None
    assert breakdown.detect_core(core_pit, SID, ASOF, off) is None
    assert breakdown.detect_flip(flip_pit, SID, ASOF, off) is None


def test_scope_leaves_the_core_de_arm_byte_identical():
    core_only = DEFAULT_CONFIG.model_copy(update={"breakdown_dearm_scope": "core_only"})
    pit = _Pit(_CORE_BREAK)
    assert breakdown.detect_core(pit, SID, ASOF, DEFAULT_CONFIG) == breakdown.detect_core(
        pit, SID, ASOF, core_only
    )


# --- the dial partition (investigation (b)) ----------------------------------------------------------


def test_the_new_dials_are_classified_detector_vs_assembler():
    """Investigation (b) partitions every ``CallConfig`` dial into DETECTOR (changes the event stream) vs
    ASSEMBLER (the stream is identical, only the card moves) — the memoization key the backtest's event
    cache is built on. That partition's own test lands with B5b; this pins the four new dials by
    OBSERVATION now, so B5b inherits an answer that was measured rather than declared."""
    # DETECTOR: the event stream differs
    flip = DEFAULT_CONFIG.model_copy(update={"revenue_accel_grade": Grade.FLIP})
    assert _revenue_accel(flip) != _revenue_accel(DEFAULT_CONFIG)
    core_only = DEFAULT_CONFIG.model_copy(update={"breakdown_dearm_scope": "core_only"})
    pit = _Pit(_FLIP_BREAK)
    assert breakdown.detect_flip(pit, SID, ASOF, core_only) != breakdown.detect_flip(
        pit, SID, ASOF, DEFAULT_CONFIG
    )
    # ASSEMBLER: the event is unchanged, the card moves
    for update, events in (
        (
            {"revenue_accel_key": "confirmation"},
            [_revenue_accel(DEFAULT_CONFIG), breakout_event()],
        ),
        (
            {"computed_conviction_requires_core_confirmation": True},
            [_revenue_accel(DEFAULT_CONFIG), breakout_event(grade=Grade.FLIP)],
        ),
    ):
        cfg = DEFAULT_CONFIG.model_copy(update=update)
        assert _revenue_accel(cfg) == _revenue_accel(DEFAULT_CONFIG), update
        assert (
            assemble_call(make_thesis(), events, ASOF, cfg).state
            != assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG).state
        ), update


# --- reachable from an overlay, and visible in the fingerprint --------------------------------------


def test_every_new_dial_survives_the_json_round_trip():
    """They exist to be flipped from a backtest overlay, which merges JSON into ``model_dump()`` and
    re-validates (``backtest.config_overlay``)."""
    merged = DEFAULT_CONFIG.model_dump()
    merged.update(
        {
            "revenue_accel_grade": "flip",
            "revenue_accel_key": "confirmation",
            "computed_conviction_requires_core_confirmation": True,
            "breakdown_dearm_scope": "core_only",
        }
    )
    cfg = CallConfig.model_validate(merged)
    assert cfg.revenue_accel_grade is Grade.FLIP
    assert cfg.revenue_accel_key == "confirmation"
    assert cfg.computed_conviction_requires_core_confirmation is True
    assert cfg.breakdown_dearm_scope == "core_only"
    json.dumps(cfg.model_dump(mode="json"))  # the canonical walk must handle every new field


@pytest.mark.parametrize(
    "dial,bad",
    [
        ("revenue_accel_key", "conviction_maybe"),
        ("breakdown_dearm_scope", "flip_only"),
        ("revenue_accel_grade", "enormous"),
    ],
)
def test_an_invalid_dial_value_is_rejected(dial, bad):
    """The Literal / enum types are the guard an overlay leans on — a typo must fail before the run."""
    merged = DEFAULT_CONFIG.model_dump()
    merged[dial] = bad
    with pytest.raises(Exception):
        CallConfig.model_validate(merged)


def test_the_new_dials_move_the_config_fingerprint():
    """Each dial is a real input to the call, so each must enter ``config_hash`` — otherwise two runs with
    different dials would be indistinguishable in the registry, which is the one thing run identity exists
    to prevent."""
    base = config_hash(DEFAULT_CONFIG)
    for update in (
        {"revenue_accel_grade": Grade.FLIP},
        {"revenue_accel_key": "confirmation"},
        {"computed_conviction_requires_core_confirmation": True},
        {"breakdown_dearm_scope": "core_only"},
    ):
        assert config_hash(DEFAULT_CONFIG.model_copy(update=update)) != base, update
