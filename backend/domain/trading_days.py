"""``is_scheduled_day`` — the ONE Mon-Fri session calendar, shared by the call path and the cron schedule.

**Why this lives in ``domain/``.** Two layers read it: ``calls/assembler.py`` counts the trading sessions
left in an armed member's entry window (``entry_window_closing``, Q14), and ``pipeline/schedule.py`` gates
the cron's Mon-Fri trigger. ``pipeline`` depends on ``calls``, never the reverse, so the predicate cannot
live in ``pipeline/`` and be read by the assembler (``tests/calls/test_layering.py`` pins that). It sits
here, beside ``market_time.py``, with one definition; ``pipeline/schedule.py`` imports it, so every
``from pipeline.schedule import is_scheduled_day`` caller is unchanged.

**The schedule's two pure halves live here too (F1, 2026-10-30).** ``parse_run_at`` (the sidecar's ``"HH:MM"``)
and ``last_expected_asof`` (the most recent night whose scheduled pass should have fired) moved here from
``pipeline/schedule.py`` for the same reason the predicate did: ``domain/market_time.current_night`` builds the
app's "current night" on them, and the serve path (``serve_view``) reads it, so they cannot live in
``pipeline/``. ``pipeline/schedule.py`` imports both, so every ``from pipeline.schedule import ...`` caller is
unchanged; ``next_scheduled_day`` is the action day's step (the first session after a night).

**What this is NOT.** It is not an exchange calendar: weekends are out, but market HOLIDAYS are counted as
sessions (they are not modeled). That is the cron trigger's own calendar — it fires Mon-Fri, holidays
included — and on the call path it means ``entry_window_closing`` comes on one session late when a holiday
sits inside the window's last few days (CALL_LOGIC §6). It is also not ``market_time.market_today``, which
answers "what day is it in market time" and deliberately does no calendar logic at all.

Pure: dates and wall times in, no clock, no settings, no I/O (``now`` and ``run_at`` are always injected).
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta


def is_scheduled_day(d: date) -> bool:
    """Mon-Fri — weekends out, market holidays INCLUDED (not modeled). The cron's gate (the shell's
    ``date +%u <= 5``) and the session count behind ``entry_window_closing``."""
    return d.weekday() < 5


def parse_run_at(raw: str) -> time:
    """``"HH:MM"`` -> ``time`` (the sidecar's RUN_AT format, e.g. ``"22:30"``). Raises ``ValueError`` on
    garbage — a malformed ``ALPHADECK_CRON_AT`` is a deploy error that must be LOUD, never a silently
    wrong schedule read."""
    return time.fromisoformat(raw.strip())


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


def next_scheduled_day(d: date) -> date:
    """The first scheduled day strictly AFTER ``d`` (a Friday -> the Monday after; a holiday counts, as above)."""
    d += timedelta(days=1)
    while not is_scheduled_day(d):
        d += timedelta(days=1)
    return d
