"""F5 — the ONE session calendar (``domain/trading_days.py``): Mon-Fri minus the NYSE's full-day closures.

Pure (no DB, no clock) except the freshness guard, which reads ``market_today()`` ON PURPOSE (its docstring).
The hand-kept list is checked two ways: an independent re-derivation of the NYSE's holiday RULES (nth-weekday
holidays, Easter for Good Friday, the weekend-observance rule including "a Saturday New Year's Day is not
observed") must reproduce every regular closure, and the one special closure in range (2025-01-09) is pinned
by name. The published calendars the dates were read from are named in the module docstring.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from domain.market_time import market_today
from domain.trading_days import (
    COVERAGE_END,
    COVERAGE_START,
    NYSE_CLOSURES,
    closure_name,
    is_first_session_of_week,
    is_scheduled_day,
    next_scheduled_day,
    why_not_a_session,
)

# the special (non-rule) full-day closures inside the covered range — each a separate NYSE announcement
SPECIAL_CLOSURES = {date(2025, 1, 9)}  # national day of mourning, President Jimmy Carter

FRESHNESS_DAYS = 180


# --- an independent re-derivation of the NYSE's regular holiday rules ---------------------------------------


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    d = date(year, month, 1)
    d += timedelta(days=(weekday - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def _last_weekday(year: int, month: int, weekday: int) -> date:
    d = date(year, month + 1, 1) - timedelta(days=1)
    return d - timedelta(days=(d.weekday() - weekday) % 7)


def _easter(year: int) -> date:
    """The Gregorian Easter Sunday (the anonymous / Meeus algorithm)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l_ = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_) // 451
    month = (h + l_ - 7 * m + 114) // 31
    day = (h + l_ - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def _observed(d: date) -> date:
    """A Saturday holiday is observed the Friday before, a Sunday one the Monday after."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _rule_closures(year: int) -> set[date]:
    out = {
        _nth_weekday(year, 1, 0, 3),  # Martin Luther King, Jr. Day
        _nth_weekday(year, 2, 0, 3),  # Washington's Birthday
        _easter(year) - timedelta(days=2),  # Good Friday
        _last_weekday(year, 5, 0),  # Memorial Day
        _nth_weekday(year, 9, 0, 1),  # Labor Day
        _nth_weekday(year, 11, 3, 4),  # Thanksgiving Day
    }
    for fixed in (date(year, 7, 4), date(year, 12, 25)):
        out.add(_observed(fixed))
    if year >= 2022:  # Juneteenth, first observed by the NYSE in 2022
        out.add(_observed(date(year, 6, 19)))
    new_year = date(year, 1, 1)
    if new_year.weekday() == 6:
        out.add(new_year + timedelta(days=1))
    elif new_year.weekday() < 5:
        out.add(new_year)
    # a SATURDAY New Year's Day is not observed (NYSE Rule 7.2): no closure on the Friday before
    return out


def test_every_regular_closure_matches_the_nyse_rules_and_only_the_named_special_one_is_extra():
    derived: set[date] = set()
    for year in range(COVERAGE_START.year, COVERAGE_END.year + 1):
        derived |= _rule_closures(year)
    listed = set(NYSE_CLOSURES)
    assert listed - derived == SPECIAL_CLOSURES
    assert derived - listed == set()


def test_the_list_is_weekdays_inside_its_range_with_a_name_each():
    assert COVERAGE_START == date(2020, 1, 1) and COVERAGE_END >= date(2027, 12, 31)
    for d, name in NYSE_CLOSURES.items():
        assert d.weekday() < 5, d
        assert COVERAGE_START <= d <= COVERAGE_END, d
        assert name.strip(), d
    per_year = {y: sum(1 for d in NYSE_CLOSURES if d.year == y) for y in range(2020, 2029)}
    # 9 before Juneteenth; 2022 and 2028 lose New Year's Day to a Saturday; 2025 adds the Carter closure
    assert per_year == {
        2020: 9,
        2021: 9,
        2022: 9,
        2023: 10,
        2024: 10,
        2025: 11,
        2026: 10,
        2027: 10,
        2028: 9,
    }


@pytest.mark.parametrize(
    "d, session",
    [
        (date(2026, 9, 7), False),  # Labor Day — the nightly ran on it before F5 (11 call rows)
        (date(2026, 11, 26), False),  # Thanksgiving
        (date(2026, 11, 27), True),  # the day after: a 13:00 HALF-DAY close is a session
        (date(2026, 12, 24), True),  # Christmas Eve 2026: a half-day, a session
        (date(2026, 7, 3), False),  # Independence Day observed (the 4th is a Saturday)
        (date(2026, 4, 3), False),  # Good Friday (EDGAR is open; the market is not)
        (date(2025, 1, 9), False),  # the special closure
        (date(2021, 12, 31), True),  # New Year's Day 2022 fell on a Saturday: NOT observed
        (date(2027, 12, 31), True),  # likewise 2028
        (date(2026, 10, 12), True),  # Columbus Day: a federal holiday, the market trades
        (date(2026, 11, 11), True),  # Veterans Day: likewise
        (date(2026, 10, 31), False),  # a Saturday
        (date(2026, 11, 1), False),  # a Sunday
        (date(2026, 10, 30), True),  # an ordinary Friday
    ],
)
def test_is_scheduled_day(d, session):
    assert is_scheduled_day(d) is session


def test_outside_the_covered_range_it_is_plain_mon_to_fri():
    # a holiday outside the list is not known — Mon-Fri is the fallback (the freshness test below keeps the
    # list ahead of today so this never applies to a live night)
    assert is_scheduled_day(date(2019, 11, 28)) is True  # Thanksgiving 2019, before the list
    assert is_scheduled_day(date(2019, 11, 30)) is False  # a Saturday is still a weekend


def test_why_not_a_session_and_closure_name():
    assert why_not_a_session(date(2026, 10, 30)) is None
    assert why_not_a_session(date(2026, 10, 31)) == "a weekend"
    assert why_not_a_session(date(2026, 11, 26)) == "a market holiday (Thanksgiving Day)"
    assert closure_name(date(2026, 9, 7)) == "Labor Day"
    assert closure_name(date(2026, 10, 31)) is None  # a weekend is not a closure


def test_next_scheduled_day_skips_weekends_and_holidays():
    assert next_scheduled_day(date(2026, 10, 30)) == date(2026, 11, 2)  # Fri -> Mon
    assert next_scheduled_day(date(2026, 11, 25)) == date(
        2026, 11, 27
    )  # Wed -> Fri over Thanksgiving
    assert next_scheduled_day(date(2026, 9, 4)) == date(2026, 9, 8)  # Fri -> Tue over Labor Day
    assert next_scheduled_day(date(2026, 12, 24)) == date(
        2026, 12, 28
    )  # over Christmas + a weekend


def test_first_session_of_week():
    assert is_first_session_of_week(date(2026, 10, 26)) is True  # a Monday
    assert is_first_session_of_week(date(2026, 10, 27)) is False
    assert is_first_session_of_week(date(2026, 9, 7)) is False  # Labor Day: no session
    assert is_first_session_of_week(date(2026, 9, 8)) is True  # the Tuesday after it
    assert is_first_session_of_week(date(2026, 9, 9)) is False
    assert is_first_session_of_week(date(2026, 10, 31)) is False  # a Saturday


def test_the_closure_list_covers_at_least_180_days_past_today():
    """FRESHNESS GUARD — DELIBERATELY DATE-DEPENDENT. It reads ``market_today()`` and FAILS once the hand-kept
    closure list ends within 180 days of it: extend ``domain/trading_days.py`` (the NYSE publishes its
    calendar three years ahead at nyse.com/markets/hours-calendars) and move ``COVERAGE_END``. Until then an
    uncovered holiday would silently count as a session — the nightly would run on it again, as it did on
    Labor Day 2026. A red run here on an untouched branch means the calendar is due, not that the code broke.
    """
    today = market_today()
    assert COVERAGE_END - today >= timedelta(days=FRESHNESS_DAYS), (
        f"the NYSE closure list ends {COVERAGE_END}, within {FRESHNESS_DAYS} days of {today}: "
        "add the next year's closures to domain/trading_days.py and move COVERAGE_END"
    )
    assert max(NYSE_CLOSURES) > today  # and it still lists at least one closure ahead
