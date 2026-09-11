from __future__ import annotations

import uuid
from datetime import date

import pytest

from db.session import DEFAULT_TENANT_ID
from replay.schema import Episode
from replay.scoring import score_episode
from scoreboard.prices import PgRealizedPrices
from tests.scoreboard.helpers import bar

# The excursion pair + the sparkline path, scored through the POSTGRES reader (the live Scoreboard's
# own leg — the DuckDB twin's agreement is pinned separately in tests/replay/test_pg_prices_parity.py).
# Every fixture here is a construction of a shape that genuinely occurs on the record: a close-only
# tape, a partially-winged bar, a name that never closed below entry, a de-arm that outlived its own
# horizon, and the inverted Sunday window.


def _reader(db, cap, known_at=None):
    return PgRealizedPrices(db, tenant_id=DEFAULT_TENANT_ID, cap=cap, known_at=known_at)


def _episode(security_id, arm: date, exit_by: date, *, dearm: date | None = None) -> Episode:
    return Episode(
        thesis_id=uuid.uuid4(),
        security_id=security_id,
        is_headline=True,
        arm_date=arm,
        last_armed_date=dearm or exit_by,
        dearm_date=dearm,
        close_reason="window_end" if dearm is None else "dearmed_other",
        exit_by=exit_by,
    )


# --- T13 / T14: the wick fields are ALL-OR-NOTHING, per column, and never fall back to the close ----


def test_one_missing_wick_nulls_that_column_and_only_that_column(db, security_id):
    """A single bar missing ``high`` nulls ``intraday_high_*`` entirely — the true intraday high could
    be INSIDE the unknown bar, so an extreme over the remaining bars is not conservative, it is wrong.
    The CLOSE pair is untouched (it is a different column), and ``low`` — complete on every bar — still
    reports. The load-bearing assertion is the last one: the null is not the close standing in."""
    bar(db, security_id, date(2026, 6, 1), 100.0, high=105.0, low=98.0)
    bar(db, security_id, date(2026, 6, 2), 110.0, high=None, low=104.0)  # close-only HIGH
    bar(db, security_id, date(2026, 6, 3), 105.0, high=112.0, low=100.0)

    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 6, 3)),
        _reader(db, cap=date(2026, 6, 30)),
    )
    # the close pair is complete and unaffected
    assert out.peak_return == pytest.approx(0.10) and out.peak_date == date(2026, 6, 2)
    assert out.trough_return == 0.0 and out.trough_date == date(2026, 6, 1)
    # high: one bar short -> the whole column abstains
    assert out.intraday_high_return is None and out.intraday_high_date is None
    # low: every bar carries it -> it still reports (all-or-nothing is PER COLUMN, not per episode)
    assert out.intraday_low_return == pytest.approx(-0.02)
    assert out.intraday_low_date == date(2026, 6, 1)
    # and the silent-lie mode this exists to catch: the abstaining column never borrows the close
    assert out.intraday_high_return != out.peak_return


def test_close_only_tape_scores_closes_and_abstains_on_both_wicks(db, security_id):
    """The honest degradation shape of a free-EOD source: closes present, no wicks at all. Both close
    excursions score; both wick excursions are ``None`` rather than a close wearing a wick's name.
    """
    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 2), 90.0)
    bar(db, security_id, date(2026, 6, 3), 95.0)

    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 6, 3)),
        _reader(db, cap=date(2026, 6, 30)),
    )
    assert out.peak_return == 0.0 and out.trough_return == pytest.approx(-0.10)
    assert out.trough_date == date(2026, 6, 2)
    assert out.intraday_high_return is None and out.intraday_low_return is None
    assert out.intraday_high_date is None and out.intraday_low_date is None


# --- T15: a real 0.0 is a MEASUREMENT, not a missing value -------------------------------------------


def test_trough_reads_a_real_zero_when_it_never_closed_below_entry(db, security_id):
    """24% of the measured record never closes below its entry. ``trough_return`` must read a real
    ``0.0`` there (the adverse excursion WAS nothing) — not ``None``, which would say "unknown" about a
    number we know exactly. Pinned as ``is not None`` first, because ``0.0 == None`` fails differently
    from ``0.0`` being absent."""
    bar(db, security_id, date(2026, 6, 1), 100.0, high=101.0, low=100.0)
    bar(db, security_id, date(2026, 6, 2), 104.0, high=105.0, low=103.0)
    bar(db, security_id, date(2026, 6, 3), 109.0, high=110.0, low=108.0)

    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 6, 3)),
        _reader(db, cap=date(2026, 6, 30)),
    )
    assert out.trough_return is not None
    assert out.trough_return == 0.0 and out.trough_date == date(2026, 6, 1)
    # the wick MAE, by contrast, CAN be a real 0.0 here only because this fixture's first low equals
    # the entry close; on the measured record it never is (max -0.2%). Nothing asserts they agree.
    assert out.intraday_low_return == 0.0


# --- T17: no-lookahead on the NEW read ---------------------------------------------------------------


def test_ohlc_window_and_path_respect_the_asof_cap(db, security_id):
    """The excursions and the path are a NEW read; without its own test a ``bars_between`` that forgot
    the cap would pass every other assertion in this file while quietly breaking invariant #1. A bar
    beyond the cap — with the window's would-be extremes on both sides — must not reach any of them.
    """
    bar(db, security_id, date(2026, 6, 1), 100.0, high=102.0, low=99.0)
    bar(db, security_id, date(2026, 6, 2), 104.0, high=106.0, low=103.0)
    bar(db, security_id, date(2026, 6, 3), 101.0, high=105.0, low=97.0)
    # past the cap: it would own every extreme AND extend the path if the read leaked
    bar(db, security_id, date(2026, 6, 10), 500.0, high=900.0, low=1.0)

    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 6, 30)),
        _reader(db, cap=date(2026, 6, 5)),
    )
    assert out.path == [100.0, 104.0, 101.0]
    assert out.peak_date == date(2026, 6, 2) and out.trough_date == date(2026, 6, 1)
    assert out.intraday_high_date == date(2026, 6, 2)
    assert out.intraday_low_date == date(2026, 6, 3)
    assert out.intraday_high_return == pytest.approx(0.06)
    assert out.intraday_low_return == pytest.approx(-0.03)


# --- T18: the path and its de-arm marker agree -------------------------------------------------------


def _path_bars(db, security_id) -> None:
    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 2), 102.0)
    bar(db, security_id, date(2026, 6, 3), 101.0)
    bar(db, security_id, date(2026, 6, 4), 105.0)
    bar(db, security_id, date(2026, 6, 5), 103.0)


def test_dearm_index_points_at_the_last_bar_on_or_before_the_dearm(db, security_id):
    _path_bars(db, security_id)
    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 6, 5), dearm=date(2026, 6, 3)),
        _reader(db, cap=date(2026, 6, 30)),
    )
    assert out.path == [100.0, 102.0, 101.0, 105.0, 103.0]
    assert out.dearm_index == 2
    assert out.path[out.dearm_index] == 101.0


def test_dearm_on_a_non_trading_day_lands_on_the_prior_bar(db, security_id):
    """No bar on the de-arm day (a weekend or holiday de-arm). The marker belongs on the last bar the
    tape actually has on or before it — never interpolated onto a day that did not trade."""
    bar(db, security_id, date(2026, 6, 1), 100.0)
    bar(db, security_id, date(2026, 6, 3), 101.0)
    bar(db, security_id, date(2026, 6, 5), 103.0)
    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 6, 5), dearm=date(2026, 6, 4)),
        _reader(db, cap=date(2026, 6, 30)),
    )
    assert out.dearm_index == 1 and out.path[1] == 101.0


def test_dearm_after_the_scored_window_has_no_place_on_the_path(db, security_id):
    """The real 8-episode case: the horizon elapsed while the record kept the member armed, so the
    de-arm postdates the last scored bar. It must be ``None`` — clamping it onto the final slot would
    draw a marker at a date the de-arm did not happen. (The render site's copy is what keeps the null
    from reading as "never de-armed".)"""
    _path_bars(db, security_id)
    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 6, 3), dearm=date(2026, 6, 5)),
        _reader(db, cap=date(2026, 6, 30)),
    )
    assert out.exit_date == date(2026, 6, 3)
    assert out.path == [100.0, 102.0, 101.0]
    assert out.dearm_index is None


def test_open_episode_has_a_path_and_no_dearm_marker(db, security_id):
    _path_bars(db, security_id)
    out = score_episode(
        _episode(security_id, date(2026, 6, 1), date(2026, 6, 5)),
        _reader(db, cap=date(2026, 6, 30)),
    )
    assert len(out.path) == 5 and out.dearm_index is None


# --- the inverted Sunday window: the new fields must not flatter it ----------------------------------


def test_inverted_window_episode_gains_nothing_from_the_new_fields(db, security_id):
    """The two real episodes that arm on a Sunday with ``exit_by`` the SAME Sunday: the scored window
    ``[arm_date, exit_by]`` contains no trading day at all, so there is nothing to describe. Every new
    field must stay empty — an empty path (the sparkline reads "—" below two closes) and four null
    excursions. This row is already known-bad (its ``forward_return`` is measured backwards — the exit
    read is unbounded below, deliberately NOT fixed in this change), and the point of the assertion is
    that nothing added here makes it LOOK better than it is: no excursion, no shape, no marker."""
    bar(db, security_id, date(2026, 8, 14), 100.0, high=101.0, low=99.0)  # Friday
    bar(db, security_id, date(2026, 8, 17), 96.0, high=97.0, low=95.0)  # Monday

    sunday = date(2026, 8, 16)
    out = score_episode(
        _episode(security_id, sunday, sunday, dearm=date(2026, 8, 17)),
        _reader(db, cap=date(2026, 9, 10)),
    )
    # the scored window is genuinely empty — 0 bars between Sunday and Sunday
    assert out.path == [] and out.dearm_index is None
    assert out.peak_return is None and out.trough_return is None
    assert out.intraday_high_return is None and out.intraday_low_return is None
    assert out.peak_date is None and out.trough_date is None
    # unchanged pre-existing behaviour, pinned so this change is visibly not the place it gets fixed:
    # the entry is Monday's close and the exit is Friday's, so the return runs backwards in time.
    assert (
        out.entry_close == 96.0 and out.exit_close == 100.0 and out.exit_date == date(2026, 8, 14)
    )
    assert out.forward_return is not None and out.forward_return > 0
