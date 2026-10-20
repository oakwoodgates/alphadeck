"""``is_scheduled_day`` — the ONE Mon-Fri session calendar, shared by the call path and the cron schedule.

**Why this lives in ``domain/``.** Two layers read it: ``calls/assembler.py`` counts the trading sessions
left in an armed member's entry window (``entry_window_closing``, Q14), and ``pipeline/schedule.py`` gates
the cron's Mon-Fri trigger. ``pipeline`` depends on ``calls``, never the reverse, so the predicate cannot
live in ``pipeline/`` and be read by the assembler (``tests/calls/test_layering.py`` pins that). It sits
here, beside ``market_time.py``, with one definition; ``pipeline/schedule.py`` imports it, so every
``from pipeline.schedule import is_scheduled_day`` caller is unchanged.

**What this is NOT.** It is not an exchange calendar: weekends are out, but market HOLIDAYS are counted as
sessions (they are not modeled). That is the cron trigger's own calendar — it fires Mon-Fri, holidays
included — and on the call path it means ``entry_window_closing`` comes on one session late when a holiday
sits inside the window's last few days (CALL_LOGIC §6). It is also not ``market_time.market_today``, which
answers "what day is it in market time" and deliberately does no calendar logic at all.

Pure: a date in, a bool out — no clock, no settings, no I/O.
"""

from __future__ import annotations

from datetime import date


def is_scheduled_day(d: date) -> bool:
    """Mon-Fri — weekends out, market holidays INCLUDED (not modeled). The cron's gate (the shell's
    ``date +%u <= 5``) and the session count behind ``entry_window_closing``."""
    return d.weekday() < 5
