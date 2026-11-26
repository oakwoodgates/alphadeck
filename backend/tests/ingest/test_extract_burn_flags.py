"""The burn's FLAG offers (W1-4, FLAG-only): ``possible-one-time`` names the line that tripped it, and a year to date
whose fiscal year starts a few days off the previous filing's is offered as a quarter derived across filings.

Fixtures: ``tests/fixtures/sec_extractor/burn_flags/`` (cut-down real documents and companyfacts of five cash
candidates that waited for the steward on 2026-11-25; README there). The tier and the machine writer's plan never
change: every candidate here was a FLAG and stays one.

- ``possible-one-time``'s check is unchanged (a non-routine ``IncreaseDecreaseIn*`` line of the burn's own period at
  least ``one_time_line_fraction`` of the operating cash flow). Today it locates the first place a bare figure appears
  and names no line: the steward review misread FEIM's as a financing item and COHR's as an inventory build. Now the
  note names every line that trips, by tag and figure, and a passage sits on each as printed, anchored by the line's
  label and figure (given the raw document, on the tagged figure itself).
- ``ytd-derived-across-filings``: with no earlier year to date of the same start, one of the same tag whose start is
  at most ``_YTD_START_SLACK_DAYS`` away is subtracted (FUBO's 10-K year starts 2025-09-28, its 10-Qs' 2025-10-01).
  A wider start gap (AMSC's mis-tagged 2026-01-01, 90 days off) or another tag (KULR) stays ``ytd-raw``.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from domain.config import ExtractorConfig
from domain.extraction import ExtractedFact, ExtractionResult, Tier
from ingest.edgar import extract as ex
from ingest.edgar.converts import clean_filing_text
from platform_values.machine import plan

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor" / "burn_flags"

# (cik, accession, primary document, filed, period of report, form) of each cited filing (README in the fixture folder)
FILINGS: dict[str, tuple[int, str, str, str, str, str]] = {
    "FEIM": (
        39020,
        "0001185185-26-003983",
        "feim10q073126.htm",
        "2026-09-14",
        "2026-07-31",
        "10-Q",
    ),
    "COHR": (
        820318,
        "0000820318-26-000023",
        "iivi-20260930.htm",
        "2026-11-04",
        "2026-09-30",
        "10-Q",
    ),
    "FUBO": (
        1484769,
        "0001628280-26-074590",
        "fubo-20260930.htm",
        "2026-11-18",
        "2026-09-30",
        "10-K",
    ),
    "AMSC": (
        880807,
        "0001437749-26-034936",
        "amsc20260930_10q.htm",
        "2026-11-04",
        "2026-09-30",
        "10-Q",
    ),
    "KULR": (
        1662684,
        "0001104659-26-124824",
        "tmb-20260930x10q.htm",
        "2026-11-10",
        "2026-09-30",
        "10-Q",
    ),
}

# Each company's waiting cash candidate on 2026-11-25 (the steward queue): cash, burn, event date, flags.
TODAY: dict[str, tuple[float, float, date, list[str]]] = {
    "FEIM": (61_407_000, -2_622_000, date(2026, 7, 31), ["possible-one-time"]),
    "COHR": (1_752_929_000, 92_681_000, date(2026, 9, 30), ["possible-one-time"]),
    "FUBO": (266_259_000, 378_249_000, date(2026, 9, 30), ["ytd-raw"]),
    "AMSC": (169_916_000, -48_720_000, date(2026, 9, 30), ["ytd-raw"]),
    "KULR": (54_460_036, 32_278_672, date(2026, 9, 30), ["ytd-raw"]),
}


def _url(ticker: str) -> str:
    cik, acc, doc, *_ = FILINGS[ticker]
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"


def _raw(ticker: str) -> str:
    form = FILINGS[ticker][5].replace("-", "").lower()
    return (_FX / f"{ticker}-{form}.htm").read_text(encoding="utf-8")


def _cf(ticker: str) -> dict:
    return json.loads((_FX / f"{ticker}-cf.json").read_text(encoding="utf-8"))


def _pure(ticker: str, **kw: Any) -> ExtractedFact:
    """``extract_facts`` as a text-only caller uses it (no raw document), or with ``kw`` (``tenq_raw``, ...)."""
    period = date.fromisoformat(FILINGS[ticker][4])
    text = clean_filing_text(_raw(ticker))
    facts = ex.extract_facts(
        _cf(ticker),
        text,
        text,
        tenq_ref=_url(ticker),
        tenk_ref=_url(ticker),
        tenq_date=period,
        tenk_date=period,
        values_form=FILINGS[ticker][5],
        **kw,
    )
    return next(f for f in facts if f.fact_type == "cash_burn")


def _with_raw(ticker: str) -> ExtractedFact:
    """The candidate as background extraction makes it: with the values filing's raw document."""
    return _pure(ticker, tenq_raw=_raw(ticker), tenq_filed=date.fromisoformat(FILINGS[ticker][3]))


def _today(f: ExtractedFact) -> tuple[float | None, float | None, date, list[str]]:
    return (f.cash_usd, f.quarterly_burn_usd, f.event_date, f.flags)


def _one_time_passages(f: ExtractedFact) -> list:
    return [p for p in f.located_passages if p.kind == "cash-flow"]


# --- the first failing test (W1-4): FEIM's passage names the line --------------------------------------------------


def test_feim_passage_names_the_line_and_figure_that_tripped():
    """FEIM's operating cash flow is 2.6M; its aggregate working-capital line, 4,102 thousand, is above 0.70 of it.
    Today the passage is anchored on the bare figure "4,102" and the note names no line."""
    c = _with_raw("FEIM")
    assert _today(c) == TODAY["FEIM"]
    first = _one_time_passages(c)[0]
    assert first.anchor == "Net changes in operating assets and liabilities (4,102)"
    assert "Net changes in operating assets and liabilities ( 4,102 )" in first.excerpt
    assert "IncreaseDecreaseInOperatingCapital $4.1M" in c.note


def test_feim_names_its_second_line_too():
    """FEIM's second line trips as well: a sentence about contract liabilities recognized as revenue, tagged
    ``IncreaseDecreaseInCommodityContractAssetsAndLiabilities`` (4.0M). Both are named, the larger first.
    """
    c = _with_raw("FEIM")
    second = _one_time_passages(c)[1]
    assert second.anchor == "we recognized $4.0 million"
    assert "4.0 million of our contract liabilities" in second.excerpt
    assert (
        "possible-one-time: IncreaseDecreaseInOperatingCapital $4.1M, "
        "IncreaseDecreaseInCommodityContractAssetsAndLiabilities $4.0M" in c.note
    )
    assert "each at least 0.7 of the operating cash flow's $2.6M" in c.note


def test_cohr_names_accrued_compensation_not_the_larger_routine_lines():
    """COHR's -92.7M quarter: the one non-routine line at or above 0.70 of it is accrued compensation, 90.2M. The
    larger lines beside it (inventories, payables, other operating net assets) are routine working capital.
    """
    c = _with_raw("COHR")
    assert _today(c) == TODAY["COHR"]
    (only,) = _one_time_passages(c)
    assert only.anchor == "Accrued compensation and benefits (90,196)"
    assert "IncreaseDecreaseInEmployeeRelatedLiabilities $90.2M" in c.note
    for routine in ("Inventories", "AccountsPayable", "OtherOperatingCapitalNet"):
        assert f"IncreaseDecreaseIn{routine}" not in c.note


@pytest.mark.parametrize(
    "ticker, anchors",
    [
        (
            "FEIM",
            [
                "Net changes in operating assets and liabilities (4,102)",
                "we recognized $4.0 million",
            ],
        ),
        ("COHR", ["Accrued compensation and benefits (90,196)"]),
    ],
)
def test_without_the_raw_document_the_lines_are_found_in_the_text(ticker, anchors):
    c = _pure(ticker)
    assert _today(c) == TODAY[ticker]
    assert [p.anchor for p in _one_time_passages(c)] == anchors


def _facts(ocf: list[dict], lines: dict[str, list[dict]] | None = None) -> dict:
    us: dict[str, Any] = {
        "CashAndCashEquivalentsAtCarryingValue": {
            "units": {"USD": [{"end": "2026-09-30", "val": 100e6, "filed": "2026-11-05"}]}
        },
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": ocf}},
    }
    for concept, rows in (lines or {}).items():
        us[concept] = {"units": {"USD": rows}}
    return {"us-gaap": us}


def _dur(start: str, end: str, val: float, accn: str = "", filed: str = "") -> dict:
    return {"start": start, "end": end, "val": val, "accn": accn, "filed": filed}


def test_a_figure_inside_a_longer_number_is_not_the_line():
    """The bare-figure search found "4,102" inside "14,102" (a balance-sheet line earlier in the filing)."""
    text = (
        "Deferred taxes 14,102 13,219 Total assets 900,000 Cash flows from operating activities: Net loss ( 2,000 ) "
        "Accrued liabilities ( 4,102 ) 1,282 Net cash used in operating activities ( 5,000 ) 1,161"
    )
    facts = _facts(
        [_dur("2026-07-01", "2026-09-30", -5_000_000)],
        {"IncreaseDecreaseInAccruedLiabilities": [_dur("2026-07-01", "2026-09-30", -4_102_000)]},
    )
    c = ex._cash_burn(facts, text, "https://x/10q.htm", date(2026, 9, 30), ExtractorConfig())
    assert c.flags == ["possible-one-time"]
    (only,) = _one_time_passages(c)
    assert only.anchor == "Accrued liabilities (4,102)"
    assert "IncreaseDecreaseInAccruedLiabilities $4.1M" in c.note


@pytest.mark.parametrize(
    "text, shown, anchor",
    [
        (  # SMCI's 10-Q, verbatim: a related-party parenthetical prints figures of its own
            "respectively) 473,879 901 Accrued liabilities (including changes in related party balances of $( 65 ) "
            "and $ 554 during the three months ended September 30, 2026 and 2025, respectively) 445,557 ( 259,350 )",
            "445,557",
            "Accrued liabilities (…) 445,557",
        ),
        (  # AVAV's 10-Q: zero-width spaces between the cells
            "Accounts payable \u200b 12,820 \u200b ( 29,625 ) \u200b Other liabilities \u200b \u200b ( 17,810 ) \u200b",
            "17,810",
            "Other liabilities (17,810)",
        ),
    ],
)
def test_the_printed_line_reads_through_parentheticals_and_invisible_characters(
    text, shown, anchor
):
    assert ex._printed_line(text, text.index(shown), shown) == anchor


def test_a_figure_in_millions_with_a_thousands_comma_is_found():
    """CLS prints its statements in millions to one decimal with a thousands comma: "1,493.8"."""
    text = "Accounts payable, accrued and other current liabilities 1,493.8 ( 12.0 )"
    assert ex._printed_figure(text, 1_493_800_000) == (text.index("1,493.8"), "1,493.8")


# --- a quarter derived across filings ------------------------------------------------------------------------------


def test_fubo_offers_the_quarter_derived_across_filings():
    """FUBO's 10-K year to date runs 2025-09-28 to 2026-09-30 (-378.2M); its third-quarter 10-Q's runs 2025-10-01 to
    2026-06-30 (-417.1M). No earlier year to date starts on 2025-09-28, so today the full year is offered as the
    "quarter" (``ytd-raw``). The fourth quarter is the difference: 38.8M generated."""
    c = _with_raw("FUBO")
    assert c.flags == ["ytd-derived-across-filings"]
    assert c.quarterly_burn_usd == -(417_093_000 - 378_249_000)
    assert (c.cash_usd, c.event_date) == (266_259_000, date(2026, 9, 30))
    assert c.tier is Tier.FLAG
    assert "burn over 2026-06-30 → 2026-09-30 (92 days" in c.note
    assert "2025-09-28 → 2026-09-30" in c.note
    assert "2025-10-01 → 2026-06-30" in c.note
    assert "0001628280-26-053535" in c.note  # the previous filing, by accession
    assert "starts 3 days apart" in c.note
    assert "FLAGS: ytd-derived-across-filings" in c.note


def test_fubo_text_only_gives_the_same_offer():
    assert _today(_pure("FUBO")) == _today(_with_raw("FUBO"))


@pytest.mark.parametrize(
    "ticker",
    [
        "KULR",  # nine months under the continuing-operations tag; the earlier quarters under the plain one
        "AMSC",  # six months tagged from 2026-01-01, 90 days off the first quarter's 2026-04-01
    ],
)
def test_a_tag_switch_or_a_wide_start_gap_stays_ytd_raw(ticker):
    c = _with_raw(ticker)
    assert _today(c) == TODAY[ticker]
    assert "NOT a quarter" in c.note


def _derive(prior_start: str, prior_end: str = "2026-06-30") -> ExtractedFact:
    facts = _facts(
        [
            _dur("2025-10-01", "2026-09-30", -90e6, "0000000000-26-000003", "2026-11-05"),
            _dur(prior_start, prior_end, -60e6, "0000000000-26-000002", "2026-08-05"),
        ]
    )
    return ex._cash_burn(facts, "", "https://x/10q.htm", date(2026, 9, 30), ExtractorConfig())


def test_a_start_seven_days_off_is_derived_eight_is_not():
    seven, eight = _derive("2025-10-08"), _derive("2025-10-09")
    assert seven.flags == ["ytd-derived-across-filings"]
    assert seven.quarterly_burn_usd == 30e6
    assert "starts 7 days apart" in seven.note
    assert eight.flags == ["ytd-raw"]
    assert eight.quarterly_burn_usd == 90e6


def test_a_derived_span_outside_the_quarter_window_is_also_burn_span():
    c = _derive("2025-09-28", prior_end="2026-03-31")
    assert c.flags == ["ytd-derived-across-filings", "burn-span"]
    assert c.quarterly_burn_usd == 30e6
    assert "burn over 2026-03-31 → 2026-09-30 (183 days" in c.note


def test_a_same_start_prior_is_still_the_auto_derivation():
    """A same-start prior keeps today's AUTO derived quarter, even beside a near-start year to date ending later."""
    facts = _facts(
        [
            _dur("2025-10-01", "2026-09-30", -90e6),
            _dur("2025-10-01", "2026-06-30", -60e6),
            _dur("2025-09-28", "2026-07-31", -70e6),
        ]
    )
    c = ex._cash_burn(facts, "", "https://x/10q.htm", date(2026, 9, 30), ExtractorConfig())
    assert c.tier is Tier.AUTO and c.flags == []
    assert c.quarterly_burn_usd == 30e6
    assert "derived (YTD − prior YTD)" in c.note


# --- FLAG-only: tier and machine plan unchanged -------------------------------------------------------------------


@pytest.mark.parametrize("ticker", list(FILINGS))
@pytest.mark.parametrize("raw", [False, True])
def test_tier_and_machine_plan_are_unchanged_on_every_fixture(ticker, raw):
    c = _with_raw(ticker) if raw else _pure(ticker)
    assert c.tier is Tier.FLAG
    p = plan(ExtractionResult(facts=[c]), "cash_burn", today=date(2026, 11, 25))
    assert p.kind == "flag"
