"""The listing rule, pure (no database): a ticker / listing date counts as window activity only when it is STRICTLY
LATER than the market date the fund's series was first stored. ``recorded_at`` is used only to judge whether a
first-seen symbol date is an observed arrival, never as a history gate. Made-up funds."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from domain.market_time import market_date_of
from radar.fund_repo import SeriesView, SymbolRow
from radar.fund_stage import FundStage, activity_dates, derive_launches, visible_as_of

N = date(2026, 10, 15)


def _stage(ticker=None, listed=None):
    return FundStage(date(2026, 4, 1), None, ticker, listed)


def test_a_symbol_on_the_night_the_series_was_stored_is_not_activity():
    assert activity_dates(_stage(ticker=N, listed=N), N) == [date(2026, 4, 1)]


def test_a_symbol_after_that_night_is_activity():
    later = date(2026, 11, 14)  # N + 30
    assert activity_dates(_stage(ticker=later, listed=later), N) == [date(2026, 4, 1), later, later]


def test_a_symbol_before_that_night_is_not_activity_either():
    assert activity_dates(_stage(ticker=date(2026, 10, 1)), N) == [date(2026, 4, 1)]


def test_an_unknown_stored_night_leaves_the_dates_counted():
    assert activity_dates(_stage(ticker=N), None) == [date(2026, 4, 1), N]


def _view(first_stored, sid="S1"):
    return SeriesView(
        accession="A-1",
        form="485APOS",
        filer_name="Test Trust",
        filer_ciks=["0000000001"],
        public_date=date(2026, 4, 1),
        private_to_public=False,
        effectiveness_date=None,
        series_key=sid,
        series_id=sid,
        series_name="Test Fund",
        kind="new",
        first_stored=first_stored,
    )


def test_the_launch_carries_the_market_date_of_its_earliest_stored_instant():
    # 00:30 ET on 2026-10-16 is 04:30 UTC the same day: the market date is the 16th, not the UTC date's neighbor
    late = datetime(2026, 10, 16, 4, 30, tzinfo=timezone.utc)
    early = datetime(2026, 10, 15, 22, 0, tzinfo=timezone.utc)  # 18:00 ET on the 15th
    (lc,) = derive_launches([_view(late), _view(early)], [])
    assert lc.first_stored == date(2026, 10, 15)
    (lc,) = derive_launches([_view(late)], [])
    assert lc.first_stored == date(2026, 10, 16)
    (lc,) = derive_launches([_view(None)], [])
    assert lc.first_stored is None


def test_a_pass_that_crosses_midnight_is_not_activity():
    # symbols stamped with the pass's night N, rows stored at 00:30 ET on N+1
    stored = datetime(2026, 10, 16, 4, 30, tzinfo=timezone.utc)
    sym = SymbolRow("S1", "C1", "TSTX", "sec_ticker_file", date(2026, 10, 15))
    (lc,) = derive_launches([_view(stored)], [sym])
    assert lc.stage.ticker == date(2026, 10, 15)
    assert date(2026, 10, 15) not in activity_dates(lc.stage, lc.first_stored)


def test_history_still_filters_by_public_date_and_symbol_night_not_by_recorded_at():
    sym_later = SymbolRow("S1", "C1", "TSTX", "sec_ticker_file", date(2026, 11, 20))
    views, syms = visible_as_of(
        [_view(datetime(2026, 9, 1, tzinfo=timezone.utc))], [sym_later], date(2026, 11, 1)
    )
    assert (
        len(views) == 1 and syms == []
    )  # a symbol first seen after asof is invisible (invariant #1)


def test_market_date_of_refuses_a_naive_instant():
    with pytest.raises(ValueError):
        market_date_of(datetime(2026, 10, 16, 4, 0))


def test_market_date_of_matches_market_today_rule():
    assert market_date_of(datetime(2026, 10, 16, 3, 59, tzinfo=timezone.utc)) == date(2026, 10, 15)
    assert market_date_of(datetime(2026, 10, 16, 4, 0, tzinfo=timezone.utc)) == date(2026, 10, 16)
