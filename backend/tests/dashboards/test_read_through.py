"""Read-Throughs (``docs/dashboards/read-through.md``): the row rule (one row per company per role, two roles on two
cards = two rows, the same role on two cards = one row with two chips, a role on a counting row, a silent row, a
role row with no resolved security counted and never a row), the last-filing pick (newest by date, ties by kind
order, a row after the as-of never picked), the next-catalyst pick (nearest on or after the as-of, a past one and an
undated one ignored), the role order, the tape against SPY (each gap), the counts and the default order; the router
off one cached read on the request's view; and, DB-backed, a role set after the as-of not showing, a filing public
after the as-of never reaching a row, and the tape ending at the as-of."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import read_through as router_mod
from dashboards.baskets import Membership
from dashboards.cache import CACHE
from dashboards.filings import KIND_ORDER, SEC_FILING_KINDS, FilingRow
from dashboards.read_through import (
    FILING_DAYS,
    ROLE_ORDER,
    TAPE_WINDOWS,
    CardRef,
    CatalystRef,
    Identity,
    ReadThroughInputs,
    Tape,
    catalyst_refs,
    identities,
    last_filing,
    next_catalyst,
    read_through,
    tape_of,
    tape_view,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Catalyst, Segment, Thesis
from domain.trading_days import is_scheduled_day
from repositories import thesis_repo
from signals.display import trailing_returns

UTC = timezone.utc
ASOF = date(2026, 10, 28)
T1, T2, T3 = (uuid.uuid4() for _ in range(3))
SX, SY, SW, SV = (uuid.uuid4() for _ in range(4))
MIXED = "mixed-scale price history"


# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def ms(
    ticker: str,
    sid: UUID | None,
    segment: str | None = "Turbines",
    role: str | None = None,
    tid: UUID = T1,
    name: str = "Grid",
) -> Membership:
    return Membership(
        thesis_id=tid,
        thesis_name=name,
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
        context_role=role,
    )


def fr(sid: UUID, kind: str, d: date, **over) -> FilingRow:
    base = dict(
        security_id=sid,
        ticker="X",
        kind=kind,
        form={"form4": "4"}.get(kind, kind),
        date=d,
        headline=f"{kind} {d}",
    )
    return FilingRow(**{**base, **over})


def cat(tid: UUID, name: str, label: str, when: date, kind: str | None = None) -> CatalystRef:
    return CatalystRef(tid, name, uuid.uuid4(), label, kind, when, None)


CARDS = (CardRef(T1, "Grid"), CardRef(T2, "Batteries"), CardRef(T3, "Quiet"))


def inputs(memberships, **over) -> ReadThroughInputs:
    base: dict = dict(
        live_cards=4,
        cards_not_yet_created=1,
        roster_fallback_cards=2,
        cards=CARDS,
        memberships=tuple(memberships),
        catalysts=(),
        identity={},
        filings={},
        tapes={},
        spy=Tape(1.5, 4.0, ASOF),
        flagged={},
    )
    return ReadThroughInputs(**{**base, **over})


def build(memberships, **over):
    return read_through(inputs(memberships, **over), ASOF)


def row_of(page, sid: UUID, role: str):
    (r,) = [r for r in page.rows if r.security_id == sid and r.role == role]
    return r


# the roster the row-rule tests share: XXX is a payer on two cards (Context on one), YYY a counter-case on one
# card and a payer on another, ZZZ has a role and no resolved security, WWW has no role
ROSTER = [
    ms("XXX", SX, "Turbines", "payer", T1, "Grid"),
    ms("XXX", SX, CONTEXT_LABEL, "payer", T2, "Batteries"),
    ms("YYY", SY, CONTEXT_LABEL, "counter_case", T1, "Grid"),
    ms("YYY", SY, BENCH_LABEL, "payer", T2, "Batteries"),
    ms("ZZZ", None, "Turbines", "direct_read", T1, "Grid"),
    ms("WWW", SW, "Turbines", None, T1, "Grid"),
]


# ---------------------------------------------------------------------------------------------------------
# the roles
# ---------------------------------------------------------------------------------------------------------


def test_the_role_order_is_payers_first_and_covers_every_context_role():
    assert ROLE_ORDER[0] == "payer"
    assert set(ROLE_ORDER) == {r.value for r in ContextRole}
    assert len(ROLE_ORDER) == len(set(ROLE_ORDER))
    # the Chain Wave's order, which this page mirrors
    assert ROLE_ORDER == (
        "payer",
        "counter_case",
        "direct_read",
        "participant",
        "financier",
        "benchmark",
    )


def test_the_tape_windows_are_the_display_members_own_trading_day_windows():
    assert set(TAPE_WINDOWS) <= set(trailing_returns.WINDOWS)
    assert TAPE_WINDOWS == (7, 30)


# ---------------------------------------------------------------------------------------------------------
# the row rule
# ---------------------------------------------------------------------------------------------------------


def test_one_row_per_company_per_role_two_roles_on_two_cards_make_two_rows():
    page = build(ROSTER)
    assert sorted((r.ticker, r.role) for r in page.rows) == [
        ("XXX", "payer"),
        ("YYY", "counter_case"),
        ("YYY", "payer"),
    ]
    # YYY is two rows with one chip each: its counter-case card and its payer card
    counter, payer = row_of(page, SY, "counter_case"), row_of(page, SY, "payer")
    assert [c.thesis_name for c in counter.cards] == ["Grid"]
    assert [c.thesis_name for c in payer.cards] == ["Batteries"]
    assert counter.key == f"{SY}:counter_case" and payer.key == f"{SY}:payer"
    assert len({r.key for r in page.rows}) == len(page.rows)


def test_the_same_role_on_two_cards_is_one_row_with_two_chips_in_card_order():
    r = row_of(build(ROSTER), SX, "payer")
    assert [(c.thesis_name, c.links) for c in r.cards] == [
        ("Batteries", (CONTEXT_LABEL,)),
        ("Grid", ("Turbines",)),
    ]
    # the chip carries the company's ticker on THAT card (the Cockpit's ?name= key) and whether it is silent
    assert [(c.ticker, c.silent) for c in r.cards] == [("XXX", True), ("XXX", False)]
    assert [c.thesis_id for c in r.cards] == [T2, T1]


def test_a_role_on_a_counting_row_counts_and_a_silent_row_is_shown_with_its_link():
    page = build(ROSTER)
    counting = row_of(page, SX, "payer")
    assert any(not c.silent for c in counting.cards)  # a role on a counting row is shown
    bench = row_of(page, SY, "payer")
    assert [(c.links, c.silent) for c in bench.cards] == [((BENCH_LABEL,), True)]
    context = row_of(page, SY, "counter_case")
    assert [(c.links, c.silent) for c in context.cards] == [((CONTEXT_LABEL,), True)]


def test_a_company_in_two_links_of_a_card_is_one_chip_with_both_links_and_a_nolink_row_says_none():
    page = build(
        [
            ms("XXX", SX, "Turbines", "payer"),
            ms("XXX", SX, "Cables", "payer"),
            ms("XXX", SX, None, "payer"),
        ]
    )
    (r,) = page.rows
    (chip,) = r.cards
    assert chip.links == ("Turbines", "Cables", None)  # None = no link


def test_a_role_row_with_no_resolved_security_is_counted_and_listed_never_a_row():
    page = build(ROSTER)
    assert page.unresolved == ("ZZZ",) and page.unresolved_rows == 1
    assert "ZZZ" not in {r.ticker for r in page.rows}
    # two of the same unresolved ticker on two cards: two rows counted, the ticker listed once
    more = build([*ROSTER, ms("ZZZ", None, None, "direct_read", T2, "Batteries")])
    assert more.unresolved == ("ZZZ",) and more.unresolved_rows == 2


def test_a_basket_row_without_a_role_makes_no_row():
    page = build([ms("WWW", SW, "Turbines", None)])
    assert page.rows == () and page.companies == 0
    assert [c.rows for c in page.by_role] == [0] * len(ROLE_ORDER)


def test_the_display_ticker_is_the_one_most_of_a_companys_cards_use():
    page = build(
        [
            ms("AAA", SX, "Turbines", "payer", T1, "Grid"),
            ms("BBB", SX, "Turbines", "payer", T2, "Batteries"),
            ms("BBB", SX, "Turbines", "payer", T3, "Quiet"),
        ]
    )
    assert [r.ticker for r in page.rows] == ["BBB"]


# ---------------------------------------------------------------------------------------------------------
# the cards with and without roles, and the counts
# ---------------------------------------------------------------------------------------------------------


def test_cards_with_no_role_are_listed_a_to_z_and_the_counts_say_what_the_table_rests_on():
    page = build(ROSTER)
    assert (page.live_cards, page.cards_counted) == (4, 3)
    assert (page.cards_not_yet_created, page.roster_fallback_cards) == (1, 2)
    assert page.cards_with_roles == 2
    assert [c.thesis_name for c in page.cards_without_roles] == ["Quiet"]
    # roles set = one per card and company: XXX on two cards, YYY on two, ZZZ on one
    assert page.role_tags == 5
    assert page.companies == 2  # distinct resolved role companies (ZZZ is unresolved)
    assert [(c.role, c.rows) for c in page.by_role] == [
        ("payer", 2),
        ("counter_case", 1),
        ("direct_read", 0),
        ("participant", 0),
        ("financier", 0),
        ("benchmark", 0),
    ]
    assert sum(c.rows for c in page.by_role) == len(page.rows) == 3
    # a card whose only role row is unresolved still holds a role (its row is counted in the unresolved)
    only_unresolved = build([ms("ZZZ", None, "Turbines", "direct_read")])
    assert only_unresolved.cards_with_roles == 1 and only_unresolved.rows == ()


def test_the_empty_case_is_a_page_not_an_error():
    page = build([])
    assert page.rows == () and page.cards_with_roles == 0
    assert [c.thesis_name for c in page.cards_without_roles] == ["Batteries", "Grid", "Quiet"]
    assert (page.role_tags, page.companies, page.unresolved_rows, page.rows_with_filing) == (0,) * 4


def test_a_role_the_order_does_not_know_is_listed_after_the_known_ones_never_dropped():
    page = build([ms("XXX", SX, "Turbines", "mystery")])
    assert [(c.role, c.rows) for c in page.by_role][-1] == ("mystery", 1)
    assert len(page.rows) == 1


# ---------------------------------------------------------------------------------------------------------
# the last-filing pick
# ---------------------------------------------------------------------------------------------------------


def test_the_last_filing_is_the_newest_row_by_date():
    rows = [
        fr(SX, "8k", date(2026, 10, 20)),
        fr(SX, "form4", date(2026, 10, 27), headline="Jane Roe (CEO): S sale"),
        fr(SX, "revenue", date(2026, 9, 1)),
    ]
    got = last_filing(rows, ASOF)
    assert got is not None
    assert (got.kind, got.form, got.date, got.days_ago) == ("form4", "4", date(2026, 10, 27), 1)
    assert got.headline == "Jane Roe (CEO): S sale"


def test_a_tie_on_the_date_goes_by_the_readers_kind_order_then_accession():
    d = date(2026, 10, 27)
    # the kind order is the reader's: 8-K before 13D/G before Form 4 before 4/A before the revenue print before
    # any other form in the EDGAR index (the filing kinds; the others are never picked at all, see below)
    filing_kinds = [k for k in KIND_ORDER if k in SEC_FILING_KINDS]
    assert filing_kinds == ["8k", "stake", "form4", "form4a", "revenue", "filing"]
    for first, later in zip(filing_kinds, filing_kinds[1:]):
        got = last_filing([fr(SX, later, d), fr(SX, first, d)], ASOF)
        assert got is not None and got.kind == first
    # same kind and day: the lower accession (a stable pick, never an arbitrary one)
    a = fr(SX, "8k", d, accession="0000000001-26-000002", headline="b")
    b = fr(SX, "8k", d, accession="0000000001-26-000001", headline="a")
    got = last_filing([a, b], ASOF)
    assert got is not None and got.accession == "0000000001-26-000001"
    assert last_filing([b, a], ASOF) == got  # the order the rows arrive in does not matter


def test_the_last_filing_is_a_filing_never_a_split_a_catalyst_or_a_stored_or_ratified_fact():
    own = [  # rows the system holds, all NEWER than the one real filing below
        fr(SX, "split", date(2026, 10, 28), form="split"),
        fr(SX, "dilution", date(2026, 10, 28), form="dilution"),
        fr(SX, "catalyst", date(2026, 10, 27), form="catalyst"),
        fr(SX, "ratified", date(2026, 10, 27), form="stored fact"),
        fr(SX, "ratified", date(2026, 10, 26), form="ratified fact"),
    ]
    real = fr(SX, "8k", date(2026, 9, 1), form="8-K", headline="8-K · 2.02")
    got = last_filing([*own, real], ASOF)
    assert got is not None and (got.kind, got.date, got.days_ago) == ("8k", date(2026, 9, 1), 57)
    assert last_filing(own, ASOF) is None  # only the system's own rows: no filing, the cell says so
    # each SEC kind counts, even against newer rows of the system's own
    for kind in ("stake", "form4", "form4a", "revenue", "filing"):
        got = last_filing([*own, fr(SX, kind, date(2026, 10, 1))], ASOF)
        assert got is not None and got.kind == kind
    # it reaches the page's row the same way, and the count of rows with a filing follows
    page = build(ROSTER, filings={SX: [*own, real], SY: own})
    assert row_of(page, SX, "payer").last_filing.kind == "8k"
    assert row_of(page, SY, "payer").last_filing is None
    assert page.rows_with_filing == 1


def test_a_lone_424B5_from_the_EDGAR_index_is_the_last_filing_when_it_is_the_newest():
    """The filing index (0070): a form nothing reads into still IS a filing — the newest one is picked over an
    older 8-K and over the system's own newer rows."""
    rows = [
        fr(SX, "8k", date(2026, 10, 1), form="8-K"),
        fr(SX, "filing", date(2026, 10, 22), form="424B5", headline="424B5 · d424b5.htm"),
        fr(SX, "split", date(2026, 10, 27), form="split"),
    ]
    got = last_filing(rows, ASOF)
    assert got is not None
    assert (got.kind, got.form, got.headline, got.days_ago) == (
        "filing",
        "424B5",
        "424B5 · d424b5.htm",
        6,
    )


def test_a_company_with_no_filing_has_none_and_a_row_after_the_asof_is_never_picked():
    assert last_filing([], ASOF) is None
    assert (
        last_filing([fr(SX, "8k", date(2026, 10, 29))], ASOF) is None
    )  # dated after the as-of (#1)
    got = last_filing([fr(SX, "8k", date(2026, 10, 29)), fr(SX, "8k", date(2026, 10, 1))], ASOF)
    assert got is not None and got.date == date(2026, 10, 1) and got.days_ago == 27


def test_the_last_filing_carries_the_link_and_the_accession_for_the_row():
    got = last_filing(
        [
            fr(
                SX,
                "8k",
                date(2026, 10, 20),
                url="https://www.sec.gov/Archives/edgar/data/1/2/",
                accession="0000000001-26-000009",
            )
        ],
        ASOF,
    )
    assert got is not None
    assert got.url == "https://www.sec.gov/Archives/edgar/data/1/2/"
    assert got.accession == "0000000001-26-000009"
    assert last_filing([fr(SX, "8k", ASOF)], ASOF).days_ago == 0  # the as-of day itself counts


def test_the_filing_window_is_the_readers_year():
    assert FILING_DAYS == 365


def test_a_row_takes_the_last_filing_of_its_company_only():
    page = build(
        ROSTER,
        filings={
            SX: [fr(SX, "8k", date(2026, 10, 20))],
            SY: [fr(SY, "form4", date(2026, 10, 27)), fr(SY, "8k", date(2026, 3, 1))],
        },
    )
    assert row_of(page, SX, "payer").last_filing.date == date(2026, 10, 20)
    # the same company in two role rows shows the same last filing on both (it is the company's)
    assert row_of(page, SY, "payer").last_filing.date == date(2026, 10, 27)
    assert row_of(page, SY, "counter_case").last_filing.date == date(2026, 10, 27)
    assert page.rows_with_filing == 3


# ---------------------------------------------------------------------------------------------------------
# the next-catalyst pick
# ---------------------------------------------------------------------------------------------------------


def test_the_next_catalyst_is_the_nearest_on_or_after_the_asof_across_the_rows_cards():
    cs = [
        cat(T1, "Grid", "Q3 earnings", date(2026, 11, 12)),
        cat(T2, "Batteries", "FDA decision", date(2026, 11, 3), "regulatory"),
        cat(T3, "Quiet", "Some other card's event", date(2026, 10, 30)),  # not the row's card
    ]
    got = next_catalyst(cs, [T1, T2], ASOF)
    assert got is not None
    assert (got.thesis_name, got.label, got.kind, got.when_date, got.days) == (
        "Batteries",
        "FDA decision",
        "regulatory",
        date(2026, 11, 3),
        6,
    )
    assert next_catalyst(cs, [T1], ASOF).label == "Q3 earnings"
    assert next_catalyst(cs, [], ASOF) is None


def test_a_catalyst_dated_in_the_past_is_ignored_and_the_asof_day_counts():
    cs = [
        cat(T1, "Grid", "Old", date(2026, 10, 27)),
        cat(T1, "Grid", "Today", date(2026, 10, 28)),
        cat(T1, "Grid", "Later", date(2026, 12, 1)),
    ]
    got = next_catalyst(cs, [T1], ASOF)
    assert got is not None and (got.label, got.days) == ("Today", 0)
    assert next_catalyst(cs[:1], [T1], ASOF) is None  # only a past one: none, not the past one


def test_the_next_catalyst_counts_from_the_origin_so_the_action_day_reads_today():
    """F2: from Wednesday's night 10-28 a live view acts on Thursday 10-29. A catalyst dated the night has passed by
    then (not picked); one dated Thursday is 0 days away ('today'); a past view, counted from its as-of, is unchanged.
    """
    cs = [
        cat(T1, "Grid", "The night", date(2026, 10, 28)),
        cat(T1, "Grid", "Action day", date(2026, 10, 29)),
        cat(T1, "Grid", "Later", date(2026, 11, 2)),
    ]
    origin = date(2026, 10, 29)
    got = next_catalyst(cs, [T1], origin)
    assert got is not None and (got.label, got.days) == ("Action day", 0)
    again = next_catalyst(cs[:1], [T1], origin)
    assert again is None  # only the night's own: it has passed
    from_asof = next_catalyst(cs, [T1], ASOF)
    assert from_asof is not None and (from_asof.label, from_asof.days) == ("The night", 0)


def test_a_tie_on_the_date_goes_by_card_name_then_label():
    d = date(2026, 11, 3)
    cs = [
        cat(T1, "Grid", "B event", d),
        cat(T2, "Batteries", "Z event", d),
        cat(T2, "Batteries", "A event", d),
    ]
    got = next_catalyst(cs, [T1, T2], ASOF)
    assert got is not None and (got.thesis_name, got.label) == ("Batteries", "A event")


def test_only_dated_catalysts_become_refs_an_undated_one_is_ignored():
    thesis = Thesis(
        id=T1,
        name="Grid",
        narrative="x",
        catalysts=[
            Catalyst(id=uuid.uuid4(), label="dated", when_date=date(2026, 11, 3), when_label="Nov"),
            Catalyst(id=uuid.uuid4(), label="undated", when_label="~3wk"),
        ],
    )
    refs = catalyst_refs([thesis])
    assert [(r.thesis_id, r.thesis_name, r.label, r.when_label) for r in refs] == [
        (T1, "Grid", "dated", "Nov")
    ]


def test_the_row_shows_its_next_catalyst_with_the_card_and_the_days_to_go():
    page = build(ROSTER, catalysts=(cat(T1, "Grid", "Q3 earnings", date(2026, 11, 12)),))
    nxt = row_of(page, SX, "payer").next_catalyst
    assert nxt is not None and (nxt.thesis_name, nxt.label, nxt.days) == ("Grid", "Q3 earnings", 15)
    # YYY's counter-case row sits on Grid too; its payer row sits on Batteries only: no catalyst
    assert row_of(page, SY, "counter_case").next_catalyst is not None
    assert row_of(page, SY, "payer").next_catalyst is None


# ---------------------------------------------------------------------------------------------------------
# the tape against SPY
# ---------------------------------------------------------------------------------------------------------

SPY = Tape(1.5, 4.0, ASOF)


def test_the_tape_is_the_companys_trailing_return_minus_spys_in_percentage_points():
    t = tape_view(Tape(6.25, -3.1, ASOF), SPY, None)
    assert (t.ret_7d, t.ret_30d, t.vs_spy_7d, t.vs_spy_30d) == (6.25, -3.1, 4.75, -7.1)
    assert (t.last_close, t.gap, t.reason) == (ASOF, None, None)


def test_a_short_window_is_blank_alone_and_never_a_made_up_number():
    t = tape_view(Tape(2.0, None, ASOF), SPY, None)
    assert (t.vs_spy_7d, t.vs_spy_30d, t.gap) == (0.5, None, None)
    both = tape_view(Tape(None, None, ASOF), SPY, None)
    assert (both.vs_spy_7d, both.vs_spy_30d) == (None, None)
    short_spy = tape_view(Tape(2.0, 3.0, ASOF), Tape(1.0, None, ASOF), None)
    assert (short_spy.vs_spy_7d, short_spy.vs_spy_30d) == (1.0, None)


def test_no_company_tape_no_spy_and_a_last_close_that_is_not_spys_are_each_a_named_gap():
    assert tape_view(None, SPY, None).gap == "no_tape"
    assert tape_view(Tape(None, None, None), SPY, None).gap == "no_tape"
    no_spy = tape_view(Tape(2.0, 3.0, ASOF), None, None)
    assert (no_spy.gap, no_spy.ret_7d, no_spy.vs_spy_7d) == (
        "no_spy",
        2.0,
        None,
    )  # its own return still shows
    assert tape_view(Tape(2.0, 3.0, ASOF), Tape(1.0, 1.0, None), None).gap == "no_spy"
    stale = tape_view(Tape(2.0, 3.0, ASOF - timedelta(days=3)), SPY, None)
    assert (stale.gap, stale.vs_spy_7d, stale.ret_7d, stale.last_close) == (
        "ends_differ",
        None,
        2.0,
        ASOF - timedelta(days=3),
    )


def test_a_flagged_price_history_shows_no_tape_at_all_only_the_reason():
    t = tape_view(
        Tape(900.0, 900.0, ASOF), SPY, MIXED
    )  # the flagged bars' fake return is never carried
    assert (t.gap, t.reason) == ("flagged", MIXED)
    assert (t.ret_7d, t.ret_30d, t.vs_spy_7d, t.vs_spy_30d, t.last_close) == (None,) * 5


def test_the_tape_comes_from_the_display_members_own_pure_rule_on_both_sides():
    closes = [10.0 + 0.1 * i for i in range(40)]
    spy_closes = [100.0 + 0.2 * i for i in range(40)]
    days = [ASOF - timedelta(days=39 - i) for i in range(40)]

    def sig(cl):
        return trailing_returns.compute([{"d": d, "close": c} for d, c in zip(days, cl)], ASOF)

    own, spy = tape_of(sig(closes)), tape_of(sig(spy_closes))
    assert own is not None and spy is not None
    assert own.end == spy.end == ASOF
    assert own.ret_7d == round((closes[-1] / closes[-8] - 1) * 100, 2)
    assert own.ret_30d == round((closes[-1] / closes[-31] - 1) * 100, 2)
    t = tape_view(own, spy, None)
    assert t.vs_spy_7d == round(own.ret_7d - spy.ret_7d, 2)
    assert t.vs_spy_30d == round(own.ret_30d - spy.ret_30d, 2)
    # fewer than 8 closes: the 7d window is short, a gap for that window alone
    short = tape_of(trailing_returns.compute([{"d": d, "close": 1.0} for d in days[-5:]], ASOF))
    assert (
        short is not None and short.ret_7d is None and short.ret_30d is None and short.end == ASOF
    )
    assert tape_of(None) is None and tape_of(trailing_returns.compute([], ASOF)) is None


def test_a_flagged_company_keeps_its_row_names_its_reason_and_is_listed_once():
    page = build(
        ROSTER,
        tapes={SX: Tape(9.0, 9.0, ASOF), SY: Tape(1.0, 2.0, ASOF)},
        flagged={SX: MIXED},
    )
    r = row_of(page, SX, "payer")
    assert r.tape.gap == "flagged" and r.tape.reason == MIXED and r.tape.ret_7d is None
    assert [(f.ticker, f.reason) for f in page.flagged] == [("XXX", MIXED)]
    # the company's other rows are unaffected
    assert row_of(page, SY, "payer").tape.vs_spy_7d == -0.5
    # a company in two role rows is flagged once on the page
    two = build(ROSTER, tapes={SY: Tape(1.0, 2.0, ASOF)}, flagged={SY: MIXED})
    assert [f.ticker for f in two.flagged] == ["YYY"]
    assert (
        row_of(two, SY, "payer").tape.gap == row_of(two, SY, "counter_case").tape.gap == "flagged"
    )


def test_rows_whose_last_close_is_not_spys_are_counted():
    page = build(
        ROSTER,
        tapes={SX: Tape(1.0, 1.0, ASOF), SY: Tape(1.0, 1.0, ASOF - timedelta(days=4))},
    )
    assert page.tape_ends_differ == 2  # YYY's two rows
    assert row_of(page, SX, "payer").tape.gap is None


def test_identity_gives_the_name_and_the_business_type_or_nothing():
    ident = identities(
        {
            SX: {"name": "Xylo Corp", "business_type": "biotech_pharma", "sector": "x"},
            SY: {"name": None, "business_type": None},
        }
    )
    assert ident == {SX: Identity("Xylo Corp", "biotech_pharma"), SY: Identity(None, None)}
    page = build(ROSTER, identity=ident)
    x, y = row_of(page, SX, "payer"), row_of(page, SY, "payer")
    assert (x.name, x.business_type) == ("Xylo Corp", "biotech_pharma")
    assert (y.name, y.business_type) == (None, None)  # unknown, never guessed


# ---------------------------------------------------------------------------------------------------------
# the default order
# ---------------------------------------------------------------------------------------------------------


def test_the_default_order_is_newest_last_filing_first_then_no_filing_a_to_z_then_role_order():
    memberships = [
        ms("BBB", SX, "Turbines", "payer"),
        ms("AAA", SY, "Turbines", "counter_case"),
        ms("AAA", SY, "Cables", "payer", T2, "Batteries"),
        ms("CCC", SW, "Turbines", "financier"),
        ms("DDD", SV, "Turbines", "benchmark"),
    ]
    page = build(
        memberships,
        filings={
            SX: [fr(SX, "8k", date(2026, 10, 20))],
            SW: [fr(SW, "form4", date(2026, 10, 27))],
            SV: [fr(SV, "8k", date(2026, 10, 27))],  # same day as CCC: ticker A to Z
        },
    )
    assert [(r.ticker, r.role) for r in page.rows] == [
        ("CCC", "financier"),
        ("DDD", "benchmark"),
        ("BBB", "payer"),
        ("AAA", "payer"),  # no filing: A to Z, a company's roles in the role order
        ("AAA", "counter_case"),
    ]


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}

    def fake_read(conn, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (asof, known_at, clock)
        return inputs(
            ROSTER,
            catalysts=(cat(T1, "Grid", "Q3 earnings", date(2026, 11, 12), "earnings"),),
            identity={SX: Identity("Xylo Corp", "biotech_pharma")},
            filings={
                SX: [
                    fr(
                        SX,
                        "8k",
                        date(2026, 10, 20),
                        form="8-K",
                        headline="8-K · 2.02 results of operations and financial condition",
                        url="https://www.sec.gov/Archives/edgar/data/1/2/",
                        accession="0000000001-26-000009",
                    )
                ]
            },
            tapes={SX: Tape(6.25, -3.1, ASOF), SY: None},
            flagged={SY: MIXED},
        )

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_inputs", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_the_whole_table_off_one_cached_read_on_the_requests_view(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/read-through", params={"asof": "2026-10-28"}).json()
    assert (body["cache"], body["clock"]) == ("computed", "public")
    asof, known_at, clock = seen["view"]
    assert asof == ASOF and clock == "public" and known_at is not None
    again = client.get("/dashboards/read-through", params={"asof": "2026-10-28"}).json()
    assert again["cache"] == "hit" and seen["reads"] == 1
    # a live as-of is the unchanged live read, and its own cached read
    client.get("/dashboards/read-through", params={"asof": market_today().isoformat()})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 2


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/read-through", params={"asof": "2026-10-28"}).json()
    assert (body["benchmark"], body["tape_windows"], body["filing_days"]) == ("SPY", [7, 30], 365)
    assert (body["live_cards"], body["cards_counted"], body["cards_not_yet_created"]) == (4, 3, 1)
    assert (body["roster_fallback_cards"], body["cards_with_roles"]) == (2, 2)
    assert [c["thesis_name"] for c in body["cards_without_roles"]] == ["Quiet"]
    assert (body["role_tags"], body["companies"], body["unresolved"], body["unresolved_rows"]) == (
        5,
        2,
        ["ZZZ"],
        1,
    )
    assert [(c["role"], c["rows"]) for c in body["by_role"]][:2] == [
        ("payer", 2),
        ("counter_case", 1),
    ]
    assert body["spy"] == {"ret_7d": 1.5, "ret_30d": 4.0, "end": "2026-10-28"}
    assert [(f["ticker"], f["reason"]) for f in body["flagged"]] == [("YYY", MIXED)]
    assert body["rows_with_filing"] == 1 and body["tape_ends_differ"] == 0

    first = body["rows"][0]  # the only row with a filing
    assert (first["ticker"], first["name"], first["role"], first["business_type"]) == (
        "XXX",
        "Xylo Corp",
        "payer",
        "biotech_pharma",
    )
    assert [(c["thesis_name"], c["links"], c["silent"], c["ticker"]) for c in first["cards"]] == [
        ("Batteries", [CONTEXT_LABEL], True, "XXX"),
        ("Grid", ["Turbines"], False, "XXX"),
    ]
    assert first["last_filing"] == {
        "kind": "8k",
        "form": "8-K",
        "date": "2026-10-20",
        "days_ago": 8,
        "headline": "8-K · 2.02 results of operations and financial condition",
        "url": "https://www.sec.gov/Archives/edgar/data/1/2/",
        "accession": "0000000001-26-000009",
    }
    assert (first["next_catalyst"]["label"], first["next_catalyst"]["days"]) == ("Q3 earnings", 15)
    assert first["next_catalyst"]["when_date"] == "2026-11-12"
    assert first["tape"] == {
        "ret_7d": 6.25,
        "ret_30d": -3.1,
        "vs_spy_7d": 4.75,
        "vs_spy_30d": -7.1,
        "last_close": "2026-10-28",
        "gap": None,
        "reason": None,
    }
    flagged = next(r for r in body["rows"] if r["ticker"] == "YYY" and r["role"] == "payer")
    assert flagged["tape"]["gap"] == "flagged" and flagged["tape"]["reason"] == MIXED
    assert flagged["last_filing"] is None and flagged["next_catalyst"] is None


def test_the_router_requires_an_asof(stub_client):
    client, seen = stub_client
    assert client.get("/dashboards/read-through").status_code == 422
    assert seen["reads"] == 0


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a role set after a past as-of does not show; a filing public after it never reaches a row; the
# tape ends at the as-of (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

PAST = date(2026, 6, 5)  # a past as-of (a Friday)
LATER = date(2026, 6, 12)  # a later past as-of, after the "late" facts below
INGESTED = datetime(2026, 9, 1, tzinfo=UTC)  # our own ingest instant, after both as-ofs
CIK_A, CIK_B = "0000876543", "0000876544"
EARLY_8K, LATE_8K = "0000876543-26-000101", "0000876543-26-000102"


def _security(
    db,
    ticker: str,
    cik: str | None,
    kind: str = "equity",
    name: str | None = None,
    sector: str | None = None,
) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, name, sector, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, kind, name, sector, "2026-01-01"),
        )
    return sid


def _price(db, sid: UUID, d: date, close: float) -> None:
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
            "recorded_at": datetime.combine(d, time.min, tzinfo=UTC),  # public at its own session
        },
    )


def _8k(db, sid: UUID, accession: str, filed: date, cik: str) -> None:
    append_fact(
        db,
        "fact_corporate_event",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "form": "8-K",
            "items": ["2.02", "9.01"],
            "accession": accession,
            "filed": filed,
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}/",
            "valid_from": filed,
            "recorded_at": INGESTED,
        },
    )


def _card(db, name: str, members: list[BasketMember], *, backdate_snapshot: bool) -> Thesis:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core")],
        basket=members,
    )
    thesis_repo.upsert(db, thesis)
    # the catalysts' own writer (upsert never names them): one ahead of both as-ofs, one already past
    thesis_repo.set_catalysts(
        db,
        thesis.id,
        [
            Catalyst(id=uuid.uuid4(), label="Readout", when_date=date(2026, 6, 20)),
            Catalyst(id=uuid.uuid4(), label="Earlier event", when_date=date(2026, 6, 1)),
            Catalyst(id=uuid.uuid4(), label="Undated", when_label="~3wk"),
        ],
        tenant_id=DEFAULT_TENANT_ID,
    )
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
        if backdate_snapshot:  # the roster as first promoted was taken before the earlier as-of
            cur.execute(
                "UPDATE basket_snapshot SET taken_at = %s WHERE thesis_id = %s",
                (datetime(2026, 6, 2, 12, 0, tzinfo=UTC), thesis.id),
            )
    db.commit()
    return thesis


def test_a_role_set_after_the_asof_does_not_show_and_a_filing_after_it_never_reaches_a_row(
    db, pinned_today
):
    """Card A holds RTA as a payer from a roster taken BEFORE both past as-ofs; card B first held RTB with no role
    (that roster, also taken before them), then the operator tagged it a counter-case: a later roster snapshot,
    taken now. At a past as-of the table reads each card's roster then: A's row is there, B has no row (its role
    did not exist yet) and B is listed among the cards with no role; today's view shows both. RTA has two 8-Ks, one
    public by the earlier as-of and one only after it, both INGESTED after both as-ofs: the public clock judges
    them on their filing dates, so the earlier view's last filing is the early one and the later view's the late
    one. The tape ends at the as-of: a bar dated after it never reaches a cell."""
    spy_id = _security(db, "SPY", None, kind="etf")
    rta = _security(
        db, "RTA", CIK_A, name="Acme Therapeutics", sector="Pharmaceutical Preparations"
    )
    rtb = _security(db, "RTB", CIK_B)
    ds = [
        d
        for i in range(60)
        if (d := date(2026, 4, 20) + timedelta(days=i)) <= LATER and is_scheduled_day(d)
    ]  # sessions: Mon-Fri minus NYSE closures (Memorial Day) — a vendor serves no holiday bar (F5)
    spy_close = {d: 100.0 + 0.2 * i for i, d in enumerate(ds)}
    rta_close = {d: 10.0 + 0.1 * i + (0.3 if i % 5 == 0 else 0.0) for i, d in enumerate(ds)}
    for d in ds:
        _price(db, spy_id, d, spy_close[d])
        _price(db, rta, d, rta_close[d])
        _price(db, rtb, d, 20.0)
    _8k(db, rta, EARLY_8K, date(2026, 6, 3), CIK_A)
    _8k(db, rta, LATE_8K, date(2026, 6, 10), CIK_A)

    card_a = _card(
        db,
        "Read-Through A",
        [
            BasketMember(
                ticker="RTA",
                role="the name",
                security_id=rta,
                segment="Core",
                context_role=ContextRole.PAYER,
            )
        ],
        backdate_snapshot=True,
    )
    card_b = _card(
        db,
        "Read-Through B",
        [BasketMember(ticker="RTB", role="the name", security_id=rtb, segment="Core")],
        backdate_snapshot=True,
    )
    # the operator tags RTB afterwards: a second roster, taken now (after both as-ofs)
    card_b.basket = [
        BasketMember(
            ticker="RTB",
            role="the name",
            security_id=rtb,
            segment="Core",
            context_role=ContextRole.COUNTER_CASE,
        )
    ]
    thesis_repo.upsert(db, card_b)
    db.commit()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM basket_snapshot WHERE thesis_id = %s", (card_b.id,))
        assert cur.fetchone()["n"] == 2  # the role change is a roster change: a second snapshot

    url = "/dashboards/read-through"
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = client.get(url, params={"asof": PAST.isoformat()}).json()
        later = client.get(url, params={"asof": LATER.isoformat()}).json()
        live = client.get(url, params={"asof": pinned_today.isoformat()}).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    assert (past["clock"], later["clock"], live["clock"]) == ("public", "public", "record")

    # the role set after the as-of does not show: only A's row, and B is listed as holding no role
    for body in (past, later):
        assert [(r["ticker"], r["role"]) for r in body["rows"]] == [("RTA", "payer")]
        assert [c["thesis_name"] for c in body["cards_without_roles"]] == ["Read-Through B"]
        assert (body["cards_counted"], body["cards_with_roles"], body["role_tags"]) == (2, 1, 1)
        assert body["roster_fallback_cards"] == 0  # both cards had a roster snapshot by then
    # today's view reads today's rosters: both rows
    assert sorted((r["ticker"], r["role"]) for r in live["rows"]) == [
        ("RTA", "payer"),
        ("RTB", "counter_case"),
    ]
    assert live["cards_without_roles"] == [] and live["cards_with_roles"] == 2

    # the filing public after the as-of never reaches the row: the earlier view's last filing is the early 8-K
    a_past = past["rows"][0]
    assert a_past["last_filing"]["accession"] == EARLY_8K
    assert (a_past["last_filing"]["date"], a_past["last_filing"]["days_ago"]) == ("2026-06-03", 2)
    assert a_past["last_filing"]["headline"].startswith("8-K · 2.02 results of operations")
    assert a_past["last_filing"]["url"].endswith(f"/{EARLY_8K.replace('-', '')}/")
    assert later["rows"][0]["last_filing"]["accession"] == LATE_8K

    # identity (the master's name, the business type derived on read) and the card's catalysts as of the as-of
    assert (a_past["name"], a_past["business_type"]) == ("Acme Therapeutics", "biotech_pharma")
    assert a_past["cards"][0]["thesis_id"] == str(card_a.id)
    assert (a_past["next_catalyst"]["label"], a_past["next_catalyst"]["days"]) == ("Readout", 15)

    # the tape ends at the as-of: the 7 and 30 trading-day returns on the bars up to PAST, minus SPY's
    upto = [d for d in ds if d <= PAST]
    tape = a_past["tape"]
    assert tape["gap"] is None and tape["last_close"] == PAST.isoformat()

    def ret(cl, n):
        return round((cl[upto[-1]] / cl[upto[-(n + 1)]] - 1) * 100, 2)

    assert tape["ret_7d"] == pytest.approx(ret(rta_close, 7), abs=1e-9)
    assert tape["ret_30d"] == pytest.approx(ret(rta_close, 30), abs=1e-9)
    assert tape["vs_spy_7d"] == pytest.approx(ret(rta_close, 7) - ret(spy_close, 7), abs=0.011)
    assert tape["vs_spy_30d"] == pytest.approx(ret(rta_close, 30) - ret(spy_close, 30), abs=0.011)
    assert past["spy"]["end"] == PAST.isoformat()
    assert past["spy"]["ret_7d"] == pytest.approx(ret(spy_close, 7), abs=1e-9)
    # the later view reads the later bars
    assert later["rows"][0]["tape"]["last_close"] == LATER.isoformat()

    # today's view: the tapes end at the later as-of, a stopped feed, so the flag reaches the price cells: the row
    # stays, names the reason, shows no return
    live_a = next(r for r in live["rows"] if r["ticker"] == "RTA")
    assert live_a["tape"]["gap"] == "flagged" and live_a["tape"]["reason"] == "price feed stopped"
    assert live_a["tape"]["vs_spy_7d"] is None and live_a["tape"]["ret_7d"] is None
    assert sorted(f["ticker"] for f in live["flagged"]) == ["RTA", "RTB"]
