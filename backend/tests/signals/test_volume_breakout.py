from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from uuid import uuid4

from domain.config import DEFAULT_CONFIG
from domain.enums import Grade, Kind
from ingest.prices.eod_loader import parse_yahoo_chart
from signals import volume_breakout

SID = uuid4()
# Real HIMS EOD (the M3 target), pulled live and committed for a reproducible, offline real-data test.
_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"
_BARS = parse_yahoo_chart(
    json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
)


def _through(d: date) -> list[dict]:
    return [b for b in _BARS if b["d"] <= d]


def test_breakout_off_before_confirmation():
    # 2026-05-28: Wells's open-market buy is known, but the momentum thrust hasn't confirmed (ret10d ~5%)
    assert (
        volume_breakout.score(_through(date(2026, 5, 28)), SID, date(2026, 5, 28), DEFAULT_CONFIG)
        is None
    )


def test_breakout_fires_momentum_only_on_hims():
    # 2026-06-01: the price breakout fires, but HIMS ran ~0.9x volume -> MOMENTUM-ONLY (flip), not volume-backed
    ev = volume_breakout.score(_through(date(2026, 6, 1)), SID, date(2026, 6, 1), DEFAULT_CONFIG)
    assert ev is not None and ev.fired and ev.kind is Kind.TECHNICAL_BREAKOUT
    assert ev.grade is Grade.FLIP
    assert ev.provenance[0].detail["volume_backed"] is False


def test_not_enough_bars():
    assert volume_breakout.score(_BARS[:3], SID, date(2026, 1, 1), DEFAULT_CONFIG) is None


def test_breakout_stays_reported_through_consolidation():
    # 2026-06-03 is a consolidation bar (not a new high), but the 06-01 breakout is still inside its
    # alpha-liveness window -> the detector reports it stamped with its OWN bar date (06-01), no flicker.
    ev = volume_breakout.score(_through(date(2026, 6, 3)), SID, date(2026, 6, 3), DEFAULT_CONFIG)
    assert ev is not None and ev.fired
    assert ev.asof == date(2026, 6, 1)  # the breakout's bar date, not the query asof
    assert ev.provenance[0].ref == f"price:{SID}:2026-06-01"


def test_decayed_breakout_is_not_resurrected():
    # The prior breakout was 2026-04-20; by 2026-05-15 it is well past its alpha-liveness window, so the
    # freshness-bounded scan does not resurrect it -> None (no stale confirmation).
    assert (
        volume_breakout.score(_through(date(2026, 5, 15)), SID, date(2026, 5, 15), DEFAULT_CONFIG)
        is None
    )


# --- §3.1 follow-through / hold quality (R13) — the SCORE input that rejects the false breakout ---------
# The follow-through is a SCORE input only: the GRADE stays volume-based (grading a weak-follow-through
# breakout down to flip is deferred — it re-verdicts the UNH flagship arc + theme-arm goldens). So the
# tests below prove the SCORE ordering while the grade is UNCHANGED.

_C = DEFAULT_CONFIG
_END = date(2026, 6, 20)
# A flat base: base_high (8-day closing high) = 100, base volume average = 1000.
_BASE = [(100.0, 1000.0, 100.0, 100.0)] * 12
# A volume-backed breakout (vol 2000 = 2x): close near the top of a tight range (strength ~0.83) ...
_STRONG = (110.0, 2000.0, 112.0, 100.0)
# ... vs the SAME breakout close sitting near the BOTTOM of a wide range (strength ~0.06 — a weak close).
_WEAK = (110.0, 2000.0, 140.0, 108.0)
_HOLD = (105.0, 1000.0, 105.0, 104.0)  # next bar holds above the 100 level (and is not a new high)
_FAIL = (99.0, 1000.0, 99.0, 98.0)  # next bar loses the breakout level


def _seq(seq: list[tuple[float, float | None, float, float]]) -> list[dict]:
    start = _END - timedelta(days=len(seq) - 1)
    return [
        {"d": start + timedelta(days=i), "close": c, "volume": v, "high": hi, "low": lo}
        for i, (c, v, hi, lo) in enumerate(seq)
    ]


def _breakout(brk, nxt):
    bars = _seq(_BASE + [brk] + ([nxt] if nxt else []))
    return volume_breakout.score(bars, SID, _END, _C)


def test_a_top_of_range_breakout_that_holds_scores_above_a_weak_or_failed_one():
    """R13 acceptance: a clean breakout (strong close + next-day hold) scores ABOVE both a weak-close
    breakout and a failed-hold breakout — that lower score IS the rejected false breakout. The GRADE stays
    volume-based on all three (score-only; grading the false ones down to flip is deferred)."""
    clean = _breakout(_STRONG, _HOLD)
    weak_close = _breakout(_WEAK, _HOLD)
    failed_hold = _breakout(_STRONG, _FAIL)
    assert clean.score > weak_close.score  # a weak close is de-rated in the score
    assert clean.score > failed_hold.score  # a failed next-day hold is de-rated in the score
    # all three are volume-backed -> the GRADE is unchanged (the follow-through is a score input only)
    assert clean.grade is weak_close.grade is failed_hold.grade is Grade.CORE


def test_a_still_fresh_breakout_with_no_next_bar_is_not_penalized():
    """A breakout that is the LAST bar has no next bar yet — the hold is UNKNOWN, not failed, so it is
    neither penalized nor rewarded (a fresh breakout keeps its full clean score)."""
    fresh = _breakout(_STRONG, None)
    held = _breakout(_STRONG, _HOLD)
    assert fresh.grade is Grade.CORE
    assert fresh.provenance[0].detail["next_bar_held"] is None
    assert fresh.score == held.score  # the unknown hold neither helps nor hurts


def test_the_two_follow_through_penalties_stack_in_the_score():
    """A weak close AND a failed hold together score BELOW either penalty alone — the multiplicative
    stack (the false breakout scored at its lowest)."""
    weak_only = _breakout(_WEAK, _HOLD)
    fail_only = _breakout(_STRONG, _FAIL)
    both = _breakout(_WEAK, _FAIL)
    assert both.score < weak_only.score and both.score < fail_only.score


def test_follow_through_details_ride_the_provenance():
    """Show-the-work (#6): the close strength + the next-day hold are recorded on the trigger's
    provenance so a score-derated breakout can be explained. The grade stays volume-based (score-only).
    """
    weak = _breakout(_WEAK, _FAIL)
    clean = _breakout(_STRONG, _HOLD)
    d = weak.provenance[0].detail
    assert d["volume_backed"] is True  # the volume WAS there ...
    assert d["close_strength"] < _C.breakout_close_strength_min  # ... but the close was weak ...
    assert d["next_bar_held"] is False  # ... and the next bar lost the level ...
    assert weak.score < clean.score  # ... so the SCORE is de-rated ...
    assert weak.grade is Grade.CORE  # ... but the grade stays volume-based (grade-down is deferred)


def test_hims_breakout_is_a_strong_close_that_holds_so_follow_through_is_inert():
    """On the real HIMS golden fixture the 06-01 breakout closed strong (top-of-range ~0.83) and 06-02
    held above the level, so the follow-through factor is 1.0 — the score is numerically unchanged and
    the grade stays FLIP (HIMS is momentum-only on VOLUME, never a follow-through demotion)."""
    ev = volume_breakout.score(_through(date(2026, 6, 2)), SID, date(2026, 6, 2), DEFAULT_CONFIG)
    assert ev is not None and ev.asof == date(
        2026, 6, 1
    )  # still the 06-01 breakout (06-02 held, no re-anchor)
    d = ev.provenance[0].detail
    assert d["close_strength"] >= DEFAULT_CONFIG.breakout_close_strength_min  # strong close
    assert d["next_bar_held"] is True  # 06-02 held above the level
    assert (
        d["volume_backed"] is False and ev.grade is Grade.FLIP
    )  # unchanged: momentum-only on volume
