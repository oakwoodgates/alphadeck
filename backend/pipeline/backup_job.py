"""In-process SINGLE-SLOT job registry for the admin "Create snapshot" trigger — kick-off -> poll, so a
``pg_dump`` that runs ~30-90s is never a held-open request.

This MIRRORS ``pipeline/daily_job.py`` deliberately (the same state machine: atomic claim -> run in a
daemon thread -> publish -> poll; the same executor seam so tests run jobs inline; the same reaper bound)
but as its OWN registry with its OWN TTLs (the established "mirror as a peer" pattern — ``daily_job`` is
itself a peer of ``workbench/draft_jobs``): a snapshot is far quicker than the ~65-min cold daily pass, so
it reads ``Settings.backup_job_running_ttl_s`` (~600s) / ``backup_job_finished_ttl_s`` (~3600s), not the
daily dials — an attribute reach for a daily TTL would AttributeError here, which is the point.

SINGLE-SLOT, not per-anything: one ``pg_dump`` snapshots the whole DB, so process-wide there is exactly
one slot to claim (``BackupRunInFlight`` -> HTTP 409 at the route) — a double-click must never stack a
second dump. Single-process is authoritative for the same reason as the daily registry (uvicorn is pinned
to ``--workers 1``).

The in-memory ``result`` is a CONVENIENCE copy for the poll — the durable record is the ``.sql`` on disk
(the list endpoint reads it regardless). A restart wipes the registry: an in-flight poll then 404s, which
the FE shows as a visible "lost from view" (the snapshot list stays the authority), never an infinite
spinner. The reaper bounds the registry: a finished job drops after its TTL, and a still-running job past
the running TTL flips to ``failed`` and frees the slot (the abandoned-thread backstop).
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

_log = logging.getLogger("alphadeck.backup")

JobStatus = Literal["running", "done", "failed"]

_lock = threading.Lock()
_jobs: dict[str, "BackupJob"] = {}  # job_id -> BackupJob
_active_job_id: str | None = None  # the ONE running job (the single-slot 409 guard)


@dataclass
class BackupJob:
    """One snapshot job's state. ``result`` is the ``run()`` return value — typed ``Any`` so this
    pipeline module never imports the ``app`` wire schema (the layering stays one-way; the route builds
    and stores its own wire object, exactly the daily-jobs pattern)."""

    job_id: str
    status: JobStatus = "running"
    result: Any = None  # set iff status == "done"
    error: str | None = None  # set iff status == "failed" — the operator-facing message
    created_at: float = field(default_factory=monotonic)
    finished_at: float | None = None
    last_polled_at: float | None = None


class BackupRunInFlight(RuntimeError):
    """A snapshot is already in progress — the kick-off endpoint maps it to HTTP 409."""


# The executor seam (the testability hinge, same as daily_job): prod spawns a daemon thread; tests
# monkeypatch ``_DEFAULT_EXECUTOR`` to run the job INLINE, so a job is terminal by the time
# ``start_backup_job`` returns — no thread-timing flakiness, no race with the test-DB teardown.
_Executor = Callable[["BackupJob", Callable[[], Any]], None]


def _thread_executor(job: BackupJob, run: Callable[[], Any]) -> None:
    threading.Thread(
        target=_run_job, args=(job, run), daemon=True, name=f"backup-{job.job_id}"
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
    ``backup_job_finished_ttl_s``; flip a still-running job past ``backup_job_running_ttl_s`` to
    ``failed`` and free the slot (the abandoned-thread backstop)."""
    global _active_job_id
    s = get_settings()
    now = monotonic()
    for jid in list(_jobs):
        job = _jobs[jid]
        if job.finished_at is None:
            if now - job.created_at > s.backup_job_running_ttl_s:
                job.status, job.error, job.finished_at = "failed", "backup timed out", now
                if _active_job_id == jid:
                    _active_job_id = None
        elif now - job.finished_at > s.backup_job_finished_ttl_s:
            del _jobs[jid]


def start_backup_job(run: Callable[[], Any], *, executor: _Executor | None = None) -> str:
    """Claim THE snapshot slot ATOMICALLY and start the job; return its ``job_id``. Raises
    ``BackupRunInFlight`` if a snapshot is already in progress. ``run`` (the ``pg_dump`` + prune) executes
    in the executor — a daemon thread in prod, inline under tests — OUTSIDE the lock (the inline executor
    re-enters ``_run_job`` -> ``_lock``, so the executor must never be called while holding it)."""
    global _active_job_id
    job = BackupJob(job_id=uuid4().hex)
    with _lock:
        _reap_locked()
        if _active_job_id is not None:
            raise BackupRunInFlight(_active_job_id)
        _jobs[job.job_id] = job
        _active_job_id = job.job_id
    (executor or _DEFAULT_EXECUTOR)(job, run)
    return job.job_id


def _run_job(job: BackupJob, run: Callable[[], Any]) -> None:
    """The worker body (daemon thread, or inline under the test executor). Stores the result or a failure
    message and ALWAYS frees the slot in ``finally`` — a failed/raising dump never bricks the trigger.
    """
    global _active_job_id
    status: JobStatus = "failed"
    result: Any = None
    error: str | None = "backup failed"
    try:
        result = run()
        status, error = "done", None
    except (
        Exception
    ) as exc:  # noqa: BLE001 — an unexpected fault becomes a visible failed job, never a 500
        _log.exception("backup job %s failed", job.job_id)
        error = f"backup failed: {exc}"
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


def get_job(job_id: str) -> BackupJob | None:
    """The poll read: stamp ``last_polled_at``, run the reaper, return the job (or ``None`` — unknown /
    expired, or wiped by a restart -> the FE shows a visible "lost from view", never a spinner)."""
    with _lock:
        _reap_locked()
        job = _jobs.get(job_id)
        if job is not None:
            job.last_polled_at = monotonic()
        return job
