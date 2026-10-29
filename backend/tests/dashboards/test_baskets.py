"""The shared basket read (``dashboards/baskets.py``): the counted cards and their rosters at an as-of, who counts,
the four groupings, the per-group company fold and the chunked point-in-time read. Pure: the repository reads
and the point-in-time view are stubbed. The Rotation Map's and the Cross Radar's own tests cover these rules
through each page; this pins the shared pieces once."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import pytest

from dashboards import baskets
from dashboards.baskets import (
    Membership,
    buckets,
    cards_as_of,
    companies_in,
    counted_rows,
    display_members,
    display_ticker,
    group_of,
    link_label,
    memberships_of,
    per_company,
    resolved_ids,
)
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis

ASOF = date(2026, 6, 1)
T1, T2 = uuid.uuid4(), uuid.uuid4()
A, B = uuid.uuid4(), uuid.uuid4()


def ms(tid, card, ticker, sid, segment="Core") -> Membership:
    return Membership(
        thesis_id=tid,
        thesis_name=card,
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
    )


def test_memberships_mark_the_silent_rows_and_keep_every_row():
    t = Thesis(
        id=T1,
        name="Grid",
        narrative="x",
        segments=[Segment(label="Turbines")],
        basket=[
            BasketMember(ticker="AAA", role="r", security_id=A, segment="Turbines"),
            BasketMember(ticker="CTX", role="r", security_id=B, segment=CONTEXT_LABEL),
            BasketMember(ticker="NEWCO", role="r", segment=None),
        ],
    )
    rows = memberships_of([t])
    assert [(m.ticker, m.segment, m.silent) for m in rows] == [
        ("AAA", "Turbines", False),
        ("CTX", CONTEXT_LABEL, True),
        ("NEWCO", None, False),
    ]
    assert resolved_ids(rows) == sorted([A, B], key=str)


def test_counted_rows_filters_silent_and_sets_unresolved_aside():
    rows = [
        ms(T1, "Grid", "AAA", A),
        ms(T1, "Grid", "CTX", B, CONTEXT_LABEL),
        ms(T1, "Grid", "NEWCO", None),
        ms(T1, "Grid", "QUIET", None, BENCH_LABEL),
    ]
    off = counted_rows(rows, include_silent=False)
    assert (
        [m.ticker for m in off.rows],
        off.basket_rows,
        off.silent_rows_left_out,
        off.unresolved_rows,
    ) == (
        ["AAA"],
        4,
        2,
        1,
    )
    on = counted_rows(rows, include_silent=True)
    assert ([m.ticker for m in on.rows], on.silent_rows_left_out, on.unresolved_rows) == (
        ["AAA", "CTX"],
        0,
        2,
    )


def test_the_four_groupings_key_label_and_meta():
    m = ms(T1, "Grid", "AAA", A, "Turbines")
    ident = {A: {"business_type": "utilities", "business_supersector": "energy_utilities"}}
    assert group_of(m, "link", ident) == (
        f"link:{T1}:Turbines",
        "Turbines · Grid",
        {"link": "Turbines", "thesis_id": T1, "thesis_name": "Grid"},
    )
    assert group_of(m, "card", ident) == (
        f"card:{T1}",
        "Grid",
        {"thesis_id": T1, "thesis_name": "Grid"},
    )
    assert group_of(m, "business_type", ident) == (
        "type:utilities",
        "utilities",
        {"business_type": "utilities"},
    )
    assert group_of(m, "sector", ident) == (
        "sector:energy_utilities",
        "energy_utilities",
        {"supersector": "energy_utilities"},
    )
    unseg = ms(T1, "Grid", "BBB", B, None)
    assert group_of(unseg, "link", {})[:2] == (f"link:{T1}:", "Unsegmented · Grid")
    assert group_of(unseg, "business_type", {})[:2] == ("type:", "Unclassified")
    assert group_of(unseg, "sector", {})[:2] == ("sector:", "Unclassified")
    assert (link_label(None), link_label("Fuel")) == ("Unsegmented", "Fuel")


def test_the_link_view_counts_a_two_link_company_in_both_and_the_others_fold_it_once():
    rows = [
        ms(T1, "Grid", "AAA", A, "Turbines"),
        ms(T1, "Grid", "AAA", A, "Cables"),
        ms(T2, "Nuclear", "AAA.X", A, "Fuel"),
        ms(T1, "Grid", "BBB", B, "Turbines"),
    ]
    by_link = buckets(rows, "link", {})
    assert list(by_link) == [f"link:{T1}:Turbines", f"link:{T1}:Cables", f"link:{T2}:Fuel"]
    assert [m.ticker for m in by_link[f"link:{T1}:Turbines"][0]] == ["AAA", "BBB"]

    card_rows, _, _ = buckets(rows, "card", {})[f"card:{T1}"]
    folded = companies_in(card_rows)
    assert [sid for sid, _ in folded] == [A, B]
    assert [(c.thesis_name, c.links) for c in folded[0][1]] == [("Grid", ("Turbines", "Cables"))]

    sector_rows, _, _ = buckets(rows, "sector", {})["sector:"]
    aaa, _bbb = companies_in(sector_rows)
    assert [(c.thesis_name, c.ticker, c.links) for c in aaa[1]] == [
        ("Grid", "AAA", ("Turbines", "Cables")),
        ("Nuclear", "AAA.X", ("Fuel",)),
    ]
    # the shown ticker: the one most of its cards use, ties by ticker
    assert display_ticker(aaa[1]) == "AAA"


@pytest.fixture
def repo(monkeypatch):
    old = Thesis(id=T1, name="Old", narrative="x")
    new = Thesis(id=T2, name="New", narrative="x")
    calls: dict = {"snapshots": [], "get_asof": []}
    monkeypatch.setattr(baskets.thesis_repo, "list_all", lambda conn: [old, new])
    monkeypatch.setattr(
        baskets.thesis_repo,
        "created_at_for",
        lambda conn, ids: {
            T1: datetime(2026, 1, 5, tzinfo=timezone.utc),
            T2: datetime(2026, 7, 1, tzinfo=timezone.utc),
        },
    )

    def snapshot_exists(conn, tid, known_at):
        calls["snapshots"].append(tid)
        return False

    def get_asof(conn, tid, known_at):
        calls["get_asof"].append((tid, known_at))
        return old

    monkeypatch.setattr(baskets.thesis_repo, "snapshot_exists_asof", snapshot_exists)
    monkeypatch.setattr(baskets.thesis_repo, "get_asof", get_asof)
    return calls


def test_cards_as_of_leaves_out_later_cards_and_counts_a_roster_fallback(repo):
    known_at = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc)
    got = cards_as_of(object(), ASOF, known_at)
    assert (
        got.live_count,
        [t.name for t in got.cards],
        got.not_yet_created,
        got.roster_fallback,
    ) == (
        2,
        ["Old"],
        1,
        1,
    )
    assert repo["get_asof"] == [(T1, known_at)]
    # the live view: today's roster, no snapshot read
    repo["get_asof"].clear()
    live = cards_as_of(object(), date(2026, 10, 1), None)
    assert ([t.name for t in live.cards], live.roster_fallback) == (["Old", "New"], 0)
    assert repo["get_asof"] == [] and repo["snapshots"] == [T1]


def test_per_company_reads_each_company_once_through_one_view_per_chunk(monkeypatch):
    monkeypatch.setattr(baskets, "CHUNK", 2)
    sids = [uuid.uuid4() for _ in range(5)]
    known_at = datetime(2026, 6, 2, 3, 0, tzinfo=timezone.utc)
    seen: list = []

    def read(pit, sid):
        seen.append((id(pit), sid, pit._basket, pit.asof, pit.known_at, pit.clock))
        return sid.hex

    out = per_company(object(), sids, ASOF, known_at, "public", read)
    assert out == {sid: sid.hex for sid in sids}
    assert [s for _, s, *_ in seen] == sids
    views = {}
    for pit_id, sid, basket, *view in seen:
        views.setdefault(pit_id, (basket, set()))[1].add(sid)
        assert view == [ASOF, known_at, "public"]
    assert [b == frozenset(got) for b, got in views.values()] == [True, True, True]  # 2 + 2 + 1


def test_display_members_fail_loud_on_a_renamed_member():
    got = display_members(("sma_position", "relative_strength"), "Cross Radar")
    assert list(got) == ["sma_position", "relative_strength"]
    with pytest.raises(RuntimeError, match="Cross Radar: display members not registered"):
        display_members(("sma_position", "no_such_member"), "Cross Radar")
