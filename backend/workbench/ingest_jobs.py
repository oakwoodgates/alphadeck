"""In-process job registry for the ON-PROMOTE new-member ingest — kick-off → poll, so a promote never
blocks on (or fails over) the data fetch for its newly-added basket members.

A promote that adds names kicks their back-half ingest (Form 4 + 8-K + 13D/G + EOD via the SAME
``pipeline.ingest_thesis`` unit the cron runs, scoped to the new ids) AFTER the spine commit, in a daemon
thread with its own DB connection; the promote response carries the ``job_id`` and the FE polls
``GET .../ingest/jobs/{job_id}``. This MIRRORS ``workbench/draft_jobs.py`` deliberately (the same state
machine: atomic claim → run in a daemon thread → publish → poll; the same executor seam so tests run jobs
inline; the same reaper bound) but as its OWN registry with its OWN TTLs (the ``daily_job`` precedent —
a big starter basket's first ingest can run past the drafter's 900s), because the two guards must not
collide: an ingest in flight must never 409 a draft, or vice versa.

**The in-flight design is QUEUE-AS-ONE-FOLLOW-UP, not fail-the-kick**: one RUNNING ingest per thesis;
a promote landing while one runs gets its new-member set queued as a single ``queued`` follow-up job
(further promotes MERGE their ids into that same queued job — one follow-up, never a convoy), which the
finishing run promotes to running in the same worker thread. Chosen over failing the kick because a
promote's new members are exactly the names waiting for data — dropping them to the nightly cron would
undo the feature the moment two Saves land close together; the merge keeps cost bounded (the cost
thread: at most one running + one pending per thesis). A queued job whose predecessor stalls past the
running TTL is flipped to a VISIBLE ``failed`` by the reaper (never silently stranded — the cron remains
the backstop either way).

FACTS ONLY, the cron's domain untouched: the runner ingests facts (append-only, ``recorded_at`` = now via
the existing machinery); it never writes a call record — ``record_if_changed`` stays ``pipeline.daily``'s.
State/verdict compute on read, so the Board updates on the next view once facts land. Fail-visible: a
failed run is a ``failed`` job on the poll + a WARNING log with the cause, never silent. KNOWN
LIMITATION (accepted — ``pipeline/daily_job.py``'s decision #10, same shape): the guard is in-process,
so it cannot see the cron SIDECAR's own concurrent walk of the same thesis; an overlap is wasteful,
never corrupting — the ingest unit is incremental/append-if-changed end-to-end.

Single-process is authoritative (uvicorn pinned to ``--workers 1``; ``draft_jobs.assert_single_worker``
already refuses env-driven scaling at app lifespan — that guard covers this registry too). A restart wipes
the registry — an in-flight poll then 404s, which the FE shows as a visible "lost from view" line (the
nightly cron still ingests everything; never an infinite spinner).
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

JobStatus = Literal["queued", "running", "done", "failed"]

# The runner receives the job's member scope AT EXECUTION time, not kick time — a queued follow-up may
# have merged in more ids since it was created (see ``start_ingest_job``).
Runner = Callable[[frozenset[UUID]], Any]

_lock = threading.Lock()
_jobs: dict[str, "IngestJob"] = {}  # job_id -> IngestJob
_active_by_thesis: dict[str, str] = {}  # thesis_id -> the RUNNING job_id (the in-flight guard)
_queued_by_thesis: dict[str, str] = (
    {}
)  # thesis_id -> the ONE queued follow-up job_id (merge target)


@dataclass
class IngestJob:
    """One ingest job's state. ``security_ids`` is the member scope (a queued job's set may GROW by
    merge until it starts). ``result`` is the runner's return value (a small summary dict) — typed
    ``Any`` so this workbench module never imports the ``app`` wire schema (the layering stays one-way,
    the draft-jobs pattern)."""

    job_id: str
    thesis_id: str
    security_ids: set[UUID]
    runner: Runner
    status: JobStatus = "running"
    result: Any = None  # set iff status == "done" (the summary the poll serves)
    error: str | None = None  # set iff status == "failed" — the operator-facing message
    created_at: float = field(default_factory=monotonic)
    finished_at: float | None = None
    last_polled_at: float | None = None


class IngestJobError(Exception):
    """An ingest run failed with an OPERATOR-FACING message (e.g. the per-name partial-failure summary).
    ``str(exc)`` is shown verbatim on the poll — the runner raises it with curated text (never a
    stack-leak); any OTHER exception becomes a generic failed message + a logged traceback."""


# The executor seam (the testability hinge, same as draft_jobs): prod spawns a daemon thread; tests
# monkeypatch ``_DEFAULT_EXECUTOR`` to run the job INLINE (synchronously), so a job is terminal by the
# time ``start_ingest_job`` returns — no thread-timing flakiness, no race with the test-DB teardown.
_Executor = Callable[["IngestJob"], None]


def _thread_executor(job: IngestJob) -> None:
    threading.Thread(target=_run_job, args=(job,), daemon=True, name=f"ingest-{job.job_id}").start()


_DEFAULT_EXECUTOR: _Executor = _thread_executor


def reset_state() -> None:
    """Clear the in-process registry. For TESTS only (the state persists across tests in one process) —
    never called on the request path."""
    with _lock:
        _jobs.clear()
        _active_by_thesis.clear()
        _queued_by_thesis.clear()


def _reap_locked() -> None:
    """Bound the registry. MUST be called holding ``_lock``. Drop a finished job past
    ``ingest_job_finished_ttl_s``; flip a still-running job past ``ingest_job_running_ttl_s`` to
    ``failed`` and free the slot (the abandoned-thread backstop). A QUEUED job past the running TTL is
    flipped to a VISIBLE ``failed`` too — its predecessor's thread is the only thing that ever starts it,
    so a stalled predecessor must never strand it silently (the nightly cron remains the backstop).
    """
    s = get_settings()
    now = monotonic()
    for jid in list(_jobs):
        job = _jobs[jid]
        if job.status == "queued":
            if now - job.created_at > s.ingest_job_running_ttl_s:
                job.status, job.error, job.finished_at = (
                    "failed",
                    "queued ingest was abandoned (the prior run stalled) — the nightly cron will "
                    "pick the names up",
                    now,
                )
                if _queued_by_thesis.get(job.thesis_id) == jid:
                    del _queued_by_thesis[job.thesis_id]
        elif job.finished_at is None:
            if now - job.created_at > s.ingest_job_running_ttl_s:
                job.status, job.error, job.finished_at = "failed", "ingest timed out", now
                if _active_by_thesis.get(job.thesis_id) == jid:
                    del _active_by_thesis[job.thesis_id]
        elif now - job.finished_at > s.ingest_job_finished_ttl_s:
            del _jobs[jid]


def start_ingest_job(
    thesis_id: UUID,
    security_ids: set[UUID],
    runner: Runner,
    *,
    executor: _Executor | None = None,
) -> IngestJob:
    """Claim the thesis's ingest slot ATOMICALLY and start the job — or, when one is already RUNNING for
    this thesis, queue the ids as the single follow-up run (merging into an existing queued job's set)
    instead of launching a parallel one. Returns the job the caller should reference (started or queued);
    it never raises for an in-flight run — a queued job is the answer, not a 409 (the promote must not
    fail, and the new names must not silently wait for the cron). ``runner`` executes in the executor —
    a daemon thread in prod, inline under tests — OUTSIDE the lock."""
    tid = str(thesis_id)
    with _lock:
        _reap_locked()
        if tid in _active_by_thesis:
            qid = _queued_by_thesis.get(tid)
            if qid is not None:
                queued = _jobs.get(qid)
                if queued is not None and queued.status == "queued":
                    # merge: ONE follow-up run per thesis — this promote's new ids join it
                    queued.security_ids |= set(security_ids)
                    return queued
            job = IngestJob(uuid4().hex, tid, set(security_ids), runner, status="queued")
            _jobs[job.job_id] = job
            _queued_by_thesis[tid] = job.job_id
            return job
        job = IngestJob(uuid4().hex, tid, set(security_ids), runner)
        _jobs[job.job_id] = job
        _active_by_thesis[tid] = job.job_id
    (executor or _DEFAULT_EXECUTOR)(job)
    return job


def _run_job(job: IngestJob) -> None:
    """The worker body (daemon thread, or inline under the test executor): run the job, then any queued
    follow-up this thread inherits, sequentially — so the follow-up needs no second thread and the
    inline test executor drives the whole chain synchronously."""
    current: IngestJob | None = job
    while current is not None:
        current = _run_one(current)


def _run_one(job: IngestJob) -> "IngestJob | None":
    """Run ONE job: store the result or a failure message (WARNING-logged — fail-visible, never silent)
    and ALWAYS release the thesis slot; if a queued follow-up exists, claim the slot for it and return it
    for the caller to run next."""
    status: JobStatus = "failed"
    result: Any = None
    error: str | None = "ingest failed"
    try:
        result = job.runner(frozenset(job.security_ids))
        status, error = "done", None
    except (
        IngestJobError
    ) as exc:  # a curated, operator-facing message (the partial-failure summary)
        error = str(exc)
        _log.warning("ingest job %s (thesis %s) failed: %s", job.job_id, job.thesis_id, exc)
    except Exception as exc:  # noqa: BLE001 — an unexpected fault becomes a visible failed job
        _log.warning("ingest job %s (thesis %s) failed", job.job_id, job.thesis_id, exc_info=True)
        error = f"ingest failed: {exc}"
    next_job: IngestJob | None = None
    with _lock:
        job.status, job.result, job.error, job.finished_at = status, result, error, monotonic()
        # release the slot ONLY if it still points at THIS job — a reaper timeout may have already freed
        # it and a newer job claimed the thesis; never release (or chain off) the newer job's claim.
        if _active_by_thesis.get(job.thesis_id) == job.job_id:
            del _active_by_thesis[job.thesis_id]
            qid = _queued_by_thesis.pop(job.thesis_id, None)
            if qid is not None:
                queued = _jobs.get(qid)
                if queued is not None and queued.status == "queued":
                    queued.status = "running"
                    _active_by_thesis[job.thesis_id] = queued.job_id
                    next_job = queued
    return next_job


def get_job(job_id: str) -> IngestJob | None:
    """The poll read: stamp ``last_polled_at``, run the reaper, return the job (or ``None`` — unknown/
    expired, or wiped by a restart → the FE shows a visible "lost from view" line; the nightly cron
    still ingests everything)."""
    with _lock:
        _reap_locked()
        job = _jobs.get(job_id)
        if job is not None:
            job.last_polled_at = monotonic()
        return job
