"""Shared Names (``docs/dashboards/shared-names.md``): the pure compute on hand-built cards, the router's
reads (pure, with the two reads stubbed), and one DB-backed read through the real ``list_all`` proving an
archived card stays off the page.

A "now" view (``asofAware: false``), so the checklist's past-as-of case does not apply: the endpoint takes no
as-of and reads today's baskets only (the plan's known limits)."""

from __future__ import annotations

import uuid
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import shared_names as router_mod
from dashboards.shared_names import SharedNames, shared_names
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, SILENT_LABELS, BasketMember, Segment, Thesis
from repositories import thesis_repo

COUNTER = ContextRole.COUNTER_CASE

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def member(
    ticker: str,
    sid: UUID | None,
    segment: str | None = "Core",
    role: ContextRole | None = None,
) -> BasketMember:
    return BasketMember(
        ticker=ticker, role="prose", security_id=sid, segment=segment, context_role=role
    )


def card(name: str, *members: BasketMember, tid: UUID | None = None) -> Thesis:
    labels = sorted({m.segment for m in members if m.segment and m.segment not in SILENT_LABELS})
    return Thesis(
        id=tid or uuid.uuid4(),
        name=name,
        narrative="x",
        segments=[Segment(label=label) for label in labels],
        basket=list(members),
    )


def row(result: SharedNames, ticker: str):
    return next(r for r in result.names if r.ticker == ticker)


def chip(result: SharedNames, ticker: str, card_name: str):
    return next(c for c in row(result, ticker).cards if c.thesis_name == card_name)


A, B, C, D = (uuid.uuid4() for _ in range(4))


# ---------------------------------------------------------------------------------------------------------
# the main table
# ---------------------------------------------------------------------------------------------------------


def test_a_company_on_three_cards_is_one_row_with_three_chips_in_card_name_order():
    cards = [
        card("Uranium", member("AAA", A)),
        card("Grid", member("AAA", A, "Turbines")),
        card("Nuclear", member("AAA", A, "Fuel")),
        card("Solo", member("ZZZ", B)),
    ]
    result = shared_names(cards, {A: "Alpha Corp", B: "Zed Inc"})

    assert [r.ticker for r in result.names] == ["AAA"]  # a name on one card is not shared
    r = row(result, "AAA")
    assert (r.security_id, r.name, r.card_count) == (A, "Alpha Corp", 3)
    assert [c.thesis_name for c in r.cards] == ["Grid", "Nuclear", "Uranium"]
    assert [c.links for c in r.cards] == [("Turbines",), ("Fuel",), ("Core",)]
    assert all(c.counts and c.silent_link is None for c in r.cards)
    assert (result.live_cards, result.basket_rows, result.companies) == (4, 4, 2)


def test_several_links_on_one_card_count_once_with_the_links_listed_together():
    cards = [
        card(
            "Grid",
            member("AAA", A, "Turbines"),
            member("AAA", A, "Cables"),
            member("AAA", A, "Turbines"),  # a repeated label lists once
        ),
        card("Nuclear", member("AAA", A, "Fuel")),
    ]
    result = shared_names(cards, {})

    r = row(result, "AAA")
    assert r.card_count == 2
    assert chip(result, "AAA", "Grid").links == ("Turbines", "Cables")  # basket order
    assert result.basket_rows == 4 and result.companies == 1


def test_a_company_on_two_links_of_ONE_card_and_nowhere_else_is_not_shared():
    result = shared_names(
        [card("Grid", member("AAA", A, "Turbines"), member("AAA", A, "Cables"))], {}
    )
    assert result.names == () and result.pairs == ()


def test_a_row_in_no_link_lists_None():
    cards = [
        card("Grid", member("AAA", A, None), member("AAA", A, "Cables")),
        card("Nuclear", member("AAA", A, "Fuel")),
    ]
    assert chip(shared_names(cards, {}), "AAA", "Grid").links == (None, "Cables")


def test_rows_sort_by_most_cards_then_ticker():
    cards = [
        card("One", member("bbb", B), member("CCC", C), member("AAA", A)),
        card("Two", member("bbb", B), member("CCC", C), member("AAA", A)),
        card("Three", member("CCC", C)),
    ]
    result = shared_names(cards, {})
    assert [(r.ticker, r.card_count) for r in result.names] == [
        ("CCC", 3),
        ("AAA", 2),
        ("bbb", 2),  # ticker order ignores case
    ]


def test_a_company_with_no_master_name_keeps_its_row():
    result = shared_names([card("One", member("AAA", A)), card("Two", member("AAA", A))], {})
    assert row(result, "AAA").name is None


def test_the_ticker_is_the_one_most_cards_carry():
    cards = [
        card("One", member("OLD", A)),
        card("Two", member("NEW", A)),
        card("Three", member("NEW", A)),
    ]
    result = shared_names(cards, {})
    r = result.names[0]
    assert r.ticker == "NEW"
    # each chip keeps the ticker its own card carries (the Cockpit's ?name= key on that card)
    assert {c.thesis_name: c.ticker for c in r.cards} == {
        "One": "OLD",
        "Three": "NEW",
        "Two": "NEW",
    }


# ---------------------------------------------------------------------------------------------------------
# Context / Bench (silent links) and the counting-only toggle
# ---------------------------------------------------------------------------------------------------------


def _silent_cards() -> list[Thesis]:
    return [
        card("Grid", member("AAA", A, "Turbines"), member("BBB", B)),
        card("Nuclear", member("AAA", A, CONTEXT_LABEL), member("BBB", B)),
        card("Uranium", member("AAA", A, BENCH_LABEL), member("BBB", B, BENCH_LABEL)),
    ]


def test_a_context_or_bench_membership_is_included_and_tagged_on_its_chip():
    result = shared_names(_silent_cards(), {})

    assert row(result, "AAA").card_count == 3
    ctx = chip(result, "AAA", "Nuclear")
    assert (ctx.silent_link, ctx.links, ctx.counts) == (CONTEXT_LABEL, (), False)
    bench = chip(result, "AAA", "Uranium")
    assert (bench.silent_link, bench.links, bench.counts) == (BENCH_LABEL, (), False)
    voting = chip(result, "AAA", "Grid")
    assert (voting.silent_link, voting.links, voting.counts) == (None, ("Turbines",), True)
    assert (result.counting_only, result.silent_rows_left_out) == (False, 0)


def test_counting_only_drops_silent_memberships_from_both_sections():
    included = shared_names(_silent_cards(), {})
    assert {(p.card_a.thesis_name, p.card_b.thesis_name) for p in included.pairs} == {
        ("Grid", "Nuclear"),
        ("Grid", "Uranium"),
        ("Nuclear", "Uranium"),
    }

    result = shared_names(_silent_cards(), {}, counting_only=True)

    assert result.counting_only is True
    assert result.silent_rows_left_out == 3  # AAA on Nuclear + Uranium, BBB on Uranium
    assert result.basket_rows == 6  # every row on the live cards, before the filter
    # AAA now counts only on Grid -> no longer shared; BBB counts on Grid + Nuclear
    assert [r.ticker for r in result.names] == ["BBB"]
    assert all(c.silent_link is None and c.counts for c in row(result, "BBB").cards)
    assert [(p.card_a.thesis_name, p.card_b.thesis_name, p.tickers) for p in result.pairs] == [
        ("Grid", "Nuclear", ("BBB",))
    ]


# ---------------------------------------------------------------------------------------------------------
# "Opposite sides"
# ---------------------------------------------------------------------------------------------------------


def test_opposite_sides_counts_on_one_card_and_counter_case_on_another():
    cards = [
        card("Grid", member("AAA", A, "Turbines")),
        card("Coal", member("AAA", A, CONTEXT_LABEL, COUNTER)),
    ]
    result = shared_names(cards, {})
    assert row(result, "AAA").opposite_sides is True
    assert chip(result, "AAA", "Coal").context_role == COUNTER


def test_opposite_sides_holds_when_the_counter_case_row_itself_counts():
    cards = [
        card("Grid", member("AAA", A, "Turbines")),
        card("Coal", member("AAA", A, "Miners", COUNTER)),
    ]
    assert row(shared_names(cards, {}), "AAA").opposite_sides is True


def test_no_opposite_sides_when_the_only_counting_card_is_the_counter_case_card():
    # counter-case where it is the company's only membership that counts; silent everywhere else
    cards = [
        card("Coal", member("AAA", A, "Miners", COUNTER)),
        card("Grid", member("AAA", A, CONTEXT_LABEL)),
    ]
    assert row(shared_names(cards, {}), "AAA").opposite_sides is False


def test_no_opposite_sides_when_it_counts_nowhere():
    cards = [
        card("Coal", member("AAA", A, CONTEXT_LABEL, COUNTER)),
        card("Grid", member("AAA", A, BENCH_LABEL)),
    ]
    assert row(shared_names(cards, {}), "AAA").opposite_sides is False


def test_no_opposite_sides_without_a_counter_case_tag():
    cards = [
        card("Coal", member("AAA", A, "Miners", ContextRole.PAYER)),
        card("Grid", member("AAA", A, "Turbines")),
    ]
    assert row(shared_names(cards, {}), "AAA").opposite_sides is False


def test_counting_only_drops_a_counter_case_tag_with_its_silent_row():
    cards = [
        card("Grid", member("AAA", A, "Turbines")),
        card("Coal", member("AAA", A, CONTEXT_LABEL, COUNTER)),
        card("Nuclear", member("AAA", A, "Fuel")),
    ]
    result = shared_names(cards, {}, counting_only=True)
    r = row(result, "AAA")
    assert r.card_count == 2 and r.opposite_sides is False


def test_the_role_reads_from_any_row_of_the_company_on_that_card():
    cards = [
        card("Coal", member("AAA", A, "Miners"), member("AAA", A, "Ports", COUNTER)),
        card("Grid", member("AAA", A, "Turbines")),
    ]
    result = shared_names(cards, {})
    assert chip(result, "AAA", "Coal").context_role == COUNTER
    assert row(result, "AAA").opposite_sides is True


# ---------------------------------------------------------------------------------------------------------
# card pairs
# ---------------------------------------------------------------------------------------------------------


def test_pairs_count_each_shared_company_once_and_sort_by_most_shared_then_names():
    t1, t2, t3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    cards = [
        card(
            "beta",
            member("CCC", C),
            member("AAA", A, "Turbines"),
            member("AAA", A, "Cables"),  # two links, one company: counts once in the pair
            member("BBB", B),
            tid=t2,
        ),
        card("Alpha", member("BBB", B), member("AAA", A), member("CCC", C), tid=t1),
        card("Gamma", member("AAA", A), member("DDD", D), tid=t3),
    ]
    result = shared_names(cards, {})

    got = [
        (p.card_a.thesis_name, p.card_b.thesis_name, p.shared_count, p.tickers)
        for p in result.pairs
    ]
    assert got == [
        ("Alpha", "beta", 3, ("AAA", "BBB", "CCC")),  # tickers in ticker order
        ("Alpha", "Gamma", 1, ("AAA",)),
        ("beta", "Gamma", 1, ("AAA",)),  # card names compare ignoring case
    ]
    first = result.pairs[0]
    assert (first.card_a.thesis_id, first.card_b.thesis_id) == (t1, t2)
    assert first.security_ids == (A, B, C)


def test_no_pairs_when_nothing_is_shared():
    result = shared_names([card("One", member("AAA", A)), card("Two", member("BBB", B))], {})
    assert result.pairs == () and result.names == ()


# ---------------------------------------------------------------------------------------------------------
# show the work: unresolved rows are counted, never dropped
# ---------------------------------------------------------------------------------------------------------


def test_rows_without_a_resolved_security_are_counted_not_matched():
    cards = [
        card("One", member("AAA", A), member("NEWCO", None), member("PRIV", None)),
        card("Two", member("AAA", A), member("NEWCO", None)),
    ]
    result = shared_names(cards, {})

    assert result.unresolved_rows == 3
    assert [r.ticker for r in result.names] == ["AAA"]  # never matched by ticker alone
    assert (result.basket_rows, result.companies) == (5, 1)


def test_counting_only_counts_a_silent_unresolved_row_as_left_out_only():
    cards = [card("One", member("NEWCO", None, CONTEXT_LABEL), member("PRIV", None))]
    result = shared_names(cards, {}, counting_only=True)
    assert (result.silent_rows_left_out, result.unresolved_rows) == (1, 1)


def test_an_empty_board():
    result = shared_names([], {})
    assert (result.live_cards, result.basket_rows, result.companies) == (0, 0, 0)
    assert result.names == () and result.pairs == ()


# ---------------------------------------------------------------------------------------------------------
# the router (pure: both reads stubbed; it only reads and calls the compute)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {}

    def fake_list_all(conn, **kwargs):
        seen["list_all_kwargs"] = kwargs
        return _silent_cards()

    def fake_names_for(conn, ids):
        seen["names_for_ids"] = set(ids)
        return {A: "Alpha Corp"}

    monkeypatch.setattr(router_mod.thesis_repo, "list_all", fake_list_all)
    monkeypatch.setattr(router_mod.master, "names_for", fake_names_for)
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()


def test_the_router_reads_the_default_live_list_and_serves_the_compute(stub_client):
    client, seen = stub_client
    r = client.get("/dashboards/shared-names")
    assert r.status_code == 200
    body = r.json()

    # list_all's DEFAULT excludes archived cards; the router must never widen it
    assert seen["list_all_kwargs"] == {}
    assert seen["names_for_ids"] == {A, B}
    assert body["counting_only"] is False
    assert (body["live_cards"], body["basket_rows"], body["unresolved_rows"]) == (3, 6, 0)
    assert [n["ticker"] for n in body["names"]] == ["AAA", "BBB"]
    aaa = body["names"][0]
    assert aaa["name"] == "Alpha Corp" and aaa["security_id"] == str(A)
    assert [c["silent_link"] for c in aaa["cards"]] == [None, "Context", "Bench"]
    assert len(body["pairs"]) == 3


def test_the_router_threads_counting_only(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/shared-names", params={"counting_only": "true"}).json()
    assert body["counting_only"] is True and body["silent_rows_left_out"] == 3
    assert [n["ticker"] for n in body["names"]] == ["BBB"]


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the real list_all keeps an archived card off the page (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------


def _seed_security(db, ticker: str, name: str) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, "2020-01-01"),
        )
    return sid


def test_an_archived_card_is_absent(db):
    shared = _seed_security(db, "SHRD", "Shared Holdings")
    other = _seed_security(db, "OTHR", "Other Co")
    live_a = card("Live A", member("SHRD", shared), member("OTHR", other))
    live_b = card("Live B", member("SHRD", shared))
    parked = card("Parked", member("SHRD", shared), member("OTHR", other))
    for t in (live_a, live_b, parked):
        thesis_repo.upsert(db, t.model_copy(update={"tenant_id": DEFAULT_TENANT_ID}))
    thesis_repo.set_archived(db, parked.id, True)
    db.commit()

    app.dependency_overrides[get_conn] = lambda: db
    try:
        body = TestClient(app).get("/dashboards/shared-names").json()
    finally:
        app.dependency_overrides.clear()

    assert body["live_cards"] == 2
    assert [(n["ticker"], n["name"], n["card_count"]) for n in body["names"]] == [
        ("SHRD", "Shared Holdings", 2)
    ]  # OTHR shares only with the archived card, so it is on one live card
    assert {c["thesis_name"] for c in body["names"][0]["cards"]} == {"Live A", "Live B"}
    assert [(p["card_a"]["thesis_name"], p["card_b"]["thesis_name"]) for p in body["pairs"]] == [
        ("Live A", "Live B")
    ]
