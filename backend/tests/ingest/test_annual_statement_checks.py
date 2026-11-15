"""Annual statements: currency, table and line selection, and the companyfacts cross-check (PR 4).

The extractor correctness plan's PR 4 (docs/temp/v2-extractor-correctness-plan-2026-11-15.md, findings 6 to 9 and
question 6), built against the 2026-11-15 audit of 20 annual cash-and-burn candidates, 7 of them wrong. Each test
below is one audited case (or a control the audit found right), on a fixture RECONSTRUCTED from the values and
wording the audit and plan recorded (``fixtures/sec_extractor/annual/audit-2026-11-15/README.md`` says field by
field what is recorded and what is filler). Every fixture reproduces, under the extractor before PR 4, the wrong
value the audit recorded; the docstrings name it.

Every annual cash-and-burn candidate stays a FLAG (operator decision): this suite checks the number, the currency
and the flags are right, and that the note says how each was established. OFFLINE: no network, no database.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import pytest

from domain.extraction import Tier
from ingest.edgar.annual_runway import extract_annual_runway

_FX = (
    Path(__file__).resolve().parent.parent
    / "fixtures"
    / "sec_extractor"
    / "annual"
    / "audit-2026-11-15"
)
_TODAY = date(2026, 11, 15)  # the audit date: staleness in the notes is deterministic
_FY2025 = date(2025, 12, 31)
_Q = 365.25 / 4  # the meter's quarter


def _text(name: str) -> str:
    return next(_FX.glob(f"{name}-*fin.txt")).read_text(encoding="utf-8")


def _cf(name: str) -> dict:
    return json.loads((_FX / f"cf-{name}.json").read_text(encoding="utf-8"))


def _run(
    name: str,
    *,
    text: str | None = None,
    cf: dict | None | str = "fixture",
    report_date: date = _FY2025,
    form: str = "20-F",
):
    facts, reason = extract_annual_runway(
        _cf(name) if cf == "fixture" else cf,
        _text(name) if text is None else text,
        annual_ref=f"https://sec.gov/{name}",
        annual_form=form,
        report_date=report_date,
        today=_TODAY,
    )
    assert reason is None and len(facts) == 1, (name, reason)
    f = facts[0]
    assert f.tier is Tier.FLAG and f.source == "annual-statements"  # ALWAYS a flag
    assert {p.kind for p in f.located_passages} == {"balance-sheet", "cash-flow"}
    return f


def _ocf(f) -> float:
    """The operating cash flow the quarterly burn was normalized from (the note's 364-day cf span)."""
    return -f.quarterly_burn_usd / _Q * 364


def _passage(f, kind: str) -> str:
    return next(p.excerpt for p in f.located_passages if p.kind == kind)


# ---------------------------------------------------------------------------------------------------------
# pattern 1: currency
# ---------------------------------------------------------------------------------------------------------


def test_ntes_yuan_with_a_usd_convenience_column_is_cny():
    """NTES prints RMB columns plus a convenience USD column. Before: USD, 47,167,904,000 (the yuan figure
    labeled dollars: the bare list order let "USD" win). After: CNY, settled by companyfacts' us-gaap CNY unit
    reproducing both figures exactly; the USD unit's 6,744,921,000 is the convenience column, never the value.
    """
    f = _run("NTES")
    assert f.statement_currency == "CNY"
    assert f.cash_usd == 47_167_904_000.0
    assert _ocf(f) == pytest.approx(50_739_800_000.0)
    assert f.flags == [
        "annual-statements"
    ]  # companyfacts settled the currency: nothing left to judge
    assert "CNY" in f.note and "convenience" in f.note
    assert "companyfacts agrees with the statement's cash balance" in f.note


def test_jcse_singapore_dollars_are_sgd():
    """JCSE prints S$'000 columns and a US$'000 convenience column. Before: USD, 9,931,000 / 2,686,000 (both
    Singapore dollars). After: SGD, settled by companyfacts' SGD unit."""
    f = _run("JCSE")
    assert f.statement_currency == "SGD"
    assert f.cash_usd == 9_931_000.0
    assert _ocf(f) == pytest.approx(2_686_000.0)
    assert f.flags == ["annual-statements"]
    assert "7,720" not in f.note.split("Cross-check")[0]  # the US$ column never becomes the value


def test_dnn_cad_dollars_header_is_cad():
    """DNN's cash-flow header reads "Expressed in thousands of CAD dollars" and its rows print "$". Before: USD
    (the CAD pattern knew only "Canadian dollars", the bare "$" matched USD, and the cash-flow side won).
    After: CAD from the header, confirmed by companyfacts' ifrs-full CAD unit; the span is companyfacts'
    matched year."""
    f = _run("DNN", form="40-F")
    assert f.statement_currency == "CAD"
    assert f.cash_usd == 465_918_000.0
    assert f.quarterly_burn_usd == pytest.approx(68_148_000 / 364 * _Q)
    assert f.flags == ["annual-statements"]
    assert "2025-01-01 → 2025-12-31" in f.note


def test_a_bare_dollar_with_no_companyfacts_is_currency_unread():
    """A bare "$" is no longer USD. QGEN's statements print only "$" (no US$, USD or U.S. dollars): with no
    companyfacts to settle it the currency is UNREAD, flagged, and the fact carries no currency (never a guessed
    "USD"). Before: EUR (a euro mention in a note outranked "$")."""
    f = _run("QGEN", cf=None)
    assert f.statement_currency is None
    assert "currency-unread" in f.flags
    assert "currency unread" in f.note


def test_text_currency_contradicted_by_companyfacts_is_currency_mismatch():
    """When companyfacts reproduces the figures in one currency and the header names another, the reproducing
    unit wins and ``currency-mismatch`` states both (synthetic: JCSE's SGD rows relabeled HKD)."""
    cf = _cf("JCSE")
    for node in cf["facts"]["us-gaap"].values():
        node["units"] = {"HKD": node["units"]["SGD"]}
    f = _run("JCSE", cf=cf)
    assert f.statement_currency == "HKD"
    assert "currency-mismatch" in f.flags
    assert "SGD" in f.note and "HKD" in f.note


# ---------------------------------------------------------------------------------------------------------
# pattern 2: the table
# ---------------------------------------------------------------------------------------------------------


def test_eh_breakdown_table_is_passed_over_for_the_consolidated_statement():
    """EH prints a parent/VIE/subsidiaries breakdown before its statements. Before: USD, cash 257,338,000 (a
    breakdown column) / -179,506,000. After: CNY, cash 256,400,000 from the consolidated balance sheet (the
    one companyfacts reproduces), the breakdown named as passed over."""
    f = _run("EH")
    assert f.statement_currency == "CNY"
    assert f.cash_usd == 256_400_000.0
    assert _ocf(f) == pytest.approx(-179_506_000.0)
    assert f.flags == ["annual-statements"]
    assert "256,400" in _passage(f, "balance-sheet")
    assert "passed over" in f.note


def test_zepp_parent_column_is_passed_over():
    """ZEPP's condensed consolidating schedule comes first and its first column is the parent company's.
    Before: cash 137,000 (a 0.1-month runway). After: 57,046,000 from the consolidated balance sheet.
    """
    f = _run("ZEPP")
    assert f.statement_currency == "USD"
    assert f.cash_usd == 57_046_000.0
    assert _ocf(f) == pytest.approx(-25_692_000.0)
    assert f.flags == ["annual-statements"]
    months = f.cash_usd / (f.quarterly_burn_usd / 3.0)
    assert 26 < months < 27  # was 0.1


def test_tm_segment_balance_sheet_is_passed_over_and_yen_is_read():
    """TM prints a balance sheet by business ("Non-Financial Services Businesses") before the consolidated one.
    Before: currency blank, cash 9,885,097,000,000 (one segment). After: JPY from "Yen in millions", cash
    12,659,622,000,000 from the consolidated statement. companyfacts ends a year earlier, so nothing is
    cross-checked, and the note SAYS so (no data is not a pass)."""
    f = _run("TM", report_date=date(2026, 3, 31))
    assert f.statement_currency == "JPY"
    assert f.cash_usd == 12_659_622_000_000.0
    assert f.quarterly_burn_usd == pytest.approx(-5_472_920_000_000 / 365 * _Q)
    assert "no row on 2026-03-31" in f.note
    assert "agrees" not in f.note


def test_only_a_breakdown_table_is_table_unverified():
    """With only the segment table in the text and no companyfacts, the old first table is kept, flagged
    ``table-unverified`` (never silently trusted)."""
    text = _text("TM")
    seg, _, rest = text.partition("[… trimmed …]")
    cash_flows = rest.split("[… trimmed …]")[-1]
    f = _run(
        "TM",
        text=seg + "\n\n[… trimmed …]\n\n" + cash_flows,
        cf=None,
        report_date=date(2026, 3, 31),
    )
    assert f.cash_usd == 9_885_097_000_000.0
    assert "table-unverified" in f.flags


def test_a_figure_off_by_less_than_one_percent_is_not_a_match():
    """The match is EXACT at the printed precision: EH's breakdown column (257,338 thousand) is within 0.4% of
    companyfacts' 256,400,000, which a 1% tolerance passed. With the consolidated statement cut away, the
    breakdown is all there is: ``table-unverified`` and ``figures-disagree``, both numbers stated.
    """
    text = _text("EH")
    parts = text.split("[… trimmed …]")
    f = _run("EH", text=parts[0] + "[… trimmed …]" + parts[2])
    assert f.cash_usd == 257_338_000.0
    assert "table-unverified" in f.flags and "figures-disagree" in f.flags
    assert "257,338,000" in f.note and "256,400,000" in f.note


def test_a_same_date_mismatch_is_figures_disagree_with_both_numbers():
    """companyfacts has a row on the cash date but no site reproduces it: ``figures-disagree``, both numbers in
    the note (synthetic: EH's companyfacts cash moved by one thousand)."""
    cf = _cf("EH")
    cf["facts"]["us-gaap"]["CashAndCashEquivalentsAtCarryingValue"]["units"]["CNY"][0][
        "val"
    ] = 256_401_000
    f = _run("EH", cf=cf)
    assert f.cash_usd == 256_400_000.0  # the consolidated statement, chosen by heading
    assert "figures-disagree" in f.flags
    assert "256,400,000" in f.note and "256,401,000" in f.note


# ---------------------------------------------------------------------------------------------------------
# pattern 3: the line
# ---------------------------------------------------------------------------------------------------------


def test_qgen_lease_line_is_never_the_operating_cash_flow():
    """QGEN's notes print "Cash paid for operating leases included in cash flows from operating activities
    $ 31,741". Before: EUR, operating cash flow +31,741,000 (the last region's line) and cash empty with no
    flag. After: USD (companyfacts), operating cash flow 654,326,000 from the statement, cash 839,005,000.
    """
    f = _run("QGEN")
    assert f.statement_currency == "USD"
    assert f.cash_usd == 839_005_000.0
    assert _ocf(f) == pytest.approx(654_326_000.0)
    assert f.flags == ["annual-statements"]
    assert "31,741" not in _passage(f, "cash-flow")


def test_lease_line_rejected_even_without_companyfacts():
    """The guard is textual, not a companyfacts rescue: with no companyfacts the statement total still wins."""
    f = _run("QGEN", cf=None)
    assert -f.quarterly_burn_usd / _Q * 365 == pytest.approx(654_326_000.0)


def test_a_located_cash_row_that_cannot_be_read_is_flagged():
    """Before, an unreadable cash row added nothing (QGEN: cash empty, no flag of its own). With QGEN's balance
    sheet cut away only the MD&A row is left (it reads as a negative balance): ``cash-unread``."""
    text = _text("QGEN")
    parts = text.split("[… trimmed …]")
    f = _run("QGEN", text="[… trimmed …]".join([parts[0], parts[2], parts[3]]), cf=None)
    assert f.cash_usd is None
    assert "cash-unread" in f.flags


# ---------------------------------------------------------------------------------------------------------
# pattern 4: short-term deposits (D1: cash means cash plus short-term investments)
# ---------------------------------------------------------------------------------------------------------


def test_blrx_short_term_bank_deposits_are_counted_and_stated():
    """BLRX: cash 3,250K plus short-term bank deposits 17,600K. Before: 3,250,000 (a 4.8-month runway). After:
    20,850,000 with the deposits carried on their own and the parts in the note."""
    f = _run("BLRX")
    assert f.statement_currency == "USD"
    assert f.cash_usd == 20_850_000.0
    assert f.investments_usd == 17_600_000.0
    assert f.flags == ["annual-statements"]
    assert "3,250,000" in f.note and "17,600,000" in f.note
    assert any(
        p.kind == "balance-sheet" and "Short-term bank deposits" in p.anchor
        for p in f.located_passages
    )


def test_blrx_without_companyfacts_states_that_nothing_was_cross_checked():
    """No companyfacts is its own stated condition, not a pass: the currency comes from the header's "USD",
    and the note says neither figure was cross-checked."""
    f = _run("BLRX", cf=None)
    assert f.statement_currency == "USD"
    assert f.cash_usd == 20_850_000.0
    assert "not cross-checked" in f.note
    assert "agrees" not in f.note


def test_plsm_shekel_column_kept_and_deposits_counted():
    """PLSM (the audit's right column pick, kept): the shekel column over the dollar column, ILS. Cash now adds
    short-term deposits: 21,604,000 + 47,500,000. Before: 21,604,000."""
    f = _run("PLSM")
    assert f.statement_currency == "ILS"
    assert f.cash_usd == 69_104_000.0
    assert f.investments_usd == 47_500_000.0
    assert _ocf(f) == pytest.approx(-32_876_000.0)
    assert f.flags == ["annual-statements"]


# ---------------------------------------------------------------------------------------------------------
# controls and the structural bound
# ---------------------------------------------------------------------------------------------------------


def test_sny_euro_control_is_unchanged():
    """SNY (right before, must not move): EUR, cash 7,657,000,000, operating cash flow +10,750,000,000."""
    f = _run("SNY")
    assert f.statement_currency == "EUR"
    assert f.cash_usd == 7_657_000_000.0
    assert _ocf(f) == pytest.approx(10_750_000_000.0)
    assert f.investments_usd is None
    assert f.flags == ["annual-statements"]


@pytest.mark.parametrize(
    "name,form,rd",
    [
        ("NTES", "20-F", _FY2025),
        ("JCSE", "20-F", _FY2025),
        ("DNN", "40-F", _FY2025),
        ("EH", "20-F", _FY2025),
        ("ZEPP", "20-F", _FY2025),
        ("TM", "20-F", date(2026, 3, 31)),
        ("QGEN", "20-F", _FY2025),
        ("BLRX", "20-F", _FY2025),
        ("PLSM", "20-F", _FY2025),
        ("SNY", "20-F", _FY2025),
    ],
)
def test_every_case_is_a_flag_whose_note_says_currency_table_and_cross_check(name, form, rd):
    """The note the steward reads names the currency and how it was determined, the table each figure came
    from, and the cross-check result, for every case; and every candidate stays a FLAG."""
    f = _run(name, form=form, report_date=rd)
    assert re.search(r"Currency: ", f.note)
    assert "Table: " in f.note
    assert "Cross-check: " in f.note
