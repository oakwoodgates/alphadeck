"""Annual statements: the third re-audit's regressions (docs/temp/v2-extractor-audits-2026-11-15/
reaudit3-annual-cash-2026-11-16.md).

- VNET: statements in RMB thousands with a US$ convenience column. Cash 5,523,571; a short-term investments
  row whose RMB value is 379,198. The stored investment part 54,225,000 is the CONVENIENCE column's number
  labeled CNY (5,577,796,000 = 5,523,571,000 + 54,225,000), and the note accepted a USD companyfacts match for
  that CNY component, so the row lost its last numerical warning (WRONG AND UNFLAGGED).
- ALC: USD millions; cash 1,527 and a separate "Time deposits" row printing its note reference 17 and then 80
  (2025) and 153 (2024). The deposits guard dropped the 80 claiming the current-year cell was blank (right total
  1,607,000,000): its blank-cell test compared the row's three figures with the count of YEAR TOKENS in the
  header, which the heading's own dates inflate ("at December 31, 2025 and 2024 ... Note 2025 2024").

The rules (docs/WORKBENCH_EXTRACTION.md, "The annual-statements runway path"): the deposits/investments part is
read from the SAME currency column as the cash value, never the convenience translation; a deposits row that
prints fewer figures than the cash row cannot be placed on that grid and is seen, quoted and NOT counted
(``investments-unconfirmed``). A companyfacts row that reproduces a deposits/investments figure only in a
DIFFERENT unit than the statement currency is not agreement: ``figures-disagree``, both numbers and units in the
note. Every candidate stays a FLAG. The fixtures are RECONSTRUCTED (``fixtures/sec_extractor/annual/
reaudit3-2026-11-16/README.md`` says field by field what is recorded and what is filler). OFFLINE: no network,
no database.
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
    / "reaudit3-2026-11-16"
)
_TODAY = date(2026, 11, 16)  # the re-audit date: staleness in the notes is deterministic
_FY2025 = date(2025, 12, 31)
_UNCONFIRMED = "investments-unconfirmed"
_VNET_STI = "Short-term investments 379,198 54,225"


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


# ---------------------------------------------------------------------------------------------------------
# VNET: the investment part comes from the cash value's currency column, never the convenience column
# ---------------------------------------------------------------------------------------------------------


def test_vnet_convenience_column_figure_is_never_the_investment_part():
    """VNET. Before: cash 5,577,796,000 = 5,523,571,000 + 54,225,000, the 54,225 read from the US$
    convenience column (the row prints two figures, 379,198 and 54,225, under a three-column header, and the
    two-figure row was mapped onto the two year headers), unflagged. After: the row cannot be placed on the
    cash row's column grid, so it is quoted and NOT counted: cash 5,523,571,000, flagged."""
    f = _run("VNET")
    assert f.statement_currency == "CNY"
    assert f.cash_usd == 5_523_571_000.0  # was 5,577,796,000
    assert f.investments_usd is None  # was 54,225,000 (the US$ convenience column, labeled CNY)
    assert f.flags == ["annual-statements", _UNCONFIRMED]  # was ["annual-statements"]
    assert _VNET_STI in f.note and "NOT counted" in f.note
    assert "convenience" in f.note.split("INVESTMENTS UNCONFIRMED")[1]
    assert "companyfacts agrees with the statement's “Short-term investments”" not in f.note
    assert (
        f.quarterly_burn_usd is not None and f.quarterly_burn_usd < 0
    )  # OCF untouched: generative


def test_vnet_row_printing_every_column_counts_the_rmb_figure():
    """CONTROL (passes before and after): the same row with its 2024 cell printed reads 379,198 from the RMB
    2025 column: cash 5,902,769,000, the audit's correct total; companyfacts' USD 54,225,000 reproduces
    nothing, so ``figures-disagree`` stays."""
    text = _text("VNET").replace(_VNET_STI, "Short-term investments 250,000 379,198 54,225")
    f = _run("VNET", text=text)
    assert f.cash_usd == 5_902_769_000.0
    assert f.investments_usd == 379_198_000.0
    assert "figures-disagree" in f.flags and _UNCONFIRMED not in f.flags


def _cny_statements(sti_row: str) -> str:
    return (
        "F-3 EXAMPLE LTD. CONSOLIDATED BALANCE SHEETS (in thousands of RMB) December 31, 2024 2025 "
        f"Current assets: Cash and cash equivalents 4,000,000 5,000,000 {sti_row} "
        "Other receivables 1,000 900 Total current assets 4,500,000 5,500,000\n\n[… trimmed …]\n\n"
        "F-6 EXAMPLE LTD. CONSOLIDATED STATEMENTS OF CASH FLOWS (in thousands of RMB) Year ended "
        "December 31, 2024 2025 Cash flows from operating activities: Loss for the year ( 3,500 ) ( 2,500 ) "
        "Net cash used in operating activities ( 3,000 ) ( 2,000 ) Cash flows from investing activities: "
        "Purchases of equipment ( 100 ) ( 90 )"
    )


def _cf_sti_usd(val: float) -> dict:
    row = {
        "end": "2025-12-31",
        "val": val,
        "accn": "synthetic",
        "form": "20-F",
        "filed": "2026-04-01",
    }
    return {"facts": {"us-gaap": {"ShortTermInvestments": {"units": {"USD": [row]}}}}}


def test_a_match_only_in_another_currency_unit_is_not_agreement():
    """A CNY statement's investments row (54,225 RMB thousands) is reproduced by companyfacts only in USD
    (54,225,000 USD): that is not agreement for a CNY figure (VNET's stored note accepted exactly this). It is
    ``figures-disagree`` with both numbers and both units in the note."""
    f = _run(
        "SYN",
        text=_cny_statements("Short-term investments 40,000 54,225"),
        cf=_cf_sti_usd(54_225_000),
    )
    assert f.statement_currency == "CNY"
    assert f.investments_usd == 54_225_000.0  # counted from its own RMB column, as before
    assert "figures-disagree" in f.flags  # was absent: the USD row was taken as agreement
    assert "companyfacts agrees with the statement's “Short-term investments”" not in f.note
    assert "statement 54,225,000 CNY" in f.note
    assert "54,225,000 (us-gaap ShortTermInvestments, USD)" in f.note


def test_a_match_in_the_statement_currency_still_agrees():
    """CONTROL: the same figure reproduced in CNY is agreement (no flag)."""
    cf = _cf_sti_usd(54_225_000)
    units = cf["facts"]["us-gaap"]["ShortTermInvestments"]["units"]
    units["CNY"] = units.pop("USD")
    f = _run("SYN", text=_cny_statements("Short-term investments 40,000 54,225"), cf=cf)
    assert "figures-disagree" not in f.flags
    assert "companyfacts agrees with the statement's “Short-term investments”" in f.note


# ---------------------------------------------------------------------------------------------------------
# ALC: a note reference followed by one figure per column is read; WAVE's dash and a blank stay empty
# ---------------------------------------------------------------------------------------------------------

_ALC_TD = "Time deposits 17 80 153"


def test_alc_note_reference_then_two_values_reads_the_current_year():
    """ALC. Before: cash 1,527,000,000, the time deposits dropped with a false "current-year cell is blank"
    reason and ``investments-unconfirmed``. After: the row prints its note reference and then one figure per
    column of the cash row, so 80 is read in the cash value's column: 1,607,000,000, the audit's total.
    """
    f = _run("ALC")
    assert f.statement_currency == "USD"
    assert f.cash_usd == 1_607_000_000.0  # was 1,527,000,000
    assert f.investments_usd == 80_000_000.0  # was None
    assert f.flags == ["annual-statements"]  # was ["annual-statements", "investments-unconfirmed"]
    assert "time deposits 80,000,000 (from the balance sheet's “Time deposits” row)" in f.note
    assert "blank" not in f.note


@pytest.mark.parametrize(
    "row,cash,investments,unconfirmed,said",
    [
        (_ALC_TD, 1_607_000_000.0, 80_000_000.0, False, "time deposits 80,000,000"),
        ("Time deposits 17 \u2014 153", 1_527_000_000.0, None, False, "dash"),
        ("Time deposits 17 153", 1_527_000_000.0, None, True, "note reference"),
    ],
    ids=["note-value-value", "note-dash-value (WAVE's shape)", "note-value (blank)"],
)
def test_note_reference_shapes_side_by_side(row, cash, investments, unconfirmed, said):
    """In the same note-column table: "17 80 153" reads 80; "17 — 153" (WAVE's shape) is an empty current
    cell, nothing added and no flag; "17 153" cannot tell a blank cell from a value, so it is quoted and NOT
    counted (the 17 is never a value)."""
    f = _run("ALC", text=_text("ALC").replace(_ALC_TD, row))
    assert f.cash_usd == cash
    assert f.investments_usd == investments
    assert (_UNCONFIRMED in f.flags) is unconfirmed
    assert said in f.note
    assert "17,000,000" not in f.note
