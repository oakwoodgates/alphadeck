"""W3b — the share count and cash/burn cite the NEWER of the latest 10-Q and the latest 10-K (operator decision B1).

Offline, end to end through ``extract_for_security`` with a fake ``EdgarClient`` that serves the repo's extractor
fixtures (``tests/fixtures/sec_extractor``) and RECORDS every read, so the cited filing, the value, its event date,
the located passages AND the documents fetched are all checked on one run.

"The moment after the 10-K": the real companyfacts cut to the rows filed on or before the 10-K's filing date — what
data.sec.gov served the evening the 10-K appeared. The latest 10-Q is then the previous quarter's, whose text is
not among the fixtures; ``_OLD_10Q`` stands in for it (only its CITATION matters here: the old rule cited it).

"Newer" is judged on the public clock (CLAUDE.md invariant #1): each filing's acceptance when trusted
(``ingest.edgar.acceptance.resolve_public_acceptance``), else its filing date — never its period of report.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from domain.extraction import Tier
from ingest.edgar import extract as ex

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor"

# A stand-in for the previous quarter's 10-Q (not among the fixtures): no cover class counts, no statement tables.
_OLD_10Q = (
    "FORM 10-Q quarterly report (stand-in for the previous quarter). Shares outstanding as of the latest "
    "practicable date: see the cover. Cash and cash equivalents. Net cash used in operating activities."
)

# One row per filing: (form, accession, primary document, filed, period of report, SEC acceptanceDateTime).
OKLO = {
    "cik": 1849056,
    "tenk": ("10-K", "0001628280-26-018698", "oklo-20251231.htm", "2026-03-17", "2025-12-31"),
    "old_tenq": ("10-Q", "0001628280-25-051349", "oklo-20250930.htm", "2025-11-12", "2025-09-30"),
    "new_tenq": ("10-Q", "0001628280-26-034095", "oklo-20260331.htm", "2026-05-12", "2026-03-31"),
    "tenk_text": "OKLO-10k.txt",
    "new_tenq_text": "OKLO-10q.txt",
}
LEU = {
    "cik": 1065059,
    "tenk": ("10-K", "0001628280-26-007117", "leu-20251231.htm", "2026-02-11", "2025-12-31"),
    "old_tenq": ("10-Q", "0001628280-25-049844", "leu-20250930.htm", "2025-11-06", "2025-09-30"),
    "new_tenq": ("10-Q", "0001628280-26-030891", "leu-20260331.htm", "2026-05-06", "2026-03-31"),
    "tenk_text": "LEU-10k.txt",
    "new_tenq_text": "LEU-10q.txt",
}


def _accepted(filed: str) -> str:
    """A trusted listed acceptance: 16:05 ET on the filing date (EDGAR's list carries it as UTC)."""
    return f"{filed}T21:05:00.000Z"


def _subs(*rows: tuple[str, str, str, str, str], accepted: dict[str, str] | None = None) -> dict:
    """A submissions index, newest first like EDGAR's ``recent`` arrays."""
    rows_sorted = sorted(rows, key=lambda r: r[3], reverse=True)
    accepted = accepted or {}
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows_sorted],
                "accessionNumber": [r[1] for r in rows_sorted],
                "primaryDocument": [r[2] for r in rows_sorted],
                "filingDate": [r[3] for r in rows_sorted],
                "reportDate": [r[4] for r in rows_sorted],
                "acceptanceDateTime": [accepted.get(r[1], _accepted(r[3])) for r in rows_sorted],
            }
        }
    }


def _companyfacts(cik: int, filed_by: str | None = None) -> dict:
    """The fixture companyfacts, optionally cut to the rows filed on or before ``filed_by``."""
    cf = json.loads((_FX / f"CIK{cik:010d}.json").read_text(encoding="utf-8"))
    if filed_by is None:
        return cf
    cut = copy.deepcopy(cf)
    for concepts in cut["facts"].values():
        for node in concepts.values():
            for unit, rows in node["units"].items():
                node["units"][unit] = [r for r in rows if r.get("filed", "") <= filed_by]
    return cut


class FakeClient:
    """Serves companyfacts, submissions and primary documents by cache key; records every read."""

    def __init__(self, cf: dict, subs: dict, docs: dict[str, str]) -> None:
        self._cf, self._subs, self._docs = cf, subs, docs
        self.json_reads: list[str] = []
        self.text_reads: list[str] = []

    def get_json(self, url: str, cache_key: str, **_: Any) -> dict:
        self.json_reads.append(cache_key)
        if cache_key.startswith("companyfacts/"):
            return self._cf
        if cache_key.startswith("submissions/"):
            return self._subs
        raise AssertionError(f"unexpected JSON read {cache_key}")

    def get_text(self, url: str, cache_key: str, **_: Any) -> str:
        self.text_reads.append(cache_key)
        accession = cache_key.split("/")[1]
        assert accession in url.replace("-", "") or accession.replace("-", "") in url
        return self._docs[accession]


def _doc_key(row: tuple[str, str, str, str, str]) -> str:
    return f"forms/{row[1]}/{row[2]}"


def _by_type(facts: list) -> dict:
    return {f.fact_type: f for f in facts}


def _cites(fact: Any, row: tuple[str, str, str, str, str]) -> bool:
    """The candidate's link AND every located passage point at ``row``'s filing."""
    acc = row[1].replace("-", "")
    return acc in fact.source_ref and all(acc in p.source_ref for p in fact.located_passages)


def _passages_from(fact: Any, text: str) -> bool:
    """Every located passage is an excerpt of ``text`` (the cited document, cleaned like the live path)."""
    clean = " ".join(ex.clean_filing_text(text).split())
    return all(p.excerpt.strip("… ").strip() in clean for p in fact.located_passages)


def _text(name: str) -> str:
    return (_FX / name).read_text(encoding="utf-8")


# ----------------------------------------------------------------------------------------------------------------
# 1. The 10-K is newer than the latest 10-Q (the moment after a 10-K): everything comes from the 10-K
# ----------------------------------------------------------------------------------------------------------------


def _tenk_newer(s: dict, *, cf: dict | None = None) -> tuple[FakeClient, list]:
    client = FakeClient(
        cf or _companyfacts(s["cik"], filed_by=s["tenk"][3]),
        _subs(s["tenk"], s["old_tenq"]),
        {s["tenk"][1]: _text(s["tenk_text"]), s["old_tenq"][1]: _OLD_10Q},
    )
    return client, ex.extract_for_security(client, s["cik"])


def test_a_newer_10k_is_the_cited_filing_for_the_share_count():
    client, facts = _tenk_newer(OKLO)
    sh = _by_type(facts)["shares_outstanding"]
    assert _cites(sh, OKLO["tenk"]), sh.source_ref
    # the value is the 10-K cover's own row: 173,568,788 as of 2026-03-13, accession 0001628280-26-018698
    assert sh.value == 173_568_788 and sh.event_date == date(2026, 3, 13)
    assert sh.tier is Tier.AUTO
    assert sh.located_passages and _passages_from(sh, _text(OKLO["tenk_text"]))


def test_a_newer_10k_is_the_cited_filing_for_cash_and_burn():
    client, facts = _tenk_newer(OKLO)
    cb = _by_type(facts)["cash_burn"]
    assert _cites(cb, OKLO["tenk"]), cb.source_ref
    # the annual column, honestly derived: FY 2025 (-82,174,000) minus the nine-month YTD (-48,745,000)
    assert cb.quarterly_burn_usd == 33_429_000
    assert "derived (YTD − prior YTD)" in cb.note
    assert cb.event_date == date(2025, 12, 31)
    assert cb.tier is Tier.AUTO and cb.flags == []
    # the 10-K fixture is the document's head (no statements), so nothing may be located; whatever is
    # located must be the 10-K's own text (``_cites`` checks every passage's link)
    assert _passages_from(cb, _text(OKLO["tenk_text"]))


def test_a_newer_10k_with_an_underivable_annual_column_stays_flagged():
    """No prior same-start YTD on file: the full-year figure is NOT a quarter. It must stay a ``ytd-raw`` FLAG
    when the 10-K is the cited filing — never an AUTO clean quarter."""
    cf = _companyfacts(OKLO["cik"], filed_by=OKLO["tenk"][3])
    rows = cf["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"]
    cf["facts"]["us-gaap"]["NetCashProvidedByUsedInOperatingActivities"]["units"]["USD"] = [
        r for r in rows if not (r.get("start") == "2025-01-01" and r["end"] < "2025-12-31")
    ]
    client, facts = _tenk_newer(OKLO, cf=cf)
    cb = _by_type(facts)["cash_burn"]
    assert _cites(cb, OKLO["tenk"]), cb.source_ref
    assert cb.tier is Tier.FLAG and "ytd-raw" in cb.flags
    assert cb.quarterly_burn_usd == 82_174_000  # the raw full year, named as such
    assert "NOT a quarter" in cb.note


def test_a_newer_10k_for_a_dual_class_filer_reads_its_own_cover():
    """LEU reports its classes only on the cover: the A+B sum is read from the CITED filing's cover."""
    client, facts = _tenk_newer(LEU)
    sh = _by_type(facts)["shares_outstanding"]
    assert _cites(sh, LEU["tenk"]), sh.source_ref
    assert sh.tier is Tier.FLAG and sh.flags == ["dual-class"]
    assert sh.value is not None
    assert _passages_from(sh, _text(LEU["tenk_text"]))


def test_a_newer_10k_is_fetched_once_and_the_old_10q_not_at_all():
    client, _ = _tenk_newer(OKLO)
    assert client.text_reads == [_doc_key(OKLO["tenk"])]
    assert client.json_reads.count(f"submissions/CIK{OKLO['cik']:010d}.json") == 1


def test_purity_keeps_reading_the_10k():
    _, facts = _tenk_newer(OKLO)
    pu = _by_type(facts)["revenue_mix"]
    assert _cites(pu, OKLO["tenk"]) and pu.tier is Tier.HUMAN and pu.value is None


# ----------------------------------------------------------------------------------------------------------------
# 2. The 10-Q is newer (the golden fixtures as served): unchanged — the 10-Q for values, the 10-K for purity
# ----------------------------------------------------------------------------------------------------------------


def _tenq_newer(s: dict, **kw: Any) -> tuple[FakeClient, list]:
    client = FakeClient(
        _companyfacts(s["cik"]),
        _subs(s["new_tenq"], s["tenk"], s["old_tenq"]),
        {
            s["new_tenq"][1]: _text(s["new_tenq_text"]),
            s["tenk"][1]: _text(s["tenk_text"]),
            s["old_tenq"][1]: _OLD_10Q,
        },
    )
    return client, ex.extract_for_security(client, s["cik"], **kw)


def test_a_newer_10q_stays_the_cited_filing():
    client, facts = _tenq_newer(OKLO)
    by = _by_type(facts)
    sh, cb, pu = by["shares_outstanding"], by["cash_burn"], by["revenue_mix"]
    assert _cites(sh, OKLO["new_tenq"]) and _cites(cb, OKLO["new_tenq"])
    assert sh.tier is Tier.AUTO and sh.value == 173_990_987 and sh.event_date == date(2026, 5, 7)
    assert cb.tier is Tier.AUTO and cb.quarterly_burn_usd == 17_867_000
    assert cb.event_date == date(2026, 3, 31)
    assert _passages_from(sh, _text(OKLO["new_tenq_text"]))
    assert _cites(pu, OKLO["tenk"])  # purity keeps its source
    # both documents read (values + purity), the submissions index once
    assert sorted(client.text_reads) == sorted([_doc_key(OKLO["new_tenq"]), _doc_key(OKLO["tenk"])])
    assert client.json_reads.count(f"submissions/CIK{OKLO['cik']:010d}.json") == 1


def test_values_only_with_a_newer_10q_never_fetches_the_10k():
    client, facts = _tenq_newer(OKLO, values_only=True)
    assert client.text_reads == [_doc_key(OKLO["new_tenq"])]
    by = _by_type(facts)
    assert _cites(by["shares_outstanding"], OKLO["new_tenq"])
    assert _cites(by["cash_burn"], OKLO["new_tenq"])
    # the purity candidate is built from the values document (the machine writer ignores purity)
    assert _cites(by["revenue_mix"], OKLO["new_tenq"]) and by["revenue_mix"].value is None


def test_values_only_with_a_newer_10k_reads_only_the_10k():
    s = OKLO
    client = FakeClient(
        _companyfacts(s["cik"], filed_by=s["tenk"][3]),
        _subs(s["tenk"], s["old_tenq"]),
        {s["tenk"][1]: _text(s["tenk_text"]), s["old_tenq"][1]: _OLD_10Q},
    )
    facts = ex.extract_for_security(client, s["cik"], values_only=True)
    assert client.text_reads == [_doc_key(s["tenk"])]
    assert all(_cites(f, s["tenk"]) for f in facts)


# ----------------------------------------------------------------------------------------------------------------
# 3. "Newer" is the PUBLIC clock, not the period of report
# ----------------------------------------------------------------------------------------------------------------


def test_newer_is_judged_by_publication_not_by_period():
    """A late 10-K (period 2025-12-31) made public AFTER a 10-Q whose period is later (2026-03-31) is the newer
    filing: the order is when each became public, never which period it covers.

    This pins the ORDER only. In this disagreement (a filing made public after one covering a later period) the
    numbers are still companyfacts' newest rows, which may belong to the other filing — a limitation the
    extractor had before B1 in the mirror case (it is not resolved here; see the W3b report)."""
    s = OKLO
    late_tenk = ("10-K", s["tenk"][1], s["tenk"][2], "2026-05-20", "2025-12-31")
    client = FakeClient(
        _companyfacts(s["cik"]),
        _subs(late_tenk, s["new_tenq"]),
        {s["tenk"][1]: _text(s["tenk_text"]), s["new_tenq"][1]: _text(s["new_tenq_text"])},
    )
    sh = _by_type(ex.extract_for_security(client, s["cik"]))["shares_outstanding"]
    assert _cites(sh, late_tenk)


def test_same_filing_date_goes_to_the_later_acceptance():
    """Both filed the same day: the trusted acceptance times order them (an after-17:30 filing rolls to the next
    business day's filing date, so the acceptance is the public instant, not the date)."""
    s = OKLO
    q = ("10-Q", s["new_tenq"][1], s["new_tenq"][2], "2026-05-12", "2026-03-31")
    k = ("10-K", s["tenk"][1], s["tenk"][2], "2026-05-12", "2025-12-31")
    docs = {s["tenk"][1]: _text(s["tenk_text"]), s["new_tenq"][1]: _text(s["new_tenq_text"])}
    cf = _companyfacts(s["cik"])
    k_later = FakeClient(
        cf,
        _subs(q, k, accepted={q[1]: "2026-05-12T14:00:00.000Z", k[1]: "2026-05-12T19:00:00.000Z"}),
        docs,
    )
    q_later = FakeClient(
        cf,
        _subs(q, k, accepted={q[1]: "2026-05-12T19:00:00.000Z", k[1]: "2026-05-12T14:00:00.000Z"}),
        docs,
    )
    assert _cites(_by_type(ex.extract_for_security(k_later, s["cik"]))["cash_burn"], k)
    assert _cites(_by_type(ex.extract_for_security(q_later, s["cik"]))["cash_burn"], q)


# ----------------------------------------------------------------------------------------------------------------
# 4. One form only: that form, fetched once
# ----------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("which", ["tenk", "new_tenq"])
def test_a_single_form_is_cited_for_every_role_and_fetched_once(which: str):
    s = OKLO
    row = s[which]
    text = _text(s["tenk_text"] if which == "tenk" else s["new_tenq_text"])
    client = FakeClient(_companyfacts(s["cik"], filed_by=row[3]), _subs(row), {row[1]: text})
    facts = ex.extract_for_security(client, s["cik"])
    assert len(facts) == 3 and all(_cites(f, row) for f in facts)
    assert client.text_reads == [_doc_key(row)]
