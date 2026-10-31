"""Pure session + RUN_AT schedule math — "when was the daily cron last EXPECTED to have run?"

The schedule's source of truth was SHELL-ONLY at first (``scripts/daily_cron.sh``: sleep until RUN_AT in
the container's TZ, Mon-Fri, plus the R6 catch-up). The admin freshness read needs the same contract in
Python, so this module replicates it as PURE functions (``now``/``run_at`` injected — no ambient clock, no
env read) that the status endpoint and its tests share.

Two deliberate modeling choices:

- **The exchange's session calendar, and the trigger reads the same one (F5, 2026-10-31).** A night is
  scheduled when its date is a market session: Mon-Fri minus the NYSE's full-day closures
  (``domain.trading_days.is_scheduled_day``). Until F5 the trigger fired Mon-Fri INCLUDING holidays, so a
  holiday evening expected a run and got one (Labor Day 2026: 11 call rows for a night with no session).
  Now a holiday expects no run — so a Wednesday-dated record read on Thanksgiving Friday morning is NOT
  stale — and the shell gets its answer from here (``python -m pipeline.schedule is-session D``, below)
  instead of keeping a copy. A weekend never expects one, so a Friday-dated record read on a Monday morning
  is NOT stale either (the don't-cry-wolf case the spec names).
- **MARKET time, resolved by the caller.** The routers pass ``domain/market_time.market_now()`` (an
  explicit ``ZoneInfo``, no longer the container's ambient clock) plus the RUN_AT wall time. No TZ math
  lives here — and the injected ``now`` may be aware or naive, since ``.time()`` drops the tzinfo, so the
  RUN_AT comparison stays naive-vs-naive either way.

EARMARK (consultant watch-item): this is still the SECOND home of the RUN_AT loop — the shell's sleep-loop
is the first, and ``market_today()`` does NOT unify them (it answers "what day is it in market time",
deliberately doing NO trading-calendar logic — no weekend skip, no holidays). The CALENDAR is no longer
duplicated: the session predicate ``is_scheduled_day`` is defined once in ``domain/trading_days.py``
(imported here, so the call path can share it without importing ``pipeline``), and since F5 the shell's
``is_session`` asks it through this module's CLI — exit 0 = a session, 1 = not, anything else = the shell's
fail-safe Mon-Fri fallback plus one loud log line. What remains duplicated is the loop shape: the shell's
``next_session`` / ``last_expected_asof`` mirror ``last_expected_asof`` here, and since 2026-09-10 the
shell's BOOT path targets ``last_expected_asof(now)`` too (the last expected night — exactly one, never
older), the same function its late-wake catch-up window closes on.

THE TARGET-AT-SCHEDULE-TIME CONTRACT (shared with the shell): the as-of a scheduled run is FOR is fixed
when the run is scheduled, never re-read when it fires. On a laptop the sidecar's ``sleep`` overshoots by
however long the host was suspended (measured fires at 23:37, 00:24 and 09:09 the next morning), and a
run that derived ``asof`` at fire time recorded the NEXT day, leaving the intended night with no
call-of-record — a hole the edge check (``expected_runs_behind``, MAX(asof) only) could never see. The
shell passes ``--asof <target>`` and catches up the weekdays a long sleep also skipped; ``missed_asofs``
here is the hole-aware read that makes such a night VISIBLE on the admin surface.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Collection, Sequence
from datetime import date, datetime, time, timedelta

# The cron's session gate — Mon-Fri minus the NYSE's full-day closures (F5). DEFINED in
# ``domain/trading_days.py`` since Q14 (the call path counts entry-window sessions with it, and ``calls``
# must never import ``pipeline``); imported here so this module's math, the shell (through ``main`` below)
# and every ``from pipeline.schedule import is_scheduled_day`` caller read the ONE definition.
from domain.trading_days import is_scheduled_day, why_not_a_session


def parse_run_at(raw: str) -> time:
    """``"HH:MM"`` -> ``time`` (the sidecar's RUN_AT format, e.g. ``"22:30"``). Raises ``ValueError`` on
    garbage — a malformed ``ALPHADECK_CRON_AT`` is a deploy error that must be LOUD, never a silently
    wrong schedule read."""
    return time.fromisoformat(raw.strip())


def last_expected_asof(now: datetime, run_at: time) -> date:
    """The most recent as-of whose scheduled run should have ALREADY fired by ``now``: today, once
    ``run_at`` has passed on a session; else the most recent prior session. Total — always a date.
    """
    d = now.date()
    if is_scheduled_day(d) and now.time() >= run_at:
        return d
    d -= timedelta(days=1)
    while not is_scheduled_day(d):
        d -= timedelta(days=1)
    return d


def expected_runs_behind(edge: date | None, expected: date) -> int | None:
    """How many SCHEDULED runs (sessions in ``(edge, expected]``) the record edge has missed. ``0`` =
    current — a Friday edge on a Monday MORNING is 0 behind (no run was due yet; don't cry wolf over a
    weekend), the same edge Monday NIGHT is 1 behind. ``None`` when ``edge is None``: a record that never
    began has no schedule to be behind — the QUIET fresh-install state, never an alarm."""
    if edge is None:
        return None
    behind = 0
    d = expected
    while d > edge:
        if is_scheduled_day(d):
            behind += 1
        d -= timedelta(days=1)
    return behind


def scheduled_window(expected: date, window: int) -> list[date]:
    """The last ``window`` SCHEDULED days ending at ``expected`` (inclusive when it is itself a scheduled
    day), ascending — the days a hole-aware freshness read scans. ``window <= 0`` → ``[]``."""
    out: list[date] = []
    d = expected
    while len(out) < window:
        if is_scheduled_day(d):
            out.append(d)
        d -= timedelta(days=1)
    out.reverse()
    return out


def missed_asofs(
    recorded: Collection[date], *, expected: date, first: date | None, window: int
) -> list[date]:
    """The HOLES: scheduled days in the last ``window`` scheduled days ending at ``expected`` (inclusive)
    that have NO call-of-record in ``recorded``, restricted to ``d >= first`` (``first`` = the earliest
    recorded as-of, so a record that began mid-window is not "missing" its pre-history; ``None`` = the
    record never began → ``[]``, the quiet fresh-install state). Ascending. Weekends and market holidays
    are never listed (never scheduled). The edge check (``expected_runs_behind``) sees only MAX(asof) — a run that fired on
    the wrong day advances the edge right over the night it skipped; this read sees the night. When the
    expected day itself is missing it appears here AND counts as 1 behind — the caller's verdict priority
    decides the wording (stale wins)."""
    if first is None:
        return []
    return [d for d in scheduled_window(expected, window) if d >= first and d not in recorded]


# --- the shell's question: ``python -m pipeline.schedule is-session YYYY-MM-DD`` -------------------------
# The sidecar (``scripts/daily_cron.sh`` ``is_session``) cannot import the closure list, so it asks here.
# The EXIT CODE is the answer — 0 = a session, 1 = not (a weekend or a market holiday) — and one line on
# stdout says which, for a human at a prompt. Anything else (2 for a malformed date — argparse's usage
# exit — or a crash) is NOT an answer: the shell then logs one loud line and falls back to Mon-Fri, so a
# broken check never stops the nightly. Imports only ``domain.trading_days``: no DB, no settings, no clock.

SESSION = 0
NOT_A_SESSION = 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m pipeline.schedule",
        description="The session calendar the cron sidecar fires on (Mon-Fri minus NYSE full-day closures).",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser(
        "is-session",
        help="exit 0 when the date is a market session, 1 when it is not (a weekend or a market holiday)",
    )
    p.add_argument("day", type=date.fromisoformat, help="YYYY-MM-DD")
    args = parser.parse_args(argv)
    why = why_not_a_session(args.day)
    if why is None:
        print(f"{args.day}: a market session")
        return SESSION
    print(f"{args.day}: not a session ({why})")
    return NOT_A_SESSION


if __name__ == "__main__":
    sys.exit(main())
