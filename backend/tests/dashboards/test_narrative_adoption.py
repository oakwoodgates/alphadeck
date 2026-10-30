"""Narrative Adoption (``docs/dashboards/narrative-adoption.md``): the 36-month axis ending at the as-of's month, a
month with no stored count ABSENT (never zero) beside a stored zero that is a zero, the stacking by form group
summed over the card's SIGNAL terms on hand-built rows (and the double count the page states), the sampled
"at least" mark on filers and the new-filers sum, a month read before it closed, the term table (windows, the
sort, "not counted yet", the first month it appeared, each term counted alone), BROAD terms left out and a
retired term ignored, the Narrative Index overlay on hand-computed numbers (one common start, a flagged name left
out, Context and Bench left out, no SPY tape), the job's run artifact read; the router off one cached read with its
404 and the weekly run in its cache key; and, DB-backed, a month that closes after a past as-of staying
invisible though stored, a past as-of ending the curve at the last month closed by then (on the last day, the day
before, the day after), the public clock ignoring when we stored a count, the live read, and the overlay reading
no bar after the as-of across the whole 36 months.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import narrative_adoption as router_mod
from dashboards.baskets import CardChoice, CardsRead, Membership
from dashboards.cache import CACHE
from dashboards.narrative_adoption import (
    BENCHMARK,
    MONTHS,
    FlaggedName,
    card_terms,
    narrative_adoption,
    overlay,
    run_identity,
    run_summary,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain import market_time
from domain.enums import TermTier
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, TermSetEntry, Thesis
from pipeline import term_mentions as tm
from pipeline.term_mentions import month_last_day, signal_terms
from repositories import thesis_repo

ASOF = date(2026, 10, 31)  # a Saturday, the last day of its month: October has closed
T1 = uuid.uuid4()
SA, SB, SC = (uuid.uuid4() for _ in range(3))
UTC = timezone.utc

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------


def mrow(
    term: str,
    month: date,
    *,
    reports: int = 0,
    events: int = 0,
    issuance: int = 0,
    other: int = 0,
    filers: int = 0,
    sampled: bool = False,
    new: int | None = None,
    fetched: datetime | None = None,
) -> dict:
    """One stored term-month as ``PointInTimeData.term_mentions`` returns it: the four groups sum to the
    documents, the CIK set has ``filers`` members. Read three days after the month closed unless told.
    """
    last = month_last_day(month)
    return {
        "term": term,
        "month": month,
        "documents": reports + events + issuance + other,
        "reports": reports,
        "events": events,
        "issuance": issuance,
        "other": other,
        "filers": filers,
        "filers_sampled": sampled,
        "filer_ciks": [f"{i:010d}" for i in range(filers)],
        "new_filers": new,
        "source_ref": f"https://efts.example/{term}/{month:%Y-%m}",
        "fetched_at": fetched or datetime.combine(last + timedelta(days=3), time(16), tzinfo=UTC),
        "valid_from": last,
    }


def m(y: int, mo: int) -> date:
    return date(y, mo, 1)


def term_set(*signal: str, broad: tuple[str, ...] = ()) -> list[dict]:
    return [{"term": t, "tier": "signal"} for t in signal] + [
        {"term": t, "tier": "broad"} for t in broad
    ]


def page(
    rows,
    *,
    terms=("alpha", "beta"),
    broad=(),
    asof=ASOF,
    ts=None,
    roster=(),
    closes=None,
    spy=None,
    flagged=None,
):
    return narrative_adoption(
        thesis_id=T1,
        thesis_name="Card A",
        asof=asof,
        term_set=ts if ts is not None else term_set(*terms, broad=broad),
        rows=rows,
        roster=list(roster),
        closes=closes or {},
        spy_closes=spy or {},
        flagged=flagged or {},
    )


def at(pg, y: int, mo: int):
    return next(p for p in pg.months if p.month == m(y, mo))


def term_row(pg, name: str):
    return next(t for t in pg.terms if t.term == name)


def ms(ticker: str, sid: UUID | None, segment: str | None = "Core") -> Membership:
    return Membership(
        thesis_id=T1,
        thesis_name="Card A",
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
    )


# ---------------------------------------------------------------------------------------------------------
# the axis
# ---------------------------------------------------------------------------------------------------------


def test_the_axis_is_36_months_ending_at_the_asofs_month_oldest_first():
    pg = page([], asof=date(2026, 10, 30))
    axis = [p.month for p in pg.months]
    assert MONTHS == 36 and len(axis) == 36
    assert axis[0] == m(2023, 11) and axis[-1] == m(2026, 10)
    assert axis == sorted(set(axis)) and all(d.day == 1 for d in axis)


def test_the_asofs_own_month_has_not_closed_until_its_last_day():
    mid = page([], asof=date(2026, 10, 30))
    assert [p.closed for p in mid.months].count(False) == 1 and not mid.months[-1].closed
    assert mid.counts.months_not_closed == 1
    end = page([], asof=date(2026, 10, 31))
    assert all(p.closed for p in end.months) and end.counts.months_not_closed == 0


def test_a_year_boundary_axis_runs_across_the_january():
    pg = page([], asof=date(2026, 1, 15))
    axis = [p.month for p in pg.months]
    assert axis[0] == m(2023, 2) and axis[-1] == m(2026, 1)
    assert m(2025, 12) in axis and m(2025, 1) in axis


# ---------------------------------------------------------------------------------------------------------
# absent is never zero
# ---------------------------------------------------------------------------------------------------------


def test_a_month_with_no_stored_count_is_absent_and_a_stored_zero_is_a_zero():
    rows = [
        mrow("alpha", m(2026, 8), reports=4, filers=2),
        mrow("alpha", m(2026, 9)),  # stored: the search found nothing that month
    ]
    pg = page(rows, terms=("alpha",))
    aug, sep, oct_, jul = at(pg, 2026, 8), at(pg, 2026, 9), at(pg, 2026, 10), at(pg, 2026, 7)
    assert (aug.counted, aug.documents) == (True, 4)
    assert (sep.counted, sep.documents, sep.filers, sep.top_terms) == (True, 0, 0, ())
    for absent in (oct_, jul):  # a month nobody stored, whether failed, deferred or not yet counted
        assert absent.counted is False and absent.terms_counted == 0
        assert (absent.documents, absent.reports, absent.events, absent.issuance, absent.other) == (
            None,
            None,
            None,
            None,
            None,
        )
        assert (absent.filers, absent.new_filers, absent.top_terms) == (None, None, ())
    assert pg.counts.months_counted == 2 and pg.counts.last_counted_month == m(2026, 9)


def test_rows_older_than_the_axis_are_not_drawn():
    rows = [
        mrow("alpha", m(2023, 10), reports=9, filers=1),
        mrow("alpha", m(2023, 11), reports=2, filers=1),
    ]
    pg = page(rows, terms=("alpha",), asof=date(2026, 10, 31))
    assert pg.months[0].month == m(2023, 11) and pg.months[0].documents == 2
    assert pg.counts.months_counted == 1 and term_row(pg, "alpha").months_stored == 1


# ---------------------------------------------------------------------------------------------------------
# the stacking, the double count, the filers
# ---------------------------------------------------------------------------------------------------------


def test_the_stacking_sums_each_form_group_over_the_terms_and_a_document_matching_two_terms_counts_twice():
    rows = [
        mrow("alpha", m(2026, 9), reports=5, events=3, issuance=2, other=1, filers=4),
        mrow("beta", m(2026, 9), reports=1, events=0, issuance=4, other=2, filers=3),
    ]
    sep = at(page(rows), 2026, 9)
    assert (sep.reports, sep.events, sep.issuance, sep.other) == (6, 3, 6, 3)
    # the term totals ADD (11 + 7): a filing that matches both terms is in both counts, so it counts twice
    assert sep.documents == 18 == sep.reports + sep.events + sep.issuance + sep.other
    assert sep.terms_counted == 2


def test_a_month_stored_for_only_some_terms_says_how_many_terms_it_rests_on():
    rows = [
        mrow("alpha", m(2026, 8), reports=1),
        mrow("beta", m(2026, 8), reports=1),
        mrow("gamma", m(2026, 8), reports=1),
        mrow("alpha", m(2026, 9), reports=1),
        mrow("gamma", m(2026, 9), reports=1),  # beta's September failed or is deferred
    ]
    pg = page(rows, terms=("alpha", "beta", "gamma"))
    assert (at(pg, 2026, 8).terms_counted, at(pg, 2026, 9).terms_counted) == (3, 2)
    assert (
        at(pg, 2026, 9).documents == 2
    )  # a floor: the sum of the two terms stored, not a zero for the third
    assert (pg.counts.months_counted, pg.counts.months_complete) == (2, 1)


def test_the_top_terms_are_the_three_with_the_most_documents_ties_by_name_and_never_a_zero():
    rows = [
        mrow("delta", m(2026, 9), reports=7),
        mrow("charlie", m(2026, 9), reports=7),
        mrow("beta", m(2026, 9), reports=2),
        mrow("alpha", m(2026, 9), reports=1),
        mrow("echo", m(2026, 9)),  # stored, no documents: never listed
    ]
    sep = at(page(rows, terms=("alpha", "beta", "charlie", "delta", "echo")), 2026, 9)
    assert [(t.term, t.documents) for t in sep.top_terms] == [
        ("charlie", 7),
        ("delta", 7),
        ("beta", 2),
    ]


def test_filers_are_summed_and_read_at_least_when_any_contributing_term_was_sampled():
    rows = [
        mrow("alpha", m(2026, 8), reports=2, filers=4),
        mrow("beta", m(2026, 8), reports=2, filers=3),
        mrow("alpha", m(2026, 9), reports=2, filers=5, sampled=True),
        mrow("beta", m(2026, 9), reports=2, filers=1),
    ]
    pg = page(rows)
    aug, sep = at(pg, 2026, 8), at(pg, 2026, 9)
    assert (aug.filers, aug.filers_sampled) == (7, False)
    assert (sep.filers, sep.filers_sampled) == (6, True)
    assert pg.counts.months_sampled == 1


def test_new_filers_sum_skips_a_terms_first_month_and_a_month_where_no_term_has_one_has_none():
    rows = [
        mrow("alpha", m(2026, 8), reports=2, filers=4, new=None),  # alpha's first stored month
        mrow("beta", m(2026, 8), reports=2, filers=3, new=None),
        mrow("alpha", m(2026, 9), reports=2, filers=4, new=2),
        mrow("beta", m(2026, 9), reports=2, filers=3, new=None),  # beta's first stored month
        mrow("alpha", m(2026, 10), reports=2, filers=4, new=0),
        mrow("beta", m(2026, 10), reports=2, filers=3, new=1),
    ]
    pg = page(rows)
    assert at(pg, 2026, 8).new_filers is None  # no earlier month: never "all new", never 0
    assert at(pg, 2026, 9).new_filers == 2
    assert at(pg, 2026, 10).new_filers == 1


def test_a_month_read_before_it_closed_is_marked_by_market_time_not_utc():
    rows = [
        # read 19:00 ET on the month's last day: before it closed
        mrow("alpha", m(2026, 9), reports=1, fetched=datetime(2026, 9, 30, 23, 0, tzinfo=UTC)),
        # read 01:00 ET the day after it closed, though it is still the 30th in UTC... no: Sept 1 UTC too
        mrow("alpha", m(2026, 8), reports=1, fetched=datetime(2026, 9, 1, 5, 0, tzinfo=UTC)),
        # read 23:30 ET on July 31, which is already August 1 in UTC: still before the month closed
        mrow("alpha", m(2026, 7), reports=1, fetched=datetime(2026, 8, 1, 3, 30, tzinfo=UTC)),
    ]
    pg = page(rows, terms=("alpha",))
    assert (at(pg, 2026, 9).read_early, at(pg, 2026, 8).read_early, at(pg, 2026, 7).read_early) == (
        True,
        False,
        True,
    )
    assert pg.counts.months_read_early == 2


# ---------------------------------------------------------------------------------------------------------
# the term table
# ---------------------------------------------------------------------------------------------------------


def test_a_term_with_no_stored_month_reads_not_counted_yet_never_zero_and_sorts_last():
    rows = [mrow("alpha", m(2026, 9), reports=3, filers=2)]
    pg = page(rows, terms=("alpha", "beta"))
    beta = term_row(pg, "beta")
    assert beta.counted is False and beta.months_stored == 0
    assert (beta.last_month_documents, beta.last_3_documents, beta.last_12_documents) == (
        None,
        None,
        None,
    )
    assert (beta.months_last_3, beta.months_last_12, beta.filers, beta.first_month) == (
        0,
        0,
        None,
        None,
    )
    assert [t.term for t in pg.terms] == ["alpha", "beta"]
    assert (pg.counts.signal_terms, pg.counts.terms_counted, pg.counts.terms_not_counted) == (
        2,
        1,
        1,
    )


def test_a_card_with_no_stored_month_at_all_has_an_empty_curve_and_every_term_not_counted_yet():
    pg = page([], terms=("alpha", "beta"))
    assert all(not p.counted for p in pg.months) and all(not t.counted for t in pg.terms)
    assert pg.counts.last_counted_month is None and pg.counts.months_counted == 0


def test_the_windows_end_at_the_last_counted_month_and_sum_the_months_stored_saying_how_many():
    rows = [
        mrow("alpha", m(2026, 10), reports=10, filers=2),
        mrow("alpha", m(2026, 9), reports=20, filers=3),
        # August: nothing stored (a failed or deferred month): a gap, never a zero
        mrow("alpha", m(2026, 7), reports=5),
        mrow("alpha", m(2026, 1), reports=100),
        mrow(
            "alpha", m(2025, 11), reports=7
        ),  # the first month of the twelve (Nov 2025 .. Oct 2026)
        mrow("alpha", m(2025, 10), reports=1000),  # one month too old for the twelve
    ]
    alpha = term_row(page(rows, terms=("alpha",)), "alpha")
    assert alpha.last_month_documents == 10 and (alpha.filers, alpha.filers_sampled) == (2, False)
    assert (alpha.last_3_documents, alpha.months_last_3) == (30, 2)  # Aug, Sep, Oct: two stored
    assert (alpha.last_12_documents, alpha.months_last_12) == (10 + 20 + 5 + 100 + 7, 5)
    assert alpha.months_stored == 6


def test_a_page_whose_last_counted_month_is_before_the_asofs_month_windows_end_there():
    rows = [mrow("alpha", m(2026, 8), reports=4), mrow("alpha", m(2026, 6), reports=9)]
    alpha = term_row(page(rows, terms=("alpha",), asof=date(2026, 10, 15)), "alpha")
    assert alpha.last_month_documents == 4 and (alpha.last_3_documents, alpha.months_last_3) == (
        13,
        2,
    )


def test_the_table_is_sorted_by_last_month_documents_and_each_term_counts_alone():
    rows = [
        mrow("alpha", m(2026, 10), reports=3, events=2, filers=4),  # 5
        mrow("beta", m(2026, 10), reports=50, filers=9),  # 50
        mrow("gamma", m(2026, 9), reports=8),  # stored, but not in the last month
    ]
    pg = page(rows, terms=("alpha", "beta", "gamma", "delta"))
    assert [t.term for t in pg.terms] == ["beta", "alpha", "gamma", "delta"]
    # the table counts each term alone: alpha's row is alpha's own 5, not the page's 55
    assert term_row(pg, "alpha").last_month_documents == 5
    assert at(pg, 2026, 10).documents == 55
    assert term_row(pg, "gamma").last_month_documents is None and term_row(pg, "gamma").counted


def test_first_appeared_is_the_first_month_with_a_document_and_says_when_the_term_may_be_older():
    rows = [
        mrow("alpha", m(2026, 1), reports=3),  # the earliest stored month already has documents
        mrow("beta", m(2026, 1)),  # stored with none
        mrow("beta", m(2026, 2), reports=3),
        mrow("gamma", m(2026, 1)),
        mrow("gamma", m(2026, 2)),
    ]
    pg = page(rows, terms=("alpha", "beta", "gamma"))
    alpha, beta, gamma = term_row(pg, "alpha"), term_row(pg, "beta"), term_row(pg, "gamma")
    assert (alpha.first_month, alpha.first_at_start) == (m(2026, 1), True)
    assert (beta.first_month, beta.first_at_start) == (m(2026, 2), False)
    assert (gamma.first_month, gamma.first_at_start) == (
        None,
        False,
    )  # counted, no document in them


# ---------------------------------------------------------------------------------------------------------
# which terms count
# ---------------------------------------------------------------------------------------------------------


def test_card_terms_are_the_jobs_own_signal_terms_and_broad_ones_are_counted_left_out():
    ts = [
        {"term": " Alpha ", "tier": "signal"},
        {
            "term": "alpha",
            "tier": "signal",
        },  # a case-insensitive duplicate: the first spelling stands
        {"term": "  ", "tier": "signal"},
        {"term": "Beta", "tier": "signal"},
        {"term": "b-one", "tier": "broad"},
        {"term": "B-ONE", "tier": "broad"},
        {"term": "b-two", "tier": "broad"},
    ]
    signal, broad = card_terms(ts)
    assert signal == signal_terms(ts) == ("Alpha", "Beta") and broad == 2
    pg = page([], ts=ts)
    assert pg.counts.signal_terms == 2 and pg.counts.broad_terms_left_out == 2


def test_a_stored_term_no_longer_in_the_term_set_is_ignored_and_counted():
    rows = [
        mrow("alpha", m(2026, 9), reports=3),
        mrow("old term", m(2026, 9), reports=99),
        mrow("old term", m(2026, 8), reports=99),
    ]
    pg = page(rows, terms=("alpha",))
    assert at(pg, 2026, 9).documents == 3 and at(pg, 2026, 8).counted is False
    assert pg.counts.retired_terms == 1 and [t.term for t in pg.terms] == ["alpha"]


def test_a_stored_row_matches_its_term_by_exact_spelling_so_an_edited_spelling_is_not_counted_yet():
    rows = [mrow("alpha term", m(2026, 9), reports=3)]
    pg = page(rows, ts=term_set("Alpha Term"))
    assert not term_row(pg, "Alpha Term").counted and at(pg, 2026, 9).counted is False
    assert pg.counts.retired_terms == 1 and pg.counts.terms_not_counted == 1


# ---------------------------------------------------------------------------------------------------------
# the Narrative Index overlay
# ---------------------------------------------------------------------------------------------------------

AXIS_START = date(2023, 11, 1)


def days(n: int, end: date = date(2026, 10, 30)) -> list[date]:
    return [end - timedelta(days=n - 1 - i) for i in range(n)]


def tape(ds: list[date], closes: list[float | None]) -> dict[date, float]:
    return {d: c for d, c in zip(ds, closes) if c is not None}


def over(roster, closes, spy, flagged=None):
    return overlay(
        roster, closes, spy, flagged or {}, asof=date(2026, 10, 30), axis_start=AXIS_START
    )


def test_the_overlay_is_the_equal_weighted_line_against_spy_both_100_at_the_start():
    ds = days(5)
    closes = {
        SA: tape(ds, [10.0, 11.0, 12.1, 12.1, 13.31]),  # +10%, +10%, 0%, +10%
        SB: tape(ds, [20.0, 20.0, 22.0, 22.0, 22.0]),  # 0%, +10%, 0%, 0%
    }
    spy = tape(ds, [100.0, 101.0, 102.0, 103.0, 104.0])
    ov = over([ms("AAA", SA), ms("BBB", SB)], closes, spy)
    assert ov.dates == tuple(ds) and ov.session_source == "benchmarks"
    # the mean of the daily returns each session: 5%, 10%, 0%, 5%
    assert ov.card == pytest.approx((100.0, 105.0, 115.5, 115.5, 121.275))
    assert ov.spy == pytest.approx((100.0, 101.0, 102.0, 103.0, 104.0))
    assert (ov.names, ov.priced, ov.unresolved, ov.silent_left_out) == (2, 2, 0, 0)
    assert ov.card_reason is None and ov.spy_reason is None and ov.flagged == ()


def test_the_overlay_starts_at_the_last_session_before_the_axis_so_the_first_months_move_is_in_it():
    before = [date(2023, 10, 30), date(2023, 10, 31)]
    inside = [date(2023, 11, 1), date(2023, 11, 2)]
    ds = before + inside
    closes = {SA: tape(ds, [10.0, 10.0, 11.0, 11.0])}
    spy = tape(ds, [100.0, 100.0, 100.0, 100.0])
    ov = over([ms("AAA", SA)], closes, spy)
    assert ov.dates[0] == date(2023, 10, 31) and ov.dates[-1] == date(2023, 11, 2)  # 10-30 is cut
    assert ov.card == pytest.approx((100.0, 110.0, 110.0))  # the Oct 31 -> Nov 1 step is the line's


def test_both_lines_share_the_later_start_so_they_read_against_each_other():
    ds = days(6)
    spy = tape(ds, [100.0, 101.0, 102.0, 103.0, 104.0, 105.0])
    closes = {
        SA: tape(ds, [None, None, 10.0, 11.0, 11.0, 12.1])
    }  # the name's first step is into session 3
    ov = over([ms("AAA", SA)], closes, spy)
    assert ov.dates == tuple(
        ds[2:]
    )  # the card's line starts at session 2 (the bar before its first step)
    assert ov.card[0] == ov.spy[0] == pytest.approx(100.0)
    assert ov.spy == pytest.approx((100.0, 100.0 * 103 / 102, 100.0 * 104 / 102, 100.0 * 105 / 102))
    assert ov.card == pytest.approx((100.0, 110.0, 110.0, 121.0))


def test_no_spy_tape_gives_no_spy_line_with_its_reason_and_the_card_line_stands_on_its_own_sessions():
    ds = days(4)
    closes = {SA: tape(ds, [10.0, 11.0, 11.0, 12.1])}
    ov = over([ms("AAA", SA)], closes, {})
    assert ov.spy is None and ov.spy_reason == f"no {BENCHMARK} tape at this as-of"
    assert ov.session_source == "companies" and ov.card == pytest.approx(
        (100.0, 110.0, 110.0, 121.0)
    )


def test_a_flagged_name_is_left_out_of_the_line_and_named_never_priced():
    ds = days(3)
    closes = {SA: tape(ds, [10.0, 20.0, 40.0]), SB: tape(ds, [20.0, 22.0, 22.0])}
    spy = tape(ds, [100.0, 100.0, 100.0])
    ov = over(
        [ms("AAA", SA), ms("BBB", SB)], closes, spy, flagged={SA: "mixed-scale price history"}
    )
    assert ov.card == pytest.approx(
        (100.0, 110.0, 110.0)
    )  # BBB's returns only: AAA's doubling is never chained
    assert ov.flagged == (FlaggedName("AAA", "mixed-scale price history"),)
    assert (ov.names, ov.priced) == (2, 1)


def test_context_and_bench_are_left_out_counted_and_an_unresolved_row_is_counted_never_priced():
    ds = days(3)
    closes = {
        SA: tape(ds, [10.0, 11.0, 12.1]),
        SB: tape(ds, [50.0, 25.0, 12.5]),
        SC: tape(ds, [5.0, 5.0, 5.0]),
    }
    roster = [
        ms("AAA", SA),
        ms("CTX", SB, CONTEXT_LABEL),
        ms("BEN", SC, BENCH_LABEL),
        ms("NOSEC", None),
        ms("NOSEC", None),  # the same row twice: one name
        ms("SILENTNOSEC", None, CONTEXT_LABEL),  # a silent row with no security is left out too
    ]
    ov = over(roster, closes, tape(ds, [100.0, 100.0, 100.0]))
    assert ov.card == pytest.approx(
        (100.0, 110.0, 121.0)
    )  # AAA alone: Context's halving never enters
    assert (ov.names, ov.priced, ov.unresolved, ov.silent_left_out) == (2, 1, 1, 3)


def test_a_card_with_no_priced_name_has_no_line_and_says_why():
    ds = days(3)
    spy = tape(ds, [100.0, 101.0, 102.0])
    none_priced = over([ms("AAA", SA)], {}, spy)
    assert none_priced.card is None and none_priced.card_reason == "no priced name in the window"
    assert none_priced.spy == pytest.approx((100.0, 101.0, 102.0)) and none_priced.names == 1
    empty = over([], {}, spy)
    assert empty.card is None and empty.card_reason == "no names on the card's roster"
    all_out = over(
        [ms("AAA", SA)], {SA: tape(ds, [1.0, 2.0, 3.0])}, spy, flagged={SA: "stopped feed"}
    )
    assert (
        all_out.card is None
        and all_out.card_reason == "every name is left out or has no resolved security"
    )
    assert all_out.flagged == (FlaggedName("AAA", "stopped feed"),)


def test_a_bar_after_the_asof_is_never_a_session_and_a_non_positive_close_is_not_a_bar():
    ds = days(4) + [date(2026, 11, 2)]  # the last is after the as-of (Oct 30)
    closes = {SA: tape(ds, [10.0, 11.0, 0.0, 12.1, 99.0])}
    ov = over([ms("AAA", SA)], closes, tape(ds, [100.0] * 5))
    assert ov.dates[-1] == date(2026, 10, 30) and date(2026, 11, 2) not in ov.dates


# ---------------------------------------------------------------------------------------------------------
# the job's run artifact
# ---------------------------------------------------------------------------------------------------------


def artifact(
    schedule: str = "weekly",
    window=("2023-11", "2026-10"),
    failed=(),
    deferred=(),
    started: str = "2026-10-24T14:00:00+00:00",
    finished: str = "2026-10-24T14:09:00+00:00",
    status: str = "finished",
) -> dict:
    return {
        "kind": "term_mentions",
        "mode": "apply",
        "schedule": schedule,
        "run_kind": "cron",
        "window": list(window),
        "started_at": started,
        "finished_at": finished,
        "status": status,
        "counts": {"requests": 1492, "failed_requests": len(failed), "deferred_rows": 7},
        "failed": list(failed),
        "deferred": list(deferred),
    }


def test_a_weekly_run_refreshed_the_last_two_months_and_a_backfill_the_whole_window():
    weekly = run_summary(artifact(), T1, "Card A")
    assert (weekly.schedule, weekly.months_from, weekly.months_to) == (
        "weekly",
        "2026-09",
        "2026-10",
    )
    assert (
        weekly.finished_on == date(2026, 10, 24)
        and weekly.requests == 1492
        and weekly.status == "finished"
    )
    january = run_summary(artifact(window=("2024-02", "2027-01")), T1, "Card A")
    assert (january.months_from, january.months_to) == ("2026-12", "2027-01")
    backfill = run_summary(artifact("backfill"), T1, "Card A")
    assert (backfill.months_from, backfill.months_to) == ("2023-11", "2026-10")


def test_the_cards_own_share_of_the_failed_and_deferred_months_is_matched_not_the_totals():
    doc = artifact(
        failed=[
            {"term": "alpha", "month": "2026-03", "error": "x", "cards": ["Card A", "Card B"]},
            {"term": "beta", "month": "2026-04", "error": "x", "cards": ["Card B"]},
        ],
        deferred=[
            {
                "card": "Card A",
                "thesis_id": str(T1),
                "term": "alpha",
                "months": "2026-04..2026-10",
                "n": 7,
            },
            {
                "card": "Card B",
                "thesis_id": str(uuid.uuid4()),
                "term": "beta",
                "months": "2026-05",
                "n": 1,
            },
        ],
    )
    run = run_summary(doc, T1, "Card A")
    assert (run.failed_requests, run.deferred_months) == (2, 7)  # the job's totals
    assert (run.card_failed_requests, run.card_deferred_months) == (1, 7)  # this card's share


def test_no_run_on_record_is_none_and_a_runs_identity_is_its_start_status_and_finish():
    assert run_summary(None, T1, "Card A") is None and run_identity(None) is None
    a, b = artifact(), artifact(started="2026-10-31T14:00:00+00:00")
    assert run_identity(a) == ("2026-10-24T14:00:00+00:00", "finished", "2026-10-24T14:09:00+00:00")
    assert run_identity(a) != run_identity(b)


def test_the_router_reads_the_newest_applied_artifact_of_the_job(tmp_path, monkeypatch):
    monkeypatch.setattr(tm, "DEFAULT_RUNS_DIR", tmp_path / "runs")
    assert router_mod.read_last_run() is None
    tm._write_artifact(artifact(started="2026-10-17T14:00:00+00:00"), tmp_path / "runs")
    tm._write_artifact(artifact(started="2026-10-24T14:00:00+00:00"), tmp_path / "runs")
    assert router_mod.read_last_run()["started_at"] == "2026-10-24T14:00:00+00:00"


# ---------------------------------------------------------------------------------------------------------
# the router, off one cached read
# ---------------------------------------------------------------------------------------------------------

RUN_1 = artifact()
RUN_2 = artifact(started="2026-10-31T14:00:00+00:00", finished="2026-10-31T14:08:00+00:00")


RASOF = date(2026, 9, 30)  # a past as-of whatever day the suite runs: September has closed


def base_page():
    ds = days(8, end=RASOF)
    rows = [
        mrow("alpha", m(2026, 7), reports=4, issuance=1, filers=3),
        mrow("alpha", m(2026, 8), reports=5, events=2, filers=4, new=2),
        mrow("beta", m(2026, 8), reports=1, filers=2, sampled=True, new=1),
        mrow("alpha", m(2026, 9), reports=6, events=1, issuance=3, filers=5, new=1),
    ]
    return narrative_adoption(
        thesis_id=T1,
        thesis_name="Card A",
        asof=RASOF,
        term_set=term_set("alpha", "beta", "gamma", broad=("b-one",)),
        rows=rows,
        roster=[ms("AAA", SA), ms("BBB", SB)],
        closes={SA: tape(ds, [10 + i for i in range(8)]), SB: tape(ds, [20.0] * 8)},
        spy_closes=tape(ds, [100.0 + i for i in range(8)]),
        flagged={SB: "mixed-scale price history"},
    )


@pytest.fixture
def stub_client(monkeypatch):
    seen: dict = {"reads": 0, "card_reads": 0, "run": RUN_1, "fallback": False}
    other = uuid.uuid4()

    def fake_cards(conn, asof):
        seen["card_reads"] += 1
        return CardsRead(
            live_cards=3,
            not_yet_created=1,
            cards=(CardChoice(T1, "Card A", 2), CardChoice(other, "Card B", 1)),
        )

    def fake_read(conn, thesis_id, asof, known_at, clock):
        seen["reads"] += 1
        seen["view"] = (thesis_id, asof, known_at, clock)
        return base_page(), seen["fallback"]

    CACHE.clear()
    monkeypatch.setattr(router_mod, "read_cards", fake_cards)
    monkeypatch.setattr(router_mod, "read_page", fake_read)
    monkeypatch.setattr(router_mod, "read_last_run", lambda: seen["run"])
    monkeypatch.setattr(CACHE, "_token_fn", lambda conn: "gen-1")
    app.dependency_overrides[get_conn] = lambda: object()
    yield TestClient(app), seen
    app.dependency_overrides.clear()
    CACHE.clear()


def test_the_router_serves_the_page_off_one_cached_read_on_the_requests_clock(stub_client):
    client, seen = stub_client
    url = f"/dashboards/narrative-adoption/{T1}"
    body = client.get(url, params={"asof": "2026-09-30"}).json()
    assert body["cache"] == "computed" and body["clock"] == "public"
    thesis_id, asof, known_at, clock = seen["view"]
    assert thesis_id == T1 and asof == RASOF and clock == "public" and known_at is not None
    again = client.get(url, params={"asof": "2026-09-30"}).json()
    assert again["cache"] == "hit" and again["months"] == body["months"]
    assert seen["reads"] == 1 and seen["card_reads"] == 1

    # today's view is the live read, and a new clock is a new read
    live = client.get(url, params={"asof": market_time.market_today().isoformat()}).json()
    assert seen["view"][2:] == (None, "record") and seen["reads"] == 2 and live["clock"] == "record"


def test_a_new_weekly_run_of_the_job_is_a_new_read_though_the_caches_own_token_did_not_move(
    stub_client,
):
    client, seen = stub_client
    url = f"/dashboards/narrative-adoption/{T1}"
    first = client.get(url, params={"asof": "2026-09-30"}).json()
    assert (
        client.get(url, params={"asof": "2026-09-30"}).json()["cache"] == "hit"
        and seen["reads"] == 1
    )
    seen["run"] = (
        RUN_2  # the Saturday job ran: no nightly pass, no basket edit, the token is still gen-1
    )
    second = client.get(url, params={"asof": "2026-09-30"}).json()
    assert second["cache"] == "computed" and seen["reads"] == 2
    assert (
        second["last_run"]["finished_on"] == "2026-10-31"
        and first["last_run"]["finished_on"] == "2026-10-24"
    )
    seen["run"] = None  # no applied run on record at all
    none = client.get(url, params={"asof": "2026-09-30"}).json()
    assert none["last_run"] is None and seen["reads"] == 3


def test_the_router_wire_shape(stub_client):
    client, seen = stub_client
    seen["fallback"] = True
    body = client.get(f"/dashboards/narrative-adoption/{T1}", params={"asof": "2026-09-30"}).json()
    assert (
        body["asof"] == "2026-09-30"
        and body["thesis_name"] == "Card A"
        and body["roster_fallback"] is True
    )
    assert body["months_window"] == 36 and len(body["months"]) == 36
    assert (body["months"][0]["month"], body["months"][-1]["month"]) == ("2023-10-01", "2026-09-01")
    aug = next(x for x in body["months"] if x["month"] == "2026-08-01")
    assert (aug["documents"], aug["reports"], aug["events"], aug["terms_counted"]) == (8, 6, 2, 2)
    assert (aug["filers"], aug["filers_sampled"], aug["new_filers"]) == (6, True, 3)
    assert [(t["term"], t["documents"]) for t in aug["top_terms"]] == [("alpha", 7), ("beta", 1)]
    june = next(x for x in body["months"] if x["month"] == "2026-06-01")
    assert june["counted"] is False and june["documents"] is None and june["closed"] is True
    assert [t["term"] for t in body["terms"]] == ["alpha", "beta", "gamma"]
    gamma = body["terms"][-1]
    assert (gamma["counted"], gamma["last_month_documents"], gamma["first_month"]) == (
        False,
        None,
        None,
    )
    c = body["counts"]
    assert (
        c["signal_terms"],
        c["terms_counted"],
        c["terms_not_counted"],
        c["broad_terms_left_out"],
    ) == (3, 2, 1, 1)
    assert (c["months_total"], c["months_counted"], c["last_counted_month"]) == (
        36,
        3,
        "2026-09-01",
    )
    ov = body["overlay"]
    assert len(ov["dates"]) == 8 == len(ov["card"]) == len(ov["spy"]) and ov["card"][0] == 100.0
    assert ov["flagged"] == [{"ticker": "BBB", "reason": "mixed-scale price history"}]
    assert (ov["names"], ov["priced"], ov["session_source"]) == (2, 1, "benchmarks")
    run = body["last_run"]
    assert (run["schedule"], run["months_from"], run["months_to"], run["requests"]) == (
        "weekly",
        "2026-09",
        "2026-10",
        1492,
    )
    assert body["cache"] == "computed" and isinstance(body["compute_ms"], int)


def test_the_router_404s_a_card_not_counted_at_the_asof(stub_client):
    client, seen = stub_client
    r = client.get(f"/dashboards/narrative-adoption/{uuid.uuid4()}", params={"asof": "2026-09-30"})
    assert r.status_code == 404 and seen["reads"] == 0


def test_the_picker_list_is_cached(stub_client):
    client, seen = stub_client
    body = client.get("/dashboards/narrative-adoption/cards", params={"asof": "2026-09-30"}).json()
    assert [c["name"] for c in body["cards"]] == [
        "Card A",
        "Card B",
    ]  # the stub's own order, served as read
    assert (body["live_cards"], body["not_yet_created"], body["cache"]) == (3, 1, "computed")
    again = client.get("/dashboards/narrative-adoption/cards", params={"asof": "2026-09-30"}).json()
    assert again["cache"] == "hit" and seen["card_reads"] == 1


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the counts and the prices are as of the past view (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

STORED = datetime(2026, 10, 5, tzinfo=UTC)  # our own ingest instant: the public clock must not care


def _get(client: TestClient, thesis_id: UUID, asof: date) -> dict:
    r = client.get(f"/dashboards/narrative-adoption/{thesis_id}", params={"asof": asof.isoformat()})
    assert r.status_code == 200, r.text
    return r.json()


def _security(db, ticker: str, kind: str = "equity") -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, None, kind, "2023-01-01"),
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
            "recorded_at": datetime.combine(d, time.min, tzinfo=UTC),
        },
    )


def _card(db, terms: list[str], basket: list[BasketMember] | None = None) -> Thesis:
    """A card with a SIGNAL term set, created before every as-of below (a fixture backdate, not an app write),
    its roster snapshot pinned to January so the past views read the snapshot."""
    card = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Card A",
        narrative="x",
        segments=[Segment(label="Core"), Segment(label=CONTEXT_LABEL), Segment(label=BENCH_LABEL)],
        basket=basket or [],
    )
    thesis_repo.upsert(db, card)
    thesis_repo.set_term_set(
        db, card.id, [TermSetEntry(term=t, tier=TermTier.SIGNAL) for t in terms]
    )
    with db.cursor() as cur:
        cur.execute("UPDATE thesis SET created_at = %s WHERE id = %s", ("2023-01-02", card.id))
        cur.execute(
            "UPDATE basket_snapshot SET taken_at = %s WHERE thesis_id = %s",
            (datetime(2023, 1, 3, tzinfo=UTC), card.id),
        )
    db.commit()
    return card


def _store(
    db, thesis_id: UUID, term: str, month: date, *, recorded_at: datetime = STORED, **kw
) -> None:
    row = mrow(term, month, **kw)
    row.pop("filer_ciks")
    append_fact(
        db,
        "fact_term_mentions",
        {
            **row,
            "tenant_id": DEFAULT_TENANT_ID,
            "thesis_id": thesis_id,
            "filer_ciks": [f"{i:010d}" for i in range(kw.get("filers", 0))],
            "recorded_at": recorded_at,
        },
    )


@pytest.fixture
def client_on(db, monkeypatch):
    CACHE.clear()
    monkeypatch.setattr(
        router_mod, "read_last_run", lambda: None
    )  # never the host's data/ directory
    app.dependency_overrides[get_conn] = lambda: db
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()


def _counted(body: dict) -> list[str]:
    return [x["month"][:7] for x in body["months"] if x["counted"]]


def test_a_month_that_closes_after_a_past_asof_is_invisible_though_it_is_stored(db, client_on):
    """Seven months of one term, every one INGESTED on 2026-10-05 (our own bookkeeping instant). At 2026-06-15
    January to May had closed; June to July are stored but had not, so they are absent from that day's curve,
    never a zero, and the last counted month is May."""
    card = _card(db, ["alpha"])
    for mo in range(1, 8):
        _store(db, card.id, "alpha", m(2026, mo), reports=mo, filers=mo)
    body = _get(client_on, card.id, date(2026, 6, 15))
    assert body["clock"] == "public"
    assert _counted(body) == ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05"]
    june = next(x for x in body["months"] if x["month"] == "2026-06-01")
    assert (june["counted"], june["closed"], june["documents"], june["filers"]) == (
        False,
        False,
        None,
        None,
    )
    assert body["counts"]["last_counted_month"] == "2026-05-01"
    # the same stored rows, a later as-of: June and July now show
    later = _get(client_on, card.id, date(2026, 9, 15))
    assert _counted(later)[-1] == "2026-07"
    assert next(x for x in later["months"] if x["month"] == "2026-07-01")["documents"] == 7


def test_a_past_asof_ends_the_curve_at_the_last_month_closed_by_then(db, client_on):
    """A month counts from its last day (the job's ``valid_from``): on that day it shows, the day before it does
    not, and the day after it still does, with the next month absent."""
    card = _card(db, ["alpha"])
    for mo in range(3, 8):
        _store(db, card.id, "alpha", m(2026, mo), reports=1)

    def last_counted(d: date) -> str | None:
        return _get(client_on, card.id, d)["counts"]["last_counted_month"]

    assert last_counted(date(2026, 6, 29)) == "2026-05-01"
    assert last_counted(date(2026, 6, 30)) == "2026-06-01"  # June's own last day
    assert last_counted(date(2026, 7, 1)) == "2026-06-01"
    # a curve with no month closed yet is empty, and every term reads "not counted yet"
    empty = _get(client_on, card.id, date(2026, 3, 30))
    assert _counted(empty) == [] and empty["terms"][0]["counted"] is False


def test_on_the_public_clock_a_count_stored_after_the_asof_still_counts_from_its_months_close(
    db, client_on
):
    """May is counted early (recorded 06-01, a partial read) and re-counted in October (recorded 10-05, the full
    count). History is judged on the public clock: at 2026-06-15 the page reads May's newest count, because May
    was public from its close, not from the day we stored a version of it."""
    card = _card(db, ["alpha"])
    _store(
        db,
        card.id,
        "alpha",
        m(2026, 5),
        recorded_at=datetime(2026, 6, 1, tzinfo=UTC),
        reports=3,
        filers=2,
    )
    _store(db, card.id, "alpha", m(2026, 5), recorded_at=STORED, reports=40, filers=9)
    may = next(
        x
        for x in _get(client_on, card.id, date(2026, 6, 15))["months"]
        if x["month"] == "2026-05-01"
    )
    assert (may["documents"], may["filers"]) == (40, 9)
    count = db.execute("SELECT count(*) AS n FROM fact_term_mentions").fetchone()["n"]
    assert count == 2  # both versions are still stored: the read picked one, it deleted nothing


def test_the_live_read_does_not_see_the_open_month_or_a_count_not_yet_recorded(
    db, client_on, monkeypatch
):
    """Pinned to Oct 14: October (valid_from Oct 31) has not closed, and a September count recorded tomorrow is
    not yet held by the record clock, so the live curve ends at August."""
    monkeypatch.setattr(
        market_time,
        "market_now",
        lambda tz=None: datetime(2026, 10, 14, 12, tzinfo=market_time.market_tz()).astimezone(
            tz or market_time.market_tz()
        ),
    )
    card = _card(db, ["alpha"])
    long_ago = datetime(2026, 1, 1, tzinfo=UTC)
    _store(db, card.id, "alpha", m(2026, 7), recorded_at=long_ago, reports=1)
    _store(db, card.id, "alpha", m(2026, 8), recorded_at=long_ago, reports=1)
    _store(
        db,
        card.id,
        "alpha",
        m(2026, 9),
        recorded_at=datetime.now(UTC) + timedelta(days=1),
        reports=1,
    )
    _store(db, card.id, "alpha", m(2026, 10), recorded_at=long_ago, reports=1)
    body = _get(client_on, card.id, date(2026, 10, 14))
    assert body["clock"] == "record"
    assert _counted(body) == ["2026-07", "2026-08"]
    assert body["counts"]["months_not_closed"] == 1


def test_the_overlay_reads_no_bar_after_the_asof_and_runs_the_whole_36_months(db, client_on):
    """Business-day bars from 2023-06-01 for SPY and one name, plus a Monday bar AFTER the as-of (a Friday,
    2026-06-12). The line ends on the as-of, starts at the last session before the axis (2023-06-30, the axis
    beginning 2023-07-01) and spans well past the display PIT's 630-day bound."""
    spy = _security(db, "SPY", kind="etf")
    name = _security(db, "NAME")
    d, i = date(2023, 6, 1), 0
    while d <= date(2026, 6, 12):
        if d.weekday() < 5:
            _price(db, spy, d, 100.0 + 0.04 * i)
            _price(db, name, d, 10.0 + 0.01 * i + (0.05 if i % 5 == 0 else 0.0))
            i += 1
        d += timedelta(days=1)
    _price(db, spy, date(2026, 6, 15), 500.0)  # after the as-of
    _price(db, name, date(2026, 6, 15), 99.0)
    card = _card(
        db, ["alpha"], [BasketMember(ticker="NAME", role="x", security_id=name, segment="Core")]
    )
    body = _get(client_on, card.id, date(2026, 6, 12))
    ov = body["overlay"]
    assert body["roster_fallback"] is False and ov["flagged"] == [], ov
    assert (ov["dates"][0], ov["dates"][-1]) == ("2023-06-30", "2026-06-12")
    assert (date(2026, 6, 12) - date(2023, 6, 30)).days > 1000 > 630
    assert ov["card"][0] == ov["spy"][0] == 100.0 and len(ov["card"]) == len(ov["dates"]) == len(
        ov["spy"]
    )
    assert ov["spy"][-1] < 200  # the 500 print dated after the as-of was never read
    assert (ov["names"], ov["priced"], ov["session_source"]) == (1, 1, "benchmarks")
    assert (
        body["months"][0]["month"] == "2023-07-01" and body["months"][-1]["month"] == "2026-06-01"
    )
