"""Leaders (``docs/dashboards/leaders.md``): each list's threshold edges, the several-lists section and its order,
one row per company with every card listed, the silent toggle, the no-reading counts, the insider clusters by
group, the reading of the display members' own output (hand-built and off the real members' pure computes),
the router (pure, with the heavy read stubbed), and one DB-backed past-as-of read proving a price published
after the as-of stays invisible to it."""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import leaders as router_mod
from dashboards.baskets import CardsAsOf, Membership
from dashboards.cache import CACHE
from dashboards.leaders import (
    LISTS,
    NO_READING,
    RVOL_LOUD,
    CompanyReading,
    LeadersInputs,
    company_reading,
    has_reading,
    is_cluster,
    leaders,
    lists_of,
    on_list,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo
from signals.display import insider_flow, range52w, relative_strength
from signals.display.base import (
    DisplayBasis,
    DisplayEvent,
    DisplayHeadline,
    DisplayMetric,
    DisplaySignal,
)

ASOF = date(2026, 6, 1)

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def reading(**kw) -> CompanyReading:
    """A company's readings: every member read, on no list, unless ``kw`` says otherwise."""
    base = dict(
        pct_off_high=-20.0,
        rs_leading=False,
        coiling=False,
        buys_30d=0,
        buyers_30d=0,
        rvol20=1.0,
        ret_7d=1.0,
        ret_30d=2.0,
        above_50d=True,
    )
    base.update(kw)
    return CompanyReading(**base)


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


def inputs(memberships, readings, **kw) -> LeadersInputs:
    cards = len({m.thesis_id for m in memberships})
    base = dict(
        live_cards=cards, cards_counted=cards, cards_not_yet_created=0, roster_fallback_cards=0
    )
    base.update(kw)
    return LeadersInputs(memberships=tuple(memberships), readings=readings, **base)


def lst(result, key: str):
    return next(x for x in result.lists if x.key == key)


def tickers(result, ids) -> list[str]:
    by_id = {n.security_id: n.ticker for n in result.names}
    return [by_id[i] for i in ids]


T1, T2, T3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
S = [uuid.uuid4() for _ in range(12)]


# ---------------------------------------------------------------------------------------------------------
# each list's threshold edges
# ---------------------------------------------------------------------------------------------------------


def test_near_52w_high_is_within_two_percent_inclusive():
    assert on_list(reading(pct_off_high=-2.0), "near_high")
    assert on_list(reading(pct_off_high=0.0), "near_high")
    assert not on_list(reading(pct_off_high=-2.01), "near_high")
    assert not on_list(reading(pct_off_high=None), "near_high")
    assert not has_reading(reading(pct_off_high=None), "near_high")


def test_volume_is_rvol20_at_the_members_own_loud_threshold():
    assert RVOL_LOUD == 1.5
    assert on_list(reading(rvol20=1.5), "volume")
    assert not on_list(reading(rvol20=1.49), "volume")
    assert not on_list(reading(rvol20=None), "volume")
    assert not has_reading(reading(rvol20=None), "volume")


def test_insider_is_one_buy_in_thirty_days_and_a_cluster_is_two_buyers():
    assert on_list(reading(buys_30d=1, buyers_30d=1), "insider")
    assert not on_list(reading(buys_30d=0, buyers_30d=0), "insider")
    assert not on_list(reading(buys_30d=None, buyers_30d=None), "insider")
    assert has_reading(reading(buys_30d=0), "insider")  # an ingested zero is a reading
    assert not has_reading(reading(buys_30d=None), "insider")
    assert not is_cluster(reading(buys_30d=5, buyers_30d=1))
    assert is_cluster(reading(buys_30d=2, buyers_30d=2))


def test_rs_and_coiling_read_the_headlines_and_a_missing_one_is_no_reading():
    assert on_list(reading(rs_leading=True), "rs_leader")
    assert not on_list(reading(rs_leading=False), "rs_leader")
    assert not has_reading(reading(rs_leading=None), "rs_leader")
    assert on_list(reading(coiling=True), "coiling")
    assert not on_list(reading(coiling=False), "coiling")
    assert not has_reading(reading(coiling=None), "coiling")
    assert lists_of(NO_READING) == ()
    assert not any(has_reading(NO_READING, k) for k in LISTS)


def test_the_insider_cluster_accent_rides_the_name():
    rows = [ms(T1, "Grid", "ONE", S[0]), ms(T1, "Grid", "TWO", S[1])]
    result = leaders(
        inputs(
            rows,
            {
                S[0]: reading(buys_30d=3, buyers_30d=1),
                S[1]: reading(buys_30d=2, buyers_30d=2),
            },
        ),
        {},
        asof=ASOF,
    )
    by = {n.ticker: n for n in result.names}
    assert (by["ONE"].cluster, by["TWO"].cluster) == (False, True)
    assert (by["TWO"].buys_30d, by["TWO"].buyers_30d) == (2, 2)


# ---------------------------------------------------------------------------------------------------------
# the several-lists section, each list's default order
# ---------------------------------------------------------------------------------------------------------


def test_several_lists_is_two_or_more_by_lists_then_30d_return():
    rows = [ms(T1, "Grid", t, S[i]) for i, t in enumerate(["AAA", "BBB", "CCC", "DDD", "EEE"])]
    readings = {
        # three lists, a small 30D return
        S[0]: reading(pct_off_high=-1.0, rs_leading=True, rvol20=2.0, ret_30d=1.0),
        # two lists, a big 30D return
        S[1]: reading(pct_off_high=0.0, coiling=True, ret_30d=10.0),
        # two lists, no 30D return: after BBB
        S[2]: reading(buys_30d=1, buyers_30d=1, rvol20=1.6, ret_30d=None),
        # one list: listed, but not in the several-lists section
        S[3]: reading(rs_leading=True, ret_30d=50.0),
        # no list
        S[4]: reading(),
    }
    result = leaders(inputs(rows, readings), {}, asof=ASOF)
    assert tickers(result, result.several) == ["AAA", "BBB", "CCC"]
    assert [n.ticker for n in result.names] == ["AAA", "BBB", "CCC", "DDD"]
    aaa = result.names[0]
    assert aaa.lists == ("near_high", "rs_leader", "volume")
    assert tickers(result, lst(result, "rs_leader").security_ids) == ["DDD", "AAA"]  # by 30D
    assert lst(result, "rs_leader").member == "relative_strength"


def test_each_lists_default_order():
    rows = [ms(T1, "Grid", t, S[i]) for i, t in enumerate(["AAA", "BBB", "CCC", "DDD"])]
    readings = {
        S[0]: reading(
            pct_off_high=-1.5, coiling=True, contraction=0.4, rvol20=1.5, buys_30d=1, buyers_30d=1
        ),
        S[1]: reading(
            pct_off_high=0.0, coiling=True, contraction=0.2, rvol20=3.0, buys_30d=1, buyers_30d=2
        ),
        S[2]: reading(
            pct_off_high=-0.5, coiling=True, contraction=None, rvol20=2.0, buys_30d=4, buyers_30d=1
        ),
        S[3]: reading(pct_off_high=-1.5, coiling=True, contraction=0.4, rvol20=None),
    }
    result = leaders(inputs(rows, readings), {}, asof=ASOF)
    assert tickers(result, lst(result, "near_high").security_ids) == ["BBB", "CCC", "AAA", "DDD"]
    assert tickers(result, lst(result, "coiling").security_ids) == ["BBB", "AAA", "DDD", "CCC"]
    assert tickers(result, lst(result, "volume").security_ids) == ["BBB", "CCC", "AAA"]
    assert tickers(result, lst(result, "insider").security_ids) == ["CCC", "BBB", "AAA"]


# ---------------------------------------------------------------------------------------------------------
# one row per company, the silent toggle, the no-reading counts
# ---------------------------------------------------------------------------------------------------------


def test_one_row_per_company_with_every_card_and_link():
    rows = [
        ms(T1, "Grid", "AAA", S[0], "Turbines"),
        ms(T1, "Grid", "AAA", S[0], "Cables"),
        ms(T2, "Nuclear", "AAA.X", S[0], "Fuel"),
        ms(T2, "Nuclear", "BBB", S[1], "Fuel"),
    ]
    readings = {S[0]: reading(pct_off_high=0.0), S[1]: reading()}
    result = leaders(inputs(rows, readings), {S[0]: {"name": "Alpha Corp"}}, asof=ASOF)
    (name,) = result.names
    assert name.security_id == S[0] and name.name == "Alpha Corp"
    assert [(c.thesis_name, c.ticker, c.links) for c in name.cards] == [
        ("Grid", "AAA", ("Turbines", "Cables")),
        ("Nuclear", "AAA.X", ("Fuel",)),
    ]
    assert result.companies == 2 and result.cards_counted == 2


def test_context_and_bench_are_left_out_unless_included():
    rows = [
        ms(T1, "Grid", "VOTE", S[0], "Core"),
        ms(T2, "Nuclear", "VOTE", S[0], BENCH_LABEL),  # the same company, silent on another card
        ms(T1, "Grid", "CTX", S[1], CONTEXT_LABEL),  # only ever silent
    ]
    readings = {S[0]: reading(pct_off_high=0.0), S[1]: reading(pct_off_high=0.0)}

    out = leaders(inputs(rows, readings), {}, asof=ASOF)
    assert [n.ticker for n in out.names] == ["VOTE"]
    assert [c.thesis_name for c in out.names[0].cards] == ["Grid"]
    assert (out.companies, out.silent_rows_left_out, out.include_silent) == (1, 2, False)

    shown = leaders(inputs(rows, readings), {}, asof=ASOF, include_silent=True)
    assert sorted(n.ticker for n in shown.names) == ["CTX", "VOTE"]
    vote = next(n for n in shown.names if n.ticker == "VOTE")
    assert [(c.thesis_name, c.links) for c in vote.cards] == [
        ("Grid", ("Core",)),
        ("Nuclear", (BENCH_LABEL,)),
    ]
    assert (shown.companies, shown.silent_rows_left_out) == (2, 0)


def test_no_reading_is_counted_per_member_over_the_companies_in_view():
    rows = [
        ms(T1, "Grid", "AAA", S[0]),
        ms(T1, "Grid", "BBB", S[1]),
        ms(T1, "Grid", "CCC", S[2]),
        ms(T1, "Grid", "CTX", S[3], CONTEXT_LABEL),
        ms(T1, "Grid", "NONE", None),  # unresolved: counted on its own line, never listed
    ]
    readings = {
        S[0]: reading(),
        S[1]: reading(rs_leading=None, buys_30d=None, buyers_30d=None, rvol20=None),
        S[3]: NO_READING,
        # S[2] has no reading at all (no tape, nothing ingested)
    }
    out = leaders(inputs(rows, readings), {}, asof=ASOF)
    counts = {x.key: x.no_reading for x in out.lists}
    assert counts == {"near_high": 1, "rs_leader": 2, "coiling": 1, "insider": 2, "volume": 2}
    assert out.unresolved_rows == 1 and out.companies == 3 and out.names == ()

    shown = leaders(inputs(rows, readings), {}, asof=ASOF, include_silent=True)
    assert {x.key: x.no_reading for x in shown.lists}["near_high"] == 2


# ---------------------------------------------------------------------------------------------------------
# insider clusters by group
# ---------------------------------------------------------------------------------------------------------


def _insider_cards():
    rows = [
        # Grid · Turbines: five names, two bought (one a cluster), one with no insider data
        ms(T1, "Grid", "AAA", S[0], "Turbines"),
        ms(T1, "Grid", "BBB", S[1], "Turbines"),
        ms(T1, "Grid", "CCC", S[2], "Turbines"),
        ms(T1, "Grid", "DDD", S[3], "Turbines"),
        ms(T1, "Grid", "EEE", S[4], "Turbines"),
        # Grid · Cables: one name bought
        ms(T1, "Grid", "FFF", S[5], "Cables"),
        ms(T1, "Grid", "GGG", S[6], "Cables"),
        # Nuclear · Fuel: AAA again (bought) and one other buyer
        ms(T2, "Nuclear", "AAA", S[0], "Fuel"),
        ms(T2, "Nuclear", "HHH", S[7], "Fuel"),
    ]
    readings = {
        S[0]: reading(buys_30d=1, buyers_30d=1),
        S[1]: reading(buys_30d=4, buyers_30d=3),
        S[2]: reading(),
        S[3]: reading(buys_30d=None, buyers_30d=None),
        S[4]: reading(),
        S[5]: reading(buys_30d=2, buyers_30d=2),
        S[6]: reading(),
        S[7]: reading(buys_30d=1, buyers_30d=1),
    }
    identity = {
        S[0]: {"business_type": "utilities", "business_supersector": "energy_utilities"},
        S[1]: {"business_type": "utilities", "business_supersector": "energy_utilities"},
        S[5]: {"business_type": "utilities", "business_supersector": "energy_utilities"},
        S[7]: {"business_type": "miner", "business_supersector": "materials"},
    }
    return rows, readings, identity


def test_a_group_shows_when_two_names_bought_with_its_share():
    rows, readings, identity = _insider_cards()
    out = leaders(inputs(rows, readings), identity, asof=ASOF, group_by="link")
    # Turbines (2 of 5) and Fuel (2 of 2); Cables has one buyer: left out
    assert {g.key for g in out.insider_groups} == {f"link:{T1}:Turbines", f"link:{T2}:Fuel"}
    turbines = next(g for g in out.insider_groups if g.link == "Turbines")
    assert (turbines.with_buys, turbines.names, turbines.share) == (2, 5, 0.4)
    assert (turbines.no_insider_data, turbines.buys, turbines.thesis_name) == (1, 5, "Grid")
    assert [(m.ticker, m.buys_30d, m.buyers_30d, m.cluster) for m in turbines.members] == [
        ("BBB", 4, 3, True),
        ("AAA", 1, 1, False),
    ]
    fuel = next(g for g in out.insider_groups if g.link == "Fuel")
    assert (fuel.with_buys, fuel.names, fuel.share) == (2, 2, 1.0)
    # by names with buys (a tie), then the share: Fuel's 100% before Turbines' 40%
    assert [g.link for g in out.insider_groups] == ["Fuel", "Turbines"]


def test_the_card_and_type_views_count_each_company_once():
    rows, readings, identity = _insider_cards()
    by_card = leaders(inputs(rows, readings), identity, asof=ASOF, group_by="card")
    grid = next(g for g in by_card.insider_groups if g.thesis_name == "Grid")
    assert (grid.with_buys, grid.names, grid.link) == (3, 7, None)
    assert [m.ticker for m in grid.members] == ["BBB", "FFF", "AAA"]

    by_type = leaders(inputs(rows, readings), identity, asof=ASOF, group_by="business_type")
    (util,) = [g for g in by_type.insider_groups if g.business_type == "utilities"]
    assert (util.with_buys, util.names) == (3, 3)  # AAA counted once across its two cards
    # the miner group has one buyer, and the unclassified group none: neither shows
    assert len(by_type.insider_groups) == 1


# ---------------------------------------------------------------------------------------------------------
# reading the members' own output
# ---------------------------------------------------------------------------------------------------------


def _sig(kind, metrics=(), events=(), headline=None, bars_used=None) -> DisplaySignal:
    return DisplaySignal(
        kind=kind,
        label=kind,
        metrics=list(metrics),
        events=list(events),
        headline=headline,
        basis=DisplayBasis(source="fact_price_eod", bars_used=bars_used),
    )


def test_company_reading_reads_the_cockpit_keys():
    r = company_reading(
        {
            "range_52w": _sig(
                "range_52w",
                [
                    DisplayMetric(key="pct_off_52w_high", label="x", value=-1.25, unit="pct"),
                    DisplayMetric(
                        key="high_52w", label="x", value=40.5, unit="price", note="on 2026-05-28"
                    ),
                ],
                bars_used=200,
            ),
            "relative_strength": _sig(
                "relative_strength",
                events=[DisplayEvent(key="rs_high_iwm", label="x", date=ASOF)],
                headline=DisplayHeadline(key="leading", label="x"),
                bars_used=relative_strength.MIN_BARS,
            ),
            "vcp": _sig(
                "vcp",
                [DisplayMetric(key="contraction", label="x", value=0.42, unit="ratio")],
                headline=DisplayHeadline(key="coiling", label="x"),
            ),
            "insider_flow_90d": _sig(
                "insider_flow_90d",
                [
                    DisplayMetric(key="buy_count_30d", label="x", value=3.0, unit="count"),
                    DisplayMetric(key="distinct_buyers_30d", label="x", value=2.0, unit="count"),
                ],
            ),
            "rvol": _sig("rvol", [DisplayMetric(key="rvol20", label="x", value=1.8)]),
            "trailing_returns": _sig(
                "trailing_returns",
                [
                    DisplayMetric(key="ret_7d", label="7d", value=-1.0, unit="pct"),
                    DisplayMetric(key="ret_30d", label="30d", unit="pct", note="n/a: 20/31 bars"),
                ],
            ),
            "sma_position": _sig(
                "sma_position", [DisplayMetric(key="pct_vs_fast", label="x", value=-0.5)]
            ),
        }
    )
    assert (r.pct_off_high, r.high_52w, r.high_date, r.range_bars) == (
        -1.25,
        40.5,
        date(2026, 5, 28),
        200,
    )
    assert (r.rs_leading, r.rs_vs) == (True, ("IWM",))
    assert (r.coiling, r.contraction) == (True, 0.42)
    assert (r.buys_30d, r.buyers_30d, r.rvol20) == (3, 2, 1.8)
    assert (r.ret_7d, r.ret_30d, r.above_50d) == (-1.0, None, False)
    assert lists_of(r) == ("near_high", "rs_leader", "coiling", "insider", "volume")
    assert company_reading({}) == NO_READING


def test_a_thin_rs_is_no_reading_and_a_quiet_insider_tape_is_a_zero():
    r = company_reading(
        {
            "relative_strength": _sig(
                "relative_strength",
                headline=DisplayHeadline(key="inline", label="x"),
                bars_used=relative_strength.MIN_BARS - 1,
            ),
            "vcp": _sig("vcp"),  # read, no coil
            "insider_flow_90d": _sig("insider_flow_90d", []),  # ingested, nothing in the window
        }
    )
    assert (r.rs_leading, r.rs_vs) == (None, ())
    assert r.coiling is False and r.contraction is None
    assert (r.buys_30d, r.buyers_30d) == (0, 0)
    inline = company_reading(
        {
            "relative_strength": _sig(
                "relative_strength",
                headline=DisplayHeadline(key="inline", label="x"),
                bars_used=relative_strength.MIN_BARS,
            )
        }
    )
    assert inline.rs_leading is False


def _bars(n: int, end: date, close=lambda i: 10.0 + i * 0.1):
    return [
        {"d": end - timedelta(days=n - 1 - i), "close": close(i), "volume": 1000.0}
        for i in range(n)
    ]


def test_the_real_members_keys_and_notes_parse():
    """Off the members' own pure computes, so a renamed key or a reworded note fails here."""
    bars = _bars(
        120, ASOF, close=lambda i: 50.0 - abs(i - 100) * 0.1
    )  # the high prints 20 bars back
    rng = range52w.compute(bars, ASOF)
    bench = _bars(120, ASOF, close=lambda i: 100.0)
    rs = relative_strength.compute(_bars(120, ASOF), {"SPY": bench, "IWM": bench}, ASOF)
    ins = insider_flow.compute(
        [
            {"valid_from": ASOF - timedelta(days=3), "txn_code": "P", "insider_name": "A"},
            {"valid_from": ASOF - timedelta(days=5), "txn_code": "P", "insider_name": "B"},
            {"valid_from": ASOF - timedelta(days=60), "txn_code": "P", "insider_name": "C"},
        ],
        ASOF,
    )
    r = company_reading({"range_52w": rng, "relative_strength": rs, "insider_flow_90d": ins})
    assert r.high_date == ASOF - timedelta(days=19) and r.range_bars == 120
    assert r.pct_off_high == round((bars[-1]["close"] / 50.0 - 1) * 100, 2)
    assert (r.rs_leading, r.rs_vs) == (True, ("SPY", "IWM"))
    assert (r.buys_30d, r.buyers_30d) == (2, 2) and is_cluster(r)


# ---------------------------------------------------------------------------------------------------------
# a flagged price history (dashboards.baskets.price_flags): on no list, in no group, named on the page
# ---------------------------------------------------------------------------------------------------------

MIXED = "mixed-scale price history"


def _robots():
    """Card 1's Turbines link: RBOT (a scale break reads as a 52-week high, RS at a high, loud volume, plus an
    insider cluster; no VCP reading), AAA (near its high, loud volume, one buy), BBB (a 2-buyer cluster) and
    CCC (on no list). Card 2's Fuel link: DDD and EEE, each with a buy."""
    rows = [
        ms(T1, "Grid", "RBOT", S[0], "Turbines"),
        ms(T1, "Grid", "AAA", S[1], "Turbines"),
        ms(T1, "Grid", "BBB", S[2], "Turbines"),
        ms(T1, "Grid", "CCC", S[3], "Turbines"),
        ms(T2, "Nuclear", "DDD", S[4], "Fuel"),
        ms(T2, "Nuclear", "EEE", S[5], "Fuel"),
    ]
    readings = {
        S[0]: reading(
            pct_off_high=0.0,
            rs_leading=True,
            coiling=None,
            buys_30d=3,
            buyers_30d=2,
            rvol20=9.0,
            ret_30d=8000.0,
        ),
        S[1]: reading(pct_off_high=-1.0, rvol20=2.0, buys_30d=1, buyers_30d=1),
        S[2]: reading(buys_30d=3, buyers_30d=2),
        S[3]: reading(),
        S[4]: reading(buys_30d=1, buyers_30d=1),
        S[5]: reading(buys_30d=2, buyers_30d=1),
    }
    return rows, readings


def _lists(result):
    return [(x.key, tickers(result, x.security_ids), x.no_reading) for x in result.lists]


def test_a_flagged_company_is_on_no_list_and_in_no_group_and_named_on_the_page():
    """RBOT is flagged: it is on no list (the insider one included), never in a no-reading count, out of every
    insider group's names, buyers and share (counted in ``flagged``); every list and the several-lists
    section read exactly as without it, and the page names it with the flag's label."""
    rows, readings = _robots()
    plain = leaders(inputs(rows, readings), {}, asof=ASOF)
    assert "RBOT" in [n.ticker for n in plain.names] and lst(plain, "coiling").no_reading == 1

    without = leaders(inputs(rows[1:], readings), {}, asof=ASOF)
    result = leaders(inputs(rows, readings, flagged={S[0]: MIXED}), {}, asof=ASOF)
    assert [n.ticker for n in result.names] == [n.ticker for n in without.names]
    assert "RBOT" not in [n.ticker for n in result.names]
    assert _lists(result) == _lists(without)
    assert lst(result, "coiling").no_reading == 0  # RBOT's missing VCP reading is not counted
    assert tickers(result, result.several) == tickers(without, without.several) == ["AAA"]

    turbines = next(g for g in result.insider_groups if g.link == "Turbines")
    alone = next(g for g in without.insider_groups if g.link == "Turbines")
    assert replace(turbines, flagged=0) == alone
    assert (turbines.with_buys, turbines.names, turbines.flagged) == (2, 3, 1)
    assert [m.ticker for m in turbines.members] == ["BBB", "AAA"]

    assert [(f.ticker, f.reason) for f in result.flagged] == [("RBOT", MIXED)]
    assert result.companies == plain.companies == 6


def test_an_unflagged_group_is_unaffected_by_a_flag_elsewhere():
    rows, readings = _robots()
    plain = leaders(inputs(rows, readings), {}, asof=ASOF)
    flagged = leaders(inputs(rows, readings, flagged={S[0]: MIXED}), {}, asof=ASOF)
    fuel = [g for g in flagged.insider_groups if g.link == "Fuel"]
    assert fuel == [g for g in plain.insider_groups if g.link == "Fuel"] and fuel[0].flagged == 0
    assert plain.flagged == ()
    assert all(g.flagged == 0 for g in plain.insider_groups)


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
        body = client.get("/dashboards/leaders", params={"asof": "2026-06-01"}).json()
        again = client.get(
            "/dashboards/leaders", params={"asof": "2026-06-01", "group_by": "card"}
        ).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    ids, asof, known_at, clock = seen["flags_view"]
    assert seen["flag_reads"] == 1 and again["cache"] == "hit"
    assert ids == set(S[:6]) and asof == ASOF and clock == "public" and known_at is not None
    assert body["flagged"] == [{"security_id": str(S[0]), "ticker": "RBOT", "reason": MIXED}]
    assert str(S[0]) not in {n["security_id"] for n in body["names"]}
    turbines = next(g for g in body["insider_groups"] if g["link"] == "Turbines")
    assert (turbines["names"], turbines["flagged"]) == (3, 1)
    grid = next(g for g in again["insider_groups"] if g["thesis_name"] == "Grid")
    assert grid["flagged"] == 1


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}
    rows, readings, identity = _insider_cards()
    readings = {**readings, S[1]: replace(readings[S[1]], pct_off_high=0.0, rvol20=2.0)}
    identity = {**identity, S[1]: {**identity[S[1]], "name": "Bravo Inc"}}

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
    body = client.get("/dashboards/leaders", params={"asof": "2026-06-01"}).json()
    assert body["cache"] == "computed" and body["group_by"] == "link"
    assert body["include_silent"] is False
    assert (body["near_high_pct"], body["rvol_loud"], body["cluster_min_buyers"]) == (-2.0, 1.5, 2)
    assert (body["group_min_names"], body["multi_min_lists"], body["insider_min_buys"]) == (2, 2, 1)
    asof, known_at, clock = seen["view"]
    assert asof == date(2026, 6, 1) and clock == "public" and known_at is not None

    for group_by in ("business_type", "sector", "card"):
        again = client.get(
            "/dashboards/leaders",
            params={"asof": "2026-06-01", "group_by": group_by, "include_silent": "true"},
        ).json()
        assert again["cache"] == "hit" and again["group_by"] == group_by
    assert seen["reads"] == 1
    assert seen["identity_ids"] == {S[i] for i in range(8)}

    today = market_today().isoformat()
    client.get("/dashboards/leaders", params={"asof": today})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 2


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/leaders", params={"asof": "2026-06-01"}).json()
    bbb = next(n for n in body["names"] if n["ticker"] == "BBB")
    assert bbb["name"] == "Bravo Inc" and bbb["lists"] == ["near_high", "insider", "volume"]
    assert (bbb["buys_30d"], bbb["buyers_30d"], bbb["cluster"]) == (4, 3, True)
    assert bbb["cards"] == [
        {"thesis_id": str(T1), "thesis_name": "Grid", "ticker": "BBB", "links": ["Turbines"]}
    ]
    assert body["several"][0] == bbb["security_id"]
    assert [x["key"] for x in body["lists"]] == list(LISTS)
    ins = next(x for x in body["lists"] if x["key"] == "insider")
    assert ins["member"] == "insider_flow_90d" and ins["no_reading"] == 1
    turbines = next(g for g in body["insider_groups"] if g["link"] == "Turbines")
    assert (turbines["with_buys"], turbines["names"], turbines["share"]) == (2, 5, 0.4)


def test_the_router_rejects_an_unknown_grouping(stub_client):
    client, _ = stub_client
    r = client.get("/dashboards/leaders", params={"asof": "2026-06-01", "group_by": "mood"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a price published after a past as-of stays invisible to it (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

_THAW = datetime(2026, 9, 1, 18, 13, 31, tzinfo=timezone.utc)


def _price(db, sid, d: date, close: float, recorded_at: datetime) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "close": close,
            "volume": 1000.0,
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
            (sid, DEFAULT_TENANT_ID, ticker, "0009876543", "2026-01-01"),
        )
    return sid


def test_a_price_published_after_a_past_asof_stays_invisible(db, monkeypatch):
    """260 daily bars rising into the as-of (its own bar the 52-week high), each public at its own session.
    The as-of's bar is then re-versioned a week after the thaw at half the close: the past view still reads
    the ORIGINAL close (the correction was not public by the end of that day), so the name sits at its high;
    a live view reads the correction, far off the high, and the name is on no list. The data flags ride the
    same views through the real reader: none at the past as-of; on today's view the tape (it ends 2026-06-01)
    is a stopped feed, so the name is on no list for that reason too, and named."""
    sid = _security(db, "LEAD")
    start = ASOF - timedelta(days=259)
    for i in range(260):
        d = start + timedelta(days=i)
        _price(db, sid, d, 10.0 + i * 0.1, datetime.combine(d, time.min, tzinfo=timezone.utc))
    _price(db, sid, ASOF, 18.95, _THAW + timedelta(days=7))  # the as-of bar (35.9), corrected later
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Leaders fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[BasketMember(ticker="LEAD", role="the name", security_id=sid, segment="Core")],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = client.get("/dashboards/leaders", params={"asof": ASOF.isoformat()}).json()
        live = client.get("/dashboards/leaders", params={"asof": market_today().isoformat()}).json()
        # the clock alone: the same live read with the flag read stubbed out
        CACHE.clear()
        monkeypatch.setattr(router_mod, "price_flags", lambda *a, **k: {})
        unflagged = client.get(
            "/dashboards/leaders", params={"asof": market_today().isoformat()}
        ).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    (name,) = past["names"]
    assert name["ticker"] == "LEAD" and "near_high" in name["lists"]
    assert (
        name["pct_off_high"] == 0.0 and name["high_date"] == ASOF.isoformat()
    )  # not the correction
    assert name["ret_7d"] == round((35.9 / 35.2 - 1) * 100, 2)
    assert [c["links"] for c in name["cards"]] == [["Core"]]
    # the card existed on the as-of; no roster snapshot then, so today's roster stood in (and is counted)
    assert past["cards_counted"] == 1 and past["roster_fallback_cards"] == 1
    # no SPY / IWM tape and no Form 4 in this fixture: counted as no reading, never dropped
    no_reading = {x["key"]: x["no_reading"] for x in past["lists"]}
    assert no_reading["rs_leader"] == 1 and no_reading["insider"] == 1
    assert no_reading["near_high"] == 0

    assert past["flagged"] == []

    assert live["names"] == [] and live["companies"] == 1
    assert live["flagged"] == [
        {"security_id": str(sid), "ticker": "LEAD", "reason": "price feed stopped"}
    ]
    assert all(x["no_reading"] == 0 for x in live["lists"])  # left out, never "no reading"
    assert unflagged["names"] == [] and unflagged["flagged"] == []
    assert next(x for x in unflagged["lists"] if x["key"] == "near_high")["security_ids"] == []
