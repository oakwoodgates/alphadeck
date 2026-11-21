"""Annual statements: the deposits/investments addition, closing cash, the year column and currency prose.

Built against the 2026-11-16 re-audit of the annual cash candidates (docs/temp/v2-extractor-audits-2026-11-15/
reaudit-annual-and-shares-2026-11-16.md), which found four candidates still WRONG AND UNFLAGGED after PR 4 (QGEN,
CGNT, WAVE, ARQQ), three of them caused by PR 4's short-term deposits rule, plus one false ``figures-disagree``
(NTES). Each case below runs on a fixture RECONSTRUCTED from the values and row wording the re-audit and the stored
candidates recorded (``fixtures/sec_extractor/annual/audit-2026-11-16/README.md`` says field by field what is
recorded and what is filler). Every fixture reproduces, under the extractor before this change, the exact stored
candidate; the docstrings name it.

The rules (docs/WORKBENCH_EXTRACTION.md, "The annual-statements runway path"): a deposits or investments row is
added ONLY from the balance-sheet statement the cash row is on, in the same year column, as its own line; anything
else is seen, quoted and NOT counted (``investments-unconfirmed``). Cash is a closing balance, never a flow line.
The year column comes from the table's own header. An explicit presentation-currency statement settles the
currency. Every candidate stays a FLAG. OFFLINE: no network, no database.
"""

from __future__ import annotations

import json
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
    / "audit-2026-11-16"
)
_TODAY = date(2026, 11, 16)  # the re-audit date: staleness in the notes is deterministic
_FY2025 = date(2025, 12, 31)
_Q = 365.25 / 4  # the meter's quarter
_TRIM = "[… trimmed …]"
_UNCONFIRMED = "investments-unconfirmed"


def _text(name: str) -> str:
    return (_FX / f"{name}-20f-fin.txt").read_text(encoding="utf-8")


def _cf(name: str) -> dict:
    return json.loads((_FX / f"cf-{name}.json").read_text(encoding="utf-8"))


def _run(
    name: str,
    *,
    text: str | None = None,
    cf: dict | None | str = "fixture",
    report_date: date = _FY2025,
):
    facts, reason = extract_annual_runway(
        _cf(name) if cf == "fixture" else cf,
        _text(name) if text is None else text,
        annual_ref=f"https://sec.gov/{name}",
        annual_form="20-F",
        report_date=report_date,
        today=_TODAY,
    )
    assert reason is None and len(facts) == 1, (name, reason)
    f = facts[0]
    assert f.tier is Tier.FLAG and f.source == "annual-statements"  # ALWAYS a flag
    return f


def _ocf(f, span: int = 364) -> float:
    """The operating cash flow the quarterly burn was normalized from."""
    return -f.quarterly_burn_usd / _Q * span


def _anchors(f, kind: str = "balance-sheet") -> list[str]:
    return [p.anchor for p in f.located_passages if p.kind == kind]


# ---------------------------------------------------------------------------------------------------------
# (A) the deposits / investments addition: only from the same balance-sheet statement
# ---------------------------------------------------------------------------------------------------------


def test_qgen_balance_sheet_wins_over_the_cash_note_and_its_own_investments_row_is_added():
    """QGEN. Before: 893,658,000 = 839,005,000 + 54,653,000, the cash row taken from the cash-composition note
    (Note 3), whose "Short-term bank deposits 54,653" is a component ALREADY INSIDE cash 839,005 (double
    counted), while the balance sheet's own "Short-term investments 259,913" was missed. After: the balance
    sheet's cash row (it agrees with companyfacts) wins, and its own current-year investments row is added:
    839,005,000 + 259,913,000 = 1,098,918,000. Nothing from the note is added."""
    f = _run("QGEN")
    assert f.statement_currency == "USD"
    assert f.cash_usd == 1_098_918_000.0
    assert f.investments_usd == 259_913_000.0
    assert _ocf(f) == pytest.approx(654_326_000.0)
    assert f.flags == ["annual-statements"]
    assert f.note.split("Table: ")[1].startswith("cash from “BALANCE SHEETS (in thousands")
    assert "short-term investments 259,913,000" in f.note
    assert "54,653" not in f.note
    assert any("Short-term investments" in a for a in _anchors(f))


def test_qgen_cash_found_only_in_a_note_adds_nothing_and_says_why():
    """With QGEN's balance sheet cut away, the only cash row is the note's total: the cash stays 839,005,000 (it
    agrees with companyfacts), the note's deposits row is NOT added (it may already be inside cash), and the
    flag plus the note quote the row that was seen and not counted."""
    parts = _text("QGEN").split(_TRIM)
    f = _run("QGEN", text=_TRIM.join(parts[1:]))
    assert f.cash_usd == 839_005_000.0
    assert f.investments_usd is None
    assert _UNCONFIRMED in f.flags
    assert "Short-term bank deposits" in f.note and "54,653" in f.note
    assert "NOT counted" in f.note
    assert "note or summary" in f.note


def test_cgnt_prior_year_summary_line_is_never_added():
    """CGNT. Before: 229,978,000 = 116,878,000 + 113,100,000, where 113,100 is LAST YEAR's total of cash,
    restricted cash and time deposits from an MD&A summary line (its 2026 cell is a dash), not a current
    investment. After: the balance sheet's cash row wins (it agrees with companyfacts) and the balance sheet
    has no investments row: 116,878,000."""
    f = _run("CGNT", report_date=date(2026, 1, 31))
    assert f.statement_currency == "USD"
    assert f.cash_usd == 116_878_000.0
    assert f.investments_usd is None
    assert _ocf(f) == pytest.approx(40_331_000.0)
    assert f.flags == ["annual-statements"]
    assert "113,100" not in f.note


def test_cgnt_summary_line_beside_an_mdna_cash_row_is_seen_and_not_counted():
    """With CGNT's financial statements cut away, the cash comes from the MD&A table (it agrees with
    companyfacts), and the summary line beside it is quoted and NOT counted."""
    f = _run("CGNT", text=_text("CGNT").split(_TRIM)[0], report_date=date(2026, 1, 31))
    assert f.cash_usd == 116_878_000.0
    assert f.investments_usd is None
    assert _UNCONFIRMED in f.flags
    assert "time deposits" in f.note and "NOT counted" in f.note


def test_wave_note_reference_is_not_a_value_and_the_newest_column_is_read():
    """WAVE (Eco Wave Power). Before: cash 6,027,000 = 6,022,000 + 5,000, where 5 is the deposits row's NOTE
    REFERENCE (the row reads: note 5, 2025 a dash, 2024 1,254), and operating cash flow -2,601,000, the 2023
    column (columns 2025 / 2024 / 2023: (3,040) / (1,817) / (2,601); stray years in a row label above the
    total moved the old column map). After: cash 6,022,000, operating cash flow -3,040,000 (the table's own
    header decides the column), and the note says the deposits row's 2025 cell is a dash."""
    f = _run("WAVE")
    assert f.statement_currency == "USD"
    assert f.cash_usd == 6_022_000.0
    assert f.investments_usd is None
    assert _ocf(f, 365) == pytest.approx(-3_040_000.0)
    assert f.flags == ["annual-statements"]
    assert "dash" in f.note and "Short-term bank deposits" in f.note


def test_a_blank_current_cell_beside_a_note_reference_is_not_counted():
    """The same row with the dash missing ("5 1,254") cannot tell the note reference from a value: the 5 is
    never read as 5,000; the row is quoted and NOT counted, flagged."""
    text = _text("WAVE").replace(
        "Short-term bank deposits 5 \u2014 1,254", "Short-term bank deposits 5 1,254"
    )
    assert "Short-term bank deposits 5 1,254" in text
    f = _run("WAVE", text=text)
    assert f.cash_usd == 6_022_000.0
    assert f.investments_usd is None
    assert _UNCONFIRMED in f.flags
    assert "note reference" in f.note


def test_blrx_and_plsm_separate_balance_sheet_rows_are_still_added():
    """The controls: BLRX's and PLSM's short-term deposits ARE their own balance-sheet rows in the current-year
    column, so they are still added (the 2026-11-15 fixtures, unchanged)."""
    old = _FX.parent / "audit-2026-11-15"

    def run(name: str):
        facts, _ = extract_annual_runway(
            json.loads((old / f"cf-{name}.json").read_text(encoding="utf-8")),
            next(old.glob(f"{name}-*fin.txt")).read_text(encoding="utf-8"),
            annual_ref="x",
            annual_form="20-F",
            report_date=_FY2025,
            today=_TODAY,
        )
        return facts[0]

    blrx, plsm = run("BLRX"), run("PLSM")
    assert (blrx.cash_usd, blrx.investments_usd) == (20_850_000.0, 17_600_000.0)
    assert (plsm.cash_usd, plsm.investments_usd) == (69_104_000.0, 47_500_000.0)
    assert blrx.flags == plsm.flags == ["annual-statements"]


def _statements(bs_header: str, bs_rows: str, cf_header: str, cf_rows: str, notes: str = "") -> str:
    text = (
        f"F-3 EXAMPLE LTD. CONSOLIDATED BALANCE SHEETS {bs_header} Current assets: {bs_rows} "
        f"Total current assets 13,000 10,900\n\n{_TRIM}\n\n"
        f"F-6 EXAMPLE LTD. CONSOLIDATED STATEMENTS OF CASH FLOWS {cf_header} Cash flows from operating "
        f"activities: Loss for the year ( 3,500 ) ( 2,500 ) {cf_rows} Cash flows from investing activities: "
        "Purchases of equipment ( 100 ) ( 90 )"
    )
    return text + (f"\n\n{_TRIM}\n\n{notes}" if notes else "")


def test_a_component_listed_under_cash_is_not_counted():
    """An "of which" line under the cash row is a component already inside cash: quoted, NOT counted."""
    text = _statements(
        "(U.S. dollars in thousands) December 31, 2025 2024",
        "Cash and cash equivalents 5,000 4,000 of which short-term deposits 1,000 900 Other receivables 1,000 900",
        "(U.S. dollars in thousands) Year ended December 31, 2025 2024",
        "Net cash used in operating activities ( 3,000 ) ( 2,000 )",
    )
    f = _run("SYN", text=text, cf=None)
    assert f.cash_usd == 5_000_000.0
    assert f.investments_usd is None
    assert _UNCONFIRMED in f.flags


# ---------------------------------------------------------------------------------------------------------
# (B) closing balance, never a flow
# ---------------------------------------------------------------------------------------------------------


def test_arqq_closing_balance_not_the_net_increase_and_currency_from_the_filing():
    """ARQQ (Arqit). Before: cash 18,264,000, the "Net increase/(decrease) in cash and cash equivalents" line,
    and ``currency-unread`` although the filing says it is presented in United States Dollars (USD 000). After:
    the closing balance "Cash and cash equivalents at end of period" 36,978,000 (no balance-sheet cash row is
    located), USD from the filing's own statement, operating cash flow -29,553,000."""
    f = _run("ARQQ", report_date=date(2025, 9, 30))
    assert f.statement_currency == "USD"
    assert f.cash_usd == 36_978_000.0
    assert _ocf(f, 365) == pytest.approx(-29_553_000.0)
    assert f.flags == ["annual-statements"]
    assert any("at end of period" in a for a in _anchors(f))
    assert "presented in United States Dollars" in f.note
    assert "closing balance" in f.note


def test_a_flow_line_is_never_the_cash_balance():
    """With ARQQ's closing row cut away only flow lines are left (net increase, opening balance): the cash is
    never valued from them — ``cash-unread``, never 18,264,000."""
    text = _text("ARQQ").replace(
        " Cash and cash equivalents at end of period 36,978 18,705 44,455", ""
    )
    f = _run("ARQQ", text=text, report_date=date(2025, 9, 30))
    assert f.cash_usd is None
    assert "cash-unread" in f.flags
    assert "18,264,000" not in f.note


# ---------------------------------------------------------------------------------------------------------
# (C) the year column: from the table's own header, either order
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "years,cash,deposits,ocf",
    [
        ("2025 2024", "5,000 4,000", "7,000 6,000", "( 3,000 ) ( 2,000 )"),
        ("2024 2025", "4,000 5,000", "6,000 7,000", "( 2,000 ) ( 3,000 )"),
    ],
    ids=["newest-first", "newest-last"],
)
def test_cash_deposits_and_operating_cash_flow_share_the_newest_column(years, cash, deposits, ocf):
    """Cash, the deposits part and the operating cash flow all come from the 2025 column, whichever side of the
    table it is printed on."""
    text = _statements(
        f"(U.S. dollars in thousands) December 31, {years}",
        f"Cash and cash equivalents {cash} Short-term deposits {deposits} Other receivables 1,000 900",
        f"(U.S. dollars in thousands) Year ended December 31, {years}",
        f"Share-based payment (2024 and 2025 plans) 260 230 Net cash used in operating activities {ocf}",
    )
    f = _run("SYN", text=text, cf=None)
    assert f.cash_usd == 12_000_000.0
    assert f.investments_usd == 7_000_000.0
    assert _ocf(f, 365) == pytest.approx(-3_000_000.0)


# ---------------------------------------------------------------------------------------------------------
# (D) currency prose
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "header,notes,currency",
    [
        ("($'000)", "The financial statements are presented in United States Dollars.", "USD"),
        ("($'000)", "These financial statements are expressed in U.S. dollars.", "USD"),
        ("($'000)", "All values are rounded to the nearest thousand (USD 000).", "USD"),
        (
            "($'000)",
            "Amounts are shown (in thousands of US dollars) unless stated otherwise.",
            "USD",
        ),
        ("(Figures in million - SA rand)", "", "ZAR"),
        ("($000s)", "", None),
        (
            "($'000)",
            "The statements are presented in United States Dollars. The parent's are expressed in euros.",
            None,
        ),
    ],
    ids=[
        "presented-in",
        "expressed-in",
        "usd-000",
        "thousands-of-us-dollars",
        "sa-rand",
        "bare",
        "conflict",
    ],
)
def test_explicit_currency_statements_are_read_and_a_bare_dollar_is_not(header, notes, currency):
    """An explicit statement ("presented in United States Dollars", "expressed in U.S. dollars", "USD 000",
    "(in thousands of US dollars)", "SA rand") settles the currency; a bare "$" or "$000s" alone does not, and
    two statements naming different currencies settle nothing."""
    text = _statements(
        f"{header} December 31, 2025 2024",
        "Cash and cash equivalents 5,000 4,000 Other receivables 1,000 900",
        f"{header} Year ended December 31, 2025 2024",
        "Net cash used in operating activities ( 3,000 ) ( 2,000 )",
        notes,
    )
    f = _run("SYN", text=text, cf=None)
    assert f.statement_currency == currency
    assert ("currency-unread" in f.flags) is (currency is None)


# ---------------------------------------------------------------------------------------------------------
# (E) figures-disagree only on a same-date, same-concept mismatch
# ---------------------------------------------------------------------------------------------------------


def test_ntes_time_deposits_are_not_compared_with_the_investments_line():
    """NTES. Before: ``figures-disagree`` on "Time deposits" 92,639,378 (RMB thousands), compared against
    companyfacts' ShortTermInvestments 22,803,503, which is the SEPARATE "Short-term investments" row of the same
    balance sheet (and agrees with it exactly). After: no flag; the time deposits are stated as not
    cross-checked, the investments as agreeing; cash 162,610,785,000 unchanged."""
    f = _run("NTES")
    assert f.statement_currency == "CNY"
    assert f.cash_usd == 162_610_785_000.0
    assert f.investments_usd == 92_639_378_000.0 + 22_803_503_000.0
    assert f.flags == ["annual-statements"]
    assert "FIGURES DISAGREE" not in f.note
    assert "“Time deposits” (us-gaap or ifrs-full, any currency): not cross-checked" in f.note
    assert "companyfacts agrees with the statement's “Short-term investments”" in f.note


def test_ntes_a_real_same_concept_mismatch_still_flags():
    """A real mismatch still flags: companyfacts' ShortTermInvestments moved by one thousand reproduces no row,
    so ``figures-disagree`` fires with both numbers (EH's cash mismatch is the 2026-11-15 suite's own test).
    """
    cf = _cf("NTES")
    cf["facts"]["us-gaap"]["ShortTermInvestments"]["units"]["CNY"][0]["val"] = 22_803_504_000
    f = _run("NTES", cf=cf)
    assert "figures-disagree" in f.flags
    assert "22,803,503,000" in f.note and "22,803,504,000" in f.note


# ---------------------------------------------------------------------------------------------------------
# every case: a FLAG whose note says what was added and from which row
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name,rd",
    [
        ("QGEN", _FY2025),
        ("CGNT", date(2026, 1, 31)),
        ("WAVE", _FY2025),
        ("ARQQ", date(2025, 9, 30)),
        ("NTES", _FY2025),
    ],
)
def test_every_case_is_a_flag_whose_note_names_currency_table_and_cross_check(name, rd):
    f = _run(name, report_date=rd)
    assert "Currency: " in f.note and "Table: " in f.note and "Cross-check: " in f.note
    if f.investments_usd is not None:
        assert "count as cash" in f.note  # the parts are named, each from its own row
