from __future__ import annotations

import json
from datetime import date, datetime, timezone

import pytest

# The run CLI drives the replay engine, which is the optional .[replay] extra. The pure identity tests
# beside this one need neither, so the skip is scoped to this module rather than the package.
pytest.importorskip("duckdb")
pytest.importorskip("pyarrow")

from backtest import artifact, store  # noqa: E402
from backtest import manifest as mf  # noqa: E402
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

    for name in (
        "manifest.json",
        "episodes.parquet",
        "outcomes.parquet",
        "metrics.json",
        # B6 — the two SERVING copies. `episodes.json` exists because reading the Parquet needs pyarrow,
        # which the lean api image does not carry; `ledger.json` is the per-thesis drill-down in the
        # Scoreboard's own vocabulary. Both are written by the real path, so this is where their absence
        # would be caught rather than in a hand-built fixture.
        "episodes.json",
        "ledger.json",
    ):
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

    # ...and the ledger the surface serves holds THESE episodes, with the identity resolved by the run
    ledger = artifact.read_ledger(outcome.path)
    assert ledger is not None
    assert sum(len(t.episodes) for t in ledger.snapshot.theses) == m.n_episodes
    assert ledger.snapshot.theses, "the seeded thesis should appear even if it armed nothing"
    if m.n_episodes:
        sid = next(e.episode.security_id for t in ledger.snapshot.theses for e in t.episodes)
        assert ledger.securities[str(sid)].ticker == "UNH"


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


def test_the_cli_offers_both_clocks_and_leaves_the_default_to_execute(capsys):
    """CW opened ``public`` — B2 built the export mode and this runner now reaches it. The effective
    default is still ``record``: the public clock excludes every fact table with no declared disclosure
    column, so it is a deliberate choice a run makes, never one it drifts into. Anything else is refused,
    because a run that accepted an unknown clock would claim an axis nothing implements.

    The PARSER's default is ``None``, though, and that is load-bearing rather than cosmetic (S1): a parser
    default of "record" cannot be told apart from the operator typing it, so the flag had to be dropped
    when ``--mirror-dir`` was supplied — and dropping it meant a clock that DISAGREED with the mirror was
    silently ignored instead of refused. The default lives in ``execute`` and nowhere else.

    The wiring itself — what the flag does to the mirror, to ``known_at_mode`` and to a SUPPLIED mirror
    that disagrees — lives in ``tests/backtest/test_clock_wiring.py``."""
    parser = build_parser()
    base = ["--start", "2025-01-01", "--end", "2025-02-01"]
    assert parser.parse_args(base).clock is None
    assert parser.parse_args([*base, "--clock", "public"]).clock == "public"
    with pytest.raises(SystemExit):
        parser.parse_args([*base, "--clock", "wall"])
    assert "invalid choice" in capsys.readouterr().err
