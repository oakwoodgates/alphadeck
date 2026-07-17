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


# --- the Rule 10b5-1 checkbox (CAPTURE-ONLY — no detector reads it) ---


def _with_aff(value: str) -> str:
    """The sample filing with an `<aff10b5One>` element injected at DOCUMENT level (where the SEC puts it:
    right after </reportingOwner>, not on a transaction)."""
    return _XML.replace(
        "</reportingOwner>", f"</reportingOwner>\n  <aff10b5One>{value}</aff10b5One>"
    )


def test_aff10b5one_absent_is_UNKNOWN_never_false():
    """THE LOAD-BEARING NULL: the sample has no checkbox — the shape of every filing before the SEC's
    Dec-2022 amendments. Absent must parse to None (unknown), NEVER False: False would assert "this sale was
    discretionary" about a filing that never said so — inventing a fact (#3)."""
    for t in parse_form4(_XML):
        assert t["aff_10b5_1"] is None


def test_aff10b5one_checked_and_clear_parse_to_true_and_false():
    """1/true = a PRE-PLANNED trade (autopilot, ~no information); 0/false = discretionary (a real decision)."""
    for checked in ("1", "true"):
        assert all(t["aff_10b5_1"] is True for t in parse_form4(_with_aff(checked)))
    for clear in ("0", "false"):
        assert all(t["aff_10b5_1"] is False for t in parse_form4(_with_aff(clear)))


def test_aff10b5one_is_filing_level_stamped_on_every_row():
    """The element is on the ownership DOCUMENT, so it applies to every transaction the filing reports —
    including the sale AND the purchase in this multi-txn sample."""
    txns = parse_form4(_with_aff("1"))
    assert len(txns) == 2 and {t["txn_code"] for t in txns} == {"P", "S"}
    assert all(t["aff_10b5_1"] is True for t in txns)


def test_aff10b5one_garbage_value_is_unknown_not_a_guess():
    assert all(t["aff_10b5_1"] is None for t in parse_form4(_with_aff("maybe")))


def test_ingest_form4_stores_the_flag(db, security_id):
    """It reaches the column (tri-state preserved through the append)."""
    ingest_form4(db, security_id, _with_aff("1"), "acc-planned")
    ingest_form4(db, security_id, _XML, "acc-unknown")  # no checkbox -> NULL
    with db.cursor() as cur:
        cur.execute(
            "SELECT accession, aff_10b5_1 FROM fact_insider_txn WHERE security_id=%s",
            (security_id,),
        )
        got = {(r["accession"], r["aff_10b5_1"]) for r in cur.fetchall()}
    assert ("acc-planned", True) in got
    assert ("acc-unknown", None) in got  # unknown stays NULL, never False


def test_the_flag_changes_NO_signal_logic(security_id):
    """CAPTURE-ONLY, proved: insider_conviction reads code 'P' and nothing else, so a buy fires IDENTICALLY
    whether it was planned, discretionary, or unknown. This slice stores data; it does not touch the call.
    """
    from signals.insider_conviction import score

    def buy(aff):
        return [
            {
                "txn_code": "P",
                "valid_from": date(2026, 6, 1),
                "usd": 500_000.0,
                "insider_name": "Doe Jane",
                "insider_role": "Chief Executive Officer",
                "accession": "acc-1",
                "aff_10b5_1": aff,  # planned / discretionary / unknown — the detector never looks
            }
        ]

    events = [score(buy(a), security_id, date(2026, 6, 8)) for a in (True, False, None)]
    assert all(e is not None and e.fired for e in events)
    # identical scoring across all three states — the flag is inert on the call path
    assert len({(e.score, e.grade, e.kind, e.role) for e in events}) == 1


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
