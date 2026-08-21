from __future__ import annotations

import argparse
from datetime import date, datetime, timezone

import pytest

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
from replay.run import add_switch_args, lab_config, run
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


# --- the lab's switch posture: option A, the lab INHERITS production (operator, 2026-08-20) --------
# Before this, main() built its cfg with an unconditional override per switch
# (`args.x or _env_on(...)`), so a MISSING flag forced False. Once five of the six switches flipped ON
# in DEFAULT_CONFIG (2026-08-15 -> -08-20) a bare `python -m replay.run` was silently backtesting a
# stack with five live detectors disabled — the lab no longer measured production. These tests pin the
# three-way precedence (force-OFF > force-ON > inherit) per switch, enumerated EXPLICITLY rather than
# read off run._LAB_SWITCHES, so deleting or mis-wiring a table row fails here instead of passing
# vacuously. Pure — no DB, no replay; `env={...}` is always passed so the developer's real environment
# can never leak in.

_LAB_SWITCH_FLAGS = {
    "breakdown_dearm_enabled": ("--breakdown-dearm", "ALPHADECK_BREAKDOWN_DEARM"),
    "insider_sell_enabled": ("--insider-sell", "ALPHADECK_INSIDER_SELL"),
    "corporate_catalyst_enabled": ("--corporate-catalyst", "ALPHADECK_CORPORATE_CATALYST"),
    "corporate_risk_enabled": ("--corporate-risk", "ALPHADECK_CORPORATE_RISK"),
    "share_creep_enabled": ("--share-creep", "ALPHADECK_SHARE_CREEP"),
    "activist_stake_enabled": ("--activist-stake", "ALPHADECK_ACTIVIST_STAKE"),
}
_LAB_SWITCH_FIELDS = tuple(_LAB_SWITCH_FLAGS)
_ALL_OFF = DEFAULT_CONFIG.model_copy(update=dict.fromkeys(_LAB_SWITCH_FIELDS, False))


def _lab_cfg(argv=(), *, base=DEFAULT_CONFIG, env=None):
    p = argparse.ArgumentParser()
    add_switch_args(p)
    return lab_config(p.parse_args(list(argv)), base=base, env={} if env is None else env)


def _off_flag(field: str) -> str:
    return _LAB_SWITCH_FLAGS[field][0].replace("--", "--no-", 1)


def test_lab_config_bare_run_inherits_the_production_defaults():
    """Option A: no flags -> every switch equals the LIVE default, so the lab measures what prod runs."""
    cfg = _lab_cfg()
    for field in _LAB_SWITCH_FIELDS:
        assert getattr(cfg, field) == getattr(DEFAULT_CONFIG, field), field
    # Vacuity guard: at least one switch is ON live, so "inherit" is observably DIFFERENT from the old
    # unconditional override (which forced every switch False on a bare run). Without this, the loop
    # above would still pass on an all-False config and prove nothing.
    assert any(getattr(DEFAULT_CONFIG, f) for f in _LAB_SWITCH_FIELDS)


@pytest.mark.parametrize("field", _LAB_SWITCH_FIELDS)
def test_lab_config_force_off_beats_the_default_and_both_force_on_legs(field):
    """`--no-<stem>` is the off-leg of an off-vs-on measure: False even when the live default is True,
    and it outranks BOTH force-on legs (the flag and the env var)."""
    flag, env_var = _LAB_SWITCH_FLAGS[field]
    assert getattr(_lab_cfg([_off_flag(field)]), field) is False
    assert getattr(_lab_cfg([_off_flag(field), flag], env={env_var: "1"}), field) is False


@pytest.mark.parametrize("field", _LAB_SWITCH_FIELDS)
def test_lab_config_force_on_flag_and_env_var_beat_a_false_default(field):
    """Against an all-off base (so the assertion cannot be satisfied by inheritance): the flag turns it
    on, the env var turns it on, and with neither, inherit correctly leaves it OFF."""
    flag, env_var = _LAB_SWITCH_FLAGS[field]
    assert getattr(_lab_cfg([flag], base=_ALL_OFF), field) is True
    assert getattr(_lab_cfg(base=_ALL_OFF, env={env_var: "1"}), field) is True
    assert getattr(_lab_cfg(base=_ALL_OFF), field) is False  # inherit, both directions


@pytest.mark.parametrize("field", _LAB_SWITCH_FIELDS)
def test_lab_config_each_flag_touches_only_its_own_switch(field):
    """Catches a mis-wired table row (a duplicated env var / config field): forcing ONE switch off
    leaves the other five at their inherited live defaults."""
    cfg = _lab_cfg([_off_flag(field)])
    for other in _LAB_SWITCH_FIELDS:
        if other != field:
            assert getattr(cfg, other) == getattr(DEFAULT_CONFIG, other), other


def test_lab_config_leaves_non_switch_dials_untouched():
    """The lab cfg differs from DEFAULT_CONFIG in the switch fields ONLY — it is not a place where
    calibration dials quietly diverge from production."""
    cfg = _lab_cfg([_off_flag("insider_sell_enabled")])
    changed = {
        f for f in DEFAULT_CONFIG.model_dump() if getattr(cfg, f) != getattr(DEFAULT_CONFIG, f)
    }
    assert changed == {"insider_sell_enabled"}


def test_run_is_reproducible(db, tmp_path):
    """Determinism pin (req 4): same (snapshot, pin, window, cfg) -> value-identical timeline + scores. We
    compare the returned metrics by value (the honest, achievable form of 'byte-reproducible'). UNH-only
    (its arc produces episodes) keeps the two full runs cheap — the property is per-run determinism.
    """
    seed_unh(db)
    db.commit()
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
