"""Track Record by Cap Tier (``docs/dashboards/track-record.md``): horizons count SESSIONS of the name's own bars (a
weekend and a holiday gap shift nothing); a path that ends before the Nth bar reads "path ended" and a horizon that
has not arrived reads "maturing", neither counted; the excess against IWM and SPY on hand-built bars; the median, the
quartiles, the hit rate and the n < 10 rule on hand-computed numbers; the cap AT THE EVENT (the close BEFORE the arm
date, the share count dated BEFORE it, the price put back to as traded across a later split, an unread ADS ratio
unpinned); the liquidity bands; the entry toggle (both entry closes on one row); the table's order with the pooled row
LAST; the router off one cached read on the request's view; and, DB-backed, an episode armed after the as-of is not an
event, a horizon not matured by the as-of is not counted (the same episode at two as-ofs), a fact published after the
as-of never reaches the cap, a flagged price history and a censored start are left out and counted, and the
"to exit_by" column reconciles with the Scoreboard's own ``forward_return``."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from psycopg.rows import dict_row

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import track_record as router_mod
from dashboards.baskets import FlaggedCompany
from dashboards.cache import CACHE
from dashboards.peer_table import CapCell
from dashboards.track_record import (
    HORIZONS,
    LIQUIDITY_KEYS,
    MIN_N,
    POOLED,
    TIER_KEYS,
    UNPINNED,
    Bar,
    Benchmarks,
    CompanyData,
    EntryRead,
    EpisodeIn,
    Market,
    Obs,
    RecordRow,
    Span,
    assemble,
    bars_of,
    benchmarks_of,
    build_row,
    cell_stats,
    count_rows,
    entry_index,
    event_adv,
    event_cap,
    exit_by_span,
    horizon_close,
    liquidity_band,
    market_of,
    quantile,
    session_span,
    slice_key,
    table_rows,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.call import CallCard
from domain.market_cap import CapTier
from domain.market_time import market_today
from domain.splits import ListedSplit, listed_from_row
from domain.thesis import BasketMember, Thesis
from repositories import editions_repo, thesis_repo
from tests.calls.factories import insider_event, make_thesis
from tests.scoreboard.helpers import keys_fired, record_day

UTC = timezone.utc

# ---------------------------------------------------------------------------------------------------------
# hand-built tapes
# ---------------------------------------------------------------------------------------------------------


def weekdays(start: date, n: int, skip: tuple[date, ...] = ()) -> list[date]:
    """``n`` Mon-Fri dates from ``start`` on, skipping the given market holidays."""
    out: list[date] = []
    d = start
    while len(out) < n:
        if d.weekday() < 5 and d not in skip:
            out.append(d)
        d += timedelta(days=1)
    return out


def tape(
    days: list[date], first: float, step: float = 1.0, volume: float | None = None
) -> list[Bar]:
    return [Bar(d, first + step * i, volume) for i, d in enumerate(days)]


def mkt(days: list[date], stale: int = 5) -> Market:
    return market_of(tape(days, 400.0), [], stale_days=stale)


NO_BENCH = Benchmarks({"IWM": {}, "SPY": {}})

# ---------------------------------------------------------------------------------------------------------
# horizons count SESSIONS of the name's own bars
# ---------------------------------------------------------------------------------------------------------

JUNETEENTH = date(2026, 6, 19)  # a Friday market holiday: no bar that day
SESSIONS = weekdays(date(2026, 6, 1), 40, skip=(JUNETEENTH,))


def test_horizons_count_the_names_own_bars_a_weekend_and_a_holiday_shift_nothing():
    bars = tape(SESSIONS, 10.0)
    i = entry_index(bars, date(2026, 6, 1), "arm_night")
    assert i == 0
    # 5 bars after Mon 06-01 is Mon 06-08: the weekend is not a bar, so calendar days (+5 = Sat) would be wrong
    assert horizon_close(bars, i, 5).d == date(2026, 6, 8)
    assert horizon_close(bars, i, 10).d == date(2026, 6, 15)
    # 15 bars after 06-01 is Tue 06-23: the 06-19 holiday has no bar, so it is not counted either
    assert horizon_close(bars, i, 15).d == date(2026, 6, 23)
    assert JUNETEENTH not in [b.d for b in bars]
    assert horizon_close(bars, i, 20).d == date(2026, 6, 30)
    assert horizon_close(bars, i, 60) is None  # the tape does not reach that far


def test_the_two_entry_rules_pick_the_first_close_on_or_after_the_arm_date_and_the_one_strictly_after():
    bars = tape(SESSIONS, 10.0)
    assert entry_index(bars, date(2026, 6, 3), "arm_night") == 2  # 06-03 has a bar
    assert entry_index(bars, date(2026, 6, 3), "next_session") == 3
    # a Sunday arm: both rules enter Monday's bar
    assert bars[entry_index(bars, date(2026, 6, 7), "arm_night")].d == date(2026, 6, 8)
    assert bars[entry_index(bars, date(2026, 6, 7), "next_session")].d == date(2026, 6, 8)
    assert entry_index(bars, date(2026, 12, 1), "arm_night") is None  # no bar on or after
    with pytest.raises(ValueError):
        entry_index(bars, date(2026, 6, 3), "someday")


# ---------------------------------------------------------------------------------------------------------
# maturing against path ended
# ---------------------------------------------------------------------------------------------------------

ASOF = date(2026, 7, 24)


def test_a_path_that_ends_before_the_nth_bar_reads_path_ended_and_is_not_counted():
    days = weekdays(date(2026, 6, 1), 10)  # the name's bars stop Fri 06-12
    market = mkt(weekdays(date(2026, 6, 1), 40))  # the market runs on to 07-24
    bars = tape(days, 10.0)
    five = session_span(bars, 0, 5, NO_BENCH, market)
    twenty = session_span(bars, 0, 20, NO_BENCH, market)
    assert five.status == "counted" and five.end_date == date(2026, 6, 8)
    assert twenty.status == "path_ended"
    assert (twenty.ret, twenty.iwm, twenty.spy, twenty.end_date) == (None, None, None, None)


def test_a_horizon_the_market_has_not_reached_is_maturing_even_for_a_name_that_stopped():
    bars = tape(weekdays(date(2026, 6, 1), 10), 10.0)  # stops 06-12
    market = mkt(weekdays(date(2026, 6, 1), 12))  # the market has had only 11 sessions since 06-01
    assert session_span(bars, 0, 20, NO_BENCH, market).status == "maturing"


def test_a_current_name_one_bar_short_of_the_horizon_is_maturing_not_path_ended():
    days = weekdays(date(2026, 6, 1), 7)
    market = mkt(days)  # the market has had 6 sessions after 06-01
    bars = tape(
        [d for d in days if d != date(2026, 6, 3)], 10.0
    )  # a vendor gap: 5 bars after the entry
    assert horizon_close(bars, 0, 6) is None
    assert (
        session_span(bars, 0, 6, NO_BENCH, market).status == "maturing"
    )  # tape is current: it waits


@pytest.mark.parametrize(
    "gap_days,status",
    [
        (3, "maturing"),
        (4, "path_ended"),
    ],  # more than stale_days (3 here) calendar days behind the market
)
def test_the_staleness_threshold_is_strict(gap_days, status):
    days = weekdays(date(2026, 6, 1), 30)  # the market's last bar: Fri 07-10
    market = mkt(days, stale=3)
    cutoff = market.last - timedelta(days=gap_days)  # a weekday either way: Tue 07-07, Mon 07-06
    bars = tape([d for d in days if d <= cutoff], 10.0)
    assert bars[-1].d == cutoff and horizon_close(bars, 0, 29) is None
    assert market.sessions_after(bars[0].d) == 29  # the market HAS had the 29 sessions
    assert session_span(bars, 0, 29, NO_BENCH, market).status == status


def test_no_market_tape_never_reads_path_ended_and_a_name_with_no_bars_is_behind():
    bars = tape(weekdays(date(2026, 6, 1), 3), 10.0)
    assert session_span(bars, 0, 20, NO_BENCH, Market()).status == "maturing"
    market = mkt(weekdays(date(2026, 6, 1), 30))
    assert (
        session_span([], None, 5, NO_BENCH, market).status == "path_ended"
    )  # nothing ever printed
    assert session_span([], None, 5, NO_BENCH, Market()).status == "path_ended"


def test_a_name_with_no_bar_yet_on_the_arm_night_is_maturing_while_its_tape_is_current():
    market = mkt(weekdays(date(2026, 6, 1), 5))
    bars = tape(weekdays(date(2026, 6, 1), 4), 10.0)  # the arm night 06-05 has no bar yet
    i = entry_index(bars, date(2026, 6, 5), "arm_night")
    assert i is None
    assert session_span(bars, i, 5, NO_BENCH, market).status == "maturing"


# ---------------------------------------------------------------------------------------------------------
# the return and the excess against IWM and SPY
# ---------------------------------------------------------------------------------------------------------

DAYS = weekdays(date(2026, 6, 1), 12)


def test_the_return_and_each_benchmarks_return_over_the_same_two_dates():
    name = [Bar(d, c) for d, c in zip(DAYS, [100.0, 101, 102, 103, 104, 110.0] + [111.0] * 6)]
    iwm = [Bar(d, c) for d, c in zip(DAYS, [50.0, 50, 50, 50, 50, 52.0] + [53.0] * 6)]
    spy = [Bar(d, c) for d, c in zip(DAYS, [400.0, 401, 402, 403, 403, 404.0] + [405.0] * 6)]
    bench = benchmarks_of(spy, iwm)
    span = session_span(name, 0, 5, bench, market_of(spy, iwm))
    assert span.status == "counted" and span.end_date == DAYS[5]
    assert span.ret == pytest.approx(0.10)
    assert span.iwm == pytest.approx(0.04)
    assert span.spy == pytest.approx(0.01)
    # the excess a cell takes is the name's return minus the comparison's
    stats = cell_stats([Obs(span.ret, span.iwm)], "iwm")
    assert stats.median == pytest.approx(0.06) and stats.n == 1 and stats.held == 1
    assert cell_stats([Obs(span.ret, span.spy)], "spy").median == pytest.approx(0.09)


def test_a_benchmark_with_no_bar_on_either_date_has_no_return_never_a_stand_in():
    name = [Bar(d, 100.0 + i) for i, d in enumerate(DAYS)]
    iwm = [Bar(d, 50.0) for d in DAYS if d != DAYS[5]]  # no IWM bar on the horizon date
    spy = [Bar(d, 400.0) for d in DAYS]
    span = session_span(name, 0, 5, benchmarks_of(spy, iwm), market_of(spy, iwm))
    assert span.status == "counted" and span.iwm is None and span.spy == pytest.approx(0.0)
    # the cell counts the span apart instead of pretending a comparison
    stats = cell_stats([Obs(span.ret, span.iwm)], "iwm")
    assert (stats.n, stats.no_comparison, stats.median) == (0, 1, None)
    assert cell_stats([Obs(span.ret, span.iwm)], "raw").n == 1  # raw needs no comparison


def test_exit_by_is_the_last_bar_on_or_before_it_the_scoreboards_window():
    name = tape(DAYS, 100.0)
    market = mkt(DAYS)
    sunday = date(2026, 6, 14)  # between Fri 06-12's bar and Mon 06-15
    span = exit_by_span(name, 0, sunday, date(2026, 6, 20), NO_BENCH, market)
    assert span.status == "counted" and span.end_date == date(2026, 6, 12)
    assert span.ret == pytest.approx(name[9].close / 100.0 - 1)  # DAYS[9] is Fri 06-12
    # on a session it is that session's bar
    on_day = exit_by_span(name, 0, date(2026, 6, 10), date(2026, 6, 20), NO_BENCH, market)
    assert on_day.end_date == date(2026, 6, 10)


def test_exit_by_not_yet_arrived_none_and_no_session_in_the_window():
    name = tape(DAYS, 100.0)
    market = mkt(DAYS)
    assert (
        exit_by_span(name, 0, date(2026, 6, 30), date(2026, 6, 12), NO_BENCH, market).status
        == "maturing"
    )
    assert exit_by_span(name, 0, None, date(2026, 6, 12), NO_BENCH, market).status == "none"
    # next-session entry with exit_by on the arm date: the window holds no session for that entry
    j = entry_index(name, date(2026, 6, 3), "next_session")
    assert (
        exit_by_span(name, j, date(2026, 6, 3), date(2026, 6, 12), NO_BENCH, market).status
        == "none"
    )


def test_exit_by_after_the_tape_stopped_behind_the_market_is_path_ended():
    market = mkt(weekdays(date(2026, 6, 1), 40))  # runs to 07-24
    name = tape(weekdays(date(2026, 6, 1), 6), 100.0)  # stops 06-08
    assert exit_by_span(name, 0, date(2026, 7, 10), ASOF, NO_BENCH, market).status == "path_ended"
    # no bar at all on or after the arm date
    assert (
        exit_by_span(name, None, date(2026, 7, 10), ASOF, NO_BENCH, market).status == "path_ended"
    )
    # the same stop, but exit_by falls ON the last bar: that bar is the horizon, counted
    assert exit_by_span(name, 0, date(2026, 6, 8), ASOF, NO_BENCH, market).status == "counted"


# ---------------------------------------------------------------------------------------------------------
# the cell statistics: median, quartiles, hit rate, the n < 10 rule
# ---------------------------------------------------------------------------------------------------------

# Twelve excess values (hand-sorted) over a benchmark return of 0.25 each; the frontend's recordView test pins the
# SAME vectors, so the two implementations cannot drift apart.
EXCESS_12 = [-0.10, -0.04, -0.02, 0.00, 0.01, 0.02, 0.03, 0.04, 0.05, 0.06, 0.08, 0.12]


def obs_of(values, bench=0.25):
    return [Obs(v + bench, bench) for v in values]


def test_quantiles_interpolate_linearly_between_order_statistics():
    assert quantile([1, 2, 3, 4], 0.5) == 2.5
    assert quantile([1, 2, 3, 4], 0.25) == pytest.approx(1.75)
    assert quantile([1, 2, 3, 4], 0.75) == pytest.approx(3.25)
    assert quantile([7], 0.25) == 7
    assert quantile([1, 2, 3], 0.5) == 2
    with pytest.raises(ValueError):
        quantile([], 0.5)


def test_a_cell_states_n_the_median_the_quartiles_and_how_often_it_held():
    stats = cell_stats(obs_of(EXCESS_12), "iwm")
    assert stats.n == 12 and stats.too_small is False
    assert stats.median == pytest.approx(0.025)  # the mean of the 6th and 7th: (0.02 + 0.03) / 2
    assert stats.q1 == pytest.approx(-0.005)  # -0.02 + 0.75 * (0.00 - -0.02)
    assert stats.q3 == pytest.approx(0.0525)  # 0.05 + 0.25 * (0.06 - 0.05)
    assert stats.held == 8  # the excess of exactly 0.00 did not hold: strictly above zero
    assert stats.hit_rate == pytest.approx(8 / 12)
    # the medians of the raw returns the cell is made of: the names' and the benchmark's
    assert stats.name_median == pytest.approx(0.275) and stats.bench_median == pytest.approx(0.25)
    assert stats.no_comparison == 0


def test_a_cell_under_ten_episodes_is_too_small_to_read_and_ten_is_not():
    nine = cell_stats(obs_of(EXCESS_12[:9]), "iwm")
    ten = cell_stats(obs_of(EXCESS_12[:10]), "iwm")
    assert MIN_N == 10
    assert (nine.n, nine.too_small) == (9, True)
    assert (ten.n, ten.too_small) == (10, False)


def test_a_cell_with_no_episode_is_empty_not_too_small_and_blank_never_zero():
    stats = cell_stats([], "iwm")
    assert stats.n == 0 and stats.too_small is False
    assert stats.median is None and stats.hit_rate is None and stats.q1 is None
    assert stats.held == 0


def test_raw_reads_the_raw_return_and_a_missing_benchmark_does_not_drop_it():
    obs = [Obs(0.10, None), Obs(-0.02, None), Obs(0.03, None)]
    stats = cell_stats(obs, "raw")
    assert stats.n == 3 and stats.held == 2
    assert stats.median == pytest.approx(0.03) and stats.bench_median is None
    with pytest.raises(ValueError):
        cell_stats(obs, "qqq")


def test_observations_without_the_comparison_are_counted_apart_not_in_the_cell():
    obs = [Obs(0.10, 0.04), Obs(0.02, None), Obs(-0.01, 0.0)]
    stats = cell_stats(obs, "spy")
    assert (stats.n, stats.no_comparison) == (2, 1)
    assert stats.median == pytest.approx((0.06 + -0.01) / 2)
    assert stats.held == 1


# ---------------------------------------------------------------------------------------------------------
# the cap AT THE EVENT
# ---------------------------------------------------------------------------------------------------------

ARM = date(2026, 6, 10)  # a Wednesday
VIEW_DAY = date(2026, 7, 1)


def shares_fact(shares, valid_from, status=None, ratio=None):
    return {
        "shares": shares,
        "valid_from": valid_from,
        "ads_ratio": ratio,
        "ads_ratio_status": status,
    }


def split(ratio_from, ratio_to, effective, status="confirmed") -> ListedSplit:
    return listed_from_row(
        {
            "key_date": effective,
            "effective_date": effective,
            "ratio_from": ratio_from,
            "ratio_to": ratio_to,
            "status": status,
        }
    )


EVENT_BARS = [
    Bar(date(2026, 6, 8), 9.0),
    Bar(date(2026, 6, 9), 10.0),
    Bar(ARM, 100.0),
    Bar(date(2026, 6, 11), 200.0),
]


def test_the_cap_uses_the_close_before_the_arm_date_and_the_share_count_dated_before_it():
    facts = [
        shares_fact(1_000_000, date(2026, 5, 1)),  # the last count dated before the arm date
        shares_fact(2_000_000, date(2026, 4, 1)),  # an older one
        shares_fact(9_000_000, ARM),  # dated ON the arm date: never used
        shares_fact(9_500_000, date(2026, 6, 20)),  # filed after: never used
    ]
    cap = event_cap(EVENT_BARS, facts, [], ARM, VIEW_DAY)
    assert cap.gap is None and cap.close_date == date(2026, 6, 9) and cap.close == 10.0
    assert (cap.shares, cap.shares_date) == (1_000_000.0, date(2026, 5, 1))
    assert cap.value == 10_000_000.0 and cap.tier is CapTier.NANO


def test_a_share_fact_dated_on_or_after_the_arm_date_alone_is_no_shares_not_a_guess():
    cap = event_cap(EVENT_BARS, [shares_fact(9_000_000, ARM)], [], ARM, VIEW_DAY)
    assert cap.gap == "no_shares" and cap.value is None and cap.tier is None


def test_no_close_before_the_arm_date_is_no_price():
    cap = event_cap([Bar(ARM, 5.0)], [shares_fact(1_000_000, date(2026, 5, 1))], [], ARM, VIEW_DAY)
    assert cap.gap == "no_price" and cap.tier is None
    assert event_cap([], [], [], ARM, VIEW_DAY).gap == "no_price"


@pytest.mark.parametrize("status,ratio", [("unread", None), ("known", None), ("known", 0)])
def test_an_unread_or_corrupt_ads_ratio_lands_unpinned_never_one_to_one(status, ratio):
    cap = event_cap(
        EVENT_BARS, [shares_fact(1_000_000, date(2026, 5, 1), status, ratio)], [], ARM, VIEW_DAY
    )
    assert cap.gap == "ads_unread" and cap.tier is None and cap.value is None


def test_a_known_ads_ratio_divides_the_ordinary_count_and_none_counts_one_to_one():
    cap = event_cap(
        EVENT_BARS, [shares_fact(10_000_000, date(2026, 5, 1), "known", 5)], [], ARM, VIEW_DAY
    )
    assert cap.value == 20_000_000.0 and cap.ads_ratio == 5  # 10M / 5 x 10.0


def test_a_reverse_split_after_the_event_puts_the_close_back_to_the_price_as_traded():
    """The bars are on the VIEW's scale: a 1-for-10 reverse split on 06-20 (in force by the view day) multiplies the
    pre-split close by 10. The cap is the price AS TRADED on 06-09 ($5.00) times the pre-split count, not $50.00
    times it (which would be 10x too large and a wrong tier)."""
    bars = [Bar(date(2026, 6, 9), 50.0), Bar(ARM, 51.0)]
    reverse = split(10, 1, date(2026, 6, 20))
    cap = event_cap(bars, [shares_fact(1_000_000, date(2026, 5, 1))], [reverse], ARM, VIEW_DAY)
    assert cap.close == pytest.approx(5.0) and cap.value == 5_000_000.0
    assert cap.tier is CapTier.NANO  # the adjusted close would have said $50M: micro
    # a view that predates the split reads the same bars unscaled
    early = event_cap(
        bars, [shares_fact(1_000_000, date(2026, 5, 1))], [reverse], ARM, date(2026, 6, 15)
    )
    assert early.close == 50.0 and early.value == 50_000_000.0 and early.tier is CapTier.MICRO


def test_a_split_between_the_count_and_the_close_puts_the_count_on_the_closes_scale():
    forward = split(
        1, 2, date(2026, 6, 5)
    )  # 2-for-1 on 06-05, between the 05-01 count and the 06-09 close
    bars = [Bar(date(2026, 6, 9), 20.0), Bar(ARM, 21.0)]  # post-split prices
    cap = event_cap(
        bars, [shares_fact(1_000_000, date(2026, 5, 1))], [forward], ARM, date(2026, 6, 12)
    )
    assert cap.split_factor == pytest.approx(2.0)
    assert cap.value == 40_000_000.0 and cap.tier is CapTier.NANO  # 2M post-split shares x $20


def test_every_cap_tier_boundary_belongs_to_the_higher_band():
    def tier(cap_usd):
        facts = [shares_fact(cap_usd / 10.0, date(2026, 5, 1))]
        return event_cap(EVENT_BARS, facts, [], ARM, VIEW_DAY).tier

    assert tier(49_999_990) is CapTier.NANO
    assert tier(50_000_000) is CapTier.MICRO
    assert tier(300_000_000) is CapTier.SMALL
    assert tier(2_000_000_000) is CapTier.MID
    assert tier(10_000_000_000) is CapTier.LARGE


# ---------------------------------------------------------------------------------------------------------
# the liquidity at the event
# ---------------------------------------------------------------------------------------------------------


def volume_tape(closes, volumes, start=date(2026, 5, 1)):
    days = weekdays(start, len(closes))
    return [Bar(d, c, v) for d, c, v in zip(days, closes, volumes)]


def test_the_adv_is_the_mean_close_times_volume_of_the_last_20_sessions_before_the_arm_date():
    bars = volume_tape([10.0] * 15 + [20.0] * 15, [100_000.0] * 30)
    arm = bars[25].d  # 25 bars precede it
    prior20 = bars[5:25]
    expected = sum(b.close * b.volume for b in prior20) / 20
    assert event_adv(bars, arm) == pytest.approx(expected)
    # the arm date's own bar and everything after it never reach the figure
    bars[25] = Bar(bars[25].d, 9999.0, 9e9)
    assert event_adv(bars, arm) == pytest.approx(expected)


def test_under_20_volume_bearing_sessions_before_the_event_is_unpinned():
    bars = volume_tape([10.0] * 30, [100_000.0] * 30)
    assert event_adv(bars, bars[19].d) is None  # 19 bars precede it
    assert event_adv(bars, bars[20].d) == pytest.approx(1_000_000.0)  # exactly 20
    # bars without a volume are skipped, not counted as zero
    holes = volume_tape([10.0] * 30, [None if i % 3 == 0 else 100_000.0 for i in range(30)])
    assert event_adv(holes, holes[29].d) is None  # fewer than 20 volume-bearing before it
    assert event_adv(holes, date(2027, 1, 1)) == pytest.approx(
        1_000_000.0
    )  # the last 20 with a volume


@pytest.mark.parametrize(
    "adv,band",
    [
        (None, None),
        (0.0, "under_1m"),
        (999_999.99, "under_1m"),
        (1_000_000.0, "1m_10m"),
        (9_999_999.0, "1m_10m"),
        (10_000_000.0, "10m_plus"),
        (5e9, "10m_plus"),
        (float("nan"), None),
    ],
)
def test_the_liquidity_bands_and_their_boundaries(adv, band):
    assert liquidity_band(adv) == band


# ---------------------------------------------------------------------------------------------------------
# the row: both entry rules, the left-out episodes
# ---------------------------------------------------------------------------------------------------------

TID, SID = UUID(int=0xA1), UUID(int=0xB1)


def episode(**over) -> EpisodeIn:
    base = dict(
        thesis_id=TID,
        thesis_name="Card A",
        security_id=SID,
        ticker="AAA",
        arm_date=date(2026, 6, 3),
        exit_by=date(2026, 6, 13),
        arm_until=date(2026, 6, 13),
        close_reason="window_end",
        is_headline=True,
        key1_source="insider",
        entry_grade="core",
        confirmation_grade="core",
        co_arm_bucket="alone",
    )
    base.update(over)
    return EpisodeIn(**base)


def company(bars, shares=(), splits=(), view_day=date(2026, 7, 24)) -> CompanyData:
    return CompanyData(
        bars=tuple(bars), shares=tuple(shares), splits=tuple(splits), view_day=view_day
    )


def test_one_row_carries_both_entry_closes_and_both_sets_of_horizon_returns():
    days = weekdays(date(2026, 4, 1), 80)
    bars = [Bar(d, 10.0 + 0.5 * i, 100_000.0) for i, d in enumerate(days)]
    row = build_row(
        episode(arm_date=days[40]),
        company(bars, [shares_fact(4_000_000, days[10])]),
        NO_BENCH,
        mkt(days),
        days[-1],
    )
    arm, nxt = row.arm_night, row.next_session
    assert arm.entry_date == days[40] and nxt.entry_date == days[41]
    assert arm.entry_close == bars[40].close and nxt.entry_close == bars[41].close
    assert arm.spans["h5"].end_date == days[45] and nxt.spans["h5"].end_date == days[46]
    assert arm.spans["h5"].ret == pytest.approx(bars[45].close / bars[40].close - 1)
    assert nxt.spans["h5"].ret == pytest.approx(bars[46].close / bars[41].close - 1)
    # the cap and the liquidity are the same for both: they belong to the event
    assert row.cap.close_date == days[39] and row.cap.value == pytest.approx(
        4_000_000 * bars[39].close
    )
    assert row.liquidity == "1m_10m" and row.adv_usd == pytest.approx(
        sum(b.close * 100_000.0 for b in bars[20:40]) / 20
    )
    assert set(arm.spans) == set(HORIZONS)


def test_a_left_out_episode_keeps_identity_only_and_nothing_is_read_for_it():
    ep = episode(price_flag="mixed-scale price history", censored_start=True, ingest_flagged=True)
    row = build_row(
        ep, company(tape(weekdays(date(2026, 6, 1), 20), 10.0)), NO_BENCH, Market(), ASOF
    )
    assert row.left_out == "flagged" and row.left_out_note == "mixed-scale price history"
    assert (
        row.cap is None and row.arm_night is None and row.next_session is None and not row.counted
    )
    assert build_row(
        episode(censored_start=True, ingest_flagged=True), None, NO_BENCH, Market(), ASOF
    ).left_out == ("censored_start")
    ingest = build_row(
        episode(ingest_flagged=True, ingest_note="armed in the freeze"),
        None,
        NO_BENCH,
        Market(),
        ASOF,
    )
    assert (ingest.left_out, ingest.left_out_note) == ("ingest_flagged", "armed in the freeze")


def test_a_counted_company_with_no_read_is_a_row_with_no_cap_never_dropped():
    row = build_row(episode(), None, NO_BENCH, mkt(weekdays(date(2026, 6, 1), 40)), ASOF)
    assert row.counted and row.cap.gap == "no_price" and slice_key(row, "tier") == UNPINNED
    assert row.liquidity is None and slice_key(row, "liquidity") == UNPINNED
    assert (
        row.arm_night.spans["h5"].status == "path_ended"
    )  # nothing ever printed, the market ran on


# ---------------------------------------------------------------------------------------------------------
# the table: the tiers in size order, unpinned, pooled LAST
# ---------------------------------------------------------------------------------------------------------


def row_of(
    tier: CapTier | None,
    *,
    liquidity: str | None = "1m_10m",
    h20: tuple[str, float | None, float | None] = ("counted", 0.10, 0.04),
    left_out=None,
    ticker="AAA",
) -> RecordRow:
    status, ret, iwm = h20
    spans = {h: Span("maturing") for h in HORIZONS}
    spans["h20"] = Span(status, ret=ret, iwm=iwm, spy=0.0 if ret is not None else None)
    read = EntryRead(date(2026, 6, 3), 10.0, spans)
    return RecordRow(
        thesis_id=TID,
        thesis_name="Card A",
        security_id=uuid.uuid4(),
        ticker=ticker,
        arm_date=date(2026, 6, 3),
        exit_by=None,
        arm_until=None,
        close_reason="window_end",
        is_headline=True,
        key1_source=None,
        entry_grade=None,
        confirmation_grade=None,
        co_arm_bucket="alone",
        left_out=left_out,
        cap=CapCell(value=1.0, tier=tier) if tier else CapCell(gap="no_shares"),
        liquidity=liquidity,
        arm_night=read,
        next_session=read,
    )


def test_the_tiered_table_lists_every_tier_then_unpinned_then_pooled_last():
    rows = [row_of(CapTier.MICRO), row_of(CapTier.MICRO), row_of(CapTier.LARGE), row_of(None)]
    table = table_rows(rows)
    assert [t.key for t in table] == [*TIER_KEYS, UNPINNED, POOLED]
    assert table[-1].key == POOLED  # the pooled row is never first
    by = {t.key: t for t in table}
    assert (by["micro"].episodes, by["large"].episodes, by[UNPINNED].episodes) == (2, 1, 1)
    assert (
        by["nano"].episodes == 0 and by["nano"].cells["h20"].n == 0
    )  # every tier is present, empty or not
    assert (
        by[POOLED].episodes == 4 and by[POOLED].cells["h20"].n == 4
    )  # the pooled row is the whole counted set
    assert by["micro"].cells["h5"].n == 0  # a horizon with nothing counted is empty
    assert by["micro"].cells["h20"].median == pytest.approx(0.06)  # 0.10 - IWM's 0.04


def test_the_liquidity_slice_swaps_the_rows_and_a_left_out_episode_is_in_no_row():
    rows = [
        row_of(CapTier.MICRO, liquidity="under_1m"),
        row_of(CapTier.MICRO, liquidity="10m_plus"),
        row_of(CapTier.MICRO, liquidity=None),
        row_of(CapTier.MICRO, left_out="censored_start"),
    ]
    table = table_rows(rows, slice_by="liquidity")
    assert [t.key for t in table] == [*LIQUIDITY_KEYS, UNPINNED, POOLED]
    by = {t.key: t.episodes for t in table}
    assert by == {"under_1m": 1, "1m_10m": 0, "10m_plus": 1, UNPINNED: 1, POOLED: 3}


def test_the_table_reads_the_picked_comparison_and_the_picked_entry_rule():
    r = row_of(CapTier.SMALL)
    assert table_rows([r], comparison="iwm")[2].cells["h20"].median == pytest.approx(0.06)
    assert table_rows([r], comparison="spy")[2].cells["h20"].median == pytest.approx(0.10)
    assert table_rows([r], comparison="raw")[2].cells["h20"].median == pytest.approx(0.10)
    assert table_rows([r], entry="next_session")[2].cells["h20"].n == 1


def test_the_counts_say_what_every_cell_rests_on():
    rows = [
        row_of(CapTier.MICRO),
        row_of(CapTier.MICRO, h20=("maturing", None, None)),
        row_of(None, liquidity=None, h20=("path_ended", None, None)),
        row_of(CapTier.SMALL, left_out="flagged"),
        row_of(CapTier.SMALL, left_out="censored_start"),
        row_of(CapTier.SMALL, left_out="ingest_flagged"),
        row_of(CapTier.SMALL, left_out="flagged"),
    ]
    counts = count_rows(rows)
    assert (counts.episodes, counts.counted) == (7, 3)
    assert (counts.left_out_flagged, counts.left_out_censored, counts.left_out_ingest) == (2, 1, 1)
    assert (counts.unpinned_cap, counts.unpinned_liquidity) == (1, 1)
    h20 = counts.arm_night["h20"]
    assert (h20.counted, h20.maturing, h20.path_ended, h20.none) == (1, 1, 1, 0)
    assert counts.arm_night["h5"].maturing == 3  # only the counted episodes are read


def test_assemble_sorts_newest_arm_first_and_names_the_market_clock_and_a_missing_benchmark():
    early, late = row_of(CapTier.MICRO, ticker="ZZZ"), row_of(CapTier.MICRO, ticker="AAA")
    late = RecordRow(**{**late.__dict__, "arm_date": date(2026, 6, 9)})
    flagged = [
        FlaggedCompany(UUID(int=2), "ZED", "price feed stopped"),
        FlaggedCompany(UUID(int=1), "ABC", "mixed-scale price history"),
    ]
    spy = tape(weekdays(date(2026, 6, 1), 3), 400.0)
    page = assemble(
        [early, late],
        flagged=flagged,
        market=market_of(spy, []),
        bench=benchmarks_of(spy, []),
        cards=2,
    )
    assert [r.ticker for r in page.rows] == ["AAA", "ZZZ"]
    assert [f.ticker for f in page.flagged] == ["ABC", "ZED"]
    assert page.market_source == "SPY" and page.benchmarks_missing == ("IWM",)
    assert page.cards == 2 and page.cards_unreadable == 0
    assert [t.key for t in page.opening][-1] == POOLED


def test_the_market_clock_falls_back_to_iwm_and_then_to_nothing():
    days = weekdays(date(2026, 6, 1), 5)
    assert market_of(tape(days, 1.0), tape(days[:3], 1.0)).source == "SPY"
    assert market_of([], tape(days[:3], 1.0)).source == "IWM"
    assert market_of([], []).source is None


def test_bars_of_drops_a_bar_with_no_positive_close_and_sorts_by_date():
    rows = [
        {"d": date(2026, 6, 2), "close": 11.0, "volume": 5},
        {"d": date(2026, 6, 1), "close": 10.0, "volume": None},
        {"d": date(2026, 6, 3), "close": None, "volume": 5},
        {"d": date(2026, 6, 4), "close": 0.0, "volume": 5},
        {"d": date(2026, 6, 5), "close": -3.0, "volume": 5},
    ]
    assert bars_of(rows) == [Bar(date(2026, 6, 1), 10.0, None), Bar(date(2026, 6, 2), 11.0, 5.0)]


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------

URL = "/dashboards/track-record"


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": [], "editions": [], "newest": None}
    days = weekdays(date(2026, 6, 1), 40)
    bars = [Bar(d, 10.0 + i, 100_000.0) for i, d in enumerate(days)]

    def fake_read(conn, asof, known_at, clock, edition, exclude_annotated, exclude_late):
        seen["reads"].append((asof, known_at, clock, edition, exclude_annotated, exclude_late))
        market = market_of(bars, [])
        row = build_row(
            episode(arm_date=days[20], exit_by=days[30]),
            company(bars, [shares_fact(4_000_000, days[2])]),
            benchmarks_of(bars, bars),
            market,
            days[-1],
        )
        left = build_row(
            episode(ticker="BBB", censored_start=True), None, NO_BENCH, market, days[-1]
        )
        return assemble(
            [row, left],
            flagged=[FlaggedCompany(UUID(int=7), "FFF", "price feed stopped")],
            market=market,
            bench=benchmarks_of(bars, bars),
            cards=1,
        )

    def fake_resolve(conn, tenant, requested):
        seen["editions"].append(requested)
        return None

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_record", fake_read)
    monkeypatch.setattr(router_mod, "_resolve_edition", fake_resolve)
    monkeypatch.setattr(router_mod, "_newest_edition", lambda conn, tenant: seen["newest"])
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_one_cached_read_on_the_requests_view_keyed_by_the_readings(stub_client):
    client, seen = stub_client
    first = client.get(URL, params={"asof": "2026-07-24"}).json()
    assert (first["cache"], first["clock"]) == ("computed", "public")
    ((asof, known_at, clock, edition, ex_annotated, ex_late),) = seen["reads"]
    assert asof == ASOF and clock == "public" and known_at is not None and edition is None
    assert seen["editions"] == [
        0
    ]  # a request that names no edition asks for the record, not the newest
    again = client.get(URL, params={"asof": "2026-07-24"}).json()
    assert again["cache"] == "hit" and len(seen["reads"]) == 1
    # the Scoreboard's own honesty readings are separate reads
    client.get(URL, params={"asof": "2026-07-24", "include_annotated": "true"})
    assert len(seen["reads"]) == 2 and seen["reads"][-1][4] is False
    client.get(URL, params={"asof": "2026-07-24", "include_late": "true"})
    assert len(seen["reads"]) == 3 and seen["reads"][-1][5] is False
    # another as-of is its own read; a live as-of is the unchanged live read
    client.get(URL, params={"asof": "2026-07-23"})
    client.get(URL, params={"asof": market_today().isoformat()})
    assert seen["reads"][-1][1:3] == (None, "record") and len(seen["reads"]) == 5


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(URL, params={"asof": "2026-07-24"}).json()
    assert (
        body["asof"] == "2026-07-24"
        and body["horizons"] == list(HORIZONS)
        and body["min_n"] == MIN_N
    )
    assert body["edition"] == {"number": 0, "label": "the original record"}
    assert body["newest_edition"] is None  # nothing published
    assert body["annotated_policy"] in ("included", "excluded")
    assert body["late_policy"] in ("included", "excluded")
    assert (
        body["market_source"] == "SPY"
        and body["benchmarks_missing"] == []
        and body["stale_days"] == 5
    )
    assert body["cards"] == 1 and body["cards_unreadable"] == 0
    assert body["flagged"] == [
        {"security_id": str(UUID(int=7)), "ticker": "FFF", "reason": "price feed stopped"}
    ]
    counts = body["counts"]
    assert (counts["episodes"], counts["counted"], counts["left_out_censored"]) == (2, 1, 1)
    assert set(counts["arm_night"]) == set(HORIZONS)
    counted, left = body["rows"]
    assert counted["left_out"] is None and left["left_out"] == "censored_start"
    assert left["cap"] is None and left["arm_night"] is None and left["next_session"] is None
    assert counted["cap"]["tier"] == "micro"  # 4M shares x the $29 close before the arm date: $116M
    assert set(counted["arm_night"]["spans"]) == set(HORIZONS)
    span = counted["arm_night"]["spans"]["h5"]
    assert span["status"] == "counted" and set(span) == {"status", "end_date", "ret", "iwm", "spy"}
    assert counted["next_session"]["entry_date"] > counted["arm_night"]["entry_date"]
    assert [t["key"] for t in body["opening"]] == [*TIER_KEYS, UNPINNED, POOLED]
    assert set(body["opening"][-1]["cells"]) == set(HORIZONS)


def test_an_edition_argument_is_resolved_by_the_scoreboards_own_rule_and_skips_the_policies(
    stub_client, monkeypatch
):
    client, seen = stub_client
    edition = type("E", (), {"number": 2, "label": "first re-run"})()
    monkeypatch.setattr(router_mod, "_resolve_edition", lambda c, t, requested: edition)
    body = client.get(URL, params={"asof": "2026-07-24", "edition": 2}).json()
    assert body["edition"] == {"number": 2, "label": "first re-run"}
    assert (body["annotated_policy"], body["late_policy"]) == ("n/a", "n/a")
    assert seen["reads"][-1][3] is edition and seen["reads"][-1][4:] == (False, False)


def test_with_editions_published_a_request_that_names_none_still_reads_the_record(stub_client):
    """The editions nightly is paused (the operator's hold), so the default is Edition 0, the calls log. The newest
    published edition rides the response for the page's selector; naming an edition reads that one.
    """
    client, seen = stub_client
    seen["newest"] = type("E", (), {"number": 3, "label": "third re-run"})()
    body = client.get(URL, params={"asof": "2026-07-24"}).json()
    assert body["edition"] == {"number": 0, "label": "the original record"}
    assert body["newest_edition"] == {"number": 3, "label": "third re-run"}
    assert seen["editions"] == [0] and seen["reads"][-1][3] is None
    # an explicit 0 is the same read (one cache entry), and an explicit N asks the Scoreboard's rule for N
    again = client.get(URL, params={"asof": "2026-07-24", "edition": 0}).json()
    assert again["cache"] == "hit" and seen["editions"] == [0, 0]
    client.get(URL, params={"asof": "2026-07-24", "edition": 3})
    assert seen["editions"] == [0, 0, 3]
    assert client.get(URL, params={"asof": "2026-07-24", "edition": -1}).status_code == 422


def test_the_router_requires_an_asof(stub_client):
    client, _ = stub_client
    assert client.get(URL).status_code == 422


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the real reads (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

# One weekday tape (06-19 is a trading day here: the holiday gap is the pure tests' job), Apr 1 - Sep 4.
TAPE_DAYS = weekdays(date(2026, 4, 1), 110)
INGESTED = datetime(2026, 9, 10, tzinfo=UTC)  # our own ingest instant, after every as-of below


def _security(db, ticker: str, kind: str = "equity", cik: str | None = None) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, f"{ticker} Inc", "2026-01-01"),
        )
    return sid


def _bars(
    db, sid: UUID, closes: dict[date, float], volume: float | None = None, until: date | None = None
) -> None:
    for d, close in closes.items():
        if until is not None and d > until:
            continue
        append_fact(
            db,
            "fact_price_eod",
            {
                "tenant_id": DEFAULT_TENANT_ID,
                "security_id": sid,
                "d": d,
                "close": close,
                "volume": volume,
                "valid_from": d,
            },
        )
    db.commit()


def _shares(db, sid, shares, valid_from, recorded, ref) -> None:
    append_fact(
        db,
        "fact_shares_outstanding",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "shares": shares,
            "source": "10-q",
            "source_ref": ref,
            "valid_from": valid_from,
            "recorded_at": recorded,
        },
    )
    db.commit()


def _card(db, name: str, sid: UUID, ticker: str) -> Thesis:
    thesis = make_thesis(
        id=uuid.uuid4(),
        name=name,
        ticker=ticker,
        basket=[BasketMember(ticker=ticker, role="the name", security_id=sid)],
        evidence=[],
        catalysts=[],
        kill_criteria=[],
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis_repo.get(db, thesis.id)


def _arm(
    db, thesis: Thesis, sid: UUID, arm: date, *, warm: date | None, liveness: int = 10
) -> None:
    """The record: a warming night first (so the arm is not a censored start), then the armed night. exit_by =
    arm_until = arm + liveness days."""
    if warm is not None:
        record_day(
            db,
            thesis,
            [insider_event(security_id=sid, liveness=60).model_copy(update={"asof": warm})],
            warm,
        )
    conv, conf = keys_fired(sid, arm, conv_liveness=liveness, conf_liveness=liveness)
    record_day(db, thesis, [conv, conf], arm)


def _get(db, **params):
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        return TestClient(app).get(URL, params=params)
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()


@pytest.fixture
def world(db):
    """SPY and IWM, one name (DEVCO) with a tape and volume, a card that warmed on 06-01 and armed on Wed 06-03 with
    exit_by Sat 06-13, and a shares fact filed before the arm date."""
    spy = _security(db, "SPY", "etf")
    iwm = _security(db, "IWM", "etf")
    dev = _security(db, "DEVCO", cik="0001234567")
    _bars(db, spy, {d: 400.0 + i for i, d in enumerate(TAPE_DAYS)})
    _bars(db, iwm, {d: 200.0 + 0.5 * i for i, d in enumerate(TAPE_DAYS)})
    closes = {d: 10.0 + 0.1 * i for i, d in enumerate(TAPE_DAYS)}
    _bars(db, dev, closes, volume=200_000.0)
    _shares(db, dev, 40_000_000, date(2026, 5, 12), datetime(2026, 5, 14, tzinfo=UTC), "dev-10q-1")
    card = _card(db, "Card A", dev, "DEVCO")
    _arm(db, card, dev, date(2026, 6, 3), warm=date(2026, 6, 1))
    return {"spy": spy, "iwm": iwm, "dev": dev, "closes": closes, "card": card}


def test_an_episode_armed_after_the_asof_is_not_an_event_and_an_unmatured_horizon_is_not_counted(
    db, pinned_today, world
):
    """The same episode at three as-ofs. Before it armed it is not an event. Five sessions in, h5 is counted and h10
    is maturing (the DB holds the later bars, and none of them reaches the earlier view). Later, h10 counts and h20 is
    still maturing."""
    closes = world["closes"]
    before = _get(db, asof="2026-06-02").json()
    assert before["rows"] == [] and before["counts"]["episodes"] == 0

    at5 = _get(db, asof="2026-06-10").json()  # 06-03 + 5 sessions
    (row,) = at5["rows"]
    assert (
        row["thesis_name"] == "Card A"
        and row["ticker"] == "DEVCO"
        and row["arm_date"] == "2026-06-03"
    )
    spans = row["arm_night"]["spans"]
    assert spans["h5"]["status"] == "counted" and spans["h5"]["end_date"] == "2026-06-10"
    assert spans["h5"]["ret"] == pytest.approx(
        closes[date(2026, 6, 10)] / closes[date(2026, 6, 3)] - 1
    )
    assert spans["h10"]["status"] == "maturing" and spans["h10"]["ret"] is None
    assert spans["exit_by"]["status"] == "maturing"  # exit_by 06-13 is after the as-of
    assert at5["clock"] == "public"

    at15 = _get(db, asof="2026-06-24").json()
    spans = at15["rows"][0]["arm_night"]["spans"]
    assert spans["h10"]["status"] == "counted" and spans["h10"]["end_date"] == "2026-06-17"
    assert spans["h20"]["status"] == "maturing" and spans["h60"]["status"] == "maturing"
    # exit_by Sat 06-13: the last bar on or before it is Fri 06-12
    assert spans["exit_by"]["status"] == "counted" and spans["exit_by"]["end_date"] == "2026-06-12"
    counts = at15["counts"]
    assert counts["arm_night"]["h5"]["counted"] == 1 and counts["arm_night"]["h20"]["maturing"] == 1


def test_the_benchmarks_return_over_the_same_two_dates_rides_the_row(db, pinned_today, world):
    body = _get(db, asof="2026-06-24").json()
    span = body["rows"][0]["arm_night"]["spans"]["h10"]
    i0, i1 = TAPE_DAYS.index(date(2026, 6, 3)), TAPE_DAYS.index(date(2026, 6, 17))
    assert span["iwm"] == pytest.approx((200.0 + 0.5 * i1) / (200.0 + 0.5 * i0) - 1)
    assert span["spy"] == pytest.approx((400.0 + i1) / (400.0 + i0) - 1)
    assert body["market_source"] == "SPY" and body["benchmarks_missing"] == []


def test_the_cap_and_the_liquidity_are_measured_at_the_event_not_at_the_asof(
    db, pinned_today, world
):
    closes = world["closes"]
    body = _get(db, asof="2026-06-24").json()
    row = body["rows"][0]
    cap = row["cap"]
    assert cap["close_date"] == "2026-06-02"  # the session BEFORE the 06-03 arm
    assert cap["close"] == pytest.approx(closes[date(2026, 6, 2)])
    assert cap["shares"] == 40_000_000.0 and cap["shares_date"] == "2026-05-12"
    assert cap["value"] == pytest.approx(round(40_000_000 * closes[date(2026, 6, 2)]))
    assert cap["tier"] == "small"
    prior = [d for d in TAPE_DAYS if d < date(2026, 6, 3)][-20:]
    expected_adv = sum(closes[d] * 200_000.0 for d in prior) / 20
    assert row["adv_usd"] == pytest.approx(expected_adv, abs=0.01)
    assert row["liquidity"] == "1m_10m"  # about $2.7M a day
    opening = {t["key"]: t for t in body["opening"]}
    assert opening["small"]["episodes"] == 1 and opening[POOLED]["episodes"] == 1
    assert (
        opening["small"]["cells"]["h5"]["n"] == 1
        and opening["small"]["cells"]["h5"]["too_small"] is True
    )


def test_a_share_fact_published_after_the_asof_never_reaches_the_cap(db, pinned_today, world):
    """A newer count (cover date 05-28, before the arm) is ratified on 06-20. A view before that day reads the older
    count; a view after it reads the newer one. A count whose cover date is AFTER the arm date is never used.
    """
    dev = world["dev"]
    _shares(
        db, dev, 90_000_000, date(2026, 5, 28), datetime(2026, 6, 20, 12, tzinfo=UTC), "dev-10q-2"
    )
    _shares(db, dev, 99_000_000, date(2026, 6, 10), datetime(2026, 6, 11, tzinfo=UTC), "dev-10q-3")
    early = _get(db, asof="2026-06-15").json()["rows"][0]["cap"]
    late = _get(db, asof="2026-06-24").json()["rows"][0]["cap"]
    assert (early["shares"], early["shares_date"]) == (40_000_000.0, "2026-05-12")
    assert (late["shares"], late["shares_date"]) == (90_000_000.0, "2026-05-28")


def test_no_share_fact_dated_before_the_arm_is_an_unpinned_cap_counted_in_the_pooled_row(
    db, pinned_today, world
):
    other = _security(db, "NOSHR", cik="0007654321")
    _bars(db, other, {d: 5.0 + 0.05 * i for i, d in enumerate(TAPE_DAYS)}, volume=50_000.0)
    _arm(db, _card(db, "Card B", other, "NOSHR"), other, date(2026, 6, 3), warm=date(2026, 6, 1))
    body = _get(db, asof="2026-06-24").json()
    by = {r["ticker"]: r for r in body["rows"]}
    assert by["NOSHR"]["cap"]["gap"] == "no_shares" and by["NOSHR"]["cap"]["tier"] is None
    assert (
        by["NOSHR"]["arm_night"]["spans"]["h10"]["status"] == "counted"
    )  # unpinned is counted, never dropped
    opening = {t["key"]: t for t in body["opening"]}
    assert opening[UNPINNED]["episodes"] == 1 and opening[POOLED]["episodes"] == 2
    assert body["counts"]["unpinned_cap"] == 1


def test_a_flagged_price_history_and_a_censored_start_are_left_out_of_every_cell_and_counted(
    db, pinned_today, world
):
    """STOPD's tape ends 12 days before the as-of (the feed stopped: a flag that reaches its prices), so its episode
    is left out and the company is named. CENS armed on the card's very first recorded night: a censored start.
    """
    stopd = _security(db, "STOPD", cik="0007000001")
    closes = {d: 8.0 + 0.05 * i for i, d in enumerate(TAPE_DAYS)}
    _bars(db, stopd, closes, volume=80_000.0, until=date(2026, 6, 12))
    _arm(db, _card(db, "Card C", stopd, "STOPD"), stopd, date(2026, 6, 3), warm=date(2026, 6, 1))
    cens = _security(db, "CENS", cik="0007000002")
    _bars(db, cens, closes, volume=80_000.0)
    _arm(db, _card(db, "Card D", cens, "CENS"), cens, date(2026, 6, 3), warm=None)

    body = _get(db, asof="2026-06-24").json()
    by = {r["ticker"]: r for r in body["rows"]}
    assert (
        by["STOPD"]["left_out"] == "flagged" and "stopped" in by["STOPD"]["left_out_note"].lower()
    )
    assert by["CENS"]["left_out"] == "censored_start"
    for t in ("STOPD", "CENS"):
        assert by[t]["cap"] is None and by[t]["arm_night"] is None and by[t]["next_session"] is None
    assert by["DEVCO"]["left_out"] is None
    counts = body["counts"]
    assert (counts["episodes"], counts["counted"]) == (3, 1)
    assert (counts["left_out_flagged"], counts["left_out_censored"], counts["left_out_ingest"]) == (
        1,
        1,
        0,
    )
    assert [f["ticker"] for f in body["flagged"]] == ["STOPD"]
    pooled = {t["key"]: t for t in body["opening"]}[POOLED]
    assert pooled["episodes"] == 1  # the left-out episodes are in no row, pooled included


def test_the_exit_by_column_reconciles_with_the_scoreboards_forward_return_to_the_cent(
    db, pinned_today, world
):
    from scoreboard.record import scoreboard_records

    asof = date(2026, 6, 24)
    result, _, _ = scoreboard_records(db, asof)
    (scored,) = [se for t in result.theses for se in t.episodes]
    body = _get(db, asof=asof.isoformat()).json()
    span = body["rows"][0]["arm_night"]["spans"]["exit_by"]
    assert span["status"] == "counted"
    assert scored.outcome.entry_close == body["rows"][0]["arm_night"]["entry_close"]
    assert scored.outcome.exit_date.isoformat() == span["end_date"]
    assert span["ret"] == pytest.approx(scored.outcome.forward_return, abs=1e-12)


def test_the_next_session_entry_rides_the_same_row(db, pinned_today, world):
    closes = world["closes"]
    row = _get(db, asof="2026-06-24").json()["rows"][0]
    assert (
        row["arm_night"]["entry_date"] == "2026-06-03"
        and row["next_session"]["entry_date"] == "2026-06-04"
    )
    nxt = row["next_session"]["spans"]["h5"]
    assert nxt["end_date"] == "2026-06-11"
    assert nxt["ret"] == pytest.approx(closes[date(2026, 6, 11)] / closes[date(2026, 6, 4)] - 1)


def test_a_live_asof_is_the_unchanged_record_read(db, pinned_today):
    """The live view (the record clock): one card armed ten sessions before today, a tape through today."""
    days = [d for d in weekdays(pinned_today - timedelta(days=90), 80) if d <= pinned_today]
    spy = _security(db, "SPY", "etf")
    dev = _security(db, "LIVEO", cik="0007000003")
    _bars(db, spy, {d: 400.0 + i for i, d in enumerate(days)})
    _bars(db, dev, {d: 10.0 + 0.1 * i for i, d in enumerate(days)}, volume=100_000.0)
    arm = days[-12]
    _arm(db, _card(db, "Card E", dev, "LIVEO"), dev, arm, warm=days[-14], liveness=40)
    body = _get(db, asof=pinned_today.isoformat()).json()
    assert body["clock"] == "record"
    (row,) = body["rows"]
    assert row["left_out"] is None and row["arm_date"] == arm.isoformat()
    assert row["arm_night"]["spans"]["h5"]["status"] == "counted"
    assert row["arm_night"]["spans"]["h20"]["status"] == "maturing"


def test_an_unknown_edition_is_a_404_like_the_scoreboards(db, pinned_today):
    assert _get(db, asof="2026-06-24", edition=7).status_code == 404


def test_the_record_is_the_default_with_an_edition_published_and_the_newest_rides_the_response(
    db, pinned_today, world
):
    """Edition 1 is published (a page of the world's card). A request that names no edition reads the RECORD
    (Edition 0, the calls log), and `newest_edition` names Edition 1; naming it reads the stored edition.
    """
    with db.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT card FROM calls WHERE thesis_id = %s ORDER BY asof DESC, seq DESC LIMIT 1",
            (world["card"].id,),
        )
        page = CallCard.model_validate(cur.fetchone()["card"])
    cut = datetime(2026, 6, 25, 2, 0, tzinfo=timezone.utc)
    editions_repo.publish_edition(
        db,
        tenant_id=DEFAULT_TENANT_ID,
        number=1,
        label="first re-run",
        causes=["initial"],
        engine={"fingerprint": "f" * 64, "config_hash": "c" * 64, "code_sha": "abc1234def"},
        data_known_by=cut,
        pages=[page],
        entries=[],
        computed_at=cut,
    )
    db.commit()

    default = _get(db, asof="2026-06-24")
    assert default.status_code == 200
    body = default.json()
    assert body["edition"] == {"number": 0, "label": "the original record"}
    assert body["newest_edition"] == {"number": 1, "label": "first re-run"}
    assert body["counts"]["episodes"] == 1  # the record's episode

    named = _get(db, asof="2026-06-24", edition=1).json()
    assert named["edition"] == {"number": 1, "label": "first re-run"}
    assert named["newest_edition"] == {"number": 1, "label": "first re-run"}
    assert (named["annotated_policy"], named["late_policy"]) == ("n/a", "n/a")
