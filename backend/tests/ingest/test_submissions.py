from __future__ import annotations

import json
from pathlib import Path

from ingest.edgar.submissions import form4_doc_url, form4_filings, parse_identity

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
