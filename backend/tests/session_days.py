"""Bar dates for synthetic test tapes that a vendor could really have served (F5).

A vendor serves no bar on an NYSE full-day closure, and the point-in-time view skips one it finds stored
(``signals.base.window_prices``), so a synthetic tape that stores a bar on Memorial Day or Juneteenth would
read one bar short through the real view. These helpers build the dates without them — the closes stay
indexed by bar, so a test's hand-computed expectations are unchanged.
"""

from __future__ import annotations

from datetime import date, timedelta

from domain.trading_days import closure_name, is_scheduled_day


def bar_days(end: date, n: int) -> list[date]:
    """The ``n`` most recent CALENDAR days ending at ``end`` (weekends kept, as the calendar-day tapes have
    them) minus the NYSE's full-day closures, ascending."""
    out: list[date] = []
    d = end
    while len(out) < n:
        if closure_name(d) is None:
            out.append(d)
        d -= timedelta(days=1)
    out.reverse()
    return out


def last_session_through(end: date) -> date:
    """The latest market session on or before ``end``. Under F3 (a trigger is live on night N iff the first session
    after N is on or before its window end) it is the session a window ending on ``end`` is last acted in: the
    trigger is live on the night before it and not on its own night."""
    d = end
    while not is_scheduled_day(d):
        d -= timedelta(days=1)
    return d


def bar_days_from(start: date, n: int) -> list[date]:
    """The ``n`` calendar days from ``start`` on (weekends kept) minus the NYSE's full-day closures."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if closure_name(d) is None:
            out.append(d)
        d += timedelta(days=1)
    return out
