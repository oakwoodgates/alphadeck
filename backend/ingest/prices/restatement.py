"""The thesis price leg's RESTATEMENT RULES (Q11, 2026-10-17) — pure: no I/O, no clock.

``ingest_security.ingest_bars_for_security`` compares a fresh vendor pull's OVERLAP with the stored tape and
appends a NEW version of a stored bar the vendor restated (source-strategy Option A — the bitemporal store's
native move). These are the rules that decide WHICH restated bars a pass writes; the I/O stays in
``ingest_security``.

**T6 — the old-bar tolerance.** The overlap splits at the trailing ``RECENT_SESSIONS`` (3) session dates of
the fresh pull — D1's own session count (``pipeline.ingest_benchmarks.plan_benchmark_bars``: "the vendor's
newest N settled session dates", so weekends and holidays need no calendar):

- a stored bar INSIDE that window keeps the exact compare (``ingest_security._restated``: rel_tol 1e-9) — the
  vendor's late volume settling lands there (MEASURED on SPY/IWM: +0.05%..+3.2%, always upward), and a
  fresh split re-bases the window with everything before it;
- an OLDER stored bar re-versions only when the close moved by at least ``CLOSE_TOL`` (0.5%) or the volume by
  at least ``VOLUME_TOL`` (5%), relative to the STORED value (``restated_old``). A stored zero or absent value
  has no relative scale, so it keeps the exact rule (``_moved``'s handling: both absent = unchanged, one
  absent = moved, a zero moved only by a nonzero). A split moves both by an integer factor, far past either
  threshold, so a genuine re-base still lands.

**T5 — revert hysteresis.** A vendor that flips a history between the split-adjusted and the unadjusted basis
(A -> B -> A) used to re-version the whole history every flip, and a flip to the unadjusted basis is a
false cliff for the breakout detector. So the OLDER restated bars are classified per (security, pass):

- a bar is a REVERT when its fresh value matches, within the T6 tolerance, a SUPERSEDED (non-latest) stored
  version of that bar — the vendor is going back to a value the store already held and replaced;
- the SET is a revert only when EVERY older restated bar is one (``classify``); one bar the store has never
  held makes it FIRST-TIME. A first-time set applies in the same pass, as before; a revert set is HELD,
  whole, until a pull on a later market date agrees (``decide`` / ``agrees``) — two consecutive nightly
  pulls. The trailing-window bars are not part of the decision: they always apply.

Why "every" bar, not "any" or "most" (MEASURED, replaying prod's member price history 2026-09-01..10-16;
``docs/FEED_LOOP.md``, price restatements): a set mixing reverts with never-seen values was as often a
GENUINE first re-base carrying a coincidental match (ARTL / BURU 09-05: 1 of 492 bars; REAX 09-05: 2 of
493) as a flip whose odd bars are cent-rounding (BURU 2025-04-22 stored 0.8059, re-served 0.81 = 0.51%).
"Any" would hold those genuine re-bases — on REAX it adds a cliff night; "most" (0.5 / 0.9) holds more bars
and moved the defect count by at most one night in thirty. Under "every", a value the store never held is
never held back, so a genuine split cannot be delayed BY CONSTRUCTION, not by a tuned fraction.

Why the window bars are not held with the set (MEASURED): holding them too was the first design, and on
LRHC 09-09 it kept a pre-split bar on the adjusted basis while its neighbors were unadjusted — a fake
``volume_breakout`` the stored tape never had. Applying them (the operator's "keep today's exact compare")
removed it, and no replayed hold had a basis change spanning both regions.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

# The trailing window that keeps the exact compare: the fresh pull's newest N session dates (D1's count).
RECENT_SESSIONS = 3
# The old-bar tolerance, relative to the stored value (operator decision Q11, 2026-10-17).
CLOSE_TOL = 0.005
VOLUME_TOL = 0.05

# The classification of a pass's OLDER restated set (``classify``).
FIRST_TIME = "first_time"
REVERT = "revert"


def _num(x: object) -> float | None:
    return None if x is None else float(x)  # numeric columns arrive as Decimal; fresh bars as float


def trailing_window_start(rows: Sequence[Mapping], sessions: int = RECENT_SESSIONS) -> date | None:
    """The FIRST date of the newest ``sessions`` session dates in ``rows`` (the fresh, settled pull) — the
    bars on or after it keep the exact compare. ``None`` when ``rows`` is empty or ``sessions <= 0`` (no
    window: every overlap bar is an older bar). The same count as D1's benchmark window: the vendor's own
    session dates, so a weekend or a holiday is simply not a date and needs no calendar."""
    if sessions <= 0:
        return None
    dates = sorted({r["d"] for r in rows})[-sessions:]
    return dates[0] if dates else None


def moved_beyond(fresh: Mapping, stored: Mapping, field: str, tol: float) -> bool:
    """Did ONE field move by at least ``tol`` relative to the STORED value? Absent / zero keep the exact
    rule ``_moved`` uses (a stored zero has no relative scale): both absent = unchanged, one absent =
    moved, a stored zero is moved only by a nonzero value."""
    a, b = _num(fresh.get(field)), _num(stored.get(field))
    if a is None and b is None:
        return False
    if a is None or b is None:
        return True
    if math.isclose(b, 0.0, abs_tol=1e-9):
        return not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-9)
    return abs(a - b) >= tol * abs(b)


def restated_old(fresh: Mapping, stored: Mapping) -> bool:
    """T6: does an OLDER overlap bar re-version? The close moved by >= ``CLOSE_TOL`` or the volume by >=
    ``VOLUME_TOL`` relative to the stored value (the two fields the detectors read; OHL cannot re-base
    without the close moving)."""
    return moved_beyond(fresh, stored, "close", CLOSE_TOL) or moved_beyond(
        fresh, stored, "volume", VOLUME_TOL
    )


def matches(fresh: Mapping, version: Mapping) -> bool:
    """Is ``fresh`` the same bar as a stored ``version``, within the T6 tolerance?"""
    return not restated_old(fresh, version)


def is_revert(fresh: Mapping, superseded: Sequence[Mapping]) -> bool:
    """Does an older restated bar go BACK to a value the store held and replaced (a superseded version)?"""
    return any(matches(fresh, v) for v in superseded)


def classify(
    old: Sequence[Mapping], superseded: Mapping[date, Sequence[Mapping]]
) -> tuple[str | None, int]:
    """``(kind, reverts)`` for one pass's OLDER restated bars: ``kind`` is ``None`` for an empty set,
    ``REVERT`` when every bar matches a superseded version of itself, else ``FIRST_TIME``; ``reverts``
    counts the bars that matched (what a log line reports)."""
    if not old:
        return None, 0
    reverts = sum(1 for r in old if is_revert(r, superseded.get(r["d"], ())))
    return (REVERT if reverts == len(old) else FIRST_TIME), reverts


@dataclass(frozen=True)
class HeldRevert:
    """The pending state of ONE held revert: the market date of the pull that saw it and the fresh close /
    volume it carried per bar date. Operational state (``price_restatement_hold``), never a fact."""

    pull_date: date
    bars: Mapping[date, Mapping]


def agrees(held: HeldRevert, old: Sequence[Mapping], *, covered: Collection[date]) -> bool:
    """Does this pass's revert set say what the held pull said — the SAME bar dates, each within the T6
    tolerance of the held value? Only the dates THIS pull still ``covered`` are compared: the vendor's
    window is a fixed range (two years), so its oldest bar drops off every night, and a whole-history flip
    would otherwise never agree with itself. A bar the held pull restated and this pull does not (or the
    reverse) is a disagreement — the vendor is still changing its mind."""
    if not old:
        return False
    expected = {d for d in held.bars if d in covered}
    if {r["d"] for r in old} != expected:
        return False
    return all(matches(r, held.bars[r["d"]]) for r in old)


@dataclass(frozen=True)
class Decision:
    """What one pass does with one security's OLDER restated bars (the trailing-window ones always apply).
    ``apply`` = write the whole older set now; else the whole set is held (``held`` counts it) — never part
    of it. ``state`` = the pending revert to keep after this pass (``None`` = clear it)."""

    apply: bool
    held: int
    state: HeldRevert | None


def decide(
    kind: str | None,
    *,
    old: Sequence[Mapping],
    pending: HeldRevert | None,
    covered: Collection[date],
    pull_date: date,
    refreshed: bool,
) -> Decision:
    """T5 for one (security, pass). ``kind`` from ``classify``; ``pending`` is the held revert carried from
    an earlier pass (or None); ``covered`` is the set of dates this pull returned; ``pull_date`` is this
    pass's market date; ``refreshed`` says the pull really re-fetched the vendor (``force_refresh`` with
    ``allow_live`` — the recurring path).

    - nothing older restated, or a FIRST-TIME set: apply it now; a pending revert is cleared (the vendor no
      longer serves it, or the store moved to a new basis).
    - a REVERT set that agrees with a pending revert from an EARLIER market date, on a refreshed pull: two
      consecutive pulls said it — apply, clear. (Two pulls on one night — a thesis loop reaching the same
      name twice, a second "Run daily now" — never confirm each other.)
    - any other REVERT: hold the whole set. A refreshed pull records what it saw, keeping the earlier pull
      date while it still agrees.

    A pull that did NOT re-fetch (the Workbench's cache-first read) re-reads a pull that already happened, so
    it never writes the state: it can neither start, confirm nor clear a hold (``state`` comes back as
    ``pending``, unchanged). The caller persists ``state`` only when it is not ``pending``."""
    if kind != REVERT:
        return Decision(apply=True, held=0, state=None if refreshed else pending)
    if pending is not None and agrees(pending, old, covered=covered):
        if refreshed and pending.pull_date < pull_date:
            return Decision(apply=True, held=0, state=None)
        return Decision(apply=False, held=len(old), state=pending)
    if not refreshed:
        return Decision(apply=False, held=len(old), state=pending)
    seen = HeldRevert(
        pull_date, {r["d"]: {"close": r["close"], "volume": r["volume"]} for r in old}
    )
    return Decision(apply=False, held=len(old), state=seen)
