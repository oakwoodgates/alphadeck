from __future__ import annotations

from datetime import date, datetime, timezone

from domain.config import DEFAULT_CONFIG
from domain.enums import State
from pipeline.seed import (
    UNH_THESIS_ID,
    seed_hims,
    seed_leu_catalyst,
    seed_nuclear,
    seed_nuclear_catalyst,
    seed_nuclear_theme_conviction,
    seed_unh,
)
from replay.export import export_snapshot
from replay.harness import replay_thesis
from replay.pit import connect_mirror
from replay.run import run
from repositories import thesis_repo

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_START = date(2025, 4, 1)
_END = date(2026, 6, 30)

_METRIC_NAMES = {
    "arm_timing_forward_return",
    "early_vs_armed_delta",
    "grade_confidence_calibration",
    "name_selection_lift",
    "false_arm_rate",
    "withheld_arm_counterfactual",
    "exit_by_vs_rollover",
}


def _seed_all(db):
    seed_hims(db)
    seed_unh(db)
    seed_nuclear(db)
    seed_nuclear_catalyst(db)
    seed_leu_catalyst(db)
    seed_nuclear_theme_conviction(db)
    db.commit()


def test_run_is_reproducible(db, tmp_path):
    """Determinism pin (req 4): same (snapshot, pin, window, cfg) -> value-identical timeline + scores. We
    compare the returned metrics by value (the honest, achievable form of 'byte-reproducible')."""
    _seed_all(db)
    m1 = run(db, start=_START, end=_END, pin=_PIN, out_dir=tmp_path / "r1")
    m2 = run(db, start=_START, end=_END, pin=_PIN, out_dir=tmp_path / "r2")
    assert m1.model_dump() == m2.model_dump()
    assert m1.n_episodes > 0


def test_cfg_is_swept_not_hardcoded(db, tmp_path):
    """Req 3: cfg flows through the harness. Raising the breakout bar to an unreachable 50%/10d means UNH
    never confirms -> never arms, so the timeline differs from the default — proving cfg isn't hardcoded.
    """
    seed_unh(db)
    db.commit()
    export_snapshot(db, tmp_path)
    con = connect_mirror(tmp_path)
    try:
        thesis = thesis_repo.get(db, UNH_THESIS_ID)
        base = replay_thesis(con, thesis, start=_START, end=_END, known_at=_PIN, cfg=DEFAULT_CONFIG)
        strict = DEFAULT_CONFIG.model_copy(update={"breakout_min_return": 0.50})
        tweaked = replay_thesis(con, thesis, start=_START, end=_END, known_at=_PIN, cfg=strict)
        base_armed = [s.asof for s in base if s.state is State.ARMED]
        tweaked_armed = [s.asof for s in tweaked if s.state is State.ARMED]
        assert base_armed, "UNH arms under the default config"
        assert base_armed != tweaked_armed
    finally:
        con.close()


def test_metrics_carry_n_and_insufficient_flags(db, tmp_path):
    """The seven claim-tied metrics are all present, each carrying n + insufficient_n + its claim; at the
    seed's scale calibration is honestly flagged insufficient (instrument, not a claim)."""
    _seed_all(db)
    m = run(db, start=_START, end=_END, pin=_PIN, out_dir=tmp_path / "r")
    assert {mr.name for mr in m.metrics} == _METRIC_NAMES
    for mr in m.metrics:
        assert mr.n >= 0 and isinstance(mr.insufficient_n, bool) and mr.claim
    cal = next(mr for mr in m.metrics if mr.name == "grade_confidence_calibration")
    assert cal.insufficient_n  # the seed cannot establish calibration — must say so
