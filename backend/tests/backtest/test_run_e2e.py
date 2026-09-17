from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

# The run CLI drives the replay engine, which is the optional .[replay] extra. The pure identity tests
# beside this one need neither, so the skip is scoped to this module rather than the package.
pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from backtest import manifest as mf  # noqa: E402
from backtest import store  # noqa: E402
from backtest.config_overlay import apply_overlay  # noqa: E402
from backtest.run import build_parser, execute, main  # noqa: E402
from domain.config import DEFAULT_CONFIG, CallConfig, config_hash  # noqa: E402
from pipeline.seed import seed_unh  # noqa: E402

_PIN = datetime(2027, 1, 1, tzinfo=timezone.utc)
_START, _END = date(2025, 4, 1), date(2026, 6, 1)


@pytest.mark.slow  # one UNH sweep through the full export -> replay -> score -> metrics path
@pytest.mark.timeout(300)
def test_a_run_leaves_a_complete_addressable_artifact(db, tmp_path):
    """The B1 claim in one test: a run is a directory that can be handed to someone else."""
    seed_unh(db)
    db.commit()
    outcome = execute(db, start=_START, end=_END, pin=_PIN, root=tmp_path)

    for name in ("manifest.json", "episodes.parquet", "outcomes.parquet", "metrics.json"):
        assert (outcome.path / name).is_file(), f"{name} missing from the run directory"
    assert list(outcome.path.glob("*.parquet")), "the frozen mirror should sit with the run"

    m = mf.read_manifest(outcome.path)
    assert m is not None
    assert m.run_id == outcome.run_id
    assert m.window_start == _START and m.window_end == _END
    assert m.clock == "record" and m.known_at_mode == "pin"
    assert m.pin == _PIN.isoformat()
    assert m.config_hash == config_hash(DEFAULT_CONFIG)
    assert mf.verify_config_hash(m) is True
    assert m.overlay_diff == {}  # a bare run is the production dials
    assert m.timings["export_s"] >= 0 and "replay_s" in m.timings
    assert m.mirror.hash and len(m.mirror.hash) == 64


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_the_manifest_names_the_theses_and_whose_roster_they_replayed_on(db, tmp_path):
    """``roster_source`` is the honest half: ``basket_snapshot`` history begins 2026-09-15, so a 2025
    window replays on TODAY's basket and the manifest has to say so rather than imply a point-in-time
    roster it never read."""
    seed_unh(db)
    db.commit()
    m = execute(db, start=_START, end=_END, pin=_PIN, root=tmp_path).manifest
    assert m.theses, "the seeded thesis should appear"
    entry = next(t for t in m.theses if t.total_days > 0)
    assert entry.roster_source in ("snapshot", "live_fallback")
    assert entry.roster_hash and len(entry.roster_hash) == 64
    assert entry.basket_size >= 1
    assert m.n_theses == len(m.theses)


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_a_run_is_registered_and_names_the_dials_it_moved(db, tmp_path):
    seed_unh(db)
    db.commit()
    cfg = apply_overlay({"insider_core_alpha_liveness_days": 90})
    outcome = execute(
        db,
        start=_START,
        end=_END,
        pin=_PIN,
        cfg=cfg,
        hypothesis="H5: the horizon is the lever",
        decision_rule="plateau, not argmax",
        root=tmp_path,
    )
    rows = store.list_runs(tmp_path)
    assert [r.run_id for r in rows] == [outcome.run_id]
    row = rows[0]
    assert row.dials_moved == ["insider_core_alpha_liveness_days"]
    assert row.hypothesis == "H5: the horizon is the lever"
    assert row.decision_rule == "plateau, not argmax"
    assert row.config_hash == config_hash(cfg)
    assert outcome.manifest.overlay_diff["insider_core_alpha_liveness_days"]["run"] == 90


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_two_runs_are_two_addressable_trials_not_an_overwrite(db, tmp_path):
    """A re-run must never overwrite: a run id in a PR description identifies ONE set of numbers."""
    seed_unh(db)
    db.commit()
    a = execute(
        db,
        start=_START,
        end=_END,
        pin=_PIN,
        root=tmp_path,
        now=datetime(2026, 9, 18, 4, 0, 0, tzinfo=timezone.utc),
    )
    b = execute(
        db,
        start=_START,
        end=_END,
        pin=_PIN,
        root=tmp_path,
        now=datetime(2026, 9, 18, 4, 0, 1, tzinfo=timezone.utc),
    )
    assert a.run_id != b.run_id
    assert a.path != b.path and a.path.is_dir() and b.path.is_dir()
    assert len(store.list_runs(tmp_path)) == 2


@pytest.mark.slow
@pytest.mark.timeout(300)
def test_the_stored_config_rebuilds_the_exact_run_config(db, tmp_path):
    """Reproducibility, end to end: the manifest alone rebuilds the CallConfig the run was threaded with."""
    seed_unh(db)
    db.commit()
    cfg = apply_overlay({"insider_flip_alpha_liveness_days": 30, "breakout_min_return": 0.2})
    m = execute(db, start=_START, end=_END, pin=_PIN, cfg=cfg, root=tmp_path).manifest
    rebuilt = CallConfig.model_validate(m.config)
    assert config_hash(rebuilt) == config_hash(cfg)
    assert json.loads(m.config_canonical_json) == m.config


# --- the CLI's own guards (no DB, no replay) -------------------------------------------------------


def test_the_cli_refuses_an_overlay_without_a_pre_registration(capsys):
    """A dial moved without a pre-registered hypothesis and decision rule is not a measurement. The guard
    is at the CLI because that is the only place it can be enforced before the run costs anything.
    """
    code = main(["--start", "2025-09-01", "--end", "2026-09-14", "--config", "whatever.json"])
    assert code == 2
    assert "pre-registered" in capsys.readouterr().err


def test_the_cli_reports_a_bad_overlay_without_running(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"not_a_dial": 1}), encoding="utf-8")
    code = main(
        [
            "--start",
            "2025-09-01",
            "--end",
            "2026-09-14",
            "--config",
            str(bad),
            "--hypothesis",
            "h",
            "--decision-rule",
            "d",
        ]
    )
    assert code == 2
    assert "not_a_dial" in capsys.readouterr().err


def test_the_cli_only_offers_the_record_clock_until_B2():
    """``public`` arrives with B2. Offering it now would let a run claim a clock it does not implement."""
    parser = build_parser()
    assert parser.parse_args(["--start", "2025-01-01", "--end", "2025-02-01"]).clock == "record"
    with pytest.raises(SystemExit):
        parser.parse_args(["--start", "2025-01-01", "--end", "2025-02-01", "--clock", "public"])
