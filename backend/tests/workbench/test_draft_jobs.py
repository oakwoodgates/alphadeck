"""The in-process draft-job registry (no DB, no HTTP, no thread) — run jobs INLINE via the executor seam and
assert the state machine: done carries the result, failures are VISIBLE messages, the thesis slot ALWAYS frees
(so a thesis never bricks), the 409 in-flight guard, and the reaper (stale-running -> failed; finished -> dropped).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from workbench import draft_jobs


@pytest.fixture(autouse=True)
def _clean():
    draft_jobs.reset_state()
    yield
    draft_jobs.reset_state()


def _inline(job, run):
    """Executor that runs the job synchronously (the prod default spawns a daemon thread)."""
    draft_jobs._run_job(job, run)


def _noop(job, run):
    """Executor that never runs the job — it stays 'running' so the thesis slot stays claimed."""


def _settings(running: float = 600.0, finished: float = 1800.0) -> SimpleNamespace:
    return SimpleNamespace(draft_job_running_ttl_s=running, draft_job_finished_ttl_s=finished)


def test_start_runs_and_stores_result():
    sentinel = object()  # result is Any — the registry never inspects it
    jid = draft_jobs.start_draft_job(uuid4(), lambda: sentinel, executor=_inline)
    job = draft_jobs.get_job(jid)
    assert job is not None
    assert job.status == "done" and job.result is sentinel and job.error is None


def test_draft_error_is_a_visible_failed_message():
    def boom():
        raise draft_jobs.DraftError("term set is empty — produce or seed it first")

    job = draft_jobs.get_job(draft_jobs.start_draft_job(uuid4(), boom, executor=_inline))
    assert job.status == "failed" and job.result is None
    assert job.error == "term set is empty — produce or seed it first"  # verbatim, operator-facing


def test_unexpected_exception_is_a_generic_failed_job():
    def boom():
        raise RuntimeError("kaboom")

    job = draft_jobs.get_job(draft_jobs.start_draft_job(uuid4(), boom, executor=_inline))
    assert job.status == "failed" and "draft failed" in job.error and "kaboom" in job.error


def test_slot_released_on_success_allows_a_new_job():
    tid = uuid4()
    draft_jobs.start_draft_job(
        tid, lambda: object(), executor=_inline
    )  # finishes inline -> slot freed
    jid2 = draft_jobs.start_draft_job(tid, lambda: object(), executor=_inline)  # no DraftInFlight
    assert draft_jobs.get_job(jid2).status == "done"


def test_slot_released_on_failure_allows_a_new_job():
    tid = uuid4()

    def boom():
        raise RuntimeError("x")

    draft_jobs.start_draft_job(tid, boom, executor=_inline)  # failed -> slot STILL freed (finally)
    jid2 = draft_jobs.start_draft_job(tid, lambda: object(), executor=_inline)
    assert draft_jobs.get_job(jid2).status == "done"


def test_second_start_while_running_raises_draft_inflight():
    tid = uuid4()
    draft_jobs.start_draft_job(tid, lambda: object(), executor=_noop)  # stays running, slot held
    with pytest.raises(draft_jobs.DraftInFlight):
        draft_jobs.start_draft_job(tid, lambda: object(), executor=_inline)


def test_reaper_fails_a_stale_running_job_and_frees_the_slot(monkeypatch):
    monkeypatch.setattr(
        draft_jobs, "get_settings", lambda: _settings(running=-1.0)
    )  # any running -> stale
    tid = uuid4()
    jid = draft_jobs.start_draft_job(tid, lambda: object(), executor=_noop)  # left running
    job = draft_jobs.get_job(jid)  # the poll triggers the reaper
    assert job.status == "failed" and "timed out" in job.error  # the abandoned-job backstop
    # the slot was freed -> a new draft can start (the thesis isn't bricked)
    jid2 = draft_jobs.start_draft_job(tid, lambda: object(), executor=_inline)
    assert draft_jobs.get_job(jid2).status == "done"


def test_reaper_drops_a_finished_job_past_its_ttl(monkeypatch):
    monkeypatch.setattr(draft_jobs, "get_settings", lambda: _settings(finished=-1.0))
    jid = draft_jobs.start_draft_job(
        uuid4(), lambda: object(), executor=_inline
    )  # done, finished_at set
    assert (
        draft_jobs.get_job(jid) is None
    )  # the next registry touch reaps it (the FE then 404s -> visible)


def test_get_job_unknown_returns_none():
    assert draft_jobs.get_job("does-not-exist") is None


# --- the single-worker startup guard (the honest-discovery slice fold) ---


def test_assert_single_worker_passes_on_empty_env_and_one():
    """The normal states boot: no scaling vars at all, an explicit '1', and blank/whitespace values."""
    draft_jobs.assert_single_worker({})
    draft_jobs.assert_single_worker({"WEB_CONCURRENCY": "1", "UVICORN_WORKERS": "1"})
    draft_jobs.assert_single_worker({"WEB_CONCURRENCY": "  "})  # blank -> ignored


def test_assert_single_worker_refuses_env_driven_scaling():
    """>1 worker via either env knob REFUSES to boot, naming the per-process registries — with multiple
    workers the 409 in-flight guard evaporates and job polls 404 on the wrong worker, SILENTLY (the exact
    failure shape the guard exists to make impossible-or-loud)."""
    for env in ({"WEB_CONCURRENCY": "4"}, {"UVICORN_WORKERS": "2"}):
        with pytest.raises(RuntimeError, match="SINGLE-WORKER by design"):
            draft_jobs.assert_single_worker(env)


def test_assert_single_worker_warns_not_raises_on_garbage():
    """An unparseable value only warns (uvicorn ignores it under the Dockerfile's explicit --workers 1) —
    a typo'd deploy var must not brick the boot when the effective worker count is still one."""
    draft_jobs.assert_single_worker({"WEB_CONCURRENCY": "lots"})  # no raise
