"""The share-count candidate offers the cited filing's own inline-XBRL cover (``ingest.edgar.inline_xbrl``).

Offline through ``extract_for_security`` with a fake ``EdgarClient`` serving cut-down raw 10-Q documents
(``tests/fixtures/sec_extractor/inline/``: the head, the ``ix:resources`` contexts, the cover page and the EPS rows,
verbatim from the cited filings) and each company's real companyfacts ``dei`` rows. FLAG-only: the tier and the
machine writer's plan never change; without the raw document the candidate is today's.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from domain.extraction import ExtractedFact, ExtractionResult, Tier
from ingest.edgar import extract as ex
from ingest.edgar.converts import clean_filing_text
from platform_values.machine import plan

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor" / "inline"

# (cik, accession, primary document, filed, period of report) of each cited 10-Q (README in the fixture folder)
FILINGS: dict[str, tuple[int, str, str, str, str]] = {
    "GOOGL": (1652044, "0001652044-26-000084", "goog-20260930.htm", "2026-10-29", "2026-09-30"),
    "HOOD": (1783879, "0001783879-26-000167", "hood-20260930.htm", "2026-10-28", "2026-09-30"),
    "GEMI": (2055592, "0002055592-26-000074", "gemi-20260930.htm", "2026-11-13", "2026-09-30"),
    "META": (1326801, "0001628280-26-066773", "meta-20260930.htm", "2026-10-22", "2026-09-30"),
    "DELL": (1571996, "0001571996-26-000046", "dell-20260731.htm", "2026-09-08", "2026-07-31"),
    "CEG": (1868275, "0001868275-26-000119", "ceg-20260930.htm", "2026-11-09", "2026-09-30"),
    "EXC": (1109357, "0001109357-26-000095", "exc-20260930.htm", "2026-10-30", "2026-09-30"),
    "ENHA": (1956439, "0001628280-26-073927", "enha-20260930.htm", "2026-11-13", "2026-09-30"),
    "GLXY": (1859392, "0001859392-26-000112", "glxy-20260930.htm", "2026-11-09", "2026-09-30"),
    "ZLAB": (1704292, "0001628280-26-070521", "zlab-20260930.htm", "2026-11-05", "2026-09-30"),
    "KNSA": (1730430, "0001104659-26-119186", "knsa-20260930x10q.htm", "2026-10-27", "2026-09-30"),
}

# Each company's waiting candidate on 2026-11-25 (the steward queue): value, event date, flags. The text-only path
# still produces exactly these.
TODAY: dict[str, tuple[float | None, date, list[str]]] = {
    "GOOGL": (None, date(2026, 9, 30), ["no-companyfacts"]),
    "HOOD": (None, date(2026, 9, 30), ["no-companyfacts"]),
    "GEMI": (None, date(2026, 9, 30), ["no-companyfacts"]),
    "META": (None, date(2026, 9, 30), ["no-companyfacts"]),
    "DELL": (635_812_750, date(2026, 7, 31), ["dual-class"]),
    "CEG": (354_307_379, date(2026, 7, 31), ["stale-cover"]),
    "EXC": (1_023_208_142, date(2026, 3, 31), ["stale-cover"]),
    "ENHA": (None, date(2026, 9, 30), ["no-companyfacts"]),
    "GLXY": (None, date(2026, 9, 30), ["no-companyfacts"]),
    "ZLAB": (88_592_343, date(2021, 2, 26), ["stale-cover"]),
    "KNSA": (49_732_123, date(2026, 9, 30), ["dual-class"]),
}


def _url(ticker: str) -> str:
    cik, acc, doc, _filed, _period = FILINGS[ticker]
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"


def _raw(ticker: str) -> str:
    return (_FX / f"{ticker}-10q.htm").read_text(encoding="utf-8")


def _cf(ticker: str) -> dict:
    return json.loads((_FX / f"{ticker}-cf.json").read_text(encoding="utf-8"))


def _subs(ticker: str) -> dict:
    _cik, acc, doc, filed, period = FILINGS[ticker]
    return {
        "filings": {
            "recent": {
                "form": ["10-Q"],
                "accessionNumber": [acc],
                "primaryDocument": [doc],
                "filingDate": [filed],
                "reportDate": [period],
                "acceptanceDateTime": [f"{filed}T21:05:00.000Z"],
            }
        }
    }


class _Client:
    """Serves the fixture companyfacts, a one-filing submissions index and the cut-down raw document."""

    def __init__(self, ticker: str) -> None:
        self.ticker = ticker

    def get_json(self, url: str, cache_key: str, **_: Any) -> dict:
        if cache_key.startswith("companyfacts/"):
            return _cf(self.ticker)
        if cache_key.startswith("submissions/"):
            return _subs(self.ticker)
        raise AssertionError(f"unexpected JSON read {cache_key}")

    def get_text(self, url: str, cache_key: str, **_: Any) -> str:
        assert cache_key == f"forms/{FILINGS[self.ticker][1]}/{FILINGS[self.ticker][2]}"
        return _raw(self.ticker)


def _shares(ticker: str) -> ExtractedFact:
    """The live wrapper's share-count candidate (``extract_for_security``, as background extraction calls it)."""
    facts = ex.extract_for_security(_Client(ticker), FILINGS[ticker][0], values_only=True)  # type: ignore[arg-type]
    return next(f for f in facts if f.fact_type == "shares_outstanding")


def _pure(ticker: str, cf: dict | None = None, **kw: Any) -> ExtractedFact:
    """``extract_facts`` as a text-only caller uses it (no raw document), or with ``kw`` (``tenq_raw``, ...)."""
    period = date.fromisoformat(FILINGS[ticker][4])
    text = clean_filing_text(_raw(ticker))
    facts = ex.extract_facts(
        cf if cf is not None else _cf(ticker),
        text,
        text,
        tenq_ref=_url(ticker),
        tenk_ref=_url(ticker),
        tenq_date=period,
        tenk_date=period,
        **kw,
    )
    return next(f for f in facts if f.fact_type == "shares_outstanding")


def _with_raw(ticker: str, cf: dict | None = None, filed: date | None = None) -> ExtractedFact:
    return _pure(
        ticker,
        cf,
        tenq_raw=_raw(ticker),
        tenq_filed=filed or date.fromisoformat(FILINGS[ticker][3]),
    )


def _assert_cover_passage(sh: ExtractedFact, ticker: str, *printed: str) -> None:
    (p,) = sh.located_passages
    assert p.kind == "cover" and p.source_ref == _url(ticker)
    for s in printed:
        assert s in p.excerpt, (s, p.excerpt)


# --- the first failing test (W1-1): GOOGL ------------------------------------------------------------------------


def test_googl_offers_its_inline_cover_sum_dated_by_the_cover():
    """GOOGL's cover prints '5,888 million', '827 million' and '5,579 million' shares of Class A, B and C (as of
    October 21, 2026), each tagged per class with scale 6. companyfacts drops the class dimension and the text
    parser needs a 6-character count, so today's candidate has no figure (``no-companyfacts``)."""
    sh = _shares("GOOGL")
    assert sh.value == 12_294_000_000
    assert sh.event_date == date(2026, 10, 21)
    assert sh.tier is Tier.FLAG
    assert sh.flags == ["dual-class"]
    _assert_cover_passage(
        sh, "GOOGL", "As of October 21, 2026", "5,888 million", "827 million", "5,579 million"
    )
    assert (
        "EPS share base (basic weighted average, 2026-07-01 to 2026-09-30): 12,256,000,000"
        in sh.note
    )


# --- multi-class covers: the inline sum, the cover's own date, the cover passage -----------------------------------


@pytest.mark.parametrize(
    "ticker, value, asof, printed",
    [
        ("HOOD", 900_491_277, date(2026, 10, 21), ("793,425,151 and 107,066,126",)),
        ("GEMI", 131_025_652, date(2026, 11, 6), ("55,898,868 and 75,126,784", "respectively")),
        ("META", 2_556_241_253, date(2026, 10, 16), ("2,214,472,367", "341,768,886")),
        ("DELL", 635_812_750, date(2026, 9, 1), ("September 1, 2026", "43,635,221")),
        ("KNSA", 78_571_705, date(2026, 10, 23), ("78,571,705 ordinary shares", "16,057,618")),
    ],
)
def test_multi_class_cover_offers_the_inline_sum_dated_by_the_cover(ticker, value, asof, printed):
    sh = _shares(ticker)
    assert (sh.tier, sh.value, sh.event_date, sh.flags) == (Tier.FLAG, value, asof, ["dual-class"])
    _assert_cover_passage(sh, ticker, *printed)
    assert f"as of {asof.isoformat()}" in sh.note


def test_dell_is_dated_by_its_cover_not_its_period_end():
    """DELL's sum was already offered (the text parser read it), dated 2026-07-31, the period end; the cover's own
    as-of date is September 1, 2026."""
    assert TODAY["DELL"][:2] == (635_812_750, date(2026, 7, 31))
    assert _shares("DELL").event_date == date(2026, 9, 1)


def test_knsa_total_beside_its_parts_is_not_summed_again():
    sh = _shares("KNSA")
    assert sh.value == 78_571_705  # today 49,732,123 (A + B from the text); never 157,143,410
    assert "Common Stock 78,571,705 is their total, not added again" in sh.note


# --- one count newer than companyfacts: companyfacts-behind -----------------------------------------------------


def test_ceg_offers_the_filings_own_count_when_companyfacts_is_behind():
    """CEG's companyfacts still ends at the Q2 cover (354,307,379 as of 2026-07-31) under the Q3 link; the Q3 10-Q
    tags 351,608,504 as of 2026-10-31."""
    sh = _shares("CEG")
    assert (sh.tier, sh.value, sh.event_date) == (Tier.FLAG, 351_608_504, date(2026, 10, 31))
    assert sh.flags == ["companyfacts-behind"]
    assert "companyfacts' newest cover row: 354,307,379 as of 2026-07-31" in sh.note
    _assert_cover_passage(sh, "CEG", "October 31, 2026", "351,608,504")


def test_exc_combined_filing_offers_the_parent_never_a_legal_entity_member():
    sh = _shares("EXC")
    assert (sh.value, sh.event_date, sh.flags) == (
        1_034_896_686,
        date(2026, 9, 30),
        ["companyfacts-behind"],
    )
    assert "another registrant of this combined filing (LegalEntityAxis)" in sh.note
    assert "Commonwealth Edison Co 127,021,427" in sh.note
    _assert_cover_passage(sh, "EXC", "Exelon Corporation", "1,034,896,686")


def test_zlab_adr_line_is_not_summed_and_the_note_says_ordinary_shares():
    sh = _shares("ZLAB")
    assert (sh.value, sh.event_date, sh.flags) == (
        1_124_448_350,
        date(2026, 10, 31),
        ["companyfacts-behind"],
    )
    assert "an ADR line, a subset of the ordinary shares: Adr 506,386,050" in sh.note
    assert "check the ADS ratio before approving" in sh.note


# --- class-outside-eps: the sum is still offered, both figures in the note ---------------------------------------


@pytest.mark.parametrize(
    "ticker, value, asof, base",
    [
        ("ENHA", 39_691_321, date(2026, 11, 12), "13,378,513"),
        ("GLXY", 385_736_936, date(2026, 11, 6), "193,344,582"),
    ],
)
def test_a_class_outside_the_eps_base_is_flagged_and_the_sum_still_offered(
    ticker, value, asof, base
):
    sh = _shares(ticker)
    assert (sh.tier, sh.value, sh.event_date) == (Tier.FLAG, value, asof)
    assert sh.flags == ["dual-class", "class-outside-eps"]
    assert f"= {value:,.0f}" in sh.note  # the sum offered
    assert (
        f"The EPS share base {base} is nearer part of the cover" in sh.note
    )  # and the base beside it
    assert "confirm which classes carry economics before approving" in sh.note


@pytest.mark.parametrize("ticker", ["GOOGL", "HOOD", "GEMI", "META", "DELL", "KNSA"])
def test_covers_inside_the_eps_base_are_not_flagged(ticker):
    assert "class-outside-eps" not in _shares(ticker).flags


# --- FLAG-only: today's candidate without the raw document; tier and machine plan unchanged ----------------------


@pytest.mark.parametrize("ticker", list(FILINGS))
def test_no_raw_document_gives_todays_candidate(ticker):
    sh = _pure(ticker)
    assert (sh.value, sh.event_date, sh.flags) == TODAY[ticker]
    assert sh.tier is Tier.FLAG


@pytest.mark.parametrize("ticker", list(FILINGS))
def test_tier_and_machine_plan_are_unchanged_on_every_inline_fixture(ticker):
    old, new = _pure(ticker), _with_raw(ticker)
    assert new != old  # every fixture's offer changes ...
    assert new.tier is old.tier is Tier.FLAG  # ... and never its tier
    day = date(2026, 11, 25)
    old_plan = plan(ExtractionResult(facts=[old]), "shares_outstanding", today=day)
    new_plan = plan(ExtractionResult(facts=[new]), "shares_outstanding", today=day)
    assert new_plan.kind == old_plan.kind == "flag"


def test_an_auto_count_is_never_re_read():
    """companyfacts caught up (CEG's Q3 cover row added: one value, dated after the period): AUTO, and the raw
    document changes nothing, not even the passage."""
    cf = copy.deepcopy(_cf("CEG"))
    rows = cf["facts"]["dei"]["EntityCommonStockSharesOutstanding"]["units"]["shares"]
    rows.append(
        {
            "end": "2026-10-31",
            "val": 351_608_504,
            "accn": "0001868275-26-000119",
            "fy": 2026,
            "fp": "Q3",
            "form": "10-Q",
            "filed": "2026-11-09",
        }
    )
    text_only = _pure("CEG", cf)
    assert text_only.tier is Tier.AUTO
    assert _with_raw("CEG", cf) == text_only


def test_an_inline_cover_dated_after_the_filing_is_refused():
    """The cover-date bound applies to the inline instant too: GOOGL's cover (2026-10-21) read as if filed
    2026-10-20 keeps today's candidate, flagged and named in the note."""
    sh = _with_raw("GOOGL", filed=date(2026, 10, 20))
    assert (sh.value, sh.event_date) == TODAY["GOOGL"][:2]
    assert sh.flags == ["no-companyfacts", "cover-after-filing"]
    assert "Refused the filing's inline cover count 12,294,000,000 as of 2026-10-21" in sh.note


def test_a_document_without_inline_xbrl_changes_nothing():
    text = clean_filing_text(_raw("GOOGL"))
    assert _pure("GOOGL", tenq_raw=text) == _pure("GOOGL")
