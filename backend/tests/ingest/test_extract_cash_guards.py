"""The cash-composition guards (W1-AUTO follow-up, 2026-11-26), each behind its own ``ExtractorConfig`` dial
(``docs/PLATFORM_EXTRACTION.md``, "What the machine applies from a filing's own inline XBRL"):

- ``investments_equity_guard``: an investment tag of ``_STI`` / ``_LT_MARKETABLE`` whose value equals a same-dated
  equity-securities balance of the registrant (``_EQUITY_SECURITIES``) is not counted; the note names both. CNP's
  "Investment in equity securities" (AT&T, Charter and WBD stock held against its exchangeable notes) is tagged
  ``MarketableSecuritiesCurrent`` 425M and, the same day, ``EquitySecuritiesFvNiCurrentAndNoncurrent`` 425M: cash is
  132M, not 557M.
- ``investments_statement_guard``: given the raw document, a counted investment tagged outside the statement tables
  (those holding the period-end cash fact or a balance-sheet total on the cash date) and only in a deferred-tax table
  is a tax-note line: not counted, the candidate flagged ``investments-unconfirmed``. LEXX's
  ``MarketableSecuritiesNoncurrent`` 349,120 is the "Marketable securities" row of its deferred-tax table: 4,737,396
  is offered, flagged. Narrowed from "anywhere outside the statements", which on the fork's applied population also
  moved debt securities filers tag only in an investments note or in prose (13 of 100 replayed went to FLAG).

Balance-sheet securities stay counted: NVDA's "Marketable debt securities" and XNCR's short- and long-term ones. The
fixtures (``tests/fixtures/sec_extractor/cash_guards/``, README there) are cut from the real documents keeping each
row's own table; on the code before the guards each gives the stored AUTO candidate.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from domain.config import ExtractorConfig
from domain.extraction import ExtractedFact, ExtractionResult, Tier
from ingest.edgar import extract as ex
from ingest.edgar.converts import clean_filing_text
from platform_values.machine import plan

_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor" / "cash_guards"
_DAY = date(2026, 11, 26)
_UNCONFIRMED = "investments-unconfirmed"

# (cik, [(form, accession, primary document, filed, period)], {document cache key: fixture file}) per company; the
# first row is the cited filing, a second one its previous 10-Q (CNP: companyfacts is two quarters behind)
_FILINGS: dict[str, tuple[int, list[tuple[str, str, str, str, str]], list[str]]] = {
    "CNP": (
        1130310,
        [
            ("10-Q", "0001130310-26-000048", "cnp-20260930.htm", "2026-10-27", "2026-09-30"),
            ("10-Q", "0001130310-26-000041", "cnp-20260630.htm", "2026-07-28", "2026-06-30"),
        ],
        ["CNP-10q.htm", "CNP-q2-10q.htm"],
    ),
    "LEXX": (
        1348362,
        [("10-K", "0001640334-26-001880", "lxrp_10k.htm", "2026-11-25", "2026-08-31")],
        ["LEXX-10k.htm"],
    ),
    "XNCR": (
        1326732,
        [("10-Q", "0001326732-26-000063", "xncr-20260930.htm", "2026-11-04", "2026-09-30")],
        ["XNCR-10q.htm"],
    ),
    "NVDA": (
        1045810,
        [("10-Q", "0001045810-26-000075", "nvda-20260726.htm", "2026-08-26", "2026-07-26")],
        ["NVDA-10q.htm"],
    ),
}


def _read(name: str) -> str:
    return (_DIR / name).read_text(encoding="utf-8")


def _cf(ticker: str) -> dict:
    return json.loads(_read(f"{ticker}-cf.json"))


class _Client:
    """Serves companyfacts, a submissions index of ``rows`` (newest first) and the raw documents by cache key."""

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


def _client(ticker: str) -> _Client:
    _, rows, files = _FILINGS[ticker]
    docs = {f"forms/{r[1]}/{r[2]}": _read(f) for r, f in zip(rows, files, strict=True)}
    return _Client(_cf(ticker), rows, docs)


def _cash(
    ticker: str, cfg: ExtractorConfig | None = None, client: _Client | None = None
) -> ExtractedFact:
    """The live wrapper's cash candidate (``extract_for_security``, as background extraction calls it)."""
    kw = {} if cfg is None else {"cfg": cfg}
    facts = ex.extract_for_security(
        client or _client(ticker), _FILINGS[ticker][0], values_only=True, **kw  # type: ignore[arg-type]
    )
    return next(f for f in facts if f.fact_type == "cash_burn")


def _text_only(ticker: str) -> ExtractedFact:
    """The pure core with no raw document (a text-only caller): the statement guard cannot run."""
    _, rows, files = _FILINGS[ticker]
    form, _, _, _, period = rows[0]
    text = clean_filing_text(_read(files[0]))
    pe = date.fromisoformat(period)
    facts = ex.extract_facts(
        _cf(ticker),
        text,
        text,
        tenq_ref="https://x/doc.htm",
        tenk_ref="https://x/doc.htm",
        tenq_date=pe,
        tenk_date=pe,
        values_form=form,
    )
    return next(f for f in facts if f.fact_type == "cash_burn")


def _kind(fact: ExtractedFact) -> str:
    return plan(ExtractionResult(facts=[fact]), fact.fact_type, today=_DAY).kind


# --- (a) an investment tag equal to a same-dated equity-securities balance -----------------------------------------


def test_cnp_equity_securities_tagged_as_marketable_securities_are_not_counted():
    """CNP (companyfacts two quarters behind, so the figures are the Q3 document's own): its balance sheet's
    "Investment in equity securities" 425M is tagged ``MarketableSecuritiesCurrent``, and the same 425M is tagged
    ``EquitySecuritiesFvNiCurrentAndNoncurrent`` on the same date. Before: 557M applied. After: 132M, still applied
    (the burn and every other check unchanged), and the note says why the 425M is out.
    """
    c = _cash("CNP")
    assert (c.cash_usd, c.investments_usd) == (132_000_000, None)
    assert (c.tier, c.flags, c.quarterly_burn_usd) == (Tier.AUTO, [], -991_000_000)
    assert (
        "not counted: MarketableSecuritiesCurrent $425.0M equals the same-dated equity securities "
        "EquitySecuritiesFvNiCurrentAndNoncurrent $425.0M" in c.note
    )
    assert _kind(c) == "auto"


def test_cnp_with_the_equity_guard_off_counts_the_equity_stake_as_before():
    c = _cash("CNP", ExtractorConfig(investments_equity_guard=False))
    assert (c.tier, c.cash_usd, c.investments_usd) == (Tier.AUTO, 557_000_000, 425_000_000)


def _inst(end: str, val: float) -> dict:
    return {"end": end, "val": val, "accn": "x", "form": "10-Q", "filed": "2026-11-01"}


def _synthetic(sti: float, equity: float | None, *, equity_end: str = "2026-09-30") -> dict:
    us: dict[str, Any] = {
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_inst("2026-09-30", 100.0)]}},
        "ShortTermInvestments": {"units": {"USD": [_inst("2026-09-30", sti)]}},
    }
    if equity is not None:
        us["EquitySecuritiesFvNi"] = {"units": {"USD": [_inst(equity_end, equity)]}}
    return {"facts": {"us-gaap": us}}


def _companyfacts_only(cf: dict, cfg: ExtractorConfig | None = None) -> ExtractedFact:
    pe = date(2026, 9, 30)
    kw = {} if cfg is None else {"cfg": cfg}
    facts = ex.extract_facts(
        cf, "", "", tenq_ref="u", tenk_ref="u", tenq_date=pe, tenk_date=pe, **kw
    )
    return next(f for f in facts if f.fact_type == "cash_burn")


def test_the_equity_guard_reads_companyfacts_alone_and_only_on_equality():
    """No raw document needed: companyfacts carries the registrant's equity balances. Only an equal, same-dated,
    non-zero value is the same line; a different value or another date is not evidence."""
    assert _companyfacts_only(_synthetic(40.0, 40.0)).cash_usd == 100.0
    assert _companyfacts_only(_synthetic(40.0, 41.0)).cash_usd == 140.0
    assert _companyfacts_only(_synthetic(40.0, 40.0, equity_end="2026-06-30")).cash_usd == 140.0
    zero = _companyfacts_only(_synthetic(0.0, 0.0))
    assert (zero.cash_usd, zero.investments_usd) == (
        100.0,
        0.0,
    )  # a zero changes nothing: not "excluded"
    off = ExtractorConfig(investments_equity_guard=False)
    assert _companyfacts_only(_synthetic(40.0, 40.0), off).cash_usd == 140.0


# --- (b) a counted investment presented outside the statements ------------------------------------------------------


def test_lexx_deferred_tax_marketable_securities_are_not_counted_and_flagged():
    """LEXX's 10-K: cash 4,594,563 and short-term investments 142,833 on the balance sheet; the 349,120 tagged
    ``MarketableSecuritiesNoncurrent`` is the "Marketable securities" row of the income-tax note's deferred-tax table,
    on no statement. Before: 5,086,516 applied. After: 4,737,396 offered, flagged ``investments-unconfirmed``.
    """
    c = _cash("LEXX")
    assert (c.cash_usd, c.investments_usd) == (4_737_396, 142_833)
    assert (c.tier, c.flags) == (Tier.FLAG, [_UNCONFIRMED])
    assert c.quarterly_burn_usd == -1_043_233  # the burn is untouched
    assert (
        "not counted: MarketableSecuritiesNoncurrent $0.3M is tagged in the filing only outside the statement "
        "tables that present its cash" in c.note
    )
    assert _kind(c) != "auto"


def test_lexx_with_the_statement_guard_off_or_no_raw_document_is_applied_as_before():
    before = (Tier.AUTO, 5_086_516, 491_953)
    c = _cash("LEXX", ExtractorConfig(investments_statement_guard=False))
    assert (c.tier, c.cash_usd, c.investments_usd) == before
    t = _text_only("LEXX")
    assert (t.tier, t.cash_usd, t.investments_usd) == before


def test_a_document_with_no_statement_table_cannot_run_the_statement_guard():
    """Statements laid out without ``<table>`` (the cash fact in no table): nothing to place the fact in, so the
    guard does not run and the candidate is the one before it."""
    client = _client("LEXX")
    key = next(iter(client.docs))
    client.docs[key] = client.docs[key].replace("<table>", "<div>").replace("</table>", "</div>")
    c = _cash("LEXX", client=client)
    assert (c.tier, c.cash_usd) == (Tier.AUTO, 5_086_516)


def test_an_investment_tagged_only_in_another_note_stays_counted():
    """The narrowing: LEXX's document with its deferred-tax facts renamed, so the 349,120 row sits in an ordinary note
    table. A filer often tags debt securities only in its investments note while the balance sheet prints them inside
    a broader line; that is not evidence of a non-cash line, so the value counts as before."""
    client = _client("LEXX")
    key = next(iter(client.docs))
    client.docs[key] = client.docs[key].replace(
        'name="us-gaap:DeferredTax', 'name="xx:NotDeferredTax'
    )
    c = _cash("LEXX", client=client)
    assert (c.tier, c.cash_usd) == (Tier.AUTO, 5_086_516)


# --- balance-sheet securities stay counted --------------------------------------------------------------------------


def test_nvda_marketable_debt_securities_on_its_balance_sheet_stay_counted():
    """NVDA: "Marketable debt securities" 34,143M (``DebtSecuritiesCurrent``) sits in the balance sheet beside cash
    22,443M; "Marketable equity securities" 42,783M (``EquitySecuritiesFvNi``) was never read and equals nothing
    counted."""
    c = _cash("NVDA")
    assert (c.tier, c.flags) == (Tier.AUTO, [])
    assert (c.cash_usd, c.investments_usd) == (56_586_000_000, 34_143_000_000)
    assert "not counted: DebtSecuritiesCurrent" not in c.note


def test_xncr_short_and_long_term_marketable_debt_securities_stay_counted():
    """XNCR: 59,622K cash + 318,487K (``DebtSecuritiesCurrent``) + 177,581K (``DebtSecuritiesNoncurrent``), both on
    the balance sheet; its 26,970K of equity securities is a separate line."""
    c = _cash("XNCR")
    assert (c.tier, c.flags) == (Tier.AUTO, [])
    assert (c.cash_usd, c.investments_usd) == (555_690_000, 496_068_000)
    assert _kind(c) == "auto"


def test_the_guards_are_on_by_default():
    cfg = ExtractorConfig()
    assert (cfg.investments_equity_guard, cfg.investments_statement_guard) == (True, True)
