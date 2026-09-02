"""The in-process on-promote ingest-job registry (no DB, no HTTP, no thread) — run jobs INLINE via the
executor seam and assert the state machine: done carries the summary, failures are VISIBLE messages
(fail-visible, never silent), the thesis slot ALWAYS frees, and the QUEUE-AS-ONE-FOLLOW-UP in-flight
design: a kick while a job runs queues (never parallels, never drops), further kicks MERGE into that one
queued job, and the finishing run chains straight into it with the merged scope.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest

from workbench import ingest_jobs


@pytest.fixture(autouse=True)
def _clean():
    ingest_jobs.reset_state()
    yield
    ingest_jobs.reset_state()


def _inline(job):
    """Executor that runs the job synchronously (the prod default spawns a daemon thread)."""
    ingest_jobs._run_job(job)


def _noop(job):
    """Executor that never runs the job — it stays 'running' so the thesis slot stays claimed."""


def _settings(running: float = 600.0, finished: float = 1800.0) -> SimpleNamespace:
    return SimpleNamespace(ingest_job_running_ttl_s=running, ingest_job_finished_ttl_s=finished)


def test_start_runs_with_the_scope_and_stores_result():
    ids = {uuid4(), uuid4()}
    seen: list[frozenset[UUID]] = []

    def runner(scope: frozenset[UUID]):
        seen.append(scope)
        return {"members": len(scope)}

    job = ingest_jobs.start_ingest_job(uuid4(), ids, runner, executor=_inline)
    assert seen == [frozenset(ids)]  # the runner received EXACTLY the kicked scope
    got = ingest_jobs.get_job(job.job_id)
    assert got is not None
    assert got.status == "done" and got.result == {"members": 2} and got.error is None


def test_ingest_error_is_a_visible_failed_message():
    def boom(scope):
        raise ingest_jobs.IngestJobError("ingested 1 of 2 new names; 1 failed — X: price: down")

    job = ingest_jobs.start_ingest_job(uuid4(), {uuid4()}, boom, executor=_inline)
    got = ingest_jobs.get_job(job.job_id)
    assert got.status == "failed" and got.result is None
    # verbatim, operator-facing (the partial-failure summary travels to the poll untouched)
    assert got.error == "ingested 1 of 2 new names; 1 failed — X: price: down"


def test_unexpected_exception_is_a_generic_failed_job():
    def boom(scope):
        raise RuntimeError("kaboom")

    job = ingest_jobs.start_ingest_job(uuid4(), {uuid4()}, boom, executor=_inline)
    got = ingest_jobs.get_job(job.job_id)
    assert got.status == "failed" and "ingest failed" in got.error and "kaboom" in got.error


def test_slot_released_on_success_and_failure_allows_a_new_job():
    tid = uuid4()
    ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda s: {}, executor=_inline)

    def boom(scope):
        raise RuntimeError("x")

    ingest_jobs.start_ingest_job(tid, {uuid4()}, boom, executor=_inline)  # slot STILL freed
    job3 = ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda s: {}, executor=_inline)
    assert job3.status == "done"  # started immediately — nothing queued, nothing bricked


def test_kick_while_running_queues_one_follow_up_and_merges_further_kicks():
    """The in-flight design: a second kick QUEUES (a distinct job, status 'queued' — never a parallel
    run, never a drop), and a third kick MERGES its ids into that SAME queued job (one follow-up run
    per thesis — the cost bound)."""
    tid = uuid4()
    a, b, c = uuid4(), uuid4(), uuid4()
    running = ingest_jobs.start_ingest_job(tid, {a}, lambda s: {}, executor=_noop)
    assert running.status == "running"

    queued = ingest_jobs.start_ingest_job(tid, {b}, lambda s: {}, executor=_noop)
    assert queued.status == "queued" and queued.job_id != running.job_id
    merged = ingest_jobs.start_ingest_job(tid, {c}, lambda s: {}, executor=_noop)
    assert merged.job_id == queued.job_id  # the SAME follow-up job — merged, not a convoy
    assert queued.security_ids == {b, c}
    # the poll sees the queued job honestly (not 'running' — it hasn't started)
    assert ingest_jobs.get_job(queued.job_id).status == "queued"


def test_finishing_run_chains_into_the_queued_job_with_the_merged_scope():
    """When the running job's worker finishes, it promotes the queued follow-up and runs it in the same
    thread — with the ids as merged AT EXECUTION time (not kick time)."""
    tid = uuid4()
    a, b, c = uuid4(), uuid4(), uuid4()
    seen: list[frozenset[UUID]] = []

    def runner(scope: frozenset[UUID]):
        seen.append(scope)
        return {"members": len(scope)}

    first = ingest_jobs.start_ingest_job(tid, {a}, runner, executor=_noop)  # held running
    queued = ingest_jobs.start_ingest_job(tid, {b}, runner, executor=_noop)
    ingest_jobs.start_ingest_job(tid, {c}, runner, executor=_noop)  # merges into `queued`

    ingest_jobs._run_job(first)  # the worker body: runs first, then chains into the follow-up

    assert seen == [frozenset({a}), frozenset({b, c})]  # exactly one follow-up run, merged scope
    assert ingest_jobs.get_job(first.job_id).status == "done"
    assert ingest_jobs.get_job(queued.job_id).status == "done"
    # both slots are free again — a fresh kick starts immediately
    fresh = ingest_jobs.start_ingest_job(tid, {uuid4()}, runner, executor=_inline)
    assert fresh.status == "done"


def test_reaper_fails_a_stale_running_job_and_its_stranded_queued_follow_up(monkeypatch):
    """The abandoned-thread backstop: a running job past the TTL flips to failed AND its queued
    follow-up flips to a VISIBLE failed too (nothing else would ever start it) — never a silent
    strand; the message points at the cron backstop."""
    tid = uuid4()
    running = ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda s: {}, executor=_noop)
    queued = ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda s: {}, executor=_noop)
    monkeypatch.setattr(ingest_jobs, "get_settings", lambda: _settings(running=-1.0))

    got_running = ingest_jobs.get_job(running.job_id)  # the poll triggers the reaper
    got_queued = ingest_jobs.get_job(queued.job_id)
    assert got_running.status == "failed" and "timed out" in got_running.error
    assert got_queued.status == "failed" and "cron" in got_queued.error  # visible, cron-backstopped
    # the slot was freed — a new kick starts immediately (the thesis isn't bricked)
    fresh = ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda s: {}, executor=_inline)
    assert fresh.status == "done"


def test_reaper_drops_a_finished_job_past_its_ttl(monkeypatch):
    job = ingest_jobs.start_ingest_job(uuid4(), {uuid4()}, lambda s: {}, executor=_inline)
    monkeypatch.setattr(ingest_jobs, "get_settings", lambda: _settings(finished=-1.0))
    assert ingest_jobs.get_job(job.job_id) is None  # reaped -> the FE 404s -> visible


def test_get_job_unknown_returns_none():
    assert ingest_jobs.get_job("does-not-exist") is None


def test_different_theses_never_queue_on_each_other():
    t1, t2 = uuid4(), uuid4()
    ingest_jobs.start_ingest_job(t1, {uuid4()}, lambda s: {}, executor=_noop)  # t1 held running
    job2 = ingest_jobs.start_ingest_job(t2, {uuid4()}, lambda s: {}, executor=_inline)
    assert job2.status == "done"  # t2 ran immediately — the guard is per thesis
