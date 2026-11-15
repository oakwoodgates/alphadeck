"""Bounded waits for tests that race threads: a stuck thread fails the test, it never hangs the run.

A ``threading.Barrier`` with no timeout waits forever when one party never arrives (a database connection that
drops before the barrier, an exception in a worker); a bare ``Thread.join()`` waits forever on a thread that
never returns. Either one stalls the whole suite until the hang guard kills the run. Give every barrier
``timeout=BARRIER_TIMEOUT_S`` (it becomes the default for ``wait()``) and join through ``join_all``.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Sequence

BARRIER_TIMEOUT_S = 60.0  # generous for a loaded CI box; a healthy rendezvous takes milliseconds
JOIN_TIMEOUT_S = 120.0  # the whole group, after the barrier's own bound has had its turn


def join_all(
    threads: Sequence[threading.Thread],
    timeout_s: float = JOIN_TIMEOUT_S,
    what: str = "worker threads",
) -> None:
    """Join every thread within ONE shared deadline; fail (do not hang) naming the ones still running."""
    deadline = time.monotonic() + timeout_s
    for t in threads:
        t.join(max(0.0, deadline - time.monotonic()))
    stuck = [t.name for t in threads if t.is_alive()]
    assert not stuck, (
        f"{len(stuck)} of {len(threads)} {what} still running after {timeout_s:.0f}s: {stuck} "
        "(a thread never reached its barrier, or its database connection dropped)"
    )
