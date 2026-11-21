"""W7a: ``extract_purity_for_security``, a plain user's extract. It reads ONLY what the purity candidate needs.

A share count and cash/burn are platform values (``docs/PLATFORM_VALUES.md``): the steward decides them, a plain
user never sees an unverified extracted number. So a user's extract builds the purity candidate alone and never
fetches the values filing, the annual document or an exhibit. Offline, over the repo's extractor fixtures, with
the recording fake client of ``test_extract_newest_filing``: every request is checked.

- a domestic filer: submissions, companyfacts (purity's revenue test) and the latest 10-K; never the 10-Q;
- a 10-Q-only filer: submissions, companyfacts and the latest 10-Q;
- an annual (20-F/40-F) filer: submissions only, and the ``purity-not-covered`` empty reason;
- a company with no periodic or annual filing at all: submissions only, ``no-annual-filing`` (unchanged words).
"""

from __future__ import annotations

from domain.extraction import Tier
from ingest.edgar import extract as ex
from tests.ingest.test_extract_newest_filing import (
    _OLD_10Q,
    OKLO,
    FakeClient,
    _companyfacts,
    _doc_key,
    _subs,
    _text,
)

_SUBS_KEY = f"submissions/CIK{OKLO['cik']:010d}.json"
_CF_KEY = f"companyfacts/CIK{OKLO['cik']:010d}.json"
_TWENTY_F = ("20-F", "0001628280-26-099001", "okl-20f.htm", "2026-04-20", "2025-12-31")


def _only_purity(result: ex.ExtractionResult) -> ex.ExtractedFact:
    assert [f.fact_type for f in result.facts] == ["revenue_mix"]
    pu = result.facts[0]
    assert pu.tier is Tier.HUMAN and pu.value is None
    return pu


def test_a_domestic_filer_reads_submissions_companyfacts_and_the_10k_only():
    client = FakeClient(
        _companyfacts(OKLO["cik"]),
        _subs(OKLO["new_tenq"], OKLO["tenk"], OKLO["old_tenq"]),
        {
            OKLO["new_tenq"][1]: _text(OKLO["new_tenq_text"]),
            OKLO["tenk"][1]: _text(OKLO["tenk_text"]),
            OKLO["old_tenq"][1]: _OLD_10Q,
        },
    )
    result = ex.extract_purity_for_security(client, OKLO["cik"])
    pu = _only_purity(result)
    assert result.empty_reason is None and result.runway_empty_reason is None
    assert OKLO["tenk"][1].replace("-", "") in pu.source_ref
    assert sorted(client.json_reads) == sorted([_SUBS_KEY, _CF_KEY])
    assert client.text_reads == [_doc_key(OKLO["tenk"])]  # never the newer 10-Q (the values filing)


def test_a_10q_only_filer_falls_back_to_the_latest_10q():
    client = FakeClient(
        _companyfacts(OKLO["cik"]),
        _subs(OKLO["new_tenq"], OKLO["old_tenq"]),
        {OKLO["new_tenq"][1]: _text(OKLO["new_tenq_text"]), OKLO["old_tenq"][1]: _OLD_10Q},
    )
    pu = _only_purity(ex.extract_purity_for_security(client, OKLO["cik"]))
    assert OKLO["new_tenq"][1].replace("-", "") in pu.source_ref
    assert client.text_reads == [_doc_key(OKLO["new_tenq"])]


def test_an_annual_filer_is_purity_not_covered_after_reading_submissions_only():
    client = FakeClient({}, _subs(_TWENTY_F, OKLO["old_tenq"]), {})
    result = ex.extract_purity_for_security(client, OKLO["cik"])
    assert result.facts == [] and result.empty_reason == "purity-not-covered"
    assert result.runway_empty_reason is None
    assert client.json_reads == [_SUBS_KEY] and client.text_reads == []


def test_a_company_with_no_filing_at_all_keeps_the_no_annual_filing_reason():
    client = FakeClient({}, _subs(), {})
    result = ex.extract_purity_for_security(client, OKLO["cik"])
    assert result.facts == [] and result.empty_reason == "no-annual-filing"
    assert client.json_reads == [_SUBS_KEY] and client.text_reads == []
