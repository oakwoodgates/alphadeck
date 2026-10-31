"""``is_scheduled_day`` — the ONE session calendar: Mon-Fri minus the NYSE's full-day closures.

**Why this lives in ``domain/``.** Every layer that asks "is this a market session?" reads it:
``calls/assembler.py`` counts the trading sessions left in an armed member's entry window
(``entry_window_closing``, Q14), ``pipeline/schedule.py`` gates the cron (and through its
``is-session`` CLI the shell sidecar that actually fires the pass), ``domain/splits.py`` measures a
split's session distance, ``replay/harness.trading_sessions`` pages the editions and the backtest, the
price leg withholds a fresh bar dated a closed day and ``signals/base.window_prices`` and the scoring reads
(``scoreboard/prices.py``, ``replay/scoring.py``) skip a stored one. NOT read by
``pipeline/night_coverage.next_edgar_open``: the on-time window ends at EDGAR's open, which follows SEC
business days (EDGAR is open on Good Friday), so it stays plain Mon-Fri, holidays included. ``pipeline`` depends on
``calls``, never the reverse, so the predicate cannot live in ``pipeline/`` and be read by the assembler
(``tests/calls/test_layering.py`` pins that). It sits here, beside ``market_time.py``, with one
definition; ``pipeline/schedule.py`` imports it, so every ``from pipeline.schedule import
is_scheduled_day`` caller is unchanged.

**The calendar (F5, operator rule 2026-10-31 — "market holidays are not sessions").** A day is a session
when it is Monday to Friday AND not in ``NYSE_CLOSURES``: the NYSE's regular holidays plus its special
full-day closures (the 2025-01-09 national day of mourning). The list is HAND-KEPT from the NYSE's own
published calendars (nyse.com/markets/hours-calendars for 2026-2028; the ICE press releases "NYSE Group
Announces ... Holiday and Early Closings Calendar" for 2020-2025; the ICE release of 2024-12-30 for the
2025-01-09 closure) and covers ``COVERAGE_START`` through ``COVERAGE_END``. Before it existed, a holiday
counted as a session: the nightly ran on Labor Day 2026 (11 call rows for a night with no session) and
``entry_window_closing`` came on one session late across a holiday.

**Outside the covered range** a date falls back to plain Mon-Fri (a holiday there is not known). That is
never silent for long: ``tests/domain/test_trading_days.py`` FAILS once the coverage ends within 180 days
of today in market time, so the list is extended (the NYSE publishes three years ahead) well before it runs
out. The nightly never stops for it — an out-of-range day is simply treated as a session.

**What this is NOT.**

- Not a half-day calendar: an early close (13:00 ET, e.g. the day after Thanksgiving, some Christmas Eves
  and July 3rds) IS a session here — it trades and settles a bar; only FULL-day closures are listed. The
  settle guard holds a half-day bar until the same 18:00 settle time (``ingest/prices/settle.py``).
- Not EDGAR's calendar: SEC's filing days follow the federal holidays, which differ from the NYSE's (EDGAR
  accepts filings on Good Friday; the NYSE trades on Columbus and Veterans Day while EDGAR is closed).
  This module answers "is there a market session", nothing about filings.
- Not ``market_time.market_today``, which answers "what day is it in market time" and deliberately does no
  calendar logic at all (a Saturday is a Saturday; a holiday is that holiday's date).

Pure: a date in, a bool out — no clock, no settings, no I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, timedelta
from types import MappingProxyType

# The NYSE's full-day closures, every one checked against the NYSE's published calendar (see the module
# docstring for the sources). Saturday/Sunday holidays are listed by the WEEKDAY the exchange observed;
# a Saturday New Year's Day is NOT observed on the Friday before (NYSE Rule 7.2: 2021-12-31 and
# 2027-12-31 traded). Juneteenth starts in 2022. Append-only in spirit: extend the range, never rewrite
# a past date (a past call row was judged against it).
_CLOSURES: dict[date, str] = {
    # 2020
    date(2020, 1, 1): "New Year's Day",
    date(2020, 1, 20): "Martin Luther King, Jr. Day",
    date(2020, 2, 17): "Washington's Birthday",
    date(2020, 4, 10): "Good Friday",
    date(2020, 5, 25): "Memorial Day",
    date(2020, 7, 3): "Independence Day (observed)",
    date(2020, 9, 7): "Labor Day",
    date(2020, 11, 26): "Thanksgiving Day",
    date(2020, 12, 25): "Christmas Day",
    # 2021
    date(2021, 1, 1): "New Year's Day",
    date(2021, 1, 18): "Martin Luther King, Jr. Day",
    date(2021, 2, 15): "Washington's Birthday",
    date(2021, 4, 2): "Good Friday",
    date(2021, 5, 31): "Memorial Day",
    date(2021, 7, 5): "Independence Day (observed)",
    date(2021, 9, 6): "Labor Day",
    date(2021, 11, 25): "Thanksgiving Day",
    date(2021, 12, 24): "Christmas Day (observed)",
    # 2022 (New Year's Day fell on a Saturday: not observed)
    date(2022, 1, 17): "Martin Luther King, Jr. Day",
    date(2022, 2, 21): "Washington's Birthday",
    date(2022, 4, 15): "Good Friday",
    date(2022, 5, 30): "Memorial Day",
    date(2022, 6, 20): "Juneteenth National Independence Day (observed)",
    date(2022, 7, 4): "Independence Day",
    date(2022, 9, 5): "Labor Day",
    date(2022, 11, 24): "Thanksgiving Day",
    date(2022, 12, 26): "Christmas Day (observed)",
    # 2023
    date(2023, 1, 2): "New Year's Day (observed)",
    date(2023, 1, 16): "Martin Luther King, Jr. Day",
    date(2023, 2, 20): "Washington's Birthday",
    date(2023, 4, 7): "Good Friday",
    date(2023, 5, 29): "Memorial Day",
    date(2023, 6, 19): "Juneteenth National Independence Day",
    date(2023, 7, 4): "Independence Day",
    date(2023, 9, 4): "Labor Day",
    date(2023, 11, 23): "Thanksgiving Day",
    date(2023, 12, 25): "Christmas Day",
    # 2024
    date(2024, 1, 1): "New Year's Day",
    date(2024, 1, 15): "Martin Luther King, Jr. Day",
    date(2024, 2, 19): "Washington's Birthday",
    date(2024, 3, 29): "Good Friday",
    date(2024, 5, 27): "Memorial Day",
    date(2024, 6, 19): "Juneteenth National Independence Day",
    date(2024, 7, 4): "Independence Day",
    date(2024, 9, 2): "Labor Day",
    date(2024, 11, 28): "Thanksgiving Day",
    date(2024, 12, 25): "Christmas Day",
    # 2025
    date(2025, 1, 1): "New Year's Day",
    date(2025, 1, 9): "National Day of Mourning for President Jimmy Carter (special closure)",
    date(2025, 1, 20): "Martin Luther King, Jr. Day",
    date(2025, 2, 17): "Washington's Birthday",
    date(2025, 4, 18): "Good Friday",
    date(2025, 5, 26): "Memorial Day",
    date(2025, 6, 19): "Juneteenth National Independence Day",
    date(2025, 7, 4): "Independence Day",
    date(2025, 9, 1): "Labor Day",
    date(2025, 11, 27): "Thanksgiving Day",
    date(2025, 12, 25): "Christmas Day",
    # 2026
    date(2026, 1, 1): "New Year's Day",
    date(2026, 1, 19): "Martin Luther King, Jr. Day",
    date(2026, 2, 16): "Washington's Birthday",
    date(2026, 4, 3): "Good Friday",
    date(2026, 5, 25): "Memorial Day",
    date(2026, 6, 19): "Juneteenth National Independence Day",
    date(2026, 7, 3): "Independence Day (observed)",
    date(2026, 9, 7): "Labor Day",
    date(2026, 11, 26): "Thanksgiving Day",
    date(2026, 12, 25): "Christmas Day",
    # 2027
    date(2027, 1, 1): "New Year's Day",
    date(2027, 1, 18): "Martin Luther King, Jr. Day",
    date(2027, 2, 15): "Washington's Birthday",
    date(2027, 3, 26): "Good Friday",
    date(2027, 5, 31): "Memorial Day",
    date(2027, 6, 18): "Juneteenth National Independence Day (observed)",
    date(2027, 7, 5): "Independence Day (observed)",
    date(2027, 9, 6): "Labor Day",
    date(2027, 11, 25): "Thanksgiving Day",
    date(2027, 12, 24): "Christmas Day (observed)",
    # 2028 (New Year's Day falls on a Saturday: not observed)
    date(2028, 1, 17): "Martin Luther King, Jr. Day",
    date(2028, 2, 21): "Washington's Birthday",
    date(2028, 4, 14): "Good Friday",
    date(2028, 5, 29): "Memorial Day",
    date(2028, 6, 19): "Juneteenth National Independence Day",
    date(2028, 7, 4): "Independence Day",
    date(2028, 9, 4): "Labor Day",
    date(2028, 11, 23): "Thanksgiving Day",
    date(2028, 12, 25): "Christmas Day",
}

#: date -> the closure's name, read-only. Every date is a weekday inside the covered range.
NYSE_CLOSURES: Mapping[date, str] = MappingProxyType(_CLOSURES)

#: The first and last day the closure list speaks for (inclusive). Outside it, Mon-Fri is the fallback.
COVERAGE_START = date(2020, 1, 1)
COVERAGE_END = date(2028, 12, 31)


def closure_name(d: date) -> str | None:
    """The NYSE full-day closure on ``d`` (its name), or ``None`` — a weekend is not a closure here."""
    return _CLOSURES.get(d)


def is_scheduled_day(d: date) -> bool:
    """Is ``d`` a market session? Mon-Fri and not an NYSE full-day closure (a half-day close IS a session).
    The cron's gate (the shell asks it through ``python -m pipeline.schedule is-session``) and the session
    count behind ``entry_window_closing``. Outside ``COVERAGE_START..COVERAGE_END`` it is plain Mon-Fri.
    """
    return d.weekday() < 5 and d not in _CLOSURES


def why_not_a_session(d: date) -> str | None:
    """``None`` for a session, else the plain reason: ``"a weekend"`` or ``"a market holiday (<name>)"``."""
    if d.weekday() >= 5:
        return "a weekend"
    name = _CLOSURES.get(d)
    return None if name is None else f"a market holiday ({name})"


def next_scheduled_day(d: date) -> date:
    """The first session strictly AFTER ``d``."""
    n = d + timedelta(days=1)
    while not is_scheduled_day(n):
        n += timedelta(days=1)
    return n


def is_first_session_of_week(d: date) -> bool:
    """Is ``d`` a session with no earlier session in its Monday-to-Sunday week? Monday normally; Tuesday
    after a Monday holiday (a weekly job keyed to "Monday's pass" would otherwise skip that week).
    """
    if not is_scheduled_day(d):
        return False
    monday = d - timedelta(days=d.weekday())
    return not any(is_scheduled_day(monday + timedelta(days=i)) for i in range(d.weekday()))
