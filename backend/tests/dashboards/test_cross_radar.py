"""Cross Radar (``docs/dashboards/cross-radar.md``): the cross walk and the per-company reading off real bars, the
pure rules (window edges, the cluster rule, first movers, the near-cross band) on hand-built readings, the four
views' counting, the router (pure, with the heavy read stubbed), and one DB-backed past-as-of read proving a
price published after the as-of stays invisible to it."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import cross_radar as router_mod
from dashboards.baskets import CardsAsOf, Membership
from dashboards.cache import CACHE
from dashboards.cross_radar import (
    CLUSTER_MIN_SHARE_PCT,
    HISTORY_SESSIONS,
    NEAR_BAND_PCT,
    RECENT_SESSIONS,
    CompanyCrosses,
    CrossEvent,
    RadarInputs,
    _gap_pct,
    clusters,
    company_crosses,
    cross_history,
    cross_radar,
    near_cross,
    recent_cross,
    timeline_sessions,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo
from signals.display import sma
from signals.display.base import DisplayBasis, DisplayHeadline, DisplaySignal

ASOF = date(2026, 6, 1)

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def _bars(closes: list[float | None], end: date = ASOF) -> list[dict]:
    """Ascending consecutive-day bars ending at ``end`` (sma's own test builder: never stale vs ASOF)."""
    start = end - timedelta(days=len(closes) - 1)
    return [{"d": start + timedelta(days=i), "close": c} for i, c in enumerate(closes)]


def reading(
    cross: str | None = None,
    ago: int = 0,
    *,
    crosses: tuple[CrossEvent, ...] | None = None,
    gap: float | None = -10.0,
    then: float | None = -10.0,
    rs: bool | None = None,
    posture: str = "below_falling",
    price: tuple[CrossEvent, ...] = (),
) -> CompanyCrosses:
    """A priced company's reading; ``cross`` + ``ago`` = one 50d x 200d cross ``ago`` sessions back."""
    if crosses is None:
        crosses = () if cross is None else (CrossEvent(ASOF - timedelta(days=ago), cross, ago),)
    return CompanyCrosses(
        bars=400,
        last_bar=ASOF,
        priced=True,
        crosses=crosses,
        price_200d=price,
        readable_sessions=HISTORY_SESSIONS,
        gap_pct=gap,
        gap_pct_then=then,
        posture=posture,
        rs_leading=rs,
    )


SHORT = CompanyCrosses(bars=120, last_bar=ASOF, priced=False, not_priced="short_tape")
STALE = CompanyCrosses(
    bars=300, last_bar=ASOF - timedelta(days=30), priced=False, not_priced="stale_tape"
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


def inputs(memberships, readings, sessions=(), **kw) -> RadarInputs:
    base = dict(
        live_cards=len({m.thesis_id for m in memberships}),
        cards_counted=len({m.thesis_id for m in memberships}),
        cards_not_yet_created=0,
        roster_fallback_cards=0,
    )
    base.update(kw)
    return RadarInputs(
        memberships=tuple(memberships), readings=readings, sessions=tuple(sessions), **base
    )


def one_group(readings_list, **kw):
    """One link group ("Core" of card "Grid") over the readings, tickers N0, N1, ..."""
    sids = [uuid.uuid4() for _ in readings_list]
    rows = [ms(T1, "Grid", f"N{i}", sid) for i, sid in enumerate(sids)]
    return cross_radar(inputs(rows, dict(zip(sids, readings_list))), {}, asof=ASOF, **kw)


def grp(rows, key_part: str):
    return next(r for r in rows if key_part in r.group.key)


T1, T2 = uuid.uuid4(), uuid.uuid4()
S = [uuid.uuid4() for _ in range(12)]

# ---------------------------------------------------------------------------------------------------------
# the walk: every cross in 90 sessions, sma.py's own tie rule
# ---------------------------------------------------------------------------------------------------------

# 220 rising bars (the 50d over the 200d), a 30-bar drop, a 30-bar rally, a 30-bar drop: death -> golden ->
# death, all inside the last 90 sessions
WHIPSAW = [100.0 + i * 0.1 for i in range(220)] + [40.0] * 30 + [200.0] * 30 + [20.0] * 30


def test_every_cross_of_the_last_90_sessions_is_found():
    bars = _bars(WHIPSAW)
    r = company_crosses(bars, ASOF, None, None)
    assert r.priced and r.readable_sessions == HISTORY_SESSIONS
    assert [(e.direction, e.sessions_ago) for e in r.crosses] == [
        ("death", 83),
        ("golden", 37),
        ("death", 3),
    ]
    # each stamped on the bar it printed (sessions_ago counts back from the last bar)
    assert [e.d for e in r.crosses] == [bars[-1 - e.sessions_ago]["d"] for e in r.crosses]
    # the Cockpit's member keeps only the most recent one: the same date and direction
    member = sma.compute(bars, ASOF)
    (last,) = [e for e in member.events if e.key in ("golden_cross", "death_cross")]
    assert (last.key, last.date) == ("death_cross", r.crosses[-1].d)


def test_exact_zeros_are_skipped_and_the_stamp_is_the_first_bar_past_the_line():
    days = [ASOF + timedelta(days=i) for i in range(5)]
    # touch-and-return: above -> ON the line -> above again = no cross
    assert cross_history(days[:4], [1.0, 0.0, 1.0, 2.0]) == []
    # a cross THROUGH the line stamps the first bar on the far side (sessions_ago 1 of 4 bars)
    assert cross_history(days[:4], [1.0, 0.0, -1.0, -2.0]) == [(days[2], 1, -1)]
    # leading zeros / Nones only seed state, never flip
    assert cross_history(days[:4], [None, 0.0, -1.0, -2.0]) == []
    # every flip, oldest first; the last one is sma._last_flip's
    diffs = [1.0, -1.0, -1.0, 1.0, 1.0]
    assert cross_history(days, diffs) == [(days[1], 3, -1), (days[3], 1, 1)]
    assert sma._last_flip(days, diffs) == (days[3], "up")


def test_the_history_window_keeps_bar_90_and_drops_bar_91():
    n = 120
    days = [ASOF - timedelta(days=n - 1 - i) for i in range(n)]
    in_at = n - HISTORY_SESSIONS  # sessions_ago 89: the 90th bar ending at the as-of
    diffs = [-1.0] * in_at + [1.0] * (n - in_at)
    assert cross_history(days, diffs) == [(days[in_at], HISTORY_SESSIONS - 1, 1)]
    out_at = in_at - 1  # sessions_ago 90: the 91st bar
    diffs = [-1.0] * out_at + [1.0] * (n - out_at)
    assert cross_history(days, diffs) == []


def test_the_price_200d_crosses_and_the_gap_ride_the_reading():
    bars = _bars(WHIPSAW)
    r = company_crosses(bars, ASOF, None, None)
    closes = [float(b["close"]) for b in bars]
    fast, slow = sma._sma_series(closes, 50), sma._sma_series(closes, 200)
    assert r.gap_pct == round((fast[-1] / slow[-1] - 1) * 100, 4)
    assert r.gap_pct_then == round((fast[-6] / slow[-6] - 1) * 100, 4)
    assert {e.direction for e in r.price_200d} == {"up", "down"}
    assert all(e.sessions_ago < HISTORY_SESSIONS for e in r.price_200d)


def _sig(kind: str, headline: str | None) -> DisplaySignal:
    return DisplaySignal(
        kind=kind,
        label=kind,
        headline=None if headline is None else DisplayHeadline(key=headline, label=headline),
        basis=DisplayBasis(source="fact_price_eod"),
    )


def test_posture_and_rs_come_off_the_members_headlines():
    bars = _bars(WHIPSAW)
    r = company_crosses(
        bars, ASOF, _sig("sma_position", "below_falling"), _sig("relative_strength", "leading")
    )
    assert (r.posture, r.rs_leading) == ("below_falling", True)
    for key in ("partial_rising", "level_rising", "above_flat", "below_na"):
        assert company_crosses(bars, ASOF, _sig("sma_position", key), None).posture == "other"
    lag = company_crosses(bars, ASOF, None, _sig("relative_strength", "lagging"))
    assert (lag.posture, lag.rs_leading) == ("other", False)


# ---------------------------------------------------------------------------------------------------------
# priced or not: counted, never a fake "no cross"
# ---------------------------------------------------------------------------------------------------------


def test_a_name_without_enough_bars_or_with_stale_tape_is_not_priced():
    rising = [100.0 + i * 0.1 for i in range(250)]
    short = company_crosses(_bars(rising[:199]), ASOF, None, None)
    assert (short.priced, short.not_priced, short.bars, short.crosses) == (
        False,
        "short_tape",
        199,
        (),
    )
    just = company_crosses(_bars(rising[:200]), ASOF, None, None)
    assert (
        just.priced and just.readable_sessions == 0
    )  # a 200d SMA, but no bar a cross could print on
    assert company_crosses([], ASOF, None, None).not_priced == "short_tape"
    # the member's own stale-tape tell: a last bar more than STALE_GAP_DAYS before the as-of
    edge = company_crosses(
        _bars(rising, ASOF - timedelta(days=sma.STALE_GAP_DAYS)), ASOF, None, None
    )
    assert edge.priced
    stale = company_crosses(
        _bars(rising, ASOF - timedelta(days=sma.STALE_GAP_DAYS + 1)), ASOF, None, None
    )
    assert (stale.priced, stale.not_priced) == (False, "stale_tape")


def test_unpriced_names_are_counted_and_left_out_of_every_share():
    # two golden crosses among the priced names, three names not priced: 2 of 2 priced cluster
    result = one_group([reading("golden", 1), reading("golden", 2), SHORT, SHORT, STALE])
    (c,) = result.clusters
    assert (c.count, c.priced, c.share) == (2, 2, 1.0)
    assert (c.group.names, c.group.priced, c.group.not_priced) == (5, 2, 3)
    assert (result.priced_companies, result.short_tape_companies, result.stale_tape_companies) == (
        2,
        2,
        1,
    )
    (row,) = result.timeline
    unpriced = [m for m in row.members if not m.priced]
    assert [m.not_priced for m in unpriced] == ["short_tape", "short_tape", "stale_tape"]
    assert all(m.crosses == () and m.posture == "other" for m in unpriced)
    assert row.posture.other == 3


# ---------------------------------------------------------------------------------------------------------
# the 10-session window
# ---------------------------------------------------------------------------------------------------------


def test_the_recent_window_is_the_last_10_bars():
    assert recent_cross(reading("golden", RECENT_SESSIONS - 1)) is not None  # bar 10: in
    assert recent_cross(reading("golden", RECENT_SESSIONS)) is None  # bar 11: out
    assert recent_cross(reading("golden", 0)) is not None  # the as-of's own bar
    # two names crossing on bar 10 and bar 11: only one counts, so no cluster
    edge = one_group([reading("golden", 9), reading("golden", 10), reading()])
    assert edge.clusters == ()
    (fm,) = edge.first_movers
    assert (fm.leader.ticker, fm.leader.sessions_ago) == ("N0", 9)


def test_a_name_counts_by_its_latest_cross_in_the_window():
    whipsaw = reading(
        crosses=(
            CrossEvent(ASOF - timedelta(days=6), "golden", 6),
            CrossEvent(ASOF - timedelta(days=2), "death", 2),
        )
    )
    assert recent_cross(whipsaw).direction == "death"
    result = one_group([whipsaw, reading("death", 4), reading("golden", 3)])
    assert [(c.direction, c.count) for c in result.clusters] == [("death", 2)]


# ---------------------------------------------------------------------------------------------------------
# the cluster rule: >= 2 names AND >= 40% of the priced names, the same direction
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "count, priced, hit",
    [(2, 2, True), (2, 4, True), (3, 7, True), (1, 2, False), (2, 10, False), (2, 5, True)],
)
def test_the_cluster_rule(count, priced, hit):
    assert CLUSTER_MIN_SHARE_PCT == 40
    assert clusters(count, priced) is hit
    result = one_group(
        [reading("golden", i) for i in range(count)] + [reading()] * (priced - count)
    )
    assert bool(result.clusters) is hit
    if hit:
        (c,) = result.clusters
        assert (c.direction, c.count, c.priced, c.within) == ("golden", count, priced, count)


def test_mixed_directions_never_cluster_together():
    assert one_group([reading("golden", 1), reading("death", 2)]).clusters == ()
    # 2 golden + 1 death of 5: a golden cluster of 2 (40%), never a cluster of 3
    result = one_group(
        [reading("golden", 1), reading("golden", 5), reading("death", 2), reading(), reading()]
    )
    assert [(c.direction, c.count, c.share) for c in result.clusters] == [("golden", 2, 0.4)]


def test_a_cluster_row_shows_its_pieces():
    result = one_group(
        [
            reading("golden", 5, rs=True),
            reading("golden", 0, rs=True),
            reading("golden", 2, rs=False),
            *[reading()] * 4,
        ]
    )
    (c,) = result.clusters
    # "3 of 7 golden · within 6 sessions · 2 are RS leaders", names newest first with their dates
    assert (c.count, c.priced, c.within, c.rs_leaders) == (3, 7, 6, 2)
    assert [(n.ticker, n.sessions_ago, n.d) for n in c.names] == [
        ("N1", 0, ASOF),
        ("N2", 2, ASOF - timedelta(days=2)),
        ("N0", 5, ASOF - timedelta(days=5)),
    ]


def test_clusters_sort_by_share_then_count():
    rows, readings = [], {}
    for name, n_cross, n_priced in (("A", 2, 4), ("B", 2, 2), ("C", 4, 8), ("D", 3, 3)):
        for i in range(n_priced):
            sid = uuid.uuid4()
            rows.append(ms(T1, "Card", f"{name}{i}", sid, name))
            readings[sid] = reading("death", 1) if i < n_cross else reading()
    result = cross_radar(inputs(rows, readings), {}, asof=ASOF)
    assert [(c.group.link, c.share, c.count) for c in result.clusters] == [
        ("D", 1.0, 3),
        ("B", 1.0, 2),
        ("C", 0.5, 4),
        ("A", 0.5, 2),
    ]


# ---------------------------------------------------------------------------------------------------------
# first movers
# ---------------------------------------------------------------------------------------------------------


def test_a_first_mover_and_the_rest_in_three_counts():
    result = one_group(
        [
            reading("golden", 4, rs=True),  # the leader
            reading(gap=-1.0, then=-1.5),  # closing in on golden
            reading(gap=-1.9, then=-2.4),  # closing in on golden
            reading(gap=3.0, then=2.5),  # already above, no cross in the window
            reading(gap=-5.0, then=-4.0),  # not yet
            reading(gap=-1.0, then=-0.5),  # widening: not yet
        ]
    )
    (fm,) = result.first_movers
    assert (fm.direction, fm.leader.ticker, fm.leader.sessions_ago, fm.leader.rs_leading) == (
        "golden",
        "N0",
        4,
        True,
    )
    assert (fm.rest, fm.closing_in, fm.already, fm.not_yet) == (5, 2, 1, 2)
    assert result.clusters == ()


def test_the_death_mirror_first_to_roll_over():
    result = one_group(
        [
            reading("death", 1),
            reading(gap=1.0, then=1.8),  # closing in on death
            reading(gap=-4.0, then=-4.0),  # already below
            reading(gap=5.0, then=5.0),  # not yet
            reading("golden", 30, gap=6.0),  # a golden cross outside the window: not yet
        ]
    )
    (fm,) = result.first_movers
    assert (fm.direction, fm.rest, fm.closing_in, fm.already, fm.not_yet) == ("death", 4, 1, 1, 2)


def test_no_first_mover_without_someone_to_lead_or_with_two_crossing():
    assert one_group([reading("golden", 1)]).first_movers == ()  # a one-name group
    assert one_group([reading("golden", 1), SHORT]).first_movers == ()  # one PRICED name
    assert one_group([reading("golden", 1), reading("golden", 2), reading()]).first_movers == ()
    # a recent cross on the other name the other way does not stop a first mover
    both = one_group([reading("golden", 1), reading("death", 2), reading()])
    assert sorted(f.direction for f in both.first_movers) == ["death", "golden"]


def test_first_movers_sort_newest_cross_first():
    rows, readings = [], {}
    for name, ago in (("Old", 8), ("New", 0), ("Mid", 3)):
        for i in range(2):
            sid = uuid.uuid4()
            rows.append(ms(T1, "Card", f"{name}{i}", sid, name))
            readings[sid] = reading("golden", ago) if i == 0 else reading()
    result = cross_radar(inputs(rows, readings), {}, asof=ASOF)
    assert [f.group.link for f in result.first_movers] == ["New", "Mid", "Old"]


# ---------------------------------------------------------------------------------------------------------
# the near-cross watch
# ---------------------------------------------------------------------------------------------------------


def test_the_near_cross_band_edges():
    # 2.0% in, 2.1% out, on the gap the walk computes (rounded, so 102/100 reads as 2.0, not 2.0000000000000018)
    assert NEAR_BAND_PCT == 2.0
    assert _gap_pct(102.0, 100.0) == 2.0 and _gap_pct(102.1, 100.0) == 2.1
    assert near_cross(reading(gap=_gap_pct(102.0, 100.0), then=2.5)) == "death"
    assert near_cross(reading(gap=_gap_pct(102.1, 100.0), then=2.5)) is None
    assert near_cross(reading(gap=_gap_pct(98.0, 100.0), then=-2.5)) == "golden"
    assert near_cross(reading(gap=_gap_pct(97.9, 100.0), then=-2.5)) is None


def test_the_near_cross_watch_needs_a_narrowing_gap_and_no_recent_cross():
    assert near_cross(reading(gap=-1.0, then=-1.5)) == "golden"  # narrowing
    assert near_cross(reading(gap=-1.0, then=-0.5)) is None  # widening
    assert near_cross(reading(gap=-1.0, then=-1.0)) is None  # unchanged
    assert near_cross(reading(gap=1.0, then=-1.5)) is None  # crossed (a recent cross would show)
    assert (
        near_cross(reading("golden", 3, gap=0.5, then=-1.0)) is None
    )  # crossed within 10 sessions
    assert near_cross(reading("golden", 10, gap=-0.5, then=-1.0)) == "golden"  # bar 11: watch again
    assert near_cross(reading(gap=0.0, then=-1.0)) == "golden"  # on the line: the side it came from
    assert near_cross(reading(gap=-1.0, then=None)) is None  # no reading 5 sessions back
    assert near_cross(SHORT) is None


def test_the_near_cross_groups_list_smallest_gap_first():
    result = one_group(
        [reading(gap=-1.2, then=-1.9), reading(gap=0.3, then=0.9), reading(gap=-5.0, then=-6.0)]
    )
    (g,) = result.near_cross
    assert [(n.ticker, n.toward, n.gap_pct, n.change) for n in g.names] == [
        ("N1", "death", 0.3, -0.6),
        ("N0", "golden", -1.2, 0.7),
    ]


# ---------------------------------------------------------------------------------------------------------
# the four views and who counts (the Rotation Map's rules, via dashboards.baskets)
# ---------------------------------------------------------------------------------------------------------


def _two_link_company():
    a, b, c = S[0], S[1], S[2]
    rows = [
        ms(T1, "Grid", "AAA", a, "Turbines"),
        ms(T1, "Grid", "AAA", a, "Cables"),
        ms(T1, "Grid", "BBB", b, "Turbines"),
        ms(T2, "Nuclear", "AAA", a, "Fuel"),
        ms(T2, "Nuclear", "CCC", c, "Fuel"),
    ]
    readings = {a: reading("golden", 1), b: reading("golden", 2), c: reading(gap=-1.0, then=-1.4)}
    identity = {
        a: {"business_type": "utilities", "business_supersector": "energy_utilities"},
        b: {"business_type": "utilities", "business_supersector": "energy_utilities"},
        c: {"business_type": "miner", "business_supersector": "materials"},
    }
    return rows, readings, identity


def test_the_link_view_counts_a_two_link_company_in_both_links():
    rows, readings, identity = _two_link_company()
    result = cross_radar(inputs(rows, readings), identity, asof=ASOF, group_by="link")
    turbines = grp(result.timeline, f"link:{T1}:Turbines")
    cables = grp(result.timeline, f"link:{T1}:Cables")
    assert [m.ticker for m in turbines.members] == ["AAA", "BBB"]
    assert [m.ticker for m in cables.members] == ["AAA"]
    assert (turbines.golden, cables.golden) == (2, 1)
    assert (turbines.group.link, turbines.group.thesis_name) == ("Turbines", "Grid")
    assert [c.group.link for c in result.clusters] == ["Turbines"]
    # Fuel: AAA crossed, CCC closing in -> a first mover with one name closing in
    (fm,) = result.first_movers
    assert (fm.group.link, fm.leader.ticker, fm.closing_in) == ("Fuel", "AAA", 1)
    assert len(result.timeline) == 3


def test_the_type_sector_and_card_views_count_each_company_once_per_group():
    rows, readings, identity = _two_link_company()
    by_card = cross_radar(inputs(rows, readings), identity, asof=ASOF, group_by="card")
    grid = grp(by_card.timeline, f"card:{T1}")
    assert (grid.group.names, grid.golden) == (2, 2)  # AAA once, its two links folded together
    aaa = next(m for m in grid.members if m.ticker == "AAA")
    assert [c.links for c in aaa.cards] == [("Turbines", "Cables")]
    assert grp(by_card.clusters, f"card:{T1}").count == 2

    by_type = cross_radar(inputs(rows, readings), identity, asof=ASOF, group_by="business_type")
    util = grp(by_type.timeline, "type:utilities")
    assert (util.group.names, util.group.business_type, util.golden) == (2, "utilities", 2)
    aaa = next(m for m in util.members if m.ticker == "AAA")
    assert [(c.thesis_name, c.links) for c in aaa.cards] == [
        ("Grid", ("Turbines", "Cables")),
        ("Nuclear", ("Fuel",)),
    ]
    by_sector = cross_radar(inputs(rows, readings), identity, asof=ASOF, group_by="sector")
    assert grp(by_sector.timeline, "sector:energy_utilities").group.names == 2
    assert grp(by_sector.near_cross, "sector:materials").names[0].ticker == "CCC"
    assert by_sector.companies == 3


def test_silent_memberships_are_left_out_by_default_and_their_own_groups_when_shown():
    rows = [
        ms(T1, "Grid", "AAA", S[0], "Turbines"),
        ms(T1, "Grid", "CTX", S[1], CONTEXT_LABEL),
        ms(T1, "Grid", "BNC", S[2], BENCH_LABEL),
        ms(T2, "Coal", "CTX", S[1], "Miners"),  # voting on another card
    ]
    readings = {S[0]: reading("golden", 1), S[1]: reading("golden", 2), S[2]: reading()}
    off = cross_radar(inputs(rows, readings), {}, asof=ASOF, group_by="link")
    assert {t.group.link for t in off.timeline} == {"Turbines", "Miners"}
    assert (off.silent_rows_left_out, off.basket_rows, off.companies) == (2, 4, 2)

    on = cross_radar(inputs(rows, readings), {}, asof=ASOF, group_by="link", include_silent=True)
    assert {t.group.link for t in on.timeline} == {"Turbines", "Miners", CONTEXT_LABEL, BENCH_LABEL}
    assert on.silent_rows_left_out == 0 and on.companies == 3

    card_off = cross_radar(inputs(rows, readings), {}, asof=ASOF, group_by="card")
    assert grp(card_off.timeline, f"card:{T1}").group.names == 1
    assert card_off.clusters == ()
    card_on = cross_radar(
        inputs(rows, readings), {}, asof=ASOF, group_by="card", include_silent=True
    )
    assert grp(card_on.timeline, f"card:{T1}").group.names == 3
    assert [c.count for c in card_on.clusters if c.group.thesis_name == "Grid"] == [2]


def test_unresolved_rows_are_counted_never_grouped():
    rows = [ms(T1, "Grid", "AAA", S[0]), ms(T1, "Grid", "NEWCO", None)]
    result = cross_radar(inputs(rows, {S[0]: reading()}), {}, asof=ASOF)
    assert (result.unresolved_rows, result.companies) == (1, 1)
    assert [m.ticker for t in result.timeline for m in t.members] == ["AAA"]


# ---------------------------------------------------------------------------------------------------------
# the timeline and its price x 200d layer
# ---------------------------------------------------------------------------------------------------------


def test_the_price_200d_layer_is_timeline_only_and_never_moves_a_cluster():
    price = (CrossEvent(ASOF - timedelta(days=1), "up", 1), CrossEvent(ASOF, "down", 0))
    readings_list = [
        reading("golden", 1, price=price),
        reading("golden", 3, price=price),
        reading(gap=-1.0, then=-1.5, price=price),
        reading(price=price),
        reading(price=price),
    ]
    sids = [uuid.uuid4() for _ in readings_list]
    rows = [ms(T1, "Grid", f"N{i}", sid) for i, sid in enumerate(sids)]
    data = inputs(rows, dict(zip(sids, readings_list)))
    off = cross_radar(data, {}, asof=ASOF, price_200d=False)
    on = cross_radar(data, {}, asof=ASOF, price_200d=True)
    assert (on.clusters, on.first_movers, on.near_cross) == (
        off.clusters,
        off.first_movers,
        off.near_cross,
    )
    assert len(on.clusters) == 1  # 2 of 5 golden
    (row_off,), (row_on,) = off.timeline, on.timeline
    assert (row_off.price_up, row_off.price_down) == (0, 0)
    assert all(m.price_200d == () for m in row_off.members)
    assert (row_on.price_up, row_on.price_down) == (5, 5)
    assert (row_on.golden, row_on.death) == (row_off.golden, row_off.death) == (2, 0)


def test_timeline_marks_sit_on_the_axis_and_rows_sort_by_total_crosses():
    sessions = [ASOF - timedelta(days=4 - i) for i in range(5)]
    old = CrossEvent(
        ASOF - timedelta(days=40), "death", 40
    )  # older than the axis: pinned at its left edge
    rows, readings = [], {}
    for link, rd in (
        ("Quiet", reading()),
        ("Busy", reading(crosses=(old, CrossEvent(ASOF - timedelta(days=1), "golden", 1)))),
        ("One", reading("death", 0)),
    ):
        sid = uuid.uuid4()
        rows.append(ms(T1, "Card", link, sid, link))
        readings[sid] = rd
    result = cross_radar(inputs(rows, readings, sessions=sessions), {}, asof=ASOF)
    assert [t.group.link for t in result.timeline] == ["Busy", "One", "Quiet"]
    busy = result.timeline[0]
    assert (busy.golden, busy.death) == (1, 1)
    # newest first; each on its column (0 = the oldest session)
    assert [(m.direction, m.pos) for m in busy.members[0].crosses] == [("golden", 3), ("death", 0)]
    assert result.timeline[1].members[0].crosses[0].pos == 4
    assert result.sessions == tuple(sessions)


def test_timeline_members_list_the_most_recent_cross_first():
    result = one_group(
        [reading(), reading("golden", 7), SHORT, reading("death", 2)],
    )
    (row,) = result.timeline
    assert [m.ticker for m in row.members] == ["N3", "N1", "N0", "N2"]


def test_the_posture_bar_counts_every_name():
    result = one_group(
        [
            reading(posture="above_rising"),
            reading(posture="above_rising"),
            reading(posture="below_rising"),
            reading(posture="other"),
            SHORT,
        ]
    )
    p = result.timeline[0].posture
    assert (p.above_rising, p.above_falling, p.below_rising, p.below_falling, p.other) == (
        2,
        0,
        1,
        0,
        2,
    )


def test_the_timeline_axis_is_spy_sessions_plus_any_a_company_printed():
    spy = [date(2026, 5, 1) + timedelta(days=i) for i in range(100)]
    extra = [date(2026, 8, 20)]
    axis = timeline_sessions(spy, extra)
    assert len(axis) == HISTORY_SESSIONS and axis[-1] == date(2026, 8, 20)
    assert axis[-2] == spy[-1]
    assert timeline_sessions([], [ASOF]) == (ASOF,)


# ---------------------------------------------------------------------------------------------------------
# a flagged price history (dashboards.baskets.price_flags): a scale break makes fake crosses, so the name is
# left out of everything and named on the page
# ---------------------------------------------------------------------------------------------------------

MIXED = "mixed-scale price history"


def _robots():
    """Card 1's Core link: RBOT's scale break reads as a fresh golden cross (1 session ago) that, with AAA's
    real one, would make a 2-of-4 cluster; BBB sits far below; CCC is closing in on a golden cross. Card 2's
    Power link: DDD and EEE both crossed golden (a real cluster)."""
    rows = [
        ms(T1, "Robots", "RBOT", S[0]),
        ms(T1, "Robots", "AAA", S[1]),
        ms(T1, "Robots", "BBB", S[2]),
        ms(T1, "Robots", "CCC", S[3]),
        ms(T2, "Power", "DDD", S[4], "Power"),
        ms(T2, "Power", "EEE", S[5], "Power"),
    ]
    readings = {
        S[0]: reading("golden", 1, gap=900.0, then=-5.0, rs=True, posture="above_rising"),
        S[1]: reading("golden", 2, gap=1.0, then=-1.0, posture="above_rising"),
        S[2]: reading(gap=-10.0, then=-9.0),
        S[3]: reading(gap=-1.5, then=-3.0, posture="below_rising"),
        S[4]: reading("golden", 3, gap=2.0, then=-1.0, posture="above_rising"),
        S[5]: reading("golden", 4, gap=3.0, then=-1.0, posture="above_rising"),
    }
    return rows, readings


def test_a_flagged_company_is_left_out_of_every_read_and_named_on_the_page():
    """Unflagged, RBOT's fake cross makes a Robots cluster. Flagged, it is never priced and in no cluster, first
    mover, near-cross list, timeline count or posture: the Robots link reads exactly as without RBOT (AAA is the
    first mover, CCC closing in). RBOT stays on the timeline row (no cross, its flag), counts in the group's
    names and ``flagged`` (never ``not_priced``), and the page names it with the flag's label."""
    rows, readings = _robots()
    plain = cross_radar(inputs(rows, readings), {}, asof=ASOF)
    assert [(c.group.link, c.count, c.priced) for c in plain.clusters] == [
        ("Power", 2, 2),
        ("Core", 2, 4),  # RBOT's fake cross + AAA's: 2 of 4 is a cluster
    ]

    without = cross_radar(inputs(rows[1:], readings), {}, asof=ASOF)
    result = cross_radar(inputs(rows, readings, flagged={S[0]: MIXED}), {}, asof=ASOF)
    assert [(c.group.link, c.count, c.priced) for c in result.clusters] == [("Power", 2, 2)]
    assert [(f.group.link, f.leader.ticker, f.rest, f.closing_in) for f in result.first_movers] == [
        ("Core", "AAA", 2, 1)
    ]
    assert [[n.ticker for n in g.names] for g in result.near_cross] == [["CCC"]]

    row, alone = grp(result.timeline, "Core"), grp(without.timeline, "Core")
    assert (row.golden, row.death, row.posture) == (alone.golden, alone.death, alone.posture)
    assert row.posture.above_rising == 1  # RBOT's "above_rising" is in no posture count
    g = row.group
    assert (g.names, g.priced, g.not_priced, g.flagged) == (4, 3, 0, 1)
    rbot = next(m for m in row.members if m.ticker == "RBOT")
    assert (rbot.flag, rbot.priced, rbot.not_priced, rbot.crosses) == (MIXED, False, None, ())
    assert row.members[-1] is rbot  # no cross, never priced, flagged: last
    assert all(m.flag is None for m in row.members if m.ticker != "RBOT")

    assert [(f.ticker, f.reason) for f in result.flagged] == [("RBOT", MIXED)]
    assert (result.companies, result.priced_companies) == (6, 5)
    assert (result.short_tape_companies, result.stale_tape_companies) == (0, 0)


def test_an_unflagged_group_is_unaffected_by_a_flag_elsewhere():
    rows, readings = _robots()
    plain = cross_radar(inputs(rows, readings), {}, asof=ASOF)
    flagged = cross_radar(inputs(rows, readings, flagged={S[0]: MIXED}), {}, asof=ASOF)
    assert grp(flagged.timeline, "Power") == grp(plain.timeline, "Power")
    assert [c for c in flagged.clusters if c.group.link == "Power"] == [
        c for c in plain.clusters if c.group.link == "Power"
    ]
    assert plain.flagged == () and grp(plain.timeline, "Core").group.flagged == 0


class _Pit:
    """A stand-in point-in-time view: each company's bars, by security."""

    def __init__(self, bars: dict):
        self.bars = bars

    def price_history(self, sid, lookback_days):
        return self.bars[sid]


def test_the_router_reads_the_price_flags_inside_the_cached_read(monkeypatch):
    """``read_inputs`` itself reads the flags (the real one, its DB reads stubbed): once per cached read, on
    the request's one ``serve_view`` pair. A flagged company's bars never shape the timeline's axis (its bar
    after SPY's last one adds no session; a clean company's does), and the page names it."""
    rows = [ms(T1, "Robots", "RBOT", S[0]), ms(T1, "Robots", "AAA", S[1])]
    spy = [ASOF - timedelta(days=i) for i in range(5, 1, -1)]  # SPY ends 2026-05-30
    bars = {
        S[0]: _bars([100.0 + i for i in range(260)], end=ASOF),  # one bar past AAA's
        S[1]: _bars([50.0 + i * 0.1 for i in range(260)], end=ASOF - timedelta(days=1)),
    }
    none = lambda pit, sid, asof: None  # noqa: E731 - the two display members: no reading
    seen: dict = {"flag_reads": 0}

    def fake_flags(conn, ids, asof, *, known_at, clock):
        seen["flag_reads"] += 1
        seen["flags_view"] = (set(ids), asof, known_at, clock)
        return {S[0]: MIXED}

    CACHE.clear()
    monkeypatch.setattr(
        router_mod, "cards_as_of", lambda conn, asof, known_at: CardsAsOf(1, [], 0, 0)
    )
    monkeypatch.setattr(router_mod, "memberships_of", lambda cards: tuple(rows))
    monkeypatch.setattr(router_mod, "display_members", lambda names, dash: {n: none for n in names})
    monkeypatch.setattr(
        router_mod,
        "per_company",
        lambda conn, sids, asof, known_at, clock, read: {s: read(_Pit(bars), s) for s in sids},
    )
    monkeypatch.setattr(router_mod, "_spy_dates", lambda conn, asof, known_at, clock: spy)
    monkeypatch.setattr(router_mod, "price_flags", fake_flags)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    try:
        client = TestClient(app)
        body = client.get("/dashboards/cross-radar", params={"asof": "2026-06-01"}).json()
        again = client.get(
            "/dashboards/cross-radar", params={"asof": "2026-06-01", "group_by": "card"}
        ).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    ids, asof, known_at, clock = seen["flags_view"]
    assert seen["flag_reads"] == 1 and again["cache"] == "hit"
    assert ids == {S[0], S[1]} and asof == ASOF and clock == "public" and known_at is not None
    assert (
        body["sessions"][-1] == (ASOF - timedelta(days=1)).isoformat()
    )  # AAA's last bar, not RBOT's
    assert body["flagged"] == [{"security_id": str(S[0]), "ticker": "RBOT", "reason": MIXED}]
    (row,) = body["timeline"]
    assert row["group"]["flagged"] == 1 and row["group"]["priced"] == 1
    rbot = next(m for m in row["members"] if m["ticker"] == "RBOT")
    assert rbot["flag"] == MIXED and rbot["crosses"] == [] and rbot["priced"] is False


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
        return inputs(rows, readings, sessions=[ASOF - timedelta(days=1), ASOF])

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


def test_the_router_threads_the_view_and_serves_every_view_off_one_cached_read(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/cross-radar", params={"asof": "2026-06-01"}).json()
    assert body["cache"] == "computed" and body["group_by"] == "link"
    assert body["include_silent"] is False and body["price_200d"] is False
    assert (body["recent_sessions"], body["history_sessions"], body["near_band_pct"]) == (
        10,
        90,
        2.0,
    )
    assert (body["cluster_min_names"], body["cluster_min_share_pct"]) == (2, 40)
    asof, known_at, clock = seen["view"]
    assert asof == date(2026, 6, 1) and clock == "public" and known_at is not None

    for group_by in ("business_type", "sector", "card"):
        for price_200d in ("true", "false"):
            again = client.get(
                "/dashboards/cross-radar",
                params={
                    "asof": "2026-06-01",
                    "group_by": group_by,
                    "include_silent": "true",
                    "price_200d": price_200d,
                },
            ).json()
            assert again["cache"] == "hit" and again["group_by"] == group_by
    assert seen["reads"] == 1
    assert seen["identity_ids"] == {S[0], S[1], S[2]}

    today = market_today().isoformat()
    client.get("/dashboards/cross-radar", params={"asof": today})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 2


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/cross-radar", params={"asof": "2026-06-01"}).json()
    (cluster,) = body["clusters"]
    assert cluster["group"]["link"] == "Turbines" and cluster["direction"] == "golden"
    assert [n["ticker"] for n in cluster["names"]] == ["AAA", "BBB"]
    assert cluster["names"][0]["cards"][0]["thesis_name"] == "Grid"
    assert body["sessions"] == ["2026-05-31", "2026-06-01"]
    fuel = next(t for t in body["timeline"] if t["group"]["link"] == "Fuel")
    assert set(fuel["posture"]) == {
        "above_rising",
        "above_falling",
        "below_rising",
        "below_falling",
        "other",
    }
    aaa = next(m for m in fuel["members"] if m["ticker"] == "AAA")
    assert aaa["crosses"] == [
        {"d": "2026-05-31", "direction": "golden", "sessions_ago": 1, "pos": 0}
    ]
    assert body["near_cross"][0]["names"][0]["ticker"] == "CCC"


def test_the_router_rejects_an_unknown_grouping(stub_client):
    client, _ = stub_client
    r = client.get("/dashboards/cross-radar", params={"asof": "2026-06-01", "group_by": "mood"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a price published after a past as-of stays invisible to it (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

_THAW = datetime(2026, 9, 1, 18, 13, 31, tzinfo=timezone.utc)
_LATER = date(2026, 9, 10)  # a later past as-of, after the correction was published


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


def test_a_price_published_after_a_past_asof_stays_invisible(db):
    """Daily bars from 319 days before the as-of to a later date, each public at its own session: the
    whipsaw tape through the as-of, then a rally after it. The as-of's own bar is re-versioned a week after
    the thaw at ten times the close. The past view reads the bars up to the as-of with the ORIGINAL close
    (neither the later bars nor the correction were public by the end of that day); a later past view reads
    both."""
    sid = _security(db, "XRAD")
    before = WHIPSAW + [20.0] * 10  # 300 bars ending at the as-of
    after = [20.0 + i * 3.0 for i in range(1, (_LATER - ASOF).days + 1)]
    start = ASOF - timedelta(days=len(before) - 1)
    for i, close in enumerate(before + after):
        d = start + timedelta(days=i)
        _price(db, sid, d, close, datetime.combine(d, time.min, tzinfo=timezone.utc))
    _price(db, sid, ASOF, 200.0, _THAW + timedelta(days=7))  # the as-of bar, corrected later
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Cross Radar fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[BasketMember(ticker="XRAD", role="the name", security_id=sid, segment="Core")],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = client.get("/dashboards/cross-radar", params={"asof": ASOF.isoformat()}).json()
        later = client.get("/dashboards/cross-radar", params={"asof": _LATER.isoformat()}).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    original = company_crosses(_bars(before), ASOF, None, None)
    corrected = company_crosses(_bars(before[:-1] + [200.0] + after, _LATER), _LATER, None, None)
    (row,) = past["timeline"]
    (name,) = row["members"]
    assert name["ticker"] == "XRAD" and row["group"]["link"] == "Core"
    assert name["priced"] and name["last_bar"] == ASOF.isoformat()
    assert name["gap_pct"] == round(original.gap_pct, 2)  # not the correction's
    assert [(m["direction"], m["d"]) for m in name["crosses"]] == [
        (e.direction, e.d.isoformat()) for e in reversed(original.crosses)
    ]
    assert all(m["d"] <= ASOF.isoformat() for m in name["crosses"])
    assert past["sessions"][-1] == ASOF.isoformat()
    # the card existed on the as-of; no roster snapshot then, so today's roster stood in (and is counted)
    assert past["cards_counted"] == 1 and past["roster_fallback_cards"] == 1

    (lname,) = later["timeline"][0]["members"]
    assert lname["last_bar"] == _LATER.isoformat()
    assert lname["gap_pct"] == round(corrected.gap_pct, 2) != name["gap_pct"]
    assert any(m["d"] > ASOF.isoformat() for m in lname["crosses"])  # the rally's golden cross
