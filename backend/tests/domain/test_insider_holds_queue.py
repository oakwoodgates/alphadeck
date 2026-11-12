"""Bad dates step 3 — the pure pieces of the steward's queue (``domain.insider_holds``): the suggested date
(spec 4.5), the ratified-date check, and the wording.

DIRECT-INPUT CONTRACTS: the holds here are dicts built by hand to pin the ARITHMETIC (a Feb 29, an unreadable
date, a missing filing date) — none is presented as a real filing. The real filings' buckets and suggestions are
pinned end to end in ``tests/app/test_admin_insider_holds_api.py``.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from domain.config import DEFAULT_CONFIG
from domain.insider_holds import (
    AMENDMENT_ROW_WORDS,
    BUCKET_AMENDMENT_ROW,
    BUCKET_OWED,
    BUCKET_PLACED,
    filing_market_date,
    hold_label,
    page_bit,
    placement_night,
    placement_problem,
    quiet_new_line,
    ratify_problem,
    reason_words,
    suggested_date,
)
from ingest.edgar.form4 import hold_evidence
from tests import held_fixtures as H


def _hold(written, *, accepted=None, filed=None, por=None, accession="0000000000-24-000001"):
    return {
        "accession": accession,
        "row": {"txn_date_as_filed": written},
        "accepted": accepted,
        "filed": filed,
        "period_of_report_as_filed": por,
    }


# 2024-05-28 19:30 New York (an evening acceptance: its market date is the 28th)
_ACC = datetime(2024, 5, 28, 23, 30, tzinfo=timezone.utc)


def test_the_written_month_and_day_in_the_latest_year_on_or_before_the_filing():
    assert suggested_date(_hold("2028-05-24", accepted=_ACC, por="2024-05-24")) == (
        date(2024, 5, 24),
        True,
    )
    # a written day after the market date in its year falls to the year before
    assert suggested_date(_hold("2028-06-01", accepted=_ACC, por="2024-05-24")) == (
        date(2023, 6, 1),
        False,  # before the period of report: it does not agree
    )
    # the market date itself is on or before
    assert suggested_date(_hold("0024-05-28", accepted=_ACC))[0] == date(2024, 5, 28)


def test_agrees_needs_a_readable_possible_period_of_report():
    assert suggested_date(_hold("2028-05-24", accepted=_ACC)) == (date(2024, 5, 24), False)
    assert suggested_date(_hold("2028-05-24", accepted=_ACC, por="n/a")) == (
        date(2024, 5, 24),
        False,
    )
    # a period of report that is itself an impossible year (after the accession's 2024)
    assert suggested_date(_hold("2028-05-24", accepted=_ACC, por="2028-05-24")) == (
        date(2024, 5, 24),
        False,
    )


def test_the_filing_date_stands_in_when_there_is_no_acceptance():
    h = _hold("2031-03-01", filed=date(2021, 3, 18), por="2021-02-26")
    assert filing_market_date(h) == date(2021, 3, 18)
    assert suggested_date(h) == (date(2021, 3, 1), True)


def test_no_suggestion_without_a_readable_date_a_valid_year_or_a_filing_date():
    assert suggested_date(_hold("garbage", accepted=_ACC)) == (None, False)
    assert suggested_date(_hold("2028-13-01", accepted=_ACC)) == (None, False)
    assert suggested_date(_hold(None, accepted=_ACC)) == (None, False)
    assert suggested_date(_hold("2028-05-24")) == (None, False)  # no acceptance, no filing date
    # Feb 29 in neither the filing's year nor the year before (2026, 2025)
    feb = datetime(2026, 3, 2, 15, tzinfo=timezone.utc)
    assert suggested_date(_hold("2028-02-29", accepted=feb, accession="0000000000-26-000001")) == (
        None,
        False,
    )
    # ...and found when the filing's own year is a leap year
    leap = datetime(2024, 3, 4, 15, tzinfo=timezone.utc)
    assert suggested_date(_hold("2028-02-29", accepted=leap))[0] == date(2024, 2, 29)


def test_a_ratified_date_passes_the_year_rule_and_sits_on_or_before_the_market_date():
    h = _hold("2028-05-24", accepted=_ACC)
    assert ratify_problem(h, date(2024, 5, 24)) is None
    assert ratify_problem(h, date(2024, 5, 28)) is None
    assert "after the filing's market date" in ratify_problem(h, date(2024, 5, 29))
    assert "year rule" in ratify_problem(h, date(1933, 1, 1))
    assert "year rule" in ratify_problem(_hold("x", filed=date(2024, 1, 2)), date(2025, 1, 1))
    assert "no acceptance and no filing date" in ratify_problem(_hold("x"), date(2024, 1, 1))


def test_the_words():
    assert "1934" in reason_words("year_before_1934")
    assert reason_words("something_else") == "something_else"
    bit = page_bit(["ABCD 0000000000-24-000001 (P, 800 sh)"], 0)
    assert bit.startswith(
        "1 insider row(s) held because the filing's transaction date cannot be true"
    )
    assert "not counted" in bit and "owed a steward decision" in bit
    assert quiet_new_line({BUCKET_OWED: 3}) == ""
    assert quiet_new_line({BUCKET_AMENDMENT_ROW: 1}) == ""  # a held 4/A row pages: never quiet
    label = hold_label(
        ticker="ABCD",
        security_id="s",
        accession="0000000000-24-000001",
        form="4/A",
        code="S",
        shares=100,
        bucket=BUCKET_AMENDMENT_ROW,
    )
    assert label == f"ABCD 0000000000-24-000001 4/A (S, 100 sh) {AMENDMENT_ROW_WORDS}"
    assert "cannot be applied yet" in label and "not counted" in label
    line = quiet_new_line({BUCKET_OWED: 1, BUCKET_PLACED: 2})
    assert (
        line.startswith("2 new held insider row(s) that need no decision")
        and "placed by rule" in line
    )


# --- why the rule does not place an owed row (``placement_problem``) --------------------------------------------

_ON_TIME = DEFAULT_CONFIG.insider_hold_on_time_days


def test_placement_problem_names_each_check_the_rule_fails():
    # direct-input contracts: one hold per check, built by hand (never presented as a real filing)
    assert (
        placement_problem(_hold("x", por="2024-05-24"), 5)
        == "no acceptance time is known for the filing"
    )
    # a filing date alone is not an acceptance time: the rule needs the acceptance
    assert placement_problem(_hold("x", filed=date(2024, 5, 28), por="2024-05-24"), 5) == (
        "no acceptance time is known for the filing"
    )
    assert placement_problem(_hold("x", accepted=_ACC), 5) == "the filing gives no period of report"
    assert placement_problem(_hold("x", accepted=_ACC, por="n/a"), 5) == (
        "the period of report as filed (n/a) cannot be read"
    )
    assert placement_problem(_hold("x", accepted=_ACC, por="0023-03-23"), 5) == (
        "the period of report as filed (0023-03-23) fails the year rule (before 1934, or after the "
        "filing's year)"
    )
    assert placement_problem(_hold("x", accepted=_ACC, por="2024-05-29"), 5) == (
        "the period of report (2024-05-29) is after the filing (2024-05-28)"
    )
    assert placement_problem(_hold("x", accepted=_ACC, por="2024-05-22"), 5) == (
        "the filing was made 6 days after its period of report (2024-05-22), more than the on-time "
        "window of 5 days"
    )
    # the window's own edges are on time: no problem, and a night
    for por in ("2024-05-23", "2024-05-28"):
        h = _hold("x", accepted=_ACC, por=por)
        assert placement_problem(h, 5) is None and placement_night(h, 5) is not None


def _real_holds() -> list[tuple[str, dict]]:
    """One hold per REAL Form 4 in ``tests/held_fixtures.py`` (the filing's own period of report and SEC's
    acceptance, as the ingest stores them), plus the real BBAI and LSCC filings, which carry no acceptance.
    """
    out = []
    for acc, f in H.FILINGS.items():
        if f.form != "4":
            continue
        ev = hold_evidence(H.xml(acc))
        out.append(
            (
                acc,
                {
                    "accession": acc,
                    "accepted": f.accepted,
                    "filed": f.filed,
                    "period_of_report_as_filed": ev.period_of_report,
                },
            )
        )
    fix = Path(__file__).resolve().parents[1] / "fixtures" / "edgar"
    for acc, name in (
        ("0001628280-24-010030", "form4_bbai_leading_zero_year.xml"),
        ("0001437749-24-004874", "form4_lscc_future_year.xml"),
    ):
        ev = hold_evidence((fix / name).read_text(encoding="utf-8"))
        out.append((acc, {"accession": acc, "period_of_report_as_filed": ev.period_of_report}))
    return out


@pytest.mark.parametrize("days", [0, _ON_TIME, 200])
def test_placement_problem_is_none_exactly_when_the_rule_places_the_row(days):
    for acc, h in _real_holds():
        night, problem = placement_night(h, days), placement_problem(h, days)
        assert (night is None) == (problem is not None), (acc, days, night, problem)


def test_the_real_late_filing_says_how_late_it_is():
    holds = dict(_real_holds())
    assert placement_problem(holds["0000899243-21-012557"], _ON_TIME) == (
        "the filing was made 122 days after its period of report (2020-11-16), more than the on-time "
        f"window of {_ON_TIME} days"
    )
    assert placement_problem(holds["0001628280-24-010030"], _ON_TIME) == (
        "no acceptance time is known for the filing"
    )
    assert placement_problem(holds["0001354488-15-000004"], _ON_TIME) is None
