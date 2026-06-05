from __future__ import annotations

import json
from pathlib import Path

from ingest.edgar.submissions import form4_doc_url, form4_filings

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
