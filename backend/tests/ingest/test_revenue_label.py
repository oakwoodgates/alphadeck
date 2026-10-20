"""The revenue-LABEL fix (2026-10-20): the fundamentals ingest stores each company's CURRENT revenue label.

The bug: ``_usd_duration_rows`` took the FIRST ``REVENUE_CONCEPTS`` label present — ``us-gaap:Revenues`` —
and most filers moved to a ``RevenueFromContractWithCustomer*`` tag around ASC 606, so they kept a dead series
(MEASURED on prod: 163 of 495 active companies with revenue; AMD's ended 2017-09-30). The fix
(``ingest.fundamentals.select_revenue``): the label whose newest quarter ends latest — on a tie, the one with
more quarters, then the list order — never joined with another; two total-revenue tags appended to the list
(utilities, banks); a data-quality screen that keeps rows no genuine quarter can be out of the series (a
fiscal-year total under a quarter context; a derived Q4 across a restatement); and on a filing where a stored
old-label number differs from the current label's, the current label's number is appended as a version of
THAT filing (``plan_versions``).

Every company here is a REAL companyfacts document trimmed to its revenue nodes
(``tests/fundamentals_fixtures.py``). The ``plan_versions`` / tie contract tests at the bottom state the
function's contract with direct inputs; they fabricate no outcome. The DB tests skip without Postgres.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from db.bitemporal import KNOWABLE_FROM, KNOWABLE_SOURCE, version_knowable_date
from db.session import DEFAULT_TENANT_ID
from domain.config import DEFAULT_CONFIG
from domain.market_time import known_at_for_asof
from domain.security import Security
from ingest.edgar.client import EdgarClient
from ingest.edgar.extract import _REVENUE, REVENUE_CONCEPTS, _days, _first, _has_annual_revenue
from ingest.fundamentals import (
    REVENUE_METRIC,
    SCREEN_FY_IN_QUARTER,
    SCREEN_RESTATED_FY_Q4,
    SCREEN_YEAR_IN_Q4,
    SHARES_OUT_METRIC,
    HeldFiling,
    QuarterPoint,
    _label_series,
    _quarters_from_rows,
    _stampable_usd_duration_rows,
    extract_revenue_quarters,
    held_versions,
    ingest_fundamentals_for_security,
    plan_versions,
    select_revenue,
    store_quarters,
)
from pipeline.ingest_fundamentals import ingest_fundamentals_for_securities
from signals import revenue_acceleration as ra
from signals.base import PointInTimeData
from tests.fundamentals_fixtures import COMPANYFACTS_CIK, companyfacts_cache, companyfacts_revenue

RFC_EX = "RevenueFromContractWithCustomerExcludingAssessedTax"
RFC_IN = "RevenueFromContractWithCustomerIncludingAssessedTax"
UTILITY = "RegulatedAndUnregulatedOperatingRevenue"
BANK = "RevenuesNetOfInterestExpense"


def _label_quarters(cf: dict, concept: str) -> list[QuarterPoint]:
    """One label's own quarters (screened) — the series that label supplies when it is chosen, each point
    stamped with its tag (as ``select_revenue`` stamps it)."""
    rows = _stampable_usd_duration_rows(cf["facts"]["us-gaap"].get(concept))
    return [replace(p, concept=concept) for p in _quarters_from_rows(rows)]


def _old_rule_quarters(cf: dict) -> list[QuarterPoint]:
    """What the PRE-FIX ingest stored: the first-present ``Revenues`` label's quarters with no screen. The
    screen only REMOVES rows, so the old output is the screened series plus the screened rows — exact when
    every screened row is a declined derived Q4 (asserted: no ``Revenues`` label here holds a quarter-context
    total, whose removal would also have let a derived Q4 in)."""
    pts, screened = _label_series(_stampable_usd_duration_rows(cf["facts"]["us-gaap"]["Revenues"]))
    assert all(x.reason == SCREEN_RESTATED_FY_Q4 for x in screened)
    back = [  # the old code kept a derived Q4 only when it was positive
        QuarterPoint(
            REVENUE_METRIC, x.period_end, x.value, x.filed, x.accession, "derived", "Q4", None
        )
        for x in screened
        if x.value > 0
    ]
    return sorted(pts + back, key=lambda p: (p.period_end, p.filed))


def _first_public(cf: dict) -> dict[tuple[date, float], date]:
    """(period_end, value) -> the earliest filing, under ANY revenue label in the document, that reported it."""
    out: dict[tuple[date, float], date] = {}
    for concept in cf["facts"]["us-gaap"]:
        for p in _label_quarters(cf, concept):
            k = (p.period_end, p.value)
            out[k] = min(out.get(k, p.filed), p.filed)
    return out


# --------------------------------------------------------------------------------------------------------
# the selection rule (pure, real documents)
# --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ticker", "label", "old_end", "new_end"),
    [
        ("AMD", RFC_EX, date(2017, 9, 30), date(2026, 6, 27)),
        ("HON", RFC_EX, date(2012, 6, 30), date(2026, 6, 30)),
        # NEE's contract tag and the utility tag both run to 2026-03-31; the utility tag (total operating
        # revenue) has the longer history, 162 quarters to 68
        ("NEE", UTILITY, date(2013, 9, 30), date(2026, 3, 31)),
    ],
)
def test_a_switched_company_gets_its_current_label(ticker, label, old_end, new_end):
    """The audit's examples: the first-present ``Revenues`` series is dead years ago; the CURRENT label runs
    to the latest quarter in the document, and the series is that label's alone."""
    cf = companyfacts_revenue(ticker)
    old = _label_quarters(cf, "Revenues")
    assert max(p.period_end for p in old) == old_end  # what the first-present rule stored
    sel = select_revenue(cf)
    assert sel.concept == label
    assert sel.points == _label_quarters(cf, label)  # one label, never joined
    assert sel.points[-1].period_end == new_end
    assert extract_revenue_quarters(cf) == sel.points
    assert sel.labels["Revenues"][2] == old_end and sel.labels[label][2] == new_end


def test_a_label_that_yields_no_quarter_is_never_chosen():
    """LHX carries a ``Revenues`` node with stampable rows but no quarter in them; the old rule took it (the
    first present) and stored NO revenue (MEASURED: 13 basket companies). It is not a candidate now.
    """
    cf = companyfacts_revenue("LHX")
    assert _stampable_usd_duration_rows(cf["facts"]["us-gaap"]["Revenues"])  # present, stampable...
    assert _label_quarters(cf, "Revenues") == []  # ...and yields nothing
    sel = select_revenue(cf)
    assert sel.concept == RFC_EX and "Revenues" not in sel.labels
    assert sel.points[-1].period_end == date(2026, 7, 3)


def test_overlapping_labels_are_never_spliced():
    """VICR reports BOTH labels on the same 2024-2025 filings. They agree on every shared filing but one —
    Q2-2025 on the 2025-08-01 10-Q (141,046,000 under ``Revenues``, 96,046,000 under the contract tag) — so a
    per-period join would mix two definitions. The current label (newest quarter 2026-03-31 vs 2025-12-31)
    supplies every point."""
    cf = companyfacts_revenue("VICR")
    rev = {(p.period_end, p.filed): p.value for p in _label_quarters(cf, "Revenues")}
    rfc = {(p.period_end, p.filed): p.value for p in _label_quarters(cf, RFC_EX)}
    shared = sorted(set(rev) & set(rfc))
    differ = [k for k in shared if rev[k] != rfc[k]]
    assert len(shared) > 1 and differ == [(date(2025, 6, 30), date(2025, 8, 1))]
    assert rev[differ[0]] == 141_046_000 and rfc[differ[0]] == 96_046_000
    sel = select_revenue(cf)
    assert sel.concept == RFC_EX
    q2 = [p for p in sel.points if p.period_end == date(2025, 6, 30)]
    assert [(p.filed, p.value) for p in q2] == [(date(2025, 8, 1), 96_046_000.0)]


def test_purity_branch_sees_annual_revenue_under_any_label():
    """``_has_annual_revenue`` took ``_first`` too: AMD's first-present ``Revenues`` holds only two 2017
    quarters, so AMD read as PRE-REVENUE and its purity candidate located the Item-1 business description
    instead of the segment table (MEASURED: 29 basket companies flip). Any label's annual row counts now.
    """
    facts = companyfacts_revenue("AMD")["facts"]
    first = _first(facts, _REVENUE, "USD")  # the old rule's rows: ``Revenues``, the first present
    assert first == facts["us-gaap"]["Revenues"]["units"]["USD"]
    assert not any(r.get("start") and _days(r) > 300 and float(r["val"]) > 0 for r in first)
    assert _has_annual_revenue(facts) is True


# --------------------------------------------------------------------------------------------------------
# the tie-break and the two appended tags (pure, real documents)
# --------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("ticker", "short", "long_"), [("TTMI", 4, 70), ("KULR", 29, 52)])
def test_a_tie_goes_to_the_label_with_more_quarters(ticker, short, long_):
    """Both labels' newest quarter ends on the same date; the list order would keep ``Revenues`` (TTMI's has 4
    quarters — the detector could evaluate nothing). The longer history wins, still from ONE label.
    """
    cf = companyfacts_revenue(ticker)
    sel = select_revenue(cf)
    assert sel.labels["Revenues"][2] == sel.labels[RFC_EX][2]  # a genuine tie on the newest quarter
    assert (sel.labels["Revenues"][0], sel.labels[RFC_EX][0]) == (short, long_)
    assert sel.concept == RFC_EX and sel.points == _label_quarters(cf, RFC_EX)


def test_the_appended_tags_come_after_the_original_three():
    """Appended, so the list-order fallback is unchanged for every company the original three cover."""
    assert [c for _t, c in REVENUE_CONCEPTS] == [
        "Revenues",
        RFC_EX,
        RFC_IN,
        UTILITY,
        BANK,
    ]


@pytest.mark.parametrize(
    ("ticker", "label", "old_end", "new_end", "new_value"),
    [
        ("XEL", UTILITY, date(2019, 9, 30), date(2026, 6, 30), 3_119_000_000),
        ("HAPN", BANK, date(2020, 12, 31), date(2026, 6, 30), 262_855_000),
    ],
)
def test_a_utility_and_a_bank_get_their_total_revenue_tag(
    ticker, label, old_end, new_end, new_value
):
    """XEL (a utility) and HAPN (a bank) stopped tagging any of the original three years ago; their current
    total revenue lives under the industry tag (``RegulatedAndUnregulatedOperatingRevenue`` = total operating
    revenue; ``RevenuesNetOfInterestExpense`` = net interest income + noninterest income)."""
    cf = companyfacts_revenue(ticker)
    sel = select_revenue(cf)
    assert max(v[2] for c, v in sel.labels.items() if c not in (UTILITY, BANK)) == old_end
    assert sel.concept == label and sel.points == _label_quarters(cf, label)
    assert (sel.points[-1].period_end, sel.points[-1].value) == (new_end, new_value)


# --------------------------------------------------------------------------------------------------------
# the data-quality screen (real documents, then the guards with direct inputs)
# --------------------------------------------------------------------------------------------------------


def _screened(ticker: str) -> list[tuple[str, date, date, float, float]]:
    return [
        (x.reason, x.period_end, x.filed, x.value, x.reference)
        for x in select_revenue(companyfacts_revenue(ticker)).screened
    ]


def test_screen_a_fiscal_year_total_filed_under_a_quarter_context():
    """VICR's FY2025 10-K tags Q3-2025 as $407,701,000 — the whole year (its 10-Q said $110,423,000). The row
    is screened, never stored; Q3-2025 keeps the 10-Q's figure."""
    assert _screened("VICR") == [
        (SCREEN_FY_IN_QUARTER, date(2025, 9, 30), date(2026, 3, 2), 407_701_000.0, 407_701_000.0)
    ]
    q3 = [
        (p.filed, p.value)
        for p in select_revenue(companyfacts_revenue("VICR")).points
        if p.period_end == date(2025, 9, 30)
    ]
    assert q3 == [(date(2025, 10, 29), 110_423_000.0)]


def test_screen_both_lhx_quarter_context_years():
    """LHX's 2026 10-K files FY2024's $21.325B under a Q4 context beside the FY row (shape 1), and FY2025's
    $21.865B ONLY under a Q4 context — no FY row at all — while the 9-month YTD was $16.217B (shape 2).
    Q4-2024 now comes from FY − 9-month ($5.523B, from the 2025 10-K, a year earlier than the bad row);
    Q4-2025 has no honest figure, so it is absent and the detector declines across it."""
    assert _screened("LHX") == [
        (SCREEN_FY_IN_QUARTER, date(2025, 1, 3), date(2026, 2, 12), 21_325e6, 21_325e6),
        (SCREEN_YEAR_IN_Q4, date(2026, 1, 2), date(2026, 2, 12), 21_865e6, 16_217e6),
    ]
    pts = select_revenue(companyfacts_revenue("LHX")).points
    q4_2024 = [(p.filed, p.value, p.basis) for p in pts if p.period_end == date(2025, 1, 3)]
    assert q4_2024 == [
        (date(2025, 2, 14), 5_523e6, "derived"),
        (date(2026, 2, 12), 5_523e6, "derived"),
    ]
    assert not [p for p in pts if p.period_end == date(2026, 1, 2)]


def test_screen_a_derived_q4_across_a_restatement():
    """LFMD restated FY2024 to $154,824,075 (continuing operations) on 2026-03-10; its 9-month last moved on
    2025-11-17 with the previous restatement. FY − 9-month = $5,487,012, against ~$50M quarters: screened.
    The two earlier pairings, each on one basis, stay ($64.25M from the original 10-K, $59.50M from the
    2025-11-17 10-Q that restated both)."""
    assert _screened("LFMD") == [
        (SCREEN_RESTATED_FY_Q4, date(2024, 12, 31), date(2026, 3, 10), 5_487_012.0, 154_824_075.0)
    ]
    q4 = [
        (p.filed, p.value)
        for p in select_revenue(companyfacts_revenue("LFMD")).points
        if p.period_end == date(2024, 12, 31)
    ]
    assert q4 == [(date(2025, 3, 11), 64_254_572.0), (date(2025, 11, 17), 59_502_219.0)]


def test_a_recast_on_one_basis_is_kept_one_across_bases_is_not():
    """TTMI recast FY2019 on 2021-02-22 after its 9-month 2019 was recast in the Q3-2020 10-Q: one basis, so
    Q4-2019 = $535.7M stands. FY2018 was recast the same day but its 9-month never was: the would-be
    $101.4M Q4 is screened. HON's FY2023 recast for the Solstice spin (2026-02-17) against a never-recast
    9-month is the same shape ($5.787B "Q4" screened)."""
    ttmi = select_revenue(companyfacts_revenue("TTMI"))
    q4_2019 = [(p.filed, p.value) for p in ttmi.points if p.period_end == date(2019, 12, 30)]
    assert (date(2021, 2, 22), 535_715_000.0) in q4_2019
    assert [(x.reason, x.period_end, x.filed) for x in ttmi.screened] == [
        (SCREEN_RESTATED_FY_Q4, date(2018, 12, 31), date(2021, 2, 22))
    ]
    assert _screened("HON") == [
        (SCREEN_RESTATED_FY_Q4, date(2023, 12, 31), date(2026, 2, 17), 5_787e6, 33_009e6)
    ]


def _row(start: str, end: str, val: float, filed: str, form: str = "10-Q", accn: str = "a") -> dict:
    return {"start": start, "end": end, "val": val, "filed": filed, "accn": accn, "form": form}


def test_screen_guard_a_year_earned_in_one_quarter_keeps_that_quarter():
    """Contract (direct inputs): a quarter equal to its year's total is a genuine quarter when every other
    quarter of that year is zero (a milestone payment) — tiny, lumpy revenue is real data."""
    rows = [
        _row("2025-01-01", "2025-03-31", 0, "2025-05-01"),
        _row("2025-04-01", "2025-06-30", 0, "2025-08-01"),
        _row("2025-07-01", "2025-09-30", 0, "2025-11-01"),
        _row("2025-10-01", "2025-12-31", 5_000_000, "2026-03-01", "10-K", "k"),
        _row("2025-01-01", "2025-12-31", 5_000_000, "2026-03-01", "10-K", "k"),
    ]
    pts, screened = _label_series(rows)
    assert screened == [] and (date(2025, 12, 31), 5_000_000.0) in [
        (p.period_end, p.value) for p in pts
    ]


def test_screen_never_judges_a_quarter_by_a_later_filing():
    """Contract (direct inputs; the independent review's case): a genuine $10.0M milestone Q1 filed
    2025-05-12, a $30K Q2, and the FY total ($10.03M) filed 2026-03-02. Judged against rows filed by its
    OWN filing, the Q1 has no year to equal — it is kept, so the store never depends on whether the company
    was first ingested before or after the 10-K."""
    rows = [
        _row("2025-01-01", "2025-03-31", 10_000_000, "2025-05-12", "10-Q", "q1"),
        _row("2025-04-01", "2025-06-30", 30_000, "2025-08-11", "10-Q", "q2"),
        _row("2025-01-01", "2025-12-31", 10_030_000, "2026-03-02", "10-K", "k"),
    ]
    pts, screened = _label_series(rows)
    assert screened == []
    assert (date(2025, 3, 31), 10_000_000.0) in [(p.period_end, p.value) for p in pts]


def test_screen_guard_other_quarters_must_be_a_material_share_of_the_year():
    """Contract (direct inputs): the 10-K files a Q4-context row equal to its own FY total, but the year's
    other quarters are $30K of $10.03M (0.3% — under the 5% share): a year earned in one quarter, kept.
    """
    rows = [
        _row("2025-01-01", "2025-03-31", 30_000, "2025-05-12", "10-Q", "q1"),
        _row("2025-10-01", "2025-12-31", 10_000_000, "2026-03-02", "10-K", "k"),
        _row("2025-01-01", "2025-12-31", 10_030_000, "2026-03-02", "10-K", "k"),
    ]
    pts, screened = _label_series(rows)
    assert screened == [] and (date(2025, 12, 31), 10_000_000.0) in [
        (p.period_end, p.value) for p in pts
    ]


def test_a_tie_never_prefers_a_subset_tag():
    """Contract (direct inputs): two tags end on the same quarter; the one with MORE quarters is smaller on
    every filing both carry — a component of revenue, never the total. The tie goes to the total despite its
    shorter history; tags that agree on their shared filings still go to the longer history."""

    def q(end: str, start: str, val: float, filed: str) -> dict:
        return {"start": start, "end": end, "val": val, "filed": filed, "accn": filed, "fy": 2026}

    longer_subset = [
        q("2025-03-31", "2025-01-01", 60, "2025-05-01"),
        q("2025-06-30", "2025-04-01", 60, "2025-08-01"),
        q("2025-09-30", "2025-07-01", 60, "2025-11-01"),
        q("2026-03-31", "2026-01-01", 70, "2026-05-01"),
    ]
    total = [
        q("2025-09-30", "2025-07-01", 100, "2025-11-01"),
        q("2026-03-31", "2026-01-01", 110, "2026-05-01"),
    ]

    def cf(a: list[dict], b: list[dict]) -> dict:
        return {
            "facts": {"us-gaap": {"Revenues": {"units": {"USD": a}}, RFC_EX: {"units": {"USD": b}}}}
        }

    assert (
        select_revenue(cf(total, longer_subset)).concept == "Revenues"
    )  # the total, 2 quarters vs 4
    same = [dict(r, val=v) for r, v in zip(longer_subset, (100, 100, 100, 110))]
    assert select_revenue(cf(total, same)).concept == RFC_EX  # they agree: the longer history


def test_screen_guard_a_big_genuine_q4_beside_its_fy_row_is_kept():
    """Contract (direct inputs): a Q4 larger than the first three quarters together passes when the 10-K states
    the year beside it — shape 2 fires only when the annual report has NO FY total."""
    rows = [
        _row("2025-01-01", "2025-03-31", 100, "2025-05-01"),
        _row("2025-04-01", "2025-06-30", 100, "2025-08-01"),
        _row("2025-07-01", "2025-09-30", 100, "2025-11-01"),
        _row("2025-01-01", "2025-09-30", 300, "2025-11-01"),
        _row("2025-10-01", "2025-12-31", 900, "2026-03-01", "10-K", "k"),
        _row("2025-01-01", "2025-12-31", 1200, "2026-03-01", "10-K", "k"),
    ]
    pts, screened = _label_series(rows)
    assert screened == [] and (date(2025, 12, 31), 900.0) in [(p.period_end, p.value) for p in pts]


# --------------------------------------------------------------------------------------------------------
# plan_versions — the store's contract (direct inputs)
# --------------------------------------------------------------------------------------------------------


def _pt(
    value: float, *, metric: str = REVENUE_METRIC, filed: date = date(2025, 8, 1)
) -> QuarterPoint:
    return QuarterPoint(
        metric_key=metric,
        period_end=date(2025, 6, 30),
        value=value,
        filed=filed,
        accession="acc",
        basis="native",
        fiscal_period="Q2",
        fiscal_year=2025,
        unit="USD" if metric == REVENUE_METRIC else "shares",
    )


def test_plan_stamps_a_new_filing_at_its_own_date():
    planned, skipped = plan_versions({}, [_pt(1.0)])
    assert skipped == 0 and len(planned) == 1
    assert planned[0].recorded_at == datetime(2025, 8, 1, tzinfo=timezone.utc)
    assert planned[0].supersedes_value is None


def test_plan_skips_a_held_filing_with_the_same_number():
    key = (REVENUE_METRIC, date(2025, 6, 30), date(2025, 8, 1))
    planned, skipped = plan_versions({key: HeldFiling(1, 1.0)}, [_pt(1.0)])
    assert planned == [] and skipped == 1


def test_plan_supersedes_a_revenue_filing_held_with_another_labels_number():
    """The current label's number becomes the LATEST version of the same filing — one microsecond after the
    versions already held — with ``valid_from`` (and so its knowable date) still the filing's."""
    key = (REVENUE_METRIC, date(2025, 6, 30), date(2025, 8, 1))
    planned, skipped = plan_versions({key: HeldFiling(1, 141_046_000.0)}, [_pt(96_046_000.0)])
    assert skipped == 0 and len(planned) == 1
    v = planned[0]
    assert v.supersedes_value == 141_046_000.0
    assert v.recorded_at == datetime(2025, 8, 1, tzinfo=timezone.utc) + timedelta(microseconds=1)
    row = {"valid_from": v.point.filed, "recorded_at": v.recorded_at}
    assert version_knowable_date(row) == date(2025, 8, 1)
    # re-planned over what that append leaves held: nothing more (idempotent)
    again, skipped = plan_versions({key: HeldFiling(2, 96_046_000.0)}, [_pt(96_046_000.0)])
    assert again == [] and skipped == 1


def test_plan_keeps_a_held_number_the_current_label_reports_later():
    """The PSQH shape (MEASURED on the dev copy): the old tag's number on a filing is the number the current
    tag itself reports for that period on a LATER filing — the current tag said something else at first. The
    held number is the current definition's, public since THIS filing; superseding it would date it a year
    late. Skipped, so D3a dates it here; a held number the current tag never reports is still superseded.
    """
    f1, f2 = date(2024, 8, 14), date(2025, 8, 12)
    key = (REVENUE_METRIC, date(2025, 6, 30), f1)
    later = [_pt(2_290_574.0, filed=f1), _pt(5_985_228.0, filed=f2)]
    planned, skipped = plan_versions({key: HeldFiling(1, 5_985_228.0)}, later)
    assert skipped == 1 and [(v.point.filed, v.supersedes_value) for v in planned] == [(f2, None)]
    planned, _ = plan_versions({key: HeldFiling(1, 1_000_000.0)}, later)
    assert [(v.point.filed, v.supersedes_value) for v in planned] == [(f1, 1_000_000.0), (f2, None)]


def test_plan_never_supersedes_a_share_filing():
    """The share series are stored per concept and never switch labels; a held share filing is skipped
    whatever its number (the behavior before the fix)."""
    key = (SHARES_OUT_METRIC, date(2025, 6, 30), date(2025, 8, 1))
    planned, skipped = plan_versions(
        {key: HeldFiling(1, 5.0)}, [_pt(6.0, metric=SHARES_OUT_METRIC)]
    )
    assert planned == [] and skipped == 1


def test_a_tie_keeps_the_revenue_concepts_order():
    """Two labels whose newest quarter ends the SAME day: the first in ``REVENUE_CONCEPTS`` order is kept, so
    a company already on its newest label is unchanged (MEASURED: 81 basket companies tie)."""

    def row(concept_val: float, accn: str) -> dict:
        return {
            "start": "2026-01-01",
            "end": "2026-03-31",
            "val": concept_val,
            "accn": accn,
            "fy": 2026,
            "fp": "Q1",
            "filed": "2026-05-01",
        }

    cf = {
        "facts": {
            "us-gaap": {
                RFC_EX: {"units": {"USD": [row(9.0, "b")]}},
                "Revenues": {"units": {"USD": [row(10.0, "a")]}},
            }
        }
    }
    sel = select_revenue(cf)
    assert sel.concept == "Revenues" and [p.value for p in sel.points] == [10.0]


# --------------------------------------------------------------------------------------------------------
# the store — DB-backed, the real ingest path, real documents
# --------------------------------------------------------------------------------------------------------


def _security(db, ticker: str) -> Security:
    sid = uuid.uuid4()
    cik = COMPANYFACTS_CIK[ticker]
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2020-01-01"),
        )
    db.commit()
    return Security(id=sid, tenant_id=DEFAULT_TENANT_ID, ticker=ticker, cik=cik)


def _count(db, sid) -> int:
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_fundamentals WHERE security_id = %s", (sid,))
        return cur.fetchone()["n"]


def _store_as_prod_held_it(db, sec: Security, ticker: str) -> int:
    """The pre-fix store: the first-present label's unscreened quarters through the REAL writer (prod's
    stamp)."""
    n = store_quarters(db, sec.id, _old_rule_quarters(companyfacts_revenue(ticker))).appended
    db.commit()
    return n


def _revenue(db, sid, asof: date) -> dict[date, dict]:
    rows = PointInTimeData(db, asof=asof, known_at=known_at_for_asof(asof)).fundamentals_facts(sid)
    return {r["period_end"]: r for r in rows if r["metric_key"] == "revenue"}


def _reingest(db, sec: Security, tmp_path, ticker: str):
    client = EdgarClient(cache_dir=companyfacts_cache(tmp_path, ticker), allow_live=False)
    res = ingest_fundamentals_for_security(db, sec, client=client, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    return res


@pytest.mark.parametrize("ticker", ["AMD", "HON", "NEE", "XEL", "TTMI", "KULR", "LFMD"])
def test_reingest_appends_the_current_label_once_count_the_table(db, tmp_path, ticker):
    """The pre-fix store, then the fixed ingest over the same document: it appends exactly the current
    label's versions not held, each stamped at its own filing (R17 — never now()); a re-run appends ZERO,
    counted on the table (the as-of read dedups, so a duplicate would hide behind a correct read).
    """
    sec = _security(db, ticker)
    held = _store_as_prod_held_it(db, sec, ticker)
    assert _count(db, sec.id) == held
    cf = companyfacts_revenue(ticker)
    old = {(p.period_end, p.filed): p.value for p in _old_rule_quarters(cf)}
    current = select_revenue(cf).points
    # a filing that reported the SAME number under both labels is already held (AMD: the two 2017 quarters
    # the 2018 10-Qs tagged both ways); everything else of the current label is new
    shared = [p for p in current if (p.period_end, p.filed) in old]
    assert all(old[(p.period_end, p.filed)] == p.value for p in shared)
    expect = [p for p in current if (p.period_end, p.filed) not in old]

    r1 = _reingest(db, sec, tmp_path, ticker)
    assert r1.appended == len(expect) and r1.skipped == len(shared)
    assert r1.screened == tuple(select_revenue(cf).screened)  # carried on the receipt...
    assert _count(db, sec.id) == held + len(expect)
    with db.cursor() as cur:
        cur.execute(
            "SELECT valid_from, recorded_at, period_end, value FROM fact_fundamentals "
            "WHERE security_id = %s",
            (sec.id,),
        )
        rows = cur.fetchall()
    assert all(
        r["recorded_at"] == datetime.combine(r["valid_from"], datetime.min.time(), timezone.utc)
        for r in rows
    )  # every version: valid_from = recorded_at = its filing (no backdating, no now())
    stored = {(r["period_end"], r["valid_from"], float(r["value"])) for r in rows}
    for x in select_revenue(cf).screened:  # ...and never stored
        assert (x.period_end, x.filed, x.value) not in stored

    r2 = _reingest(db, sec, tmp_path, ticker)
    assert r2.appended == 0 and _count(db, sec.id) == held + len(expect)


@pytest.mark.parametrize(
    ("ticker", "first_new_filing", "old_end", "new_end", "new_value", "new_filed"),
    [
        (
            "HON",
            date(2018, 4, 20),
            date(2012, 6, 30),
            date(2026, 6, 30),
            9_719_000_000,
            date(2026, 7, 23),
        ),
        (
            "AMD",
            date(2018, 8, 2),
            date(2017, 9, 30),
            date(2026, 6, 27),
            11_536_000_000,
            date(2026, 8, 5),
        ),
    ],
)
def test_the_as_of_read_before_and_after_the_reingest(
    db, tmp_path, ticker, first_new_filing, old_end, new_end, new_value, new_filed
):
    """At any as-of before the current label's FIRST filing the read is IDENTICAL before and after the
    re-ingest — nothing appended is visible before it was filed (no lookahead; HON's 2008-2012 history reads
    exactly as it did). Today, the newest quarter moves from the dead label's to the current one's, dated at
    its own filing."""
    sec = _security(db, ticker)
    _store_as_prod_held_it(db, sec, ticker)
    early, today = first_new_filing - timedelta(days=1), date(2026, 10, 20)
    before_early, before_today = _revenue(db, sec.id, early), _revenue(db, sec.id, today)
    assert max(before_today) == old_end
    assert (
        ticker != "HON" or max(before_early) == old_end
    )  # non-vacuous: HON's old series is visible

    _reingest(db, sec, tmp_path, ticker)
    assert _revenue(db, sec.id, early) == before_early
    after = _revenue(db, sec.id, today)
    assert max(after) == new_end
    assert float(after[new_end]["value"]) == new_value
    assert after[new_end][KNOWABLE_FROM] == new_filed


def test_first_knowability_survives_the_reingest(db, tmp_path):
    """D3a still holds on the re-ingested series: AMD's Q1-2025 ($7,438M) was first filed 2025-05-07 and
    re-reported UNCHANGED as the 2026-05-06 10-Q's comparative. The read picks the comparative but dates it
    at its first filing — so a year-old quarter never reads as newly knowable."""
    sec = _security(db, "AMD")
    _store_as_prod_held_it(db, sec, "AMD")
    _reingest(db, sec, tmp_path, "AMD")
    q = _revenue(db, sec.id, date(2026, 10, 20))[date(2025, 3, 29)]
    assert q["valid_from"] == date(2026, 5, 6) and q["accession"] == "0000002488-26-000076"
    assert q[KNOWABLE_FROM] == date(2025, 5, 7)
    assert q[KNOWABLE_SOURCE] == "0000002488-25-000047"


def test_a_period_superseded_on_two_filings_dates_at_its_first(db, tmp_path):
    """MTRN's Q1-2018 is $303,467,000 under the contract tag on its 2018-04-26 10-Q and every later filing;
    the old store held the ``Revenues`` sub-line ($11,680,000) on the 2018-04-26 10-Q and on the 2019-05-02
    comparative. The re-ingest supersedes BOTH filings, so the versions interleave (11.68M, 303M, 303M, 11.68M,
    303M, 303M). D3a's run counts each filing once, by its latest version, so the number dates at
    2018-04-26 — not at the 2019-05-02 comparative the superseded row used to break it at."""
    sec = _security(db, "MTRN")
    _store_as_prod_held_it(db, sec, "MTRN")
    _reingest(db, sec, tmp_path, "MTRN")
    with db.cursor() as cur:
        cur.execute(
            "SELECT valid_from, value FROM fact_fundamentals WHERE security_id = %s AND "
            "period_end = '2018-03-30' ORDER BY recorded_at, id",
            (sec.id,),
        )
        versions = [(r["valid_from"], float(r["value"])) for r in cur.fetchall()]
    assert versions == [
        (date(2018, 4, 26), 11_680_000.0),
        (date(2018, 4, 26), 303_467_000.0),
        (date(2019, 2, 14), 303_467_000.0),
        (date(2019, 5, 2), 11_680_000.0),
        (date(2019, 5, 2), 303_467_000.0),
        (date(2020, 2, 13), 303_467_000.0),
    ]
    q = _revenue(db, sec.id, date(2026, 10, 20))[date(2018, 3, 30)]
    assert float(q["value"]) == 303_467_000
    assert q[KNOWABLE_FROM] == date(2018, 4, 26)
    assert q[KNOWABLE_SOURCE] == "0001104657-18-000032"


def test_a_stored_revenue_row_names_its_tag(db, tmp_path):
    """#6: two tags can share one accession, so each re-ingested revenue row records which tag it came
    from (``source = companyfacts:<concept>``); the old rows and the share series keep ``companyfacts``.
    """
    sec = _security(db, "VICR")
    _store_as_prod_held_it(db, sec, "VICR")
    _reingest(db, sec, tmp_path, "VICR")
    with db.cursor() as cur:
        cur.execute(
            "SELECT source, count(*) AS n FROM fact_fundamentals WHERE security_id = %s GROUP BY source",
            (sec.id,),
        )
        by_source = {r["source"]: r["n"] for r in cur.fetchall()}
    assert set(by_source) == {"companyfacts", f"companyfacts:{RFC_EX}"}
    assert by_source["companyfacts"] == len(_old_rule_quarters(companyfacts_revenue("VICR")))


@pytest.mark.parametrize(
    "ticker",
    ["AMD", "HON", "NEE", "VICR", "LHX", "LFMD", "TTMI", "KULR", "XEL", "HAPN", "MTRN"],
)
def test_the_reingest_creates_no_phantom_past_event(db, tmp_path, ticker):
    """Walk every week from 2024-01-01 to 2026-10-20 through the REAL point-in-time read (``known_at`` capped
    at each as-of's market-day end, the serve path's scrub-back) and the REAL detector, after the re-ingest.
    Every revenue-acceleration event must be dated EXACTLY at the first filing that made its quarter's
    number public: never before (lookahead), never after (a stale number re-dated fresh — the D3 phantom).
    """
    sec = _security(db, ticker)
    _store_as_prod_held_it(db, sec, ticker)
    _reingest(db, sec, tmp_path, ticker)
    public = _first_public(companyfacts_revenue(ticker))
    events = 0
    d = date(2024, 1, 1)
    while d <= date(2026, 10, 20):
        pit = PointInTimeData(db, asof=d, known_at=known_at_for_asof(d))
        ev = ra.detect(pit, sec.id, d, DEFAULT_CONFIG)
        if ev is not None:
            events += 1
            pe = date.fromisoformat(ev.provenance[0].detail["period_end"])
            row = {r["period_end"]: r for r in pit.fundamentals_facts(sec.id)}[pe]
            assert ev.asof <= d
            assert ev.asof == public[(pe, float(row["value"]))], (d, pe, ev.asof)
            assert ev.provenance[0].ref == row[KNOWABLE_SOURCE]
        d += timedelta(days=7)
    # not vacuous: these real series fire in the window, except HON's (flat through its spin), LHX's (its
    # screened Q4-2025 leaves a hole the detector declines across), HAPN's and MTRN's
    assert events > 0 or ticker in ("HON", "LHX", "HAPN", "MTRN")


def test_a_same_filing_label_conflict_reads_the_current_labels_number(db, tmp_path):
    """VICR: the pre-fix store held Q2-2025 = 141,046,000 (``Revenues``) for the 2025-08-01 10-Q, where the
    current label reports 96,046,000. The re-ingest appends the current number as a version of that SAME
    filing: the old row stays (append-only, counted), and from 2025-08-01 on the read gives 96,046,000,
    dated at that filing."""
    sec = _security(db, "VICR")
    _store_as_prod_held_it(db, sec, "VICR")
    key = (REVENUE_METRIC, date(2025, 6, 30), date(2025, 8, 1))
    assert held_versions(db, sec.id)[key] == HeldFiling(1, 141_046_000.0)

    _reingest(db, sec, tmp_path, "VICR")
    assert held_versions(db, sec.id)[key] == HeldFiling(2, 96_046_000.0)
    with db.cursor() as cur:
        cur.execute(
            "SELECT value, recorded_at FROM fact_fundamentals WHERE security_id = %s AND "
            "period_end = '2025-06-30' AND valid_from = '2025-08-01' ORDER BY recorded_at",
            (sec.id,),
        )
        rows = [(float(r["value"]), r["recorded_at"]) for r in cur.fetchall()]
    stamp = datetime(2025, 8, 1, tzinfo=timezone.utc)
    assert rows == [(141_046_000.0, stamp), (96_046_000.0, stamp + timedelta(microseconds=1))]
    q = _revenue(db, sec.id, date(2025, 9, 1))[date(2025, 6, 30)]
    assert float(q["value"]) == 96_046_000 and q[KNOWABLE_FROM] == date(2025, 8, 1)
    assert _revenue(db, sec.id, date(2025, 7, 31)).get(date(2025, 6, 30)) is None  # not before it


def test_the_nightly_leg_stores_the_current_label_with_no_extra_step(db, tmp_path):
    """Prevention: the nightly's own fundamentals leg (``pipeline.ingest_fundamentals
    .ingest_fundamentals_for_securities``, what ``pipeline.daily`` calls per thesis) on a company it has
    never stored writes AMD's CURRENT series — its newest quarter, not 2017's."""
    sec = _security(db, "AMD")
    client = EdgarClient(cache_dir=companyfacts_cache(tmp_path, "AMD"), allow_live=False)
    [res] = ingest_fundamentals_for_securities(
        db, [sec], client=client, tenant_id=DEFAULT_TENANT_ID
    )
    assert res.error is None and res.appended == len(
        select_revenue(companyfacts_revenue("AMD")).points
    )
    rows = _revenue(db, sec.id, date(2026, 10, 20))
    assert max(rows) == date(2026, 6, 27)
    assert min(rows) == date(
        2017, 4, 1
    )  # the contract tag's own history, nothing from the dead label
