"""Fundamentals Multiples (``docs/dashboards/fundamentals-multiples.md``): the window (twelve, fewer, none), the
calendar-quarter axis across fiscal calendars, the year-over-year rule (within ten days, a missing prior, a prior of
zero or less), the derived/native basis, the re-report tag, the medians and their n's on hand-computed numbers (one
name, two names, a quarter with one contributor), Context and Bench never in a median, and the counts; the router off
one cached read with its 404; and, DB-backed, a revenue print filed after a past as-of not being drawn (two prints
for one period end, the earlier as-of shows the first and the later one the second), a roster row added after the
as-of not being a chart, and an active set-aside counted and the read falling back to the version it superseded.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.routers.dashboards import fundamentals_multiples as router_mod
from dashboards.baskets import CardChoice, CardsRead, Membership
from dashboards.cache import CACHE
from dashboards.filings import YOY_TOLERANCE_DAYS
from dashboards.fundamentals_multiples import (
    MIN_MEDIAN_NAMES,
    NO_LINK,
    QUARTERS,
    THIN_BELOW,
    CompanyRead,
    MultiplesInputs,
    build_axis,
    fundamentals_multiples,
    median_line,
    quarter_dots,
    quarter_index,
    quarter_label,
    quarter_of,
    revenue_rows,
    yoy_pct,
)
from db.bitemporal import append_fact
from db.session import DEFAULT_TENANT_ID
from domain.enums import ContextRole
from domain.market_time import market_today
from domain.thesis import BENCH_LABEL, CONTEXT_LABEL, BasketMember, Segment, Thesis
from ingest.fundamentals import QuarterPoint, fundamentals_row
from repositories import fundamentals_set_aside_repo, thesis_repo

ASOF = date(2026, 8, 14)  # a Friday
T1 = uuid.uuid4()
SA, SB, SC, SD, SE, SF = (uuid.uuid4() for _ in range(6))
CIK = "0000876544"

# ---------------------------------------------------------------------------------------------------------
# builders
# ---------------------------------------------------------------------------------------------------------

SEGMENTS = (
    Segment(label="Turbines", descriptor="make the power"),
    Segment(label="Cables"),
    Segment(label="Empty link"),
    Segment(label=CONTEXT_LABEL),
    Segment(label=BENCH_LABEL),
)


def qend(year: int, q: int) -> date:
    return (date(year, 3, 31), date(year, 6, 30), date(year, 9, 30), date(year, 12, 31))[q - 1]


def quarter_ends(year: int, q: int, n: int) -> list[date]:
    """``n`` consecutive calendar quarter ends starting at (year, q)."""
    out = []
    for _ in range(n):
        out.append(qend(year, q))
        year, q = (year + 1, 1) if q == 4 else (year, q + 1)
    return out


def rev(
    end: date,
    value: float,
    *,
    filed: date | None = None,
    basis: str = "native",
    accession: str | None = None,
    knowable: date | None = None,
    knowable_accession: str | None = None,
    metric: str = "revenue",
) -> dict:
    """One ``fundamentals_facts`` row as the point-in-time view returns it."""
    filed = filed or end + timedelta(days=40)
    acc = accession or f"0000876544-{filed:%y}-{filed:%m%d}01"
    return {
        "metric_key": metric,
        "period_end": end,
        "fiscal_period": f"Q{(end.month - 1) // 3 + 1}",
        "fiscal_year": end.year,
        "value": Decimal(str(value)),
        "basis": basis,
        "accession": acc,
        "valid_from": filed,
        "knowable_from": knowable or filed,
        "knowable_accession": knowable_accession or acc,
    }


def series(year: int, q: int, values: list[float], **kw) -> tuple[dict, ...]:
    """Consecutive quarterly revenue rows from (year, q), one per value."""
    return tuple(rev(e, v, **kw) for e, v in zip(quarter_ends(year, q, len(values)), values))


def m(
    ticker: str, sid: UUID | None, segment: str | None = "Turbines", role: str | None = None
) -> Membership:
    return Membership(
        thesis_id=T1,
        thesis_name="Grid",
        ticker=ticker,
        security_id=sid,
        segment=segment,
        silent=segment in (CONTEXT_LABEL, BENCH_LABEL),
        context_role=role,
    )


def read(*rows: dict | tuple, name: str | None = None, cik: str | None = CIK, aside: int = 0):
    flat: list[dict] = []
    for r in rows:
        flat.extend(r if isinstance(r, tuple) else [r])
    return CompanyRead(name=name, cik=cik, rows=tuple(flat), set_aside=aside)


def build(
    roster: list[Membership],
    facts: dict[UUID, CompanyRead] | None = None,
    *,
    segments: tuple[Segment, ...] = SEGMENTS,
    fallback: bool = False,
    asof: date = ASOF,
):
    return fundamentals_multiples(
        MultiplesInputs(
            thesis_id=T1,
            thesis_name="Grid",
            segments=segments,
            roster=tuple(roster),
            facts=facts or {},
            roster_fallback=fallback,
        ),
        asof=asof,
    )


def company(page, ticker: str):
    return next(c for c in page.companies if c.ticker == ticker)


def row_of(page, key: str):
    return next(r for r in page.rows if r.key == key)


def at(median, quarter: str):
    return next(p for p in median.points if p.quarter == quarter)


# a card that uses every rule once. A: fourteen quarters (2023-Q1 .. 2026-Q2), a year-over-year of +20% in 2024,
# +25% in 2025 and +20% in 2026. B: four quarters (2025-Q1, Q2, 2026-Q1, Q2): +20% then +50%. C: ten (2024-Q1 ..
# 2026-Q2): +20% in 2025, +25% then +50% in 2026. D (Context): a huge growth that must never move a median. E
# (Bench): no revenue stored. U: a row with no resolved security.
A_ROWS = series(2023, 1, [100] * 4 + [120] * 4 + [150] * 4 + [180] * 2)
B_ROWS = series(2025, 1, [50, 50]) + series(2026, 1, [60, 75])
C_ROWS = series(2024, 1, [10] * 4 + [12] * 4 + [15, 18])
D_ROWS = series(2024, 1, [1] * 4 + [11] * 4 + [111] * 2)

BASE_ROSTER = [
    m("AAA", SA, "Turbines"),
    m("BBB", SB, "Turbines"),
    m("UNR", None, "Turbines"),  # no resolved security
    m("CCC", SC, "Cables"),
    m(
        "AAA", SA, "Cables"
    ),  # AAA sits in two links: a chart in both rows, one name in the card line
    m("DDD", SD, CONTEXT_LABEL, role="payer"),
    m("EEE", SE, BENCH_LABEL),
    m("OLD", SF, "Old wires"),  # a roster link that is not on the card today (no revenue stored)
    m("AAA", SA, "Turbines"),  # the same company twice in one link: one chart
]
BASE_FACTS = {
    SA: read(A_ROWS, name="A Corp"),
    SB: read(B_ROWS, name="B Corp"),
    SC: read(C_ROWS, name="C Corp"),
    SD: read(D_ROWS, name="D Corp"),
    SE: read(name="E Corp"),
    SF: read(name="Old Corp"),
}


def base():
    return build(BASE_ROSTER, BASE_FACTS)


# ---------------------------------------------------------------------------------------------------------
# the calendar-quarter axis
# ---------------------------------------------------------------------------------------------------------


def test_the_calendar_quarter_of_a_period_end_across_fiscal_calendars():
    assert [quarter_of(d) for d in (date(2026, 3, 31), date(2026, 4, 1), date(2026, 12, 31))] == [
        "2026-Q1",
        "2026-Q2",
        "2026-Q4",
    ]
    # a January / April / July / October filer: the quarter of the MONTH the period ends, not a fiscal label
    assert [quarter_of(date(2026, mm, dd)) for mm, dd in ((1, 31), (4, 30), (7, 31), (10, 31))] == [
        "2026-Q1",
        "2026-Q2",
        "2026-Q3",
        "2026-Q4",
    ]
    # a 52/53-week filer: the year ends in the first days of January, and the next quarter in early April
    assert quarter_of(date(2025, 12, 28)) == "2025-Q4" and quarter_of(date(2026, 1, 3)) == "2026-Q1"
    assert quarter_label(quarter_index(date(2026, 5, 9))) == "2026-Q2"
    assert quarter_index(date(2026, 1, 1)) - quarter_index(date(2025, 12, 31)) == 1


def test_the_axis_is_twelve_quarters_ending_at_the_newest_latest_print():
    axis = build_axis([date(2026, 6, 30), date(2026, 3, 31), date(2025, 9, 30)])
    assert len(axis) == QUARTERS == 12
    assert (axis[0], axis[-1]) == ("2023-Q3", "2026-Q2")
    assert list(axis) == sorted(axis)  # sortable as text
    assert build_axis([]) == ()


def test_a_fiscal_calendar_that_runs_a_quarter_ahead_moves_the_card_axis_and_cuts_the_oldest_bar():
    cal = series(2023, 3, [10] * 12)  # calendar filer: 2023-Q3 .. 2026-Q2
    # a July-end filer: period ends Jan/Apr/Jul/Oct 31, the newest July 31, 2026 (calendar 2026-Q3)
    fisc_ends = [
        date(2025, 1, 31),
        date(2025, 4, 30),
        date(2025, 7, 31),
        date(2025, 10, 31),
        date(2026, 1, 31),
        date(2026, 4, 30),
        date(2026, 7, 31),
    ]
    fisc = tuple(rev(e, 20.0, filed=e + timedelta(days=30)) for e in fisc_ends)
    page = build(
        [m("CAL", SA, "Turbines"), m("FIS", SB, "Turbines")],
        {SA: read(cal), SB: read(fisc)},
        asof=date(2026, 8, 31),
    )
    assert (page.axis[0], page.axis[-1]) == ("2023-Q4", "2026-Q3")
    cal_c, fis_c = company(page, "CAL"), company(page, "FIS")
    # the calendar filer's oldest print (2023-Q3) is before the axis: eleven bars, the last slot (2026-Q3) empty
    assert (
        len(cal_c.bars) == 11
        and cal_c.bars[0].quarter == "2023-Q4"
        and cal_c.bars[-1].quarter == "2026-Q2"
    )
    assert cal_c.state == "series" and cal_c.periods == 12
    # the July filer's bars sit in the calendar quarter of their period end
    assert [b.quarter for b in fis_c.bars] == [
        "2025-Q1",
        "2025-Q2",
        "2025-Q3",
        "2025-Q4",
        "2026-Q1",
        "2026-Q2",
        "2026-Q3",
    ]
    assert fis_c.bars[-1].period_end == date(2026, 7, 31)


def test_two_bars_in_one_calendar_quarter_both_draw_and_the_later_period_end_is_the_dot():
    ends = [date(2025, 3, 31), date(2025, 12, 31), date(2026, 2, 28), date(2026, 3, 31)]
    page = build(
        [m("TRN", SA, "Turbines")],
        {SA: read(rev(ends[0], 100), rev(ends[1], 110), rev(ends[2], 90), rev(ends[3], 150))},
        asof=date(2026, 6, 30),
    )
    c = company(page, "TRN")
    assert [b.quarter for b in c.bars] == ["2025-Q1", "2025-Q4", "2026-Q1", "2026-Q1"]
    # Feb 28 has no prior year row (nothing near Feb 28, 2025), Mar 31 does (+50%): the later period end decides
    assert [b.yoy for b in c.bars] == [None, None, None, pytest.approx(50.0)]
    assert quarter_dots(c) == {"2026-Q1": pytest.approx(50.0)}
    # and when the LATER bar has no dot, the earlier one does not stand in for it
    page2 = build(
        [m("TRN", SA, "Turbines")],
        {
            SA: read(
                rev(date(2025, 2, 28), 100),
                rev(date(2026, 2, 28), 130),
                rev(date(2026, 3, 31), 150),
            )
        },
        asof=date(2026, 6, 30),
    )
    c2 = company(page2, "TRN")
    assert [b.yoy for b in c2.bars] == [None, pytest.approx(30.0), None]
    assert quarter_dots(c2) == {}


# ---------------------------------------------------------------------------------------------------------
# the window: twelve, fewer, none
# ---------------------------------------------------------------------------------------------------------


def test_a_name_with_fourteen_quarters_draws_the_last_twelve_and_one_with_four_is_thin():
    page = base()
    a, b = company(page, "AAA"), company(page, "BBB")
    assert len(a.bars) == 12 and a.periods == 14 and a.state == "series"
    assert (a.bars[0].period_end, a.bars[-1].period_end) == (date(2023, 9, 30), date(2026, 6, 30))
    assert [x.quarter for x in a.bars] == list(page.axis)
    assert len(b.bars) == 4 and b.state == "thin" and THIN_BELOW == 5
    assert [x.quarter for x in b.bars] == [
        "2025-Q1",
        "2025-Q2",
        "2026-Q1",
        "2026-Q2",
    ]  # drawn as is
    # the thin line is "fewer than five bars": four is thin, five is not
    four = build([m("FOU", SA)], {SA: read(series(2025, 3, [1, 2, 3, 4]))})
    five = build([m("FIV", SA)], {SA: read(series(2025, 2, [1, 2, 3, 4, 5]))})
    assert company(four, "FOU").state == "thin" and company(five, "FIV").state == "series"


def test_a_name_with_no_series_is_an_empty_frame_and_is_counted():
    page = base()
    e, u = company(page, "EEE"), company(page, "UNR")
    assert (e.state, e.bars, e.last_print, e.periods) == ("none", (), None, 0)
    assert (u.state, u.security_id, u.name, u.bars) == ("unresolved", None, None, ())
    assert u.company_key == "ticker:UNR"
    # a resolved name the reader returned nothing for is "none" too, never a missing chart
    gone = build([m("GON", SA)], {})
    assert company(gone, "GON").state == "none" and gone.axis == ()
    assert gone.card_line.counting == 1 and gone.card_line.points == ()


def test_a_series_that_ends_before_the_axis_is_stale_with_its_last_print_still_stated():
    page = build(
        [m("NEW", SA), m("OLD", SB)],
        {SA: read(series(2024, 3, [10] * 8)), SB: read(series(2021, 1, [5] * 8))},
    )
    old = company(page, "OLD")
    assert old.state == "stale" and old.bars == () and old.periods == 8
    assert old.last_print is not None and old.last_print.quarter == "2022-Q4"
    assert old.last_print.filed == date(2023, 2, 9)
    assert (
        page.counts.stale == 1 and page.counts.with_series == 2 and page.counts.without_series == 0
    )


def test_only_revenue_rows_on_or_before_the_asof_and_filed_by_it_are_read():
    rows = [
        rev(date(2026, 3, 31), 100, filed=date(2026, 5, 11)),
        rev(date(2026, 6, 30), 120, filed=date(2026, 8, 20)),  # filed after the as-of
        rev(date(2026, 9, 30), 130, filed=date(2026, 8, 1)),  # a period end after the as-of
        rev(date(2026, 3, 31), 5_000_000, metric="shares_outstanding_xbrl"),  # another metric
        {**rev(date(2025, 12, 31), 90), "value": None},  # no number
        {**rev(date(2025, 9, 30), 80), "value": float("nan")},  # a non-finite number
        {**rev(date(2025, 6, 30), 70), "valid_from": None},  # no filing date
    ]
    kept = revenue_rows(rows, ASOF)
    assert [r["period_end"] for r in kept] == [date(2026, 3, 31)]
    page = build([m("AAA", SA)], {SA: read(*rows)})
    assert [b.period_end for b in company(page, "AAA").bars] == [date(2026, 3, 31)]


# ---------------------------------------------------------------------------------------------------------
# the dating, the basis and the year-over-year rule
# ---------------------------------------------------------------------------------------------------------


def test_a_bar_is_placed_by_its_period_end_and_dated_by_its_filing_with_the_edgar_link():
    page = base()
    last = company(page, "AAA").bars[-1]
    assert (last.quarter, last.period_end, last.filed) == (
        "2026-Q2",
        date(2026, 6, 30),
        date(2026, 8, 9),
    )
    assert last.value == 180.0 and last.basis == "native"
    assert last.accession == "0000876544-26-080901"
    assert last.url == "https://www.sec.gov/Archives/edgar/data/876544/000087654426080901/"
    assert (last.fiscal_period, last.fiscal_year) == ("Q2", 2026)
    # no SEC CIK: no link is made up
    nolink = build([m("AAA", SA)], {SA: read(A_ROWS, cik=None)})
    assert company(nolink, "AAA").bars[-1].url is None


def test_the_derived_and_native_basis_is_carried_and_an_unstated_one_is_said_so():
    page = build(
        [m("AAA", SA)],
        {
            SA: read(
                rev(date(2025, 12, 31), 90, basis="derived"),
                rev(date(2026, 3, 31), 100),
                {**rev(date(2026, 6, 30), 110), "basis": None},
            )
        },
    )
    assert [b.basis for b in company(page, "AAA").bars] == ["derived", "native", "basis not stated"]


def test_a_re_reported_bar_carries_the_earlier_date_it_was_first_knowable_only_when_it_is_earlier():
    page = build(
        [m("AAA", SA)],
        {
            SA: read(
                rev(
                    date(2025, 6, 30),
                    160,
                    filed=date(2026, 8, 7),
                    accession="0000876544-26-080701",
                    knowable=date(2025, 8, 8),
                    knowable_accession="0000876544-25-080801",
                ),
                rev(
                    date(2026, 3, 31), 100, filed=date(2026, 5, 11)
                ),  # knowable the day it was filed
            )
        },
    )
    again, once = company(page, "AAA").bars
    assert again.filed == date(2026, 8, 7) and again.first_knowable == date(2025, 8, 8)
    assert again.first_knowable_accession == "0000876544-25-080801"
    assert once.first_knowable is None and once.first_knowable_accession is None


def test_the_year_over_year_rule_within_ten_days_a_missing_prior_and_a_prior_of_zero_or_less():
    end = date(2026, 6, 30)
    now = rev(end, 150)

    def yoy(prior: dict | None, *, extra: list[dict] | None = None) -> float | None:
        rows = [now, *([prior] if prior else []), *(extra or [])]
        return yoy_pct(now, rows)

    # the same period a year earlier: +50%
    assert yoy(rev(date(2025, 6, 30), 100)) == pytest.approx(50.0)
    # a 52/53-week filer drifts: nine and ten days away still match, eleven does not
    assert YOY_TOLERANCE_DAYS == 10
    assert yoy(rev(date(2025, 6, 21), 100)) == pytest.approx(50.0)
    assert yoy(rev(date(2025, 6, 20), 100)) == pytest.approx(50.0)
    assert yoy(rev(date(2025, 6, 19), 100)) is None
    assert yoy(rev(date(2025, 7, 11), 100)) is None
    # the nearer of two candidates wins (July 3 is three days off, June 25 is five)
    assert yoy(rev(date(2025, 6, 25), 125), extra=[rev(date(2025, 7, 3), 100)]) == pytest.approx(
        50.0
    )
    # no prior in the read, a prior of zero, a negative prior: no dot, never a division
    assert yoy(None) is None
    assert yoy(rev(date(2025, 6, 30), 0)) is None
    assert yoy(rev(date(2025, 6, 30), -40)) is None
    # a fall is a negative change, a halving is -50%
    assert yoy_pct(rev(end, 50), [rev(end, 50), rev(date(2025, 6, 30), 100)]) == pytest.approx(
        -50.0
    )


def test_a_prior_year_row_outside_the_axis_still_gives_the_dot_without_needing_a_bar():
    page = base()
    a = company(page, "AAA")
    # 2024-Q1 is inside the axis; its prior (2023-Q1) is older than the axis, so it has no bar, but is in the read
    first_2024 = next(b for b in a.bars if b.quarter == "2024-Q1")
    assert first_2024.prior_period_end == date(2023, 3, 31) and first_2024.yoy == pytest.approx(
        20.0
    )
    # the oldest bar on the axis (2023-Q3) has no prior row at all
    assert a.bars[0].yoy is None and a.bars[0].prior_period_end is None
    assert [b.yoy for b in a.bars if b.quarter.startswith("2025")] == [pytest.approx(25.0)] * 4


def test_the_last_print_is_the_newest_period_with_the_days_it_was_filed_before_the_asof():
    page = base()
    lp = company(page, "AAA").last_print
    assert lp is not None
    assert (lp.quarter, lp.period_end, lp.filed, lp.days_before) == (
        "2026-Q2",
        date(2026, 6, 30),
        date(2026, 8, 9),
        5,
    )
    assert lp.yoy == pytest.approx(20.0)
    assert company(page, "EEE").last_print is None


# ---------------------------------------------------------------------------------------------------------
# the medians
# ---------------------------------------------------------------------------------------------------------


def test_a_link_median_is_the_median_of_the_dots_and_drawn_only_with_two_contributors():
    """Turbines holds A (+25% through 2025, +20% in 2026), B (+20%, +50% in 2026) and a row with no resolved
    security (it counts as a name, never contributes). 2025: one contributor (A), so nothing is drawn; 2026-Q1:
    A 20 and B 20 -> 20; 2026-Q2: A 20 and B 50 -> 35."""
    page = base()
    t = row_of(page, "link:Turbines").median
    assert t is not None and t.counting == 3
    assert (at(t, "2025-Q3").median, at(t, "2025-Q3").n) == (None, 1)
    assert (at(t, "2024-Q2").median, at(t, "2024-Q2").n) == (None, 1)
    assert (at(t, "2023-Q4").median, at(t, "2023-Q4").n) == (None, 0)
    assert (at(t, "2026-Q1").median, at(t, "2026-Q1").n) == (pytest.approx(20.0), 2)
    assert (at(t, "2026-Q2").median, at(t, "2026-Q2").n) == (pytest.approx(35.0), 2)
    assert [p.quarter for p in t.points] == list(page.axis)


def test_a_company_in_two_links_counts_in_both_medians_and_once_in_the_card_line():
    """Cables holds C (+20% in 2025, then 25, 50) and A: 2025: (20, 25) -> 22.5; 2026-Q1 (25, 20) -> 22.5;
    2026-Q2 (50, 20) -> 35; 2024: only A has a dot -> not drawn. The card line is over A, B and C ONCE each:
    2026-Q1 (20, 20, 25) -> 20 over three names; 2026-Q2 (20, 50, 50) -> 50; 2025 (A 25, C 20) -> 22.5.
    """
    page = base()
    cab = row_of(page, "link:Cables").median
    assert cab is not None and cab.counting == 2
    assert (at(cab, "2025-Q2").median, at(cab, "2025-Q2").n) == (pytest.approx(22.5), 2)
    assert (at(cab, "2026-Q1").median, at(cab, "2026-Q1").n) == (pytest.approx(22.5), 2)
    assert (at(cab, "2026-Q2").median, at(cab, "2026-Q2").n) == (pytest.approx(35.0), 2)
    assert (at(cab, "2024-Q3").median, at(cab, "2024-Q3").n) == (None, 1)
    card = page.card_line
    # A, B, C, the unresolved row and the old-link name with no series: A once, though it sits in two links
    assert card.counting == 5
    assert (at(card, "2026-Q1").median, at(card, "2026-Q1").n) == (pytest.approx(20.0), 3)
    assert (at(card, "2026-Q2").median, at(card, "2026-Q2").n) == (pytest.approx(50.0), 3)
    assert (at(card, "2025-Q4").median, at(card, "2025-Q4").n) == (pytest.approx(22.5), 2)
    assert (at(card, "2024-Q4").median, at(card, "2024-Q4").n) == (None, 1)


def test_a_row_with_exactly_one_counting_name_shows_that_names_dots_as_its_line():
    page = build(
        [m("AAA", SA, "Turbines"), m("CCC", SC, "Cables"), m("BBB", SB, "Cables")],
        {SA: read(A_ROWS), SB: read(B_ROWS), SC: read(C_ROWS)},
    )
    one = row_of(page, "link:Turbines").median
    assert one is not None and one.counting == 1
    # the single name's own dots: 2024 +20, 2025 +25, 2026 +20; a quarter with no dot is n=0, not drawn
    assert (at(one, "2024-Q2").median, at(one, "2024-Q2").n) == (pytest.approx(20.0), 1)
    assert (at(one, "2025-Q1").median, at(one, "2025-Q1").n) == (pytest.approx(25.0), 1)
    assert (at(one, "2023-Q4").median, at(one, "2023-Q4").n) == (None, 0)
    assert MIN_MEDIAN_NAMES == 2
    # an empty real link is a row with nobody and no median points
    empty = row_of(page, "link:Empty link")
    assert empty.companies == () and empty.median is not None and empty.median.counting == 0
    assert all(p.median is None and p.n == 0 for p in empty.median.points)


def test_the_median_of_an_odd_and_an_even_count_on_hand_numbers():
    ends = quarter_ends(2025, 2, 5)  # 2025-Q2 .. 2026-Q2; the 2025-Q2 row is the prior of 2026-Q2
    roster = [m("N01", SA), m("N02", SB), m("N03", SC)]
    facts = {
        SA: read(rev(ends[0], 100), rev(ends[4], 110)),  # +10
        SB: read(rev(ends[0], 100), rev(ends[4], 130)),  # +30
        SC: read(rev(ends[0], 100), rev(ends[4], 200)),  # +100
    }
    page = build(roster, facts, asof=date(2026, 8, 14))
    line = row_of(page, "link:Turbines").median
    assert line is not None
    assert (at(line, "2026-Q2").median, at(line, "2026-Q2").n) == (
        pytest.approx(30.0),
        3,
    )  # odd: the middle
    two = build(roster[:2], facts, asof=date(2026, 8, 14))
    even = row_of(two, "link:Turbines").median
    assert even is not None and at(even, "2026-Q2").median == pytest.approx(
        20.0
    )  # even: the two middles' mean
    assert median_line([], page.axis).counting == 0


def test_context_and_bench_names_never_enter_a_median():
    """D (Context) grows 1000% a year; with it counted, every 2025 and 2026 median would move. None does."""
    with_d = base()
    without_d = build(
        [r for r in BASE_ROSTER if r.ticker != "DDD"],
        {k: v for k, v in BASE_FACTS.items() if k != SD},
    )
    for key in ("link:Turbines", "link:Cables"):
        assert row_of(with_d, key).median == row_of(without_d, key).median
    assert with_d.card_line == without_d.card_line
    # the Context and Bench rows carry no median at all, and hold their names
    assert row_of(with_d, "silent:Context").median is None
    assert row_of(with_d, "silent:Bench").median is None
    d = company(with_d, "DDD")
    assert d.silent is True and d.state == "series" and len(d.bars) == 10
    # with D counted 2025-Q4 would be the middle of (A 25, C 20, D 1100) = 25 over three names
    assert (at(with_d.card_line, "2025-Q4").median, at(with_d.card_line, "2025-Q4").n) == (
        pytest.approx(22.5),
        2,
    )
    assert (
        at(with_d.card_line, "2026-Q2").n,
        row_of(with_d, "silent:Context").companies,
        row_of(with_d, "silent:Bench").companies,
    ) == (3, (str(SD),), (str(SE),))


def test_a_silent_company_with_a_stray_voting_row_counts_as_a_voting_name_in_its_voting_rows_only():
    page = build(
        [m("AAA", SA, "Turbines"), m("AAA", SA, CONTEXT_LABEL), m("BBB", SB, "Turbines")],
        {SA: read(A_ROWS), SB: read(B_ROWS)},
    )
    assert company(page, "AAA").silent is False
    assert [r.key for r in page.rows if r.kind == "silent"] == []
    assert row_of(page, "link:Turbines").median.counting == 2  # type: ignore[union-attr]


# ---------------------------------------------------------------------------------------------------------
# the rows, the counts and "nothing dropped"
# ---------------------------------------------------------------------------------------------------------


def test_the_rows_are_the_real_links_in_chain_order_then_old_links_no_link_context_and_bench():
    roster = [*BASE_ROSTER, m("NOL", uuid.uuid4(), None)]  # a row in no link
    page = build(roster, {**BASE_FACTS})
    assert [r.key for r in page.rows] == [
        "link:Turbines",
        "link:Cables",
        "link:Empty link",
        "old:Old wires",
        "no_link",
        "silent:Context",
        "silent:Bench",
    ]
    turbines = row_of(page, "link:Turbines")
    assert (turbines.kind, turbines.label, turbines.descriptor) == (
        "link",
        "Turbines",
        "make the power",
    )
    assert row_of(page, "no_link").label == NO_LINK and row_of(page, "no_link").descriptor is None
    # roster order inside a row, a company once (AAA appears twice in Turbines), the unresolved row by its ticker
    assert turbines.companies == (str(SA), str(SB), "ticker:UNR")
    assert row_of(page, "link:Cables").companies == (str(SC), str(SA))
    assert row_of(page, "old:Old wires").companies == (str(SF),)
    # the row only exists once somebody sits in it (no Context or Bench row on a card with nobody there)
    plain = build([m("AAA", SA, "Turbines")], {SA: read(A_ROWS)})
    assert [r.key for r in plain.rows] == ["link:Turbines", "link:Cables", "link:Empty link"]


def test_discovered_is_a_link_wherever_it_sits():
    segs = (Segment(label="Discovered"), Segment(label="Turbines"), Segment(label=CONTEXT_LABEL))
    page = build([m("AAA", SA, "Turbines"), m("BBB", SB, "Discovered")], segments=segs)
    assert [r.key for r in page.rows] == ["link:Discovered", "link:Turbines"]
    assert page.counts.links_today == 2


def test_the_counts_on_hand_computed_numbers():
    page = base()
    c = page.counts
    # nine roster rows: AAA three times (two links), BBB, UNR, CCC, DDD, EEE, OLD
    assert (c.roster_rows, c.resolved_rows, c.unresolved_rows, c.silent_rows) == (9, 8, 1, 2)
    assert (c.links_today, c.old_links) == (3, 1)
    # seven companies: A, B, U, C, D, E, F; five count, two are Context or Bench
    assert (c.companies, c.counting, c.silent_companies) == (7, 5, 2)
    # a series stored for A, B, C, D (four); none for U, E, F (three, the unresolved row among them)
    assert (c.with_series, c.without_series) == (4, 3)
    assert (c.thin, c.stale, c.set_aside) == (1, 0, 0)  # B is thin
    assert len(page.companies) == c.companies


def test_nothing_is_dropped_every_roster_company_is_a_chart_in_a_row_and_listed_once():
    page = base()
    listed = [ck for r in page.rows for ck in r.companies]
    assert set(listed) == {c.company_key for c in page.companies}
    for r in page.rows:
        assert len(set(r.companies)) == len(r.companies)  # once per row
    assert len({c.company_key for c in page.companies}) == len(page.companies)
    # the set-asides ride on the company and in the total
    withaside = build(
        BASE_ROSTER, {**BASE_FACTS, SA: read(A_ROWS, aside=2), SB: read(B_ROWS, aside=1)}
    )
    assert company(withaside, "AAA").set_aside == 2 and withaside.counts.set_aside == 3


def test_the_roster_note_and_the_card_come_through():
    page = build([m("AAA", SA)], {SA: read(A_ROWS)}, fallback=True)
    assert page.roster_fallback is True and page.thesis_name == "Grid" and page.thesis_id == T1
    assert page.asof == ASOF
    assert build([], {}).axis == () and build([], {}).counts.companies == 0


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
    url = f"/dashboards/fundamentals-multiples/{T1}"
    body = client.get(url, params={"asof": "2026-08-14"}).json()
    assert body["cache"] == "computed" and body["clock"] == "public"
    assert (body["quarters"], body["thin_below"], body["min_median_names"]) == (12, 5, 2)
    thesis_id, asof, known_at, clock = seen["view"]
    assert thesis_id == T1 and asof == ASOF and clock == "public" and known_at is not None
    again = client.get(url, params={"asof": "2026-08-14"}).json()
    assert again["cache"] == "hit" and again["rows"] == body["rows"]
    assert seen["reads"] == 1 and seen["card_reads"] == 1

    # today's view is the live read, and a new clock is a new read
    live = client.get(url, params={"asof": market_today().isoformat()}).json()
    assert seen["view"][2:] == (None, "record") and seen["reads"] == 2 and live["clock"] == "record"


def test_the_router_wire_shape(stub_client):
    client, _ = stub_client
    body = client.get(
        f"/dashboards/fundamentals-multiples/{T1}", params={"asof": "2026-08-14"}
    ).json()
    assert body["asof"] == "2026-08-14" and body["thesis_name"] == "Grid"
    assert len(body["axis"]) == 12 and (body["axis"][0], body["axis"][-1]) == ("2023-Q3", "2026-Q2")
    assert [r["key"] for r in body["rows"]] == [
        "link:Turbines",
        "link:Cables",
        "link:Empty link",
        "old:Old wires",
        "silent:Context",
        "silent:Bench",
    ]
    turbines = body["rows"][0]
    assert (turbines["kind"], turbines["descriptor"]) == ("link", "make the power")
    assert turbines["companies"] == [str(SA), str(SB), "ticker:UNR"]
    assert turbines["median"]["counting"] == 3
    q2 = next(p for p in turbines["median"]["points"] if p["quarter"] == "2026-Q2")
    assert q2["n"] == 2 and q2["median"] == pytest.approx(35.0)
    quiet = next(p for p in turbines["median"]["points"] if p["quarter"] == "2025-Q1")
    assert quiet == {"quarter": "2025-Q1", "median": None, "n": 1}
    assert body["rows"][-1]["median"] is None  # Bench: no median
    a = next(c for c in body["companies"] if c["ticker"] == "AAA")
    assert (a["security_id"], a["state"], a["periods"], a["silent"]) == (
        str(SA),
        "series",
        14,
        False,
    )
    assert len(a["bars"]) == 12
    bar = a["bars"][-1]
    assert (
        bar["quarter"] == "2026-Q2"
        and bar["period_end"] == "2026-06-30"
        and bar["filed"] == "2026-08-09"
    )
    assert bar["value"] == 180.0 and bar["basis"] == "native" and bar["yoy"] == pytest.approx(20.0)
    assert bar["url"].endswith("/000087654426080901/") and bar["first_knowable"] is None
    assert a["last_print"] == {
        "quarter": "2026-Q2",
        "period_end": "2026-06-30",
        "filed": "2026-08-09",
        "days_before": 5,
        "yoy": pytest.approx(20.0),
    }
    u = next(c for c in body["companies"] if c["ticker"] == "UNR")
    assert (u["state"], u["security_id"], u["bars"], u["last_print"]) == (
        "unresolved",
        None,
        [],
        None,
    )
    assert body["counts"]["companies"] == 7 and body["counts"]["thin"] == 1
    assert body["card_line"]["counting"] == 5 and body["roster_fallback"] is False


def test_the_router_404s_a_card_not_counted_at_the_asof(stub_client):
    client, seen = stub_client
    r = client.get(
        f"/dashboards/fundamentals-multiples/{uuid.uuid4()}", params={"asof": "2026-08-14"}
    )
    assert r.status_code == 404 and seen["reads"] == 0


def test_the_picker_list_is_cached(stub_client):
    client, seen = stub_client
    body = client.get(
        "/dashboards/fundamentals-multiples/cards", params={"asof": "2026-08-14"}
    ).json()
    assert [c["name"] for c in body["cards"]] == [
        "Grid",
        "Batteries",
    ]  # the stub's own order, served as read
    assert (body["live_cards"], body["not_yet_created"], body["cache"]) == (3, 1, "computed")
    again = client.get(
        "/dashboards/fundamentals-multiples/cards", params={"asof": "2026-08-14"}
    ).json()
    assert again["cache"] == "hit" and seen["card_reads"] == 1


# ---------------------------------------------------------------------------------------------------------
# DB-backed: the roster and the facts are as of the past view (skips without Postgres)
# ---------------------------------------------------------------------------------------------------------

PAST = date(2026, 6, 5)  # a past as-of
LATER = date(2026, 8, 12)  # a later past as-of, after every "late" print below
INGESTED = datetime(
    2026, 9, 1, tzinfo=timezone.utc
)  # our own ingest instant: the public clock must not care


def _security(db, ticker: str, cik: str | None) -> UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, 'equity', %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    return sid


_STAMP = [0]


def _revenue(
    db,
    sid: UUID,
    period_end: date,
    value: float,
    filed: date,
    accession: str,
    *,
    basis: str = "native",
) -> UUID:
    """One stored revenue version through the ingest's own row shape; every one INGESTED in September (a distinct
    second each, so the versions of one period stay distinct), so only the public clock can place it.
    """
    _STAMP[0] += 1
    q = QuarterPoint(
        metric_key="revenue",
        period_end=period_end,
        value=value,
        filed=filed,
        accession=accession,
        basis=basis,
        fiscal_period=f"Q{(period_end.month - 1) // 3 + 1}",
        fiscal_year=period_end.year,
    )
    return append_fact(
        db,
        "fact_fundamentals",
        {**fundamentals_row(sid, q), "recorded_at": INGESTED + timedelta(seconds=_STAMP[0])},
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
    r = client.get(
        f"/dashboards/fundamentals-multiples/{thesis_id}", params={"asof": asof.isoformat()}
    )
    assert r.status_code == 200, r.text
    return r.json()


def _card(name: str, basket: list[BasketMember]) -> Thesis:
    return Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name=name,
        narrative="x",
        segments=[Segment(label="Core"), Segment(label=CONTEXT_LABEL), Segment(label=BENCH_LABEL)],
        basket=basket,
    )


def _company_in(body: dict, ticker: str) -> dict:
    return next(c for c in body["companies"] if c["ticker"] == ticker)


def test_a_revenue_print_filed_after_a_past_asof_is_not_drawn_and_a_restated_one_dates_at_its_filing(
    db,
):
    """CMA: five quarterly prints public by June 5 (2025-Q1 .. 2026-Q1), a restated 2026-Q1 (a NEW value, filed
    Aug 7), a re-report of 2025-Q2 (the SAME value, filed Aug 7 as a prior-year comparative) and a new 2026-Q2
    print (filed Aug 7); every one INGESTED in September. At June 5 the page draws the five original prints only:
    the 2026-Q1 bar is the first filing's value, dated May 11, with no 2026-Q2 bar at all. At Aug 12 it draws the
    2026-Q2 print, the restated 2026-Q1 value dated Aug 7, and 2025-Q2 dated Aug 7 but first knowable Aug 8, 2025.
    """
    a = _security(db, "CMA", CIK)
    ends = [
        date(2025, 3, 31),
        date(2025, 6, 30),
        date(2025, 9, 30),
        date(2025, 12, 31),
        date(2026, 3, 31),
    ]
    filed = [
        date(2025, 5, 12),
        date(2025, 8, 8),
        date(2025, 11, 7),
        date(2026, 2, 19),
        date(2026, 5, 11),
    ]
    values = [100.0, 160.0, 170.0, 180.0, 200.0]
    for i, (end, f, v) in enumerate(zip(ends, filed, values)):
        _revenue(db, a, end, v, f, f"0000876544-{f:%y}-{f:%m%d}01")
    # the restated Q1 (a different value), the re-report of Q2-2025 (the same value) and the new Q2-2026 print
    _revenue(db, a, ends[4], 260.0, date(2026, 8, 7), "0000876544-26-080701")
    _revenue(db, a, ends[1], 160.0, date(2026, 8, 7), "0000876544-26-080702")
    _revenue(db, a, date(2026, 6, 30), 240.0, date(2026, 8, 7), "0000876544-26-080703")
    card = _card(
        "Multiples fixture", [BasketMember(ticker="CMA", role="x", security_id=a, segment="Core")]
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

    pa, la = _company_in(past, "CMA"), _company_in(later, "CMA")
    assert past["clock"] == "public"
    # June 5: five prints, none after the as-of, the newest axis quarter is 2026-Q1
    assert [b["quarter"] for b in pa["bars"]] == [
        "2025-Q1",
        "2025-Q2",
        "2025-Q3",
        "2025-Q4",
        "2026-Q1",
    ]
    assert past["axis"][-1] == "2026-Q1"
    q1 = pa["bars"][-1]
    assert (q1["value"], q1["filed"]) == (200.0, "2026-05-11")
    assert q1["yoy"] == pytest.approx(100.0)  # 200 against 2025-Q1's 100
    assert pa["bars"][1]["filed"] == "2025-08-08" and pa["bars"][1]["first_knowable"] is None
    assert pa["last_print"]["days_before"] == 25 and pa["last_print"]["filed"] == "2026-05-11"
    assert all(b["filed"] <= PAST.isoformat() for b in pa["bars"])

    # Aug 12: the new print is drawn; the restated value stands for 2026-Q1, dated at ITS filing
    assert [b["quarter"] for b in la["bars"]][-2:] == ["2026-Q1", "2026-Q2"]
    q1b, q2b = la["bars"][-2], la["bars"][-1]
    assert (q1b["value"], q1b["filed"]) == (260.0, "2026-08-07")
    assert q1b["first_knowable"] is None  # a changed number is new knowledge at its own date
    assert (q2b["value"], q2b["filed"], q2b["yoy"]) == (240.0, "2026-08-07", pytest.approx(50.0))
    assert la["last_print"]["days_before"] == 5
    # the identical re-report keeps the earlier date it was first public
    rr = next(b for b in la["bars"] if b["quarter"] == "2025-Q2")
    assert rr["filed"] == "2026-08-07" and rr["first_knowable"] == "2025-08-08"
    assert rr["first_knowable_accession"] == "0000876544-25-080801"
    assert rr["url"].endswith("/000087654426080702/")


def test_a_roster_row_added_after_a_past_asof_is_not_a_chart(db):
    """Two promotes of one card: the first roster (CMA in Core) is snapshotted on June 1, the second (CMA, plus
    CMB in Core and CMC in Context) on June 10. The page at June 5 charts the first roster only; at June 12 all
    three; the live view (today) the current roster. The roster note says nothing at either past as-of (a snapshot
    stood in)."""
    a, b, c = _security(db, "CMA", CIK), _security(db, "CMB", None), _security(db, "CMC", None)
    for sid in (a, b, c):
        _revenue(db, sid, date(2026, 3, 31), 100.0, date(2026, 5, 11), "0000876544-26-051101")
    first = _card(
        "Multiples roster", [BasketMember(ticker="CMA", role="x", security_id=a, segment="Core")]
    )
    thesis_repo.upsert(db, first)
    db.commit()
    _pin_latest_snapshot(db, first.id, datetime(2026, 6, 1, tzinfo=timezone.utc))
    second = first.model_copy(
        update={
            "basket": [
                first.basket[0],
                BasketMember(ticker="CMB", role="x", security_id=b, segment="Core"),
                BasketMember(
                    ticker="CMC",
                    role="x",
                    security_id=c,
                    segment=CONTEXT_LABEL,
                    context_role=ContextRole.PAYER,
                ),
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
            f"/dashboards/fundamentals-multiples/{uuid.uuid4()}", params={"asof": PAST.isoformat()}
        )
    finally:
        app.dependency_overrides.clear()
        CACHE.clear()

    def charts(body: dict) -> list[str]:
        return sorted(c["ticker"] for c in body["companies"])

    assert missing.status_code == 404
    assert charts(past) == ["CMA"] and past["counts"]["roster_rows"] == 1
    assert [r["key"] for r in past["rows"]] == ["link:Core"] and past["rows"][0]["companies"] == [
        str(a)
    ]
    assert past["roster_fallback"] is False and past["clock"] == "public"
    assert charts(later) == ["CMA", "CMB", "CMC"] and later["counts"]["roster_rows"] == 3
    core = next(r for r in later["rows"] if r["key"] == "link:Core")
    assert core["companies"] == [str(a), str(b)]
    ctx = next(r for r in later["rows"] if r["key"] == "silent:Context")
    assert ctx["companies"] == [str(c)] and ctx["median"] is None
    assert later["card_line"]["counting"] == 2  # CMC is Context: never counted
    assert charts(live) == ["CMA", "CMB", "CMC"] and live["clock"] == "record"


def test_an_active_set_aside_is_counted_and_the_read_falls_back_to_the_version_it_superseded(db):
    """CMA's 2026-Q1 revenue: the first filing (May 11, 200), then a bad restatement (Aug 7, 9,000). The data-quality
    set-aside on the restatement (appended later, read only here) makes every read fall back to the first filing's
    number. At Aug 12 the bar is 200 dated May 11 and CMA carries one set-aside; at June 5 the set-aside (a version
    filed Aug 7) is not yet on any chart, so the count is zero."""
    a = _security(db, "CMA", CIK)
    _revenue(db, a, date(2025, 3, 31), 100.0, date(2025, 5, 12), "0000876544-25-051201")
    _revenue(db, a, date(2026, 3, 31), 200.0, date(2026, 5, 11), "0000876544-26-051101")
    bad = _revenue(db, a, date(2026, 3, 31), 9000.0, date(2026, 8, 7), "0000876544-26-080701")
    fundamentals_set_aside_repo.append_set_aside(
        db,
        bad,
        reason="a fiscal-year total in a quarter",
        rule="fundamentals-quality-screen@test",
        author="system:test",
    )
    card = _card(
        "Multiples aside", [BasketMember(ticker="CMA", role="x", security_id=a, segment="Core")]
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

    la, pa = _company_in(later, "CMA"), _company_in(past, "CMA")
    bar = la["bars"][-1]
    assert (bar["quarter"], bar["value"], bar["filed"]) == ("2026-Q1", 200.0, "2026-05-11")
    assert bar["yoy"] == pytest.approx(100.0)
    assert la["set_aside"] == 1 and later["counts"]["set_aside"] == 1
    assert pa["set_aside"] == 0 and past["counts"]["set_aside"] == 0
