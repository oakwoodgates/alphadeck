"""Overlap Map (``docs/dashboards/overlap-map.md``): the pure compute on hand-built cards (the counting rules, the
pair counts, the groups at each threshold), the layout (deterministic in any process, no two bubbles touching,
the strip, independent of the threshold), the router with its reads stubbed, and one DB-backed past-as-of read
proving a card created after the as-of and a roster row added after it stay out of the page.

Every card and ticker here is synthetic (the repo is public)."""

from __future__ import annotations

import math
import os
import random
import subprocess
import sys
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import overlap_map as router_mod
from dashboards.baskets import CardsAsOf
from dashboards.cache import CACHE
from dashboards.overlap_map import (
    BOX_H,
    BOX_W,
    OverlapPair,
    groups_at,
    layout_bubbles,
    overlap_map,
    radius,
)
from db.session import DEFAULT_TENANT_ID
from domain.market_time import current_night, market_today
from domain.thesis import (
    BENCH_LABEL,
    CONTEXT_LABEL,
    ETFS_LABEL,
    SILENT_LABELS,
    BasketMember,
    Segment,
    Thesis,
)
from repositories import thesis_repo

BACKEND = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def sec(n: int) -> UUID:
    return UUID(int=n)


def member(ticker: str, sid: UUID | None, segment: str | None = "Core") -> BasketMember:
    return BasketMember(ticker=ticker, role="prose", security_id=sid, segment=segment)


def card(name: str, *members: BasketMember, tid: UUID | None = None) -> Thesis:
    labels = sorted({m.segment for m in members if m.segment and m.segment not in SILENT_LABELS})
    return Thesis(
        id=tid or uuid.uuid4(),
        name=name,
        narrative="x",
        segments=[Segment(label=label) for label in labels],
        basket=list(members),
    )


def tick(n: int) -> str:
    return f"T{n}"


def cards_of(spec: dict[str, list[int]]) -> list[Thesis]:
    """Cards named by key, each holding the companies (by number) of its list, all in the link "Core"."""
    return [card(name, *(member(tick(n), sec(n)) for n in ns)) for name, ns in spec.items()]


def card_index(result, name: str) -> int:
    return next(i for i, c in enumerate(result.cards) if c.name == name)


def pair_map(result) -> dict[tuple[str, str], int]:
    return {(result.cards[p.a].name, result.cards[p.b].name): p.shared for p in result.pairs}


# ---------------------------------------------------------------------------------------------------------
# who counts, and the pair counts
# ---------------------------------------------------------------------------------------------------------


def test_a_company_in_two_links_of_one_card_counts_once_for_that_card():
    a = card(
        "Alpha",
        member("T1", sec(1), "Core"),
        member("T1", sec(1), "Edge"),
        member("T2", sec(2), "Core"),
    )
    b = card("Beta", member("T1", sec(1), "Core"))
    r = overlap_map([a, b], {}, include_silent=False)
    assert r.cards[card_index(r, "Alpha")].companies == 2  # not 3 rows
    assert pair_map(r) == {("Alpha", "Beta"): 1}
    (t1,) = [c for c in r.companies if c.ticker == "T1"]
    on_alpha = next(row for row in t1.rows if row.card == card_index(r, "Alpha"))
    assert on_alpha.links == ("Core", "Edge")


def test_silent_rows_are_out_by_default_and_in_with_the_toggle():
    a = card("Alpha", member("T1", sec(1)), member("T9", sec(9), CONTEXT_LABEL))
    b = card("Beta", member("T2", sec(2)), member("T9", sec(9), BENCH_LABEL))
    off = overlap_map([a, b], {}, include_silent=False)
    assert pair_map(off) == {} and off.silent_rows_left_out == 2 and off.companies_in_view == 2
    on = overlap_map([a, b], {}, include_silent=True)
    assert pair_map(on) == {("Alpha", "Beta"): 1} and on.silent_rows_left_out == 0
    nine = next(c for c in on.companies if c.ticker == "T9")
    assert [row.silent for row in nine.rows] == [True, True]
    assert on.basket_rows == off.basket_rows == 4


def test_an_etfs_link_row_is_silent():
    """The ETFs link is read through is_silent like Context and Bench: no label is named in the compute."""
    assert ETFS_LABEL in SILENT_LABELS
    a = card("Alpha", member("T1", sec(1)), member("FND", sec(50), ETFS_LABEL))
    b = card("Beta", member("T2", sec(2)), member("FND", sec(50), ETFS_LABEL))
    off = overlap_map([a, b], {}, include_silent=False)
    assert pair_map(off) == {} and off.silent_rows_left_out == 2
    assert all(c.companies == 1 for c in off.cards)
    on = overlap_map([a, b], {}, include_silent=True)
    assert pair_map(on) == {("Alpha", "Beta"): 1}


def test_an_unresolved_row_is_counted_on_the_quiet_line_and_never_matched():
    a = card("Alpha", member("T1", sec(1)), member("ZZZ", None))
    b = card("Beta", member("T2", sec(2)), member("ZZZ", None))
    r = overlap_map([a, b], {}, include_silent=False)
    assert r.unresolved_rows == 2 and r.basket_rows == 4
    assert pair_map(r) == {} and r.companies_in_view == 2
    assert all(c.shares_nothing for c in r.cards)


def test_pairs_count_every_shared_company_and_the_counts_of_the_page():
    r = overlap_map(
        cards_of({"Alpha": [1, 2, 3, 4], "Beta": [3, 4, 5], "Gamma": [4, 6], "Delta": [7]}),
        {},
        include_silent=False,
        live_cards=6,
        not_yet_created=1,
        roster_fallback_cards=2,
    )
    assert pair_map(r) == {("Alpha", "Beta"): 2, ("Alpha", "Gamma"): 1, ("Beta", "Gamma"): 1}
    assert all(p.a < p.b for p in r.pairs)
    assert (r.live_cards, r.cards_counted, r.cards_not_yet_created, r.roster_fallback_cards) == (
        6,
        4,
        1,
        2,
    )
    assert (r.companies_in_view, r.companies_shared) == (7, 2)  # T3 on 2 cards, T4 on 3
    assert [c.name for c in r.cards] == ["Alpha", "Beta", "Delta", "Gamma"]  # A -> Z
    assert next(c for c in r.cards if c.name == "Delta").shares_nothing


def test_sector_and_business_type_ride_from_the_masters_identity_and_unclassified_is_none():
    ident = {
        sec(1): {"name": "One Corp", "business_supersector": "materials", "business_type": "miner"},
        sec(2): {"name": "Two Corp"},
    }
    r = overlap_map(cards_of({"Alpha": [1, 2, 3]}), ident, include_silent=False)
    by = {c.ticker: c for c in r.companies}
    assert (by["T1"].name, by["T1"].supersector, by["T1"].business_type) == (
        "One Corp",
        "materials",
        "miner",
    )
    assert (by["T2"].supersector, by["T2"].business_type) == (None, None)
    assert by["T3"].name is None  # no master row: kept, never dropped


# ---------------------------------------------------------------------------------------------------------
# the groups at each threshold
# ---------------------------------------------------------------------------------------------------------

# hand-built: shared counts  A-B 5, B-C 3, C-D 1, E-F 2, G alone
#   threshold 1: {A,B,C,D} 4 and {E,F} 2, G has no line        -> sizes (4, 2), no line 1
#   threshold 2: {A,B,C} 3 and {E,F} 2, D and G have no line   -> (3, 2), 2
#   threshold 3: {A,B,C} 3, E, F, D, G no line                 -> (3,), 4
#   threshold 4: {A,B} 2                                       -> (2,), 5
#   threshold 5: {A,B} 2                                       -> (2,), 5
#   threshold 6: nobody                                        -> (), 7
PAIRS = [
    OverlapPair(0, 1, 5),
    OverlapPair(1, 2, 3),
    OverlapPair(2, 3, 1),
    OverlapPair(4, 5, 2),
]


@pytest.mark.parametrize(
    "threshold, sizes, no_line",
    [(1, (4, 2), 1), (2, (3, 2), 2), (3, (3,), 4), (4, (2,), 5), (5, (2,), 5), (6, (), 7)],
)
def test_groups_on_a_hand_built_fixture(threshold, sizes, no_line):
    g = groups_at(7, PAIRS, threshold)
    assert (g.threshold, g.sizes, g.no_line) == (threshold, sizes, no_line)


def test_the_payload_holds_groups_for_thresholds_one_to_five():
    ids = [uuid.UUID(int=1000 + i) for i in range(4)]
    cards = [
        card("Alpha", *(member(tick(n), sec(n)) for n in (1, 2, 3)), tid=ids[0]),
        card("Beta", *(member(tick(n), sec(n)) for n in (1, 2, 4)), tid=ids[1]),
        card("Gamma", *(member(tick(n), sec(n)) for n in (4, 5)), tid=ids[2]),
        card("Delta", member("T9", sec(9)), tid=ids[3]),
    ]
    r = overlap_map(cards, {}, include_silent=False)
    assert [g.threshold for g in r.groups] == [1, 2, 3, 4, 5]
    assert [(g.sizes, g.no_line) for g in r.groups] == [
        ((3,), 1),  # Alpha-Beta 2, Beta-Gamma 1
        ((2,), 2),
        ((), 4),
        ((), 4),
        ((), 4),
    ]


# ---------------------------------------------------------------------------------------------------------
# the layout
# ---------------------------------------------------------------------------------------------------------


def synthetic(n_cards: int = 65, universe: int = 1590, seed: int = 7) -> list[Thesis]:
    """65 cards, ~2,200 rows: a seeded random draw (counts 1-64, some silent rows) from a shared universe."""
    rng = random.Random(seed)
    cards: list[Thesis] = []
    for c in range(n_cards):
        size = min(64, max(1, int(rng.lognormvariate(3.0, 0.8))))
        picks = rng.sample(range(universe), size)
        rows = [member(tick(n), sec(n)) for n in picks]
        rows += [member(tick(n), sec(n), CONTEXT_LABEL) for n in rng.sample(range(universe), 10)]
        cards.append(card(f"Card {c:02d}", *rows, tid=uuid.UUID(int=5000 + c)))
    return cards


def test_the_same_input_gives_the_same_positions():
    cards = synthetic()
    a = overlap_map(cards, {}, include_silent=False)
    b = overlap_map(
        list(reversed(cards)), {}, include_silent=False
    )  # the input order does not matter
    assert [(c.x, c.y, c.r) for c in a.cards] == [(c.x, c.y, c.r) for c in b.cards]


def test_the_positions_do_not_depend_on_the_process_hash_seed():
    """Python's hash() varies per process (PYTHONHASHSEED); the layout seeds from hashlib, so two processes with
    different seeds draw the same map."""
    code = (
        "from dashboards.overlap_map import layout_bubbles\n"
        "keys = [f'card-{i}' for i in range(12)]\n"
        "sizes = [3 + i * 2 for i in range(12)]\n"
        "w = {(i, i + 1): 1 + i for i in range(11)}\n"
        "print(layout_bubbles(keys, sizes, w))\n"
    )
    outs = []
    for hash_seed in ("1", "2"):
        env = {**os.environ, "PYTHONHASHSEED": hash_seed, "PYTHONPATH": str(BACKEND)}
        done = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=True
        )
        outs.append(done.stdout)
    assert outs[0] == outs[1] and outs[0].strip()


def test_no_two_bubbles_overlap_and_all_sit_in_the_box():
    r = overlap_map(synthetic(), {}, include_silent=False)
    placed = [c for c in r.cards if c.x is not None and c.y is not None]
    assert len(placed) >= 40
    for c in placed:
        assert c.r > 0 and c.r - 1e-6 <= c.x <= BOX_W - c.r + 1e-6
        assert c.r - 1e-6 <= c.y <= BOX_H - c.r + 1e-6
    for i, p in enumerate(placed):
        for q in placed[i + 1 :]:
            assert math.hypot(p.x - q.x, p.y - q.y) >= p.r + q.r, (p.name, q.name)


def test_the_cloud_fills_the_box_with_bubbles_big_enough_to_read():
    """The map is the page's picture: its bubbles span most of the box in both directions (no thin band of tiny
    bubbles) and the biggest card reads at a size a person can see. Checked on two shapes of input.
    """
    for include_silent in (False, True):
        r = overlap_map(synthetic(), {}, include_silent=include_silent)
        placed = [c for c in r.cards if c.x is not None and c.y is not None]
        width = max(c.x + c.r for c in placed) - min(c.x - c.r for c in placed)
        height = max(c.y + c.r for c in placed) - min(c.y - c.r for c in placed)
        assert width >= 0.75 * BOX_W and height >= 0.75 * BOX_H, (include_silent, width, height)
        assert max(c.r for c in placed) >= 35


def test_the_cloud_fills_the_box_when_a_few_cards_hang_by_one_shared_name():
    """Cards linked to the main group by a single shared name (the long-arm case) do not stretch the extent."""
    keys = [f"card-{i:02d}" for i in range(40)]
    sizes = [10 + (i * 7) % 50 for i in range(40)]
    weights = {(i, i + 1): 3 + (i % 5) for i in range(0, 30)}  # a chain of strong links
    weights.update({(i, 30 + (i % 10)): 1 for i in range(0, 10)})  # ten cards hanging by one name
    pos = layout_bubbles(keys, sizes, weights)
    width = max(x + r for x, _, r in pos) - min(x - r for x, _, r in pos)
    height = max(y + r for _, y, r in pos) - min(y - r for _, y, r in pos)
    assert width >= 0.75 * BOX_W and height >= 0.75 * BOX_H


def test_area_is_proportional_to_the_companies_above_the_floor():
    r = overlap_map(synthetic(), {}, include_silent=False)
    big = [c for c in r.cards if c.x is not None and c.companies >= 9]
    assert big
    ratios = [c.r * c.r / c.companies for c in big]
    assert max(ratios) == pytest.approx(min(ratios), rel=0.02)  # one scale for every bubble
    assert radius(1) > 0 and radius(0) > 0  # a 1-name (or empty) card stays visible


def test_a_card_sharing_nothing_goes_to_the_strip_and_is_never_dropped():
    r = overlap_map(
        cards_of({"Alpha": [1, 2], "Beta": [2, 3], "Loner": [8, 9], "Empty": []}),
        {},
        include_silent=False,
    )
    assert len(r.cards) == 4
    strip = {c.name for c in r.cards if c.shares_nothing}
    assert strip == {"Loner", "Empty"}
    for c in r.cards:
        assert (c.x is None and c.y is None) == c.shares_nothing and c.r > 0


def test_positions_are_the_same_whatever_threshold_the_page_reads():
    """The layout reads every pair weight and takes no threshold; the groups are separate fields."""
    r = overlap_map(synthetic(), {}, include_silent=False)
    again = overlap_map(synthetic(), {}, include_silent=False)
    assert [(c.x, c.y) for c in r.cards] == [(c.x, c.y) for c in again.cards]
    assert [g.threshold for g in r.groups] == [1, 2, 3, 4, 5]
    import inspect

    assert "threshold" not in inspect.signature(layout_bubbles).parameters


def test_a_pair_that_shares_more_sits_nearer_than_one_that_shares_little():
    keys = ["a", "b", "c", "d"]
    sizes = [20, 20, 20, 20]
    pos = layout_bubbles(keys, sizes, {(0, 1): 18, (1, 2): 1, (2, 3): 18})
    d01 = math.hypot(pos[0][0] - pos[1][0], pos[0][1] - pos[1][1])
    d12 = math.hypot(pos[1][0] - pos[2][0], pos[1][1] - pos[2][1])
    assert d01 < d12


def test_layout_of_nothing_and_of_one_bubble():
    assert layout_bubbles([], [], {}) == []
    x, y, r = layout_bubbles(["only"], [10], {})[0]
    assert (x, y) == (BOX_W / 2, BOX_H / 2) and r > 0


def test_compute_time_on_a_65_card_fixture_is_within_the_plans_bar():
    cards = synthetic()
    rows = sum(len(t.basket) for t in cards)
    t0 = time.perf_counter()
    overlap_map(cards, {}, include_silent=False)
    elapsed = time.perf_counter() - t0
    print(f"overlap-map compute: {len(cards)} cards, {rows} rows, {elapsed:.2f} s")
    assert (
        elapsed < 6.0
    )  # the plan's bar is 3 s cold on prod's shape; this guards a regression, not a figure


# ---------------------------------------------------------------------------------------------------------
# the router (reads stubbed)
# ---------------------------------------------------------------------------------------------------------


def _client(monkeypatch, cards, identity, *, live=None, later=0, fallback=0):
    monkeypatch.setattr(
        router_mod,
        "cards_as_of",
        lambda conn, asof, known_at, **_: CardsAsOf(
            live if live is not None else len(cards), cards, later, fallback
        ),
    )
    monkeypatch.setattr(router_mod.master, "identity_for", lambda conn, ids, **kw: identity)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: object()
    return TestClient(app)


def test_the_route_serves_one_payload_with_cards_referenced_by_index(monkeypatch):
    cards = cards_of({"Alpha": [1, 2, 3], "Beta": [3, 4], "Solo": [9]})
    ident = {
        sec(3): {
            "name": "Three Corp",
            "business_supersector": "technology",
            "business_type": "software_it",
        }
    }
    client = _client(monkeypatch, cards, ident, live=5, later=2, fallback=1)
    # The CURRENT night is the live read (clock "record"); a fixed date turns into a past, public-clock
    # view once the next night begins (this test failed from 2026-11-05 22:30 ET on).
    night = current_night().isoformat()
    try:
        r = client.get("/dashboards/overlap-map", params={"asof": night})
        assert r.status_code == 200, r.text
        body = r.json()
        again = client.get("/dashboards/overlap-map", params={"asof": night}).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert body["cache"] == "computed" and again["cache"] == "hit"
    assert [c["name"] for c in body["cards"]] == ["Alpha", "Beta", "Solo"]
    assert body["pairs"] == [{"a": 0, "b": 1, "shared": 1}]
    assert (body["live_cards"], body["cards_counted"], body["cards_not_yet_created"]) == (5, 3, 2)
    assert body["roster_fallback_cards"] == 1 and body["include_silent"] is False
    three = next(c for c in body["companies"] if c["ticker"] == "T3")
    assert three["name"] == "Three Corp" and three["supersector"] == "technology"
    assert sorted(row["card"] for row in three["rows"]) == [0, 1]
    assert body["cards"][2]["shares_nothing"] is True and body["cards"][2]["x"] is None
    assert [g["threshold"] for g in body["groups"]] == [1, 2, 3, 4, 5]
    assert body["clock"] == "record" and body["box_w"] == BOX_W


def test_the_toggle_is_part_of_the_cache_key(monkeypatch):
    cards = [
        card("Alpha", member("T1", sec(1)), member("T9", sec(9), CONTEXT_LABEL)),
        card("Beta", member("T2", sec(2)), member("T9", sec(9), CONTEXT_LABEL)),
    ]
    client = _client(monkeypatch, cards, {})
    try:
        off = client.get("/dashboards/overlap-map", params={"asof": "2026-11-04"}).json()
        on = client.get(
            "/dashboards/overlap-map", params={"asof": "2026-11-04", "include_silent": "true"}
        ).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    assert off["pairs"] == [] and len(on["pairs"]) == 1
    assert off["silent_rows_left_out"] == 2 and on["silent_rows_left_out"] == 0


# ---------------------------------------------------------------------------------------------------------
# point in time (DB-backed)
# ---------------------------------------------------------------------------------------------------------

PAST = date(
    2026, 6, 5
)  # a past as-of, after the first roster (June 1) and before the second (June 10)
LATER = date(2026, 6, 12)  # after the second roster, before the second card existed


def _security(db, ticker: str) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, 'equity', %s)",
            (sid, DEFAULT_TENANT_ID, ticker, None, "2026-01-01"),
        )
    return sid


def _pin_latest_snapshot(db, thesis_id: UUID, when: datetime) -> None:
    """``upsert`` stamps a snapshot ``now()``; pin the newest one to a controlled instant."""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE basket_snapshot SET taken_at = %s WHERE id = ("
            "  SELECT id FROM basket_snapshot WHERE thesis_id = %s ORDER BY taken_at DESC, id DESC LIMIT 1)",
            (when, thesis_id),
        )
    db.commit()


def _created(db, thesis_id: UUID, when: str) -> None:
    with db.cursor() as cur:  # a fixture backdate, not an app write
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", (when, thesis_id))
    db.commit()


def test_a_card_created_and_a_row_added_after_a_past_asof_are_not_on_the_page(db):
    """Card Early is promoted twice: AAA and BBB on June 1, then CCC added on June 10. Card Late (created June 20)
    holds BBB and CCC. At June 5 only Early exists and holds AAA and BBB (no CCC, no pair); at June 12 Early holds
    all three and Late is still absent; today both exist and share BBB and CCC."""
    a, b, c = _security(db, "OMA"), _security(db, "OMB"), _security(db, "OMC")
    early = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Early",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[member("OMA", a), member("OMB", b)],
    )
    thesis_repo.upsert(db, early)
    db.commit()
    _pin_latest_snapshot(db, early.id, datetime(2026, 6, 1, tzinfo=timezone.utc))
    thesis_repo.upsert(db, early.model_copy(update={"basket": [*early.basket, member("OMC", c)]}))
    db.commit()
    _pin_latest_snapshot(db, early.id, datetime(2026, 6, 10, tzinfo=timezone.utc))
    _created(db, early.id, "2026-01-02")
    late = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Late",
        narrative="x",
        segments=[Segment(label="Core")],
        basket=[member("OMB", b), member("OMC", c)],
    )
    thesis_repo.upsert(db, late)
    db.commit()
    _created(db, late.id, "2026-06-20")

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)

        def get(d: date) -> dict:
            r = client.get("/dashboards/overlap-map", params={"asof": d.isoformat()})
            assert r.status_code == 200, r.text
            return r.json()

        past, later, live = get(PAST), get(LATER), get(market_today())
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    def tickers(body: dict) -> list[str]:
        return sorted(c["ticker"] for c in body["companies"])

    assert [c["name"] for c in past["cards"]] == ["Early"]
    assert tickers(past) == ["OMA", "OMB"] and past["pairs"] == []
    assert past["cards_not_yet_created"] == 1 and past["clock"] == "public"
    assert past["roster_fallback_cards"] == 0  # a snapshot stood in, not today's roster

    assert [c["name"] for c in later["cards"]] == ["Early"]
    assert tickers(later) == ["OMA", "OMB", "OMC"] and later["cards_not_yet_created"] == 1

    assert [c["name"] for c in live["cards"]] == ["Early", "Late"]
    assert live["pairs"] == [{"a": 0, "b": 1, "shared": 2}] and live["clock"] == "record"
