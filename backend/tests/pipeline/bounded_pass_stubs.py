"""Stub pass BODIES for ``tests/pipeline/test_bounded_pass.py``.

Module-level on purpose: ``run_bounded`` runs its body in a SPAWNED child process, which imports the body by
module and name, so a stub defined inside a test function could not reach the child. Each takes the real
``on_started`` hook the runner hands it. What they touch is REAL: ``acquire_pass_lock`` takes the actual
call-of-record advisory lock on the test DB (the child inherits ``DATABASE_URL`` from the test process), and
``write_run_start`` writes the actual M1 start artifact — so the tests can watch the lock and the artifact,
not a stand-in for either. Only the pass's WORK is stubbed (no real 90-minute run).
"""

from __future__ import annotations

import os
import signal
import time
from datetime import date, datetime, timezone
from pathlib import Path

ASOF = date(2026, 10, 16)


def _start(runs_dir: str, on_started):
    """What ``run_daily_pass`` does first: take the lock, write the start artifact, report it."""
    from pipeline.cron_run_log import write_run_start
    from pipeline.run_lock import acquire_pass_lock

    lock = acquire_pass_lock(holder="manual")
    started = datetime.now(timezone.utc)
    write_run_start(
        asof=ASOF, allow_live=True, started_at=started, base_dir=Path(runs_dir), run_kind="manual"
    )
    on_started(started, ASOF)
    return lock


def hang_holding_the_lock(runs_dir: str, *, on_started) -> dict:
    """A pass that HANGS mid-run while holding the lock (a black-holed socket, a stuck query)."""
    _lock = _start(runs_dir, on_started)  # held until the process is stopped
    time.sleep(3600)
    return {}


def hang_before_starting(runs_dir: str, *, on_started) -> dict:
    """A pass stuck before it ever held the lock (MEASURED shape: a connect to an unreachable DB with no
    connect timeout hangs) — nothing to mark when it is stopped."""
    time.sleep(3600)
    return {}


def hang_ignoring_sigterm(runs_dir: str, *, on_started) -> dict:
    """The case ``timeout -k`` exists for: a process the first signal does not stop (POSIX only)."""
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    return hang_holding_the_lock(runs_dir, on_started=on_started)


def finish(runs_dir: str, *, on_started) -> dict:
    """A pass that finishes in time: its payload comes back to the parent."""
    on_started(datetime.now(timezone.utc), ASOF)
    return {"mode": "live", "theses": 1, "runs_dir": runs_dir}


def raise_inside(runs_dir: str, *, on_started) -> dict:
    """A pass that raises (its own H3 path pages and marks itself in a real run)."""
    raise RuntimeError("db exploded")


def refused(runs_dir: str, *, on_started) -> dict:
    """Another process held the lock when this one tried to take it."""
    from pipeline.run_lock import PassAlreadyRunning

    raise PassAlreadyRunning("pid 7 (alphadeck-pass:cron) since 22:30")


def die_mid_pass(runs_dir: str, *, on_started) -> dict:
    """A pass process killed from outside after it started (the OOM killer): no Python cleanup runs."""
    _lock = _start(runs_dir, on_started)  # still held when the process ends
    os._exit(3)
