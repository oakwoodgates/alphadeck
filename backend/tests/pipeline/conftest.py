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


@pytest.fixture(autouse=True)
def _onboard_runs_home(tmp_path, monkeypatch):
    """X1 — the same redirect for the staged onboarding job's run artifacts (``pipeline/onboarding``): a batch
    writes ``data/onboard_runs/<started>.json``, which a test must never litter (or read back)."""
    from pipeline import onboarding

    d = tmp_path / "onboard_runs_home"
    monkeypatch.setattr(onboarding, "_DEFAULT_RUNS_DIR", d)
    return d


@pytest.fixture(autouse=True)
def _extraction_runs_home(request, tmp_path, monkeypatch):
    """V2 W3: the same redirect for the background extraction's run artifacts (``pipeline/extraction_run_log``):
    the onboarding job's new-company step writes ``data/extraction_runs/<started>.json``, which a test must
    never litter (or read back). The run log's own tests are left alone: they pin the real default and pass
    their own tmp directory everywhere else."""
    from pipeline import extraction_run_log

    if request.module.__name__.rsplit(".", 1)[-1] == "test_extraction_run_log":
        return None
    d = tmp_path / "extraction_runs_home"
    monkeypatch.setattr(extraction_run_log, "EXTRACTION_RUNS_DIR", d)
    return d
