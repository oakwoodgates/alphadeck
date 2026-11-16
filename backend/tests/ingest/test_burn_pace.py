"""The burn PACE CHECK (V2 extractor correctness PR 3; operator decision D3, 2026-11-15).

An audit of 460 auto-applied cash-and-burn values found that for 39 of the 200 cash-burning companies ONE unusual
quarter drives the runway: the latest quarter's burn is more than 2x off the year-to-date pace, or has the opposite
sign. UP's third quarter burned 17.1M against 191.3M in its first half: its runway read about 9 months where its
nine-month pace gives about 2. The arithmetic is right; the reading rests on one quarter.

D3 (approved): a quarter far off the year-to-date pace is a VISIBLE CAVEAT on the applied value, never a queued flag.
The tier never moves because of pace (AUTO stays AUTO and is applied; FLAG stays FLAG): the caveat and the
year-to-date pace ride the extracted fact and its stored note (``domain/burn_pace.py``).

The fixtures in ``tests/fixtures/edgar/burn_pace/`` are RECONSTRUCTED, not copied: the fork's EDGAR cache was not
readable for this work. Each holds only the nodes the reader uses, with the values the audit files record
(``docs/temp/v2-extractor-audits-2026-11-15/quarterly-cash-FINDINGS.md``): UP's cash 51,811,000, nine months
-208,345,000 and six months -191,260,000; RCEL's cash 10,500,000, nine months -14,278,000 and six months -13,568,000;
RXRX's cash 549,853,000, nine months -264,454,000 and six months -187,048,000; AAL's cash 1,124m and short-term
investments 7,163m, nine months generating 4,541m and a third quarter burning 153m. Synthetic, marked
``"accn": "reconstructed"``: every accession, filed date and fiscal label; the fiscal year start (a calendar year);
AAL's six months (the recorded nine months plus the recorded quarter's burn). The cash-generating case reuses
``burn_period/EHC.json``.

Display-only: the pace check feeds the runway's words, never its pips, and never a call.
"""

from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import pytest

from domain.burn_pace import pace_divergence, runway_pace
from domain.config import ExtractorConfig
from domain.extraction import Tier
from ingest.edgar import extract as ex

_FX = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "burn_pace"
_BP = Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "burn_period"
_Q3 = date(2026, 9, 30)
_OCF = "NetCashProvidedByUsedInOperatingActivities"
# a configuration under which the divergence rule can never fire (the tier must not care either way)
_NEVER = ExtractorConfig(pace_ratio_low=0.0, pace_ratio_high=math.inf)


def _facts(ticker: str, folder: Path = _FX) -> dict:
    return json.loads((folder / f"{ticker}.json").read_text(encoding="utf-8"))["facts"]


def _cb(facts: dict, period: date = _Q3, cfg: ExtractorConfig | None = None):
    return ex._cash_burn(facts, "", "10-Q", period, cfg or ExtractorConfig())


# ---------------------------------------------------------------------------------------------------------
# the audit's cases
# ---------------------------------------------------------------------------------------------------------


def test_up_one_quiet_quarter_carries_the_caveat_and_stays_auto():
    """UP: the runway reads 9.1 months at the third quarter's burn and 2.2 at the nine-month pace. The value is
    still AUTO (applied); the caveat names both readings in one sentence."""
    cb = _cb(_facts("UP"))
    assert cb.quarterly_burn_usd == 17_085_000
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert cb.ytd_quarterly_burn_usd == 69_943_025
    assert cb.ytd_burn_days == 272
    assert cb.pace_caveat == (
        "Last quarter's burn ($17.1M) is well below the year-to-date pace ($69.9M a quarter): runway reads "
        "9.1 months at last quarter's pace and 2.2 months at the year-to-date pace."
    )
    assert (
        "year-to-date pace: burn 208,345,000 over 2026-01-01 → 2026-09-30 (272 days) = 69,943,025 a quarter"
        in cb.note
    )
    assert f"pace caveat: {cb.pace_caveat}" in cb.note
    assert cb.note.endswith("No attention flags.")


def test_rcel_reads_44_months_at_the_quarter_and_under_7_at_the_pace():
    cb = _cb(_facts("RCEL"))
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert cb.pace_caveat == (
        "Last quarter's burn ($710.0K) is well below the year-to-date pace ($4.8M a quarter): runway reads "
        "44.4 months at last quarter's pace and 6.6 months at the year-to-date pace."
    )


def test_aal_opposite_signs_are_named():
    """AAL's third quarter burned 153m while its nine months generated 4,541m: the runway exists only at the one
    quarter's pace, and the caveat says the year to date generated cash."""
    cb = _cb(_facts("AAL"))
    assert cb.quarterly_burn_usd == 153_000_000
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert cb.ytd_quarterly_burn_usd is not None and cb.ytd_quarterly_burn_usd < 0
    assert cb.pace_caveat == (
        "Last quarter burned $153.0M, but the year to date generated $1.5B a quarter, so the 162.5-month runway "
        "rests on that one quarter."
    )


def test_a_steady_burner_has_its_pace_and_no_caveat():
    """RXRX's quarter (77.4M) is 0.87x its nine-month pace (88.8M a quarter): the pace is carried, quietly, and no
    caveat is written (honest loudness: the caveat marks the exception)."""
    cb = _cb(_facts("RXRX"))
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert cb.ytd_quarterly_burn_usd == 88_779_250
    assert cb.pace_caveat is None
    assert "year-to-date pace: burn 264,454,000" in cb.note
    assert "pace caveat" not in cb.note


def test_a_cash_generating_company_has_no_runway_caveat():
    """EHC generated cash in the quarter and the nine months: there is no runway, so no runway caveat."""
    cb = _cb(_facts("EHC", _BP))
    assert cb.quarterly_burn_usd is not None and cb.quarterly_burn_usd < 0
    assert cb.ytd_quarterly_burn_usd is not None and cb.ytd_quarterly_burn_usd < 0
    assert cb.pace_caveat is None and "pace caveat" not in cb.note


def _dur(start: str, end: str, val: float, filed: str | None = None) -> dict:
    return {"start": start, "end": end, "val": val, "filed": filed or end}


def _g(*ocf: dict, cash: float = 50e6, cash_end: str = "2026-09-30") -> dict:
    return {
        "us-gaap": {
            "CashAndCashEquivalentsAtCarryingValue": {
                "units": {"USD": [{"end": cash_end, "val": cash, "filed": cash_end}]}
            },
            _OCF: {"units": {"USD": list(ocf)}},
        }
    }


def test_a_cash_generator_far_off_its_pace_still_has_no_caveat():
    """Generating 2M in the quarter against 100M a quarter year to date is more than 2x apart, but neither reading
    is a runway."""
    f = _g(_dur("2026-01-01", "2026-06-30", 200e6), _dur("2026-01-01", "2026-09-30", 202e6))
    cb = _cb(f)
    assert cb.ytd_quarterly_burn_usd is not None and cb.pace_caveat is None


def test_a_first_fiscal_quarter_has_no_separate_pace_and_no_caveat():
    """A first quarter IS the year to date: no pace beside it, nothing in the note, no caveat."""
    f = _g(_dur("2026-01-01", "2026-03-31", -9e6), cash_end="2026-03-31")
    cb = _cb(f, date(2026, 3, 31))
    assert cb.tier is Tier.AUTO and cb.flags == []
    assert cb.ytd_quarterly_burn_usd is None and cb.ytd_burn_days is None
    assert cb.pace_caveat is None and "year-to-date pace" not in cb.note


def test_a_quarter_burning_after_a_generating_year_reads_the_flip_the_other_way():
    """Cash-positive last quarter (the meter reads cash-generative) while the year to date burned: the caveat says
    so, with the runway the pace implies."""
    f = _g(_dur("2026-01-01", "2026-06-30", -60e6), _dur("2026-01-01", "2026-09-30", -55e6))
    cb = _cb(f)
    assert cb.quarterly_burn_usd == -5e6
    assert cb.tier is Tier.AUTO
    assert cb.pace_caveat == (
        "Last quarter was cash-positive ($5.0M), but the year to date burned $18.5M a quarter: about 8.1 months "
        "of runway at that pace."
    )


# ---------------------------------------------------------------------------------------------------------
# the tier never moves, the rule is the dials', no lookahead
# ---------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("ticker", ["UP", "RCEL", "AAL", "RXRX"])
def test_the_pace_never_changes_the_tier_or_the_flags(ticker: str):
    """D3: a caveat, never a flag. The same filing read with a rule that can never call a quarter divergent has
    the same tier, flags and values."""
    on, off = _cb(_facts(ticker)), _cb(_facts(ticker), cfg=_NEVER)
    assert on.tier is off.tier is Tier.AUTO
    assert on.flags == off.flags == []
    assert (on.cash_usd, on.quarterly_burn_usd) == (off.cash_usd, off.quarterly_burn_usd)
    assert not any("pace" in fl for fl in on.flags)


def test_a_flagged_value_stays_flagged_with_its_caveat():
    """No FLAG becomes AUTO: UP's value read under an older cash date keeps ``stale-cash`` and still carries the
    caveat."""
    facts = _facts("UP")
    facts["us-gaap"]["CashAndCashEquivalentsAtCarryingValue"]["units"]["USD"][0][
        "end"
    ] = "2026-06-30"
    cb = _cb(facts)
    assert cb.tier is Tier.FLAG and cb.flags == ["stale-cash"]
    assert cb.pace_caveat is not None


def test_the_ratio_bounds_are_dials():
    """The magic-number guard: the 0.5x-2x bounds are ``ExtractorConfig`` dials. RXRX's 0.87x is steady by default
    and divergent once the lower bound is raised past it."""
    assert _cb(_facts("RXRX")).pace_caveat is None
    assert _cb(_facts("RXRX"), cfg=ExtractorConfig(pace_ratio_low=0.9)).pace_caveat is not None
    assert _cb(_facts("UP"), cfg=ExtractorConfig(pace_ratio_low=0.2)).pace_caveat is None


@pytest.mark.parametrize(
    ("quarter", "pace", "kind"),
    [
        (2.0, 1.0, None),  # exactly 2x is not MORE than 2x
        (2.01, 1.0, "divergent"),
        (0.5, 1.0, None),
        (0.49, 1.0, "divergent"),
        (1.0, -1.0, "opposite"),
        (-1.0, 1.0, "opposite"),
        (-1.0, -9.0, None),  # neither burns: no runway either way
        (1.0, 0.0, "divergent"),
    ],
)
def test_pace_divergence_rule(quarter: float, pace: float, kind: str | None):
    assert pace_divergence(quarter, pace) == kind


def test_the_pace_reads_the_original_filing_not_a_later_restatement():
    """No lookahead: the year to date is the row first filed for that period. A later filing's restated column for
    the same period (filed a year on) is not what was public on the cited filing."""
    f = _g(
        _dur("2026-01-01", "2026-06-30", -191_260_000, "2026-08-07"),
        _dur("2026-01-01", "2026-09-30", -208_345_000, "2026-11-06"),
        _dur("2026-01-01", "2026-09-30", -400_000_000, "2027-11-05"),
    )
    cb = _cb(f)
    assert "year-to-date pace: burn 208,345,000 over" in cb.note


def test_an_inception_to_date_column_is_never_a_pace():
    """A development-stage filer's cumulative column (longer than a fiscal year) is not a year to date."""
    f = _g(
        _dur("2026-07-01", "2026-09-30", -10e6),
        _dur("2019-01-01", "2026-09-30", -900e6),
    )
    cb = _cb(f)
    assert cb.ytd_quarterly_burn_usd is None and cb.pace_caveat is None


def test_a_burn_that_is_not_one_quarter_gets_no_pace():
    """The check applies only to a burn that passed the quarter window (plan Q5): URI's 183-day "quarter" is
    flagged ``burn-span`` and gets no pace."""
    cb = _cb(_facts("URI", _BP))
    assert "burn-span" in cb.flags
    assert cb.ytd_quarterly_burn_usd is None and "year-to-date pace" not in cb.note


def test_the_stored_note_gives_every_screen_the_same_reading():
    """The note is the storage (no column yet): ``runway_pace`` recovers the pace from it and re-applies the rule to
    the stored burn, matching what the extractor carried."""
    cb = _cb(_facts("UP"))
    rp = runway_pace(cb.cash_usd, cb.quarterly_burn_usd, cb.note)
    assert rp is not None
    assert rp.ytd_quarterly_burn_usd == cb.ytd_quarterly_burn_usd
    assert rp.ytd_days == 272
    assert rp.ytd_runway_months == 2.2
    assert rp.caveat == cb.pace_caveat
    steady = _cb(_facts("RXRX"))
    rs = runway_pace(steady.cash_usd, steady.quarterly_burn_usd, steady.note)
    assert rs is not None and rs.caveat is None and rs.ytd_runway_months == 18.6
    assert runway_pace(1e6, 1e5, "cash_usd $1.0M = cash+equivalents $1.0M") is None
    assert runway_pace(1e6, None, cb.note) is None
