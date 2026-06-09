from __future__ import annotations

import uuid
from datetime import date

from domain.enums import Grade, Verdict
from replay.metrics import MIN_N, compute_metrics
from replay.schema import Outcome

_METRIC_NAMES = {
    "arm_timing_forward_return",
    "early_vs_armed_delta",
    "grade_confidence_calibration",
    "name_selection_lift",
    "false_arm_rate",
    "withheld_arm_counterfactual",
    "exit_by_vs_rollover",
}


def _out(grade, fwd, *, is_headline=True, thesis=None, warm=None, close_reason="window_end"):
    return Outcome(
        thesis_id=thesis or uuid.uuid4(),
        security_id=uuid.uuid4(),
        is_headline=is_headline,
        entry_grade=grade,
        verdict=Verdict.CORE_ENTRY if grade is Grade.CORE else Verdict.STARTER_ENTRY,
        confidence=0.6,
        close_reason=close_reason,
        arm_date=date(2025, 8, 1),
        exit_by=date(2025, 11, 1),
        entry_close=100.0,
        exit_close=100.0 * (1 + fwd),
        forward_return=fwd,
        warm_return=warm,
        peak_return=max(fwd, 0.0),
        exit_vs_peak_days=5,
    )


def test_seven_metrics_present_and_calibration_insufficient_at_small_n():
    """All seven metrics present; calibration is flagged insufficient because each grade bucket is below
    MIN_N — even though the TOTAL n (4) could otherwise look adequate (the per-bucket rule)."""
    outs = [
        _out(Grade.CORE, 0.10),
        _out(Grade.FLIP, -0.20),
        _out(Grade.CORE, 0.05),
        _out(Grade.FLIP, 0.0),
    ]
    m = compute_metrics(outs)
    assert {mr.name for mr in m.metrics} == _METRIC_NAMES
    cal = next(mr for mr in m.metrics if mr.name == "grade_confidence_calibration")
    assert cal.insufficient_n  # 2 core + 2 flip, each < MIN_N


def test_calibration_sufficient_only_when_each_bucket_meets_min_n():
    """With >= MIN_N in EACH graded bucket, calibration is no longer insufficient and reports monotonicity
    (core median above flip median)."""
    outs = [_out(Grade.CORE, 0.10) for _ in range(MIN_N)] + [
        _out(Grade.FLIP, -0.10) for _ in range(MIN_N)
    ]
    m = compute_metrics(outs)
    cal = next(mr for mr in m.metrics if mr.name == "grade_confidence_calibration")
    assert not cal.insufficient_n
    assert cal.summary["monotonic"] == 1.0  # core (0.10) >= flip (-0.10)


def test_false_arm_excludes_managing_and_name_selection_is_relative():
    """false_arm excludes operator-takeover (managing) episodes; name-selection compares the headline to
    the rest of the SAME thesis's basket (relative — isolates selection from theme beta)."""
    tid = uuid.uuid4()
    outs = [
        _out(Grade.CORE, -0.10, close_reason="managing"),  # excluded from false-arm
        _out(Grade.FLIP, -0.20, close_reason="window_end"),  # the one judged arm (adverse)
        _out(Grade.FLIP, 0.30, is_headline=True, thesis=tid),  # headline of thesis tid
        _out(Grade.FLIP, 0.10, is_headline=False, thesis=tid),  # a peer in tid
    ]
    m = compute_metrics(outs)
    fa = next(mr for mr in m.metrics if mr.name == "false_arm_rate")
    assert fa.summary["total"] == 3.0  # the managing episode is excluded from the judged set
    assert fa.summary["adverse"] == 1.0
    ns = next(mr for mr in m.metrics if mr.name == "name_selection_lift")
    assert ns.detail and ns.detail[0]["lift"] == 0.20  # headline 0.30 - peer 0.10
