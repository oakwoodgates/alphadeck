"""The pure Mon-Fri + RUN_AT schedule math (no DB, no clock — now/run_at injected). The load-bearing
cases are the spec's don't-cry-wolf pair: a Friday edge on a Monday MORNING is 0 behind (not stale),
the same edge Monday NIGHT is 1 behind (stale). 2026-07-17 is a Friday; 07-20 the following Monday.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from pipeline.schedule import (
    expected_runs_behind,
    is_scheduled_day,
    last_expected_asof,
    missed_asofs,
    parse_run_at,
    scheduled_window,
)

_RUN_AT = time(22, 30)

_FRI = date(2026, 7, 17)
_SAT = date(2026, 7, 18)
_SUN = date(2026, 7, 19)
_MON = date(2026, 7, 20)

# The hole-aware read's fixtures — the MEASURED prod week: 2026-09-03 Thu, 09-04 Fri (a hole: the Friday
# target woke on Saturday and the wake-day weekday check skipped it), 09-07 Mon, 09-08 Tue (a hole: the
# Tuesday target fired Wed 09:09 and recorded Wednesday), 09-09 Wed, 09-10 Thu. Since F5, 09-07 is LABOR
# DAY to the schedule (not a session): its recorded rows are never a hole and never expected.
_S_THU = date(2026, 9, 3)
_S_FRI = date(2026, 9, 4)
_S_MON = date(2026, 9, 7)
_S_TUE = date(2026, 9, 8)
_S_WED = date(2026, 9, 9)
_S_THU2 = date(2026, 9, 10)


def test_parse_run_at_reads_the_sidecar_format():
    assert parse_run_at("22:30") == time(22, 30)
    assert parse_run_at(" 06:05 ") == time(6, 5)  # whitespace-tolerant (an env var edit)


def test_parse_run_at_is_loud_on_garbage():
    # a malformed ALPHADECK_CRON_AT is a deploy error — never a silently wrong schedule
    with pytest.raises(ValueError):
        parse_run_at("half past ten")


def test_is_scheduled_day_is_mon_to_fri_minus_market_holidays():
    assert is_scheduled_day(_FRI) is True
    assert is_scheduled_day(_MON) is True
    assert is_scheduled_day(_SAT) is False
    assert is_scheduled_day(_SUN) is False
    # F5: 2026-07-03 (observed Independence Day, a Friday) is NOT a scheduled day any more — the cron
    # fires on the exchange's sessions (until F5 it fired on it, and on Labor Day, for a night with none)
    assert is_scheduled_day(date(2026, 7, 3)) is False
    assert is_scheduled_day(date(2026, 9, 7)) is False  # Labor Day: 11 call rows recorded before F5


def test_a_holiday_expects_no_run_and_the_night_before_it_is_current():
    # Thanksgiving Thu 2026-11-26: Wednesday's record read on Thanksgiving night or the Friday morning
    # after is CURRENT — the holiday expects no run (don't cry wolf over a closed market)
    wed, fri = date(2026, 11, 25), date(2026, 11, 27)
    assert last_expected_asof(datetime(2026, 11, 26, 23, 0), _RUN_AT) == wed
    assert last_expected_asof(datetime(2026, 11, 27, 9, 0), _RUN_AT) == wed
    assert expected_runs_behind(wed, wed) == 0
    # ...and Friday night's run (a half-day close IS a session) makes it 1 behind
    assert last_expected_asof(datetime(2026, 11, 27, 23, 0), _RUN_AT) == fri
    assert expected_runs_behind(wed, fri) == 1


def test_last_expected_before_run_at_is_the_prior_weekday():
    # Monday 09:00, RUN_AT 22:30 → today's run hasn't fired yet → the last expected asof is FRIDAY
    assert last_expected_asof(datetime(2026, 7, 20, 9, 0), _RUN_AT) == _FRI


def test_last_expected_after_run_at_is_today():
    assert last_expected_asof(datetime(2026, 7, 20, 23, 0), _RUN_AT) == _MON
    # the boundary instant counts as fired (the shell's `next <= now` is inclusive the same way)
    assert last_expected_asof(datetime(2026, 7, 20, 22, 30), _RUN_AT) == _MON


def test_last_expected_on_a_weekend_is_friday():
    # no weekend runs are scheduled — Saturday AND Sunday both expect Friday's run, at any hour
    assert last_expected_asof(datetime(2026, 7, 18, 23, 59), _RUN_AT) == _FRI
    assert last_expected_asof(datetime(2026, 7, 19, 8, 0), _RUN_AT) == _FRI


def test_fri_edge_monday_morning_is_ZERO_behind():
    # THE spec case: a Friday-dated record viewed Monday before RUN_AT is NOT stale — don't cry wolf
    # over a weekend
    expected = last_expected_asof(datetime(2026, 7, 20, 9, 0), _RUN_AT)  # -> Friday
    assert expected_runs_behind(_FRI, expected) == 0


def test_fri_edge_monday_night_is_ONE_behind():
    expected = last_expected_asof(datetime(2026, 7, 20, 23, 0), _RUN_AT)  # -> Monday
    assert expected_runs_behind(_FRI, expected) == 1


def test_behind_counts_only_weekdays():
    # edge the PRIOR Monday, expected the next Monday → Tue+Wed+Thu+Fri+Mon = 5 (Sat/Sun never count)
    assert expected_runs_behind(date(2026, 7, 13), _MON) == 5


def test_weekend_edge_is_current_until_monday_runs():
    # a Saturday-dated edge (a manual weekend run) still reads current against Friday's expectation…
    assert expected_runs_behind(_SAT, _FRI) == 0
    # …and becomes 1 behind once Monday's run was expected
    assert expected_runs_behind(_SAT, _MON) == 1


def test_edge_at_or_ahead_of_expected_is_zero():
    assert expected_runs_behind(_MON, _MON) == 0
    # a manual run TODAY before RUN_AT puts the edge AHEAD of the last expected run — 0, never negative
    assert expected_runs_behind(_MON, _FRI) == 0


def test_none_edge_is_none_not_a_number():
    # the record-never-began state has no schedule to be behind — the QUIET fresh-install state (the
    # caller renders it as "never begun", never a loud stale alarm)
    assert expected_runs_behind(None, _MON) is None


# --- the hole-aware read: scheduled_window + missed_asofs (pure; no DB) ---


def test_scheduled_window_is_the_last_n_sessions_ascending():
    # 5 scheduled days ending Tue 09-08: Tue 09-01, Wed, Thu, Fri, (weekend + Labor Day skipped), Tue
    assert scheduled_window(_S_TUE, 5) == [
        date(2026, 9, 1),
        date(2026, 9, 2),
        _S_THU,
        _S_FRI,
        _S_TUE,
    ]
    assert _S_MON not in scheduled_window(_S_TUE, 10)  # Labor Day is never scanned
    assert scheduled_window(_S_TUE, 0) == []  # 0 disables the scan
    # a weekend `expected` (a manual weekend run's date) walks back to Friday — never a weekend day
    assert scheduled_window(date(2026, 9, 6), 1) == [_S_FRI]


def test_missed_a_single_friday_hole():
    # THE measured shape #1: the Friday target woke on Saturday → skipped; Thu + Mon + Tue recorded. The
    # edge (Tue) is CURRENT on Tuesday night — the edge check sees nothing; this read sees Friday.
    missed = missed_asofs({_S_THU, _S_MON, _S_TUE}, expected=_S_TUE, first=_S_THU, window=10)
    assert missed == [_S_FRI]
    assert expected_runs_behind(_S_TUE, _S_TUE) == 0  # ...and the edge check is indeed blind to it


def test_missed_the_wrong_day_tuesday_hole():
    # THE measured shape #2: the Tuesday target fired Wed 09:09 → recorded WEDNESDAY; Tuesday is a hole
    # under a current edge
    missed = missed_asofs({_S_MON, _S_WED, _S_THU2}, expected=_S_THU2, first=_S_MON, window=10)
    assert missed == [_S_TUE]


def test_missed_never_lists_weekends():
    # a clean Fri → Mon record: Sat/Sun are never scheduled, so they are never "missing" — and a manual
    # Saturday run in `recorded` changes nothing
    assert missed_asofs({_S_FRI, _S_MON}, expected=_S_MON, first=_S_FRI, window=10) == []
    assert (
        missed_asofs({_S_FRI, date(2026, 9, 5), _S_MON}, expected=_S_MON, first=_S_FRI, window=10)
        == []
    )


def test_missed_never_lists_a_market_holiday():
    # F5: a record with NO Labor Day row has no hole there (Fri 09-04 -> Tue 09-08 is a clean record), and
    # the expected night on Labor Day evening is still Friday's
    assert missed_asofs({_S_FRI, _S_TUE}, expected=_S_TUE, first=_S_FRI, window=10) == []
    assert last_expected_asof(datetime(2026, 9, 7, 23, 0), _RUN_AT) == _S_FRI
    assert expected_runs_behind(_S_FRI, _S_TUE) == 1  # only Tuesday was owed


def test_missed_ignores_pre_history_when_the_record_began_mid_window():
    # the record's first as-of is Monday: the window reaches back to late August, but nights before the
    # record began are pre-history, not holes (a fresh install must never show them)
    assert missed_asofs({_S_MON, _S_TUE}, expected=_S_TUE, first=_S_MON, window=10) == []
    # ...whereas a hole INSIDE the record's span still counts
    assert missed_asofs({_S_MON, _S_WED}, expected=_S_WED, first=_S_MON, window=10) == [_S_TUE]


def test_missed_is_bounded_by_the_window():
    # first = Aug 31, only Tue 09-08 recorded: a 3-session window (Thu, Fri, Tue — Labor Day is not a
    # session) lists Thu + Fri only — Aug 31 → Sep 2 are holes too, but OUTSIDE the window (loudness stays
    # bounded, ascending order)
    missed = missed_asofs({_S_TUE}, expected=_S_TUE, first=date(2026, 8, 31), window=3)
    assert missed == [_S_THU, _S_FRI]
    assert missed_asofs({_S_TUE}, expected=_S_TUE, first=date(2026, 8, 31), window=0) == []


def test_missed_is_empty_when_the_record_never_began():
    # first=None = no call-of-record at all — the quiet fresh-install state, never a list of "misses"
    assert missed_asofs(set(), expected=_S_TUE, first=None, window=10) == []


def test_the_expected_day_itself_missing_is_in_BOTH_reads():
    # Monday recorded, Tuesday's run expected and absent: it is 1 behind (the edge check) AND a listed
    # hole (this read) — deliberate; the router's verdict priority (stale > gappy) decides the wording
    assert missed_asofs({_S_MON}, expected=_S_TUE, first=_S_MON, window=10) == [_S_TUE]
    assert expected_runs_behind(_S_MON, _S_TUE) == 1
