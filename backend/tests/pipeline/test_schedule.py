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
    parse_run_at,
)

_RUN_AT = time(22, 30)

_FRI = date(2026, 7, 17)
_SAT = date(2026, 7, 18)
_SUN = date(2026, 7, 19)
_MON = date(2026, 7, 20)


def test_parse_run_at_reads_the_sidecar_format():
    assert parse_run_at("22:30") == time(22, 30)
    assert parse_run_at(" 06:05 ") == time(6, 5)  # whitespace-tolerant (an env var edit)


def test_parse_run_at_is_loud_on_garbage():
    # a malformed ALPHADECK_CRON_AT is a deploy error — never a silently wrong schedule
    with pytest.raises(ValueError):
        parse_run_at("half past ten")


def test_is_scheduled_day_is_mon_to_fri_holidays_included():
    assert is_scheduled_day(_FRI) is True
    assert is_scheduled_day(_MON) is True
    assert is_scheduled_day(_SAT) is False
    assert is_scheduled_day(_SUN) is False
    # 2026-07-03 (observed Independence Day, a Friday) is still a SCHEDULED day — the trigger's
    # calendar, not the exchange's: the cron genuinely fires (an idempotent near-no-op)
    assert is_scheduled_day(date(2026, 7, 3)) is True


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
