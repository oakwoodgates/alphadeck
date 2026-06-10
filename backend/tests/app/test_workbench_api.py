from __future__ import annotations

import uuid
from datetime import date

from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype
from domain.thesis import BasketMember, Segment, Thesis
from ingest.cash_burn import ingest_cash_burn
from ingest.revenue_mix import ingest_revenue_mix
from repositories import thesis_repo


def _client(db) -> TestClient:
    # share the test's connection (and its seeded, committed data) with the app
    app.dependency_overrides[get_conn] = lambda: db
    return TestClient(app)


def _scored_thesis(db, security_id) -> uuid.UUID:
    ingest_revenue_mix(
        db,
        security_id,
        segment_label="reactors",
        mix_pct=100,
        source="10-k-business-description",
        source_ref="10-K-biz",
        event_date=date(2025, 12, 31),
    )
    ingest_cash_burn(
        db,
        security_id,
        cash_usd=500_000_000,
        quarterly_burn_usd=25_000_000,
        source="10-q",
        source_ref="10-Q",
        event_date=date(2026, 3, 31),
    )
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="Small-scale nuclear",
        narrative="AI power demand + the SMR build-out.",
        segments=[Segment(label="reactors", descriptor="catalyst-rich")],
        basket=[
            BasketMember(
                ticker="DEVCO",
                role="the name",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
                segment="reactors",
            )
        ],
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis.id


def test_scored_endpoint_serves_meters_on_real_data(db, security_id):
    tid = _scored_thesis(db, security_id)
    try:
        r = _client(db).get(f"/workbench/theses/{tid}/scored", params={"asof": "2026-06-02"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    body = r.json()
    assert body["thesis_id"] == str(tid)
    assert [s["label"] for s in body["segments"]] == ["reactors"]
    assert len(body["members"]) == 1
    m = body["members"][0]
    assert m["ticker"] == "DEVCO" and m["segment"] == "reactors"
    assert m["purity"]["pips"] == 4 and m["purity"]["value"] == 100.0  # pure-play
    assert m["runway"]["pips"] == 4  # 500M / (25M/3) = 60 months
    assert m["dilution"]["pips"] is None  # no convert fact -> "—", not a fake 0
    assert m["fit"] == "pure-play"
    assert (
        m["purity"]["provenance"][0]["ref"] == "10-K-biz"
    )  # "behind the scores" traces to the filing


def test_promote_creates_incubating_thesis_on_the_board(db, security_id):
    payload = {
        "name": "Nuclear (promoted)",
        "narrative": "AI power demand + SMR build-out.",
        "ticker": None,
        "segments": [{"label": "reactors", "descriptor": "catalyst-rich"}],
        "basket": [
            {
                "ticker": "DEVCO",
                "role": "the name",
                "archetype": "high_beta",
                "security_id": str(security_id),
                "segment": "reactors",
                "authored_by": "operator_set",
            }
        ],
    }
    client = _client(db)
    try:
        r = client.post("/workbench/theses", json=payload)
        assert r.status_code == 200
        tid = r.json()["id"]
        assert [s["label"] for s in r.json()["segments"]] == ["reactors"]
        # it now shows on the Board (GET /theses) and the chain persisted (GET /theses/{id})
        assert any(t["id"] == tid for t in client.get("/theses").json())
        detail = client.get(f"/theses/{tid}").json()
        assert detail["basket"][0]["segment"] == "reactors"
        assert detail["basket"][0]["authored_by"] == "operator_set"
    finally:
        app.dependency_overrides.clear()


def test_promote_rejects_orphan_segment_placement(db, security_id):
    """A name placed in a link that isn't in the chain -> 422 (the Slice-1 validator, surfaced by the API)."""
    payload = {
        "name": "bad",
        "narrative": "x",
        "ticker": None,
        "segments": [{"label": "reactors"}],
        "basket": [
            {
                "ticker": "DEVCO",
                "role": "r",
                "archetype": "leader",
                "security_id": str(security_id),
                "segment": "fuel",  # not in segments
            }
        ],
    }
    client = _client(db)
    try:
        assert client.post("/workbench/theses", json=payload).status_code == 422
    finally:
        app.dependency_overrides.clear()


def _insert_security(db, ticker, *, name=None, cik=None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def test_securities_search_serves_the_master(db):
    """The authoring typeahead (Slice 4b): the resolver surfaces exact master rows for the operator to pick
    — a discovery net (INVARIANT #2), never a guess. No match -> []."""
    oklo = _insert_security(db, "OKLO", name="Oklo Inc.", cik="0001849056")
    _insert_security(db, "LEU", name="Centrus Energy Corp.")
    client = _client(db)
    try:
        hits = client.get("/workbench/securities", params={"q": "OK"}).json()
        assert [h["ticker"] for h in hits] == ["OKLO"]
        assert hits[0]["security_id"] == str(oklo) and hits[0]["cik"] == "0001849056"
        assert client.get("/workbench/securities", params={"q": "ZZZ"}).json() == []
    finally:
        app.dependency_overrides.clear()


def test_promote_stamps_authored_by_operator_set(db, security_id):
    """The human authoring path STAMPS authorship server-side: a body claiming `system_drafted` is coerced
    to `operator_set` (the operator is the author; `system_drafted` is reserved for the S5 drafter's own
    write path)."""
    payload = {
        "name": "Nuclear",
        "narrative": "x",
        "ticker": None,
        "segments": [{"label": "reactors"}],
        "basket": [
            {
                "ticker": "DEVCO",
                "role": "r",
                "archetype": "leader",
                "security_id": str(security_id),
                "segment": "reactors",
                "authored_by": "system_drafted",  # a stale / spoofed value from the body
            }
        ],
    }
    client = _client(db)
    try:
        tid = client.post("/workbench/theses", json=payload).json()["id"]
        detail = client.get(f"/theses/{tid}").json()
        assert detail["basket"][0]["authored_by"] == "operator_set"
    finally:
        app.dependency_overrides.clear()
