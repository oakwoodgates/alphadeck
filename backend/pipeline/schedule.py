"""Pure Mon-Fri + RUN_AT schedule math — "when was the daily cron last EXPECTED to have run?"

The schedule's source of truth was SHELL-ONLY at first (``scripts/daily_cron.sh``: sleep until RUN_AT in
the container's TZ, Mon-Fri, plus the R6 catch-up). The admin freshness read needs the same contract in
Python, so this module replicates it as PURE functions (``now``/``run_at`` injected — no ambient clock, no
env read) that the status endpoint and its tests share.

Two deliberate modeling choices:

- **The TRIGGER's calendar, not the exchange's.** The cron fires Mon-Fri INCLUDING market holidays (a
  holiday run is an idempotent near-no-op), so "expected" here is Mon-Fri too. A Monday-holiday evening
  therefore expects a run — which the trigger genuinely fires — and a weekend never expects one, so a
  Friday-dated record read on a Monday morning is NOT stale (the don't-cry-wolf case the spec names).
- **MARKET time, resolved by the caller.** The routers pass ``domain/market_time.market_now()`` (an
  explicit ``ZoneInfo``, no longer the container's ambient clock) plus the RUN_AT wall time. No TZ math
  lives here — and the injected ``now`` may be aware or naive, since ``.time()`` drops the tzinfo, so the
  RUN_AT comparison stays naive-vs-naive either way.

EARMARK (consultant watch-item): this is still the SECOND home of the Mon-Fri + RUN_AT contract — the
shell's sleep-loop is the first, and ``market_today()`` does NOT unify them (it answers "what day is it in
market time", deliberately doing NO trading-calendar logic — no weekend skip, no holidays). The Mon-Fri
schedule math belongs HERE; the remaining consolidation is shrinking the shell to a dumb trigger. Until
then keep the two in step.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta


def parse_run_at(raw: str) -> time:
    """``"HH:MM"`` -> ``time`` (the sidecar's RUN_AT format, e.g. ``"22:30"``). Raises ``ValueError`` on
    garbage — a malformed ``ALPHADECK_CRON_AT`` is a deploy error that must be LOUD, never a silently
    wrong schedule read."""
    return time.fromisoformat(raw.strip())


def is_scheduled_day(d: date) -> bool:
    """The cron's Mon-Fri gate (the shell's ``date +%u <= 5``) — holidays included, weekends out."""
    return d.weekday() < 5


def last_expected_asof(now: datetime, run_at: time) -> date:
    """The most recent as-of whose scheduled run should have ALREADY fired by ``now``: today, once
    ``run_at`` has passed on a weekday; else the most recent prior weekday. Total — always a date.
    """
    d = now.date()
    if is_scheduled_day(d) and now.time() >= run_at:
        return d
    d -= timedelta(days=1)
    while not is_scheduled_day(d):
        d -= timedelta(days=1)
    return d


def expected_runs_behind(edge: date | None, expected: date) -> int | None:
    """How many SCHEDULED runs (weekdays in ``(edge, expected]``) the record edge has missed. ``0`` =
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
