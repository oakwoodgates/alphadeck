"""Unsplit debt-security TOTALS and the missing long-term marketable tag (the 2026-11-16 quarterly cash re-audit).

The re-audit of the auto-applied quarterly cash found investments still left out in a few shapes:

- GKOS keeps its 174.4M of short-term investments ONLY under ``AvailableForSaleSecuritiesDebtSecurities``, a
  TOTAL with no ``Current``/``Noncurrent`` suffix, which the reader did not know: it stored 162,327,000.
- CRUS reports 324.4M of long-term marketable securities under
  ``DebtSecuritiesAvailableForSaleExcludingAccruedInterestNoncurrent``, the noncurrent sibling of a short-term
  tag the reader already knew but missing from the long-term list: it stored 752,047,000.

An unsplit total is not a plain addend. It may restate the current and noncurrent parts already read (adding it
would double count), and the same filers often tag securities classed as CASH EQUIVALENTS into it (commercial
paper under 90 days: GM's and NTSK's totals hold amounts already inside cash), or restricted holdings (NHC). The
rule: beside its own current/noncurrent parts the total is only a CHECK (parts above the total contradict it ->
``investments-unreconciled``; a total above the parts leaves the excess uncounted and named); beside other
investment tags it is named and not counted; ALONE it is counted (AUTO) only when the same-dated combined
cash-and-short-term-investments line confirms cash + it. Otherwise (the third re-audit, operator decision) it is
QUOTED, NOT ADDED: of 17 such offers 10 were overstated (NVDA's 46.9bn total already includes cash equivalents;
EHC's, NHC's and TPC's securities are restricted), and a steward's approve stores the offered number, so the
offer is the conservative one: cash and equivalents (plus any suffixed parts), a FLAG
(``investments-unreconciled``), and the note asks the steward to add the eligible part from the balance sheet.

``GKOS.json`` and ``CRUS.json`` in ``tests/fixtures/edgar/cash_composition/`` are RECONSTRUCTED from the re-audit
(``docs/temp/v2-extractor-audits-2026-11-15/reaudit-quarterly-cash-2026-11-16.md``): GKOS's cash, its two
``AvailableForSaleSecuritiesDebtSecurities`` values (2026-06-30 171,741,000 and 2026-09-30 174,372,000) and its
operating cash flow (40,867,000 less 2,297,000) are the re-audit's; CRUS's 324,387,000 is the re-audit's, and its
stored 752,047,000 is put on the cash tag alone because the real cash/short-term split was not recorded. What is
not recorded is synthetic and marked ``"accn": "reconstructed"``: every accession, filed date and fiscal label;
CRUS's balance-sheet date and operating cash flow. Fed through the old reader each reproduces the stored value
(GKOS 162,327,000; CRUS 752,047,000). The rule of the second re-audit offered GKOS 336,699,000.

``NVDA.json`` is RECONSTRUCTED from the third re-audit
(``docs/temp/v2-extractor-audits-2026-11-15/reaudit3-quarterly-cash-2026-11-16.md``): cash and equivalents
22,443,000,000 and the 46,900,000,000 debt total on the balance sheet of the cited 10-Q (nvda-20260726) are the
re-audit's; the total's tag name (the re-audit says only "debt-total tag"), the operating cash flow, accession,
filed date and fiscal label are synthetic (``"accn": "reconstructed"``). The second re-audit's rule offered
69,343,000,000 (a 12,757,000,000 double count).

Display-only: the cash reading feeds the runway meter and the platform value, never a call.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from domain.config import ExtractorConfig
from domain.extraction import Tier
from ingest.edgar import extract as ex

_FX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "cash_composition"
_COMBINED = "CashCashEquivalentsAndShortTermInvestments"
_AFS = "AvailableForSaleSecuritiesDebtSecurities"
_AFS_EX = "DebtSecuritiesAvailableForSaleExcludingAccruedInterest"


def _cb(ticker: str, period: date):
    facts = json.loads((_FX / f"{ticker}.json").read_text(encoding="utf-8"))["facts"]
    return ex._cash_burn(facts, "", "10-Q", period, ExtractorConfig())


# ---------------------------------------------------------------------------------------------------------
# the re-audit's cases
# ---------------------------------------------------------------------------------------------------------


def test_gkos_quotes_its_lone_unsplit_debt_total_and_offers_cash_only():
    """GKOS: only the unsplit total is tagged on the cash date and no combined line confirms it. It is QUOTED,
    not added: the offer is cash and equivalents (the conservative number a steward's approve would store), a
    FLAG, and the note names the total and asks the steward to add the eligible part (here the re-audit found
    all 174.4M eligible: the steward corrects to 336,699,000).
    """
    cb = _cb("GKOS", date(2026, 9, 30))
    assert cb.cash_usd == 162_327_000  # was 336,699,000 (162,327,000 + 174,372,000)
    assert cb.investments_usd is None  # was 174,372,000: nothing counted
    assert cb.tier is Tier.FLAG and cb.flags == ["investments-unreconciled"]
    assert cb.quarterly_burn_usd == -(40_867_000 - 2_297_000)  # the burn is untouched
    assert f"a debt-securities total of $174.4M ({_AFS}) was reported" in cb.note
    assert "a total not split into current and noncurrent" in cb.note
    assert "not counted" in cb.note
    assert "already inside cash equivalents or restricted" in cb.note
    assert "add the eligible part from the balance sheet" in cb.note
    assert "no short-term investments tagged on the cash date" not in cb.note


def test_nvda_does_not_double_count_a_debt_total_that_holds_cash_equivalents():
    """NVDA: the 46.9bn total already includes cash equivalents; the old offer (69,343,000,000) double counted
    12,757,000,000. Now the offer is cash and equivalents only, the total quoted."""
    cb = _cb("NVDA", date(2026, 7, 26))
    assert cb.cash_usd == 22_443_000_000  # was 69,343,000,000
    assert cb.investments_usd is None  # was 46,900,000,000
    assert cb.tier is Tier.FLAG and cb.flags == ["investments-unreconciled"]
    assert f"a debt-securities total of $46900.0M ({_AFS}) was reported" in cb.note
    assert "add the eligible part from the balance sheet" in cb.note


def test_crus_counts_long_term_marketable_securities_under_the_ex_accrued_interest_noncurrent_tag():
    cb = _cb("CRUS", date(2026, 9, 27))
    assert cb.cash_usd == 752_047_000 + 324_387_000  # old reading: 752,047,000
    assert cb.investments_usd == 324_387_000
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert (
        "long-term marketable securities $324.4M "
        "(DebtSecuritiesAvailableForSaleExcludingAccruedInterestNoncurrent)" in cb.note
    )


# ---------------------------------------------------------------------------------------------------------
# the rule, on synthetic companyfacts
# ---------------------------------------------------------------------------------------------------------

_D = "2026-03-31"
_PERIOD = date(2026, 3, 31)


def _inst(end: str, val: float) -> dict:
    return {"end": end, "val": val, "filed": end}


def _run(cfg: ExtractorConfig | None = None, **nodes: list[dict]):
    g = {c: {"units": {"USD": rows}} for c, rows in nodes.items()}
    g["NetCashProvidedByUsedInOperatingActivities"] = {
        "units": {"USD": [{"start": "2026-01-01", "end": _D, "val": -5e6}]}
    }
    return ex._cash_burn({"us-gaap": g}, "", "10-Q", _PERIOD, cfg or ExtractorConfig())


def test_a_total_that_agrees_with_its_parts_is_a_check_never_added():
    """Suffixed parts AND the unsplit total on the cash date, agreeing: the parts count (as today), the total is
    stated as reconciling, never a third addend."""
    cb = _run(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
        AvailableForSaleSecuritiesDebtSecuritiesCurrent=[_inst(_D, 50e6)],
        AvailableForSaleSecuritiesDebtSecuritiesNoncurrent=[_inst(_D, 30e6)],
        **{_AFS: [_inst(_D, 80e6)]},
    )
    assert cb.cash_usd == 180e6 and cb.investments_usd == 80e6
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert f"{_AFS} $80.0M reconciles to its current + noncurrent parts $80.0M" in cb.note


def test_a_total_below_its_parts_is_a_flag():
    """The parts (50M + 30M) exceed the total that must contain them (60M): the composition is contradicted.
    The parts are offered as read, never trimmed to the total."""
    cb = _run(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
        AvailableForSaleSecuritiesDebtSecuritiesCurrent=[_inst(_D, 50e6)],
        AvailableForSaleSecuritiesDebtSecuritiesNoncurrent=[_inst(_D, 30e6)],
        **{_AFS: [_inst(_D, 60e6)]},
    )
    assert cb.tier is Tier.FLAG and cb.flags == ["investments-unreconciled"]
    assert cb.cash_usd == 180e6 and cb.investments_usd == 80e6
    assert f"{_AFS} $60.0M ≠ its current + noncurrent parts $80.0M" in cb.note


def test_a_total_above_its_parts_leaves_the_excess_uncounted_and_named():
    """GM-shaped: the debt total holds securities classed as cash equivalents, already inside cash. The excess
    is never added (it cannot be told apart from cash), the value stays what the parts give, and it is said.
    """
    cb = _run(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
        DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent=[_inst(_D, 50e6)],
        **{_AFS_EX: [_inst(_D, 70e6)]},
    )
    assert cb.cash_usd == 150e6 and cb.investments_usd == 50e6
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert f"{_AFS_EX} $70.0M exceeds its current + noncurrent parts $50.0M" in cb.note
    assert "not counted" in cb.note


def test_a_total_beside_other_investment_tags_is_named_never_added():
    """NTSK-shaped: marketable securities 846.5M counted; a debt total of 956.2M that also holds 109.7M of cash
    equivalents. With no current/noncurrent part of its own it may restate what was counted: named, not added.
    """
    cb = _run(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 220_854_000)],
        MarketableSecuritiesCurrent=[_inst(_D, 846_509_000)],
        **{_AFS: [_inst(_D, 956_229_000)]},
    )
    assert cb.cash_usd == 220_854_000 + 846_509_000
    assert cb.investments_usd == 846_509_000
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert f"{_AFS} $956.2M" in cb.note and "not counted" in cb.note


def test_an_unsplit_total_confirmed_by_the_combined_line_is_auto():
    """Alone, but the combined cash-and-short-term-investments line equals cash + the total: the total is
    established as the short-term investments, none of it inside cash. Counted, AUTO."""
    cb = _run(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
        **{_AFS: [_inst(_D, 50e6)], _COMBINED: [_inst(_D, 150e6)]},
    )
    assert cb.cash_usd == 150e6 and cb.investments_usd == 50e6
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert "a total not split into current and noncurrent" in cb.note
    assert f"reconciles to {_COMBINED} $150.0M" in cb.note


def test_an_unsplit_total_the_combined_line_contradicts_is_quoted_not_added():
    """The combined line (120M) does not confirm cash + the total (150M): not established, so not added."""
    cb = _run(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
        **{_AFS: [_inst(_D, 50e6)], _COMBINED: [_inst(_D, 120e6)]},
    )
    assert cb.tier is Tier.FLAG and cb.flags == ["investments-unreconciled"]
    assert cb.cash_usd == 100e6 and cb.investments_usd is None  # was 150e6 and 50e6
    assert f"a debt-securities total of $50.0M ({_AFS}) was reported" in cb.note
    assert f"{_COMBINED} $120.0M ≠ cash + the debt-securities total $150.0M" in cb.note


def test_two_unsplit_totals_alone_are_quoted_the_largest_never_the_sum():
    cb = _run(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
        **{_AFS: [_inst(_D, 50e6)], _AFS_EX: [_inst(_D, 49.8e6)]},
    )
    assert cb.cash_usd == 100e6 and cb.investments_usd is None  # was 150e6 and 50e6
    assert cb.tier is Tier.FLAG and cb.flags == ["investments-unreconciled"]
    assert "a debt-securities total of $50.0M" in cb.note
    assert "overlapping tags are never summed" in cb.note


def test_a_total_off_the_cash_date_is_ignored():
    """OKLO-shaped: its ``AvailableForSaleSecuritiesDebtSecurities`` last reported 2024: a different balance sheet,
    never counted, never a flag (the golden OKLO value is unchanged)."""
    cb = _run(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
        **{_AFS: [_inst("2024-06-30", 50e6)]},
    )
    assert cb.cash_usd == 100e6 and cb.investments_usd is None
    assert cb.tier is Tier.AUTO and cb.flags == []


def test_deposits_and_equity_or_mixed_totals_are_never_read():
    """ODYS-shaped: ``DepositsAssetsCurrent`` also carries security and vendor deposits paid to others, so it is
    not read; ``AvailableForSaleSecurities`` and ``MarketableSecurities`` can hold equity stakes, never read.
    """
    cb = _run(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 17_220_000)],
        DepositsAssetsCurrent=[_inst(_D, 333_000)],
        AvailableForSaleSecurities=[_inst(_D, 20e6)],
        MarketableSecurities=[_inst(_D, 30e6)],
    )
    assert cb.cash_usd == 17_220_000 and cb.investments_usd is None
    assert cb.tier is Tier.AUTO and cb.flags == []


def test_the_debt_total_list_as_built():
    """Pinned so a change is deliberate: debt-only totals, each with its own current/noncurrent parts."""
    assert ex._DEBT_TOTALS == {
        _AFS: (
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
            "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
        ),
        _AFS_EX: (
            "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent",
            "DebtSecuritiesAvailableForSaleExcludingAccruedInterestNoncurrent",
        ),
    }
