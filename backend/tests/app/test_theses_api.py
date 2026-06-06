from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.openapi_export import export
from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype
from domain.thesis import BasketMember, Thesis
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from repositories import thesis_repo

_F = Path(__file__).resolve().parent.parent / "fixtures"
_WELLS_ACCESSION = "0001773751-26-000086"


def _seed_hims_thesis(db, security_id) -> uuid.UUID:
    ingest_form4(
        db,
        security_id,
        (_F / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        _WELLS_ACCESSION,
    )
    ingest_prices(
        db,
        security_id,
        parse_yahoo_chart(
            json.loads((_F / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
        ),
    )
    thesis = Thesis(
        id=uuid.uuid4(),
        tenant_id=DEFAULT_TENANT_ID,
        name="HIMS — insider conviction",
        narrative="A director bought ~$1.2M open-market off the lows; watching for confirmation.",
        ticker="HIMS",
        basket=[
            BasketMember(
                ticker="HIMS",
                role="the name",
                archetype=Archetype.HIGH_BETA,
                security_id=security_id,
            )
        ],
    )
    thesis_repo.upsert(db, thesis)
    db.commit()
    return thesis.id


def _client(db) -> TestClient:
    # share the test's connection (and its seeded, committed data) with the app
    app.dependency_overrides[get_conn] = lambda: db
    return TestClient(app)


def test_call_endpoint_serves_armed_card_on_real_data(db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    try:
        r = _client(db).get(f"/theses/{tid}/call", params={"asof": "2026-06-01"})
    finally:
        app.dependency_overrides.clear()

    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "armed"
    assert body["verdict"] == "starter_entry"
    assert body["arm_until"] == "2026-06-11"
    assert body["armed_security_id"] == str(security_id)
    # the conviction trigger's provenance resolves to a clickable EDGAR filing URL
    urls = [p["url"] for t in body["triggers_fired"] for p in t["sources"] if p["url"]]
    assert any("sec.gov/Archives/edgar/data" in u and _WELLS_ACCESSION in u for u in urls)


def test_call_endpoint_warming_before_breakout(db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    try:
        r = _client(db).get(f"/theses/{tid}/call", params={"asof": "2026-05-28"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200
    assert r.json()["state"] == "warming"


def test_call_endpoint_is_deterministic(db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    try:
        client = _client(db)
        a = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"}).json()
        b = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"}).json()
    finally:
        app.dependency_overrides.clear()
    assert a == b


def test_list_and_get_thesis(db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    try:
        client = _client(db)
        listing = client.get("/theses").json()
        detail = client.get(f"/theses/{tid}").json()
    finally:
        app.dependency_overrides.clear()
    assert any(t["id"] == str(tid) and t["ticker"] == "HIMS" for t in listing)
    assert detail["ticker"] == "HIMS"
    assert detail["basket"][0]["ticker"] == "HIMS"
    assert "tenant_id" not in detail  # the wire schema must not leak the domain's tenant_id


def test_call_endpoint_unknown_thesis_404(db):
    try:
        r = _client(db).get(f"/theses/{uuid.uuid4()}/call", params={"asof": "2026-06-01"})
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 404


def test_openapi_export_exposes_the_call_contract(tmp_path):
    schema = json.loads(export(tmp_path / "openapi.json").read_text(encoding="utf-8"))
    assert "/theses/{thesis_id}/call" in schema["paths"]
    assert "/theses" in schema["paths"]
