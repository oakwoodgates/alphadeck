"""Price & Volume Landing (``docs/dashboards/landing.md``): the per-name arithmetic on hand-built bars (range
position, the window and prior-window boundaries in trading bars, the volume ratio's rising rule, every
quadrant, up/down-day volume), the pure grouping (thin groups, the quadrant split, who counts in each view),
the router (pure, with the heavy read stubbed), and one DB-backed past-as-of read proving a bar published after
the as-of stays invisible to it."""

from __future__ import annotations

import uuid
from dataclasses import asdict
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import landing as router_mod
from dashboards.baskets import CardsAsOf, Membership
from dashboards.cache import CACHE
from dashboards.landing import (
    LOOKBACK_DAYS,
    MIN_PRICED,
    NO_READING,
    RISING_MULT,
    WINDOW_KEYS,
    CompanyLanding,
    LandingInputs,
    WindowReading,
    company_landing,
    landing,
    quadrant,
    range_position,
    rising,
    up_down_volume,
    volume_ratio,
    window_return,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo
from signals.display import sma
from signals.horizons import display_bounds

ASOF = date(2026, 6, 1)

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def bar(d: date, close: float, *, high=None, low=None, volume: float | None = 100.0) -> dict:
    """One EOD bar; ``high`` / ``low`` default to the close +/- 1 (pass ``None`` via ``high=None`` after
    building to test a missing one)."""
    return {
        "d": d,
        "close": close,
        "high": close + 1.0 if high is None else high,
        "low": close - 1.0 if low is None else low,
        "volume": volume,
    }


def series(closes, *, volumes=None, end: date = ASOF, step_days: int = 1) -> list[dict]:
    """Ascending bars ending at ``end``, one per ``step_days`` calendar days."""
    n = len(closes)
    vols = volumes if volumes is not None else [100.0] * n
    return [
        bar(end - timedelta(days=step_days * (n - 1 - i)), c, volume=v)
        for i, (c, v) in enumerate(zip(closes, vols))
    ]


def reading(key: str, **kw) -> WindowReading:
    return WindowReading(key=key, **kw)


def company(priced: bool = True, **per_window) -> CompanyLanding:
    """A company's readings; ``per_window={"7d": dict(range_pos=..., quadrant=...)}``."""
    return CompanyLanding(
        bars=200 if priced else 0,
        last_bar=ASOF if priced else None,
        priced=priced,
        not_priced=None if priced else "no_tape",
        windows=tuple(reading(k, **per_window.get(k, {})) for k in WINDOW_KEYS),
    )


def ms(
    tid: UUID, card: str, ticker: str, sid: UUID | None, segment: str | None = "Core"
) -> Membership:
    return Membership(
        thesis_id=tid,
        thesis_name=card,
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
    )


def inputs(memberships, readings, **kw) -> LandingInputs:
    base = dict(
        live_cards=len({m.thesis_id for m in memberships}),
        cards_counted=len({m.thesis_id for m in memberships}),
        cards_not_yet_created=0,
        roster_fallback_cards=0,
    )
    base.update(kw)
    return LandingInputs(memberships=tuple(memberships), readings=readings, **base)


def grp(result, key_part: str):
    return next(g for g in result.groups if key_part in g.key)


def win(group, key: str = "7d"):
    return next(w for w in group.windows if w.key == key)


T1, T2 = uuid.uuid4(), uuid.uuid4()
S = [uuid.uuid4() for _ in range(8)]


# ---------------------------------------------------------------------------------------------------------
# range position
# ---------------------------------------------------------------------------------------------------------


def test_range_position_at_the_low_the_high_and_in_between():
    bars = series([10.0, 12.0, 11.0])  # window high 13, low 9 (each close +/- 1)
    bars[-1] = bar(ASOF, 9.0, high=11.0, low=9.0)  # closed on the window's low
    assert range_position(bars, 3) == 0.0
    bars[-1] = bar(ASOF, 13.0, high=13.0, low=12.0)  # closed on the window's high
    assert range_position(bars, 3) == 100.0
    bars[-1] = bar(ASOF, 11.0, high=11.5, low=10.5)  # (11 - 9) / (13 - 9)
    assert range_position(bars, 3) == 50.0


def test_a_zero_width_range_or_a_missing_high_or_low_reads_no_position():
    flat = [
        bar(ASOF - timedelta(days=1), 5.0, high=5.0, low=5.0),
        bar(ASOF, 5.0, high=5.0, low=5.0),
    ]
    assert range_position(flat, 2) is None
    bars = series([10.0, 11.0, 12.0])
    bars[0]["high"] = None
    assert range_position(bars, 3) is None
    assert range_position(bars, 2) is not None  # that bar is outside the 2-bar window
    bars[-1]["low"] = None
    assert range_position(bars, 1) is None
    assert range_position(series([10.0]), 2) is None  # fewer bars than the window


def test_a_close_outside_its_bars_range_reads_at_the_edge():
    bars = series([10.0, 11.0])
    bars[-1] = bar(ASOF, 14.0, high=13.0, low=10.5)  # a vendor close above its own high
    assert range_position(bars, 2) == 100.0


def test_the_1d_range_is_the_asof_bars_own_range():
    bars = series([50.0, 20.0, 30.0])  # earlier bars far outside the as-of bar's range
    bars[-1] = bar(ASOF, 30.0, high=32.0, low=22.0)
    assert range_position(bars, 1) == 80.0  # (30 - 22) / (32 - 22): the earlier bars never enter
    got = company_landing(bars, ASOF)
    one = next(w for w in got.windows if w.key == "1d")
    assert one.range_pos == 80.0


# ---------------------------------------------------------------------------------------------------------
# the window boundaries, in trading bars
# ---------------------------------------------------------------------------------------------------------


def test_the_windows_count_trading_bars_and_the_prior_window_is_the_n_bars_before():
    """22 bars two calendar days apart (so calendar days could never be the unit). The 7D window = the last 7
    bars; its prior window = bars 8..14 back; bar 15 back and earlier never enter the volume ratio.
    """
    closes = [10.0] * 22
    vols = [100.0] * 22
    vols[-15] = 1_000_000.0  # bar 15 back: outside both windows
    for i in range(1, 8):
        vols[-i] = 20.0  # this window: 7 x 20 = 140
    for i in range(8, 15):
        vols[-i] = 10.0  # the prior window: 7 x 10 = 70
    bars = series(closes, volumes=vols, step_days=2)
    bars[-8] = bar(
        bars[-8]["d"], 10.0, high=99.0, low=1.0, volume=10.0
    )  # 8 back: outside the range
    bars[-7] = bar(
        bars[-7]["d"], 10.0, high=12.0, low=10.0, volume=20.0
    )  # 7 back: the window's high
    bars[-1] = bar(ASOF, 11.0, high=11.0, low=8.0, volume=20.0)  # the window's low is 8
    assert volume_ratio(bars, 7) == 2.0
    assert range_position(bars, 7) == 75.0  # (11 - 8) / (12 - 8): the 99 / 1 bar is 8 back, outside
    # the return is the close vs the close 7 bars back (bars[-8])
    assert window_return([float(b["close"]) for b in bars], 7) == (10.0, 1)
    # a tape of exactly 2N bars has a ratio; one bar fewer does not
    assert volume_ratio(bars[-14:], 7) == 2.0
    assert volume_ratio(bars[-13:], 7) is None


def test_the_1d_volume_ratio_compares_the_asof_bar_with_the_single_bar_before():
    bars = series([10.0, 10.0, 11.0], volumes=[999.0, 50.0, 100.0])
    assert volume_ratio(bars, 1) == 2.0


def test_a_volumeless_bar_or_a_zero_prior_window_reads_no_ratio():
    bars = series([10.0] * 4, volumes=[100.0, None, 100.0, 100.0])
    assert volume_ratio(bars, 2) is None
    assert volume_ratio(bars, 1) == 1.0  # the volumeless bar is outside the 1D windows
    assert volume_ratio(series([10.0] * 2, volumes=[0.0, 100.0]), 1) is None


# ---------------------------------------------------------------------------------------------------------
# the rising rule and the quadrants
# ---------------------------------------------------------------------------------------------------------


def test_a_ratio_of_exactly_1_2_is_not_rising_and_1_21_is():
    assert RISING_MULT == 1.2
    at = volume_ratio(series([10.0, 11.0], volumes=[100.0, 120.0]), 1)
    above = volume_ratio(series([10.0, 11.0], volumes=[100.0, 121.0]), 1)
    assert at == 1.2 and rising(at) is False
    assert above == 1.21 and rising(above) is True
    # the judged ratio is the shown (2-decimal) one: 1.204 shows 1.20 and is not rising
    near = volume_ratio(series([10.0, 11.0], volumes=[1000.0, 1204.0]), 1)
    assert near == 1.2 and rising(near) is False


def test_every_quadrant_including_flat():
    assert quadrant(1, 1.5) == "accumulation"
    assert quadrant(1, 1.2) == "thin_rally"
    assert quadrant(-1, 1.21) == "distribution"
    assert quadrant(-1, 0.4) == "rest"
    assert quadrant(0, 3.0) == "flat"
    assert quadrant(0, None) == "flat"  # an unchanged close is flat whatever the volume did
    assert quadrant(1, None) is None  # a move without a volume ratio: no reading
    assert quadrant(None, 1.5) is None  # no return: no reading


def test_the_quadrants_read_off_real_bars():
    def one_day(prev: float, last: float, v0: float, v1: float) -> str | None:
        got = company_landing(series([prev, last], volumes=[v0, v1]), ASOF)
        return next(w for w in got.windows if w.key == "1d").quadrant

    assert one_day(10.0, 11.0, 100.0, 200.0) == "accumulation"
    assert one_day(10.0, 11.0, 100.0, 100.0) == "thin_rally"
    assert one_day(10.0, 9.0, 100.0, 200.0) == "distribution"
    assert one_day(10.0, 9.0, 100.0, 50.0) == "rest"
    assert one_day(10.0, 10.0, 100.0, 500.0) == "flat"


def test_the_return_sign_is_exact_and_a_non_positive_base_reads_none():
    assert window_return([10.0, 10.0001], 1) == (
        0.0,
        1,
    )  # rounds to 0.00% but the close did move up
    assert window_return([10.0, 10.0], 1) == (0.0, 0)
    assert window_return([0.0, 10.0], 1) == (None, None)
    assert window_return([10.0], 1) == (None, None)


# ---------------------------------------------------------------------------------------------------------
# up/down-day volume
# ---------------------------------------------------------------------------------------------------------


def test_up_down_day_volume_sums_each_side_and_skips_unchanged_days():
    # vs the prior close: up (300), down (100), unchanged (900, neither), down (200)
    bars = series([10.0, 11.0, 10.5, 10.5, 10.0], volumes=[5.0, 300.0, 100.0, 900.0, 200.0])
    assert up_down_volume(bars, 4) == 1.0  # 300 / (100 + 200)
    assert up_down_volume(bars, 2) == 0.0  # unchanged + down: no up-day volume


def test_up_down_day_volume_with_no_down_days_reads_none():
    bars = series([10.0, 11.0, 12.0, 12.0], volumes=[100.0] * 4)
    assert up_down_volume(bars, 3) is None
    assert up_down_volume(series([10.0, 11.0], volumes=[100.0, 100.0]), 1) is None
    # a down day with no volume is no down-day volume
    assert up_down_volume(series([10.0, 9.0], volumes=[100.0, 0.0]), 1) is None
    # the first day needs the close before it
    assert up_down_volume(series([10.0, 9.0]), 2) is None


# ---------------------------------------------------------------------------------------------------------
# priced or not
# ---------------------------------------------------------------------------------------------------------


def test_a_stale_or_empty_tape_is_counted_as_not_priced_and_reads_no_window():
    stale = series([10.0, 11.0], end=ASOF - timedelta(days=sma.STALE_GAP_DAYS + 1))
    got = company_landing(stale, ASOF)
    assert got.priced is False and got.not_priced == "stale_tape" and got.bars == 2
    assert all(w.range_pos is None and w.quadrant is None for w in got.windows)
    edge = company_landing(
        series([10.0, 11.0], end=ASOF - timedelta(days=sma.STALE_GAP_DAYS)), ASOF
    )
    assert edge.priced is True
    assert company_landing([], ASOF) == NO_READING
    assert company_landing([{"d": ASOF, "close": None}], ASOF).not_priced == "no_tape"


def test_a_short_tape_reads_only_the_windows_it_covers():
    got = company_landing(series([10.0 + i for i in range(20)]), ASOF)
    by_key = {w.key: w for w in got.windows}
    assert by_key["7d"].ret is not None and by_key["7d"].vol_ratio is not None
    assert by_key["30d"].ret is None and by_key["30d"].range_pos is None
    assert by_key["30d"].vol_ratio is None and by_key["30d"].quadrant is None


def test_the_read_fits_inside_the_display_views_price_bound():
    # the per-company read goes through the display PIT, whose price read is bounded; a lookback past the
    # bound would silently truncate the 90D prior window
    assert display_bounds()["fact_price_eod"] >= LOOKBACK_DAYS
    # 2 x 90 trading bars fit in the lookback even with a holiday a month
    assert LOOKBACK_DAYS * 5 / 7 - 12 >= 180


# ---------------------------------------------------------------------------------------------------------
# groups: medians, thin groups, the split
# ---------------------------------------------------------------------------------------------------------


def test_the_median_range_position_over_the_names_with_one():
    rows = [ms(T1, "Grid", f"N{i}", S[i]) for i in range(4)]
    readings = {
        S[0]: company(**{"7d": dict(range_pos=10.0)}),
        S[1]: company(**{"7d": dict(range_pos=60.0)}),
        S[2]: company(**{"7d": dict(range_pos=90.0)}),
        S[3]: company(),  # priced, no 7D position
    }
    (g,) = landing(inputs(rows, readings), {}, asof=ASOF).groups
    assert win(g).range_median == 60.0 and win(g).range_n == 3
    assert g.names == 4 and g.priced == 4


def test_fewer_than_three_priced_names_shows_no_median():
    assert MIN_PRICED == 3
    rows = [ms(T1, "Grid", f"N{i}", S[i]) for i in range(3)]
    readings = {
        S[0]: company(**{"7d": dict(range_pos=10.0, up_down=1.5)}),
        S[1]: company(**{"7d": dict(range_pos=60.0, up_down=0.5)}),
        S[2]: company(priced=False),
    }
    (g,) = landing(inputs(rows, readings), {}, asof=ASOF).groups
    assert win(g).range_median is None and win(g).range_n == 2
    assert win(g).up_down_median is None and win(g).up_down_n == 2
    assert g.priced == 2


def test_the_median_up_down_day_volume():
    rows = [ms(T1, "Grid", f"N{i}", S[i]) for i in range(3)]
    readings = {S[i]: company(**{"30d": dict(up_down=v)}) for i, v in enumerate((0.8, 1.4, 2.0))}
    (g,) = landing(inputs(rows, readings), {}, asof=ASOF).groups
    assert win(g, "30d").up_down_median == 1.4 and win(g, "30d").up_down_n == 3


def test_the_quadrant_split_counts_every_name():
    quads = ["accumulation", "accumulation", "accumulation", "thin_rally", "rest", None, "flat"]
    rows = [ms(T1, "Grid", f"N{i}", S[i]) for i in range(len(quads))]
    readings = {S[i]: company(**{"30d": dict(quadrant=q)}) for i, q in enumerate(quads)}
    (g,) = landing(inputs(rows, readings), {}, asof=ASOF).groups
    assert asdict(win(g, "30d").split) == {
        "accumulation": 3,
        "thin_rally": 1,
        "distribution": 0,
        "rest": 1,
        "flat": 1,
        "unread": 1,
    }
    assert win(g, "7d").split.unread == len(quads)  # no 7D readings at all


def test_default_order_is_the_7d_median_position_highest_first_dashes_last_then_label():
    rows = [
        ms(T1, "Grid", "A1", S[0], "Alpha"),
        ms(T1, "Grid", "A2", S[1], "Alpha"),
        ms(T1, "Grid", "A3", S[2], "Alpha"),
        ms(T1, "Grid", "B1", S[3], "Beta"),
        ms(T1, "Grid", "B2", S[4], "Beta"),
        ms(T1, "Grid", "B3", S[5], "Beta"),
        ms(T1, "Grid", "C1", S[6], "Charlie"),  # one name: "—"
    ]
    pos = [20.0, 30.0, 40.0, 70.0, 80.0, 90.0, 99.0]
    readings = {S[i]: company(**{"7d": dict(range_pos=p)}) for i, p in enumerate(pos)}
    result = landing(inputs(rows, readings), {}, asof=ASOF)
    assert [g.link for g in result.groups] == ["Beta", "Alpha", "Charlie"]
    # a group's names: the 7D position, highest first
    assert [m.ticker for m in result.groups[0].members] == ["B3", "B2", "B1"]


# ---------------------------------------------------------------------------------------------------------
# who counts, and the groups (the Rotation Map's rules, via dashboards/baskets.py)
# ---------------------------------------------------------------------------------------------------------


def _two_link_company():
    """AAA sits in two links of Grid and on Nuclear; BBB on Grid; CCC on Nuclear."""
    rows = [
        ms(T1, "Grid", "AAA", S[0], "Turbines"),
        ms(T1, "Grid", "AAA", S[0], "Cables"),
        ms(T1, "Grid", "BBB", S[1], "Turbines"),
        ms(T2, "Nuclear", "AAA", S[0], "Fuel"),
        ms(T2, "Nuclear", "CCC", S[2], "Fuel"),
    ]
    readings = {
        S[0]: company(**{"7d": dict(range_pos=90.0, quadrant="accumulation")}),
        S[1]: company(**{"7d": dict(range_pos=10.0, quadrant="rest")}),
        S[2]: company(**{"7d": dict(range_pos=50.0, quadrant="thin_rally")}),
    }
    identity = {
        S[0]: {"business_type": "utilities", "business_supersector": "energy_utilities"},
        S[1]: {"business_type": "utilities", "business_supersector": "energy_utilities"},
        S[2]: {"business_type": "miner", "business_supersector": "materials"},
    }
    return rows, readings, identity


def test_the_link_view_counts_a_two_link_company_in_both_links():
    rows, readings, identity = _two_link_company()
    result = landing(inputs(rows, readings), identity, asof=ASOF, group_by="link")
    turbines, cables = grp(result, "Turbines"), grp(result, "Cables")
    assert turbines.names == 2 and cables.names == 1
    assert win(turbines).split.accumulation == 1 and win(cables).split.accumulation == 1
    # the company counts once per link across the whole view: twice in Grid, once in Nuclear
    assert sum(win(g).split.accumulation for g in result.groups) == 3
    assert result.companies == 3


def test_the_type_sector_and_card_views_count_each_company_once_per_group():
    rows, readings, identity = _two_link_company()
    by_type = landing(inputs(rows, readings), identity, asof=ASOF, group_by="business_type")
    util = grp(by_type, "utilities")
    assert util.names == 2 and win(util).split.accumulation == 1 and win(util).split.rest == 1
    aaa = next(m for m in util.members if m.ticker == "AAA")
    assert [(c.thesis_name, c.links) for c in aaa.cards] == [
        ("Grid", ("Turbines", "Cables")),
        ("Nuclear", ("Fuel",)),
    ]
    by_sector = landing(inputs(rows, readings), identity, asof=ASOF, group_by="sector")
    assert grp(by_sector, "energy_utilities").names == 2
    by_card = landing(inputs(rows, readings), identity, asof=ASOF, group_by="card")
    grid = grp(by_card, str(T1))
    assert grid.names == 2 and win(grid).split.accumulation == 1  # AAA once, not once per link


def test_silent_memberships_are_left_out_by_default_and_their_own_rows_when_shown():
    rows = [
        ms(T1, "Grid", "AAA", S[0], "Turbines"),
        ms(T1, "Grid", "SPX", S[1], CONTEXT_LABEL),
        ms(T1, "Grid", "OLD", S[2], BENCH_LABEL),
    ]
    readings = {sid: company(**{"7d": dict(quadrant="accumulation")}) for sid in S[:3]}
    hidden = landing(inputs(rows, readings), {}, asof=ASOF)
    assert [g.link for g in hidden.groups] == ["Turbines"]
    assert hidden.silent_rows_left_out == 2 and hidden.companies == 1
    shown = landing(inputs(rows, readings), {}, asof=ASOF, include_silent=True)
    assert {g.link for g in shown.groups} == {"Turbines", CONTEXT_LABEL, BENCH_LABEL}
    assert shown.silent_rows_left_out == 0 and shown.companies == 3
    by_card = landing(inputs(rows, readings), {}, asof=ASOF, group_by="card", include_silent=True)
    assert win(by_card.groups[0]).split.accumulation == 3


def test_unresolved_rows_are_counted_never_grouped_and_not_priced_names_are_counted():
    rows = [
        ms(T1, "Grid", "AAA", S[0]),
        ms(T1, "Grid", "BBB", S[1]),
        ms(T1, "Grid", "CCC", S[2]),
        ms(T1, "Grid", "ZZZ", None),
    ]
    stale = CompanyLanding(bars=5, last_bar=date(2026, 5, 1), priced=False, not_priced="stale_tape")
    readings = {S[0]: company(), S[1]: stale}  # CCC has no reading at all: no tape
    result = landing(inputs(rows, readings), {}, asof=ASOF)
    assert result.unresolved_rows == 1 and result.companies == 3
    assert result.priced_companies == 1
    assert result.stale_tape_companies == 1 and result.no_tape_companies == 1
    (g,) = result.groups
    assert g.names == 3 and g.priced == 1
    assert [m.ticker for m in g.members] == ["AAA", "BBB", "CCC"]  # every name listed, priced first


# ---------------------------------------------------------------------------------------------------------
# a flagged price history (dashboards.baskets.price_flags): left out of every aggregate, named on the page
# ---------------------------------------------------------------------------------------------------------

MIXED = "mixed-scale price history"


def _robots():
    """Card 1's Core link: RBOT (a scale break reads as a close at the top of every window on rising volume)
    and three clean names. Card 2's Fuel link: three clean names of its own."""
    rows = [ms(T1, "Robots", "RBOT", S[0])]
    rows += [ms(T1, "Robots", t, S[i]) for i, t in ((1, "AAA"), (2, "BBB"), (3, "CCC"))]
    rows += [ms(T2, "Nuclear", t, S[i], "Fuel") for i, t in ((4, "DDD"), (5, "EEE"), (6, "FFF"))]
    loud = dict(range_pos=100.0, quadrant="accumulation", up_down=9.0, ret=8900.0, vol_ratio=9.0)
    readings = {S[0]: company(**{k: loud for k in WINDOW_KEYS})}
    for i, (pos, q, ud) in enumerate(
        [
            (20.0, "distribution", 0.6),
            (50.0, "rest", 1.0),
            (80.0, "thin_rally", 1.4),
            (30.0, "rest", 0.9),
            (60.0, "accumulation", 1.3),
            (90.0, "accumulation", 2.0),
        ],
        start=1,
    ):
        w = dict(range_pos=pos, quadrant=q, up_down=ud)
        readings[S[i]] = company(**{k: w for k in WINDOW_KEYS})
    return rows, readings


@pytest.mark.parametrize("group_by", ["link", "card"])
def test_a_flagged_company_is_left_out_of_every_aggregate_and_named_on_the_page(group_by):
    """RBOT is flagged: every window's median range position, quadrant split (not even ``unread``) and median
    up/down-day volume reads exactly as without it. It stays listed (no window, its flag, neither priced nor
    not priced), counts in the group's names and ``flagged``, and the page names it with the flag's label.
    """
    rows, readings = _robots()
    plain = landing(inputs(rows, readings), {}, asof=ASOF, group_by=group_by)
    assert win(next(g for g in plain.groups if g.thesis_id == T1)).split.accumulation == 1

    without = landing(inputs(rows[1:], readings), {}, asof=ASOF, group_by=group_by)
    result = landing(
        inputs(rows, readings, flagged={S[0]: MIXED}), {}, asof=ASOF, group_by=group_by
    )
    g = next(g for g in result.groups if g.thesis_id == T1)
    alone = next(x for x in without.groups if x.key == g.key)
    assert g.windows == alone.windows
    assert (win(g).range_median, win(g).range_n, win(g).up_down_median) == (50.0, 3, 1.0)
    assert asdict(win(g).split) == {
        "accumulation": 0,
        "thin_rally": 1,
        "distribution": 1,
        "rest": 1,
        "flat": 0,
        "unread": 0,
    }
    assert (g.names, g.priced, g.flagged) == (4, 3, 1)

    rbot = next(m for m in g.members if m.ticker == "RBOT")
    assert (rbot.flag, rbot.priced, rbot.not_priced, rbot.last_bar) == (MIXED, False, None, None)
    assert all(w.range_pos is None and w.quadrant is None for w in rbot.windows)
    assert g.members[-1] is rbot
    assert all(m.flag is None for m in g.members if m.ticker != "RBOT")

    assert [(f.ticker, f.reason) for f in result.flagged] == [("RBOT", MIXED)]
    assert (result.companies, result.priced_companies) == (7, 6)
    assert (result.no_tape_companies, result.stale_tape_companies) == (0, 0)


def test_an_unflagged_group_is_unaffected_by_a_flag_elsewhere():
    rows, readings = _robots()
    plain = landing(inputs(rows, readings), {}, asof=ASOF)
    flagged = landing(inputs(rows, readings, flagged={S[0]: MIXED}), {}, asof=ASOF)
    assert grp(flagged, f"link:{T2}:Fuel") == grp(plain, f"link:{T2}:Fuel")
    assert grp(flagged, f"link:{T2}:Fuel").flagged == 0
    assert plain.flagged == () and grp(plain, f"link:{T1}:Core").flagged == 0


def test_the_router_reads_the_price_flags_inside_the_cached_read(monkeypatch):
    """``read_inputs`` itself reads the flags (the real one, its DB reads stubbed): once per cached read, for
    every resolved company, on the request's one ``serve_view`` pair; every grouping after it is a hit.
    """
    rows, readings = _robots()
    seen: dict = {"flag_reads": 0}

    def fake_flags(conn, ids, asof, *, known_at, clock):
        seen["flag_reads"] += 1
        seen["flags_view"] = (set(ids), asof, known_at, clock)
        return {S[0]: MIXED}

    CACHE.clear()
    monkeypatch.setattr(
        router_mod, "cards_as_of", lambda conn, asof, known_at: CardsAsOf(2, [], 0, 0)
    )
    monkeypatch.setattr(router_mod, "memberships_of", lambda cards: tuple(rows))
    monkeypatch.setattr(router_mod, "_readings", lambda conn, sids, asof, known_at, clock: readings)
    monkeypatch.setattr(router_mod, "price_flags", fake_flags)
    monkeypatch.setattr(router_mod.master, "identity_for", lambda conn, ids, **kw: {})
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    try:
        client = TestClient(app)
        body = client.get("/dashboards/landing", params={"asof": "2026-06-01"}).json()
        again = client.get(
            "/dashboards/landing", params={"asof": "2026-06-01", "group_by": "card"}
        ).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    ids, asof, known_at, clock = seen["flags_view"]
    assert seen["flag_reads"] == 1 and again["cache"] == "hit"
    assert ids == set(S[:7]) and asof == ASOF and clock == "public" and known_at is not None
    assert body["flagged"] == [{"security_id": str(S[0]), "ticker": "RBOT", "reason": MIXED}]
    core = next(g for g in body["groups"] if g["link"] == "Core")
    assert (core["names"], core["priced"], core["flagged"]) == (4, 3, 1)
    rbot = next(m for m in core["members"] if m["ticker"] == "RBOT")
    assert rbot["flag"] == MIXED and rbot["priced"] is False and rbot["not_priced"] is None
    assert again["flagged"] == body["flagged"]


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}
    rows, readings, identity = _two_link_company()

    def fake_read(conn, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock)
        return inputs(rows, readings)

    def fake_identity(conn, ids, **kw):
        seen["identity_ids"] = set(ids)
        return identity

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_inputs", fake_read)
    monkeypatch.setattr(router_mod.master, "identity_for", fake_identity)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_threads_the_view_and_serves_every_grouping_off_one_cached_read(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/landing", params={"asof": "2026-06-01"}).json()
    assert body["cache"] == "computed" and body["group_by"] == "link"
    assert body["include_silent"] is False and len(body["groups"]) == 3
    assert body["min_priced"] == MIN_PRICED and body["rising_mult"] == RISING_MULT
    assert body["stale_gap_days"] == sma.STALE_GAP_DAYS
    assert body["windows"] == [
        {"key": "1d", "bars": 1},
        {"key": "7d", "bars": 7},
        {"key": "30d", "bars": 30},
        {"key": "90d", "bars": 90},
    ]
    asof, known_at, clock = seen["view"]
    assert asof == date(2026, 6, 1) and clock == "public" and known_at is not None

    for group_by in ("business_type", "sector", "card"):
        again = client.get(
            "/dashboards/landing",
            params={"asof": "2026-06-01", "group_by": group_by, "include_silent": "true"},
        ).json()
        assert again["cache"] == "hit" and again["group_by"] == group_by
    assert seen["reads"] == 1
    assert seen["identity_ids"] == {S[0], S[1], S[2]}

    today = market_today().isoformat()
    client.get("/dashboards/landing", params={"asof": today})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 2


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(
        "/dashboards/landing", params={"asof": "2026-06-01", "group_by": "business_type"}
    ).json()
    util = next(g for g in body["groups"] if g["business_type"] == "utilities")
    assert util["names"] == 2 and util["link"] is None
    assert [w["key"] for w in util["windows"]] == list(WINDOW_KEYS)
    seven = next(w for w in util["windows"] if w["key"] == "7d")
    assert seven["split"] == {
        "accumulation": 1,
        "thin_rally": 0,
        "distribution": 0,
        "rest": 1,
        "flat": 0,
        "unread": 0,
    }
    assert seven["range_median"] is None and seven["range_n"] == 2  # two names: under MIN_PRICED
    aaa = next(m for m in util["members"] if m["ticker"] == "AAA")
    assert next(w for w in aaa["windows"] if w["key"] == "7d")["quadrant"] == "accumulation"
    assert [c["links"] for c in aaa["cards"]] == [["Turbines", "Cables"], ["Fuel"]]


def test_the_router_rejects_an_unknown_grouping(stub_client):
    client, _ = stub_client
    r = client.get("/dashboards/landing", params={"asof": "2026-06-01", "group_by": "mood"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a bar published after a past as-of stays invisible to it (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------


def _price(db, sid, d: date, close: float, volume: float, recorded_at: datetime) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "open": close,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": volume,
            "valid_from": d,
            "recorded_at": recorded_at,
        },
    )


def _security(db, ticker: str) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, "0009876544", "2026-01-01"),
        )
    return sid


def test_a_bar_published_after_a_past_asof_stays_invisible(db):
    """30 daily bars ending three days before today (close 10, 11, ..., 39; volume 100), each public at its own
    session. That last bar is then re-versioned NOW at a lower close on heavy volume: the past view at its date
    still reads the ORIGINAL bar (the correction was not public by the end of that day), a live view reads the
    correction (its tape is three days old, inside the stale gap)."""
    last = market_today() - timedelta(days=3)
    sid = _security(db, "LAND")
    start = last - timedelta(days=29)
    for i in range(30):
        d = start + timedelta(days=i)
        _price(db, sid, d, 10.0 + i, 100.0, datetime.combine(d, time.min, tzinfo=timezone.utc))
    # the last bar (close 39, volume 100), corrected now to close 30 on 500
    _price(db, sid, last, 30.0, 500.0, datetime.now(timezone.utc))
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Landing fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[BasketMember(ticker="LAND", role="the name", security_id=sid, segment="Core")],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = client.get("/dashboards/landing", params={"asof": last.isoformat()}).json()
        live = client.get("/dashboards/landing", params={"asof": market_today().isoformat()}).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    def one_day(body) -> dict:
        (group,) = body["groups"]
        (name,) = group["members"]
        assert name["ticker"] == "LAND" and group["link"] == "Core" and name["priced"] is True
        return next(w for w in name["windows"] if w["key"] == "1d")

    then = one_day(past)
    # the original bar: up from 38 on the same volume, mid-way in its own 38..40 range
    assert then["ret"] == round((39.0 / 38.0 - 1) * 100, 2)
    assert then["vol_ratio"] == 1.0 and then["quadrant"] == "thin_rally"
    assert then["range_pos"] == 50.0
    assert past["cards_counted"] == 1 and past["priced_companies"] == 1
    # the real flag reader rode both views: a clean three-day-old tape carries no price flag
    assert past["flagged"] == [] and live["flagged"] == []
    # no roster snapshot by then, so today's roster stood in (and is counted)
    assert past["roster_fallback_cards"] == 1
    now = one_day(live)
    # the correction: down from 38 on 5x the volume, mid-way in its own 29..31 range
    assert now["ret"] == round((30.0 / 38.0 - 1) * 100, 2)
    assert now["vol_ratio"] == 5.0 and now["quadrant"] == "distribution"
    assert now["range_pos"] == 50.0
