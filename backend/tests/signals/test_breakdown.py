"""§2.5 (core) + §3.3 (flip) — the breakdown detectors' bar math (R10/R11).

Pure, fixed-timestamp tests over crafted EOD bars. The grade-aware VETO these feed is proven at the
assembler in ``tests/calls/test_breakdown_dearm.py``; here we prove each detector fires (and, as
importantly, DECLINES) on the right price shape and carries the right ``dearm_grade``.

The pure ``core_score`` / ``flip_score`` are UNGATED (the math is testable independent of the v0 master
switch); the pit-reading ``detect_core`` / ``detect_flip`` no-op unless ``breakdown_dearm_enabled`` is ON
(the last test proves that gate), so the live app + goldens emit nothing by default.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from calls.assembler import assemble_call
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role, State
from signals import breakdown
from tests.calls.factories import ASOF, SID, breakout_event, insider_event, make_thesis

_C = DEFAULT_CONFIG
_ON = DEFAULT_CONFIG.model_copy(update={"breakdown_dearm_enabled": True})  # the de-arm turned ON


def _bars(closes: list[float], end: date = ASOF) -> list[dict]:
    """Ascending-date EOD bars (last bar = the as-of bar), close only — the field both breakdown
    detectors read. Consecutive calendar days ending at ``end``."""
    start = end - timedelta(days=len(closes) - 1)
    return [{"d": start + timedelta(days=i), "close": c} for i, c in enumerate(closes)]


# --- §2.5 CORE breakdown: a close below the 200-day base (R10) --------------------------------------


def test_core_break_below_200d_fires_targeting_core():
    """A close below the 200d SMA, reached by crossing DOWN from above -> a core-breakdown RISK signal
    carrying dearm_grade=CORE, ungraded (a risk carries no call-strength grade)."""
    closes = [100.0] * 205 + [120.0] * 10 + [90.0]  # base -> run above the 200d -> break below it
    ev = breakdown.core_score(_bars(closes), SID, ASOF, _C)
    assert ev is not None and ev.fired
    assert ev.role is Role.RISK_SIGNAL and ev.kind is Kind.BREAKDOWN
    assert ev.grade is None  # a risk is ungraded...
    assert ev.dearm_grade is Grade.CORE  # ...but carries WHICH grade it de-arms (the property)
    assert ev.asof == ASOF  # the cross IS the last bar here, so the break date == asof
    d = ev.provenance[0].detail
    assert d["close"] == 90.0 and d["sma_window"] == 200 and d["sma"] > 90.0
    assert "not a sell" in ev.label.lower()  # advisory phrasing (#4/#5)


def test_core_break_is_fire_dated_at_the_downcross_not_the_query_asof():
    """The break is fire-dated at the DOWNWARD-cross bar (when the base broke), NOT the latest bar — this
    is what lets the assembler require the break to POST-DATE the arm (the coordinator's fix)."""
    closes = (
        [100.0] * 205 + [120.0] * 10 + [90.0] * 4
    )  # crosses down 4 bars before asof, stays below
    bars = _bars(closes)
    ev = breakdown.core_score(bars, SID, ASOF, _C)
    assert ev is not None
    assert ev.asof == bars[-4]["d"] and ev.asof < ASOF  # the down-cross bar, not the query asof


def test_a_20pct_dip_that_holds_the_200d_does_not_fire():
    """R10 — NEVER the first pullback: a ~20% dip off a high that still HOLDS the 200d SMA de-arms nothing
    (a run from a 50 base to 120, a dip to 96 — 96 is far above the ~54 SMA)."""
    closes = [50.0] * 200 + [120.0] * 10 + [96.0]
    assert breakdown.core_score(_bars(closes), SID, ASOF, _C) is None


def test_chronic_downtrend_never_above_200d_does_not_fire():
    """The regime gate (#7): a name never above its 200d SMA (a steady decliner) is not an established-
    uptrend break — no downward cross, so no core-breakdown, however far below it sits."""
    closes = [
        300.0 - i for i in range(210)
    ]  # monotonic decline: price always below the trailing mean
    assert breakdown.core_score(_bars(closes), SID, ASOF, _C) is None


def test_core_break_declines_without_a_years_tape():
    """#9: fewer than ~a year of bars -> we can't honestly compute a 200d SMA -> decline (never fabricate)."""
    assert breakdown.core_score(_bars([100.0] * 120 + [50.0]), SID, ASOF, _C) is None


def test_core_break_holds_while_price_holds_the_line():
    """A close exactly AT / above the 200d line is not a break (only strictly below de-arms)."""
    assert breakdown.core_score(_bars([100.0] * 215), SID, ASOF, _C) is None  # flat AT the line


# --- §3.3 FLIP breakdown: a close back below the 8-day breakout base (R11) --------------------------


def test_flip_fall_back_below_the_8day_base_fires_targeting_flip():
    """A recent 8-day breakout given back — the latest close falls below the base it cleared -> a
    flip-breakdown RISK signal carrying dearm_grade=FLIP."""
    closes = [100.0] * 20 + [115.0] + [95.0]  # base 100 -> breakout 115 -> fall back below the base
    ev = breakdown.flip_score(_bars(closes), SID, ASOF, _C)
    assert ev is not None and ev.fired
    assert ev.role is Role.RISK_SIGNAL and ev.kind is Kind.BREAKDOWN and ev.grade is None
    assert ev.dearm_grade is Grade.FLIP
    d = ev.provenance[0].detail
    assert d["close"] == 95.0 and d["base_high"] == 100.0  # the base the breakout cleared


def test_flip_holding_above_the_base_does_not_fire():
    """Still above the 8-day base it cleared -> a consolidation, not a breakdown (never the first dip)."""
    closes = [100.0] * 20 + [115.0] + [110.0]  # 110 > the 100 base
    assert breakdown.flip_score(_bars(closes), SID, ASOF, _C) is None


def test_flip_break_needs_a_recent_breakout_to_break_from():
    """No breakout in the window (flat tape) -> nothing to fall back from -> None (a chronic drifter
    below some level is not a flip breakdown)."""
    assert breakdown.flip_score(_bars([100.0] * 30), SID, ASOF, _C) is None


def test_flip_break_declines_once_the_breakout_ages_out_of_its_window():
    """The breakout is scoped to the flip liveness window (10d): once it ages out, the flip-breakdown
    stops firing even though price is below the old base — the flip arm has lapsed on its own clock.
    """
    # base -> breakout -> 15 bars below the base, so the breakout sits ~15d before asof (past the 10d window)
    closes = [100.0] * 20 + [115.0] + [95.0] * 15
    assert breakdown.flip_score(_bars(closes), SID, ASOF, _C) is None


# --- composition: the REAL core detector output de-arms a real core hold ----------------------------


def test_real_core_breakdown_event_dearms_a_core_hold_end_to_end():
    """End to end: the core detector's own event (dearm_grade=CORE, fire-dated at the down-cross) fed into
    the assembler alongside a core conviction + core confirmation that armed EARLIER de-arms the core hold —
    the real detector-to-veto path, with the post-date rule satisfied (the break comes after the arm).
    """
    closes = (
        [100.0] * 205 + [120.0] * 10 + [90.0]
    )  # the down-cross (and break) is the last bar = ASOF
    break_ev = breakdown.core_score(_bars(closes), SID, ASOF, _C)
    assert break_ev is not None and break_ev.dearm_grade is Grade.CORE and break_ev.asof == ASOF

    # the arm formed 3 days BEFORE the break (both keys still live at ASOF): core insider + core breakout
    prior = ASOF - timedelta(days=3)
    conv = insider_event().model_copy(update={"asof": prior})
    conf = breakout_event(grade=Grade.CORE).model_copy(update={"asof": prior})
    armed = assemble_call(make_thesis(), [conv, conf], ASOF, _ON)
    assert armed.state is State.ARMED and armed.entry_grade is Grade.CORE

    dearmed = assemble_call(make_thesis(), [conv, conf, break_ev], ASOF, _ON)
    assert (
        dearmed.state is State.WARMING
    )  # the real detector output de-armed the core hold (post-arm)
    assert dearmed.armed_security_id is None


# --- the v0 MASTER SWITCH: the pit-reading detectors no-op until the flag is ON ---------------------


class _PricePIT:
    """A minimal point-in-time view returning fixed bars — enough for the gated ``detect_*`` wrappers."""

    def __init__(self, bars: list[dict[str, Any]]) -> None:
        self._bars = bars
        self.asof = ASOF
        self.known_at = datetime(2027, 1, 1, tzinfo=timezone.utc)
        self.tenant_id = DEFAULT_TENANT_ID

    def price_history(self, security_id: UUID, lookback_days: int | None = None) -> list[dict]:
        return self._bars


def test_the_flag_gates_the_detectors_off_by_default():
    """``detect_core`` / ``detect_flip`` emit NOTHING with the default config (flag OFF) even on a genuine
    break, and fire with the flag ON — so the live app + goldens see no breakdown while the lab can turn it
    on. (The pure ``core_score`` / ``flip_score`` above stay ungated — the math is flag-independent.)
    """
    core_pit = _PricePIT(_bars([100.0] * 205 + [120.0] * 10 + [90.0]))
    assert breakdown.detect_core(core_pit, SID, ASOF, _C) is None  # flag OFF -> no-op
    assert breakdown.detect_core(core_pit, SID, ASOF, _ON) is not None  # flag ON -> fires

    flip_pit = _PricePIT(_bars([100.0] * 20 + [115.0] + [95.0]))
    assert breakdown.detect_flip(flip_pit, SID, ASOF, _C) is None  # flag OFF -> no-op
    assert breakdown.detect_flip(flip_pit, SID, ASOF, _ON) is not None  # flag ON -> fires
