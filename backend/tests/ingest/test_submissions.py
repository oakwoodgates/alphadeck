from __future__ import annotations

import json
from pathlib import Path

from ingest.edgar.submissions import form4_filings

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
