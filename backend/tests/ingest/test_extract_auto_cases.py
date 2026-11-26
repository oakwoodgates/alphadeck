"""The W1 AUTO cases (operator decision D4, 2026-11-26), each behind its own ``ExtractorConfig`` dial
(``docs/PLATFORM_EXTRACTION.md``, "What the machine applies from a filing's own inline XBRL"):

- **A1** ``cover_eps_tolerance`` (2%): a share count read from the cited 10-Q/10-K's own inline-XBRL cover is AUTO when
  the cover tags its counts at one as-of date, on or after the period end and not after the filing date, every count
  is a plain class of common stock, and their sum is within the tolerance of the same filing's EPS share base for the
  cited period. The same bound narrows ``class-outside-eps`` to a cover sum MORE than the tolerance above the base.
- **A2** ``cash_auto_companyfacts_behind``: the W1-2 cash candidate read from the cited document because companyfacts is
  behind is AUTO when it is the registrant's own undimensioned figures and every AUTO check (period, span, composition,
  one-time) passes on them, the one-time check on reconciliation lines of the offered quarter (derived like the
  quarter). Two quarters behind, the previous 10-Q's own year to date is read (one more document).
- **A3** ``debt_securities_counted``: ``DebtSecuritiesCurrent`` / ``DebtSecuritiesNoncurrent`` count like the other
  short- and long-term investment tags (NVDA's 34,143M).
- **A6** ``operating_capital_routine``: the aggregate working-capital line is routine working capital (FEIM's).
- ``ytd_start_slack_days``: W1-4's module constant, now a dial (7).

Every dial off gives the FLAG-only candidates of W1-1, W1-2 and W1-4 (their own test files pin those with the dials
off). Fixtures: ``tests/fixtures/sec_extractor/inline/`` (W1-1's eleven covers, plus COIN, AMRC, HIMS, CRWV, DKNG,
MSTR, TEM, S and REGN), ``inline_cash/`` (W1-2), ``auto_cash/`` (CEG's and NEE's documents with their reconciliation
lines, NEE's previous 10-Q), ``burn_flags/`` (W1-4); READMEs there.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from domain.config import ExtractorConfig
from domain.extraction import ExtractedFact, ExtractionResult, Tier
from ingest.edgar import extract as ex
from ingest.edgar.converts import clean_filing_text
from platform_values.machine import plan

_SEC = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor"
_DAY = date(2026, 11, 26)


def _off() -> ExtractorConfig:
    """Every AUTO dial of this PR off: the FLAG-only behaviour of W1-1, W1-2 and W1-4."""
    return ExtractorConfig(
        cover_eps_tolerance=None,
        cash_auto_companyfacts_behind=False,
        debt_securities_counted=False,
        operating_capital_routine=False,
    )


# (folder, cik, accession, primary document, filed, period of report) of each cited filing (READMEs)
FILINGS: dict[str, tuple[str, int, str, str, str, str]] = {
    "GOOGL": (
        "inline",
        1652044,
        "0001652044-26-000084",
        "goog-20260930.htm",
        "2026-10-29",
        "2026-09-30",
    ),
    "HOOD": (
        "inline",
        1783879,
        "0001783879-26-000167",
        "hood-20260930.htm",
        "2026-10-28",
        "2026-09-30",
    ),
    "GEMI": (
        "inline",
        2055592,
        "0002055592-26-000074",
        "gemi-20260930.htm",
        "2026-11-13",
        "2026-09-30",
    ),
    "META": (
        "inline",
        1326801,
        "0001628280-26-066773",
        "meta-20260930.htm",
        "2026-10-22",
        "2026-09-30",
    ),
    "DELL": (
        "inline",
        1571996,
        "0001571996-26-000046",
        "dell-20260731.htm",
        "2026-09-08",
        "2026-07-31",
    ),
    "CEG": (
        "inline",
        1868275,
        "0001868275-26-000119",
        "ceg-20260930.htm",
        "2026-11-09",
        "2026-09-30",
    ),
    "EXC": (
        "inline",
        1109357,
        "0001109357-26-000095",
        "exc-20260930.htm",
        "2026-10-30",
        "2026-09-30",
    ),
    "ENHA": (
        "inline",
        1956439,
        "0001628280-26-073927",
        "enha-20260930.htm",
        "2026-11-13",
        "2026-09-30",
    ),
    "GLXY": (
        "inline",
        1859392,
        "0001859392-26-000112",
        "glxy-20260930.htm",
        "2026-11-09",
        "2026-09-30",
    ),
    "ZLAB": (
        "inline",
        1704292,
        "0001628280-26-070521",
        "zlab-20260930.htm",
        "2026-11-05",
        "2026-09-30",
    ),
    "KNSA": (
        "inline",
        1730430,
        "0001104659-26-119186",
        "knsa-20260930x10q.htm",
        "2026-10-27",
        "2026-09-30",
    ),
    "COIN": (
        "inline",
        1679788,
        "0001679788-26-000122",
        "coin-20260930.htm",
        "2026-10-29",
        "2026-09-30",
    ),
    "AMRC": (
        "inline",
        1488139,
        "0001628280-26-072337",
        "amrc-20260930.htm",
        "2026-11-09",
        "2026-09-30",
    ),
    "HIMS": (
        "inline",
        1773751,
        "0001773751-26-000246",
        "hims-20260930.htm",
        "2026-11-09",
        "2026-09-30",
    ),
    "CRWV": (
        "inline",
        1769628,
        "0001769628-26-000465",
        "crwv-20260930.htm",
        "2026-11-12",
        "2026-09-30",
    ),
    "DKNG": (
        "inline",
        1883685,
        "0001883685-26-000041",
        "dkng-20260930.htm",
        "2026-11-06",
        "2026-09-30",
    ),
    "MSTR": (
        "inline",
        1050446,
        "0001050446-26-000060",
        "mstr-20260930.htm",
        "2026-11-02",
        "2026-09-30",
    ),
    "TEM": (
        "inline",
        1717115,
        "0001193125-26-442624",
        "tem-20260930.htm",
        "2026-11-02",
        "2026-09-30",
    ),
    "S": ("inline", 1583708, "0001583708-26-000055", "s-20260731.htm", "2026-08-28", "2026-07-31"),
    "REGN": (
        "inline",
        872589,
        "0000872589-26-000034",
        "regn-20260930.htm",
        "2026-10-30",
        "2026-09-30",
    ),
}
# the cash filings: NEE's previous 10-Q is the Q2 one (companyfacts ends at its Q1 10-Q: two quarters behind)
CASH: dict[str, tuple[str, int, str, str, str, str]] = {
    "CEG": (
        "auto_cash",
        1868275,
        "0001868275-26-000119",
        "ceg-20260930.htm",
        "2026-11-09",
        "2026-09-30",
    ),
    "NEE": (
        "auto_cash",
        753308,
        "0000753308-26-000081",
        "nee-20260930.htm",
        "2026-10-21",
        "2026-09-30",
    ),
    "NVDA": (
        "inline_cash",
        1045810,
        "0001045810-26-000075",
        "nvda-20260726.htm",
        "2026-08-26",
        "2026-07-26",
    ),
}
NEE_Q2 = ("0000753308-26-000060", "nee-20260630.htm", "2026-07-24", "2026-06-30")


def _url(cik: int, acc: str, doc: str) -> str:
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{doc}"


def _read(folder: str, name: str) -> str:
    return (_SEC / folder / name).read_text(encoding="utf-8")


def _cf(folder: str, ticker: str) -> dict:
    # CEG's cash fixture reads W1-2's companyfacts (inline_cash/), its share fixture W1-1's (inline/)
    if folder == "auto_cash" and ticker == "CEG":
        folder = "inline_cash"
    return json.loads(_read(folder, f"{ticker}-cf.json"))


class _Client:
    """Serves companyfacts, a submissions index of ``rows`` (newest first) and the raw documents ``docs`` by cache key;
    records every document read."""

    def __init__(
        self, cf: dict, rows: list[tuple[str, str, str, str, str]], docs: dict[str, str]
    ) -> None:
        self.cf, self.rows, self.docs, self.read = cf, rows, docs, []

    def get_json(self, url: str, cache_key: str, **_: Any) -> dict:
        if cache_key.startswith("companyfacts/"):
            return self.cf
        if cache_key.startswith("submissions/"):
            return {
                "filings": {
                    "recent": {
                        "form": [r[0] for r in self.rows],
                        "accessionNumber": [r[1] for r in self.rows],
                        "primaryDocument": [r[2] for r in self.rows],
                        "filingDate": [r[3] for r in self.rows],
                        "reportDate": [r[4] for r in self.rows],
                        "acceptanceDateTime": [f"{r[3]}T21:05:00.000Z" for r in self.rows],
                    }
                }
            }
        raise AssertionError(f"unexpected JSON read {cache_key}")

    def get_text(self, url: str, cache_key: str, **_: Any) -> str:
        self.read.append(cache_key)
        return self.docs[cache_key]


def _shares(ticker: str, cfg: ExtractorConfig | None = None) -> ExtractedFact:
    """The live wrapper's share-count candidate (``extract_for_security``, as background extraction calls it)."""
    folder, cik, acc, doc, filed, period = FILINGS[ticker]
    client = _Client(
        _cf(folder, ticker),
        [("10-Q", acc, doc, filed, period)],
        {f"forms/{acc}/{doc}": _read(folder, f"{ticker}-10q.htm")},
    )
    kw = {} if cfg is None else {"cfg": cfg}
    facts = ex.extract_for_security(client, cik, values_only=True, **kw)  # type: ignore[arg-type]
    return next(f for f in facts if f.fact_type == "shares_outstanding")


def _pure_shares(ticker: str, raw: str | None = None, **kw: Any) -> ExtractedFact:
    folder, cik, acc, doc, filed, period = FILINGS[ticker]
    raw = raw if raw is not None else _read(folder, f"{ticker}-10q.htm")
    text = clean_filing_text(raw)
    pe = date.fromisoformat(kw.pop("period", period))
    kw.setdefault("tenq_filed", date.fromisoformat(filed))
    facts = ex.extract_facts(
        _cf(folder, ticker),
        text,
        text,
        tenq_ref=_url(cik, acc, doc),
        tenk_ref=_url(cik, acc, doc),
        tenq_date=pe,
        tenk_date=pe,
        tenq_raw=raw,
        **kw,
    )
    return next(f for f in facts if f.fact_type == "shares_outstanding")


def _kind(fact: ExtractedFact) -> str:
    return plan(ExtractionResult(facts=[fact]), fact.fact_type, today=_DAY).kind


# --- A1: the first failing test -------------------------------------------------------------------------------------


def test_googl_cover_is_applied_when_its_eps_share_base_agrees():
    """GOOGL's cover tags 5,888M + 827M + 5,579M shares of Classes A, B and C as of 2026-10-21 (one date, after the
    2026-09-30 period end, before the 2026-10-29 filing); the same filing's EPS share base for the quarter is 12,256M,
    0.31% from the 12,294M sum. W1-1 offers the sum flagged ``dual-class``; the machine applies it.
    """
    sh = _shares("GOOGL")
    assert sh.tier is Tier.AUTO
    assert (sh.value, sh.event_date, sh.flags) == (12_294_000_000, date(2026, 10, 21), [])
    assert _kind(sh) == "auto"
    assert "EPS share base" in sh.note and "+0.31%" in sh.note
    assert "applied automatically" in sh.note


# --- A1: applied ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ticker, value, asof",
    [
        ("GOOGL", 12_294_000_000, date(2026, 10, 21)),
        ("META", 2_556_241_253, date(2026, 10, 16)),
        ("HOOD", 900_491_277, date(2026, 10, 21)),
        ("COIN", 264_763_418, date(2026, 10, 22)),
        ("AMRC", 53_111_375, date(2026, 11, 5)),
        ("HIMS", 234_412_422, date(2026, 11, 6)),  # Class A + Class V
        ("CRWV", 556_125_724, date(2026, 10, 30)),  # the sum 0.51% under the base
        (
            "DELL",
            635_812_750,
            date(2026, 9, 1),
        ),  # dated by its cover, not the 2026-07-31 period end
        ("CEG", 351_608_504, date(2026, 10, 31)),  # one count, companyfacts behind
        (
            "EXC",
            1_034_896_686,
            date(2026, 9, 30),
        ),  # the parent's count beside six other registrants'
        ("S", 348_049_117, date(2026, 8, 21)),  # 1.91% above the base: inside 2%
    ],
)
def test_a_cover_within_the_tolerance_of_its_eps_base_is_auto(ticker, value, asof):
    sh = _shares(ticker)
    assert (sh.tier, sh.value, sh.event_date, sh.flags) == (Tier.AUTO, value, asof, [])
    assert _kind(sh) == "auto"
    assert sh.source == "10-q-cover"
    assert sh.located_passages and sh.located_passages[0].kind == "cover"


# --- A1: still the steward's -----------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ticker, value, flags",
    [
        (
            "ENHA",
            39_691_321,
            ["dual-class", "class-outside-eps"],
        ),  # base 13.4M: Class B outside earnings
        ("GLXY", 385_736_936, ["dual-class", "class-outside-eps"]),  # base 193.3M: Up-C Class B
        ("DKNG", 891_305_041, ["dual-class", "class-outside-eps"]),  # base 496.9M: unpaired Class B
        ("GEMI", 131_025_652, ["dual-class"]),  # 2.0004% above the base: issuance, just outside 2%
        ("MSTR", 422_354_607, ["dual-class"]),  # 5.5% above its per-class EPS facts: issuance
        (
            "TEM",
            182_401_637,
            ["dual-class", "class-outside-eps"],
        ),  # an EPS base tagged unscaled (181,311)
        (
            "ZLAB",
            1_124_448_350,
            ["companyfacts-behind"],
        ),  # an ADR line set aside beside the ordinary count
        ("KNSA", 78_571_705, ["dual-class"]),  # a total tagged beside its four class parts
        (
            "REGN",
            103_447_859,
            ["dual-class"],
        ),  # CommonStockMember beside Class A: a total or a class, untold
    ],
)
def test_a_cover_the_eps_base_does_not_confirm_stays_flag(ticker, value, flags):
    sh = _shares(ticker)
    assert (sh.tier, sh.value, sh.flags) == (Tier.FLAG, value, flags)
    assert _kind(sh) == "flag"


@pytest.mark.parametrize("ticker", ["S", "REGN"])
def test_class_outside_eps_is_not_a_finding_within_the_tolerance(ticker):
    """S's cover is 1.91% and REGN's 1.72% above their EPS share bases: W1-1 flagged a small class outside earnings
    for both (``nearest``); inside the tolerance the gap is not evidence of one. With the dial off it still fires.
    """
    assert "class-outside-eps" not in _shares(ticker).flags
    assert "class-outside-eps" in _shares(ticker, _off()).flags


def test_gemi_is_applied_only_with_a_wider_tolerance():
    assert _shares("GEMI").tier is Tier.FLAG
    wider = _shares("GEMI", ExtractorConfig(cover_eps_tolerance=0.021))
    assert (wider.tier, wider.value) == (Tier.AUTO, 131_025_652)


@pytest.mark.parametrize(
    "ticker", ["GOOGL", "META", "HOOD", "DELL", "CEG", "EXC", "S", "REGN", "GEMI"]
)
def test_with_the_cover_dial_off_the_candidate_is_w1_1s(ticker):
    off = _shares(ticker, _off())
    assert off.tier is Tier.FLAG
    assert off == _shares(ticker, ExtractorConfig(cover_eps_tolerance=None))


def test_a_cover_with_no_filing_date_is_not_applied():
    """The as-of date cannot be checked against the filing date: offered, not applied."""
    assert _pure_shares("GOOGL", tenq_filed=None).tier is Tier.FLAG
    assert _pure_shares("GOOGL").tier is Tier.AUTO


def test_a_cover_dated_before_the_period_end_is_not_applied():
    """GOOGL's 2026-10-21 cover read as a filing for a period ending 2026-10-31."""
    sh = _pure_shares("GOOGL", period="2026-10-31")
    assert sh.tier is Tier.FLAG


def _googl_with_a_second_cover_date() -> str:
    """GOOGL's document plus one more Class B cover count dated 2026-10-01 (a cover tagged at two as-of dates)."""
    raw = _read("inline", "GOOGL-10q.htm")
    ctx = re.search(r'<xbrli:context id="c-29">.*?</xbrli:context>', raw, re.S).group(0)  # type: ignore[union-attr]
    extra = re.sub(
        r"<xbrli:instant>[^<]+</xbrli:instant>", "<xbrli:instant>2026-10-01</xbrli:instant>", ctx
    )
    extra = extra.replace('id="c-29"', 'id="c-x"')
    fact = (
        '<ix:nonFraction unitRef="shares" contextRef="c-x" decimals="-6" '
        'name="dei:EntityCommonStockSharesOutstanding" scale="6">826</ix:nonFraction>'
    )
    raw = raw.replace(ctx, ctx + extra, 1)
    return raw.replace("</body>", f"<div>{fact}</div></body>", 1)


def test_a_cover_tagged_at_two_as_of_dates_is_not_applied():
    raw = _googl_with_a_second_cover_date()
    sh = _pure_shares("GOOGL", raw=raw)
    assert (sh.value, sh.event_date) == (
        12_294_000_000,
        date(2026, 10, 21),
    )  # the latest date's counts, offered
    assert sh.tier is Tier.FLAG


# --- A2: cash from the cited document when companyfacts is behind -----------------------------------------------------


def _cash_client(ticker: str, prior: bool = False) -> _Client:
    folder, cik, acc, doc, filed, period = CASH[ticker]
    rows = [("10-Q", acc, doc, filed, period)]
    docs = {f"forms/{acc}/{doc}": _read(folder, f"{ticker}-10q.htm")}
    if prior:
        rows.append(("10-Q", NEE_Q2[0], NEE_Q2[1], NEE_Q2[2], NEE_Q2[3]))
        docs[f"forms/{NEE_Q2[0]}/{NEE_Q2[1]}"] = _read(folder, f"{ticker}-q2-10q.htm")
    return _Client(_cf(folder, ticker), rows, docs)


def _cash(client: _Client, ticker: str, cfg: ExtractorConfig | None = None) -> ExtractedFact:
    kw = {} if cfg is None else {"cfg": cfg}
    facts = ex.extract_for_security(client, CASH[ticker][1], values_only=True, **kw)  # type: ignore[arg-type]
    return next(f for f in facts if f.fact_type == "cash_burn")


def test_ceg_one_quarter_behind_is_applied_with_the_parents_figures():
    """CEG's companyfacts ends at Q2. The Q3 document tags the registrant's own cash (603M), nine months of operating
    cash flow (4,979M) and its reconciliation lines; companyfacts has the six months (1,553M) and their lines. The
    quarter (92 days) and every check pass on those figures, the one-time check on lines derived the same way.
    """
    c = _cash(_cash_client("CEG"), "CEG")
    assert (c.tier, c.flags) == (Tier.AUTO, [])
    assert (c.cash_usd, c.quarterly_burn_usd, c.event_date) == (
        603_000_000,
        -(4_979_000_000 - 1_553_000_000),
        date(2026, 9, 30),
    )
    assert "companyfacts is behind the cited filing" in c.note
    assert "No attention flags." in c.note
    assert _kind(c) == "auto"


def test_nee_two_quarters_behind_reads_its_previous_10q_and_is_applied():
    """NEE's companyfacts ends at Q1. The quarter is the Q3 document's nine months minus the Q2 10-Q's own six months
    (the one more document read), never minus companyfacts' three (183 days)."""
    client = _cash_client("NEE", prior=True)
    c = _cash(client, "NEE")
    assert client.read == [
        f"forms/{CASH['NEE'][2]}/{CASH['NEE'][3]}",
        f"forms/{NEE_Q2[0]}/{NEE_Q2[1]}",
    ]
    assert (c.tier, c.flags, c.event_date) == (Tier.AUTO, [], date(2026, 9, 30))
    assert "burn over 2026-06-30 → 2026-09-30 (92 days" in c.note
    assert NEE_Q2[1] in c.note  # the previous filing named
    assert _kind(c) == "auto"


def test_a2_dial_off_gives_w1_2s_flag_and_reads_no_second_document():
    client = _cash_client("NEE", prior=True)
    c = _cash(client, "NEE", _off())
    assert len(client.read) == 1
    assert c.tier is Tier.FLAG and "companyfacts-behind" in c.flags and "burn-span" in c.flags
    ceg = _cash(_cash_client("CEG"), "CEG", _off())
    assert (ceg.tier, ceg.flags) == (Tier.FLAG, ["companyfacts-behind"])


def test_nee_without_its_previous_10q_stays_flag():
    """No previous 10-Q in the index: the 183-day span is offered, flagged, as W1-2 does."""
    c = _cash(_cash_client("NEE"), "NEE")
    assert c.tier is Tier.FLAG and {"burn-span", "companyfacts-behind"} <= set(c.flags)


def _without_lines(raw: str) -> str:
    """The document with every reconciliation-line fact's tag renamed out of reach (the printed text stays)."""
    return re.sub(r'name="us-gaap:IncreaseDecreaseIn(\w+)"', r'name="xx:Untagged\1"', raw)


def test_a_one_time_check_that_cannot_run_keeps_the_flag():
    """CEG's document with no reconciliation line tagged for its nine months: the one-time check has nothing to run
    on for the offered quarter, so the candidate stays the steward's (W1-2's flag) and the note says why.
    """
    client = _cash_client("CEG")
    key = next(iter(client.docs))
    client.docs[key] = _without_lines(client.docs[key])
    c = _cash(client, "CEG")
    assert (c.tier, c.flags) == (Tier.FLAG, ["companyfacts-behind"])
    assert "the one-time check cannot run" in c.note


def test_only_the_registrants_own_facts_are_read():
    """With every undimensioned context removed only other registrants' (``LegalEntityAxis``) facts remain: nothing is
    read from the document and today's companyfacts candidate stands, flagged."""
    client = _cash_client("NEE", prior=True)
    for k, v in client.docs.items():
        client.docs[k] = re.sub(
            r"<xbrli:context\b.*?</xbrli:context>",
            lambda m: m.group(0) if "explicitMember" in m.group(0) else "",
            v,
            flags=re.S,
        )
    c = _cash(client, "NEE")
    assert c.tier is Tier.FLAG and c.event_date < date(2026, 9, 30)


# --- A3: the two debt-securities tags ---------------------------------------------------------------------------------


def _nvda(cfg: ExtractorConfig | None = None) -> ExtractedFact:
    folder, cik, acc, doc, filed, period = CASH["NVDA"]
    client = _Client(
        _cf(folder, "NVDA"),
        [("10-Q", acc, doc, filed, period)],
        {f"forms/{acc}/{doc}": _read(folder, "NVDA-10q.htm")},
    )
    return _cash(client, "NVDA", cfg)


def test_nvda_counts_its_current_debt_securities():
    """NVDA's balance sheet carries 34,143M of marketable securities under ``DebtSecuritiesCurrent`` beside 22,443M of
    cash and equivalents: 56,586M, the steward's figure (today 22,443M, the lone 46.9bn debt total quoted).
    """
    c = _nvda()
    assert (c.tier, c.flags) == (Tier.AUTO, [])
    assert (c.cash_usd, c.investments_usd) == (56_586_000_000, 34_143_000_000)
    assert "short-term investments $34143.0M (DebtSecuritiesCurrent)" in c.note
    assert _kind(c) == "auto"


def test_a3_dial_off_gives_w1_2s_flag():
    c = _nvda(_off())
    assert (c.tier, c.cash_usd) == (Tier.FLAG, 22_443_000_000)
    assert "DebtSecuritiesCurrent $34143.0M" in c.note


def _inst(end: str, val: float) -> dict:
    return {"end": end, "val": val, "filed": "2026-11-01", "form": "10-Q", "accn": "x"}


def test_a_debt_securities_tag_beside_its_afs_part_is_never_added_to_it():
    """IBIO tags its 31.2M of AFS debt securities under both tags: the largest is taken, never their sum."""
    facts = {
        "us-gaap": {
            "CashAndCashEquivalentsAtCarryingValue": {
                "units": {"USD": [_inst("2026-09-30", 45_435_000)]}
            },
            "AvailableForSaleSecuritiesDebtSecuritiesCurrent": {
                "units": {"USD": [_inst("2026-09-30", 31_219_000)]}
            },
            "DebtSecuritiesCurrent": {"units": {"USD": [_inst("2026-09-30", 31_200_000)]}},
        }
    }
    c = ex._cash_burn(facts, "", "10-Q", date(2026, 9, 30), ExtractorConfig())
    assert (c.cash_usd, c.investments_usd) == (45_435_000 + 31_219_000, 31_219_000)
    assert "overlapping tags are never summed" in c.note


# --- A6: the aggregate working-capital line -------------------------------------------------------------------------

_FEIM = (
    "burn_flags",
    39020,
    "0001185185-26-003983",
    "feim10q073126.htm",
    "2026-09-14",
    "2026-07-31",
)


def _feim(cfg: ExtractorConfig | None = None) -> ExtractedFact:
    folder, cik, acc, doc, filed, period = _FEIM
    raw = _read(folder, "FEIM-10q.htm")
    text = clean_filing_text(raw)
    facts = ex.extract_facts(
        _cf(folder, "FEIM"),
        text,
        text,
        tenq_ref=_url(cik, acc, doc),
        tenk_ref=_url(cik, acc, doc),
        tenq_date=date.fromisoformat(period),
        tenk_date=date.fromisoformat(period),
        tenq_raw=raw,
        tenq_filed=date.fromisoformat(filed),
        **({} if cfg is None else {"cfg": cfg}),
    )
    return next(f for f in facts if f.fact_type == "cash_burn")


def test_feims_aggregate_working_capital_line_no_longer_trips_but_its_commodity_line_does():
    c = _feim()
    assert c.tier is Tier.FLAG and c.flags == ["possible-one-time"]
    assert "IncreaseDecreaseInOperatingCapital" not in c.note
    assert (
        "possible-one-time: IncreaseDecreaseInCommodityContractAssetsAndLiabilities $4.0M, at least 0.7"
        in c.note
    )
    assert not any(
        "Net changes in operating assets and liabilities" in p.anchor for p in c.located_passages
    )


def test_a6_dial_off_names_both_lines():
    assert "IncreaseDecreaseInOperatingCapital $4.1M" in _feim(_off()).note


# --- the 52/53-week slack, now a dial -------------------------------------------------------------------------------


def test_the_ytd_start_slack_is_a_dial():
    """FUBO's 10-K year starts 3 days before its 10-Qs': derived across filings at the default 7, a raw year to date
    at 2."""
    cf = _cf("burn_flags", "FUBO")
    facts = cf["facts"]
    assert ex._quarter(facts, ExtractorConfig()).basis == "ytd-derived-across-filings"  # type: ignore[union-attr]
    assert ex._quarter(facts, ExtractorConfig(ytd_start_slack_days=2)).basis == "ytd-raw"  # type: ignore[union-attr]
