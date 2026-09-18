"""WINDOWS — the unit of work for a pass, and how many of them run at once.

A pass tiles its span into disjoint, gapless windows and runs every point once per window. Three things
make that the right unit, and none of them is speed:

* **Cross-window agreement.** A point's metric is pooled across its windows and its delta is recomputed on
  each, so "does this dial help" is asked of separate measurements rather than of slices of one run. A
  dial that helps in one six-week window and hurts in the next is visibly unstable, and that is the
  question the whole sweep exists to answer.
* **Blast radius.** A job that dies costs about 90 seconds, not an hour. MEASURED, and the reason the unit
  changed at all: a 1-year, 12-thesis, public-clock run at 6 workers and K=5 did not produce a registry
  row in 3h40m before it was killed.
* **Bounded memory.** Each job holds one window's episodes and its own price-tape cache.

**Concurrency is NOT one of the three.** The split was designed when the null phase was serial and 96% of
a run, and separate processes were the only way to parallelize it; after the tape memo (M1) the nulls are
~7 s and the replay dominates, and the replay already fans out over its own workers. Running two window
jobs at once still helps a little — one job's wall clock is its LARGEST thesis, so its workers idle near
the end and a second job fills that tail — but the box is ~2.8 usable cores either way, so expect a
fraction, not a factor. The history of that reversal, with the numbers, is in `docs/BACKTEST.md`.

One thing the split costs nothing in: the mirror is the whole tape regardless of window
(``export_snapshot`` takes no date bound), so ONE export serves every window of every point.
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
