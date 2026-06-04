from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.edgar.form4 import parse_form4

_XML = (
    Path(__file__).resolve().parent.parent / "fixtures" / "edgar" / "form4_sample.xml"
).read_text(encoding="utf-8")


def test_parse_form4_extracts_transactions():
    txns = parse_form4(_XML)
    assert len(txns) == 2

    buy = next(t for t in txns if t["txn_code"] == "P")  # open-market purchase
    assert buy["shares"] == 10000
    assert buy["price"] == 21.0
    assert buy["usd"] == 210000.0
    assert buy["txn_date"] == date(2026, 6, 1)
    assert buy["insider_name"] == "Doe Jane"
    assert "Chief Executive Officer" in (buy["insider_role"] or "")

    assert any(t["txn_code"] == "S" for t in txns)  # the sale is parsed too; the detector filters
