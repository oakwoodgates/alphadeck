"""Chain Map (``docs/dashboards/chain-map.md``): the placement rules on hand-built rosters (each company once per
column, a company in two links in both, the columns in chain order with an old link, No link and the quiet
Context/Bench columns last, a role-tagged name in a real link staying in its column with no rail copy, a role-tagged
name with no real link on its rail, the rail sides and order, one edge per rail node), the glyphs (the 50-day side,
a flagged name grayed and named, an unpriced name without a side, the last-filing age and its tie rule), the counts
and "nothing dropped"; the router off one cached read with its 404; and, DB-backed, a roster row added after a past
as-of not being on the map, and a filing published after it not being the last filing."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import chain_map as router_mod
from dashboards.baskets import CardChoice, CardsRead
from dashboards.cache import CACHE
from dashboards.chain_map import (
    FILING_DAYS,
    LEFT_ROLES,
    NO_LINK,
    RIGHT_ROLES,
    ROLE_ORDER,
    ROLE_SIDE,
    CompanyFacts,
    MapInputs,
    RosterRow,
    chain_map,
    last_filing,
    pct_vs_50d,
    roster_rows,
    side_of,
)
from dashboards.filings import KIND_ORDER, SEC_FILING_KINDS, FilingRow
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import Authorship, ContextRole
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo
from signals.display import sma

ASOF = date(2026, 6, 5)  # a Friday
T1 = uuid.uuid4()
SX, SY, SZ, SP, SF, SO, SN, SC, SB, SR, SD, SS = (uuid.uuid4() for _ in range(12))
MIXED = "mixed-scale price history"

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------

SEGMENTS = (
    Segment(label="Turbines", descriptor="make the power"),
    Segment(label="Cables"),
    Segment(label=CONTEXT_LABEL),
    Segment(label=BENCH_LABEL),
)


def row(
    ticker: str,
    sid: UUID | None,
    segment: str | None = "Turbines",
    role: str | None = None,
    fit: str | None = None,
    authored: str = "system_drafted",
    terms: tuple[str, ...] = (),
) -> RosterRow:
    return RosterRow(
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
        context_role=role,
        thesis_fit=fit,
        authored_by=authored,
        surfaced_terms=terms,
    )


def fr(
    sid: UUID,
    days_before: int,
    kind: str = "8k",
    form: str = "8-K",
    headline: str = "8-K · 9.01",
    accession: str | None = None,
) -> FilingRow:
    return FilingRow(
        security_id=sid,
        ticker="?",
        kind=kind,  # type: ignore[arg-type]
        form=form,
        date=ASOF - timedelta(days=days_before),
        headline=headline,
        accession=accession,
    )


def build(
    roster: list[RosterRow],
    facts: dict[UUID, CompanyFacts] | None = None,
    *,
    flagged: dict[UUID, str] | None = None,
    segments: tuple[Segment, ...] = SEGMENTS,
    fallback: bool = False,
):
    return chain_map(
        MapInputs(
            thesis_id=T1,
            thesis_name="Grid",
            segments=segments,
            roster=tuple(roster),
            facts=facts or {},
            flagged=flagged or {},
            roster_fallback=fallback,
        ),
        asof=ASOF,
    )


def col(page, key: str):
    return next(c for c in page.columns if c.key == key)


def tickers(nodes) -> list[str]:
    return [n.ticker for n in nodes]


def rail(page, side: str):
    return next(r for r in page.rails if r.side == side)


# a card that uses every rule once
BASE_ROWS = [
    row("XXX", SX, "Turbines"),
    row("YYY", SY, "Turbines", role="counter_case"),  # a role in a real link: a tag, no rail copy
    row("XXX", SX, "Cables"),  # XXX sits in two links
    row("ZZZ", SZ, "Cables"),
    row("PAY", SP, CONTEXT_LABEL, role="payer"),  # a silent name with a role: the left rail
    row("FIN", SF, "Old wires", role="financier"),  # a role, only an old link: the left rail
    row("OLD", SO, "Old wires"),
    row("NOL", SN, None),
    row("CTX", SC, CONTEXT_LABEL),  # no role: the quiet Context column
    row("BEN", SB, BENCH_LABEL, role="direct_read"),  # the right rail
    row("UNR", None, "Turbines"),  # no resolved security
    row("RDR", SR, CONTEXT_LABEL, role="counter_case"),  # the right rail, after BEN
    row("DUP", SD, "Turbines"),
    row("DUP", SD, "Turbines"),  # the same company twice in one link: one node
    row("SPN", SS, BENCH_LABEL),
]
BASE_FACTS = {
    SX: CompanyFacts("X Corp", "utilities", 4.2, (fr(SX, 3),)),
    SY: CompanyFacts("Y Corp", "miner", -1.5, ()),
    SZ: CompanyFacts(
        "Z Corp",
        "software_it",
        0.0,
        (
            fr(SZ, 10, "form4", "4", "Jane Roe (CEO): P purchase"),
            fr(SZ, 10, "8k", "8-K", "8-K · 5.02"),
            fr(SZ, 40),
        ),
    ),
}


def base():
    return build(BASE_ROWS, BASE_FACTS)


# ---------------------------------------------------------------------------------------------------------
# the glyph helpers
# ---------------------------------------------------------------------------------------------------------


def test_the_side_of_the_50_day_average():
    assert side_of(None) is None
    assert [side_of(v) for v in (4.2, -0.01, 0.0, -0.0)] == ["above", "below", "at", "at"]


def test_the_50_day_percent_is_read_off_the_real_sma_member():
    """The metric key the page reads is the member's own: a rename would show here, not as a page of blanks."""
    days = [date(2026, 3, 2) + timedelta(days=i) for i in range(60)]
    rising = [{"d": d, "close": 10.0 + i} for i, d in enumerate(days)]
    sig = sma.compute(rising, days[-1])
    # the 50-day average of the last 50 closes (20 … 69) is 44.5, the last close is 69
    assert pct_vs_50d(sig) == pytest.approx((69.0 / 44.5 - 1.0) * 100.0, abs=0.01)
    assert side_of(pct_vs_50d(sig)) == "above"
    falling = [{"d": d, "close": 100.0 - i} for i, d in enumerate(days)]
    assert side_of(pct_vs_50d(sma.compute(falling, days[-1]))) == "below"
    # fewer than 50 bars: the member states no 50-day line, so the page states no side
    assert pct_vs_50d(sma.compute(rising[:30], days[29])) is None
    assert pct_vs_50d(None) is None


def test_the_last_filing_is_the_newest_by_date_then_kind_order():
    a, b = uuid.uuid4(), uuid.uuid4()
    rows = [
        fr(a, 10, "form4", "4", "Jane Roe: P purchase"),
        fr(a, 10, "8k", "8-K", "8-K · 5.02"),
        fr(a, 40, "8k", "8-K", "older"),
    ]
    got = last_filing(rows, ASOF)
    assert (got.date, got.age_days, got.form, got.headline) == (
        ASOF - timedelta(days=10),
        10,
        "8-K",
        "8-K · 5.02",
    )
    assert KIND_ORDER.index("8k") < KIND_ORDER.index("form4")
    # the pick does not depend on the order the rows arrive in
    assert last_filing(list(reversed(rows)), ASOF) == got
    # a row dated after the as-of cannot be read: skipped, never the answer
    future = FilingRow(b, "?", "8k", "8-K", ASOF + timedelta(days=1), "from the future")
    assert last_filing([future], ASOF) is None
    assert last_filing([future, fr(b, 2)], ASOF).age_days == 2
    assert last_filing([], ASOF) is None
    assert last_filing([fr(b, 0)], ASOF).age_days == 0
    assert FILING_DAYS == 365


def test_the_last_filing_is_a_filing_never_a_split_a_catalyst_or_a_stored_or_ratified_fact():
    a = uuid.uuid4()
    own = [  # rows the system holds, all NEWER than the one real filing below
        fr(a, 0, "split", "split", "1-for-10 reverse split"),
        fr(a, 0, "dilution", "dilution", "convertible notes: $350.0M principal"),
        fr(a, 1, "catalyst", "catalyst", "regulatory: FDA decision"),
        fr(a, 1, "ratified", "stored fact", "10-q-cover: shares outstanding 540.6M"),
        fr(a, 2, "ratified", "ratified fact", "shares outstanding 84.2M"),
    ]
    real = fr(a, 30, "8k", "8-K", "8-K · 2.02")
    got = last_filing([*own, real], ASOF)
    assert (got.age_days, got.form, got.headline) == (30, "8-K", "8-K · 2.02")
    assert last_filing(own, ASOF) is None  # nothing but the system's own rows: no filing
    # each SEC kind counts
    for kind, form in (("stake", "SC 13D"), ("form4", "4"), ("form4a", "4/A"), ("revenue", "10-Q")):
        assert last_filing([*own, fr(a, 5, kind, form, "x")], ASOF).form == form
    assert SEC_FILING_KINDS == {"8k", "stake", "form4", "form4a", "revenue"}


# ---------------------------------------------------------------------------------------------------------
# the columns and the nodes
# ---------------------------------------------------------------------------------------------------------


def test_the_columns_are_the_real_links_in_chain_order_then_old_links_no_link_context_and_bench():
    page = base()
    assert [(c.key, c.kind, c.label) for c in page.columns] == [
        ("link:Turbines", "link", "Turbines"),
        ("link:Cables", "link", "Cables"),
        ("old:Old wires", "old_link", "Old wires"),
        ("no_link", "no_link", NO_LINK),
        ("silent:Context", "silent", CONTEXT_LABEL),
        ("silent:Bench", "silent", BENCH_LABEL),
    ]
    assert col(page, "link:Turbines").descriptor == "make the power"
    assert col(page, "link:Cables").descriptor is None
    # only a real link carries a descriptor, and the old link keeps its roster order
    assert col(page, "old:Old wires").descriptor is None


def test_discovered_is_a_link_wherever_it_sits_and_an_empty_link_is_still_a_column():
    segs = (
        Segment(label="Fuel"),
        Segment(label="Discovered"),
        Segment(label="Reactors"),
        Segment(label=CONTEXT_LABEL),
        Segment(label=BENCH_LABEL),
    )
    page = build([row("XXX", SX, "Discovered"), row("YYY", SY, "Reactors")], segments=segs)
    assert [c.label for c in page.columns] == ["Fuel", "Discovered", "Reactors"]
    assert [len(c.nodes) for c in page.columns] == [0, 1, 1]
    assert page.counts.links_today == 3
    # Context and Bench are not links, and with nobody in them they are not columns either
    assert all(c.kind != "silent" for c in page.columns)


def test_a_card_with_no_roster_is_its_links_and_empty_rails():
    page = build([])
    assert [c.label for c in page.columns] == ["Turbines", "Cables"]
    assert all(not c.nodes for c in page.columns)
    assert [(r.side, r.nodes) for r in page.rails] == [("left", ()), ("right", ())]
    assert page.edges == () and page.flagged == ()
    assert page.counts.companies == 0 and page.counts.roles == ()


def test_each_company_is_one_node_per_column_in_roster_order():
    page = base()
    # XXX sits in two links: a node in both; DUP's two rows in one link are one node; roster order inside a column
    assert tickers(col(page, "link:Turbines").nodes) == ["XXX", "YYY", "UNR", "DUP"]
    assert tickers(col(page, "link:Cables").nodes) == ["XXX", "ZZZ"]
    xxx_nodes = [n for c in page.columns for n in c.nodes if n.ticker == "XXX"]
    assert [n.key for n in xxx_nodes] == [f"link:Turbines|{SX}", f"link:Cables|{SX}"]
    assert len({n.key for c in page.columns for n in c.nodes}) == sum(
        len(c.nodes) for c in page.columns
    )
    # both nodes know every link the company sits in
    assert all(n.links == ("Turbines", "Cables") for n in xxx_nodes)
    dup = next(n for n in col(page, "link:Turbines").nodes if n.ticker == "DUP")
    assert dup.links == ("Turbines",)


def test_the_old_link_no_link_and_the_untagged_silent_columns():
    page = base()
    assert tickers(col(page, "old:Old wires").nodes) == ["OLD"]  # FIN has a role: it is on a rail
    assert tickers(col(page, "no_link").nodes) == ["NOL"]
    assert col(page, "no_link").nodes[0].links == (None,)
    assert tickers(col(page, "silent:Context").nodes) == ["CTX"]  # PAY and RDR have roles: on rails
    assert tickers(col(page, "silent:Bench").nodes) == ["SPN"]  # BEN has a role: on a rail
    assert col(page, "silent:Context").nodes[0].links == (CONTEXT_LABEL,)


def test_a_role_tagged_name_in_a_real_link_stays_in_its_column_with_its_role_and_no_rail_copy():
    page = base()
    yyy = next(n for n in col(page, "link:Turbines").nodes if n.ticker == "YYY")
    assert yyy.role == "counter_case"
    on_rails = [n.ticker for r in page.rails for n in r.nodes]
    assert "YYY" not in on_rails
    assert yyy.key not in {e.node_key for e in page.edges}
    # an untagged name carries no role: a tag is never a placeholder
    assert next(n for n in col(page, "link:Cables").nodes if n.ticker == "ZZZ").role is None


def test_a_role_tagged_company_in_a_real_link_and_in_context_is_a_link_node_only():
    """An older snapshot can hold a company in a real link AND in Context. The company has a role: it stays in
    its link column, never also in the Context column (that column holds the names with no role) and never on a
    rail."""
    page = build(
        [row("AAA", SX, "Turbines", role="payer"), row("AAA", SX, CONTEXT_LABEL, role="payer")]
    )
    assert tickers(col(page, "link:Turbines").nodes) == ["AAA"]
    assert all(c.kind != "silent" for c in page.columns)
    assert all(not r.nodes for r in page.rails) and page.edges == ()
    assert page.counts.companies == 1 and page.counts.on_rails == 0


def test_a_company_with_no_role_in_a_real_link_and_in_context_is_a_node_in_both():
    page = build([row("AAA", SX, "Turbines"), row("AAA", SX, CONTEXT_LABEL)])
    assert tickers(col(page, "link:Turbines").nodes) == ["AAA"]
    assert tickers(col(page, "silent:Context").nodes) == ["AAA"]
    assert page.counts.companies == 1 and page.counts.untagged_silent == 1


def test_the_rails_hold_the_role_tagged_names_with_no_real_link_by_side_and_role_order():
    page = base()
    left, right = rail(page, "left"), rail(page, "right")
    assert (left.label, right.label) == ("what pays and funds", "what reads across")
    assert tickers(left.nodes) == ["PAY", "FIN"]  # payer, then financier
    assert tickers(right.nodes) == ["BEN", "RDR"]  # direct read, then counter-case
    assert [n.role for n in left.nodes + right.nodes] == [
        "payer",
        "financier",
        "direct_read",
        "counter_case",
    ]
    assert [n.key for n in left.nodes] == [f"rail:left|{SP}", f"rail:left|{SF}"]
    # a rail node knows where it sits on the card
    assert left.nodes[0].links == (CONTEXT_LABEL,) and left.nodes[1].links == ("Old wires",)


def test_a_role_sorts_ahead_of_roster_order_and_roster_order_holds_inside_a_role():
    rows = [
        row("PB", SB, CONTEXT_LABEL, role="benchmark"),
        row("PA", SC, CONTEXT_LABEL, role="participant"),
        row("CC", SR, CONTEXT_LABEL, role="counter_case"),
        row("DR", SD, CONTEXT_LABEL, role="direct_read"),
        row("P2", SS, BENCH_LABEL, role="participant"),
        row("FN", SF, CONTEXT_LABEL, role="financier"),
        row("PY", SP, CONTEXT_LABEL, role="payer"),
        row("P1", SO, CONTEXT_LABEL, role="payer"),
    ]
    page = build(rows)
    assert tickers(rail(page, "left").nodes) == ["PY", "P1", "FN"]
    assert tickers(rail(page, "right").nodes) == ["DR", "CC", "PA", "P2", "PB"]
    assert [(e.side, e.role) for e in page.edges] == [
        ("left", "payer"),
        ("left", "payer"),
        ("left", "financier"),
        ("right", "direct_read"),
        ("right", "counter_case"),
        ("right", "participant"),
        ("right", "participant"),
        ("right", "benchmark"),
    ]


def test_one_edge_per_rail_node_left_rail_first_and_the_edge_names_the_rail_not_a_target():
    page = base()
    rail_nodes = [n for r in page.rails for n in r.nodes]
    assert [e.node_key for e in page.edges] == [n.key for n in rail_nodes]
    assert [(e.role, e.side) for e in page.edges] == [
        ("payer", "left"),
        ("financier", "left"),
        ("direct_read", "right"),
        ("counter_case", "right"),
    ]
    # the wire has no link and no company for an edge to land on: only the node it leaves, its role and its rail
    assert set(vars(page.edges[0])) == {"node_key", "role", "side"}


def test_every_context_role_is_placed_on_exactly_one_rail():
    assert set(ROLE_SIDE) == {r.value for r in ContextRole}
    assert set(LEFT_ROLES).isdisjoint(RIGHT_ROLES)
    assert len(ROLE_ORDER) == len(set(ROLE_ORDER)) == len(ContextRole)
    assert [ROLE_SIDE[r] for r in ROLE_ORDER] == ["left"] * len(LEFT_ROLES) + ["right"] * len(
        RIGHT_ROLES
    )


def test_a_row_with_no_resolved_security_is_a_plain_node_keyed_by_its_ticker_and_counted():
    page = base()
    unr = next(n for n in col(page, "link:Turbines").nodes if n.ticker == "UNR")
    assert unr.company_key == "ticker:UNR" and unr.security_id is None
    assert (
        unr.name,
        unr.business_type,
        unr.side_50d,
        unr.pct_vs_50d,
        unr.last_filing,
        unr.flag,
    ) == (
        None,
        None,
        None,
        None,
        None,
        None,
    )
    assert page.counts.unresolved_rows == 1 and page.counts.resolved_rows == len(BASE_ROWS) - 1
    # a role-tagged unresolved row stands on its rail like any other company
    rails = build(
        [
            row("nr", None, CONTEXT_LABEL, role="payer"),
            row("NR ", None, CONTEXT_LABEL, role="payer"),
        ]
    )
    assert tickers(rail(rails, "left").nodes) == ["nr"]  # one company: ticker:NR twice


# ---------------------------------------------------------------------------------------------------------
# the glyphs on a node
# ---------------------------------------------------------------------------------------------------------


def test_a_node_carries_its_business_type_50_day_side_and_last_filing_age():
    page = base()
    nodes = {n.ticker: n for n in col(page, "link:Turbines").nodes + col(page, "link:Cables").nodes}
    x, y, z = nodes["XXX"], nodes["YYY"], nodes["ZZZ"]
    assert (x.name, x.business_type, x.side_50d, x.pct_vs_50d) == (
        "X Corp",
        "utilities",
        "above",
        4.2,
    )
    assert (x.last_filing.age_days, x.last_filing.form) == (3, "8-K")
    assert (y.business_type, y.side_50d, y.pct_vs_50d, y.last_filing) == (
        "miner",
        "below",
        -1.5,
        None,
    )
    # exactly on the line: "at"; two facts on the newest day: the 8-K by kind order
    assert (z.side_50d, z.pct_vs_50d) == ("at", 0.0)
    assert (z.last_filing.age_days, z.last_filing.headline) == (10, "8-K · 5.02")
    # a company the master/readers say nothing about is a node with blanks, never a missing node
    old = col(page, "old:Old wires").nodes[0]
    assert (old.name, old.business_type, old.side_50d, old.last_filing) == (None, None, None, None)


def test_an_unpriced_name_has_no_side_and_a_name_with_no_filing_has_no_age():
    page = build(
        [row("NEW", SX), row("BAR", SY)],
        {SX: CompanyFacts("New Co", None, None, ()), SY: CompanyFacts("Bar Co", "bank", 2.0, ())},
    )
    new, bar = col(page, "link:Turbines").nodes
    assert (new.side_50d, new.pct_vs_50d, new.last_filing, new.business_type) == (
        None,
        None,
        None,
        None,
    )
    assert (bar.side_50d, bar.last_filing) == ("above", None)


def test_a_flagged_name_is_grayed_and_named_with_no_50_day_side_but_stays_on_the_map():
    page = build(
        [row("XXX", SX), row("YYY", SY, role="payer"), row("FLG", SZ, CONTEXT_LABEL, role="payer")],
        {
            SX: CompanyFacts("X Corp", "utilities", 4.2, (fr(SX, 3),)),
            SY: CompanyFacts("Y Corp", "miner", 7.0, ()),
            SZ: CompanyFacts("F Corp", "bank", 9.0, (fr(SZ, 5),)),
        },
        flagged={SX: MIXED, SZ: "price feed stopped"},
    )
    x = col(page, "link:Turbines").nodes[0]
    assert (x.flag, x.side_50d, x.pct_vs_50d) == (MIXED, None, None)
    assert x.last_filing.age_days == 3  # a filing is not a price reading: it still shows
    assert x.business_type == "utilities"
    flg = rail(page, "left").nodes[0]
    assert flg.ticker == "FLG" and (flg.flag, flg.side_50d) == ("price feed stopped", None)
    # an unflagged name keeps its side; the flagged ones are listed by ticker with their reasons
    assert col(page, "link:Turbines").nodes[1].side_50d == "above"
    assert [(f.ticker, f.reason, f.security_id) for f in page.flagged] == [
        ("FLG", "price feed stopped", SZ),
        ("XXX", MIXED, SX),
    ]
    assert page.counts.with_side == 1 and page.counts.without_side == 2


def test_the_description_is_carried_with_its_author_only_when_there_is_one():
    page = build(
        [
            row(
                "XXX",
                SX,
                fit="makes the turbines",
                authored="operator_edited",
                terms=("turbine", "HVDC"),
            ),
            row("YYY", SY, fit="   ", authored="system_drafted"),
            row("ZZZ", SZ, fit=None, authored="system_drafted"),
        ]
    )
    x, y, z = col(page, "link:Turbines").nodes
    assert (x.thesis_fit, x.authored_by, x.matched_terms) == (
        "makes the turbines",
        "operator_edited",
        ("turbine", "HVDC"),
    )
    # a blank description is no description: no prose, and no author to credit for it
    assert (y.thesis_fit, y.authored_by, z.thesis_fit, z.authored_by) == (None, None, None, None)


def test_a_company_in_two_links_carries_each_links_own_description():
    """A name the draft puts in N links is N rows, each with its own prose: the node shows ITS column's."""
    page = build(
        [
            row("XXX", SX, "Turbines", fit="blades", authored="system_drafted"),
            row("XXX", SX, "Cables", fit="the cable maker", authored="operator_edited"),
        ]
    )
    a = col(page, "link:Turbines").nodes[0]
    b = col(page, "link:Cables").nodes[0]
    assert (a.thesis_fit, a.authored_by) == ("blades", "system_drafted")
    assert (b.thesis_fit, b.authored_by) == ("the cable maker", "operator_edited")


# ---------------------------------------------------------------------------------------------------------
# the counts, and nothing dropped
# ---------------------------------------------------------------------------------------------------------


def test_the_counts_on_hand_computed_numbers():
    c = base().counts
    assert (c.links_today, c.old_links) == (2, 1)
    # 15 rows; UNR has no security; PAY, CTX, BEN, RDR and SPN sit in Context or Bench
    assert (c.roster_rows, c.resolved_rows, c.unresolved_rows, c.silent_rows) == (15, 14, 1, 5)
    # distinct companies: XXX YYY ZZZ PAY FIN OLD NOL CTX BEN UNR RDR DUP SPN
    assert (c.companies, c.on_rails, c.untagged_silent) == (13, 4, 2)
    assert [(r.role, r.names) for r in c.roles] == [
        ("payer", 1),
        ("financier", 1),
        ("direct_read", 1),
        (
            "counter_case",
            2,
        ),  # YYY in a link and RDR on a rail: the role counts the company either way
    ]
    # a side: XXX, YYY, ZZZ (exactly on the line is a side); a filing: XXX, ZZZ
    assert (c.with_side, c.without_side) == (3, 10)
    assert (c.with_filing, c.without_filing) == (2, 11)


def test_nothing_is_dropped_every_roster_company_is_on_the_chain_a_rail_or_a_silent_column():
    page = base()
    drawn = {n.company_key for c in page.columns for n in c.nodes} | {
        n.company_key for r in page.rails for n in r.nodes
    }
    expected = {str(r.security_id) if r.security_id else f"ticker:{r.ticker}" for r in BASE_ROWS}
    assert drawn == expected and len(drawn) == page.counts.companies
    # and a role-tagged company is drawn once in total: on a rail, or in its link column(s), never both
    rail_keys = {n.company_key for r in page.rails for n in r.nodes}
    column_keys = {n.company_key for c in page.columns for n in c.nodes}
    assert rail_keys.isdisjoint(column_keys)
    assert len(rail_keys) == page.counts.on_rails


def test_the_roster_note_and_the_card_come_through():
    page = build([row("XXX", SX)], fallback=True)
    assert page.roster_fallback is True and page.asof == ASOF
    assert (page.thesis_id, page.thesis_name) == (T1, "Grid")
    assert build([row("XXX", SX)]).roster_fallback is False


def test_the_roster_rows_pair_the_links_and_roles_with_the_prose_row_for_row():
    card = Thesis(
        id=T1,
        name="Grid",
        narrative="x",
        segments=list(SEGMENTS),
        basket=[
            BasketMember(
                ticker="XXX",
                role="r",
                security_id=SX,
                segment="Turbines",
                thesis_fit="blades",
                authored_by=Authorship.OPERATOR_EDITED,
                surfaced_terms=["turbine"],
            ),
            BasketMember(
                ticker="PAY",
                role="r",
                security_id=SP,
                segment=CONTEXT_LABEL,
                context_role=ContextRole.PAYER,
            ),
        ],
    )
    from dashboards.baskets import memberships_of

    rows = roster_rows(memberships_of([card]), card.basket)
    assert [(r.ticker, r.segment, r.silent, r.context_role) for r in rows] == [
        ("XXX", "Turbines", False, None),
        ("PAY", CONTEXT_LABEL, True, "payer"),
    ]
    assert (rows[0].thesis_fit, rows[0].authored_by, rows[0].surfaced_terms) == (
        "blades",
        "operator_edited",
        ("turbine",),
    )
    assert (rows[1].thesis_fit, rows[1].authored_by) == (None, "system_drafted")
    with pytest.raises(ValueError):  # the two walks must agree; a mismatch is never papered over
        roster_rows(memberships_of([card])[:1], card.basket)


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy reads stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0, "card_reads": 0}
    other = uuid.uuid4()

    def fake_cards(conn, asof):
        seen["card_reads"] += 1
        return CardsRead(
            live_cards=3,
            not_yet_created=1,
            cards=(CardChoice(T1, "Grid", 2), CardChoice(other, "Batteries", 1)),
        )

    def fake_read(conn, thesis_id, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (thesis_id, asof, known_at, clock)
        return base()

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_cards", fake_cards)
    monkeypatch.setattr(router_mod, "read_page", fake_read)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_the_page_off_one_cached_read_on_the_requests_clock(stub_client):
    client, seen = stub_client
    url = f"/dashboards/chain-map/{T1}"
    body = client.get(url, params={"asof": "2026-06-05"}).json()
    assert body["cache"] == "computed" and body["clock"] == "public" and body["filing_days"] == 365
    thesis_id, asof, known_at, clock = seen["view"]
    assert thesis_id == T1 and asof == ASOF and clock == "public" and known_at is not None
    again = client.get(url, params={"asof": "2026-06-05"}).json()
    assert again["cache"] == "hit" and again["columns"] == body["columns"]
    assert seen["reads"] == 1 and seen["card_reads"] == 1

    # today's view is the live read, and a new clock is a new read
    live = client.get(url, params={"asof": market_today().isoformat()}).json()
    assert seen["view"][2:] == (None, "record") and seen["reads"] == 2 and live["clock"] == "record"


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(f"/dashboards/chain-map/{T1}", params={"asof": "2026-06-05"}).json()
    assert body["asof"] == "2026-06-05" and body["thesis_name"] == "Grid"
    assert [c["key"] for c in body["columns"]] == [
        "link:Turbines",
        "link:Cables",
        "old:Old wires",
        "no_link",
        "silent:Context",
        "silent:Bench",
    ]
    turbines = body["columns"][0]
    assert (turbines["kind"], turbines["descriptor"]) == ("link", "make the power")
    xxx = turbines["nodes"][0]
    assert (
        xxx["ticker"] == "XXX"
        and xxx["security_id"] == str(SX)
        and xxx["links"] == ["Turbines", "Cables"]
    )
    assert (xxx["business_type"], xxx["side_50d"], xxx["pct_vs_50d"]) == ("utilities", "above", 4.2)
    assert xxx["last_filing"] == {
        "date": "2026-06-02",
        "age_days": 3,
        "form": "8-K",
        "headline": "8-K · 9.01",
    }
    assert xxx["role"] is None and xxx["flag"] is None and xxx["matched_terms"] == []
    yyy = turbines["nodes"][1]
    assert yyy["role"] == "counter_case"
    assert [r["side"] for r in body["rails"]] == ["left", "right"]
    assert [n["ticker"] for n in body["rails"][0]["nodes"]] == ["PAY", "FIN"]
    assert [(e["role"], e["side"]) for e in body["edges"]][0] == ("payer", "left")
    assert body["counts"]["companies"] == 13
    assert body["counts"]["roles"][0] == {"role": "payer", "names": 1}
    assert body["flagged"] == [] and body["roster_fallback"] is False


def test_the_router_404s_a_card_not_counted_at_the_asof(stub_client):
    client, seen = stub_client
    r = client.get(f"/dashboards/chain-map/{uuid.uuid4()}", params={"asof": "2026-06-05"})
    assert r.status_code == 404 and seen["reads"] == 0


def test_the_picker_list_is_cached(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/chain-map/cards", params={"asof": "2026-06-05"}).json()
    assert [c["name"] for c in body["cards"]] == [
        "Grid",
        "Batteries",
    ]  # the stub's own order, served as read
    assert (body["live_cards"], body["not_yet_created"], body["cache"]) == (3, 1, "computed")
    again = client.get("/dashboards/chain-map/cards", params={"asof": "2026-06-05"}).json()
    assert again["cache"] == "hit" and seen["card_reads"] == 1


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the roster and the facts are as of the past view (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

PAST = date(2026, 6, 5)  # a past as-of
LATER = date(2026, 6, 12)  # a later past as-of, after every "late" fact below
INGESTED = datetime(
    2026, 9, 1, tzinfo=timezone.utc
)  # our own ingest instant: the public clock must not care
CIK = "0000876544"
EARLY_8K, LATE_8K = "0000876544-26-000101", "0000876544-26-000102"


def _security(db, ticker: str, cik: str | None) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, 'equity', %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
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
            "recorded_at": datetime.combine(d, time.min, tzinfo=timezone.utc),
        },
    )


def _eight_k(db, sid: UUID, accession: str, filed: date) -> None:
    append_fact(
        db,
        "fact_corporate_event",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "form": "8-K",
            "items": ["5.02", "9.01"],
            "accession": accession,
            "filed": filed,
            "source_ref": f"https://www.sec.gov/Archives/edgar/data/876544/{accession.replace('-', '')}/",
            "valid_from": filed,
            "recorded_at": INGESTED,
        },
    )


def _pin_latest_snapshot(db, thesis_id: UUID, when: datetime) -> None:
    """``upsert`` stamps a snapshot ``now()``; pin the newest one to a controlled instant."""
    with db.cursor() as cur:
        cur.execute(
            "UPDATE basket_snapshot SET taken_at = %s WHERE id = ("
            "  SELECT id FROM basket_snapshot WHERE thesis_id = %s ORDER BY taken_at DESC, id DESC LIMIT 1)",
            (when, thesis_id),
        )
    db.commit()


def _backdate(db, thesis_id: UUID) -> None:
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis_id))
    db.commit()


def _get(client: TestClient, thesis_id: UUID, asof: date) -> dict:
    r = client.get(f"/dashboards/chain-map/{thesis_id}", params={"asof": asof.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def test_a_roster_row_added_after_a_past_asof_is_not_on_the_map(db):
    """Two promotes of one card: the first roster (AAA in Core) is snapshotted on June 1, the second (AAA, plus a
    payer BBB in Context and CCC in Core) on June 10. The map at June 5 draws the first roster only: no BBB on a
    rail, no CCC in the column, no edge, and the counts say so. At June 12 it draws all three; the live view
    (today) draws the current roster. The roster note says nothing at either past as-of (a snapshot stood in).
    """
    a, b, c = _security(db, "CMA", CIK), _security(db, "CMB", None), _security(db, "CMC", None)
    first = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Chain Map fixture",
        narrative="x",
        segments=[Segment(label="Core"), Segment(label=CONTEXT_LABEL), Segment(label=BENCH_LABEL)],
        basket=[BasketMember(ticker="CMA", role="the name", security_id=a, segment="Core")],
    )
    thesis_repo.upsert(db, first)
    db.commit()
    _pin_latest_snapshot(db, first.id, datetime(2026, 6, 1, tzinfo=timezone.utc))
    second = first.model_copy(
        update={
            "basket": [
                first.basket[0],
                BasketMember(
                    ticker="CMB",
                    role="the payer",
                    security_id=b,
                    segment=CONTEXT_LABEL,
                    context_role=ContextRole.PAYER,
                ),
                BasketMember(ticker="CMC", role="the name", security_id=c, segment="Core"),
            ]
        }
    )
    thesis_repo.upsert(db, second)
    db.commit()
    _pin_latest_snapshot(db, first.id, datetime(2026, 6, 10, tzinfo=timezone.utc))
    _backdate(db, first.id)

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = _get(client, first.id, PAST)
        later = _get(client, first.id, LATER)
        live = _get(client, first.id, market_today())
        missing = client.get(
            f"/dashboards/chain-map/{uuid.uuid4()}", params={"asof": PAST.isoformat()}
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    def drawn(body: dict) -> list[str]:
        return sorted(
            n["ticker"]
            for n in [n for c in body["columns"] for n in c["nodes"]]
            + [n for r in body["rails"] for n in r["nodes"]]
        )

    assert missing.status_code == 404
    assert drawn(past) == ["CMA"]
    assert [n["ticker"] for r in past["rails"] for n in r["nodes"]] == [] and past["edges"] == []
    assert (
        past["counts"]["roster_rows"],
        past["counts"]["companies"],
        past["counts"]["roles"],
    ) == (1, 1, [])
    assert past["roster_fallback"] is False and past["clock"] == "public"

    assert drawn(later) == ["CMA", "CMB", "CMC"]
    assert [n["ticker"] for n in later["rails"][0]["nodes"]] == ["CMB"]
    assert [(e["role"], e["side"]) for e in later["edges"]] == [("payer", "left")]
    core = next(col_ for col_ in later["columns"] if col_["key"] == "link:Core")
    assert [n["ticker"] for n in core["nodes"]] == ["CMA", "CMC"]
    assert later["counts"]["roles"] == [{"role": "payer", "names": 1}]

    assert drawn(live) == ["CMA", "CMB", "CMC"] and live["clock"] == "record"


def test_a_filing_public_after_a_past_asof_is_not_the_last_filing_and_the_side_reads_the_past_tape(
    db,
):
    """CMA has an early 8-K (filed June 3, public by the first as-of) and a late one (June 10, public only by the
    second), both INGESTED in September: the public clock judges each on its own filing date. Fifty-five weekday
    bars climb to the as-of, so CMA is above its 50-day average. The map at June 5 names the early 8-K at age 2
    and never the late one; at June 12 the late one at age 2. A company with no bars has no side."""
    a, b = _security(db, "CMA", CIK), _security(db, "CMB", None)
    days = [
        d for d in (date(2026, 3, 23) + timedelta(days=i) for i in range(82)) if d.weekday() < 5
    ]
    days = [d for d in days if d <= LATER]
    for i, d in enumerate(days):
        _price(db, a, d, 20.0 + 0.1 * i)
    _eight_k(db, a, EARLY_8K, date(2026, 6, 3))
    _eight_k(db, a, LATE_8K, date(2026, 6, 10))
    card = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Chain Map glyphs",
        narrative="x",
        segments=[Segment(label="Core"), Segment(label=CONTEXT_LABEL), Segment(label=BENCH_LABEL)],
        basket=[
            BasketMember(ticker="CMA", role="the name", security_id=a, segment="Core"),
            BasketMember(ticker="CMB", role="the name", security_id=b, segment="Core"),
        ],
    )
    thesis_repo.upsert(db, card)
    db.commit()
    _backdate(db, card.id)

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = _get(client, card.id, PAST)
        later = _get(client, card.id, LATER)
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    def node(body: dict, ticker: str) -> dict:
        return next(n for c in body["columns"] for n in c["nodes"] if n["ticker"] == ticker)

    pa, la = node(past, "CMA"), node(later, "CMA")
    assert pa["last_filing"]["age_days"] == 2 and pa["last_filing"]["date"] == "2026-06-03"
    assert pa["last_filing"]["form"] == "8-K" and "5.02" in pa["last_filing"]["headline"]
    assert la["last_filing"]["age_days"] == 2 and la["last_filing"]["date"] == "2026-06-10"
    # the side comes off the tape as it stood: bars up to the as-of, and only the bars public by then
    assert pa["side_50d"] == "above" and pa["pct_vs_50d"] > 0 and pa["flag"] is None
    assert la["side_50d"] == "above"
    assert pa["pct_vs_50d"] != la["pct_vs_50d"]  # the later view read the next five bars
    # a name with no bars and no filing is on the map with blank glyphs, counted
    pb = node(past, "CMB")
    assert (pb["side_50d"], pb["last_filing"]) == (None, None)
    assert (past["counts"]["with_side"], past["counts"]["without_side"]) == (1, 1)
    assert (past["counts"]["with_filing"], past["counts"]["without_filing"]) == (1, 1)
    assert (
        node(past, "CMA")["business_type"] is None
    )  # no sector stored: unclassified, never a guess
