"""The quarterly cash COMPOSITION (V2 extractor correctness, PR 1; operator decisions D1 and D2, 2026-11-15).

The written cash rule (``docs/EXTRACTOR.md``, "The uniform cash rule") is cash + equivalents + ALL
marketable securities, current and noncurrent. An audit of 460 auto-applied quarterly cash values found the
reader applying it only partly:

- short-term investments left out because they sat under tags the reader did not know (RBRK, LMAT), while the
  note claimed "marketable securities ($0.0M)";
- the generic ``LongTermInvestments`` line (often stakes in other companies) added to cash and called
  marketable securities (DIS stored 12.8bn against 5.2bn of cash; KMI 8.8bn against 0.9bn);
- a zero-valued cash tag kept by the tie-break while plain ``Cash`` showed 8.4M on the same date (SOAR).

D1: one "cash and short-term investments" number, the investment part also carried on its own
(``ExtractedFact.investments_usd``). D2: never count the generic long-term investments line; long-term
MARKETABLE securities still count.

The fixtures in ``tests/fixtures/edgar/cash_composition/`` are RECONSTRUCTED, not copied: the fork's EDGAR cache
was not readable for this work. Each holds only the nodes the composer reads, with the tag names and values the
audit files record (``docs/temp/v2-extractor-audits-2026-11-15/quarterly-cash-FINDINGS.md`` and
``quarterly-cash-verdicts.txt``, and the build plan's finding 1, 2 and 2b rows). What the audit files do NOT
record is synthetic and marked ``"accn": "reconstructed"``: every accession, filed date and fiscal label; DIS's,
KMI's and SOAR's dates and operating cash flow; SOAR's investment tag; CRSP's tag names. Fed through the old
reader, each reproduces the value the audit found stored (RBRK 416,166,000; LMAT 27,493,000; DIS
12,812,000,000; KMI 8,754,000,000; SOAR 1,908,000 AUTO; TSLA 45,447,000,000; CRSP 2,276,569,000).

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
_RESTRICTED = "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"
_COMBINED = "CashCashEquivalentsAndShortTermInvestments"


def _facts(ticker: str) -> dict:
    return json.loads((_FX / f"{ticker}.json").read_text(encoding="utf-8"))["facts"]


def _cb(ticker: str, period: date):
    return ex._cash_burn(_facts(ticker), "", "10-Q", period, ExtractorConfig())


# ---------------------------------------------------------------------------------------------------------
# the audit's cases
# ---------------------------------------------------------------------------------------------------------


def test_rbrk_counts_short_term_investments_held_under_the_afs_debt_tag():
    """RBRK kept 1.33bn of short-term investments under ``AvailableForSaleSecuritiesDebtSecuritiesCurrent``,
    a tag the old reader did not know: it stored 416,166,000 and wrote "marketable securities ($0.0M)".
    """
    cb = _cb("RBRK", date(2026, 7, 31))
    assert cb.cash_usd == 416_166_000 + 1_331_483_000
    assert cb.investments_usd == 1_331_483_000
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert (
        "short-term investments $1331.5M (AvailableForSaleSecuritiesDebtSecuritiesCurrent)"
        in cb.note
    )
    assert "cash+equivalents $416.2M" in cb.note
    assert "$0.0M" not in cb.note


def test_lmat_counts_the_current_tag_and_names_the_discontinued_one():
    """LMAT's old ``AvailableForSaleSecuritiesCurrent`` stops at 2021 (off-date, never summed); its current
    securities sit under the AFS-debt tag on the cash date. Old reading: 27,493,000."""
    cb = _cb("LMAT", date(2026, 9, 30))
    assert cb.cash_usd == 27_493_000 + 362_228_000
    assert cb.investments_usd == 362_228_000
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert "AvailableForSaleSecuritiesCurrent (2021-06-30)" in cb.note and "EXCLUDED" in cb.note
    assert "$0.0M" not in cb.note


def test_dis_never_counts_the_long_term_investments_line():
    """D2: DIS's 7.6bn ``LongTermInvestments`` (largely stakes in other companies) is not cash. Old reading:
    12,812,000,000, the line called "marketable securities". The note names it as not counted."""
    cb = _cb("DIS", date(2026, 6, 27))
    assert cb.cash_usd == 5_185_000_000
    assert cb.investments_usd is None  # no investment counted: nothing to split out
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert "not counted as cash: LongTermInvestments $7627.0M" in cb.note
    assert "EquityMethodInvestments" not in cb.note  # never read for cash, so never stated
    assert "marketable securities ($" not in cb.note


def test_kmi_never_counts_the_long_term_investments_line():
    cb = _cb("KMI", date(2026, 9, 30))
    assert cb.cash_usd == 918_000_000  # old reading: 8,754,000,000
    assert cb.tier is Tier.AUTO
    assert "not counted as cash: LongTermInvestments $7836.0M" in cb.note


def test_soar_cash_tags_that_disagree_are_a_flag_stating_both():
    """SOAR's ``CashAndCashEquivalentsAtCarryingValue`` is 0 on the date ``Cash`` and the restricted-cash
    total read 8,440,000: cash and equivalents can never be smaller than cash alone, so the composition is not
    established. Old reading: AUTO 1,908,000 (the zero plus the long-term investments line)."""
    cb = _cb("SOAR", date(2026, 6, 30))
    assert cb.tier is Tier.FLAG and "cash-tags-disagree" in cb.flags
    assert "CashAndCashEquivalentsAtCarryingValue $0.0M" in cb.note
    assert "Cash $8.4M" in cb.note
    assert cb.cash_usd == 0.0  # the first-listed tag, offered for the steward, never applied
    assert "not counted as cash: LongTermInvestments $1.9M" in cb.note


def test_tsla_already_right_stays_the_same_value():
    """No regression: TSLA's investments sit under ``ShortTermInvestments``, which the old reader read."""
    cb = _cb("TSLA", date(2026, 9, 30))
    assert cb.cash_usd == 45_447_000_000
    assert cb.investments_usd == 27_594_000_000
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert cb.quarterly_burn_usd == -(14_897_000_000 - 8_634_000_000)
    assert "short-term investments $27594.0M (ShortTermInvestments)" in cb.note


def test_crsp_long_term_marketable_securities_still_count():
    """D2 keeps the written rule's noncurrent MARKETABLE securities: CRSP's 4,021k stays in."""
    cb = _cb("CRSP", date(2026, 9, 30))
    assert cb.cash_usd == 2_276_569_000
    assert cb.investments_usd == 2_080_521_000 + 4_021_000
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert "long-term marketable securities $4.0M (MarketableSecuritiesNoncurrent)" in cb.note


# ---------------------------------------------------------------------------------------------------------
# the rules, on synthetic companyfacts
# ---------------------------------------------------------------------------------------------------------

_D = "2026-03-31"
_PERIOD = date(2026, 3, 31)


def _inst(end: str, val: float) -> dict:
    return {"end": end, "val": val, "filed": end}


def _synthetic(**nodes: list[dict]) -> dict:
    g = {c: {"units": {"USD": rows}} for c, rows in nodes.items()}
    g["NetCashProvidedByUsedInOperatingActivities"] = {
        "units": {"USD": [{"start": "2026-01-01", "end": _D, "val": -5e6}]}
    }
    return {"us-gaap": g}


def _run(facts: dict):
    return ex._cash_burn(facts, "", "10-Q", _PERIOD, ExtractorConfig())


def test_nothing_tagged_is_said_so_never_a_fake_zero():
    cb = _run(_synthetic(CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)]))
    assert cb.cash_usd == 100e6 and cb.investments_usd is None
    assert "no short-term investments tagged on the cash date" in cb.note
    assert "no long-term marketable securities tagged on the cash date" in cb.note
    assert "$0.0M" not in cb.note


def test_overlapping_investment_tags_take_the_largest_never_the_sum():
    """An AFS tag often restates ``ShortTermInvestments``: the largest same-dated value, never the sum, and
    every tag seen is named."""
    cb = _run(
        _synthetic(
            CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
            ShortTermInvestments=[_inst(_D, 50e6)],
            AvailableForSaleSecuritiesDebtSecuritiesCurrent=[_inst(_D, 40e6)],
        )
    )
    assert cb.cash_usd == 150e6 and cb.investments_usd == 50e6
    assert "AvailableForSaleSecuritiesDebtSecuritiesCurrent $40.0M" in cb.note
    assert "overlapping tags are never summed" in cb.note


def test_a_combined_tag_that_reconciles_is_stated_and_never_added():
    cb = _run(
        _synthetic(
            CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
            ShortTermInvestments=[_inst(_D, 50e6)],
            **{_COMBINED: [_inst(_D, 150e6)]},
        )
    )
    assert cb.cash_usd == 150e6  # the combined tag is a check, never a third addend
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert f"reconciles to {_COMBINED} $150.0M" in cb.note


def test_a_combined_tag_that_does_not_reconcile_is_a_flag():
    """The combined tag says 180M but cash + the investments read come to 100M: investments sit under a tag
    the reader does not know. The value is offered (never derived from the difference) and flagged.
    """
    cb = _run(
        _synthetic(
            CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
            **{_COMBINED: [_inst(_D, 180e6)]},
        )
    )
    assert cb.tier is Tier.FLAG and cb.flags == ["investments-unreconciled"]
    assert cb.cash_usd == 100e6
    assert f"{_COMBINED} $180.0M" in cb.note and "cash + short-term investments $100.0M" in cb.note


def test_a_combined_tag_within_the_tolerance_reconciles():
    """Rounding between two tags of one balance sheet is not a disagreement (the dial)."""
    facts = _synthetic(
        CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
        ShortTermInvestments=[_inst(_D, 50e6)],
        **{_COMBINED: [_inst(_D, 150.5e6)]},
    )
    assert _run(facts).flags == []
    strict = ex._cash_burn(
        facts, "", "10-Q", _PERIOD, ExtractorConfig(investments_reconcile_tolerance=0.001)
    )
    assert strict.flags == ["investments-unreconciled"]


def test_cash_that_includes_restricted_cash_never_silently_reconciles():
    """Cash read from the restricted-cash total already includes restricted cash: added to the investments
    it overshoots the combined tag, and that is a flag, never a silent sum."""
    cb = _run(
        _synthetic(
            ShortTermInvestments=[_inst(_D, 50e6)],
            **{_RESTRICTED: [_inst(_D, 110e6)], _COMBINED: [_inst(_D, 150e6)]},
        )
    )
    assert cb.tier is Tier.FLAG and cb.flags == ["investments-unreconciled"]
    assert "includes restricted cash" in cb.note


def test_investments_dated_after_the_cash_are_a_flag():
    """The investments sit on a newer balance sheet than the cash and not on the cash date: the composition
    at one date cannot be established."""
    cb = _run(
        _synthetic(
            CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
            ShortTermInvestments=[_inst("2026-06-30", 50e6)],
        )
    )
    assert "investments-off-date" in cb.flags and cb.tier is Tier.FLAG
    assert cb.cash_usd == 100e6
    assert "ShortTermInvestments (2026-06-30)" in cb.note


def test_plain_cash_below_cash_and_equivalents_is_a_component_not_a_disagreement():
    """OKLO-shaped: ``Cash`` 15.6M beside cash and equivalents 1,594.1M on one date is the cash part of the
    equivalents line, consistent, never a flag."""
    cb = _run(
        _synthetic(
            CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 1_594_103_000)],
            Cash=[_inst(_D, 15_571_000)],
        )
    )
    assert cb.flags == [] and cb.cash_usd == 1_594_103_000


def test_restricted_total_below_cash_and_equivalents_disagrees():
    cb = _run(
        _synthetic(
            CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
            **{_RESTRICTED: [_inst(_D, 90e6)]},
        )
    )
    assert cb.flags == ["cash-tags-disagree"]


def test_restricted_investments_and_equity_securities_are_never_read():
    cb = _run(
        _synthetic(
            CashAndCashEquivalentsAtCarryingValue=[_inst(_D, 100e6)],
            RestrictedInvestmentsCurrent=[_inst(_D, 20e6)],
            EquitySecuritiesFvNiCurrent=[_inst(_D, 30e6)],
            MarketableSecuritiesEquitySecurities=[_inst(_D, 40e6)],
        )
    )
    assert cb.cash_usd == 100e6 and cb.investments_usd is None


def test_the_tag_lists_as_built():
    """The lists ``docs/EXTRACTOR.md`` ("The uniform cash rule") names: pinned so a change is deliberate."""
    names = lambda lst: [c for _, c in lst]  # noqa: E731
    assert names(ex._STI) == [
        "ShortTermInvestments",
        "MarketableSecuritiesCurrent",
        "AvailableForSaleSecuritiesCurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesCurrent",
        "DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent",
        "HeldToMaturitySecuritiesCurrent",
        "OtherShortTermInvestments",
        "CertificatesOfDepositAtCarryingValue",
        "TimeDepositsAtCarryingValue",
    ]
    assert names(ex._LT_MARKETABLE) == [
        "MarketableSecuritiesNoncurrent",
        "AvailableForSaleSecuritiesNoncurrent",
        "AvailableForSaleSecuritiesDebtSecuritiesNoncurrent",
        "DebtSecuritiesAvailableForSaleExcludingAccruedInterestNoncurrent",
        "HeldToMaturitySecuritiesNoncurrent",
    ]
    assert names(ex._NOT_CASH) == [
        "LongTermInvestments",
        "EquityMethodInvestments",
        "OtherLongTermInvestments",
        "LongTermInvestmentsAndReceivablesNet",
    ]
