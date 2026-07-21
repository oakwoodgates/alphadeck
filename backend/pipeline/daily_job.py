"""In-process SINGLE-SLOT job registry for the admin "Run daily now" trigger — kick-off → poll, so a
daily pass that can run ~65 minutes cold is never a held-open request.

This MIRRORS ``workbench/draft_jobs.py`` deliberately (the same state machine: atomic claim → run in a
daemon thread → publish → poll; the same executor seam so tests run jobs inline; the same reaper bound)
but as its OWN registry with its OWN TTLs (Slice-1 decision #1): the drafter's 900s running TTL would
flip a HEALTHY cold daily pass to "failed" mid-run, so this registry reads
``Settings.daily_job_running_ttl_s`` (~7200s) / ``daily_job_finished_ttl_s`` (~3600s) instead.

SINGLE-SLOT, not per-thesis: one daily pass walks EVERY thesis, so process-wide there is exactly one
slot to claim (``DailyRunInFlight`` → HTTP 409 at the route) — a double-click or stray retry must never
stack a second live-EDGAR pass. Single-process is authoritative for the same reason as the drafter
(uvicorn is pinned to ``--workers 1``; ``assert_single_worker`` refuses env-driven scaling at boot).

KNOWN LIMITATION (accepted, decision #10): the guard is in-process — it cannot see the cron SIDECAR's
own run in a separate container. An overlap is wasteful, never corrupting: the pass is idempotent
end-to-end (incremental ingest + ``record_if_changed``).

The in-memory ``result`` is a CONVENIENCE copy for the poll — the durable record is what the pass itself
writes (the calls log + the run-of-record artifact). A restart wipes the registry: an in-flight poll then
404s, which the FE shows as a visible "lost from view" (the run history stays the authority), never an
infinite spinner. The reaper bounds the registry: a finished job drops after its TTL, and a still-running
job past the running TTL flips to ``failed`` and frees the slot (the abandoned-thread backstop).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal
from uuid import uuid4

from domain.settings import get_settings

_log = logging.getLogger("alphadeck.admin")

JobStatus = Literal["running", "done", "failed"]

_lock = threading.Lock()
_jobs: dict[str, "DailyJob"] = {}  # job_id -> DailyJob
_active_job_id: str | None = None  # the ONE running job (the single-slot 409 guard)


@dataclass
class DailyJob:
    """One daily-run job's state. ``result`` is the ``run()`` return value — typed ``Any`` so this
    pipeline module never imports the ``app`` wire schema (the layering stays one-way; the route builds
    and stores its own wire object, exactly the draft-jobs pattern)."""

    job_id: str
    status: JobStatus = "running"
    result: Any = None  # set iff status == "done"
    error: str | None = None  # set iff status == "failed" — the operator-facing message
    created_at: float = field(default_factory=monotonic)
    finished_at: float | None = None
    last_polled_at: float | None = None


class DailyRunInFlight(RuntimeError):
    """A daily run is already in progress — the kick-off endpoint maps it to HTTP 409."""


# The executor seam (the testability hinge, same as draft_jobs): prod spawns a daemon thread; tests
# monkeypatch ``_DEFAULT_EXECUTOR`` to run the job INLINE, so a job is terminal by the time
# ``start_daily_job`` returns — no thread-timing flakiness, no race with the test-DB teardown.
_Executor = Callable[["DailyJob", Callable[[], Any]], None]


def _thread_executor(job: DailyJob, run: Callable[[], Any]) -> None:
    threading.Thread(
        target=_run_job, args=(job, run), daemon=True, name=f"daily-{job.job_id}"
    ).start()


_DEFAULT_EXECUTOR: _Executor = _thread_executor


def reset_state() -> None:
    """Clear the in-process registry. For TESTS only (the state persists across tests in one process) —
    never called on the request path."""
    global _active_job_id
    with _lock:
        _jobs.clear()
        _active_job_id = None


def _reap_locked() -> None:
    """Bound the registry. MUST be called holding ``_lock``. Drop a finished job past
    ``daily_job_finished_ttl_s``; flip a still-running job past ``daily_job_running_ttl_s`` to ``failed``
    and free the slot (the abandoned-thread backstop — sized ABOVE the worst cold pass, see Settings).
    """
    global _active_job_id
    s = get_settings()
    now = monotonic()
    for jid in list(_jobs):
        job = _jobs[jid]
        if job.finished_at is None:
            if now - job.created_at > s.daily_job_running_ttl_s:
                job.status, job.error, job.finished_at = "failed", "daily run timed out", now
                if _active_job_id == jid:
                    _active_job_id = None
        elif now - job.finished_at > s.daily_job_finished_ttl_s:
            del _jobs[jid]


def start_daily_job(run: Callable[[], Any], *, executor: _Executor | None = None) -> str:
    """Claim THE daily-run slot ATOMICALLY and start the job; return its ``job_id``. Raises
    ``DailyRunInFlight`` if a run is already in progress. ``run`` (the full daily pass) executes in the
    executor — a daemon thread in prod, inline under tests — OUTSIDE the lock (the inline executor
    re-enters ``_run_job`` → ``_lock``, so the executor must never be called while holding it)."""
    global _active_job_id
    job = DailyJob(job_id=uuid4().hex)
    with _lock:
        _reap_locked()
        if _active_job_id is not None:
            raise DailyRunInFlight(_active_job_id)
        _jobs[job.job_id] = job
        _active_job_id = job.job_id
    (executor or _DEFAULT_EXECUTOR)(job, run)
    return job.job_id


def _run_job(job: DailyJob, run: Callable[[], Any]) -> None:
    """The worker body (daemon thread, or inline under the test executor). Stores the result or a failure
    message and ALWAYS frees the slot in ``finally`` — a failed/raising run never bricks the trigger.
    """
    global _active_job_id
    status: JobStatus = "failed"
    result: Any = None
    error: str | None = "daily run failed"
    try:
        result = run()
        status, error = "done", None
    except (
        Exception
    ) as exc:  # noqa: BLE001 — an unexpected fault becomes a visible failed job, never a 500
        _log.exception("daily run job %s failed", job.job_id)
        error = f"daily run failed: {exc}"
    finally:
        with _lock:
            job.status, job.result, job.error, job.finished_at = (
                status,
                result,
                error,
                monotonic(),
            )
            # free the slot ONLY if it still points at THIS job — a reaper timeout may have already freed
            # it and a newer job claimed the slot; never release the newer job's claim.
            if _active_job_id == job.job_id:
                _active_job_id = None


def get_job(job_id: str) -> DailyJob | None:
    """The poll read: stamp ``last_polled_at``, run the reaper, return the job (or ``None`` — unknown /
    expired, or wiped by a restart → the FE shows a visible "lost from view", never a spinner)."""
    with _lock:
        _reap_locked()
        job = _jobs.get(job_id)
        if job is not None:
            job.last_polled_at = monotonic()
        return job
