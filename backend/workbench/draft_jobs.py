"""In-process job registry for the narrative→chain draft — kick-off → poll, so a multi-minute draft is never a
held-open request.

The draft pipeline (EDGAR discovery + the Opus web-search tail-sweep + Sonnet decompose + narrate) takes
minutes; held open, it blew past nginx's 300s ``proxy_read_timeout`` — the browser 504'd while the backend kept
churning (abandoned work billing). So the POST KICKS OFF a job (returns 202 + ``job_id`` immediately) and the FE
POLLS a status endpoint. The draft LOGIC is unchanged — only the delivery.

This mirrors ``workbench.research_runner`` EXACTLY: a module-level registry + a ``threading.Lock``, an ATOMIC
check-and-claim (one running job per thesis → ``DraftInFlight`` → HTTP 409), and a ``finally``-release so a
failed/timed-out job never strands a thesis permanently in-flight. Single-process is authoritative: uvicorn runs
ONE worker (``Dockerfile`` CMD, no ``--workers``) and the job runs in a daemon thread in that process, so a
module-level dict + a ``threading.Lock`` is correct. (If ``--workers>1`` is ever added, this needs a shared store
— a DB-backed ``draft_jobs`` table — the same caveat as ``research_runner``.)

The result (a ``ChainDraftOut``) lives ONLY in memory and is RESPONSE-ONLY: the draft writes no fact and no
promote (INVARIANT #2/#3). A restart wipes the registry — an in-flight poll then 404s, which the FE shows as a
visible "draft was lost" failure (never an infinite spinner). The reaper bounds the registry: a finished job is
dropped after its TTL, and a still-running job past the running TTL is flipped to ``failed`` (the abandoned-job
backstop — the real cost bound is the Opus client's ``max_retries=0`` + 300s SDK timeout, one bounded pass).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import Any, Literal
from uuid import UUID, uuid4

from domain.settings import get_settings

_log = logging.getLogger("alphadeck.workbench")

JobStatus = Literal["running", "done", "failed"]

_lock = threading.Lock()
_jobs: dict[str, "DraftJob"] = {}  # job_id -> DraftJob
_active_by_thesis: dict[str, str] = {}  # thesis_id -> the RUNNING job_id (the 409 in-flight guard)


@dataclass
class DraftJob:
    """One draft job's state. ``result`` is the ``run()`` return value (a ``ChainDraftOut``) — typed ``Any`` so
    this workbench module never imports the ``app`` wire schema (the layering stays one-way)."""

    job_id: str
    thesis_id: str
    status: JobStatus = "running"
    result: Any = None  # set iff status == "done" (a ChainDraftOut)
    error: str | None = None  # set iff status == "failed" — the operator-facing message
    created_at: float = field(default_factory=monotonic)
    finished_at: float | None = None
    last_polled_at: float | None = None


class DraftInFlight(RuntimeError):
    """A draft job for this thesis is already running — the kick-off endpoint maps it to HTTP 409. A
    double-click or a stray retry must NEVER launch a parallel (expensive) Opus pass."""


class DraftError(Exception):
    """A draft failed with an OPERATOR-FACING message (e.g. discovery not ready). ``str(exc)`` is shown to the
    operator verbatim on the poll, so the kick-off thunk raises it with the curated text (NOT a stack-leak). Any
    OTHER exception becomes a generic failed message + a logged traceback."""


# The executor seam (the testability hinge): prod spawns a daemon thread; tests monkeypatch ``_DEFAULT_EXECUTOR``
# to run the job INLINE (synchronously), so a job is ``done``/``failed`` by the time ``start_draft_job`` returns
# — no thread-timing flakiness, no race with the test DB teardown.
_Executor = Callable[["DraftJob", Callable[[], Any]], None]


def _thread_executor(job: DraftJob, run: Callable[[], Any]) -> None:
    threading.Thread(
        target=_run_job, args=(job, run), daemon=True, name=f"draft-{job.job_id}"
    ).start()


_DEFAULT_EXECUTOR: _Executor = _thread_executor


def reset_state() -> None:
    """Clear the in-process registry. For TESTS only (the state persists across tests in one process) — never
    called on the request path."""
    with _lock:
        _jobs.clear()
        _active_by_thesis.clear()


def _reap_locked() -> None:
    """Bound the registry. MUST be called holding ``_lock``. Drop a finished job past ``draft_job_finished_ttl_s``;
    flip a still-running job past ``draft_job_running_ttl_s`` to ``failed`` and free its slot (the abandoned-job
    backstop, set ABOVE the FE poll-cap so the operator sees "timed out, try again" before the reaper acts).
    """
    s = get_settings()
    now = monotonic()
    for jid in list(_jobs):
        job = _jobs[jid]
        if job.finished_at is None:
            if now - job.created_at > s.draft_job_running_ttl_s:
                job.status, job.error, job.finished_at = "failed", "draft timed out", now
                if _active_by_thesis.get(job.thesis_id) == jid:
                    del _active_by_thesis[job.thesis_id]
        elif now - job.finished_at > s.draft_job_finished_ttl_s:
            del _jobs[jid]


def start_draft_job(
    thesis_id: UUID,
    run: Callable[[], Any],
    *,
    executor: _Executor | None = None,
) -> str:
    """Claim the thesis's draft slot ATOMICALLY and start the job; return its ``job_id``. Raises ``DraftInFlight``
    if a draft for this thesis is already running. ``run`` (the slow pipeline) executes in the executor — a daemon
    thread in prod, inline under tests — OUTSIDE the lock (the inline executor re-enters ``_run_job`` → ``_lock``,
    so the executor must never be called while holding it)."""
    tid = str(thesis_id)
    job = DraftJob(job_id=uuid4().hex, thesis_id=tid)
    with _lock:
        _reap_locked()
        if tid in _active_by_thesis:
            raise DraftInFlight(tid)
        _jobs[job.job_id] = job
        _active_by_thesis[tid] = job.job_id
    (executor or _DEFAULT_EXECUTOR)(job, run)
    return job.job_id


def _run_job(job: DraftJob, run: Callable[[], Any]) -> None:
    """The worker body (daemon thread, or inline under the test executor). Stores the result or a failure message
    and ALWAYS releases the thesis slot in ``finally`` — a failed/raising job never bricks a thesis.
    """
    status: JobStatus = "failed"
    result: Any = None
    error: str | None = "draft failed"
    try:
        result = run()
        status, error = "done", None
    except DraftError as exc:  # a curated, operator-facing message (e.g. discovery not ready)
        error = str(exc)
    except (
        Exception
    ) as exc:  # noqa: BLE001 — an unexpected fault becomes a visible failed job, never a 500
        _log.exception("draft job %s failed", job.job_id)
        error = f"draft failed: {exc}"
    finally:
        with _lock:
            job.status, job.result, job.error, job.finished_at = (
                status,
                result,
                error,
                monotonic(),
            )
            # free the slot ONLY if it still points at THIS job — a reaper-timeout may have already freed it and a
            # newer job claimed the thesis; never delete the newer job's slot.
            if _active_by_thesis.get(job.thesis_id) == job.job_id:
                del _active_by_thesis[job.thesis_id]


def get_job(job_id: str) -> DraftJob | None:
    """The poll read: stamp ``last_polled_at``, run the reaper, return the job (or ``None`` — unknown/expired, or
    wiped by a restart → the FE shows a visible "draft was lost")."""
    with _lock:
        _reap_locked()
        job = _jobs.get(job_id)
        if job is not None:
            job.last_polled_at = monotonic()
        return job
