from __future__ import annotations

from datetime import date
from pathlib import Path

from ingest.edgar.form4 import existing_accessions, ingest_form4, parse_form4

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


def test_existing_accessions_is_distinct_set(db, security_id):
    assert existing_accessions(db, security_id) == set()  # nothing stored yet
    # two filings (4 rows total) — the helper returns the DISTINCT accessions, not the row count
    ingest_form4(db, security_id, _XML, "0000000000-26-000001")
    ingest_form4(db, security_id, _XML, "0000000000-26-000002")
    db.commit()
    assert existing_accessions(db, security_id) == {
        "0000000000-26-000001",
        "0000000000-26-000002",
    }
