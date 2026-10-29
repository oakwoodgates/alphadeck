"""Peer Table (``docs/dashboards/peer-table.md``): the cap rule (known / unread / NULL ADS ratio, a split between the
count and the close, awaiting price, awaiting shares, a flagged price history), the tiers, the YoY rule, runway
(cash-positive), overhang, purity, the tape against SPY, the insider cell, the once-per-company fold with its chips,
the scopes and who is in each, the groups (their order and the medians and n's on hand-computed numbers), the
coverage by column and reason, a flagged name grayed and named; the router off one cached read per scope with its
404; and, DB-backed, a fact published after a past as-of never reaching a cell."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import peer_table as router_mod
from dashboards.baskets import Membership
from dashboards.cache import CACHE
from dashboards.peer_table import (
    COLUMNS,
    CardRef,
    CompanyReads,
    Identity,
    PeerInputs,
    ScopeBase,
    cap_cell,
    company_row,
    coverage,
    group_stats,
    identities,
    insider_cell,
    metric_cell,
    overhang_cell,
    peer_table,
    purity_cell,
    revenue_cells,
    runway_cell,
    scope_present,
    scope_rows,
    scope_sids,
    scopes,
    spy_cell,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.market_cap import CapTier
from domain.market_time import market_today
from domain.splits import ListedSplit
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from ingest.edgar.converts import ConvertTerms, ingest_convert_terms
from repositories import thesis_repo
from signals.display import insider_flow, range52w, rvol, sma, trailing_returns, volume_regime
from signals.display.base import DisplayBasis, DisplayMetric, DisplaySignal

UTC = timezone.utc
ASOF = date(2026, 10, 28)
T1, T2, T3 = (uuid.uuid4() for _ in range(3))
S1, S2, S3, S4, S5, S6 = (uuid.uuid4() for _ in range(6))
MIXED = "mixed-scale price history"
AAA, BBB, CCC, DDD = S1, S2, S3, S4


# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def sig(kind: str, end: date | None = ASOF, **metrics) -> DisplaySignal:
    """A display member's output with the named metrics: a number, or ``(None, "note")`` for a stated gap."""
    ms = []
    for key, v in metrics.items():
        value, note = v if isinstance(v, tuple) else (v, None)
        ms.append(DisplayMetric(key=key, label=key, value=value, note=note))
    return DisplaySignal(
        kind=kind,
        label=kind,
        metrics=ms,
        basis=DisplayBasis(source="fact_price_eod", window_end=end),
    )


def ms(
    ticker: str,
    sid: UUID | None,
    segment: str | None = "Turbines",
    tid: UUID = T1,
    name: str = "Grid",
    role: str | None = None,
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


def ident(
    name: str | None = None, bt: str | None = "miner", ss: str | None = "materials", royalty=False
):
    return Identity(name=name, business_type=bt, supersector=ss, royalty=royalty)


def shares_fact(shares=10_000_000, valid_from=date(2026, 5, 10), status=None, ratio=None):
    return {
        "shares": shares,
        "valid_from": valid_from,
        "ads_ratio": ratio,
        "ads_ratio_status": status,
    }


def rev(period_end: date, value: float, filed: date | None = None):
    return {"period_end": period_end, "value": value, "valid_from": filed or period_end}


CARDS = (
    CardRef(T1, "Grid", ("Turbines", "Cables", CONTEXT_LABEL, BENCH_LABEL)),
    CardRef(T2, "Batteries"),
)


def base(memberships, identity=None, cards=CARDS, **over) -> ScopeBase:
    args = dict(
        live_cards=4,
        cards_not_yet_created=1,
        roster_fallback_cards=2,
        cards=cards,
        memberships=tuple(memberships),
        identity=identity or {},
    )
    return ScopeBase(**{**args, **over})


SPY = sig("trailing_returns", ret_7d=1.0, ret_30d=4.0)


def table(memberships, identity, reads, scope="business_type", key="miner", **over):
    inputs = PeerInputs(base(memberships, identity), reads, over.pop("spy", SPY))
    return peer_table(inputs, scope, key, ASOF)


def company(page, sid):
    (c,) = [c for c in page.companies if c.security_id == sid]
    return c


# ---------------------------------------------------------------------------------------------------------
# the approximate market cap (the Workbench's rule, copied)
# ---------------------------------------------------------------------------------------------------------

CLOSE = (date(2026, 10, 28), 25.0)


def test_the_cap_is_the_latest_close_times_the_latest_shares_and_gets_its_tier():
    c = cap_cell(CompanyReads(close=CLOSE, shares=shares_fact(10_000_000)))
    assert (c.value, c.tier, c.gap) == (250_000_000.0, CapTier.MICRO, None)
    assert (c.close, c.close_date, c.shares, c.shares_date) == (
        25.0,
        date(2026, 10, 28),
        10_000_000.0,
        date(2026, 5, 10),
    )
    assert (c.ads_ratio, c.split_factor) == (None, None)
    # $300M exactly is small: the higher band starts at its floor
    assert cap_cell(CompanyReads(close=CLOSE, shares=shares_fact(12_000_000))).tier is CapTier.SMALL


def test_a_null_ads_status_is_not_applicable_and_counts_one_to_one():
    # legacy rows and every domestic name carry NULL / NULL: a NULL-means-suppress reading would blank every cap
    c = cap_cell(CompanyReads(close=CLOSE, shares=shares_fact(8_000_000, status=None, ratio=None)))
    assert c.value == 200_000_000.0 and c.gap is None


def test_a_known_ads_ratio_divides_the_ordinary_count():
    # 1,000,000,000 ordinary shares, 5 per ADS, an ADS at $10 -> $2B: the mid band starts there
    c = cap_cell(
        CompanyReads(
            close=(CLOSE[0], 10.0), shares=shares_fact(1_000_000_000, status="known", ratio=5)
        )
    )
    assert (c.value, c.tier, c.ads_ratio) == (2_000_000_000.0, CapTier.MID, 5)


def test_an_unread_ads_ratio_withholds_the_cap_it_is_never_guessed_at_one_to_one():
    c = cap_cell(
        CompanyReads(close=CLOSE, shares=shares_fact(1_000_000_000, status="unread", ratio=None))
    )
    assert (c.value, c.tier, c.gap) == (None, None, "ads_unread")
    assert c.shares == 1_000_000_000.0  # the count on file stays visible


@pytest.mark.parametrize("ratio", [None, 0, 0.5])
def test_a_known_status_with_a_missing_or_absurd_ratio_is_a_corrupt_pairing_treated_as_unread(
    ratio,
):
    c = cap_cell(
        CompanyReads(close=CLOSE, shares=shares_fact(10_000_000, status="known", ratio=ratio))
    )
    assert c.gap == "ads_unread" and c.value is None


def test_no_price_is_awaiting_price_and_no_shares_is_awaiting_shares_and_neither_is_awaiting_price():
    price_only = cap_cell(CompanyReads(close=CLOSE))
    assert (price_only.gap, price_only.value, price_only.close) == ("no_shares", None, 25.0)
    shares_only = cap_cell(CompanyReads(shares=shares_fact()))
    assert (shares_only.gap, shares_only.value, shares_only.shares) == (
        "no_price",
        None,
        10_000_000.0,
    )
    assert cap_cell(CompanyReads()).gap == "no_price"


def test_a_flagged_price_history_withholds_the_cap_and_names_the_reason():
    c = cap_cell(CompanyReads(flag=MIXED, close=CLOSE, shares=shares_fact()))
    assert (c.gap, c.note, c.value, c.tier) == ("flagged", MIXED, None, None)


def test_a_split_between_the_count_and_the_close_puts_the_count_on_the_closes_scale():
    """A 1-for-16 reverse split after the count's date: 160M shares were counted before it, the close is post-split,
    so the count is divided by 16 (never a pre-split count times a post-split price: a x16 error).
    """
    reverse = ListedSplit(
        key_date=date(2026, 2, 20),
        effective_date=date(2026, 3, 1),
        ratio_from=16,
        ratio_to=1,
        status="confirmed",
    )
    reads = CompanyReads(
        close=(date(2026, 10, 28), 20.0),
        shares=shares_fact(160_000_000, valid_from=date(2026, 1, 10)),
        splits=(reverse,),
    )
    c = cap_cell(reads)
    assert c.value == 200_000_000.0 and c.tier is CapTier.MICRO  # 160M / 16 * $20, not $3.2B
    assert c.split_factor == pytest.approx(1 / 16)
    # a count dated ON or after the split is already post-split: untouched
    after = CompanyReads(
        close=(date(2026, 10, 28), 20.0),
        shares=shares_fact(10_000_000, valid_from=date(2026, 3, 1)),
        splits=(reverse,),
    )
    assert cap_cell(after).value == 200_000_000.0 and cap_cell(after).split_factor is None
    # a split that is only a candidate is not in force
    candidate = ListedSplit(date(2026, 2, 20), date(2026, 3, 1), 16, 1, "needs_review")
    unreviewed = CompanyReads(close=reads.close, shares=reads.shares, splits=(candidate,))
    assert (
        cap_cell(unreviewed).value == 3_200_000_000.0 and cap_cell(unreviewed).split_factor is None
    )


def test_a_zero_count_or_close_is_not_a_cap():
    c = cap_cell(CompanyReads(close=CLOSE, shares=shares_fact(0)))
    assert (c.gap, c.value, c.tier) == ("no_cap", None, None) and c.note


# ---------------------------------------------------------------------------------------------------------
# revenue and YoY
# ---------------------------------------------------------------------------------------------------------


def test_revenue_is_the_latest_period_and_yoy_is_against_the_same_period_a_year_earlier():
    rows = [
        rev(date(2025, 3, 31), 8_000_000, date(2025, 5, 8)),
        rev(date(2025, 6, 30), 9_000_000, date(2025, 8, 7)),
        rev(date(2026, 3, 31), 10_000_000, date(2026, 5, 7)),
        rev(date(2026, 6, 30), 13_500_000, date(2026, 8, 6)),
    ]
    r, y = revenue_cells(rows)
    assert (r.value, r.period_end, r.gap) == (13_500_000.0, date(2026, 6, 30), None)
    assert (y.value, y.prior_value, y.prior_period_end, y.gap) == (
        50.0,
        9_000_000.0,
        date(2025, 6, 30),
        None,
    )


def test_a_52_week_filers_period_end_drift_of_a_few_days_still_finds_its_prior_year():
    rows = [rev(date(2025, 6, 28), 100.0), rev(date(2026, 7, 4), 90.0)]  # 6 days off a year
    _, y = revenue_cells(rows)
    assert y.value == -10.0 and y.prior_period_end == date(2025, 6, 28)


def test_no_prior_year_row_in_the_read_is_a_blank_yoy_with_a_stated_revenue_never_zero():
    r, y = revenue_cells([rev(date(2026, 6, 30), 5_000_000)])
    assert r.value == 5_000_000.0 and r.gap is None
    assert (y.value, y.gap) == (None, "no_prior_year")
    # a row a year earlier but 15 days off is not the same period
    _, off = revenue_cells([rev(date(2025, 6, 15), 100.0), rev(date(2026, 6, 30), 120.0)])
    assert off.gap == "no_prior_year"
    # a prior year of zero revenue has no percent change
    _, zero = revenue_cells([rev(date(2025, 6, 30), 0.0), rev(date(2026, 6, 30), 120.0)])
    assert zero.gap == "no_prior_year"


def test_no_revenue_facts_blank_both_cells():
    r, y = revenue_cells([])
    assert (r.gap, y.gap, r.value, y.value) == ("no_revenue", "no_revenue", None, None)
    assert revenue_cells([{"period_end": None, "value": 1.0}])[0].gap == "no_revenue"


# ---------------------------------------------------------------------------------------------------------
# runway, overhang, purity
# ---------------------------------------------------------------------------------------------------------


def burn(cash: float, quarterly: float, valid_from=date(2026, 5, 10)):
    return {"cash_usd": cash, "quarterly_burn_usd": quarterly, "valid_from": valid_from}


def test_runway_is_cash_over_the_monthly_burn_in_months():
    c = runway_cell(burn(12_000_000, 3_000_000))
    assert (c.months, c.cash_positive, c.gap, c.as_of) == (12.0, False, None, date(2026, 5, 10))
    assert runway_cell(burn(10_000_000, 4_000_000)).months == 7.5  # 10M / (4M / 3), one decimal


@pytest.mark.parametrize("quarterly", [0, -2_000_000])
def test_a_zero_or_negative_burn_is_cash_positive_with_no_finite_months(quarterly):
    c = runway_cell(burn(5_000_000, quarterly))
    assert (
        c.cash_positive is True and c.months is None and c.gap is None
    )  # a stated figure, not a blank


def test_no_cash_burn_fact_is_a_blank_not_a_zero():
    assert runway_cell(None).gap == "no_burn"


def convert_fact(
    maturity=date(2032, 6, 1),
    issued=date(2026, 5, 21),
    accession="0001193125-26-234847",
    principal=402_500_000.0,
    rate=33.859,
    shares=228_357_303,
):
    terms = ConvertTerms(
        principal_total_usd=principal,
        coupon_pct=0.0,
        maturity_date=maturity,
        conversion_rate=rate,
        conversion_price_usd=29.53,
        issued_date=issued,
    )
    return {
        "instrument_kind": "convertible_notes",
        "accession": accession,
        "shares_outstanding": shares,
        "terms": terms.model_dump(mode="json"),
        "valid_from": issued,
    }


def test_overhang_is_the_dilution_clocks_own_percent_to_one_decimal():
    # 402.5M principal x 33.859 shares per $1,000 = 13.63M shares on 228.36M outstanding: 5.97%, shown 6.0
    c = overhang_cell([convert_fact()], ASOF)
    assert (c.value, c.gap) == (6.0, None)
    # a second live convert adds its shares to the same overhang
    two = overhang_cell(
        [convert_fact(), convert_fact(accession="x", principal=100e6, rate=20.0)], ASOF
    )
    assert two.value == 6.8


def test_overhang_is_blank_with_no_live_convert_never_a_zero_and_a_matured_one_is_not_live():
    none = overhang_cell([], ASOF)
    assert (none.value, none.gap, none.note) == (None, "no_convert", None)
    matured = overhang_cell([convert_fact(maturity=date(2026, 9, 1))], ASOF)
    assert (matured.value, matured.gap) == (None, "no_convert")
    assert matured.note and "none live" in matured.note


def test_purity_is_the_largest_mix_line_with_its_segment():
    facts = [
        {"mix_pct": 12.0, "segment_label": "enrichment"},
        {"mix_pct": 61.5, "segment_label": "nuclear"},
        {"mix_pct": 30.0, "segment_label": "services"},
    ]
    c = purity_cell(facts)
    assert (c.value, c.segment, c.gap) == (61.5, "nuclear", None)
    assert purity_cell([]).gap == "no_mix"


# ---------------------------------------------------------------------------------------------------------
# the tape, against the display members' own readings
# ---------------------------------------------------------------------------------------------------------


def test_the_metric_cells_read_the_members_own_keys_and_say_why_one_is_blank():
    s = sig("sma_position", pct_vs_fast=5.2, pct_vs_slow=(None, "n/a: 120/200 bars"))
    assert metric_cell(s, "pct_vs_fast", None).value == 5.2
    short = metric_cell(s, "pct_vs_slow", None)
    assert (short.value, short.gap, short.note) == (None, "no_reading", "n/a: 120/200 bars")
    assert metric_cell(None, "pct_vs_fast", None).gap == "no_price"
    flagged = metric_cell(s, "pct_vs_fast", MIXED)  # the flagged name's value is never carried
    assert (flagged.value, flagged.gap, flagged.note) == (None, "flagged", MIXED)
    # a stated zero is a number, not a blank
    zero = metric_cell(sig("rvol", rvol20=0.0), "rvol20", None)
    assert zero.value == 0.0 and zero.gap is None


def test_the_members_metric_keys_this_page_reads_exist_and_state_a_value_on_a_full_tape():
    days = [date(2025, 9, 1) + timedelta(days=i) for i in range(300) if (i % 7) not in (5, 6)]
    bars = [
        {"d": d, "close": 10.0 + 0.02 * i, "low": 9.5, "volume": 1000.0 + i}
        for i, d in enumerate(days)
    ]
    asof = days[-1]
    got = {
        "range": range52w.compute(bars, asof),
        "sma": sma.compute(bars, asof),
        "rvol": rvol.compute(bars, asof),
        "volume": volume_regime.compute(bars, asof),
        "returns": trailing_returns.compute(bars, asof),
    }
    for sig_, key, name in [
        (got["range"], "pct_off_52w_high", "off_high"),
        (got["sma"], "pct_vs_fast", "sma50"),
        (got["sma"], "pct_vs_slow", "sma200"),
        (got["rvol"], "rvol20", "rvol20"),
        (got["volume"], "adv_usd_20d", "adv"),
        (got["returns"], "ret_30d", "ret"),
    ]:
        cell = metric_cell(sig_, key, None)
        assert cell.gap is None and cell.value is not None, name


def test_vs_spy_is_the_30_day_return_minus_spys_in_percentage_points():
    own = sig("trailing_returns", ret_30d=10.0)
    c = spy_cell(own, SPY, None)
    assert (c.value, c.ret_pct, c.spy_pct, c.last_close, c.gap) == (6.0, 10.0, 4.0, ASOF, None)


def test_each_vs_spy_gap_is_named():
    own = sig("trailing_returns", ret_30d=10.0)
    assert spy_cell(None, SPY, None).gap == "no_price"
    assert spy_cell(sig("trailing_returns", end=None, ret_30d=1.0), SPY, None).gap == "no_price"
    no_spy = spy_cell(own, None, None)
    assert (no_spy.gap, no_spy.ret_pct, no_spy.value) == (
        "no_spy",
        10.0,
        None,
    )  # its own return still shows
    stale = spy_cell(sig("trailing_returns", end=ASOF - timedelta(days=3), ret_30d=10.0), SPY, None)
    assert (stale.gap, stale.value, stale.ret_pct) == ("ends_differ", None, 10.0)
    short = spy_cell(sig("trailing_returns", ret_30d=(None, "n/a: 20/31 bars")), SPY, None)
    assert (short.gap, short.value, short.note) == ("no_reading", None, "n/a: 20/31 bars")
    flagged = spy_cell(own, SPY, MIXED)
    assert (flagged.gap, flagged.note, flagged.value, flagged.ret_pct) == (
        "flagged",
        MIXED,
        None,
        None,
    )


def test_the_insider_cell_is_the_members_screened_buys_zero_is_stated_and_no_form4_is_blank():
    c = insider_cell(
        sig("insider_flow_90d", buy_count=3.0, buy_usd=1_250_000.0, distinct_buyers=2.0)
    )
    assert (c.buys, c.usd, c.buyers, c.gap) == (3, 1_250_000.0, 2, None)
    zero = insider_cell(sig("insider_flow_90d", buy_count=0.0, buy_usd=0.0, distinct_buyers=0.0))
    assert (zero.buys, zero.usd, zero.gap) == (
        0,
        0.0,
        None,
    )  # Form 4s on file, none a buy: a stated zero
    none = insider_cell(None)  # the member gives nothing when no Form 4 is ingested
    assert (none.buys, none.gap) == (None, "no_form4")


def test_the_insider_cell_runs_off_the_real_members_screen():
    rows = [
        {
            "txn_code": "P",
            "valid_from": ASOF - timedelta(days=10),
            "insider_name": "Jane Roe",
            "usd": 50_000.0,
            "price": 5.0,
            "shares": 10_000,
        },
        {
            "txn_code": "S",
            "valid_from": ASOF - timedelta(days=5),
            "insider_name": "John Doe",
            "usd": 9_000.0,
            "price": 9.0,
            "shares": 1_000,
        },
    ]
    c = insider_cell(insider_flow.compute(rows, ASOF))
    assert (c.buys, c.usd, c.buyers) == (1, 50_000.0, 1)
    assert insider_cell(insider_flow.compute([], ASOF)).gap == "no_form4"


# ---------------------------------------------------------------------------------------------------------
# a company, once: the fold, the chips, the roles, the silent tag
# ---------------------------------------------------------------------------------------------------------

ROSTER = [
    ms("AAA", AAA, "Turbines", T1, "Grid", "payer"),
    ms("AAA", AAA, "Cables", T1, "Grid", "payer"),
    ms("AAX", AAA, CONTEXT_LABEL, T2, "Batteries", "counter_case"),
    ms("BBB", BBB, "Turbines", T1, "Grid"),
    ms("CCC", CCC, CONTEXT_LABEL, T1, "Grid"),
    ms("CCC", CCC, BENCH_LABEL, T2, "Batteries"),
    ms("ZZZ", None, "Turbines", T1, "Grid"),
]
IDS = {
    AAA: ident("Acme", "miner", "materials"),
    BBB: ident("Bolt", "miner", "materials"),
    CCC: ident("Cobalt", "utilities", "energy_utilities", royalty=True),
}


def test_a_company_on_several_cards_and_links_is_one_row_with_its_chips_roles_and_ticker():
    page = table(ROSTER, IDS, {}, scope="all", key=None)
    assert [c.ticker for c in page.companies].count("AAA") == 1
    a = company(page, AAA)
    assert a.rows == 3 and a.name == "Acme" and a.silent_only is False
    # one chip per card in card-name order, the links folded together on the card, silent where it is
    assert [(c.thesis_name, c.links, c.silent, c.role, c.ticker) for c in a.cards] == [
        ("Batteries", (CONTEXT_LABEL,), True, "counter_case", "AAX"),
        ("Grid", ("Turbines", "Cables"), False, "payer", "AAA"),
    ]
    assert a.roles == ("payer", "counter_case")  # the enum's own order
    assert a.ticker == "AAA"  # two cards, one each: the tie goes to the lower ticker


def test_a_company_whose_every_row_is_context_or_bench_is_silent_only():
    page = table(ROSTER, IDS, {}, scope="all", key=None)
    c = company(page, CCC)
    assert c.silent_only is True and c.rows == 2 and c.royalty is True
    assert page.silent_only == 1
    assert company(page, BBB).silent_only is False


def test_a_row_with_no_resolved_security_is_counted_and_listed_never_a_row():
    page = table(ROSTER, IDS, {}, scope="all", key=None)
    assert "ZZZ" not in {c.ticker for c in page.companies}
    assert [(u.ticker, u.thesis_name) for u in page.unresolved] == [("ZZZ", "Grid")]
    assert page.rows == 6  # the resolved rows only
    assert sum(len(g.members) for g in page.groups) == len(page.companies) == 3


def test_a_company_with_no_master_row_or_no_reads_is_a_row_with_every_cell_blank():
    page = table([ms("QQQ", S5)], {}, {}, scope="all", key=None)
    c = page.companies[0]
    assert (c.name, c.business_type, c.supersector) == (None, None, None)
    gaps = {col: getattr(c, col).gap for col in COLUMNS}
    assert all(g is not None for g in gaps.values()), gaps  # nothing is made up
    assert (
        gaps["cap"] == "no_price"
        and gaps["revenue"] == "no_revenue"
        and gaps["insider"] == "no_form4"
    )
    assert c.workbench_facts is False


def test_a_business_type_scope_keeps_all_of_a_companys_cards_not_just_one():
    page = table(ROSTER, IDS, {}, scope="business_type", key="miner")
    assert sorted(c.ticker for c in page.companies) == ["AAA", "BBB"]
    assert (
        len(company(page, AAA).cards) == 2
    )  # its Batteries row rides along: it is a miner on every card
    assert (page.scope_cards, page.rows) == (2, 4)


# ---------------------------------------------------------------------------------------------------------
# the scopes: who is in each, and the pickers
# ---------------------------------------------------------------------------------------------------------


def test_the_scope_rows_per_kind():
    b = base(ROSTER, IDS)
    assert scope_sids(b, "business_type", "miner") == sorted([AAA, BBB], key=str)
    assert scope_sids(b, "business_type", "utilities") == [CCC]
    assert scope_sids(b, "supersector", "energy_utilities") == [CCC]
    assert scope_sids(b, "supersector", "materials") == sorted([AAA, BBB], key=str)
    assert scope_sids(b, "card", str(T2)) == sorted([AAA, CCC], key=str)
    assert scope_sids(b, "all", None) == sorted([AAA, BBB, CCC], key=str)
    # a card scope's rows are that card's rows only
    assert {m.thesis_id for m in scope_rows(b, "card", str(T2))} == {T2}
    # a company with no master row (or no type) is Unclassified
    odd = base([*ROSTER, ms("QQQ", S5)], IDS)
    assert scope_sids(odd, "business_type", "unclassified") == [S5]
    assert scope_sids(odd, "supersector", "unclassified") == [S5]


def test_the_pickers_list_leaves_biggest_first_unclassified_last_each_company_once():
    roster = [
        ms("AAA", AAA),
        ms("BBB", BBB),
        ms("CCC", CCC),
        ms("DDD", DDD),
        ms("EEE", S5),
        ms("FFF", S6),
        ms("AAA", AAA, "Turbines", T2, "Batteries"),  # a second card: still one company
    ]
    idents = {
        AAA: ident(bt="miner"),
        BBB: ident(bt="miner"),
        CCC: ident(bt="utilities", ss="energy_utilities"),
        DDD: ident(bt="bank", ss="financials"),
    }  # S5 and S6 have no master row
    found = scopes(base(roster, idents), ASOF)
    assert [(o.key, o.companies) for o in found.business_types] == [
        ("miner", 2),
        ("bank", 1),
        ("utilities", 1),
        ("unclassified", 2),  # the tail, whatever its size
    ]
    assert [(o.key, o.companies) for o in found.supersectors] == [
        ("materials", 2),
        ("energy_utilities", 1),
        ("financials", 1),
        ("unclassified", 2),
    ]
    assert found.companies == 6 and found.unresolved_rows == 0
    assert (found.default_scope, found.default_key) == ("business_type", "miner")
    # the counted cards A to Z with their link counts today and their companies at the as-of
    assert [(c.name, c.links, c.companies) for c in found.cards] == [
        ("Batteries", 0, 1),
        ("Grid", 2, 6),
    ]
    assert (found.cards_counted, found.live_cards, found.cards_not_yet_created) == (2, 4, 1)
    assert found.roster_fallback_cards == 2


def test_the_default_is_the_first_leaf_with_two_companies_else_the_first_real_leaf_else_none():
    one_each = [ms("AAA", AAA), ms("CCC", CCC)]
    idents = {AAA: ident(bt="miner"), CCC: ident(bt="utilities", ss="energy_utilities")}
    found = scopes(base(one_each, idents), ASOF)
    assert (found.default_scope, found.default_key) == (
        "business_type",
        "miner",
    )  # the first, none has two
    only_unclassified = scopes(base([ms("QQQ", S5), ms("RRR", S6)], {}), ASOF)
    assert (only_unclassified.default_scope, only_unclassified.default_key) == (
        "business_type",
        "unclassified",
    )
    empty = scopes(base([], {}), ASOF)
    assert (empty.default_scope, empty.default_key, empty.companies) == (None, None, 0)
    assert empty.business_types == () and empty.cards[0].companies == 0


def test_scope_present_is_the_routers_404_rule():
    found = scopes(base(ROSTER, IDS), ASOF)
    assert scope_present(found, "business_type", "miner")
    assert not scope_present(found, "business_type", "bank")
    assert not scope_present(found, "business_type", None)
    assert scope_present(found, "supersector", "materials")
    assert not scope_present(found, "supersector", "financials")
    assert scope_present(found, "card", str(T1)) and not scope_present(
        found, "card", str(uuid.uuid4())
    )
    assert not scope_present(found, "card", "not-a-uuid")
    assert scope_present(found, "all", None)


def test_the_unresolved_rows_a_card_scope_lists_are_that_cards_others_list_every_counted_card():
    roster = [*ROSTER, ms("YYY", None, "Cells", T2, "Batteries")]
    everything = table(roster, IDS, {}, scope="all", key=None)
    assert sorted(u.ticker for u in everything.unresolved) == ["YYY", "ZZZ"]
    batteries = table(roster, IDS, {}, scope="card", key=str(T2))
    assert [u.ticker for u in batteries.unresolved] == ["YYY"]
    miners = table(roster, IDS, {}, scope="business_type", key="miner")
    assert sorted(u.ticker for u in miners.unresolved) == [
        "YYY",
        "ZZZ",
    ]  # no scope can claim a row with no security


# ---------------------------------------------------------------------------------------------------------
# the groups: order, placement, and the figures on hand-computed numbers
# ---------------------------------------------------------------------------------------------------------


def priced(sma50: float | None, ret30: float | None, **over) -> dict:
    """A company's reads with a 50-day reading and a 30-day return (SPY's is 4.0)."""
    return dict(
        close=CLOSE,
        sma=sig("sma_position", pct_vs_fast=sma50, pct_vs_slow=sma50),
        returns=sig("trailing_returns", ret_30d=ret30),
        **over,
    )


# four miners: A and B and C count, D is Context-only and its price history is flagged
PEER_ROSTER = [
    ms("AAA", AAA, "Turbines"),
    ms("BBB", BBB, "Turbines"),
    ms("CCC", CCC, "Cables"),
    ms("DDD", DDD, CONTEXT_LABEL),
]
PEER_IDS = {sid: ident(bt="miner") for sid in (AAA, BBB, CCC, DDD)}
PEER_READS = {
    # A: cap $250M, above its 50d (+5), 10 vs SPY's 4 = +6.0, revenue +20.0% YoY, 12.0 months of runway
    AAA: CompanyReads(
        **priced(5.0, 10.0),
        shares=shares_fact(10_000_000),
        revenue=(rev(date(2025, 6, 30), 100.0), rev(date(2026, 6, 30), 120.0)),
        cash_burn=burn(12_000_000, 3_000_000),
    ),
    # B: cap $1B, below its 50d (-3), -2 vs SPY's 4 = -6.0, revenue but no prior year, cash-positive
    BBB: CompanyReads(
        **{**priced(-3.0, -2.0), "close": (CLOSE[0], 50.0)},
        shares=shares_fact(20_000_000),
        revenue=(rev(date(2026, 6, 30), 50.0),),
        cash_burn=burn(9_000_000, -1.0),
    ),
    # C: no shares (no cap), above its 50d (+1), 5 vs SPY's 4 = +1.0, revenue -10.0% YoY, no cash/burn fact
    CCC: CompanyReads(
        **priced(1.0, 5.0),
        revenue=(rev(date(2025, 6, 30), 100.0), rev(date(2026, 6, 30), 90.0)),
    ),
    # D: Context-only, a flagged price history, revenue +50.0% YoY, 6.0 months of runway
    DDD: CompanyReads(
        flag=MIXED,
        shares=shares_fact(10_000_000),
        revenue=(rev(date(2025, 6, 30), 100.0), rev(date(2026, 6, 30), 150.0)),
        cash_burn=burn(6_000_000, 3_000_000),
    ),
}


def peers():
    return table(PEER_ROSTER, PEER_IDS, PEER_READS)


def test_group_figures_on_hand_computed_numbers_each_with_its_n():
    (g,) = peers().groups
    s = g.stats  # every name in the group: A, B, C, D
    assert s.names == 4
    assert (s.cap.value, s.cap.n) == (
        625_000_000.0,
        2,
    )  # median of $250M and $1B (an even count averages)
    assert (s.above_50d, s.priced_50d) == (2, 3)  # A and C above; D's flagged 50d is not a reading
    assert (s.vs_spy.value, s.vs_spy.n) == (1.0, 3)  # median of +6.0, -6.0, +1.0
    assert (s.yoy.value, s.yoy.n) == (
        20.0,
        3,
    )  # median of +20.0, -10.0, +50.0 (B has no prior year)
    assert s.runway == 3  # A months, B cash-positive, D months; C has no fact


def test_the_counting_figures_leave_out_the_names_that_never_count():
    (g,) = peers().groups
    s = g.stats_counting  # A, B, C (D is Context-only)
    assert s.names == 3
    assert (s.cap.value, s.cap.n) == (625_000_000.0, 2)
    assert (s.above_50d, s.priced_50d) == (2, 3)
    assert (s.vs_spy.value, s.vs_spy.n) == (1.0, 3)
    assert (s.yoy.value, s.yoy.n) == (5.0, 2)  # median of +20.0 and -10.0
    assert s.runway == 2


def test_a_group_with_one_priced_name_carries_its_value_with_n_one_never_a_median_of_many():
    only = table([ms("AAA", AAA)], {AAA: ident()}, {AAA: PEER_READS[AAA]})
    s = only.groups[0].stats
    assert (s.cap.value, s.cap.n) == (250_000_000.0, 1)
    assert (s.vs_spy.value, s.vs_spy.n) == (6.0, 1)
    assert (s.yoy.value, s.yoy.n) == (20.0, 1)
    assert (s.above_50d, s.priced_50d) == (1, 1)


def test_a_group_with_nothing_stated_says_none_not_zero():
    empty = group_stats([])
    assert empty.names == 0 and (empty.cap.value, empty.cap.n) == (None, 0)
    assert (empty.vs_spy.value, empty.yoy.value, empty.above_50d, empty.priced_50d) == (
        None,
        None,
        0,
        0,
    )
    bare = table([ms("QQQ", S5)], {}, {}, scope="all", key=None).groups
    leaf = bare[-1].stats
    assert leaf.names == 1 and leaf.cap.n == 0 and leaf.runway == 0


def test_the_default_order_is_the_group_order_with_names_a_to_z_inside():
    roster = [ms("ccc", CCC), ms("AAA", AAA), ms("bbb", BBB)]
    page = table(roster, {s: ident() for s in (AAA, BBB, CCC)}, {})
    assert [c.ticker for c in page.companies] == ["AAA", "bbb", "ccc"]  # case-insensitive
    assert [c.key for c in page.companies] == list(page.groups[0].members)


def test_a_flagged_name_keeps_its_row_its_price_cells_gray_and_it_is_named_once():
    page = peers()
    d = company(page, DDD)
    assert d.flag == MIXED
    for col in ("cap", "off_high", "sma50", "sma200", "rvol20", "adv", "vs_spy"):
        cell = getattr(d, col)
        assert (cell.gap, cell.note) == ("flagged", MIXED), col
        assert getattr(cell, "value", None) is None
    # the cells that read no price stay stated
    assert d.revenue.value == 150.0 and d.yoy.value == 50.0 and d.runway.months == 6.0
    assert [(f.ticker, f.reason) for f in page.flagged] == [("DDD", MIXED)]
    assert company(page, AAA).flag is None and company(page, AAA).cap.gap is None


def test_the_coverage_counts_every_blank_by_column_and_reason():
    page = peers()
    cov = {c.column: c for c in page.coverage}
    assert [c.column for c in page.coverage] == list(COLUMNS)
    cap = cov["cap"]
    assert (cap.stated, cap.total) == (2, 4)
    assert [(b.gap, b.n) for b in cap.blanks] == [("flagged", 1), ("no_shares", 1)]
    assert (cov["sma50"].stated, cov["sma50"].total) == (3, 4)
    assert [(b.gap, b.n) for b in cov["sma50"].blanks] == [("flagged", 1)]
    assert (cov["yoy"].stated, [(b.gap, b.n) for b in cov["yoy"].blanks]) == (
        3,
        [("no_prior_year", 1)],
    )
    assert (cov["runway"].stated, [(b.gap, b.n) for b in cov["runway"].blanks]) == (
        3,
        [("no_burn", 1)],
    )
    assert cov["purity"].stated == 0 and [(b.gap, b.n) for b in cov["purity"].blanks] == [
        ("no_mix", 4)
    ]
    assert cov["insider"].stated == 0 and cov["insider"].total == 4
    # every column: stated + the blanks = the names, and a stated cell is exactly one with no gap
    for c in page.coverage:
        assert c.stated + sum(b.n for b in c.blanks) == c.total == 4
    counting = {c.column: c for c in page.coverage_counting}
    assert (counting["cap"].stated, counting["cap"].total) == (2, 3)  # D (Context-only) is out
    assert [(b.gap, b.n) for b in counting["cap"].blanks] == [("no_shares", 1)]
    # an empty scope: zero of zero, no division
    assert [(c.stated, c.total, c.blanks) for c in coverage([])] == [(0, 0, ())] * len(COLUMNS)


def test_workbench_facts_are_the_four_the_operator_marks_a_name_for():
    page = peers()
    assert company(page, AAA).workbench_facts is True  # shares and cash/burn
    assert company(page, CCC).workbench_facts is False  # revenue alone is not a Workbench fact
    only_mix = CompanyReads(mix=({"mix_pct": 10.0, "segment_label": "x"},))
    assert company_row(AAA, [ms("AAA", AAA)], ident(), only_mix, None, ASOF).workbench_facts is True
    only_dilution = CompanyReads(dilution=(convert_fact(),))
    assert company_row(AAA, [ms("AAA", AAA)], ident(), only_dilution, None, ASOF).workbench_facts


def test_the_supersector_scope_groups_by_leaf_with_other_and_unclassified_last():
    roster = [
        ms("AAA", AAA),
        ms("BBB", BBB),
        ms("CCC", CCC),
        ms("DDD", DDD),
        ms("EEE", S5),
    ]
    idents = {
        AAA: ident(bt="semiconductors", ss="technology"),
        BBB: ident(bt="software_it", ss="technology"),
        CCC: ident(bt="other", ss="technology"),
        DDD: ident(bt=None, ss="technology"),
    }
    page = table(roster, {**idents}, {}, scope="supersector", key="technology")
    assert [(g.key, g.depth, g.kind, len(g.members)) for g in page.groups] == [
        ("type:semiconductors", 0, "business_type", 1),
        ("type:software_it", 0, "business_type", 1),
        ("type:other", 0, "business_type", 1),
        ("type:unclassified", 0, "business_type", 1),
    ]
    assert [c.ticker for c in page.companies] == ["AAA", "BBB", "CCC", "DDD"]


def test_the_all_scope_nests_leaves_under_supersectors_and_each_company_is_listed_once():
    idents = {
        AAA: ident(bt="miner", ss="materials"),
        BBB: ident(bt="chemicals_materials", ss="materials"),
        CCC: ident(bt="utilities", ss="energy_utilities"),
        DDD: ident(bt=None, ss=None),
    }
    roster = [ms("AAA", AAA), ms("BBB", BBB), ms("CCC", CCC), ms("DDD", DDD)]
    page = table(roster, idents, {}, scope="all", key=None)
    assert [(g.key, g.depth, g.kind) for g in page.groups] == [
        ("sector:energy_utilities", 0, "supersector"),
        ("type:utilities", 1, "business_type"),
        ("sector:materials", 0, "supersector"),
        ("type:chemicals_materials", 1, "business_type"),
        ("type:miner", 1, "business_type"),
        ("sector:unclassified", 0, "supersector"),
        ("type:unclassified", 1, "business_type"),
    ]
    header = next(g for g in page.groups if g.key == "sector:materials")
    assert (
        header.members == () and header.stats.names == 2
    )  # its leaves list the names; it counts them
    assert sum(len(g.members) for g in page.groups) == len(page.companies) == 4


def test_a_card_scope_groups_by_link_in_chain_order_each_company_once_under_its_first_link():
    card = CardRef(T1, "Grid", ("Turbines", "Cables", "Empty", CONTEXT_LABEL, BENCH_LABEL))
    roster = [
        ms("AAA", AAA, "Turbines"),
        ms("AAA", AAA, "Cables"),  # a second link of the same card
        ms("BBB", BBB, "Cables"),
        ms("CCC", CCC, "Retired Link"),  # on the roster, no longer on the card
        ms("DDD", DDD, None),
        ms("EEE", S5, CONTEXT_LABEL),
        ms("FFF", S6, BENCH_LABEL),
    ]
    idents = {s: ident() for s in (AAA, BBB, CCC, DDD, S5, S6)}
    page = peer_table(
        PeerInputs(base(roster, idents, cards=(card,)), {}, SPY), "card", str(T1), ASOF
    )
    got = [
        (g.key, g.label, g.kind, [company_key_to_ticker(page, k) for k in g.members])
        for g in page.groups
    ]
    assert got == [
        ("link:Turbines", "Turbines", "link", ["AAA"]),  # AAA once, under its first link
        ("link:Cables", "Cables", "link", ["BBB"]),
        ("link:Empty", "Empty", "link", []),  # a real link with no names is still shown
        ("old_link:Retired Link", "Retired Link", "old_link", ["CCC"]),
        ("no_link", "No link", "no_link", ["DDD"]),
        ("silent:Context", "Context", "silent", ["EEE"]),
        ("silent:Bench", "Bench", "silent", ["FFF"]),
    ]
    assert page.scope_name == "Grid" and page.scope_cards == 1
    assert next(g for g in page.groups if g.key == "link:Empty").stats.names == 0
    a = company(page, AAA)
    assert a.cards[0].links == ("Turbines", "Cables")  # its other link rides in its chip


def company_key_to_ticker(page, key: str) -> str:
    return next(c.ticker for c in page.companies if c.key == key)


def test_the_page_counts_what_it_rests_on():
    page = table(ROSTER, IDS, {}, scope="all", key=None)
    assert (page.live_cards, page.cards_counted) == (4, 2)
    assert (page.cards_not_yet_created, page.roster_fallback_cards) == (1, 2)
    assert page.scope == "all" and page.key is None and page.scope_name is None
    assert page.spy is not None and page.spy.ret_30d == 4.0 and page.spy.end == ASOF
    nospy = table(ROSTER, IDS, {}, scope="all", key=None, spy=None)
    assert nospy.spy is None
    assert all(c.vs_spy.gap == "no_price" for c in nospy.companies)  # no reads, and no SPY either


def test_identities_carry_the_name_the_leaf_the_supersector_and_the_royalty_overlay():
    got = identities(
        {
            S1: {
                "name": "Xylo",
                "business_type": "miner",
                "business_supersector": "materials",
                "royalty": True,
            },
            S2: {"name": None, "business_type": None, "business_supersector": None},
        }
    )
    assert got[S1] == Identity("Xylo", "miner", "materials", True)
    assert got[S2] == Identity(None, None, None, False)  # unknown, never guessed


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy reads stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"base": 0, "reads": [], "views": []}

    def fake_base(conn, asof, known_at):
        seen["base"] += 1
        seen["views"].append((asof, known_at))
        return base(
            PEER_ROSTER + [ms("ZZZ", None, "Turbines")],
            PEER_IDS | {S5: ident(bt="bank", ss="financials")},
        )

    def fake_reads(conn, sids, asof, known_at, clock):
        seen["reads"].append((tuple(sids), asof, known_at, clock))
        return {s: PEER_READS[s] for s in sids if s in PEER_READS}, SPY

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_base", fake_base)
    monkeypatch.setattr(router_mod, "read_reads", fake_reads)
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


URL = "/dashboards/peer-table"


def test_the_router_serves_a_scope_off_one_cached_read_on_the_requests_view(stub_client):
    client, seen = stub_client
    params = {"asof": "2026-10-28", "scope": "business_type", "key": "miner"}
    body = client.get(URL, params=params).json()
    assert (body["cache"], body["clock"]) == ("computed", "public")
    ((sids, asof, known_at, clock),) = seen["reads"]
    assert asof == ASOF and clock == "public" and known_at is not None
    assert set(sids) == {AAA, BBB, CCC, DDD}  # only the companies in the scope were read
    again = client.get(URL, params=params).json()
    assert again["cache"] == "hit" and len(seen["reads"]) == 1
    assert seen["base"] == 1  # the roster-level read is shared by the scope list and every scope
    # another scope is its own read; the scope list came from the same cached roster read
    client.get(URL, params={"asof": "2026-10-28", "scope": "all"})
    assert len(seen["reads"]) == 2 and seen["base"] == 1
    # a live as-of is the unchanged live read, and its own cached read
    client.get(URL, params={"asof": market_today().isoformat(), "scope": "all"})
    assert seen["reads"][-1][2:] == (None, "record") and len(seen["reads"]) == 3


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(
        URL, params={"asof": "2026-10-28", "scope": "business_type", "key": "miner"}
    ).json()
    assert (body["scope"], body["key"], body["scope_name"]) == ("business_type", "miner", None)
    assert body["benchmark"] == "SPY" and body["columns"] == list(COLUMNS)
    assert (body["live_cards"], body["cards_counted"], body["cards_not_yet_created"]) == (4, 2, 1)
    assert body["roster_fallback_cards"] == 2
    assert (body["rows"], body["silent_only"]) == (4, 1)
    assert body["unresolved"] == [{"ticker": "ZZZ", "thesis_id": str(T1), "thesis_name": "Grid"}]
    assert body["unresolved_rows"] == 1
    assert [(f["ticker"], f["reason"]) for f in body["flagged"]] == [("DDD", MIXED)]
    assert body["spy"] == {"ret_30d": 4.0, "end": "2026-10-28"}
    assert [c["ticker"] for c in body["companies"]] == ["AAA", "BBB", "CCC", "DDD"]

    (group,) = body["groups"]
    assert (group["key"], group["kind"], group["depth"]) == ("type:miner", "business_type", 0)
    assert group["members"] == [c["key"] for c in body["companies"]]
    assert group["stats"]["cap"] == {"value": 625_000_000.0, "n": 2}
    assert group["stats_counting"]["yoy"] == {"value": 5.0, "n": 2}

    a = body["companies"][0]
    assert a["cap"]["value"] == 250_000_000.0 and a["cap"]["tier"] == "micro"
    assert (
        a["cap"]["gap"] is None and a["cap"]["close"] == 25.0 and a["cap"]["shares"] == 10_000_000.0
    )
    assert (a["revenue"]["value"], a["revenue"]["period_end"]) == (120.0, "2026-06-30")
    assert a["yoy"] == {
        "value": 20.0,
        "prior_value": 100.0,
        "prior_period_end": "2025-06-30",
        "gap": None,
    }
    assert a["runway"]["months"] == 12.0 and a["runway"]["cash_positive"] is False
    assert (a["vs_spy"]["value"], a["vs_spy"]["ret_pct"], a["vs_spy"]["spy_pct"]) == (
        6.0,
        10.0,
        4.0,
    )
    assert a["sma50"] == {"value": 5.0, "gap": None, "note": None}
    assert a["insider"]["gap"] == "no_form4" and a["purity"]["gap"] == "no_mix"
    assert a["overhang"]["gap"] == "no_convert"
    assert a["cards"][0]["thesis_name"] == "Grid" and a["cards"][0]["links"] == ["Turbines"]
    d = body["companies"][3]
    assert d["flag"] == MIXED and d["cap"]["gap"] == "flagged" and d["silent_only"] is True
    cov = {c["column"]: c for c in body["coverage"]}
    assert cov["cap"] == {
        "column": "cap",
        "stated": 2,
        "total": 4,
        "blanks": [{"gap": "flagged", "n": 1}, {"gap": "no_shares", "n": 1}],
    }
    assert body["coverage_counting"][0]["total"] == 3


def test_the_scope_lists_endpoint(stub_client):
    client, seen = stub_client
    body = client.get(f"{URL}/scopes", params={"asof": "2026-10-28"}).json()
    assert (body["clock"], body["cache"]) == ("public", "computed")
    assert [(o["key"], o["companies"]) for o in body["business_types"]] == [("miner", 4)]
    assert (body["default_scope"], body["default_key"]) == ("business_type", "miner")
    assert [(c["name"], c["companies"]) for c in body["cards"]] == [("Batteries", 0), ("Grid", 4)]
    assert body["unresolved_rows"] == 1 and body["companies"] == 4
    again = client.get(f"{URL}/scopes", params={"asof": "2026-10-28"}).json()
    assert again["cache"] == "hit" and seen["base"] == 1


def test_a_scope_key_not_present_at_the_asof_is_a_404_and_reads_nothing(stub_client):
    client, seen = stub_client
    for params in (
        {"scope": "business_type", "key": "bank"},
        {"scope": "supersector", "key": "healthcare"},
        {"scope": "card", "key": str(uuid.uuid4())},
        {"scope": "card", "key": "not-a-uuid"},
    ):
        r = client.get(URL, params={"asof": "2026-10-28", **params})
        assert r.status_code == 404, params
    assert seen["reads"] == []


def test_a_scope_that_needs_a_key_without_one_is_a_422_and_all_ignores_it(stub_client):
    client, _ = stub_client
    assert client.get(URL, params={"asof": "2026-10-28", "scope": "card"}).status_code == 422
    assert client.get(URL, params={"asof": "2026-10-28", "scope": "nonsense"}).status_code == 422
    assert (
        client.get(URL, params={"asof": "2026-10-28"}).status_code == 422
    )  # the scope is required
    ok = client.get(URL, params={"asof": "2026-10-28", "scope": "all", "key": "whatever"})
    assert ok.status_code == 200 and ok.json()["key"] is None


def test_the_router_requires_an_asof(stub_client):
    client, seen = stub_client
    assert client.get(URL, params={"scope": "all"}).status_code == 422
    assert client.get(f"{URL}/scopes").status_code == 422
    assert seen["reads"] == []


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a fact published after a past as-of never reaches a cell (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

PAST = date(2026, 6, 5)  # a past as-of (a Friday)
LATER = date(2026, 6, 12)  # a later past as-of, after the "late" facts below
INGESTED = datetime(2026, 9, 1, tzinfo=UTC)  # our own ingest instant, after both as-ofs
CIK_A, CIK_B = "0000876543", "0000876544"


def _security(db, ticker: str, cik: str | None, kind="equity", name=None, sector=None) -> UUID:
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


def _shares(db, sid, shares, valid_from, recorded, ref) -> None:
    append_fact(
        db,
        "fact_shares_outstanding",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "shares": shares,
            "source": "10-q",
            "source_ref": ref,
            "valid_from": valid_from,
            "recorded_at": recorded,
        },
    )


def _burn(db, sid, cash, quarterly, valid_from, recorded, ref) -> None:
    append_fact(
        db,
        "fact_cash_burn",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "cash_usd": cash,
            "quarterly_burn_usd": quarterly,
            "source": "10-q",
            "source_ref": ref,
            "valid_from": valid_from,
            "recorded_at": recorded,
        },
    )


def _revenue(db, sid, period_end: date, value: float, filed: date, accession: str) -> None:
    append_fact(
        db,
        "fact_fundamentals",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "metric_key": "revenue",
            "period_end": period_end,
            "value": value,
            "basis": "native",
            "accession": accession,
            "valid_from": filed,  # a period counts from the day it was filed
            "recorded_at": INGESTED,
        },
    )


def _convert(db, sid, accession: str, issued: date, principal: float, rate: float) -> None:
    ingest_convert_terms(
        db,
        sid,
        ConvertTerms(
            principal_total_usd=principal,
            coupon_pct=0.0,
            maturity_date=date(2032, 6, 1),
            conversion_rate=rate,
            conversion_price_usd=29.53,
            issued_date=issued,
        ),
        accession=accession,
        shares_outstanding=228_357_303,
        shares_outstanding_ref="0001773751-26-000076",
        recorded_at=INGESTED,
    )


def _card(db, name: str, members: list[BasketMember]) -> Thesis:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core")],
        basket=members,
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # the card existed on the as-of (a fixture backdate, not an app write)
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2026-01-02", thesis.id))
        cur.execute(  # the roster as first promoted was taken before the earlier as-of
            "UPDATE basket_snapshot SET taken_at = %s WHERE thesis_id = %s",
            (datetime(2026, 6, 2, 12, 0, tzinfo=UTC), thesis.id),
        )
    db.commit()
    return thesis


def test_a_fact_published_after_a_past_asof_never_reaches_a_cell(db, pinned_today):
    """PTA has two shares facts, two cash/burn facts, four quarterly revenue prints and two convertible-note facts;
    the second of each was published after the earlier past as-of (a later ``valid_from``, and for the ratified
    Workbench facts a later ratification too). At the earlier as-of every cell reads the first of them; at the later
    one, the second. PTB has a tape and nothing else: its cells are blank for want of a fact, never zero. The tape
    ends at the as-of, and today's view (the feed stopped before today) grays the price cells with the flag's
    reason while the cells that read no price stay stated."""
    spy_id = _security(db, "SPY", None, kind="etf")
    pta = _security(
        db, "PTA", CIK_A, name="Acme Therapeutics", sector="Pharmaceutical Preparations"
    )
    ptb = _security(db, "PTB", CIK_B, name="Beta Bio", sector="Pharmaceutical Preparations")
    ds = [
        d
        for i in range(60)
        if (d := date(2026, 4, 20) + timedelta(days=i)) <= LATER and d.weekday() < 5
    ]
    spy_close = {d: 100.0 + 0.2 * i for i, d in enumerate(ds)}
    pta_close = {d: 10.0 + 0.1 * i + (0.3 if i % 5 == 0 else 0.0) for i, d in enumerate(ds)}
    for d in ds:
        _price(db, spy_id, d, spy_close[d])
        _price(db, pta, d, pta_close[d])
        _price(db, ptb, d, 20.0)
    # Workbench facts: the ratification instant is the public instant (recorded_at), the second one is later
    _shares(db, pta, 10_000_000, date(2026, 5, 10), datetime(2026, 5, 12, tzinfo=UTC), "pta-10q-1")
    _shares(
        db, pta, 40_000_000, date(2026, 6, 8), datetime(2026, 6, 9, 12, tzinfo=UTC), "pta-10q-2"
    )
    _burn(
        db,
        pta,
        12_000_000,
        3_000_000,
        date(2026, 5, 10),
        datetime(2026, 5, 12, tzinfo=UTC),
        "pta-b1",
    )
    _burn(
        db,
        pta,
        6_000_000,
        3_000_000,
        date(2026, 6, 8),
        datetime(2026, 6, 9, 12, tzinfo=UTC),
        "pta-b2",
    )
    # revenue: a fiscal year ending in January (the XBRL series, each print counts from the day it was filed)
    _revenue(db, pta, date(2025, 1, 31), 8_000_000, date(2025, 3, 10), "0000876543-25-000001")
    _revenue(db, pta, date(2025, 4, 30), 9_000_000, date(2025, 6, 9), "0000876543-25-000002")
    _revenue(db, pta, date(2026, 1, 31), 10_000_000, date(2026, 3, 10), "0000876543-26-000001")
    _revenue(db, pta, date(2026, 4, 30), 13_500_000, date(2026, 6, 8), "0000876543-26-000002")
    _convert(db, pta, "0001193125-26-234847", date(2026, 5, 21), 402_500_000.0, 33.859)
    _convert(db, pta, "0001193125-26-300001", date(2026, 6, 10), 100_000_000.0, 20.0)
    db.commit()

    _card(
        db,
        "Peer Table DB card",
        [
            BasketMember(ticker="PTA", role="the name", security_id=pta, segment="Core"),
            BasketMember(ticker="PTB", role="the name", security_id=ptb, segment="Core"),
        ],
    )

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    params = {"scope": "business_type", "key": "biotech_pharma"}
    try:
        client = TestClient(app)
        past = client.get(URL, params={"asof": PAST.isoformat(), **params}).json()
        later = client.get(URL, params={"asof": LATER.isoformat(), **params}).json()
        live = client.get(URL, params={"asof": pinned_today.isoformat(), **params}).json()
        scopes_past = client.get(f"{URL}/scopes", params={"asof": PAST.isoformat()}).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    assert (past["clock"], later["clock"], live["clock"]) == ("public", "public", "record")
    assert scopes_past["default_scope"] == "business_type"
    assert [(o["key"], o["companies"]) for o in scopes_past["business_types"]] == [
        ("biotech_pharma", 2)
    ]
    assert [(o["key"], o["companies"]) for o in scopes_past["supersectors"]] == [("healthcare", 2)]
    for body in (past, later):
        assert [c["ticker"] for c in body["companies"]] == ["PTA", "PTB"]
        assert body["roster_fallback_cards"] == 0 and body["flagged"] == []

    def at(body, ticker):
        return next(c for c in body["companies"] if c["ticker"] == ticker)

    upto = {asof: [d for d in ds if d <= asof] for asof in (PAST, LATER)}

    def ret(cl, asof, n):
        return round((cl[upto[asof][-1]] / cl[upto[asof][-(n + 1)]] - 1) * 100, 2)

    # the shares fact published after the earlier as-of never reaches its cap; the later view reads it
    a_past, a_later = at(past, "PTA"), at(later, "PTA")
    close_past, close_later = pta_close[upto[PAST][-1]], pta_close[upto[LATER][-1]]
    assert a_past["cap"]["shares"] == 10_000_000.0
    assert a_past["cap"]["value"] == pytest.approx(round(10_000_000 * close_past))
    assert a_past["cap"]["tier"] == "micro"
    assert a_past["cap"]["close_date"] == PAST.isoformat()
    assert a_later["cap"]["shares"] == 40_000_000.0
    assert a_later["cap"]["value"] == pytest.approx(round(40_000_000 * close_later))
    assert a_later["cap"]["tier"] == "small"
    # the cash/burn fact: 12.0 months, then 6.0
    assert a_past["runway"]["months"] == 12.0 and a_later["runway"]["months"] == 6.0
    # the revenue print filed after the earlier as-of: the earlier view's latest period is the January one
    assert (a_past["revenue"]["value"], a_past["revenue"]["period_end"]) == (
        10_000_000.0,
        "2026-01-31",
    )
    assert a_past["yoy"]["value"] == 25.0 and a_past["yoy"]["prior_period_end"] == "2025-01-31"
    assert (a_later["revenue"]["value"], a_later["revenue"]["period_end"]) == (
        13_500_000.0,
        "2026-04-30",
    )
    assert a_later["yoy"]["value"] == 50.0 and a_later["yoy"]["prior_period_end"] == "2025-04-30"
    # the second convert, issued after the earlier as-of, joins the overhang only at the later one
    assert a_past["overhang"]["value"] == 6.0 and a_later["overhang"]["value"] == 6.8
    # the tape ends at the as-of
    assert a_past["vs_spy"]["last_close"] == PAST.isoformat()
    assert a_past["vs_spy"]["ret_pct"] == pytest.approx(ret(pta_close, PAST, 30), abs=0.011)
    assert a_past["vs_spy"]["value"] == pytest.approx(
        ret(pta_close, PAST, 30) - ret(spy_close, PAST, 30), abs=0.011
    )
    assert a_later["vs_spy"]["last_close"] == LATER.isoformat()
    high = max(pta_close[d] for d in upto[PAST])
    assert a_past["off_high"]["value"] == pytest.approx(
        round((pta_close[upto[PAST][-1]] / high - 1) * 100, 2), abs=0.011
    )
    assert a_past["rvol20"]["value"] == 1.0  # a flat volume tape
    adv = sum(pta_close[d] * 1000.0 for d in upto[PAST][-20:]) / 20
    assert a_past["adv"]["value"] == pytest.approx(adv, abs=0.01)
    # a tape this short (35 bars) cannot state a 50-day average: blank with the member's own reason
    assert a_past["sma50"]["value"] is None and a_past["sma50"]["gap"] == "no_reading"
    assert a_past["sma50"]["note"] == f"n/a: {len(upto[PAST])}/50 bars"

    # PTB has a tape and nothing else: blank for want of a fact, never zero
    b = at(past, "PTB")
    assert (b["cap"]["gap"], b["cap"]["value"]) == ("no_shares", None)
    assert (b["revenue"]["gap"], b["runway"]["gap"], b["overhang"]["gap"], b["purity"]["gap"]) == (
        "no_revenue",
        "no_burn",
        "no_convert",
        "no_mix",
    )
    assert b["insider"]["gap"] == "no_form4" and b["workbench_facts"] is False
    assert a_past["workbench_facts"] is True
    cap_cov = next(c for c in past["coverage"] if c["column"] == "cap")
    assert (cap_cov["stated"], cap_cov["total"], cap_cov["blanks"]) == (
        1,
        2,
        [{"gap": "no_shares", "n": 1}],
    )
    (group,) = past["groups"]
    assert group["stats"]["cap"]["n"] == 1 and group["stats"]["names"] == 2

    # today's view: the feed stopped at the later date, so the flag reaches the price cells; the rest stay stated
    assert sorted(f["ticker"] for f in live["flagged"]) == ["PTA", "PTB"]
    live_a = at(live, "PTA")
    assert live_a["flag"] == "price feed stopped"
    for col in ("cap", "off_high", "sma50", "sma200", "rvol20", "adv", "vs_spy"):
        assert live_a[col]["gap"] == "flagged" and live_a[col]["value"] is None, col
    assert live_a["runway"]["months"] == 6.0 and live_a["revenue"]["value"] == 13_500_000.0


def test_a_context_role_reaches_the_company_row_as_a_quiet_tag(db, pinned_today):
    """The roster's context role is display metadata: it rides the company row and its chip, nothing else (#4)."""
    sid = _security(db, "RLA", CIK_A, name="Role Co", sector="Pharmaceutical Preparations")
    _card(
        db,
        "Role card",
        [
            BasketMember(
                ticker="RLA",
                role="the name",
                security_id=sid,
                segment="Core",
                context_role=ContextRole.PAYER,
            )
        ],
    )
    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        body = (
            TestClient(app)
            .get(
                URL,
                params={
                    "asof": pinned_today.isoformat(),
                    "scope": "business_type",
                    "key": "biotech_pharma",
                },
            )
            .json()
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()
    (c,) = body["companies"]
    assert c["roles"] == ["payer"] and c["cards"][0]["role"] == "payer"
