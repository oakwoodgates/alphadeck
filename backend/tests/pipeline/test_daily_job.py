"""The in-process SINGLE-SLOT daily-run job registry (no DB, no HTTP, no thread) — run jobs INLINE via
the executor seam and assert the state machine: done carries the result, a fault is a VISIBLE failed
message, the slot ALWAYS frees (a failed run never bricks the trigger), the single-slot 409 guard, and
the reaper on the job's OWN TTLs (the drafter's 900s would kill a healthy ~65-min cold pass — the whole
reason this registry exists apart from ``draft_jobs``)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from pipeline import daily_job


@pytest.fixture(autouse=True)
def _clean():
    daily_job.reset_state()
    yield
    daily_job.reset_state()


def _inline(job, run):
    """Executor that runs the job synchronously (the prod default spawns a daemon thread)."""
    daily_job._run_job(job, run)


def _noop(job, run):
    """Executor that never runs the job — it stays 'running' so the single slot stays claimed."""


def _settings(running: float = 7200.0, finished: float = 3600.0) -> SimpleNamespace:
    return SimpleNamespace(daily_job_running_ttl_s=running, daily_job_finished_ttl_s=finished)


def test_start_runs_and_stores_result():
    sentinel = (
        object()
    )  # result is Any — the registry never inspects it (the route shapes the wire)
    jid = daily_job.start_daily_job(lambda: sentinel, executor=_inline)
    job = daily_job.get_job(jid)
    assert job is not None
    assert job.status == "done" and job.result is sentinel and job.error is None


def test_exception_is_a_visible_failed_job():
    def boom():
        raise RuntimeError("kaboom")

    job = daily_job.get_job(daily_job.start_daily_job(boom, executor=_inline))
    assert job.status == "failed" and "daily run failed" in job.error and "kaboom" in job.error


def test_slot_released_on_success_allows_a_new_run():
    daily_job.start_daily_job(lambda: object(), executor=_inline)  # finishes inline -> slot freed
    jid2 = daily_job.start_daily_job(lambda: object(), executor=_inline)  # no DailyRunInFlight
    assert daily_job.get_job(jid2).status == "done"


def test_slot_released_on_failure_allows_a_new_run():
    def boom():
        raise RuntimeError("x")

    daily_job.start_daily_job(boom, executor=_inline)  # failed -> slot STILL freed (finally)
    jid2 = daily_job.start_daily_job(lambda: object(), executor=_inline)
    assert daily_job.get_job(jid2).status == "done"


def test_second_start_while_running_raises_in_flight():
    # THE single-slot guard: one daily pass at a time, process-wide — a double-click must never stack
    # a second live-EDGAR pass
    daily_job.start_daily_job(lambda: object(), executor=_noop)  # stays running, slot held
    with pytest.raises(daily_job.DailyRunInFlight):
        daily_job.start_daily_job(lambda: object(), executor=_inline)


def test_reaper_fails_a_stale_running_job_and_frees_the_slot(monkeypatch):
    monkeypatch.setattr(
        daily_job, "get_settings", lambda: _settings(running=-1.0)
    )  # any running -> stale
    jid = daily_job.start_daily_job(lambda: object(), executor=_noop)  # left running
    job = daily_job.get_job(jid)  # the poll triggers the reaper
    assert job.status == "failed" and "timed out" in job.error  # the abandoned-thread backstop
    # the slot was freed -> a new run can start (the trigger isn't bricked)
    jid2 = daily_job.start_daily_job(lambda: object(), executor=_inline)
    assert daily_job.get_job(jid2).status == "done"


def test_reaper_drops_a_finished_job_past_its_ttl(monkeypatch):
    monkeypatch.setattr(daily_job, "get_settings", lambda: _settings(finished=-1.0))
    jid = daily_job.start_daily_job(lambda: object(), executor=_inline)  # done, finished_at set
    # the next registry touch reaps it — the FE then 404s, a VISIBLE "lost from view" (the run-log
    # artifact + calls rows the pass wrote remain the durable record)
    assert daily_job.get_job(jid) is None


def test_get_job_unknown_returns_none():
    assert daily_job.get_job("does-not-exist") is None


def test_own_ttls_not_the_drafters(monkeypatch):
    """The registry reads daily_job_* TTLs, NEVER the drafter's draft_job_* dials — a settings object
    carrying ONLY the daily dials must satisfy the reaper (an attribute reach for a draft TTL would
    AttributeError here, which is the point)."""
    monkeypatch.setattr(daily_job, "get_settings", lambda: _settings())
    jid = daily_job.start_daily_job(lambda: object(), executor=_inline)
    assert daily_job.get_job(jid).status == "done"  # reaped fine on the daily dials alone
