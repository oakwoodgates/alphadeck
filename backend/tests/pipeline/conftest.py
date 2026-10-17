from __future__ import annotations

import pytest

from pipeline import cron_run_log


@pytest.fixture(autouse=True)
def _cron_runs_home(tmp_path, monkeypatch):
    """Redirect the cron run-of-record home (``pipeline/cron_run_log``) to a per-test tmp dir for EVERY
    pipeline test — the ``tests/app`` conftest's ``cron_runs_dir`` twin. Since M1 a daily pass writes a
    START artifact the moment it holds the run lock (``write_run_start``), before any test-stubbed writer
    runs, so without this a pass-running test would litter the real gitignored ``data/cron_runs`` — and a
    pass's stale-tape / name-error baselines would read whatever a dev machine has there. A test that pins
    its own dir (``monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", ...)``) still wins: it runs after
    this fixture."""
    d = tmp_path / "cron_runs_home"
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", d)
    return d
