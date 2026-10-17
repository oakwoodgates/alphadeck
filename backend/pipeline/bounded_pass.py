"""The Admin "Run daily now" pass, BOUNDED like the sidecar's (C2) — one value, one way to end it.

THE GAP. The cron sidecar runs every ``pipeline.daily`` pass under ``timeout -k 60 "$RUN_TIMEOUT_S"``
(``scripts/daily_cron.sh``, default 5400 s): a hung pass is stopped, its process dies, Postgres ends its
session and so releases the call-of-record lock (``pipeline/run_lock.py`` — a session-level advisory lock),
and the start artifact it left reads "did not finish" on Admin. The Admin button ran the SAME pass in a
thread of the backend process with no bound at all: a run past 90 minutes read "did not finish" until it
landed, and a HUNG one held the run lock — and so blocked every later nightly pass — for as long as the
backend lived. A Python thread cannot be stopped from outside, so the bound needs a process boundary.

WHAT THIS DOES. ``run_admin_pass`` runs the pass in a CHILD PROCESS (``spawn`` — a fresh interpreter, never
a fork of the threaded server) and waits at most ``Settings.cron_run_timeout_s`` — the backend's mirror of
the sidecar's ``RUN_TIMEOUT_S``, both set by compose from the one host variable
``ALPHADECK_CRON_RUN_TIMEOUT_S`` (pinned equal by ``tests/pipeline/test_killed_runs.py``). Past it, the
child is ended the way ``timeout -k 60`` ends the sidecar's: SIGTERM, then SIGKILL after ``KILL_GRACE_S``.
Then, because this parent outlives the child (the sidecar's shell has no Python to do it):

- the pass's START artifact (M1, written by the child the moment it held the lock) is re-marked
  ``crashed`` with the reason (``cron_run_log.mark_run_crashed`` — only while it still reads
  ``running``), so Admin alarms on it at once with the words "ran past the run timeout";
- ONE crash page goes out through the notifier (H3's ``HealthEvent.crashed``), as a crash inside the pass
  would have sent;
- the job fails visibly with the same words.

The run lock needs no step: the child's death closed its connection, and Postgres released the lock with
the session. A child that dies on its own before finishing (the OOM killer) is handled the same way, with
its exit code as the reason. A pass that raises INSIDE the child already paged and marked itself crashed
(``run_daily_pass``'s H3 path), so the parent only reports it.

THE PROTOCOL (child → parent, one pipe): ``("started", started_at, asof)`` the moment the start artifact
is written (``run_daily_pass(on_started=...)``), then exactly one of ``("done", payload)`` — the run payload
``build_run_payload`` makes, so the poll shows the same numbers as the artifact — ``("refused", holder)``
(another process holds the lock: ``PassAlreadyRunning``) or ``("failed", type, message)``.

The child's output goes to the backend's stdout as before (inherited), so ``docker logs`` still carries
the pass. Imports here stay light: the child imports only this module and the pass body it runs.
"""

from __future__ import annotations

import logging
import multiprocessing
from collections.abc import Callable
from datetime import date, datetime, timezone
from pathlib import Path
from time import monotonic
from typing import Any

from pipeline.run_lock import PassAlreadyRunning

_log = logging.getLogger("alphadeck.admin")

# The sidecar's `timeout -k 60`: how long a stopped pass gets between SIGTERM and SIGKILL. A Python pass with
# no SIGTERM handler dies on the first signal, so this only matters for a process stuck where the signal
# cannot reach it.
KILL_GRACE_S = 60.0
# How often the waiting parent wakes to check the clock while the child is silent.
_POLL_S = 1.0


class PassTimedOut(RuntimeError):
    """The pass ran past the run timeout and was stopped (the job's visible failure)."""


class PassDied(RuntimeError):
    """The pass's process ended before it reported a result (killed from outside: OOM, a kill -9)."""


class PassFailed(RuntimeError):
    """The pass raised inside the child; its own crash path already paged and marked the artifact."""


def pass_payload(*, on_started: Callable[[datetime, date], None] | None = None) -> dict:
    """THE BODY: the cron's exact unit of work (``run_daily_pass`` — live, as-of today, run kind
    ``manual``) and its run payload, the one mapping the poll and the artifact share. The child process
    runs this; called directly it is the same pass, in process and unbounded."""
    from pipeline.cron_run_log import build_run_payload
    from pipeline.daily import run_daily_pass

    outcome = run_daily_pass(on_started=on_started)
    return build_run_payload(outcome.results, **outcome.payload_kwargs())


def run_admin_pass(*, timeout_s: float | None = None) -> dict:
    """The Admin "Run daily now" unit: ``pass_payload`` in a child process, bounded by the run timeout —
    ``Settings.cron_run_timeout_s`` unless ``timeout_s`` is given. Returns the run payload; raises
    ``PassTimedOut`` / ``PassDied`` / ``PassFailed`` / ``PassAlreadyRunning`` (each becomes the job's
    visible error)."""
    if timeout_s is None:
        from domain.settings import get_settings

        timeout_s = float(get_settings().cron_run_timeout_s)
    return run_bounded(pass_payload, timeout_s=timeout_s)


def _child_main(conn: Any, body: Callable[..., dict], args: tuple) -> None:
    """The child process's entry: run ``body(*args, on_started=...)`` and report over ``conn``. Never
    raises out (the parent reads the pipe; an exception here would only reach stderr)."""

    def _started(started_at: datetime, asof: date) -> None:
        conn.send(("started", started_at.isoformat(), asof.isoformat()))

    try:
        payload = body(*args, on_started=_started)
    except PassAlreadyRunning as exc:
        conn.send(("refused", exc.holder))
    except (
        BaseException
    ) as exc:  # noqa: BLE001 — every failure is reported to the parent, never lost
        msg = " ".join(str(exc).split())[:300]
        conn.send(("failed", type(exc).__name__, msg))
    else:
        conn.send(("done", payload))
    finally:
        conn.close()


def _aftermath(started_at: datetime | None) -> str:
    """What the parent did about a pass that did not finish, said honestly: a pass that never reported its
    start (stopped while still connecting — before it held the lock or wrote an artifact) has nothing to
    mark."""
    if started_at is None:
        return "it had not started yet (no run artifact, no run lock held)"
    return "its run artifact is marked crashed and the run lock was released with it"


def _stop(proc: Any, grace_s: float) -> None:
    """End the child the way ``timeout -k`` does: SIGTERM, wait ``grace_s``, then SIGKILL."""
    if proc.is_alive():
        proc.terminate()
        proc.join(grace_s)
    if proc.is_alive():
        proc.kill()
        proc.join()


def _end_unfinished(
    *,
    exc: Exception,
    started_at: datetime | None,
    asof: date | None,
    runs_dir: Path | None,
    notifier: Any,
) -> None:
    """The parent's half of ending a pass that did not finish: re-mark its start artifact ``crashed`` (only
    if it still reads ``running``) and send ONE crash page. Both fail-open — reporting a dead pass must never
    raise over the error being reported."""
    from pipeline.cron_run_log import mark_run_crashed
    from pipeline.daily import _crash_label, _page_crash

    if started_at is not None:
        try:
            mark_run_crashed(
                started_at=started_at,
                crashed_at=datetime.now(timezone.utc),
                error=_crash_label(exc),
                base_dir=runs_dir,
            )
        except Exception:  # noqa: BLE001 — mark_run_crashed is fail-open already; belt and braces
            _log.exception("could not mark the stopped pass crashed")
    try:
        from notify import get_notifier

        _page_crash(notifier or get_notifier(), asof, exc)
    except Exception:  # noqa: BLE001 — the page is best-effort
        _log.exception("could not page the stopped pass")


def run_bounded(
    body: Callable[..., dict],
    args: tuple = (),
    *,
    timeout_s: float,
    grace_s: float = KILL_GRACE_S,
    runs_dir: Path | None = None,
    notifier: Any = None,
    clock: Callable[[], float] = monotonic,
) -> dict:
    """Run ``body(*args, on_started=...)`` in a spawned child process for at most ``timeout_s`` seconds
    (from launch, like the sidecar's ``timeout``). ``body`` must be importable by the child (a module-level
    function). ``runs_dir`` is where the start artifact lives (``None`` = the run-log default — the same
    one the child's pass writes to). See the module docstring for what a timeout does."""
    ctx = multiprocessing.get_context("spawn")
    recv, send = ctx.Pipe(duplex=False)
    proc = ctx.Process(
        target=_child_main, args=(send, body, args), name="alphadeck-admin-pass", daemon=True
    )
    proc.start()
    send.close()  # the parent's copy of the write end: the child's exit then reads as EOF
    deadline = clock() + timeout_s
    started_at: datetime | None = None
    asof: date | None = None
    try:
        while True:
            remaining = deadline - clock()
            if remaining <= 0:
                break
            if not recv.poll(min(remaining, _POLL_S)):
                continue
            try:
                msg = recv.recv()
            except EOFError:
                proc.join(grace_s)
                code = proc.exitcode
                exc: Exception = PassDied(
                    f"the pass process exited (code {code}) before it finished — {_aftermath(started_at)}"
                )
                _end_unfinished(
                    exc=exc, started_at=started_at, asof=asof, runs_dir=runs_dir, notifier=notifier
                )
                raise exc from None
            kind = msg[0]
            if kind == "started":
                started_at = datetime.fromisoformat(msg[1])
                asof = date.fromisoformat(msg[2])
            elif kind == "done":
                proc.join(grace_s)
                return msg[1]
            elif kind == "refused":
                proc.join(grace_s)
                raise PassAlreadyRunning(msg[1])
            else:  # "failed" — the pass's own crash path already paged and marked the artifact
                proc.join(grace_s)
                raise PassFailed(f"{msg[1]}: {msg[2]}" if msg[2] else msg[1])
        _stop(proc, grace_s)
        exc = PassTimedOut(
            f"the pass ran past the run timeout ({timeout_s:g} s) and was stopped — "
            f"{_aftermath(started_at)}"
        )
        _end_unfinished(
            exc=exc, started_at=started_at, asof=asof, runs_dir=runs_dir, notifier=notifier
        )
        raise exc
    finally:
        recv.close()
        if proc.is_alive():  # an unexpected exit from the loop: never leave a pass running
            _stop(proc, grace_s)
