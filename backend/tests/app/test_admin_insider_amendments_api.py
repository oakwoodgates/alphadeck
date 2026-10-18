"""R8-A — the Admin "Insider amendments" API on REAL filings (``tests/form4a_fixtures.py``): the panel's read
(pending loud with the evidence; decided / applied / inert quiet), and every action with its Undo — Ratify
void, Keep original, Link to candidate — plus the conflicts the operator can hit (409) and the effect each
decision has on what the call reads."""

from __future__ import annotations

from datetime import date, datetime, timezone

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from tests.form4a_fixtures import seed

FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
BASE = "/admin/insider-amendments"


def _accessions(db, sid, asof):
    rows = as_of(
        db,
        "fact_insider_txn",
        security_id=sid,
        asof=asof,
        known_at=FAR,
        tenant_id=DEFAULT_TENANT_ID,
    )
    return {r["accession"] for r in rows}


def _by_acc(body: dict) -> dict[str, tuple[str, dict]]:
    out = {}
    for bucket in ("pending", "decided", "applied", "inert"):
        for it in body[bucket]:
            out[it["accession"]] = (bucket, it)
    return out


def test_the_panel_buckets_by_what_the_operator_owes(client, db):
    for t in ("FLS", "ATCH", "RPAY", "TDG", "SKYT"):
        seed(db, t)
    body = client.get(BASE).json()
    got = _by_acc(body)
    assert body["total"] == 9
    # owed a decision: the FLS void, the TDG duplicates, the SKYT ambiguous link
    assert {a for a, (b, _) in got.items() if b == "pending"} == {
        "0001628280-26-037326",
        "0001352573-26-000011",
        "0001856680-26-000016",
    }
    assert {b for a, (b, _) in got.items() if a.startswith("0001493152-26-0446")} == {"applied"}
    assert got["0001104659-26-041960"][0] == "inert"  # RPAY clerical
    fls = got["0001628280-26-037326"][1]
    # the evidence the operator ratifies on: the footnote, and the original's rows beside the amendment's
    assert any("cancelled by the Reporting Person's broker" in f for f in fls["footnotes"])
    assert fls["amendment_rows"] == []
    assert [o["accession"] for o in fls["originals"]] == ["0001628280-26-035913"]
    assert fls["originals"][0]["role"] == "original"
    assert fls["originals"][0]["rows"][0]["txn_code"] == "P"
    assert fls["suppressing"] == ["0001628280-26-035913"]  # pending void suppresses meanwhile
    assert fls["touches_buy"] is True and fls["undo_id"] is None
    assert fls["filing_url"].endswith("/30625/000162828026037326/")
    tdg = got["0001352573-26-000011"][1]
    assert tdg["disposition"] == "duplicates"
    assert tdg["suppressing"] == ["0001352573-26-000009", "0001352573-26-000010"]
    assert {o["role"] for o in tdg["originals"]} == {"original", "voided"}
    skyt = got["0001856680-26-000016"][1]
    assert skyt["disposition"] == "ambiguous" and skyt["suppressing"] == []
    assert skyt["candidate_accessions"] == ["0001856680-26-000010", "0001856680-26-000013"]


def test_ratify_void_then_undo(client, db):
    sid = seed(db, "FLS")
    acc = "0001628280-26-037326"
    r = client.post(f"{BASE}/{sid}/{acc}/apply", json={"reason": "broker cancelled it"})
    assert r.status_code == 200
    body = r.json()
    assert (body["status"], body["verdict"]) == ("applied", "apply")
    assert body["undo_id"] and body["suppressing"] == ["0001628280-26-035913"]
    assert _by_acc(client.get(BASE).json())[acc][0] == "decided"
    assert client.post(f"{BASE}/{sid}/{acc}/apply").status_code == 409  # a verdict already stands
    undo = client.post(f"{BASE}/decisions/{body['undo_id']}/revoke")
    assert undo.status_code == 200 and undo.json()["status"] == "pending"
    assert (
        client.post(f"{BASE}/decisions/{body['undo_id']}/revoke").status_code == 404
    )  # undone already


def test_keep_original_restores_the_buy_and_undo_suppresses_it_again(client, db):
    sid = seed(db, "AZTA")
    acc, asof = "0001193125-25-282945", date(2025, 8, 15)
    assert "0000950170-25-107291" not in _accessions(db, sid, asof)  # pending void: suppressed
    r = client.post(
        f"{BASE}/{sid}/{acc}/keep", json={"reason": "only attaches a power of attorney"}
    )
    assert r.status_code == 200 and r.json()["status"] == "kept" and r.json()["suppressing"] == []
    assert "0000950170-25-107291" in _accessions(db, sid, asof)  # the CFO's buy STANDS
    client.post(f"{BASE}/decisions/{r.json()['undo_id']}/revoke")
    assert "0000950170-25-107291" not in _accessions(db, sid, asof)


def test_keep_overrules_an_auto_applied_correction(client, db):
    sid = seed(db, "ARBE")
    acc, asof = "0001213900-26-107326", date(2026, 10, 7)
    assert _accessions(db, sid, asof) == {
        acc
    }  # the corrected 50,000-share row replaced the original
    assert client.post(f"{BASE}/{sid}/{acc}/apply").status_code == 409  # already applies
    r = client.post(f"{BASE}/{sid}/{acc}/keep")
    assert r.status_code == 200 and r.json()["status"] == "kept"
    assert _accessions(db, sid, asof) == {"0001213900-26-106000"}


def test_link_to_candidate_then_apply_then_undo_in_order(client, db):
    sid = seed(db, "SKYT")
    acc, asof = "0001856680-26-000016", date(2026, 3, 31)
    assert client.post(f"{BASE}/{sid}/{acc}/apply").status_code == 409  # ambiguous: link first
    bad = client.post(f"{BASE}/{sid}/{acc}/link", json={"target_accession": "0001352573-26-000008"})
    assert bad.status_code == 409  # not one of its candidates
    r = client.post(f"{BASE}/{sid}/{acc}/link", json={"target_accession": "0001856680-26-000013"})
    assert r.status_code == 200
    linked = r.json()
    assert (linked["method"], linked["disposition"], linked["status"]) == (
        "operator",
        "partial",
        "pending",
    )
    applied = client.post(f"{BASE}/{sid}/{acc}/apply").json()
    assert applied["status"] == "applied" and "0001856680-26-000013" not in _accessions(
        db, sid, asof
    )
    link_id = next(d["id"] for d in applied["decisions"] if d["action"] == "link")
    assert client.post(f"{BASE}/decisions/{link_id}/revoke").status_code == 409  # verdict first
    client.post(f"{BASE}/decisions/{applied['undo_id']}/revoke")
    back = client.post(f"{BASE}/decisions/{link_id}/revoke").json()
    assert back["disposition"] == "ambiguous" and back["status"] == "pending"
    assert "0001856680-26-000013" in _accessions(db, sid, asof)


def test_nothing_to_ratify_on_an_inert_amendment(client, db):
    sid = seed(db, "RPAY")
    r = client.post(f"{BASE}/{sid}/0001104659-26-041960/apply")
    assert r.status_code == 409 and "clerical" in r.json()["detail"]
    assert client.post(f"{BASE}/{sid}/0000000000-00-000000/apply").status_code == 404
    assert client.post(f"{BASE}/{sid}/not-an-accession/apply").status_code == 422
