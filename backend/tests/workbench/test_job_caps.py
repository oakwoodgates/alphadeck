"""A global cap on the API's background jobs (security hardening PR 2, item 5; docs/DEPLOY.md §Connections).

Each running draft job and each running on-promote ingest job holds its own database connection for its whole run,
so the number running at once, across every thesis, is capped: ``Settings.draft_jobs_max_running`` and
``Settings.ingest_jobs_max_running`` (4 and 4; 0 = no cap). Beyond the cap a new draft is refused
(``DraftCapReached`` -> the route's 429) and a new ingest is not started (``IngestCapReached`` -> the promote sends
its names to the onboarding queue). The one-per-thesis rules stay, and a promote whose thesis already has an ingest
running still queues behind it (no new connection).

A job counts until its worker returns, even after the reaper has marked it failed (its thread still holds the
connection).
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from workbench import draft_jobs, ingest_jobs


@pytest.fixture(autouse=True)
def _clean():
    draft_jobs.reset_state()
    ingest_jobs.reset_state()
    yield
    draft_jobs.reset_state()
    ingest_jobs.reset_state()


def _settings(cap: int, running: float = 600.0) -> SimpleNamespace:
    return SimpleNamespace(
        draft_job_running_ttl_s=running,
        draft_job_finished_ttl_s=1800.0,
        ingest_job_running_ttl_s=running,
        ingest_job_finished_ttl_s=1800.0,
        draft_jobs_max_running=cap,
        ingest_jobs_max_running=cap,
    )


class _Held:
    """An executor that holds every job it is given until ``finish`` runs them (a running thread, in effect)."""

    def __init__(self, run_job) -> None:
        self.run_job = run_job
        self.held: list = []

    def __call__(self, *args) -> None:
        self.held.append(args)

    def finish_one(self) -> None:
        self.run_job(*self.held.pop(0))


# --- drafts -----------------------------------------------------------------------------------------------------------


def test_drafts_beyond_the_cap_are_refused(monkeypatch):
    monkeypatch.setattr(draft_jobs, "get_settings", lambda: _settings(2))
    ex = _Held(draft_jobs._run_job)
    draft_jobs.start_draft_job(uuid4(), lambda: 1, executor=ex)
    draft_jobs.start_draft_job(uuid4(), lambda: 1, executor=ex)
    with pytest.raises(draft_jobs.DraftCapReached):
        draft_jobs.start_draft_job(uuid4(), lambda: 1, executor=ex)
    ex.finish_one()  # one finishes: there is room again
    draft_jobs.start_draft_job(uuid4(), lambda: 1, executor=ex)


def test_the_per_thesis_draft_rule_still_comes_first(monkeypatch):
    monkeypatch.setattr(draft_jobs, "get_settings", lambda: _settings(1))
    ex = _Held(draft_jobs._run_job)
    tid = uuid4()
    draft_jobs.start_draft_job(tid, lambda: 1, executor=ex)
    with pytest.raises(draft_jobs.DraftInFlight):
        draft_jobs.start_draft_job(tid, lambda: 1, executor=ex)


def test_a_reaped_draft_still_counts_until_its_worker_returns(monkeypatch):
    monkeypatch.setattr(draft_jobs, "get_settings", lambda: _settings(1, running=-1.0))
    ex = _Held(draft_jobs._run_job)
    draft_jobs.start_draft_job(uuid4(), lambda: 1, executor=ex)
    # the reaper (run on every claim) marks it failed at once, but its thread still holds a connection
    with pytest.raises(draft_jobs.DraftCapReached):
        draft_jobs.start_draft_job(uuid4(), lambda: 1, executor=ex)
    ex.finish_one()
    draft_jobs.start_draft_job(uuid4(), lambda: 1, executor=ex)


def test_a_zero_draft_cap_is_no_cap(monkeypatch):
    monkeypatch.setattr(draft_jobs, "get_settings", lambda: _settings(0))
    ex = _Held(draft_jobs._run_job)
    for _ in range(10):
        draft_jobs.start_draft_job(uuid4(), lambda: 1, executor=ex)


# --- ingests ----------------------------------------------------------------------------------------------------------


def test_ingests_beyond_the_cap_are_not_started(monkeypatch):
    monkeypatch.setattr(ingest_jobs, "get_settings", lambda: _settings(2))
    ex = _Held(ingest_jobs._run_job)
    ingest_jobs.start_ingest_job(uuid4(), {uuid4()}, lambda ids: {}, executor=ex)
    ingest_jobs.start_ingest_job(uuid4(), {uuid4()}, lambda ids: {}, executor=ex)
    tid = uuid4()
    assert not ingest_jobs.has_room(tid)
    with pytest.raises(ingest_jobs.IngestCapReached):
        ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda ids: {}, executor=ex)
    ex.finish_one()
    assert ingest_jobs.has_room(tid)
    ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda ids: {}, executor=ex)


def test_a_thesis_with_a_running_ingest_still_queues_at_the_cap(monkeypatch):
    monkeypatch.setattr(ingest_jobs, "get_settings", lambda: _settings(1))
    ex = _Held(ingest_jobs._run_job)
    tid = uuid4()
    ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda ids: {}, executor=ex)
    assert ingest_jobs.has_room(
        tid
    )  # a follow-up rides the running job's thread: no new connection
    job = ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda ids: {}, executor=ex)
    assert job.status == "queued"


def test_a_chained_follow_up_does_not_count_twice(monkeypatch):
    monkeypatch.setattr(ingest_jobs, "get_settings", lambda: _settings(1))
    ex = _Held(ingest_jobs._run_job)
    tid = uuid4()
    ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda ids: {}, executor=ex)
    ingest_jobs.start_ingest_job(tid, {uuid4()}, lambda ids: {}, executor=ex)  # queued
    ex.finish_one()  # runs the job, then its follow-up, in the same worker
    assert ingest_jobs.has_room(uuid4())


def test_a_zero_ingest_cap_is_no_cap(monkeypatch):
    monkeypatch.setattr(ingest_jobs, "get_settings", lambda: _settings(0))
    ex = _Held(ingest_jobs._run_job)
    for _ in range(10):
        ingest_jobs.start_ingest_job(uuid4(), {uuid4()}, lambda ids: {}, executor=ex)
