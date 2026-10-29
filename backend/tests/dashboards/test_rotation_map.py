"""Rotation Map (``docs/dashboards/rotation-map.md``): the pure grouping on hand-built inputs, the reading of the
display members' own output, the router (pure, with the heavy read stubbed), and one DB-backed past-as-of
read proving a price published after the as-of stays invisible to it."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import rotation_map as router_mod
from dashboards.baskets import CardsAsOf
from dashboards.cache import CACHE
from dashboards.rotation_map import (
    MIN_PRICED,
    NO_TAPE,
    WINDOWS,
    CompanyTape,
    Membership,
    RotationInputs,
    cards_that_existed,
    company_tape,
    recent_cross,
    rotation_map,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo
from signals.display.base import (
    DisplayBasis,
    DisplayEvent,
    DisplayHeadline,
    DisplayMetric,
    DisplaySignal,
)

ASOF = date(2026, 6, 1)
NY = ZoneInfo("America/New_York")

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def tape(r7: float | None = None, **kw) -> CompanyTape:
    """A company's readings; ``r7`` sets the 7d return, ``rets={...}`` any windows."""
    rets = {w: None for w in WINDOWS}
    rets["ret_7d"] = r7
    rets.update(kw.pop("rets", {}))
    return CompanyTape(returns=rets, priced=any(v is not None for v in rets.values()), **kw)


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


def inputs(memberships, tapes, spy=None, **kw) -> RotationInputs:
    base = dict(
        live_cards=len({m.thesis_id for m in memberships}),
        cards_counted=len({m.thesis_id for m in memberships}),
        cards_not_yet_created=0,
        roster_fallback_cards=0,
    )
    base.update(kw)
    return RotationInputs(memberships=tuple(memberships), tapes=tapes, spy=spy, **base)


def grp(result, key_part: str):
    return next(g for g in result.groups if key_part in g.key)


def win(group, key: str = "ret_7d"):
    return next(s for s in group.windows if s.key == key)


T1, T2, T3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
S = [uuid.uuid4() for _ in range(12)]


# ---------------------------------------------------------------------------------------------------------
# medians and x/n
# ---------------------------------------------------------------------------------------------------------


def test_median_and_x_of_n_up_over_the_priced_names_only():
    rows = [ms(T1, "Grid", f"N{i}", S[i], "Turbines") for i in range(5)]
    tapes = {S[0]: tape(4.0), S[1]: tape(-2.0), S[2]: tape(1.0), S[3]: tape(10.0), S[4]: NO_TAPE}
    g = rotation_map(inputs(rows, tapes), {}, asof=ASOF).groups[0]

    s = win(g)
    assert (s.median, s.n, s.up) == (
        2.5,
        4,
        3,
    )  # median of [-2, 1, 4, 10]; the unpriced name sits in n's gap
    assert g.names == 5  # every company counts in the group size, priced or not
    assert (win(g, "ret_1y").median, win(g, "ret_1y").n) == (None, 0)


def test_a_thin_group_shows_no_median_sorts_last_and_is_never_toned():
    rows, tapes = _ladder(10)  # ten groups with a median: one toned on each side
    rows += [ms(T2, "Small", "X", S[10], "Thin"), ms(T2, "Small", "Y", S[11], "Thin")]
    tapes |= {S[10]: tape(99.0), S[11]: tape(98.0)}
    result = rotation_map(inputs(rows, tapes), {}, asof=ASOF)

    thin = grp(result, f"link:{T2}:Thin")
    assert (win(thin).median, win(thin).n, win(thin).up) == (None, 2, 2)
    assert win(thin).tone is None  # 98-99% would top the column, but a "—" is never colored
    assert result.groups[-1] is thin
    assert [win(g).median for g in result.groups if win(g).tone == "pos"] == [9.0]


def test_exactly_min_priced_names_is_a_group_read():
    rows = [ms(T1, "C", f"N{i}", S[i]) for i in range(MIN_PRICED)]
    g = rotation_map(
        inputs(rows, {S[i]: tape(float(i)) for i in range(MIN_PRICED)}), {}, asof=ASOF
    ).groups[0]
    assert win(g).median == 1.0


# ---------------------------------------------------------------------------------------------------------
# color only the unusual
# ---------------------------------------------------------------------------------------------------------


def _ladder(n_groups: int):
    """``n_groups`` single-card link groups of three names each; group i's 7d median is i."""
    rows, tapes = [], {}
    for i in range(n_groups):
        for j in range(3):
            sid = uuid.uuid4()
            rows.append(ms(T1, "Card", f"T{i}{j}", sid, f"L{i:02d}"))
            tapes[sid] = tape(float(i) + (j - 1) * 0.5)  # median = i
    return rows, tapes


def test_top_and_bottom_ten_percent_are_toned_and_the_rest_stay_gray():
    rows, tapes = _ladder(20)
    result = rotation_map(inputs(rows, tapes), {}, asof=ASOF)
    tones = {win(g).median: win(g).tone for g in result.groups}
    assert [m for m, t in tones.items() if t == "pos"] == [19.0, 18.0]  # 20 groups -> 2 each side
    assert sorted(m for m, t in tones.items() if t == "neg") == [0.0, 1.0]
    assert sum(1 for t in tones.values() if t is None) == 16


def test_a_view_of_fewer_than_ten_groups_colors_nothing():
    rows, tapes = _ladder(9)
    result = rotation_map(inputs(rows, tapes), {}, asof=ASOF)
    assert all(s.tone is None for g in result.groups for s in g.windows)


def test_ties_at_the_cut_are_all_toned_and_an_all_equal_column_is_not():
    rows, tapes = _ladder(10)
    result = rotation_map(inputs(rows, tapes), {}, asof=ASOF)
    assert sum(1 for g in result.groups if win(g).tone == "pos") == 1
    flat = {sid: tape(3.0) for sid in tapes}
    result = rotation_map(inputs(rows, flat), {}, asof=ASOF)
    assert all(win(g).tone is None for g in result.groups)


def test_the_tone_is_per_column():
    rows, tapes = _ladder(10)
    # flip the 1d column: the group with the lowest 7d has the highest 1d
    flipped = {
        sid: tape(t.returns["ret_7d"], rets={"ret_1d": -(t.returns["ret_7d"] or 0)})
        for sid, t in tapes.items()
    }
    result = rotation_map(inputs(rows, flipped), {}, asof=ASOF)
    top7 = next(g for g in result.groups if win(g).tone == "pos")
    assert win(top7, "ret_1d").tone == "neg"


# ---------------------------------------------------------------------------------------------------------
# the four views: who counts
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
    tapes = {a: tape(5.0), b: tape(1.0), c: tape(2.0)}
    identity = {
        a: {"business_type": "utilities", "business_supersector": "energy_utilities"},
        b: {"business_type": "utilities", "business_supersector": "energy_utilities"},
        c: {"business_type": "miner", "business_supersector": "materials"},
    }
    return rows, tapes, identity


def test_the_link_view_counts_a_two_link_company_in_both_links():
    rows, tapes, identity = _two_link_company()
    result = rotation_map(inputs(rows, tapes), identity, asof=ASOF, group_by="link")
    turbines = grp(result, f"link:{T1}:Turbines")
    cables = grp(result, f"link:{T1}:Cables")
    assert [m.ticker for m in turbines.members] == ["AAA", "BBB"]
    assert [m.ticker for m in cables.members] == ["AAA"]
    assert (turbines.link, turbines.thesis_name) == ("Turbines", "Grid")
    assert len(result.groups) == 3


def test_the_type_sector_and_card_views_count_each_company_once_per_group():
    rows, tapes, identity = _two_link_company()
    by_type = rotation_map(inputs(rows, tapes), identity, asof=ASOF, group_by="business_type")
    util = grp(by_type, "type:utilities")
    assert util.names == 2 and util.business_type == "utilities"
    aaa = next(m for m in util.members if m.ticker == "AAA")
    # one company, two cards: each card with its links there
    assert [(c.thesis_name, c.links) for c in aaa.cards] == [
        ("Grid", ("Turbines", "Cables")),
        ("Nuclear", ("Fuel",)),
    ]

    by_sector = rotation_map(inputs(rows, tapes), identity, asof=ASOF, group_by="sector")
    assert grp(by_sector, "sector:energy_utilities").names == 2
    assert grp(by_sector, "sector:materials").supersector == "materials"

    by_card = rotation_map(inputs(rows, tapes), identity, asof=ASOF, group_by="card")
    grid = grp(by_card, f"card:{T1}")
    assert grid.names == 2 and grid.thesis_name == "Grid"
    assert [m.cards[0].links for m in grid.members if m.ticker == "AAA"] == [("Turbines", "Cables")]
    assert by_card.companies == 3


def test_unsegmented_and_unclassified():
    rows = [ms(T1, "Grid", "AAA", S[0], None), ms(T1, "Grid", "BBB", S[1], "Turbines")]
    tapes = {S[0]: tape(1.0), S[1]: tape(2.0)}
    links = rotation_map(inputs(rows, tapes), {}, asof=ASOF, group_by="link")
    unseg = next(g for g in links.groups if g.key == f"link:{T1}:")
    assert unseg.link is None and unseg.names == 1
    by_type = rotation_map(
        inputs(rows, tapes), {S[1]: {"business_type": "miner"}}, asof=ASOF, group_by="business_type"
    )
    assert {g.business_type for g in by_type.groups} == {None, "miner"}
    by_sector = rotation_map(inputs(rows, tapes), {}, asof=ASOF, group_by="sector")
    assert [(g.key, g.supersector, g.names) for g in by_sector.groups] == [("sector:", None, 2)]


def test_silent_memberships_are_left_out_by_default_and_their_own_rows_when_shown():
    rows = [
        ms(T1, "Grid", "AAA", S[0], "Turbines"),
        ms(T1, "Grid", "CTX", S[1], CONTEXT_LABEL),
        ms(T1, "Grid", "BNC", S[2], BENCH_LABEL),
        ms(T2, "Coal", "CTX", S[1], "Miners"),  # voting on another card
    ]
    tapes = {S[0]: tape(1.0), S[1]: tape(2.0), S[2]: tape(3.0)}

    off = rotation_map(inputs(rows, tapes), {}, asof=ASOF, group_by="link")
    assert {g.link for g in off.groups} == {"Turbines", "Miners"}
    assert (off.silent_rows_left_out, off.basket_rows, off.companies) == (2, 4, 2)

    on = rotation_map(inputs(rows, tapes), {}, asof=ASOF, group_by="link", include_silent=True)
    assert {g.link for g in on.groups} == {"Turbines", "Miners", CONTEXT_LABEL, BENCH_LABEL}
    assert on.silent_rows_left_out == 0 and on.companies == 3

    card_off = rotation_map(inputs(rows, tapes), {}, asof=ASOF, group_by="card")
    assert grp(card_off, f"card:{T1}").names == 1
    card_on = rotation_map(inputs(rows, tapes), {}, asof=ASOF, group_by="card", include_silent=True)
    assert grp(card_on, f"card:{T1}").names == 3


def test_unresolved_rows_are_counted_never_grouped():
    rows = [
        ms(T1, "Grid", "AAA", S[0]),
        ms(T1, "Grid", "NEWCO", None),
        ms(T1, "Grid", "QUIET", None, CONTEXT_LABEL),
    ]
    off = rotation_map(inputs(rows, {S[0]: tape(1.0)}), {}, asof=ASOF)
    assert (off.unresolved_rows, off.silent_rows_left_out, off.companies) == (1, 1, 1)
    on = rotation_map(inputs(rows, {S[0]: tape(1.0)}), {}, asof=ASOF, include_silent=True)
    assert on.unresolved_rows == 2
    assert [m.ticker for g in on.groups for m in g.members] == ["AAA"]


# ---------------------------------------------------------------------------------------------------------
# the other columns
# ---------------------------------------------------------------------------------------------------------


def test_above_50d_rvol_rs_and_priced_counts():
    rows = [ms(T1, "Grid", f"N{i}", S[i]) for i in range(4)]
    tapes = {
        S[0]: tape(1.0, pct_vs_50d=3.0, rvol20=2.0, rs_leading=True),
        S[1]: tape(1.0, pct_vs_50d=-1.0, rvol20=1.0, rs_leading=False),
        S[2]: tape(1.0, pct_vs_50d=0.0, rvol20=0.5),  # exactly on the line is not above
        S[3]: NO_TAPE,
    }
    result = rotation_map(inputs(rows, tapes), {}, asof=ASOF)
    g = result.groups[0]
    assert (g.above_50d, g.above_50d_n) == (1, 3)
    assert (g.rvol20_median, g.rvol20_n) == (1.0, 3)
    assert (g.rs_leaders, g.rs_n) == (1, 2)
    assert (result.companies, result.priced_companies) == (4, 3)
    name = next(m for m in g.members if m.ticker == "N0")
    assert (name.above_50d, name.rvol20, name.rs_leading) == (True, 2.0, True)
    assert next(m for m in g.members if m.ticker == "N3").above_50d is None


def test_the_cross_window_is_thirty_calendar_days_on_or_before_the_asof():
    on_day_30 = tape(cross="golden", cross_date=ASOF - timedelta(days=30))
    on_day_31 = tape(cross="golden", cross_date=ASOF - timedelta(days=31))
    today = tape(cross="death", cross_date=ASOF)
    after = tape(cross="death", cross_date=ASOF + timedelta(days=1))  # never: a future print
    assert recent_cross(on_day_30, ASOF) == "golden"
    assert recent_cross(on_day_31, ASOF) is None
    assert recent_cross(today, ASOF) == "death"
    assert recent_cross(after, ASOF) is None

    rows = [ms(T1, "Grid", f"N{i}", S[i]) for i in range(4)]
    tapes = {S[0]: on_day_30, S[1]: on_day_31, S[2]: today, S[3]: after}
    g = rotation_map(inputs(rows, tapes), {}, asof=ASOF).groups[0]
    assert (g.golden, g.death) == (1, 1)
    marks = {m.ticker: (m.cross, m.cross_date) for m in g.members}
    assert marks["N0"] == ("golden", ASOF - timedelta(days=30))
    assert marks["N1"] == (None, None)


def test_spy_returns_ride_the_payload_and_set_x_of_n_above_spy():
    rows = [ms(T1, "Grid", f"N{i}", S[i]) for i in range(3)]
    tapes = {S[0]: tape(1.0), S[1]: tape(3.0), S[2]: tape(5.0)}
    spy = {w: None for w in WINDOWS} | {"ret_7d": 2.0}
    result = rotation_map(inputs(rows, tapes, spy=spy), {}, asof=ASOF)
    s = win(result.groups[0])
    # vs SPY = the median minus SPY's own 7d return; the page subtracts, the payload carries both
    assert (s.median, result.spy["ret_7d"], s.median - result.spy["ret_7d"]) == (3.0, 2.0, 1.0)
    assert s.above_spy == 2
    assert win(result.groups[0], "ret_1d").above_spy is None  # no SPY return for that window

    no_spy = rotation_map(inputs(rows, tapes, spy=None), {}, asof=ASOF)
    assert no_spy.spy is None and win(no_spy.groups[0]).above_spy is None


def test_default_sort_is_7d_median_highest_first_with_dashes_last_then_label():
    rows = [
        *[ms(T1, "Card", f"A{i}", uuid.uuid4(), "alpha") for i in range(3)],
        *[ms(T1, "Card", f"B{i}", uuid.uuid4(), "Beta") for i in range(3)],
        *[ms(T1, "Card", f"C{i}", uuid.uuid4(), "gamma") for i in range(3)],
        ms(T1, "Card", "D0", uuid.uuid4(), "delta"),
    ]
    vals = {"alpha": 1.0, "Beta": 5.0, "gamma": 1.0, "delta": 9.0}
    tapes = {m.security_id: tape(vals[m.segment]) for m in rows}
    result = rotation_map(inputs(rows, tapes), {}, asof=ASOF)
    assert [g.link for g in result.groups] == ["Beta", "alpha", "gamma", "delta"]
    # names inside a group: 7d return highest first
    names = [ms(T2, "X", t, sid, "L") for t, sid in (("LOW", S[0]), ("HIGH", S[1]), ("NONE", S[2]))]
    g = rotation_map(
        inputs(names, {S[0]: tape(1.0), S[1]: tape(2.0), S[2]: NO_TAPE}), {}, asof=ASOF
    ).groups[0]
    assert [m.ticker for m in g.members] == ["HIGH", "LOW", "NONE"]


# ---------------------------------------------------------------------------------------------------------
# reading the members' own output
# ---------------------------------------------------------------------------------------------------------


def _sig(kind: str, metrics=(), events=(), headline=None) -> DisplaySignal:
    return DisplaySignal(
        kind=kind,
        label=kind,
        metrics=list(metrics),
        events=list(events),
        headline=headline,
        basis=DisplayBasis(source="fact_price_eod"),
    )


def test_company_tape_reads_the_cockpit_keys():
    t = company_tape(
        {
            "trailing_returns": _sig(
                "trailing_returns",
                [
                    DisplayMetric(key="ret_1d", label="1d", value=0.5, unit="pct"),
                    DisplayMetric(key="ret_7d", label="7d", value=-1.25, unit="pct"),
                    DisplayMetric(key="ret_1y", label="1Y", unit="pct", note="n/a: 100/253 bars"),
                ],
            ),
            "sma_position": _sig(
                "sma_position",
                [DisplayMetric(key="pct_vs_fast", label="vs 50d", value=4.0, unit="pct")],
                [
                    DisplayEvent(key="cross_sma50", label="x", date=date(2026, 5, 30)),
                    DisplayEvent(key="death_cross", label="d", date=date(2026, 5, 20)),
                ],
            ),
            "rvol": _sig("rvol", [DisplayMetric(key="rvol20", label="RVOL20", value=1.8)]),
            "relative_strength": _sig(
                "relative_strength", headline=DisplayHeadline(key="leading", label="Leading")
            ),
        }
    )
    assert t.returns == {
        "ret_1d": 0.5,
        "ret_7d": -1.25,
        "ret_30d": None,
        "ret_90d": None,
        "ret_1y": None,
    }
    assert (t.pct_vs_50d, t.rvol20, t.rs_leading, t.priced) == (4.0, 1.8, True, True)
    assert (t.cross, t.cross_date) == ("death", date(2026, 5, 20))

    inline = company_tape(
        {
            "relative_strength": _sig(
                "relative_strength", headline=DisplayHeadline(key="inline", label="x")
            )
        }
    )
    assert inline.rs_leading is False and inline.priced is False
    assert company_tape({}) == NO_TAPE


# ---------------------------------------------------------------------------------------------------------
# cards created after the as-of
# ---------------------------------------------------------------------------------------------------------


def test_cards_created_after_the_asof_are_left_out():
    old = Thesis(id=uuid.uuid4(), name="Old", narrative="x")
    same_day = Thesis(id=uuid.uuid4(), name="Same day", narrative="x")
    next_day = Thesis(id=uuid.uuid4(), name="Next day", narrative="x")
    unknown = Thesis(id=uuid.uuid4(), name="Unknown", narrative="x")
    created = {
        old.id: datetime(2026, 1, 5, tzinfo=timezone.utc),
        # 23:30 ET on the as-of day is 03:30 UTC the next day: still the as-of's market day
        same_day.id: datetime(2026, 6, 2, 3, 30, tzinfo=timezone.utc),
        next_day.id: datetime(2026, 6, 2, 4, 30, tzinfo=timezone.utc),  # 00:30 ET 06-02
    }
    kept, later = cards_that_existed([old, same_day, next_day, unknown], created, ASOF, NY)
    assert [t.name for t in kept] == ["Old", "Same day", "Unknown"]
    assert later == 1


# ---------------------------------------------------------------------------------------------------------
# a flagged price history (dashboards.baskets.price_flags): left out of every aggregate, named on the page
# ---------------------------------------------------------------------------------------------------------

MIXED = "mixed-scale price history"


def _robots():
    """Card 1's Robots link: RBOT (a +8,900% one-bar scale break read as a return, a golden cross, RS leading,
    loud volume) and three clean names. Card 2's Power link: two clean names, one of them also on card 1.
    """
    rbot, a, b, c, d = S[0], S[1], S[2], S[3], S[4]
    rows = [
        ms(T1, "Robots", "RBOT", rbot, "Robots"),
        ms(T1, "Robots", "AAA", a, "Robots"),
        ms(T1, "Robots", "BBB", b, "Robots"),
        ms(T1, "Robots", "CCC", c, "Robots"),
        ms(T2, "Power", "AAA", a, "Power"),
        ms(T2, "Power", "DDD", d, "Power"),
    ]
    tapes = {
        rbot: tape(
            8900.0,
            rets={"ret_1d": 8900.0, "ret_30d": 8000.0},
            pct_vs_50d=900.0,
            rvol20=9.0,
            cross="golden",
            cross_date=ASOF - timedelta(days=3),
            rs_leading=True,
        ),
        a: tape(2.0, pct_vs_50d=1.0, rvol20=1.0, rs_leading=False),
        b: tape(-1.0, pct_vs_50d=-2.0, rvol20=1.2, rs_leading=False),
        c: tape(3.0, pct_vs_50d=4.0, rvol20=0.8, rs_leading=False),
        d: tape(5.0, pct_vs_50d=2.0, rvol20=1.5, rs_leading=True),
    }
    return rows, tapes


def _stats(g):
    """Everything a group row aggregates (its names and flagged counts aside)."""
    return (
        [(s.key, s.median, s.n, s.up, s.above_spy) for s in g.windows],
        (g.above_50d, g.above_50d_n, g.rvol20_median, g.rvol20_n),
        (g.golden, g.death, g.rs_leaders, g.rs_n),
    )


@pytest.mark.parametrize("group_by", ["link", "card", "business_type"])
def test_a_flagged_company_is_left_out_of_every_aggregate_and_named_on_the_page(group_by):
    """RBOT is flagged: every median, "x/n up", "above SPY", above-50d share, RVOL median, cross and RS count of
    its group reads exactly as without it; it stays listed (no reading, its flag), counts in the group's names
    and in ``flagged``, never in ``priced_companies``, and the page names it with the flag's label.
    """
    rows, tapes = _robots()
    spy = {w: 0.5 for w in WINDOWS}
    # card 1's four names share a business type; DDD (card 2 only) is another
    identity = {sid: {"business_type": "robotics"} for sid in S[:4]}
    identity[S[4]] = {"business_type": "utilities"}
    without = rotation_map(
        inputs([m for m in rows if m.ticker != "RBOT"], tapes, spy=spy),
        identity,
        asof=ASOF,
        group_by=group_by,
    )
    result = rotation_map(
        inputs(rows, tapes, spy=spy, flagged={S[0]: MIXED}),
        identity,
        asof=ASOF,
        group_by=group_by,
    )
    g = next(g for g in result.groups if any(m.ticker == "RBOT" for m in g.members))
    alone = next(x for x in without.groups if x.key == g.key)
    assert _stats(g) == _stats(alone)
    assert win(g).median == 2.0 and (win(g).n, win(g).up) == (3, 2)  # median of [-1, 2, 3]
    assert (g.golden, g.rs_leaders) == (0, 0)  # RBOT's cross and RS never count
    assert (g.names, g.flagged) == (alone.names + 1, 1)

    rbot = next(m for m in g.members if m.ticker == "RBOT")
    assert rbot.flag == MIXED and all(v is None for v in rbot.returns.values())
    assert (rbot.above_50d, rbot.rvol20, rbot.cross, rbot.rs_leading) == (None, None, None, None)
    assert g.members[-1] is rbot  # no reading sorts last
    assert all(m.flag is None for m in g.members if m.ticker != "RBOT")

    assert [(f.ticker, f.reason) for f in result.flagged] == [("RBOT", MIXED)]
    assert (result.companies, result.priced_companies) == (5, 4)


def test_an_unflagged_group_is_unaffected_by_a_flag_elsewhere():
    """Card 2's Power link holds no flagged name: it reads exactly as with no flag at all."""
    rows, tapes = _robots()
    plain = rotation_map(inputs(rows, tapes), {}, asof=ASOF)
    flagged = rotation_map(inputs(rows, tapes, flagged={S[0]: MIXED}), {}, asof=ASOF)
    assert grp(flagged, f"link:{T2}:Power") == grp(plain, f"link:{T2}:Power")
    assert grp(flagged, f"link:{T2}:Power").flagged == 0
    assert plain.flagged == () and grp(plain, f"link:{T1}:Robots").flagged == 0


def test_a_flagged_company_out_of_view_is_not_named():
    """A flag on a Context name stays off the page while Context is left out, and is named once shown."""
    rows = [ms(T1, "Grid", "AAA", S[0]), ms(T1, "Grid", "CTX", S[1], CONTEXT_LABEL)]
    tapes = {S[0]: tape(1.0), S[1]: tape(2.0)}
    off = rotation_map(inputs(rows, tapes, flagged={S[1]: MIXED}), {}, asof=ASOF)
    assert off.flagged == () and off.priced_companies == 1
    on = rotation_map(
        inputs(rows, tapes, flagged={S[1]: MIXED}), {}, asof=ASOF, include_silent=True
    )
    assert [(f.ticker, f.reason) for f in on.flagged] == [("CTX", MIXED)]
    assert (on.companies, on.priced_companies) == (2, 1)


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}
    rows, tapes, identity = _two_link_company()

    def fake_read(conn, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock)
        return inputs(rows, tapes, spy={w: 1.0 for w in WINDOWS})

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
    body = client.get("/dashboards/rotation-map", params={"asof": "2026-06-01"}).json()
    assert body["cache"] == "computed" and body["group_by"] == "link"
    assert body["include_silent"] is False and len(body["groups"]) == 3
    assert body["min_priced"] == MIN_PRICED and body["cross_days"] == 30
    assert body["spy"]["ret_7d"] == 1.0
    asof, known_at, clock = seen["view"]
    assert asof == date(2026, 6, 1) and clock == "public" and known_at is not None

    for group_by in ("business_type", "sector", "card"):
        again = client.get(
            "/dashboards/rotation-map",
            params={"asof": "2026-06-01", "group_by": group_by, "include_silent": "true"},
        ).json()
        assert again["cache"] == "hit" and again["group_by"] == group_by
    assert seen["reads"] == 1
    assert seen["identity_ids"] == {S[0], S[1], S[2]}

    today = market_today().isoformat()
    client.get("/dashboards/rotation-map", params={"asof": today})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 2


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(
        "/dashboards/rotation-map", params={"asof": "2026-06-01", "group_by": "business_type"}
    ).json()
    util = next(g for g in body["groups"] if g["business_type"] == "utilities")
    assert util["names"] == 2 and util["link"] is None
    assert [w["key"] for w in util["windows"]] == list(WINDOWS)
    aaa = next(m for m in util["members"] if m["ticker"] == "AAA")
    assert aaa["returns"]["ret_7d"] == 5.0
    assert [c["links"] for c in aaa["cards"]] == [["Turbines", "Cables"], ["Fuel"]]


def test_the_router_rejects_an_unknown_grouping(stub_client):
    client, _ = stub_client
    r = client.get("/dashboards/rotation-map", params={"asof": "2026-06-01", "group_by": "mood"})
    assert r.status_code == 422


def test_the_router_reads_the_price_flags_inside_the_cached_read(monkeypatch):
    """``read_inputs`` itself reads the flags (the real one, its DB reads stubbed): once per cached read, for
    every resolved company, on the request's one ``serve_view`` pair; every grouping after it is a hit.
    """
    rows, tapes = _robots()
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
    monkeypatch.setattr(router_mod, "_tapes", lambda conn, sids, asof, known_at, clock: tapes)
    monkeypatch.setattr(router_mod, "_spy", lambda conn, asof, known_at, clock: None)
    monkeypatch.setattr(router_mod, "price_flags", fake_flags)
    monkeypatch.setattr(router_mod.master, "identity_for", lambda conn, ids, **kw: {})
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    try:
        client = TestClient(app)
        body = client.get("/dashboards/rotation-map", params={"asof": "2026-06-01"}).json()
        again = client.get(
            "/dashboards/rotation-map", params={"asof": "2026-06-01", "group_by": "card"}
        ).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    ids, asof, known_at, clock = seen["flags_view"]
    assert seen["flag_reads"] == 1 and again["cache"] == "hit"
    assert ids == set(S[:5]) and asof == ASOF and clock == "public" and known_at is not None
    assert body["flagged"] == [{"security_id": str(S[0]), "ticker": "RBOT", "reason": MIXED}]
    robots = next(g for g in body["groups"] if g["link"] == "Robots")
    assert robots["flagged"] == 1 and win_out(robots)["median"] == 2.0
    rbot = next(m for m in robots["members"] if m["ticker"] == "RBOT")
    assert rbot["flag"] == MIXED and rbot["returns"]["ret_7d"] is None
    assert again["flagged"] == body["flagged"]


def win_out(group: dict, key: str = "ret_7d") -> dict:
    return next(w for w in group["windows"] if w["key"] == key)


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
    """220 daily bars ending at the as-of, each public at its own session. The as-of's own bar is then
    re-versioned a week after the thaw at double the close: the past view still reads the ORIGINAL close
    (the correction was not public by the end of that day), a live view reads the correction. The data flags
    ride the same views through the real reader: none at the past as-of; on today's view the tape (it ends
    2026-06-01) is a stopped feed, so the name is left out of every aggregate and named."""
    sid = _security(db, "ROTA")
    start = ASOF - timedelta(days=219)
    for i in range(220):
        d = start + timedelta(days=i)
        _price(db, sid, d, 10.0 + i * 0.1, datetime.combine(d, time.min, tzinfo=timezone.utc))
    _price(db, sid, ASOF, 63.8, _THAW + timedelta(days=7))  # the as-of bar (31.9), corrected later
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Rotation fixture",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[BasketMember(ticker="ROTA", role="the name", security_id=sid, segment="Core")],
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
    db.commit()

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = client.get("/dashboards/rotation-map", params={"asof": ASOF.isoformat()}).json()
        live = client.get(
            "/dashboards/rotation-map", params={"asof": market_today().isoformat()}
        ).json()
        # the clock alone: the same live read with the flag read stubbed out
        CACHE.clear()
        monkeypatch.setattr(router_mod, "price_flags", lambda *a, **k: {})
        unflagged = client.get(
            "/dashboards/rotation-map", params={"asof": market_today().isoformat()}
        ).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    (group,) = past["groups"]
    (name,) = group["members"]
    assert name["returns"]["ret_1d"] == round((31.9 / 31.8 - 1) * 100, 2)  # not the 63.8 correction
    assert name["ticker"] == "ROTA" and group["link"] == "Core"
    # the card existed on the as-of; no roster snapshot then, so today's roster stood in (and is counted)
    assert past["cards_counted"] == 1 and past["roster_fallback_cards"] == 1
    assert name["flag"] is None and past["flagged"] == [] and group["flagged"] == 0

    # today's view: no bar since 2026-06-01 is a stopped feed (``tape_stale_days``), a flag on every price
    # cell, so the name is left out of every aggregate: listed with its flag and no reading, and named
    (lgroup,) = live["groups"]
    (lname,) = lgroup["members"]
    assert lname["flag"] == "price feed stopped" and lname["returns"]["ret_1d"] is None
    assert (lgroup["names"], lgroup["flagged"], live["priced_companies"]) == (1, 1, 0)
    assert all(w["n"] == 0 for w in lgroup["windows"])
    assert live["flagged"] == [
        {"security_id": str(sid), "ticker": "ROTA", "reason": "price feed stopped"}
    ]
    (uname,) = unflagged["groups"][0]["members"]
    assert uname["returns"]["ret_1d"] == round((63.8 / 31.8 - 1) * 100, 2)
