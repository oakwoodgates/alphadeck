"""WINDOWS — the unit of work for a pass, and how many of them run at once.

A year-long run is not a unit of work on this box. MEASURED: a 1-year, 12-thesis, public-clock run at 6
workers and K=5 did not produce a registry row in 3h40m (it was killed, not crashed — a lower bound, not a
timing). The cost model is ``export + replay/workers + nulls(serial)``, and the last term is the one that
grew: ``--workers`` covers the replay phase only, so the null draws run on the main process start to
finish.

That is what makes the SUB-WINDOW the unit. Splitting a year into N disjoint windows and launching them as
N SEPARATE PROCESSES is what parallelizes the nulls — each job owns its own serial null phase — and it
costs nothing in fidelity, because the mirror is the whole tape regardless of window (``export_snapshot``
takes no date bound), so every window of every point can share ONE frozen export.

It also changes what a curve point IS, for the better: a point becomes the POOLED read across its windows,
and the per-window deltas become cross-WINDOW sign agreement. A dial that helps in one six-week window and
hurts in the next has found nothing, and that was already the question ``_sub_bounds`` was asking of one
long run — now it is asked of runs that are genuinely separate measurements.
"""

from __future__ import annotations

import os
from datetime import date, timedelta

#: Six weeks. Not a tuning constant with a theory behind it — it is the size the operator's first split
#: used, and it is short enough that a window job finishes in a sitting on this box. The honest caveat
#: travels with it: a six-week window holds roughly 30 trading sessions, which is where the timing null
#: starts to run out of alternative entry dates to draw from (see the short-window caveat on the surface).
DEFAULT_WINDOW_DAYS = 42

#: MEASURED on this box: six replay workers bought ~2.8 usable cores, and the year run sustained ~2.8.
#: So two concurrent window jobs saturate it and three is already oversubscribed — the default is 2, never
#: 6. The jobs contend on CPU only: the database is touched once, by the shared export, before any job
#: starts.
MEASURED_USABLE_CORES = 2.8


def tile(start: date, end: date, days: int = DEFAULT_WINDOW_DAYS) -> list[tuple[date, date]]:
    """``[start, end]`` as DISJOINT, contiguous windows of at most ``days`` each.

    Contiguous and gapless so the windows together are exactly the span the pass claims to cover — a
    reader must never have to wonder which weeks fell between two points. The LAST window absorbs the
    remainder rather than being dropped or padded: dropping it would silently shorten the pass, and
    padding it past ``end`` would price days the pass did not claim."""
    if days <= 0:
        raise ValueError("a window must be at least one day long")
    out: list[tuple[date, date]] = []
    cursor = start
    while cursor <= end:
        last = min(cursor + timedelta(days=days - 1), end)
        # the tail is absorbed rather than left as a stub: a final window shorter than half the others has
        # too few sessions for its own delta to mean much, and a stub point in a curve reads as a result
        if (end - last).days < days // 2:
            last = end
        out.append((cursor, last))
        cursor = last + timedelta(days=1)
    return out


def default_concurrency() -> int:
    """How many window jobs to run at once. Two on this box, and the reasoning is MEASURED rather than
    guessed: ~2.8 usable cores, and each job is a whole `backtest.run` whose replay phase may itself fan
    out. Capped at 3 regardless of what a bigger machine reports, because the numbers this pass produces
    are compared against each other and a pass that saturates its box measures contention as well as
    dials."""
    cpus = os.cpu_count() or 2
    return max(1, min(3, int(MEASURED_USABLE_CORES), cpus - 1))
