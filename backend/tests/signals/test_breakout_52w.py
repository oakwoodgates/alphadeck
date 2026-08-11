from __future__ import annotations

from datetime import date, timedelta

from calls.assembler import assemble_call
from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind, Role, State, Verdict
from signals import breakout_52w
from tests.calls.factories import ASOF, SID, breakout_event, insider_event, make_thesis

_C = DEFAULT_CONFIG


def _bars(
    closes: list[float],
    vols: list[float | None],
    end: date = ASOF,
) -> list[dict]:
    """Ascending-date EOD bars (last bar = the as-of bar) carrying only close + volume — the two fields
    the 52-week detector reads. Consecutive calendar days ending at ``end``."""
    start = end - timedelta(days=len(closes) - 1)
    return [
        {"d": start + timedelta(days=i), "close": c, "volume": v}
        for i, (c, v) in enumerate(zip(closes, vols))
    ]


def _base_then(
    tail_closes: list[float],
    tail_vols: list[float | None],
    base_n: int = 246,
    base_close: float = 100.0,
    base_vol: float = 1000.0,
    end: date = ASOF,
) -> list[dict]:
    """``base_n`` flat base bars (default 246 > the 245 min-base-bars gate) then the given tail bars."""
    closes = [base_close] * base_n + tail_closes
    vols: list[float | None] = [base_vol] * base_n + tail_vols
    return _bars(closes, vols, end=end)


def test_fresh_52w_high_on_volume_fires_core_with_a_long_arm():
    """A fresh 52-week CLOSING high on RVOL >= 1.5x the ~50-day average -> a CORE structural breakout,
    fire-dated at the bar, carrying the 45-day (vs the 8-day tool's 10-day) liveness window (R9)."""
    ev = breakout_52w.score(_base_then([110.0], [3000.0]), SID, ASOF, _C)
    assert ev is not None and ev.fired
    assert (
        ev.role is Role.ENTRY_TRIGGER and ev.kind is Kind.TECHNICAL_BREAKOUT
    )  # a VARIANT, no new Kind
    assert ev.grade is Grade.CORE  # structural — fixed core, never volume-graded down
    assert ev.alpha_liveness_days == _C.breakout_52w_alpha_liveness_days == 45
    assert ev.asof == ASOF  # fire-date-anchored at the breakout bar
    d = ev.provenance[0].detail
    assert d["high_52w"] == 100.0 and d["rvol"] == 3.0 and d["close"] == 110.0
    assert ev.provenance[0].ref == f"price:{SID}:{ASOF.isoformat()}"


def test_not_at_a_52w_high_does_not_fire():
    """Below the prior 52-week closing high -> no structural breakout, even on heavy volume."""
    assert breakout_52w.score(_base_then([99.0], [5000.0]), SID, ASOF, _C) is None


def test_fresh_high_on_thin_volume_does_not_fire():
    """A fresh 52-week high WITHOUT the volume gate (RVOL < 1.5x) declines — a year-high on no
    participation is not the structural breakout (R9)."""
    assert breakout_52w.score(_base_then([110.0], [1000.0]), SID, ASOF, _C) is None  # RVOL ~1.0


def test_a_year_high_with_no_volume_on_the_bar_declines_not_fabricates():
    """No volume on the breakout bar -> the volume gate can't be met -> None, never a fabricated
    volume-backed breakout (#6/#9)."""
    assert breakout_52w.score(_base_then([110.0], [None]), SID, ASOF, _C) is None


def test_too_few_bars_cannot_claim_a_52_week_high():
    """Below the min-base-bars gate the detector refuses to assert a '52-week high' on less than ~a year
    of tape (#9) — even a huge up-bar on volume returns None."""
    short = _bars([100.0] * 100 + [200.0], [1000.0] * 100 + [9000.0])
    assert breakout_52w.score(short, SID, ASOF, _C) is None


def test_a_decayed_52w_breakout_is_not_resurrected():
    """The freshness floor mirrors the assembler liveness: a 52-week breakout well past its 45-day window,
    with nothing fresh since, is not resurrected (no stale confirmation)."""
    # 246 flat base, the 110 breakout, then 60 flat bars @105 (never a new high) -> the breakout sits ~60d
    # before asof, outside the 45d liveness window.
    bars = _base_then([110.0] + [105.0] * 60, [3000.0] + [1000.0] * 60)
    assert breakout_52w.score(bars, SID, ASOF, _C) is None


def test_breakout_stays_reported_through_a_consolidation_within_liveness():
    """A fresh 52-week high then a few consolidation bars (no new high) still inside the 45-day window ->
    the detector keeps reporting the breakout stamped with ITS OWN bar date, no flicker."""
    end = ASOF
    bars = _base_then([110.0, 108.0, 109.0], [3000.0, 1200.0, 1100.0], end=end)
    ev = breakout_52w.score(bars, SID, end, _C)
    assert ev is not None and ev.fired
    breakout_day = end - timedelta(days=2)  # the 110 bar is two days before the last (asof) bar
    assert ev.asof == breakout_day
    assert ev.provenance[0].detail["close"] == 110.0


# --- Composition (end-to-end): the strongest confirmation wins its grade (no assembler change) ----------


def _core_52w_on(sec, asof: date = ASOF):
    """A REAL core 52-week breakout event from the detector, co-located on ``sec`` at ``asof``."""
    ev = breakout_52w.score(_base_then([110.0], [3000.0], end=asof), sec, asof, DEFAULT_CONFIG)
    assert ev is not None and ev.grade is Grade.CORE
    return ev


def test_core_52w_breakout_arms_a_core_entry():
    """A core conviction + a core 52-week breakout co-located on the same name -> Armed CORE_ENTRY (R9).
    The confirmation grade is core and the entry grade is core."""
    card = assemble_call(
        make_thesis(), [insider_event(grade=Grade.CORE), _core_52w_on(SID)], ASOF, DEFAULT_CONFIG
    )
    assert card.state is State.ARMED
    assert card.verdict is Verdict.CORE_ENTRY
    assert card.confirmation_grade is Grade.CORE and card.entry_grade is Grade.CORE
    assert card.armed_security_id == SID
    # the long structural arm window rides through: arm_until = the breakout fire date + 45d liveness
    assert card.arm_until == ASOF + timedelta(days=DEFAULT_CONFIG.breakout_52w_alpha_liveness_days)


def test_a_cofiring_flip_8day_breakout_does_not_drag_a_core_52w_down_to_flip():
    """The crux: with BOTH a core 52-week breakout and a flip 8-day breakout co-firing on the same name,
    the confirmation grade is the STRONGEST (call_grade = max), so the co-firing flip never drags the
    core 52-week breakout down to a starter. Still Armed CORE_ENTRY, not momentum-only."""
    events = [
        insider_event(grade=Grade.CORE),
        _core_52w_on(SID),  # the structural core breakout
        breakout_event(
            grade=Grade.FLIP, score=0.45
        ),  # a co-firing momentum-only 8-day breakout on SID
    ]
    card = assemble_call(make_thesis(), events, ASOF, DEFAULT_CONFIG)
    assert card.state is State.ARMED
    assert card.verdict is Verdict.CORE_ENTRY
    assert card.confirmation_grade is Grade.CORE and card.entry_grade is Grade.CORE
    assert (
        "momentum-only" not in card.counter_case.lower()
    )  # not caveated as an unconfirmed breakout
    # both breakout rows surface on the card (honest — two distinct signals co-fired on the name)
    breakout_grades = {t.grade for t in card.triggers_fired if t.kind is Kind.TECHNICAL_BREAKOUT}
    assert breakout_grades == {Grade.FLIP, Grade.CORE}


def test_only_the_flip_8day_without_the_core_52w_is_a_starter():
    """The mirror that proves the core 52-week breakout is what lifts the entry: the SAME core conviction
    with ONLY the flip 8-day breakout (no 52-week) arms as a momentum-only STARTER, not a core entry.
    """
    card = assemble_call(
        make_thesis(),
        [insider_event(grade=Grade.CORE), breakout_event(grade=Grade.FLIP, score=0.45)],
        ASOF,
        DEFAULT_CONFIG,
    )
    assert card.state is State.ARMED
    assert card.verdict is Verdict.STARTER_ENTRY  # the flip confirmation caps it at a starter
    assert card.confirmation_grade is Grade.FLIP and card.entry_grade is Grade.FLIP
