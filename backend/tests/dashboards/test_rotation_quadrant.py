"""Rotation Quadrant (``docs/dashboards/rotation-quadrant.md``): x and y on hand-computed numbers, each quadrant at
its edges (exactly 100), the tail's points and spacing, a group with fewer than 3 priced names listed but not
measured, a group without enough history, a flagged price history left out of the line and named, the link view
counting a two-link company in both links while the other views count it once; the router off one cached read;
and, DB-backed, a price published after a past as-of staying invisible to it."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import rotation_quadrant as router_mod
from dashboards.baskets import Membership
from dashboards.cache import CACHE
from dashboards.rotation_quadrant import (
    AGO_SESSIONS,
    LEVEL_SESSIONS,
    MEASURE_SPAN,
    MIN_PRICED,
    MOMENTUM_SESSIONS,
    TAIL_POINTS,
    TAIL_STEP,
    QuadrantGroup,
    group_sort_key,
    point,
    quadrant,
    quadrant_inputs,
    rotation_quadrant,
    rs_level,
    rs_momentum,
    tail,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo

ASOF = date(2026, 6, 1)
T1, T2 = uuid.uuid4(), uuid.uuid4()
S = [uuid.uuid4() for _ in range(8)]
MIXED = "mixed-scale price history"

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def days(n: int, end: date = ASOF) -> list[date]:
    """``n`` consecutive calendar days ending at ``end`` (the tests' sessions)."""
    return [end - timedelta(days=n - 1 - i) for i in range(n)]


def tape(ds: list[date], closes: list[float | None]) -> dict[date, float]:
    return {d: c for d, c in zip(ds, closes) if c is not None}


def flat_spy(ds: list[date]) -> dict[date, float]:
    return tape(ds, [100.0] * len(ds))


def ms(
    tid: UUID, ticker: str, sid: UUID | None, segment: str | None = "Core", card: str = "Card"
) -> Membership:
    return Membership(
        thesis_id=tid,
        thesis_name=card,
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
    )


def build(memberships, closes, spy=None, *, identity=None, include_silent=False, flagged=None):
    cards = len({m.thesis_id for m in memberships})
    return quadrant_inputs(
        memberships=memberships,
        closes=closes,
        spy=spy if spy is not None else {},
        identity=identity or {},
        asof=ASOF,
        include_silent=include_silent,
        live_cards=cards,
        cards_counted=cards,
        cards_not_yet_created=0,
        roster_fallback_cards=0,
        flagged=flagged,
    )


def view(inputs, group_by="link"):
    return rotation_quadrant(inputs, asof=ASOF, group_by=group_by)


def grp(page, key_part: str):
    return next(g for g in page.groups if key_part in g.key)


def trio(rs: list[float], ds: list[date], tickers=("AAA", "BBB", "CCC"), segment="Core"):
    """Three names moving identically along ``rs`` (so their equal-weighted line is ``100 × rs / rs[0]``)."""
    rows = [ms(T1, t, S[i], segment=segment) for i, t in enumerate(tickers)]
    closes = {S[i]: tape(ds, [base * v for v in rs]) for i, base in enumerate((10.0, 20.0, 40.0))}
    return rows, closes


# ---------------------------------------------------------------------------------------------------------
# x and y on hand-computed numbers
# ---------------------------------------------------------------------------------------------------------

# 86 sessions: the RS ratio is 1.0 throughout except 0.8 on session 65.
#   x on 65 = 100 × 0.8 ÷ ((64 × 1.0 + 0.8) / 65) = 80 × 65 / 64.8 = 80.2469...
#   x on 85 = 100 × 1.0 ÷ ((64 × 1.0 + 0.8) / 65) = 100 × 65 / 64.8 = 100.3086...  (0.8 still in its window)
#   y on 85 = 100 × x85 ÷ x65 = 100 / 0.8 = 125.0
RS_PATH = [1.0] * 65 + [0.8] + [1.0] * 20


def test_x_and_y_on_hand_computed_numbers():
    assert len(RS_PATH) == 86 and LEVEL_SESSIONS == 65 and MOMENTUM_SESSIONS == 20
    assert rs_level(RS_PATH, 65) == pytest.approx(80 * 65 / 64.8)
    assert rs_level(RS_PATH, 85) == pytest.approx(100 * 65 / 64.8)
    assert rs_momentum(RS_PATH, 85) == pytest.approx(125.0)
    assert rs_level(RS_PATH, 64) == pytest.approx(100.0)  # the first full window: all 1.0
    assert rs_level(RS_PATH, 63) is None  # 64 sessions: not a full window
    # the first y: on 84, x 20 sessions back is on 64 (the first full window); on 83 that x is on 63, none
    assert rs_momentum(RS_PATH, 84) == pytest.approx(100 * 65 / 64.8)  # 100 × x84 ÷ x64 (= 100)
    assert rs_momentum(RS_PATH, 83) is None


def test_the_group_measures_the_same_numbers_end_to_end():
    """Three names moving along RS_PATH against a flat SPY: the group's RS ratio IS RS_PATH."""
    ds = days(86)
    rows, closes = trio(RS_PATH, ds)
    g = grp(view(build(rows, closes, flat_spy(ds))), "Core")
    assert g.status == "plotted" and g.names == 3 and g.priced == 3
    assert g.x == round(100 * 65 / 64.8, 2) == 100.31
    assert g.y == 125.0 and g.quadrant == "leading"
    assert g.sessions_flat == 0


def test_rs_is_the_group_line_over_spys_line():
    """The same group against an SPY that halves its RS: the line ÷ SPY's, not the line alone."""
    ds = days(86)
    rows, closes = trio([1.0] * 86, ds)  # the group flat at 100
    spy = tape(
        ds, [100.0 / v for v in RS_PATH]
    )  # SPY moves inversely, so the RS ratio is RS_PATH again
    g = grp(view(build(rows, closes, spy)), "Core")
    assert g.x == 100.31 and g.y == 125.0


def test_a_steady_outperformer_reads_x_above_100_and_flat_momentum():
    """A constant 0.1% a session over SPY: x sits above 100 and never moves, so y is exactly 100 (as shown):
    weakening, since exactly 100 is never above."""
    ds = days(130)
    rows, closes = trio([1.001**i for i in range(130)], ds)
    g = grp(view(build(rows, closes, flat_spy(ds))), "Core")
    assert g.x is not None and g.x > 103 and g.y == 100.0
    assert g.quadrant == "weakening"
    assert {p.x for p in g.tail} == {g.x}  # x is the same on every tail point


# ---------------------------------------------------------------------------------------------------------
# the quadrants at their edges
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "x, y, expected",
    [
        (100.01, 100.01, "leading"),
        (100.01, 100.0, "weakening"),
        (100.01, 99.99, "weakening"),
        (100.0, 100.0, "lagging"),
        (99.99, 99.99, "lagging"),
        (100.0, 99.99, "lagging"),
        (100.0, 100.01, "improving"),
        (99.99, 100.01, "improving"),
        (None, 100.5, None),
        (100.5, None, None),
    ],
)
def test_each_quadrant_at_its_edges(x, y, expected):
    assert quadrant(x, y) == expected


def test_the_quadrant_is_read_off_the_values_as_shown():
    """A raw x a hair above 100 shows as 100.0 and is not above it."""
    rs = [1.0] * 64 + [1.0 + 4e-5]  # x = 100.0039...: shown as 100.0
    p = point(rs, days(65), 64)
    assert p.x == 100.0 and p.y is None and p.quadrant is None
    assert quadrant(p.x, 100.01) == "improving"


# ---------------------------------------------------------------------------------------------------------
# the tail
# ---------------------------------------------------------------------------------------------------------


def _wave(n: int) -> list[float]:
    """A deterministic up-and-down RS path (a sawtooth with a drift)."""
    return [1.0 + 0.002 * i + 0.05 * ((i % 30) / 30.0) for i in range(n)]


def test_the_tail_is_nine_points_every_five_sessions_oldest_first():
    ds = days(140)
    rs = _wave(140)
    points = tail(rs, ds)
    assert len(points) == TAIL_POINTS == 9 and TAIL_STEP == 5
    idx = [ds.index(p.session) for p in points]
    assert idx == [139 - 5 * k for k in range(8, -1, -1)]  # 99, 104, ..., 139
    assert all(b - a == TAIL_STEP for a, b in zip(idx, idx[1:]))
    assert points[-1].session == ASOF
    for p, t in zip(points, idx):
        assert p == point(rs, ds, t)
        assert p.x is not None and p.y is not None  # every point measurable: 140 >= MEASURE_SPAN
    assert MEASURE_SPAN == 40 + 20 + 65


def test_the_groups_tail_and_its_quadrant_four_weeks_ago():
    ds = days(140)
    rs = _wave(140)
    rows, closes = trio(rs, ds)
    g = grp(view(build(rows, closes, flat_spy(ds))), "Core")
    for got, want in zip(g.tail, tail(rs, ds)):
        assert got.session == want.session and got.quadrant == want.quadrant
        assert got.x == pytest.approx(want.x, abs=0.011) and got.y == pytest.approx(
            want.y, abs=0.011
        )
    ago = g.tail[TAIL_POINTS - 1 - AGO_SESSIONS // TAIL_STEP]
    assert ago.session == ds[139 - AGO_SESSIONS] and g.quadrant_ago == ago.quadrant
    assert (g.tail[-1].x, g.tail[-1].y, g.tail[-1].quadrant) == (g.x, g.y, g.quadrant)


def test_a_short_read_leaves_the_oldest_tail_points_empty():
    ds = days(30)
    points = tail([1.0] * 30, ds)
    assert len(points) == 9
    assert [p.session for p in points[:3]] == [None, None, None]  # 29 - 40, - 35, - 30 < 0
    assert points[3].session == ds[4] and all(p.x is None for p in points)


# ---------------------------------------------------------------------------------------------------------
# who is plotted: fewer than 3 priced names, not enough history
# ---------------------------------------------------------------------------------------------------------


def test_fewer_than_three_priced_names_is_listed_never_plotted():
    """AAA and BBB are priced; CCC's last daily return is into the session 65 back (outside the last 65);
    DDD has no bars. Four names, two priced: listed with "—", never measured, never dropped."""
    ds = days(140)
    full = [10.0 + 0.01 * i for i in range(140)]
    rows = [ms(T1, t, S[i]) for i, t in enumerate(("AAA", "BBB", "CCC", "DDD"))]
    closes = {
        S[0]: tape(ds, full),
        S[1]: tape(ds, full),
        S[2]: tape(
            ds, full[:75] + [None] * 65
        ),  # bars on sessions 0..74: its last return into 74 = end - 65
        S[3]: {},
    }
    page = view(build(rows, closes, flat_spy(ds)))
    (g,) = page.groups
    assert g.names == 4 and g.priced == 2 < MIN_PRICED
    assert g.status == "too_small" and g.x is None and g.y is None and g.quadrant is None
    assert g.tail == () and g.quadrant_ago is None
    assert [m.ticker for m in g.members if m.priced] == ["AAA", "BBB"]
    assert (page.groups_plotted, page.groups_too_small, page.groups_no_history) == (0, 1, 0)

    # one session later and CCC's last return is inside the last 65: three priced, measured
    closes[S[2]] = tape(ds, full[:76] + [None] * 64)
    (g,) = view(build(rows, closes, flat_spy(ds))).groups
    assert g.priced == 3 and g.status == "plotted"


def test_a_short_tape_is_not_enough_history_with_x_shown_when_it_alone_reads():
    """70 sessions: today's x reads (65 sessions), y does not (it needs x 20 sessions back: 85)."""
    ds = days(70)
    rows, closes = trio(_wave(70), ds)
    page = view(build(rows, closes, flat_spy(ds)))
    (g,) = page.groups
    assert g.status == "no_history" and g.x is not None and g.y is None and g.quadrant is None
    assert (page.groups_plotted, page.groups_too_small, page.groups_no_history) == (0, 0, 1)


def test_names_that_joined_recently_are_not_enough_history_however_long_spy_runs():
    """SPY has a year; the group's names only the last 80 sessions. Before they join the line is carried flat,
    which is not history: today's y needs 85 sessions of the group's own line."""
    ds = days(253)
    rs = [None] * 173 + _wave(80)
    rows = [ms(T1, t, S[i]) for i, t in enumerate(("AAA", "BBB", "CCC"))]
    closes = {S[i]: tape(ds, [None if v is None else 10.0 * v for v in rs]) for i in range(3)}
    (g,) = view(build(rows, closes, flat_spy(ds))).groups
    assert g.priced == 3 and g.status == "no_history" and g.x is not None and g.y is None

    rs = [None] * 167 + _wave(86)  # 86 bars: the line starts 85 sessions back, enough for y
    closes = {S[i]: tape(ds, [None if v is None else 10.0 * v for v in rs]) for i in range(3)}
    (g,) = view(build(rows, closes, flat_spy(ds))).groups
    assert g.status == "plotted"


def test_no_spy_tape_measures_nothing():
    ds = days(140)
    rows, closes = trio(_wave(140), ds)
    inputs = build(rows, closes, {})
    page = view(inputs)
    (g,) = page.groups
    assert not inputs.spy_available and page.session_source == "companies"
    assert g.status == "no_history" and g.x is None and g.y is None and g.priced == 3


def test_a_session_no_name_can_contribute_to_is_counted_flat():
    ds = days(140)
    rs: list[float | None] = list(_wave(140))
    rs[120] = None  # every name misses session 120: sessions 120 and 121 carry flat
    rows = [ms(T1, t, S[i]) for i, t in enumerate(("AAA", "BBB", "CCC"))]
    closes = {S[i]: tape(ds, [None if v is None else 10.0 * v for v in rs]) for i in range(3)}
    (g,) = view(build(rows, closes, flat_spy(ds))).groups
    assert g.sessions_flat == 2 and g.status == "plotted"


# ---------------------------------------------------------------------------------------------------------
# a flagged price history: left out of the line, still a name, named
# ---------------------------------------------------------------------------------------------------------


def _with_robot(n: int = 140):
    """RBOT's stored tape breaks scale 10 sessions back (its close jumps ×90); BBB, CCC and DDD are clean."""
    ds = days(n)
    rbot = [5.0 * 0.99**i for i in range(n)]
    for i in range(n - 10, n):
        rbot[i] *= 90.0
    clean = {
        S[1]: tape(ds, [10.0 * (1.0 + 0.003 * i) for i in range(n)]),
        S[2]: tape(ds, [20.0 * (1.0 + 0.001 * i) for i in range(n)]),
        S[3]: tape(ds, [30.0 * (1.0 - 0.0005 * i) for i in range(n)]),
    }
    rows = [ms(T1, "RBOT", S[0])] + [
        ms(T1, t, S[i]) for i, t in zip((1, 2, 3), ("BBB", "CCC", "DDD"))
    ]
    return ds, rows, {S[0]: tape(ds, rbot), **clean}


def test_a_flagged_name_is_left_out_of_the_line_and_named():
    ds, rows, closes = _with_robot()
    spy = flat_spy(ds)
    unflagged = grp(view(build(rows, closes, spy)), "Core")
    inputs = build(rows, closes, spy, flagged={S[0]: MIXED})
    g = grp(view(inputs), "Core")
    alone = grp(view(build(rows[1:], {s: closes[s] for s in S[1:4]}, spy)), "Core")
    assert unflagged.x != alone.x  # the ×90 step moves the line when it is read
    assert (g.x, g.y, g.quadrant, g.tail) == (alone.x, alone.y, alone.quadrant, alone.tail)
    assert g.names == 4 and g.priced == 3 and alone.names == 3
    assert [(f.ticker, f.reason) for f in g.flagged] == [("RBOT", MIXED)]
    rbot = next(m for m in g.members if m.ticker == "RBOT")
    assert rbot.flag == MIXED and rbot.priced is False
    assert all(m.flag is None for m in g.members if m.ticker != "RBOT")
    assert inputs.flagged_companies == 1 and inputs.priced_companies == 3 and inputs.companies == 4
    assert view(inputs).flagged_companies == 1


def test_a_flag_that_leaves_fewer_than_three_priced_names_is_too_small_and_still_named():
    ds, rows, closes = _with_robot()
    g = grp(view(build(rows[:3], closes, flat_spy(ds), flagged={S[0]: MIXED})), "Core")
    assert g.status == "too_small" and g.names == 3 and g.priced == 2
    assert [(f.ticker, f.reason) for f in g.flagged] == [("RBOT", MIXED)]


def test_a_flagged_company_never_reaches_the_session_calendar():
    ds, rows, closes = _with_robot(90)
    late = ASOF + timedelta(days=1)
    closes[S[0]] = {**closes[S[0]], late: 1.0}
    asof = ASOF + timedelta(days=2)
    common = dict(
        memberships=rows,
        closes=closes,
        spy=flat_spy(ds),
        identity={},
        asof=asof,
        include_silent=False,
        live_cards=1,
        cards_counted=1,
        cards_not_yet_created=0,
        roster_fallback_cards=0,
    )
    assert quadrant_inputs(**common).sessions[-1] == late
    assert quadrant_inputs(**common, flagged={S[0]: MIXED}).sessions[-1] == ASOF


# ---------------------------------------------------------------------------------------------------------
# who counts: the link view double-counts a two-link company, the other views count it once
# ---------------------------------------------------------------------------------------------------------


def _two_links():
    ds = days(140)
    w = _wave(140)
    rows = [
        ms(T1, "AAA", S[0], segment="Turbines"),
        ms(T1, "AAA", S[0], segment="Cables"),
        ms(T1, "BBB", S[1], segment="Turbines"),
        ms(T1, "CCC", S[2], segment="Turbines"),
        ms(T1, "DDD", S[3], segment="Cables"),
        ms(T1, "EEE", S[4], segment="Cables"),
    ]
    closes = {
        S[0]: tape(ds, [10.0 * v**3 for v in w]),  # AAA moves most: weighting it twice would show
        S[1]: tape(ds, [10.0 * v for v in w]),
        S[2]: tape(ds, [10.0 for _ in w]),
        S[3]: tape(ds, [10.0 / v for v in w]),
        S[4]: tape(ds, [10.0 * v for v in w]),
    }
    identity = {
        sid: {"business_type": "utilities", "business_supersector": "utilities"} for sid in S
    }
    return ds, rows, closes, identity


def test_the_link_view_counts_a_two_link_company_in_both_links():
    ds, rows, closes, identity = _two_links()
    page = view(build(rows, closes, flat_spy(ds), identity=identity), "link")
    turbines, cables = grp(page, ":Turbines"), grp(page, ":Cables")
    assert [m.ticker for m in turbines.members] == ["AAA", "BBB", "CCC"]
    assert [m.ticker for m in cables.members] == ["AAA", "DDD", "EEE"]
    assert turbines.names == 3 and cables.names == 3 and len(page.groups) == 2


@pytest.mark.parametrize("group_by", ["business_type", "sector", "card"])
def test_the_other_views_count_each_company_once_per_group(group_by):
    ds, rows, closes, identity = _two_links()
    page = view(build(rows, closes, flat_spy(ds), identity=identity), group_by)
    (g,) = page.groups
    assert g.names == 5 and [m.ticker for m in g.members].count("AAA") == 1
    # AAA weighted once: the same line as the roster with its second row removed
    once = view(build(rows[:1] + rows[2:], closes, flat_spy(ds), identity=identity), group_by)
    assert (g.x, g.y, g.tail) == (once.groups[0].x, once.groups[0].y, once.groups[0].tail)


def test_unclassified_and_unsegmented_group_as_their_own():
    ds = days(140)
    rows, closes = trio(_wave(140), ds, segment=None)
    page = view(build(rows, closes, flat_spy(ds)), "business_type")
    assert [g.business_type for g in page.groups] == [None] and page.groups[
        0
    ].label == "Unclassified"
    page = view(build(rows, closes, flat_spy(ds)), "link")
    assert page.groups[0].link is None and page.groups[0].label.startswith("Unsegmented")


def test_context_and_bench_are_left_out_by_default_and_their_own_link_group_when_shown():
    ds = days(140)
    rows, closes = trio(_wave(140), ds)
    rows = rows + [ms(T1, "CTX", S[5], segment=CONTEXT_LABEL)]
    closes = {**closes, S[5]: tape(ds, [10.0] * 140)}
    off = build(rows, closes, flat_spy(ds))
    assert [g.link for g in view(off).groups] == ["Core"]
    assert off.silent_rows_left_out == 1 and off.companies == 3
    on = build(rows, closes, flat_spy(ds), include_silent=True)
    assert sorted(g.link for g in view(on).groups) == [CONTEXT_LABEL, "Core"]
    assert grp(view(on, "card"), "card:").names == 4 and on.include_silent is True


def test_unresolved_rows_are_counted_never_grouped():
    ds = days(140)
    rows, closes = trio(_wave(140), ds)
    inputs = build(rows + [ms(T1, "ZZZ", None)], closes, flat_spy(ds))
    assert inputs.unresolved_rows == 1 and view(inputs).groups[0].names == 3


# ---------------------------------------------------------------------------------------------------------
# the default order
# ---------------------------------------------------------------------------------------------------------


def test_the_default_order_is_quadrant_then_x_highest_first_with_unmeasured_last():
    def g(label, q=None, x=None):
        return QuadrantGroup(key=label, label=label, quadrant=q, x=x)

    groups = [
        g("lag", "lagging", 98.0),
        g("zz too small"),
        g("lead lo", "leading", 101.0),
        g("weak", "weakening", 102.0),
        g("imp hi", "improving", 99.9),
        g("lead hi", "leading", 105.0),
        g("aa no history"),
        g("imp lo", "improving", 95.0),
    ]
    assert [x.label for x in sorted(groups, key=group_sort_key)] == [
        "imp hi",
        "imp lo",
        "lead hi",
        "lead lo",
        "weak",
        "lag",
        "aa no history",
        "zz too small",
    ]


def test_every_grouping_is_built_once_and_served_in_the_default_order():
    ds = days(140)
    rows = [ms(T1, t, S[i], segment="Up") for i, t in enumerate(("AAA", "BBB", "CCC"))]
    rows += [ms(T1, t, S[i], segment="Down") for i, t in zip((3, 4, 5), ("DDD", "EEE", "FFF"))]
    closes = {S[i]: tape(ds, [10.0 * v for v in _wave(140)]) for i in range(3)}
    closes |= {S[i]: tape(ds, [10.0 / v for v in _wave(140)]) for i in (3, 4, 5)}
    inputs = build(rows, closes, flat_spy(ds))
    assert set(inputs.groupings) == {"link", "business_type", "sector", "card"}
    page = view(inputs)
    assert page.groups == tuple(sorted(page.groups, key=group_sort_key))
    assert {g.quadrant for g in page.groups} <= {"leading", "weakening", "lagging", "improving"}


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}
    ds, rows, closes, identity = _two_links()

    def fake_read(conn, asof, known_at, clock, include_silent):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock, include_silent)
        return build(
            rows,
            closes,
            flat_spy(ds),
            identity=identity,
            include_silent=include_silent,
            flagged={S[4]: MIXED},
        )

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_inputs", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_threads_the_view_and_serves_every_grouping_off_one_cached_read(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/rotation-quadrant", params={"asof": "2026-06-01"}).json()
    assert body["cache"] == "computed" and body["group_by"] == "link"
    assert body["include_silent"] is False and len(body["groups"]) == 2
    assert (body["level_sessions"], body["momentum_sessions"]) == (65, 20)
    assert (body["tail_step"], body["tail_points"], body["ago_sessions"]) == (5, 9, 20)
    assert (body["min_priced"], body["priced_sessions"]) == (3, 65)
    asof, known_at, clock, silent = seen["view"]
    assert asof == ASOF and clock == "public" and known_at is not None and silent is False

    cables = next(g for g in body["groups"] if g["link"] == "Cables")
    assert cables["flagged"] == [{"ticker": "EEE", "reason": MIXED}]
    # AAA and DDD priced, EEE flagged: too small, never measured
    assert cables["status"] == "too_small" and cables["priced"] == 2 and cables["tail"] == []
    assert cables["x"] is None and cables["quadrant"] is None
    turbines = next(g for g in body["groups"] if g["link"] == "Turbines")
    assert turbines["status"] == "plotted" and len(turbines["tail"]) == 9

    for group_by in ("business_type", "sector", "card"):
        again = client.get(
            "/dashboards/rotation-quadrant", params={"asof": "2026-06-01", "group_by": group_by}
        ).json()
        assert again["cache"] == "hit" and again["group_by"] == group_by
    assert seen["reads"] == 1

    client.get(
        "/dashboards/rotation-quadrant", params={"asof": "2026-06-01", "include_silent": "true"}
    )
    assert seen["reads"] == 2 and seen["view"][3] is True  # the toggle is part of the key

    client.get("/dashboards/rotation-quadrant", params={"asof": market_today().isoformat()})
    assert seen["view"][1:3] == (None, "record") and seen["reads"] == 3


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(
        "/dashboards/rotation-quadrant", params={"asof": "2026-06-01", "group_by": "card"}
    ).json()
    (g,) = body["groups"]
    assert g["thesis_id"] == str(T1) and g["link"] is None and g["names"] == 5
    assert g["status"] == "plotted" and g["quadrant"] in (
        "leading",
        "weakening",
        "lagging",
        "improving",
    )
    assert [p["session"] for p in g["tail"]][-1] == "2026-06-01" and len(g["tail"]) == 9
    aaa = next(m for m in g["members"] if m["ticker"] == "AAA")
    assert aaa["cards"] == [
        {
            "thesis_id": str(T1),
            "thesis_name": "Card",
            "ticker": "AAA",
            "links": ["Turbines", "Cables"],
        }
    ]
    assert body["groups_plotted"] == 1 and body["flagged_companies"] == 1
    assert body["last_session"] == "2026-06-01" and body["sessions"] == 139


def test_the_router_rejects_an_unknown_grouping(stub_client):
    client, _ = stub_client
    r = client.get(
        "/dashboards/rotation-quadrant", params={"asof": "2026-06-01", "group_by": "mood"}
    )
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


def _security(db, ticker: str, cik: str | None, kind: str = "equity") -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, "2026-01-01"),
        )
    return sid


def test_a_price_published_after_a_past_asof_stays_invisible(db, monkeypatch):
    """150 daily bars ending at the as-of for SPY and three names, each public at its own session. QRA's as-of
    bar is then re-versioned a week after the thaw at double the close: the past view measures the group on
    the ORIGINAL close (the correction was not public by the end of that day), a live view on the correction.
    The data flags ride the same views through the real reader: none at the past as-of; on today's view the
    tapes (they end 2026-06-01) are stopped feeds, so every name is left out and named."""
    n = 150
    ds = days(n)
    spy_id = _security(db, "SPY", None, kind="etf")
    shapes = {
        "QRA": [10.0 + 0.1 * i for i in range(n)],
        "QRB": [20.0 + 0.05 * i + (0.4 if i % 7 == 0 else 0.0) for i in range(n)],
        "QRC": [30.0 - 0.02 * i for i in range(n)],
    }
    sids = {t: _security(db, t, f"000987654{k}") for k, t in enumerate(shapes)}
    for i, d in enumerate(ds):
        at = datetime.combine(d, time.min, tzinfo=timezone.utc)
        _price(db, spy_id, d, 100.0 + 0.03 * i, at)
        for t, closes in shapes.items():
            _price(db, sids[t], d, closes[i], at)
    corrected = shapes["QRA"][-1] * 2
    _price(db, sids["QRA"], ASOF, corrected, _THAW + timedelta(days=7))
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Quadrant fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[
            BasketMember(ticker=t, role="the name", security_id=sids[t], segment="Core")
            for t in shapes
        ],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = client.get("/dashboards/rotation-quadrant", params={"asof": ASOF.isoformat()}).json()
        live_params = {"asof": market_today().isoformat()}
        live = client.get("/dashboards/rotation-quadrant", params=live_params).json()
        # the clock alone: the same live read with the flag read stubbed out
        CACHE.clear()
        monkeypatch.setattr(router_mod, "price_flags", lambda *a, **k: {})
        unflagged = client.get("/dashboards/rotation-quadrant", params=live_params).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    def expected(closes_by_ticker):
        rows = [ms(thesis.id, t, sids[t]) for t in shapes]
        spy = tape(ds, [100.0 + 0.03 * i for i in range(n)])
        inputs = build(rows, {sids[t]: tape(ds, c) for t, c in closes_by_ticker.items()}, spy)
        return view(inputs).groups[0]

    original = expected(shapes)
    fixed = expected({**shapes, "QRA": shapes["QRA"][:-1] + [corrected]})
    assert original.status == "plotted" and (original.x, original.y) != (fixed.x, fixed.y)

    (g,) = [x for x in past["groups"] if x["thesis_id"] == str(thesis.id)]
    assert past["last_session"] == ASOF.isoformat() and past["spy_available"] is True
    assert g["x"] == pytest.approx(original.x, abs=0.011) and g["quadrant"] == original.quadrant
    assert g["y"] == pytest.approx(original.y, abs=0.011)
    assert g["status"] == "plotted" and g["names"] == 3 and g["priced"] == 3
    assert g["flagged"] == [] and past["flagged_companies"] == 0  # no flag at the past as-of
    assert past["cards_counted"] >= 1 and past["roster_fallback_cards"] >= 1

    # today's view: no bar since 2026-06-01 is a stopped feed, a flag on every price cell, so every name is
    # left out: still names, never priced, the group too small to measure (never a made-up reading)
    (lg,) = [x for x in live["groups"] if x["thesis_id"] == str(thesis.id)]
    assert lg["status"] == "too_small" and lg["names"] == 3 and lg["priced"] == 0
    assert sorted(f["ticker"] for f in lg["flagged"]) == ["QRA", "QRB", "QRC"]
    assert {f["reason"] for f in lg["flagged"]} == {"price feed stopped"}

    (ug,) = [x for x in unflagged["groups"] if x["thesis_id"] == str(thesis.id)]
    # the correction, public by today
    assert ug["x"] == pytest.approx(fixed.x, abs=0.011) and ug["y"] == pytest.approx(
        fixed.y, abs=0.011
    )
