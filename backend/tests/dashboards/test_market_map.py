"""Market Map (``docs/dashboards/market-map.md``): the pure readings (year to date, 20-day dollar volume, the cap with
its cover-page fallback and its source), the payload on hand-built inputs, the router (pure, with the heavy read
stubbed) and one DB-backed past-as-of read proving a price bar, a cover share count and a card published after the
as-of stay invisible to it."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import market_map as router_mod
from dashboards.baskets import CardsAsOf, Membership
from dashboards.cache import CACHE
from dashboards.market_map import (
    COVER_MAX_AGE_DAYS,
    COVER_MIN_SHARES,
    DOLLAR_VOLUME_BARS,
    TRAILING,
    WINDOWS,
    CompanyCap,
    CompanyReading,
    CoverPick,
    MapInputs,
    benchmark_returns,
    company_cap,
    company_reading,
    cover_shares,
    dollar_volume_20d,
    gap_reason,
    market_map,
    pick_shares,
    window_returns,
    ytd_return,
)
from dashboards.peer_table import CapCell
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.market_time import market_today
from domain.splits import ListedSplit
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from repositories import thesis_repo
from signals.display import trailing_returns

ASOF = date(2026, 6, 1)
T1, T2 = uuid.uuid4(), uuid.uuid4()
S = [uuid.uuid4() for _ in range(8)]

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def bar(d: date, close: float | None, volume: float | None = 1000.0) -> dict:
    return {"d": d, "close": close, "volume": volume}


def tape(start: date, closes: list[float], volume: float | None = 1000.0) -> list[dict]:
    """One bar per calendar day from ``start`` (the windows count bars, so spacing does not matter here)."""
    return [bar(start + timedelta(days=i), c, volume) for i, c in enumerate(closes)]


def split(
    effective: date, ratio_from: int, ratio_to: int, status: str = "confirmed"
) -> ListedSplit:
    return ListedSplit(
        key_date=effective,
        effective_date=effective,
        ratio_from=Decimal(ratio_from),
        ratio_to=Decimal(ratio_to),
        status=status,
    )


def cover_row(
    value: float,
    period_end: date,
    filed: date,
    fiscal_period: str | None = "Q1",
    metric_key: str = "shares_out_cover_xbrl",
) -> dict:
    return {
        "metric_key": metric_key,
        "value": Decimal(str(value)),
        "period_end": period_end,
        "valid_from": filed,
        "fiscal_period": fiscal_period,
    }


# ---------------------------------------------------------------------------------------------------------
# year to date
# ---------------------------------------------------------------------------------------------------------


def test_ytd_is_the_last_close_against_the_last_close_on_or_before_dec_31_of_the_prior_year():
    bars = [
        bar(date(2025, 12, 30), 10.0),
        bar(date(2025, 12, 31), 12.0),  # the base: the last close on or before Dec 31
        bar(date(2026, 1, 2), 13.0),
        bar(date(2026, 5, 29), 15.0),
    ]
    r = ytd_return(bars, ASOF)
    assert r.value == 25.0 and r.note is None  # 15 / 12 - 1


def test_ytd_base_is_the_prior_session_when_dec_31_has_no_bar():
    # Dec 31 2022 was a Saturday: the base is Friday Dec 30's close
    bars = [
        bar(date(2022, 12, 29), 9.0),
        bar(date(2022, 12, 30), 10.0),
        bar(date(2023, 3, 1), 11.0),
    ]
    assert ytd_return(bars, date(2023, 3, 1)).value == 10.0


def test_ytd_at_a_past_asof_is_against_the_year_before_that_asofs_year():
    bars = [
        bar(date(2024, 12, 31), 20.0),
        bar(date(2025, 6, 2), 22.0),
        bar(date(2025, 12, 31), 40.0),
        bar(date(2026, 5, 29), 50.0),
    ]
    assert (
        ytd_return([b for b in bars if b["d"] <= date(2025, 6, 2)], date(2025, 6, 2)).value == 10.0
    )
    assert ytd_return(bars, ASOF).value == 25.0


def test_a_company_listed_this_year_has_a_ytd_gap_with_the_reason():
    bars = [bar(date(2026, 2, 3), 5.0), bar(date(2026, 5, 29), 8.0)]
    r = ytd_return(bars, ASOF)
    assert r.value is None
    assert r.note == "no close on or before 2025-12-31 (first bar 2026-02-03)"


def test_no_session_yet_this_year_is_a_gap_not_a_zero():
    r = ytd_return([bar(date(2025, 12, 30), 5.0)], date(2026, 1, 2))
    assert r.value is None and "no session yet in 2026" in (r.note or "")


def test_ytd_gaps_for_no_bars_and_a_non_positive_base():
    assert ytd_return([], ASOF).note == "no price bars at the as-of"
    assert ytd_return([bar(date(2025, 12, 31), None)], ASOF).note == "no price bars at the as-of"
    zero = ytd_return([bar(date(2025, 12, 31), 0.0), bar(date(2026, 3, 2), 4.0)], ASOF)
    assert zero.value is None and zero.note == "non-positive base close"


# ---------------------------------------------------------------------------------------------------------
# dollar volume 20D
# ---------------------------------------------------------------------------------------------------------


def test_dollar_volume_is_the_mean_of_close_times_volume_over_the_last_20_sessions():
    bars = tape(date(2026, 4, 1), [1.0] * 5 + [10.0] * 20)  # 25 bars: only the last 20 count
    r = dollar_volume_20d(bars)
    assert r.value == 10.0 * 1000.0 and r.note is None


def test_fewer_than_20_sessions_is_a_gap_not_a_partial_mean():
    r = dollar_volume_20d(tape(date(2026, 5, 1), [10.0] * 19))
    assert r.value is None and r.note == "19 of 20 sessions"
    assert dollar_volume_20d([]).note == "no price bars at the as-of"


def test_a_missing_volume_in_the_window_is_a_gap():
    bars = tape(date(2026, 5, 1), [10.0] * 20)
    bars[3] = bar(bars[3]["d"], 10.0, None)
    r = dollar_volume_20d(bars)
    assert r.value is None and r.note == "volume missing on 1 of the last 20 sessions"
    # a missing volume OLDER than the 20-session window does not matter
    older = tape(date(2026, 4, 1), [10.0] * 21)
    older[0] = bar(older[0]["d"], 10.0, None)
    assert dollar_volume_20d(older).value == 10_000.0


def test_dollar_volume_bars_constant_is_the_window():
    assert DOLLAR_VOLUME_BARS == 20


# ---------------------------------------------------------------------------------------------------------
# the six windows
# ---------------------------------------------------------------------------------------------------------


def test_the_windows_are_the_cockpits_five_with_ytd_between_90d_and_1y():
    assert WINDOWS == ("ret_1d", "ret_7d", "ret_30d", "ret_90d", "ytd", "ret_1y")
    assert set(TRAILING) < set(WINDOWS)


def test_window_returns_read_the_trailing_member_and_say_why_a_window_is_blank():
    bars = tape(
        date(2025, 12, 1), [10.0 + i * 0.1 for i in range(91)]
    )  # 91 bars through 2026-03-01
    sig = trailing_returns.compute(bars, date(2026, 3, 1))
    returns, gaps = window_returns(sig, bars, date(2026, 3, 1))
    assert returns["ret_1d"] == round((bars[-1]["close"] / bars[-2]["close"] - 1) * 100, 2)
    assert returns["ret_90d"] is not None  # 91 bars: exactly enough for 90 back
    assert returns["ret_1y"] is None and gaps["ret_1y"] == "91 of 253 bars"
    assert returns["ytd"] is not None and "ytd" not in gaps
    assert set(returns) == set(WINDOWS)


def test_window_returns_with_no_signal_gap_every_trailing_window():
    returns, gaps = window_returns(None, [], ASOF)
    assert all(v is None for v in returns.values())
    assert set(gaps) == set(WINDOWS)
    assert gaps["ret_7d"] == "no price bars at the as-of"


def test_gap_reason_words():
    assert gap_reason("n/a: 61/91 bars") == "61 of 91 bars"
    assert gap_reason("n/a: non-positive base close") == "non-positive base close"
    assert gap_reason(None) == "no reading"


def test_a_benchmark_reads_by_the_same_rule_and_none_without_a_tape():
    bars = tape(date(2025, 12, 20), [100.0 + i for i in range(100)])
    r = benchmark_returns(bars, date(2026, 3, 29))
    assert r is not None and set(r) == set(WINDOWS)
    assert r["ret_1d"] == round((bars[-1]["close"] / bars[-2]["close"] - 1) * 100, 2)
    assert r["ret_1y"] is None
    assert benchmark_returns([], ASOF) is None
    assert benchmark_returns([bar(date(2026, 5, 29), None)], ASOF) is None


# ---------------------------------------------------------------------------------------------------------
# the cap: the Workbench count wins, the filing cover stands in for a 10-Q filer, the source is named
# ---------------------------------------------------------------------------------------------------------

CLOSE = (date(2026, 5, 29), 5.0)


def test_the_workbench_count_wins_and_the_cover_is_never_consulted():
    wb = [
        {
            "shares": 40_000_000,
            "valid_from": date(2026, 3, 1),
            "ads_ratio": None,
            "ads_ratio_status": None,
        }
    ]
    rows = [cover_row(999_000_000, date(2026, 5, 5), date(2026, 5, 10))]
    shares, source, _ = pick_shares(wb, rows, ASOF)
    assert source == "workbench" and shares is not None and shares["shares"] == 40_000_000
    cap = company_cap(CLOSE, shares, source)
    assert cap.cell.value == 200_000_000.0 and cap.cell.tier is not None
    assert cap.cell.tier.value == "micro" and cap.source == "workbench"


def test_the_latest_workbench_fact_by_valid_from_is_the_pick():
    wb = [
        {"shares": 1, "valid_from": date(2026, 1, 1), "ads_ratio": None, "ads_ratio_status": None},
        {"shares": 3, "valid_from": date(2026, 4, 1), "ads_ratio": None, "ads_ratio_status": None},
        {"shares": 2, "valid_from": date(2026, 2, 1), "ads_ratio": None, "ads_ratio_status": None},
    ]
    shares, _, _ = pick_shares(wb, [], ASOF)
    assert shares is not None and shares["shares"] == 3


def test_the_cover_count_stands_in_for_a_quarterly_filer_and_is_labeled():
    rows = [
        cover_row(30_000_000, date(2026, 2, 20), date(2026, 3, 1), "FY"),
        cover_row(50_000_000, date(2026, 5, 5), date(2026, 5, 10), "Q1"),
    ]
    shares, source, _ = pick_shares([], rows, ASOF)
    assert source == "cover" and shares is not None and shares["shares"] == 50_000_000.0
    assert shares["valid_from"] == date(2026, 5, 5)  # the cover's own date, not the filing's
    assert shares["ads_ratio"] is None and shares["ads_ratio_status"] is None
    cap = company_cap(CLOSE, shares, source)
    assert cap.cell.value == 250_000_000.0 and cap.source == "cover"


def test_a_company_with_only_annual_cover_counts_is_not_stood_in_for():
    rows = [cover_row(30_000_000, date(2026, 2, 20), date(2026, 3, 1), "FY")]
    assert cover_shares(rows, ASOF) == CoverPick(
        None
    )  # not a 10-Q filer: no stand-in, and nothing to explain
    assert pick_shares([], rows, ASOF) == (None, None, None)
    shares, source, _ = pick_shares([], rows, ASOF)
    cap = company_cap(CLOSE, shares, source)
    assert cap.cell.value is None and cap.cell.gap == "no_shares" and cap.source is None


def test_other_share_metrics_and_null_fiscal_periods_do_not_count_as_a_quarterly_cover():
    rows = [
        cover_row(
            10_000_000, date(2026, 5, 5), date(2026, 5, 10), "Q1", metric_key="shares_out_xbrl"
        ),
        cover_row(10_000_000, date(2026, 5, 5), date(2026, 5, 10), None),
    ]
    assert cover_shares(rows, ASOF).shares is None


def test_the_latest_cover_row_is_by_filing_date_then_period_end():
    rows = [
        cover_row(1_000_000, date(2026, 5, 5), date(2026, 5, 10), "Q1"),
        cover_row(2_000_000, date(2026, 5, 28), date(2026, 5, 29), "Q1"),  # a later filing
        cover_row(
            3_000_000, date(2026, 5, 4), date(2026, 5, 29), "Q1"
        ),  # same filing day, older period end
    ]
    got = cover_shares(rows, ASOF).shares
    assert got is not None and got["shares"] == 2_000_000.0


def test_the_cover_count_is_scaled_by_the_split_list_from_the_covers_own_date():
    """100M shares on the cover dated 2026-03-01, filed 2026-04-15; a 1-for-10 reverse split took effect
    2026-04-01 (after the cover's date, before the filing): the count is put on today's scale, $5 x 10M = $50M.
    Dated from the filing (04-15) the split would be missed and the cap would read $500M."""
    rows = [cover_row(100_000_000, date(2026, 3, 1), date(2026, 4, 15), "Q1")]
    shares, source, _ = pick_shares([], rows, ASOF)
    cap = company_cap(CLOSE, shares, source, [split(date(2026, 4, 1), 10, 1)])
    assert cap.cell.value == 50_000_000.0 and cap.cell.split_factor == pytest.approx(0.1)
    assert cap.cell.tier is not None and cap.cell.tier.value == "micro" and cap.source == "cover"
    # a split that is not in force (needs review) changes nothing
    unsettled = company_cap(CLOSE, shares, source, [split(date(2026, 4, 1), 10, 1, "needs_review")])
    assert unsettled.cell.value == 500_000_000.0


def test_a_cover_date_later_than_its_filing_is_clamped_to_the_filing():
    rows = [cover_row(10_000_000, date(2034, 3, 5), date(2026, 5, 10), "Q1")]  # a date typo
    got = cover_shares(rows, ASOF).shares
    assert got is not None and got["valid_from"] == date(2026, 5, 10)


def test_a_workbench_count_withheld_by_its_ads_ratio_keeps_the_gap_and_no_source():
    wb = [
        {
            "shares": 10_000_000,
            "valid_from": date(2026, 3, 1),
            "ads_ratio": None,
            "ads_ratio_status": "unread",
        }
    ]
    shares, source, _ = pick_shares(wb, [cover_row(1, date(2026, 5, 5), date(2026, 5, 10))], ASOF)
    cap = company_cap(CLOSE, shares, source)
    assert cap.cell.value is None and cap.cell.gap == "ads_unread" and cap.source is None


def test_no_close_is_a_no_price_gap():
    cap = company_cap(None, {"shares": 1, "valid_from": date(2026, 1, 1)}, "cover")
    assert cap.cell.value is None and cap.cell.gap == "no_price" and cap.source is None


# the cover stand-in is refused when it is stale or a placeholder (MEASURED on a prod copy, 2026-10-30: Visa's and
# Mastercard's latest cover rows are from 2010, FOXA's is 2019 and the value 1)


def test_the_two_thresholds_are_named_constants():
    assert (COVER_MAX_AGE_DAYS, COVER_MIN_SHARES) == (200, 10_000)


def test_a_recent_cover_count_is_used():
    rows = [cover_row(50_000_000, date(2026, 5, 5), date(2026, 5, 10), "Q1")]
    got = cover_shares(rows, ASOF)
    assert got.refused is None and got.shares is not None and got.shares["shares"] == 50_000_000.0


def test_a_stale_cover_count_is_refused_with_its_date_and_leaves_the_gap():
    rows = [cover_row(5_000_000_000, date(2010, 10, 29), date(2010, 11, 2), "Q3")]  # Visa's shape
    got = cover_shares(rows, ASOF)
    assert got.shares is None
    assert got.refused == "the filing cover's count is from 2010-11-02, too old to stand in"
    picked = pick_shares([], rows, ASOF)
    assert (picked.shares, picked.source, picked.refused) == (None, None, got.refused)
    cap = company_cap(CLOSE, picked.shares, picked.source, (), picked.refused)
    assert cap.cell.value is None and cap.cell.gap == "no_shares" and cap.source is None
    assert cap.note == got.refused


def test_a_placeholder_cover_count_is_refused():
    rows = [cover_row(1, date(2026, 5, 5), date(2026, 5, 10), "Q1")]  # FOXA's and QVCG's shape
    got = cover_shares(rows, ASOF)
    assert got.shares is None
    assert got.refused == "the filing cover's count (1) is not a share count"
    # at the floor it is a share count, one below it is not
    assert cover_shares(
        [cover_row(COVER_MIN_SHARES, date(2026, 5, 5), date(2026, 5, 10))], ASOF
    ).shares
    below = cover_shares(
        [cover_row(COVER_MIN_SHARES - 1, date(2026, 5, 5), date(2026, 5, 10))], ASOF
    )
    assert (
        below.shares is None
        and below.refused == "the filing cover's count (9,999) is not a share count"
    )


def test_the_200_days_are_inclusive_and_counted_from_the_filing_date():
    filed = ASOF - timedelta(days=COVER_MAX_AGE_DAYS)
    ok = cover_shares([cover_row(5_000_000, filed - timedelta(days=40), filed)], ASOF)
    assert ok.shares is not None  # exactly 200 days: used
    late = filed - timedelta(days=1)
    gone = cover_shares([cover_row(5_000_000, late - timedelta(days=40), late)], ASOF)
    assert (
        gone.shares is None and gone.refused is not None and "too old" in gone.refused
    )  # 201 days


def test_the_age_is_judged_from_a_past_asof_not_from_today():
    """One cover count filed 2026-01-15: at the as-of 2026-06-01 it is 137 days old and used; at 2026-09-01 it is
    228 days old and refused; at 2026-02-01 it is 17 days old and used. The same rule, each against its own day
    (today's date never enters)."""
    rows = [cover_row(5_000_000, date(2026, 1, 10), date(2026, 1, 15), "Q1")]
    assert cover_shares(rows, date(2026, 6, 1)).shares is not None
    assert cover_shares(rows, date(2026, 2, 1)).shares is not None
    late = cover_shares(rows, date(2026, 9, 1))
    assert late.shares is None and "from 2026-01-15, too old" in (late.refused or "")


def test_only_the_latest_cover_row_is_a_candidate_never_an_older_one():
    """The latest row is a placeholder while an older one is a real count: the older is staler, so there is no
    stand-in (the gap stays) rather than a quieter wrong number."""
    rows = [
        cover_row(50_000_000, date(2026, 2, 20), date(2026, 3, 1), "Q4"),
        cover_row(50_000_000, date(2026, 2, 20), date(2026, 3, 1), "Q1"),
        cover_row(1, date(2026, 5, 5), date(2026, 5, 10), "Q1"),
    ]
    got = cover_shares(rows, ASOF)
    assert got.shares is None and got.refused == "the filing cover's count (1) is not a share count"


def test_the_refusal_reason_rides_the_payload_as_cap_note():
    rows = [ms(T1, "Grid", "VISA", S[0]), ms(T1, "Grid", "OK", S[1])]
    refused = "the filing cover's count is from 2010-11-02, too old to stand in"
    no_cap = CompanyCap(CapCell(gap="no_shares"), None, refused)
    out = market_map(inputs(rows, {S[0]: reading(cap=no_cap), S[1]: reading()}), {}, asof=ASOF)
    visa, ok = out.companies[1], out.companies[0]  # sorted by ticker: OK, VISA
    assert (visa.ticker, visa.cap, visa.cap_gap, visa.cap_note) == (
        "VISA",
        None,
        "no_shares",
        refused,
    )
    assert (ok.ticker, ok.cap_note) == ("OK", None)


# ---------------------------------------------------------------------------------------------------------
# a company's reading and the payload
# ---------------------------------------------------------------------------------------------------------

NOCAP = CompanyCap(CapCell(gap="no_shares"), None)


def reading(r1d: float | None = 1.0, cap: CompanyCap = NOCAP, **kw) -> CompanyReading:
    returns = {w: None for w in WINDOWS}
    returns["ret_1d"] = r1d
    return CompanyReading(
        returns=returns,
        gaps={w: "n/a" for w in WINDOWS if returns[w] is None},
        priced=r1d is not None,
        cap=cap,
        **kw,
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


def inputs(memberships, readings, flagged=None, **kw) -> MapInputs:
    base = dict(
        live_cards=len({m.thesis_id for m in memberships}),
        cards_counted=len({m.thesis_id for m in memberships}),
        cards_not_yet_created=0,
        roster_fallback_cards=0,
        spy={w: 1.0 for w in WINDOWS},
        iwm=None,
    )
    base.update(kw)
    return MapInputs(
        memberships=tuple(memberships), readings=readings, flagged=flagged or {}, **base
    )


def test_company_reading_puts_the_windows_the_dollar_volume_and_the_cap_together():
    bars = tape(date(2025, 12, 20), [10.0 + i * 0.1 for i in range(100)])
    sig = trailing_returns.compute(bars, date(2026, 3, 29))
    cap = company_cap(
        (bars[-1]["d"], bars[-1]["close"]),
        {"shares": 1_000_000, "valid_from": date(2026, 1, 1)},
        "cover",
    )
    r = company_reading(bars, sig, date(2026, 3, 29), cap)
    assert r.priced and r.returns["ret_7d"] is not None and r.dollar_volume is not None
    assert r.dollar_volume_note is None and r.cap.source == "cover"
    blank = company_reading([], None, ASOF, NOCAP)
    assert (
        not blank.priced
        and blank.dollar_volume is None
        and blank.dollar_volume_note == "no price bars at the as-of"
    )


def test_the_payload_carries_every_membership_marked_and_a_company_once():
    rows = [
        ms(T1, "Grid", "AAA", S[0], "Turbines"),
        ms(T1, "Grid", "AAA", S[0], "Cables"),  # the same company in a second link of the card
        ms(T1, "Grid", "AAA", S[0], "Cables"),  # an exact duplicate row: shown once
        ms(T2, "Fuel", "AAA", S[0], "Core"),
        ms(T1, "Grid", "CTX", S[1], CONTEXT_LABEL),  # silent only
        ms(T1, "Grid", "BBB", S[2], None),  # no link
    ]
    readings = {S[0]: reading(2.0), S[1]: reading(-1.0), S[2]: reading(0.5)}
    identity = {
        S[0]: {
            "name": "Acme Corp",
            "business_type": "utilities",
            "business_supersector": "energy_utilities",
        },
        S[2]: {"name": "Bee Co"},
    }
    out = market_map(inputs(rows, readings), identity, asof=ASOF)

    assert [c.ticker for c in out.companies] == ["AAA", "BBB", "CTX"]
    aaa = out.companies[0]
    assert (aaa.name, aaa.business_type, aaa.supersector) == (
        "Acme Corp",
        "utilities",
        "energy_utilities",
    )
    assert aaa.silent_only is False and out.companies[2].silent_only is True
    assert out.companies[1].business_type is None  # unclassified: None, never a made-up label
    mine = [(m.thesis_id, m.link, m.silent) for m in out.memberships if m.security_id == S[0]]
    assert sorted(mine, key=str) == sorted(
        [(T1, "Turbines", False), (T1, "Cables", False), (T2, "Core", False)], key=str
    )
    assert [(m.link, m.silent) for m in out.memberships if m.security_id == S[1]] == [
        (CONTEXT_LABEL, True)
    ]
    assert [m.link for m in out.memberships if m.security_id == S[2]] == [None]
    assert [c.name for c in out.cards] == ["Fuel", "Grid"]
    assert (out.basket_rows, out.silent_rows, out.companies_total) == (6, 1, 3)


def test_unresolved_rows_are_counted_never_drawn():
    rows = [
        ms(T1, "Grid", "AAA", S[0]),
        ms(T1, "Grid", "GONE", None),
        ms(T1, "Grid", "GONE2", None, CONTEXT_LABEL),
    ]
    out = market_map(inputs(rows, {S[0]: reading()}), {}, asof=ASOF)
    assert (out.unresolved_rows, out.unresolved_silent_rows) == (1, 1)
    assert [c.ticker for c in out.companies] == ["AAA"] and len(out.memberships) == 1


def test_a_flagged_company_is_left_off_the_map_listed_and_its_rows_go_with_it():
    rows = [
        ms(T1, "Grid", "RBOT", S[0]),
        ms(T1, "Grid", "AAA", S[1]),
        ms(T2, "Fuel", "RBOT", S[0]),
        ms(T2, "Fuel", "CTXF", S[2], CONTEXT_LABEL),
    ]
    flagged = {S[0]: "mixed-scale price history", S[2]: "price feed stopped"}
    out = market_map(inputs(rows, {S[1]: reading()}, flagged), {}, asof=ASOF)
    assert [c.ticker for c in out.companies] == ["AAA"]
    assert [m.security_id for m in out.memberships] == [S[1]]
    assert [(f.ticker, f.reason, f.silent_only) for f in out.flagged] == [
        ("CTXF", "price feed stopped", True),
        ("RBOT", "mixed-scale price history", False),
    ]
    assert out.companies_total == 3  # a left-out company still counts in the total


def test_a_ticker_that_differs_across_cards_shows_the_one_most_cards_use():
    rows = [
        ms(T1, "Grid", "AAA", S[0]),
        ms(T2, "Fuel", "AAA.B", S[0]),
        ms(uuid.uuid4(), "Third", "AAA", S[0]),
    ]
    out = market_map(inputs(rows, {S[0]: reading()}), {}, asof=ASOF)
    assert out.companies[0].ticker == "AAA"
    assert sorted(m.ticker for m in out.memberships) == [
        "AAA",
        "AAA",
        "AAA.B",
    ]  # each card keeps its own


def test_the_cap_fields_name_the_source_or_the_reason():
    capped = company_cap(
        (date(2026, 5, 29), 5.0), {"shares": 50_000_000, "valid_from": date(2026, 1, 1)}, "cover"
    )
    rows = [ms(T1, "Grid", "AAA", S[0]), ms(T1, "Grid", "BBB", S[1])]
    out = market_map(inputs(rows, {S[0]: reading(cap=capped), S[1]: reading()}), {}, asof=ASOF)
    a, b = out.companies
    assert (a.cap, a.cap_tier, a.cap_source, a.cap_gap) == (250_000_000.0, "micro", "cover", None)
    assert (b.cap, b.cap_tier, b.cap_source, b.cap_gap) == (None, None, None, "no_shares")


# ---------------------------------------------------------------------------------------------------------
# the router (pure: the heavy read stubbed)
# ---------------------------------------------------------------------------------------------------------


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0}
    rows = [
        ms(T1, "Grid", "AAA", S[0], "Turbines"),
        ms(T2, "Fuel", "AAA", S[0], "Core"),
        ms(T2, "Fuel", "BBB", S[1]),
    ]
    readings = {S[0]: reading(2.0), S[1]: reading(-1.0)}
    identity = {
        S[0]: {
            "name": "Acme",
            "business_type": "utilities",
            "business_supersector": "energy_utilities",
        }
    }

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


def test_the_router_threads_the_view_and_serves_every_request_off_one_cached_read(stub_client):
    client, seen = stub_client
    first = client.get("/dashboards/market-map", params={"asof": "2026-06-01"}).json()
    again = client.get("/dashboards/market-map", params={"asof": "2026-06-01"}).json()
    assert first["cache"] == "computed" and again["cache"] == "hit" and seen["reads"] == 1
    asof, known_at, clock = seen["view"]
    assert asof == date(2026, 6, 1) and clock == "public" and known_at is not None
    assert seen["identity_ids"] == {S[0], S[1]}

    today = market_today().isoformat()
    client.get("/dashboards/market-map", params={"asof": today})
    assert seen["view"][1:] == (None, "record") and seen["reads"] == 2


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get("/dashboards/market-map", params={"asof": "2026-06-01"}).json()
    assert body["windows"] == list(WINDOWS) and body["dollar_volume_bars"] == 20
    assert body["spy"]["ytd"] == 1.0 and body["iwm"] is None
    aaa = next(c for c in body["companies"] if c["ticker"] == "AAA")
    assert (
        aaa["name"] == "Acme"
        and aaa["business_type"] == "utilities"
        and aaa["returns"]["ret_1d"] == 2.0
    )
    assert (
        set(aaa["returns"]) == set(WINDOWS) and aaa["cap_gap"] == "no_shares" and aaa["cap"] is None
    )
    assert sorted(
        (m["link"], m["silent"]) for m in body["memberships"] if m["security_id"] == str(S[0])
    ) == [
        ("Core", False),
        ("Turbines", False),
    ]
    assert {c["name"] for c in body["cards"]} == {"Grid", "Fuel"}
    assert (body["basket_rows"], body["companies_total"], body["flagged"]) == (3, 2, [])


def test_the_router_requires_an_asof(stub_client):
    client, seen = stub_client
    assert client.get("/dashboards/market-map").status_code == 422
    assert seen["reads"] == 0


def test_the_router_reads_flags_once_inside_the_cached_read_and_never_reads_a_flagged_companys_prices(
    monkeypatch,
):
    rows = [ms(T1, "Grid", "RBOT", S[0]), ms(T1, "Grid", "AAA", S[1])]
    seen: dict = {"flag_reads": 0}

    def fake_flags(conn, ids, asof, *, known_at, clock):
        seen["flag_reads"] += 1
        seen["flags_view"] = (set(ids), asof, known_at, clock)
        return {S[0]: "mixed-scale price history"}

    def fake_readings(conn, sids, asof, known_at, clock):
        seen["read_sids"] = list(sids)
        seen["readings_view"] = (asof, known_at, clock)
        return {s: reading(3.0) for s in sids}

    CACHE.clear()
    monkeypatch.setattr(
        router_mod, "cards_as_of", lambda conn, asof, known_at: CardsAsOf(2, [], 0, 0)
    )
    monkeypatch.setattr(router_mod, "memberships_of", lambda cards: tuple(rows))
    monkeypatch.setattr(router_mod, "_readings", fake_readings)
    monkeypatch.setattr(
        router_mod, "_benchmarks", lambda conn, asof, known_at, clock: {"SPY": None, "IWM": None}
    )
    monkeypatch.setattr(router_mod, "price_flags", fake_flags)
    monkeypatch.setattr(router_mod.master, "identity_for", lambda conn, ids, **kw: {})
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    try:
        client = TestClient(app)
        body = client.get("/dashboards/market-map", params={"asof": "2026-06-01"}).json()
        again = client.get("/dashboards/market-map", params={"asof": "2026-06-01"}).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    ids, asof, known_at, clock = seen["flags_view"]
    assert seen["flag_reads"] == 1 and again["cache"] == "hit"
    assert ids == {S[0], S[1]} and asof == ASOF and clock == "public" and known_at is not None
    assert seen["read_sids"] == [S[1]]  # the flagged company's prices are not read
    assert seen["readings_view"] == (ASOF, known_at, "public")
    assert [c["ticker"] for c in body["companies"]] == ["AAA"]
    assert body["flagged"] == [
        {
            "security_id": str(S[0]),
            "ticker": "RBOT",
            "reason": "mixed-scale price history",
            "silent_only": False,
        }
    ]
    assert body["spy"] is None and body["iwm"] is None


# ---------------------------------------------------------------------------------------------------------
# DB-backed: a bar, a cover share count and a card published after a past as-of stay invisible (skips without
# Postgres)
# ---------------------------------------------------------------------------------------------------------

UTC = timezone.utc
_INGEST = datetime(2026, 9, 1, tzinfo=UTC)  # our own ingest instant, after the as-of


def _price(
    db, sid, d: date, close: float, volume: float = 1000.0, recorded_at: datetime | None = None
) -> None:
    append_fact(
        db,
        "fact_price_eod",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "d": d,
            "close": close,
            "volume": volume,
            "valid_from": d,
            "recorded_at": recorded_at or datetime.combine(d, time.min, tzinfo=UTC),
        },
    )


def _security(
    db, ticker: str, name: str | None = None, kind: str = "equity", cik: str | None = None
) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik or "0009876543", name, kind, "2025-01-01"),
        )
    return sid


def _cover(db, sid, value: float, period_end: date, filed: date, fp: str = "Q1") -> None:
    append_fact(
        db,
        "fact_fundamentals",
        {
            "tenant_id": DEFAULT_TENANT_ID,
            "security_id": sid,
            "metric_key": "shares_out_cover_xbrl",
            "period_end": period_end,
            "fiscal_period": fp,
            "value": value,
            "unit": "shares",
            "basis": "native",
            "accession": f"0001-{period_end.isoformat()}",
            "valid_from": filed,  # a count is public from the day its filing was
            "recorded_at": _INGEST,
        },
    )


def _card(db, name: str, members: list[BasketMember], created: str = "2026-01-02") -> Thesis:
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core")],
        basket=members,
    )
    thesis_repo.upsert(db, thesis)
    with db.cursor() as cur:  # a fixture backdate, not an app write
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", (created, thesis.id))
    db.commit()
    return thesis


def test_a_bar_a_cover_count_and_a_card_published_after_a_past_asof_stay_invisible(db, monkeypatch):
    """MMA is a quarterly filer with no Workbench count: its 10-Q cover count of 40M shares was filed before the
    as-of, a later one of 400M after it. Its tape runs a year back across December 31 and ends at the as-of; a bar
    after the as-of (a $90 close, recorded after it) and the later cover count must not reach the past view. A
    Context member rides the payload marked silent. A card created after the as-of is not on the map. The same
    company read live today (the tape ended 2026-06-01: a stopped feed) is left off and listed."""
    asof = ASOF
    spy = _security(db, "SPY", "SPDR S&P 500", kind="etf", cik=None)
    mma = _security(db, "MMA", "Alpha Co", cik="0000111111")
    ctx = _security(db, "CTXN", "Context Co", cik="0000222222")
    later_co = _security(db, "LATE", "Late Co", cik="0000333333")

    start = asof - timedelta(days=364)  # 2025-06-02 .. 2026-06-01 inclusive
    for i in range(365):
        d = start + timedelta(days=i)
        _price(db, mma, d, 10.0 + i * 0.05)
        _price(db, ctx, d, 5.0)
        _price(db, spy, d, 400.0 + i)
    _price(
        db, mma, asof + timedelta(days=1), 90.0
    )  # public the day after the as-of: invisible to it
    _cover(db, mma, 40_000_000, date(2026, 5, 5), date(2026, 5, 10), "Q1")
    _cover(
        db, mma, 400_000_000, date(2026, 6, 10), date(2026, 6, 12), "Q1"
    )  # filed after the as-of

    _card(
        db,
        "Map fixture",
        [
            BasketMember(ticker="MMA", role="the name", security_id=mma, segment="Core"),
            BasketMember(ticker="CTXN", role="context", security_id=ctx, segment=CONTEXT_LABEL),
        ],
    )
    _card(
        db,
        "Born later",
        [BasketMember(ticker="LATE", role="the name", security_id=later_co, segment="Core")],
        created="2026-06-10",
    )

    CACHE.clear()
    app.dependency_overrides[get_conn] = lambda: db
    try:
        client = TestClient(app)
        past = client.get("/dashboards/market-map", params={"asof": asof.isoformat()}).json()
        live = client.get(
            "/dashboards/market-map", params={"asof": market_today().isoformat()}
        ).json()
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    by_ticker = {c["ticker"]: c for c in past["companies"]}
    assert set(by_ticker) == {"MMA", "CTXN"} and past["cards_counted"] == 1
    assert past["cards_not_yet_created"] == 1 and "LATE" not in by_ticker
    assert [c["name"] for c in past["cards"]] == ["Map fixture"]

    a = by_ticker["MMA"]
    last_close = 10.0 + 364 * 0.05  # the as-of's own bar; the $90 bar is a day later
    assert a["priced"] and a["name"] == "Alpha Co"
    assert a["returns"]["ret_1d"] == round((last_close / (last_close - 0.05) - 1) * 100, 2)
    base = 10.0 + (date(2025, 12, 31) - start).days * 0.05  # the Dec 31 2025 close
    assert a["returns"]["ytd"] == round((last_close / base - 1) * 100, 2)
    year_ago = (
        10.0 + (364 - 252) * 0.05
    )  # 252 bars back; the 365-bar tape is long enough for the 1Y window
    assert (
        a["returns"]["ret_1y"] == round((last_close / year_ago - 1) * 100, 2)
        and "ret_1y" not in a["gaps"]
    )
    assert a["dollar_volume"] == pytest.approx(
        sum((10.0 + i * 0.05) * 1000.0 for i in range(345, 365)) / 20, abs=0.01
    )
    # the cap: the cover count FILED BEFORE the as-of (40M), not the later 400M, on the last close
    assert a["cap_source"] == "cover" and a["cap"] == pytest.approx(
        40_000_000 * last_close, abs=1.0
    )
    assert a["cap_tier"] == "small" and a["cap_gap"] is None

    c = by_ticker["CTXN"]
    assert c["silent_only"] is True and c["cap"] is None and c["cap_gap"] == "no_shares"
    marks = {(m["security_id"], m["link"], m["silent"]) for m in past["memberships"]}
    assert marks == {(str(mma), "Core", False), (str(ctx), "Context", True)}
    assert past["spy"] is not None and past["spy"]["ret_1d"] == round((764 / 763 - 1) * 100, 2)
    assert past["iwm"] is None  # no IWM tape in this fixture: a stated None, never a made-up series
    # the card existed on the as-of with no roster snapshot then: today's roster stood in, and is counted
    assert past["roster_fallback_cards"] == 1 and past["flagged"] == []

    # today's view: the tape ends 2026-06-01, a stopped feed, so the company is left off the map and listed
    assert [c["ticker"] for c in live["companies"]] == []
    stopped = {f["ticker"]: f["reason"] for f in live["flagged"]}
    assert stopped["MMA"] == "price feed stopped" and stopped["CTXN"] == "price feed stopped"
    assert "LATE" in stopped  # today the later card counts too; it has no tape at all
