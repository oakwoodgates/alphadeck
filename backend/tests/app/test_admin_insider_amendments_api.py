"""R8-A / R8-B — the Admin "Insider amendments" API on REAL filings (``tests/form4a_fixtures.py``): the panel's
read — OWED loud (only an ambiguous amendment the system cannot resolve that touches a buy/sell a call can
read), then the quiet buckets: applied automatically (the filer's own correction — each with its evidence and
its Undo, Keep original), decided, unresolved — historical, queued, inert — and every action with its Undo,
plus the conflicts the operator can hit (409) and the effect each decision has on what the call reads.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from db.bitemporal import as_of
from db.session import DEFAULT_TENANT_ID
from tests.form4a_fixtures import ingest_amendment, ingest_originals, make_security, seed

FAR = datetime(2100, 1, 1, tzinfo=timezone.utc)
BASE = "/admin/insider-amendments"
BUCKETS = ("owed", "auto", "decided", "historical", "queued", "inert")


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


def _codes_of(db, sid, asof, accession) -> set[str]:
    rows = as_of(
        db,
        "fact_insider_txn",
        security_id=sid,
        asof=asof,
        known_at=FAR,
        tenant_id=DEFAULT_TENANT_ID,
    )
    return {r["txn_code"] for r in rows if r["accession"] == accession}


def _by_acc(body: dict) -> dict[str, tuple[str, dict]]:
    out = {}
    for bucket in BUCKETS:
        for it in body[bucket]:
            out[it["accession"]] = (bucket, it)
    return out


def _pin_today(monkeypatch, day: date) -> None:
    from app.routers import admin_insider_amendments as router_mod

    monkeypatch.setattr(router_mod, "market_today", lambda: day)


def test_the_panel_buckets_by_what_the_operator_owes(client, db, monkeypatch):
    _pin_today(monkeypatch, date(2026, 10, 20))
    for t in ("FLS", "ATCH", "RPAY", "TDG", "SKYT"):
        seed(db, t)
    eltp = make_security(db, "ELTP")
    ingest_originals(db, eltp, "ELTP")
    ingest_amendment(db, eltp, "0001465825-12-000008")
    body = client.get(BASE).json()
    got = _by_acc(body)
    assert body["total"] == 10 and body["review_horizon_days"] == 940
    assert set(BUCKETS) <= set(body) and "pending" not in body and "applied" not in body
    # owed: ONLY the SKYT ambiguous link (its candidates' sales sit inside the review horizon)
    assert {a for a, (b, _) in got.items() if b == "owed"} == {"0001856680-26-000016"}
    # applied automatically: the FLS void, the TDG duplicates, ATCH's five corrections
    auto = {a for a, (b, _) in got.items() if b == "auto"}
    assert auto == {"0001628280-26-037326", "0001352573-26-000011"} | {
        a for a in got if a.startswith("0001493152-26-0446")
    }
    assert got["0001465825-12-000008"][0] == "historical"  # ELTP: a 2012 sale no call reads
    assert got["0001104659-26-041960"][0] == "inert"  # RPAY clerical
    fls = got["0001628280-26-037326"][1]
    assert (fls["status"], fls["review"], fls["system_basis"], fls["auto_resolved"]) == (
        "applied",
        "auto",
        "system:filer-correction",
        True,
    )
    # the Undo of a system application is Keep original — no revoke target is advertised for it
    assert fls["undo_id"] is None
    # the evidence beside it: the footnote, and the original's rows beside the amendment's
    assert any("cancelled by the Reporting Person's broker" in f for f in fls["footnotes"])
    assert fls["amendment_rows"] == []
    assert [o["accession"] for o in fls["originals"]] == ["0001628280-26-035913"]
    assert fls["originals"][0]["role"] == "original"
    assert fls["originals"][0]["rows"][0]["txn_code"] == "P"
    assert fls["suppressing"] == ["0001628280-26-035913"]
    assert fls["filing_url"].endswith("/30625/000162828026037326/")
    system = next(d for d in fls["decisions"] if d["standing"])
    assert (system["action"], system["basis"]) == ("apply", "system:filer-correction")
    tdg = got["0001352573-26-000011"][1]
    assert tdg["disposition"] == "duplicates" and tdg["review"] == "auto"
    assert tdg["suppressing"] == ["0001352573-26-000009", "0001352573-26-000010"]
    skyt = got["0001856680-26-000016"][1]
    assert (skyt["disposition"], skyt["suppressing"], skyt["in_review"]) == ("ambiguous", [], True)
    assert skyt["candidate_accessions"] == ["0001856680-26-000010", "0001856680-26-000013"]
    eltp_item = got["0001465825-12-000008"][1]
    assert eltp_item["in_review"] is False and eltp_item["signal_effect"] is True


def test_keep_original_is_the_undo_of_a_system_application_and_undo_restores_it(client, db):
    """FLS: the system applied the void (the canceled buy suppressed). Keep original restores the buy; its
    Undo restores the system's application — the exact inverse pair (WB#1)."""
    sid = seed(db, "FLS")
    acc, asof = "0001628280-26-037326", date(2026, 6, 15)
    assert "0001628280-26-035913" not in _accessions(db, sid, asof)
    assert (
        client.post(f"{BASE}/{sid}/{acc}/add").status_code == 409
    )  # a zero-row 4/A has no rows to add
    r = client.post(f"{BASE}/{sid}/{acc}/keep", json={"reason": "not canceled"})
    assert r.status_code == 200
    kept = r.json()
    assert (kept["status"], kept["review"], kept["suppressing"]) == ("kept", "decided", [])
    assert kept["undo_id"] and "0001628280-26-035913" in _accessions(db, sid, asof)
    assert _by_acc(client.get(BASE).json())[acc][0] == "decided"
    assert client.post(f"{BASE}/{sid}/{acc}/keep").status_code == 409  # an operator verdict stands
    undo = client.post(f"{BASE}/decisions/{kept['undo_id']}/revoke")
    assert undo.status_code == 200
    back = undo.json()
    assert (back["status"], back["review"], back["system_basis"]) == (
        "applied",
        "auto",
        "system:filer-correction",
    )
    assert "0001628280-26-035913" not in _accessions(db, sid, asof)
    assert (
        client.post(f"{BASE}/decisions/{kept['undo_id']}/revoke").status_code == 404
    )  # undone already


def test_azta_void_is_kept_as_filed_and_use_the_correction_hides_the_buy(client, db):
    """AZTA's zero-row 4/A only attaches a power of attorney: the system keeps the original (no retraction
    words) and lists it quietly with Use the correction as its inverse — which hides the buy; Undo restores it.
    """
    sid = seed(db, "AZTA")
    acc, asof = "0001193125-25-282945", date(2025, 8, 15)
    assert "0000950170-25-107291" in _accessions(db, sid, asof)  # the CFO's buy STANDS
    bucket, it = _by_acc(client.get(BASE).json())[acc]
    assert (bucket, it["status"], it["system_basis"], it["reading"]) == (
        "auto",
        "kept",
        "system:no-retraction",
        "no_retraction",
    )
    r = client.post(f"{BASE}/{sid}/{acc}/apply", json={"reason": "it is a cancellation"})
    assert r.status_code == 200 and r.json()["review"] == "decided"
    assert "0000950170-25-107291" not in _accessions(db, sid, asof)
    client.post(f"{BASE}/decisions/{r.json()['undo_id']}/revoke")
    assert "0000950170-25-107291" in _accessions(db, sid, asof)


def test_an_owed_amendment_offers_three_verdicts_each_undoable(client, db):
    """YYAI is owed (its words neither fix nor add): Add to the original keeps the original buys and adds the
    amendment's rows; Use the correction replaces the whole original; Keep the original changes nothing.
    """
    sid = seed(db, "YYAI")
    acc, asof = "0002091629-25-000011", date(2025, 10, 31)
    bucket, it = _by_acc(client.get(BASE).json())[acc]
    assert (bucket, it["unresolved"], it["reading"]) == ("owed", True, "unresolved")
    add = client.post(f"{BASE}/{sid}/{acc}/add").json()
    assert add["review"] == "decided" and _accessions(db, sid, asof) == {
        "0002091629-25-000003",
        acc,
    }
    assert len(add["kept_rows"]) == 2  # both original buys stand
    client.post(f"{BASE}/decisions/{add['undo_id']}/revoke")
    use = client.post(f"{BASE}/{sid}/{acc}/apply").json()
    assert _accessions(db, sid, asof) == {acc}
    assert client.post(f"{BASE}/{sid}/{acc}/keep").status_code == 409  # undo yours first
    client.post(f"{BASE}/decisions/{use['undo_id']}/revoke")
    kept = client.post(f"{BASE}/{sid}/{acc}/keep").json()
    assert kept["status"] == "kept" and _accessions(db, sid, asof) == {"0002091629-25-000003"}


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
    assert (linked["method"], linked["disposition"], linked["status"], linked["review"]) == (
        "operator",
        "partial",
        "pending",
        "owed",  # an operator-linked amendment stays the operator's — never applied by the system
    )
    applied = client.post(f"{BASE}/{sid}/{acc}/apply").json()
    assert applied["status"] == "applied"
    # Use the correction: the amendment replaces the whole linked original
    assert applied["kept_rows"] == []
    assert _codes_of(db, sid, asof, "0001856680-26-000013") == set()
    link_id = next(d["id"] for d in applied["decisions"] if d["action"] == "link")
    assert client.post(f"{BASE}/decisions/{link_id}/revoke").status_code == 409  # verdict first
    client.post(f"{BASE}/decisions/{applied['undo_id']}/revoke")
    back = client.post(f"{BASE}/decisions/{link_id}/revoke").json()
    assert back["disposition"] == "ambiguous" and back["status"] == "pending"
    assert _codes_of(db, sid, asof, "0001856680-26-000013") == {"M", "S"}


def test_nothing_to_ratify_on_an_inert_amendment(client, db):
    sid = seed(db, "RPAY")
    r = client.post(f"{BASE}/{sid}/0001104659-26-041960/apply")
    assert r.status_code == 409 and "clerical" in r.json()["detail"]
    assert client.post(f"{BASE}/{sid}/0000000000-00-000000/apply").status_code == 404
    assert client.post(f"{BASE}/{sid}/not-an-accession/apply").status_code == 422


def test_a_no_signal_effect_amendment_is_applied_automatically_under_its_own_basis(client, db):
    """MRCY's 4/A adds only an omitted grant (code A) beside its two sales: applied automatically with basis
    system:no-signal-effect (its sales stand — no row a call reads changes); Keep the original is its Undo.
    """
    sid = seed(db, "MRCY")
    acc = "0001049521-26-000071"
    bucket, it = _by_acc(client.get(BASE).json())[acc]
    assert bucket == "auto"
    assert (it["status"], it["auto_resolved"], it["signal_effect"], it["system_basis"]) == (
        "applied",
        True,
        False,
        "system:no-signal-effect",
    )
    assert it["undo_id"] is None and it["kept_rows"] == [
        "0001049521-26-000054#0",
        "0001049521-26-000054#1",
    ]
    kept = client.post(f"{BASE}/{sid}/{acc}/keep").json()
    assert kept["status"] == "kept" and kept["review"] == "decided"


def test_owed_lists_the_in_window_items_first(client, db, monkeypatch):
    """The panel judges urgency at today's market day — pinned to 2026-10-16. FLS's void handed back to the
    operator (its system application revoked) sits in tonight's P window; SKYT's ambiguous link is outside the
    window but inside the review horizon: FLS leads."""
    _pin_today(monkeypatch, date(2026, 10, 16))
    fls = seed(db, "FLS")
    seed(db, "SKYT")
    system = next(
        d
        for d in _by_acc(client.get(BASE).json())["0001628280-26-037326"][1]["decisions"]
        if d["standing"]
    )
    assert client.post(f"{BASE}/decisions/{system['id']}/revoke").status_code == 200
    body = client.get(BASE).json()
    assert body["asof"] == "2026-10-16"
    order = [(i["ticker"], i["in_window"]) for i in body["owed"]]
    assert order == [("FLS", ["P"]), ("SKYT", [])]
    assert _accessions(db, fls, date(2026, 6, 15)).isdisjoint(
        {"0001628280-26-035913"}
    )  # still suppressed
