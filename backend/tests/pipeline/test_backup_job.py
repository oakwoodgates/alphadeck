"""The in-process SINGLE-SLOT snapshot job registry (no DB, no HTTP, no thread) — run jobs INLINE via
the executor seam and assert the state machine: done carries the result, a fault is a VISIBLE failed
message, the slot ALWAYS frees (a failed dump never bricks the trigger), the single-slot 409 guard, and
the reaper on the job's OWN backup_job_* TTLs (a peer of test_daily_job.py)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline import backup_job


@pytest.fixture(autouse=True)
def _clean():
    backup_job.reset_state()
    yield
    backup_job.reset_state()


def _inline(job, run):
    """Executor that runs the job synchronously (the prod default spawns a daemon thread)."""
    backup_job._run_job(job, run)


def _noop(job, run):
    """Executor that never runs the job — it stays 'running' so the single slot stays claimed."""


def _settings(running: float = 600.0, finished: float = 3600.0) -> SimpleNamespace:
    return SimpleNamespace(backup_job_running_ttl_s=running, backup_job_finished_ttl_s=finished)


def test_start_runs_and_stores_result():
    sentinel = (
        object()
    )  # result is Any — the registry never inspects it (the route shapes the wire)
    jid = backup_job.start_backup_job(lambda: sentinel, executor=_inline)
    job = backup_job.get_job(jid)
    assert job is not None
    assert job.status == "done" and job.result is sentinel and job.error is None


def test_exception_is_a_visible_failed_job():
    def boom():
        raise RuntimeError("kaboom")

    job = backup_job.get_job(backup_job.start_backup_job(boom, executor=_inline))
    assert job.status == "failed" and "backup failed" in job.error and "kaboom" in job.error


def test_slot_released_on_success_allows_a_new_run():
    backup_job.start_backup_job(lambda: object(), executor=_inline)  # finishes inline -> slot freed
    jid2 = backup_job.start_backup_job(lambda: object(), executor=_inline)  # no BackupRunInFlight
    assert backup_job.get_job(jid2).status == "done"


def test_slot_released_on_failure_allows_a_new_run():
    def boom():
        raise RuntimeError("x")

    backup_job.start_backup_job(boom, executor=_inline)  # failed -> slot STILL freed (finally)
    jid2 = backup_job.start_backup_job(lambda: object(), executor=_inline)
    assert backup_job.get_job(jid2).status == "done"


def test_second_start_while_running_raises_in_flight():
    # THE single-slot guard: one snapshot at a time, process-wide — a double-click must never stack a
    # second pg_dump
    backup_job.start_backup_job(lambda: object(), executor=_noop)  # stays running, slot held
    with pytest.raises(backup_job.BackupRunInFlight):
        backup_job.start_backup_job(lambda: object(), executor=_inline)


def test_reaper_fails_a_stale_running_job_and_frees_the_slot(monkeypatch):
    monkeypatch.setattr(
        backup_job, "get_settings", lambda: _settings(running=-1.0)
    )  # any running -> stale
    jid = backup_job.start_backup_job(lambda: object(), executor=_noop)  # left running
    job = backup_job.get_job(jid)  # the poll triggers the reaper
    assert job.status == "failed" and "timed out" in job.error  # the abandoned-thread backstop
    # the slot was freed -> a new run can start (the trigger isn't bricked)
    jid2 = backup_job.start_backup_job(lambda: object(), executor=_inline)
    assert backup_job.get_job(jid2).status == "done"


def test_reaper_drops_a_finished_job_past_its_ttl(monkeypatch):
    monkeypatch.setattr(backup_job, "get_settings", lambda: _settings(finished=-1.0))
    jid = backup_job.start_backup_job(lambda: object(), executor=_inline)  # done, finished_at set
    # the next registry touch reaps it — the FE then 404s, a VISIBLE "lost from view" (the .sql on disk
    # remains the durable record)
    assert backup_job.get_job(jid) is None


def test_get_job_unknown_returns_none():
    assert backup_job.get_job("does-not-exist") is None


def test_own_ttls_not_the_dailys(monkeypatch):
    """The registry reads backup_job_* TTLs, NEVER the daily_job_* dials — a settings object carrying
    ONLY the backup dials must satisfy the reaper (an attribute reach for a daily TTL would AttributeError
    here, which is the point)."""
    monkeypatch.setattr(backup_job, "get_settings", lambda: _settings())
    jid = backup_job.start_backup_job(lambda: object(), executor=_inline)
    assert backup_job.get_job(jid).status == "done"  # reaped fine on the backup dials alone
