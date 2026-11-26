"""Cash and burn from the cited filing's own inline XBRL when companyfacts is behind (W1-2, FLAG-only).

Offline through ``extract_for_security`` with a fake ``EdgarClient`` serving cut-down raw 10-Q documents
(``tests/fixtures/sec_extractor/inline_cash/``: the head, the ``ix:resources`` contexts and units, and the statement rows
holding the cash, investment and operating-cash-flow facts, verbatim from the cited filings) and each company's real
companyfacts, cut to the concepts the cash reader reads. companyfacts misses combined multi-registrant filings for
months: CEG's newest rows are its Q2 10-Q's (one quarter behind), DUK's and FE's their Q1's (two behind). The tier
and the machine writer's plan never change; without the raw document the candidate is today's.
"""

from __future__ import annotations

import copy
import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from domain.extraction import ExtractedFact, ExtractionResult, Tier
from ingest.edgar import extract as ex
from ingest.edgar.converts import clean_filing_text
from platform_values.machine import plan

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor" / "inline_cash"

# (cik, accession, primary document, filed, period of report) of each cited 10-Q (README in the fixture folder)
FILINGS: dict[str, tuple[int, str, str, str, str]] = {
    "CEG": (1868275, "0001868275-26-000119", "ceg-20260930.htm", "2026-11-09", "2026-09-30"),
    "DUK": (1326160, "0001326160-26-000051", "duk-20260930.htm", "2026-11-05", "2026-09-30"),
    "FE": (1031296, "0001031296-26-000144", "fe-20260930.htm", "2026-10-28", "2026-09-30"),
    "AMGN": (318154, "0000318154-26-000155", "amgn-20260930.htm", "2026-11-04", "2026-09-30"),
    "NVDA": (1045810, "0001045810-26-000075", "nvda-20260726.htm", "2026-08-26", "2026-07-26"),
}

# Each company's waiting cash candidate on 2026-11-25 (the steward queue): cash, burn, event date, flags. The
# text-only path still produces exactly these.
TODAY: dict[str, tuple[float, float, date, list[str]]] = {
    "CEG": (697_000_000, -1_128_000_000, date(2026, 6, 30), ["burn-off-period", "stale-cash"]),
    "DUK": (
        2_140_000_000,
        -1_512_000_000,
        date(2026, 3, 31),
        ["burn-off-period", "stale-cash", "investments-unreconciled"],
    ),
    "FE": (
        52_000_000,
        -148_000_000,
        date(2026, 3, 31),
        ["burn-off-period", "possible-one-time", "stale-cash"],
    ),
    "AMGN": (13_190_000_000, -4_241_000_000, date(2026, 9, 30), ["investments-unreconciled"]),
    "NVDA": (22_443_000_000, -24_077_000_000, date(2026, 7, 26), ["investments-unreconciled"]),
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


def _cash(ticker: str) -> ExtractedFact:
    """The live wrapper's cash candidate (``extract_for_security``, as background extraction calls it)."""
    facts = ex.extract_for_security(_Client(ticker), FILINGS[ticker][0], values_only=True)  # type: ignore[arg-type]
    return next(f for f in facts if f.fact_type == "cash_burn")


def _pure(ticker: str, cf: dict | None = None, raw: str | None = None, **kw: Any) -> ExtractedFact:
    """``extract_facts`` as a text-only caller uses it (no raw document), or with ``kw`` (``tenq_raw``, ...)."""
    period = date.fromisoformat(FILINGS[ticker][4])
    text = clean_filing_text(raw if raw is not None else _raw(ticker))
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
    return next(f for f in facts if f.fact_type == "cash_burn")


def _with_raw(ticker: str, cf: dict | None = None, raw: str | None = None) -> ExtractedFact:
    doc = raw if raw is not None else _raw(ticker)
    return _pure(
        ticker,
        cf,
        doc,
        tenq_raw=doc,
        tenq_filed=date.fromisoformat(FILINGS[ticker][3]),
    )


def _today(f: ExtractedFact) -> tuple[float | None, float | None, date, list[str]]:
    return (f.cash_usd, f.quarterly_burn_usd, f.event_date, f.flags)


# --- the first failing test (W1-2): CEG, one quarter behind ------------------------------------------------------


def test_ceg_offers_the_cited_quarters_cash_and_burn_not_the_previous_quarters():
    """CEG's companyfacts still ends at the Q2 10-Q (cash 697M as of 2026-06-30, six months of operating cash flow)
    sixteen days after the Q3 10-Q, so today's candidate offers the Q2 figures under the Q3 link. The Q3 document
    tags the registrant's cash, 603M at 2026-09-30, and nine months of operating cash flow, 4,979M; the quarter is
    that minus companyfacts' six months (1,553M)."""
    c = _cash("CEG")
    assert c.source_ref == _url("CEG")
    assert c.cash_usd == 603_000_000  # today 697,000,000: the Q2 balance sheet
    assert c.quarterly_burn_usd == -(
        4_979_000_000 - 1_553_000_000
    )  # a cash generator: a negative burn
    assert c.event_date == date(2026, 9, 30)  # today 2026-06-30
    assert c.tier is Tier.FLAG
    assert c.flags == ["companyfacts-behind"]
    assert "companyfacts is behind the cited filing" in c.note
    assert "its newest cash is as of 2026-06-30" in c.note
    assert "the quarter subtracts companyfacts' year to date ending 2026-06-30" in c.note
    assert "burn over 2026-06-30 → 2026-09-30 (92 days" in c.note
    assert "FLAGS: companyfacts-behind" in c.note
    kinds = {p.kind for p in c.located_passages}
    assert {
        "balance-sheet",
        "cash-flow",
    } <= kinds  # the steward reads the statements, never a bare figure


# --- two quarters behind: the year to date minus companyfacts' first quarter, flagged burn-span ----------------


@pytest.mark.parametrize(
    "ticker, cash, ytd, prior, flags",
    [
        (
            "DUK",
            451_000_000,
            7_889_000_000,
            1_512_000_000,
            ["burn-span", "investments-unreconciled", "companyfacts-behind"],
        ),
        ("FE", 48_000_000, 2_058_000_000, 148_000_000, ["burn-span", "companyfacts-behind"]),
    ],
)
def test_two_quarters_behind_offers_the_span_flagged(ticker, cash, ytd, prior, flags):
    """DUK's and FE's companyfacts end at Q1: the Q3 document's nine months minus companyfacts' three span 183 days,
    two quarters, so the value is offered flagged ``burn-span`` (the previous 10-Q's document is read only by the
    AUTO PR)."""
    c = _cash(ticker)
    assert (c.cash_usd, c.quarterly_burn_usd, c.event_date) == (
        cash,
        -(ytd - prior),
        date(2026, 9, 30),
    )
    assert c.tier is Tier.FLAG
    assert c.flags == flags
    assert "burn over 2026-03-31 → 2026-09-30 (183 days" in c.note
    assert "the burn spans 183 days, outside the 80-100-day quarter window" in c.note


def test_duk_quotes_its_period_end_debt_total_as_today():
    """DUK's undimensioned debt-securities total at 2026-09-30 (6,147M: nuclear decommissioning trust holdings) is a
    lone total: quoted, never added, as the reader does with companyfacts' (5,418M at 2026-03-31 today).
    """
    c = _cash("DUK")
    assert c.investments_usd is None
    assert "not counted: a debt-securities total of $6147.0M" in c.note


def test_fe_names_its_long_term_investments_and_counts_none():
    c = _cash("FE")
    assert c.investments_usd is None
    assert "not counted as cash: LongTermInvestments $619.0M" in c.note


def _drop_undimensioned_contexts(raw: str) -> str:
    """The document with every context that carries no dimension removed: only the other registrants' facts
    (``dei:LegalEntityAxis``) and dimensioned ones remain readable."""

    def keep(m: re.Match[str]) -> str:
        return m.group(0) if "explicitMember" in m.group(0) or "typedMember" in m.group(0) else ""

    return re.sub(r"<xbrli:context\b.*?</xbrli:context>", keep, raw, flags=re.S)


@pytest.mark.parametrize("ticker", ["CEG", "DUK", "FE"])
def test_another_registrants_facts_are_never_read(ticker):
    """A combined filing tags each registrant's statements under ``dei:LegalEntityAxis``: with the registrant's own
    (undimensioned) facts gone, nothing is read and today's candidate stands."""
    raw = _drop_undimensioned_contexts(_raw(ticker))
    assert "LegalEntityAxis" in raw
    c = _with_raw(ticker, raw=raw)
    assert _today(c) == TODAY[ticker]
    assert c == _pure(ticker, raw=raw)


def test_a_fact_in_another_currency_is_never_read():
    raw = _raw("CEG").replace("iso4217:USD", "iso4217:EUR")
    assert _today(_with_raw("CEG", raw=raw)) == TODAY["CEG"]


# --- AMGN and NVDA: evidence named in the note, never counted ----------------------------------------------------


def test_amgn_names_the_cash_equivalents_fair_value_equal_to_its_quoted_total():
    """AMGN's lone debt total (12,621M) equals its same-dated cash-equivalents fair-value disclosure: the total sits
    inside the 13,190M of cash and equivalents already. Named in the note; the figure and flags are today's.
    """
    c = _with_raw("AMGN")
    assert _today(c) == TODAY["AMGN"]
    assert c.tier is Tier.FLAG
    assert (
        "The quoted AvailableForSaleSecuritiesDebtSecurities $12621.0M equals the filing's"
        in c.note
    )
    assert "CashAndCashEquivalentsFairValueDisclosure on the cash date" in c.note
    assert "amgn:AvailableForSalesInvestmentsMember" in c.note
    assert "adding it would count it twice" in c.note


def test_nvda_names_its_uncounted_debt_securities_tag():
    """NVDA's 34,143M of current debt securities sit under ``DebtSecuritiesCurrent``, a tag the composition does
    not count (the steward's 56,586M is cash plus it). Named in the note; counting it is the AUTO PR's (A3).
    """
    c = _with_raw("NVDA")
    assert _today(c) == TODAY["NVDA"]
    assert c.tier is Tier.FLAG
    assert "DebtSecuritiesCurrent $34143.0M" in c.note
    assert "under a tag the reader does not count" in c.note


# --- FLAG-only: today's candidate without the raw document; tier and machine plan unchanged ----------------------


@pytest.mark.parametrize("ticker", list(FILINGS))
def test_no_raw_document_gives_todays_candidate(ticker):
    c = _pure(ticker)
    assert _today(c) == TODAY[ticker]
    assert c.tier is Tier.FLAG


@pytest.mark.parametrize("ticker", list(FILINGS))
def test_tier_and_machine_plan_are_unchanged_on_every_fixture(ticker):
    old, new = _pure(ticker), _with_raw(ticker)
    assert new != old  # every fixture's offer changes ...
    assert new.tier is old.tier is Tier.FLAG  # ... and never its tier
    day = date(2026, 11, 25)
    old_plan = plan(ExtractionResult(facts=[old]), "cash_burn", today=day)
    new_plan = plan(ExtractionResult(facts=[new]), "cash_burn", today=day)
    assert new_plan.kind == old_plan.kind == "flag"


def _ceg_caught_up() -> dict:
    """CEG's companyfacts with the Q3 10-Q's own rows added, as SEC will publish them."""
    cf = copy.deepcopy(_cf("CEG"))
    us = cf["facts"]["us-gaap"]
    row = {
        "accn": "0001868275-26-000119",
        "fy": 2026,
        "fp": "Q3",
        "form": "10-Q",
        "filed": "2026-11-09",
    }
    us["CashAndCashEquivalentsAtCarryingValue"]["units"]["USD"].append(
        {"end": "2026-09-30", "val": 603_000_000, **row}
    )
    us["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"].append(
        {"start": "2026-01-01", "end": "2026-09-30", "val": 4_979_000_000, **row}
    )
    return cf


def test_once_companyfacts_catches_up_the_raw_document_changes_nothing():
    """companyfacts caught up (CEG's Q3 rows added): the reader's own candidate, AUTO, and the raw document adds
    nothing, not even a sentence."""
    text_only = _pure("CEG", _ceg_caught_up())
    assert text_only.tier is Tier.AUTO
    assert (text_only.cash_usd, text_only.quarterly_burn_usd) == (603_000_000, -3_426_000_000)
    assert _with_raw("CEG", _ceg_caught_up()) == text_only


def test_a_document_without_inline_xbrl_changes_nothing():
    text = clean_filing_text(_raw("CEG"))
    assert _pure("CEG", tenq_raw=text, tenq_filed=date(2026, 11, 9)) == _pure("CEG")
