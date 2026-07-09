from __future__ import annotations

import json
from pathlib import Path

from ingest.edgar.submissions import filings_of, form4_doc_url, form4_filings, parse_identity

_SUBS = json.loads(
    (
        Path(__file__).resolve().parent.parent / "fixtures" / "edgar" / "cached_sample.json"
    ).read_text(encoding="utf-8")
)


def test_form4_filings_lists_form4s():
    filings = form4_filings(_SUBS)
    assert len(filings) == 1
    assert filings[0]["accession"] == "0001234567-26-000123"
    assert filings[0]["primary_doc"] == "doc4.xml"


def _subs_with_dates() -> dict:
    return {
        "filings": {
            "recent": {
                "form": ["10-Q"],
                "accessionNumber": ["0000723125-26-000047"],
                "primaryDocument": ["mu-10q.htm"],
                "filingDate": ["2026-06-25"],
                "reportDate": ["2026-05-28"],
            }
        }
    }


def test_filings_of_carries_the_report_date_distinct_from_filed():
    """The load-bearing distinction (the every-name "dual-class" mis-flag): ``filed`` is the FILING date,
    ``report_date`` the PERIOD OF REPORT — ~a month apart on a 10-Q. Both must ride."""
    f = filings_of(_subs_with_dates(), "10-Q")[0]
    assert f["filed"] == "2026-06-25"
    assert f["report_date"] == "2026-05-28"


def test_filings_of_defends_a_missing_report_date_array():
    subs = _subs_with_dates()
    del subs["filings"]["recent"]["reportDate"]
    assert filings_of(subs, "10-Q")[0]["report_date"] == ""  # defensive "", never a crash


def test_latest_filing_threads_the_period_of_report_not_the_filing_date():
    """THE WIRING REGRESSION the golden suite couldn't see (it tests the pure core with a hand-picked
    date): the live wrapper's date must be the PERIOD OF REPORT. With the FILING date, a cover's "as of"
    (always earlier) failed the shares currency gate on every name -> the universal "dual-class" lie.
    MU's real dates pin it: cover 06-17, filed 06-25, period 05-28."""
    from datetime import date

    from ingest.edgar.extract import _latest_filing

    class _FakeClient:
        def get_json(self, url: str, cache_key: str) -> dict:
            return _subs_with_dates()

        def get_text(self, url: str, cache_key: str) -> str:
            return "cover text"

    got = _latest_filing(_FakeClient(), 723125, "10-Q")  # type: ignore[arg-type]
    assert got is not None
    _url, _text, period = got
    assert period == date(2026, 5, 28)  # the PERIOD OF REPORT — not 2026-06-25 (filed)


def test_form4_doc_url_uses_raw_xml_not_xsl_render():
    # submissions gives the xsl-rendered path; we must fetch the raw ownership XML to parse it
    url = form4_doc_url("1773751", "0001773751-26-000086", "xslF345X06/wk-form4_1779828505.xml")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1773751/000177375126000086/wk-form4_1779828505.xml"
    )
    assert "xsl" not in url


# --- parse_identity: machine-parsed master identity from submissions (Workbench enrichment, Slice 1) ---


def test_parse_identity_active_reads_sector_and_exchange():
    ident = parse_identity(
        {
            "sicDescription": "Electric Services",
            "exchanges": ["NYSE", "OTC"],
            "tickers": ["OKLO"],
            "formerNames": [],
        }
    )
    assert ident.sector == "Electric Services"
    assert ident.exchange == "NYSE"  # first of exchanges
    assert ident.status == "active"  # a current ticker AND exchange present
    assert ident.former_names == []


def test_parse_identity_reads_filer_category():
    """The SEC filer `category` (a maturity/size tell) is surfaced; absent -> None (never invented). EDGAR joins
    multiple attributes with a literal "<br>" — those tags are stripped to a clean " · "-joined string (no raw
    markup ever reaches the chip)."""
    ident = parse_identity(
        {
            "sicDescription": "Semiconductors",
            "category": "Large accelerated filer",
            "tickers": ["MU"],
        }
    )
    assert ident.category == "Large accelerated filer"
    # the <br>-joined form (the live bug) → tags stripped, joined with " · "
    assert (
        parse_identity({"category": "Non-accelerated filer<br>Smaller reporting company"}).category
        == "Non-accelerated filer · Smaller reporting company"
    )
    # a leading <br> (the SCHMID/GOWell case) → no stray separator
    assert (
        parse_identity({"category": "<br>Emerging growth company"}).category
        == "Emerging growth company"
    )
    assert parse_identity({"sicDescription": "Semiconductors"}).category is None  # absent -> None


def test_parse_identity_no_listing_is_inactive():
    """No current ticker / exchange -> a listing-presence 'inactive' (a HEURISTIC, never a delisting verdict)."""
    ident = parse_identity({"sicDescription": "Blank Checks", "exchanges": [], "tickers": []})
    assert ident.status == "inactive"
    assert ident.exchange is None
    assert ident.sector == "Blank Checks"


def test_parse_identity_extracts_former_names_for_the_bridge():
    """formerNames is parsed (the rebrand history the identity-bridge slice will use) though unused today;
    a blank name is dropped."""
    ident = parse_identity(
        {
            "sicDescription": "Biological Products",
            "exchanges": ["Nasdaq"],
            "tickers": ["ATAI"],
            "formerNames": [
                {
                    "name": "Perception Neuroscience Holdings",
                    "from": "2018-01-01",
                    "to": "2021-05-15",
                },
                {"name": "", "from": "x", "to": "y"},
            ],
        }
    )
    assert ident.former_names == [
        {"name": "Perception Neuroscience Holdings", "from": "2018-01-01", "to": "2021-05-15"}
    ]


def test_parse_identity_tolerates_a_sparse_submissions():
    """A sparse/old submissions (missing keys) -> all-None/empty, status inactive — never raises."""
    ident = parse_identity({})
    assert (ident.sector, ident.exchange, ident.status, ident.former_names) == (
        None,
        None,
        "inactive",
        [],
    )
