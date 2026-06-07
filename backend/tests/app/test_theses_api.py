from __future__ import annotations

import json
import uuid
from pathlib import Path

from fastapi.testclient import TestClient

from app.deps import get_conn
from app.main import app
from app.openapi_export import export
from app.schemas_api import edgar_url
from db.session import DEFAULT_TENANT_ID
from domain.enums import Archetype
from domain.thesis import BasketMember, Thesis
from ingest.edgar.converts import clean_filing_text, ingest_convert_terms, parse_convert_terms
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, parse_yahoo_chart
from repositories import thesis_repo

_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"
_WELLS_ACCESSION = "0001773751-26-000086"


def _seed_hims_thesis(db, security_id) -> uuid.UUID:
    ingest_form4(
        db,
        security_id,
        (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        _WELLS_ACCESSION,
    )
    ingest_prices(
        db,
        security_id,
        parse_yahoo_chart(
            json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
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
    terms = parse_convert_terms(
        clean_filing_text((_SEED / "edgar" / "hims_converts_8k.htm").read_text(encoding="utf-8")),
        clean_filing_text(
            (_SEED / "edgar" / "hims_converts_pricing.htm").read_text(encoding="utf-8")
        ),
    )
    ingest_convert_terms(
        db,
        security_id,
        terms,
        accession="0001193125-26-234847",
        shares_outstanding=228_357_303,
        shares_outstanding_ref="0001773751-26-000076",
    )
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
    assert body["confidence"] is not None  # an armed card carries the confidence bar
    # each fired trigger is attributed to its name, resolved from the security master (this fixture's
    # security is "DEVCO"); a multi-name basket would show each breakout's own ticker
    assert body["triggers_fired"] and all(t["ticker"] == "DEVCO" for t in body["triggers_fired"])
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
    body = r.json()
    assert body["state"] == "warming"
    assert body["confidence"] is None  # a not-yet card shows no confidence bar (§7)


def test_call_endpoint_is_deterministic(db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    try:
        client = _client(db)
        a = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"}).json()
        b = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"}).json()
    finally:
        app.dependency_overrides.clear()
    assert a == b


def test_call_endpoint_does_not_write_to_the_calls_log(db, security_id):
    """The serve path is read-only: a GET (or a refetch / slider scrub) recomputes and writes no
    accountability row — that is the batch ``pipeline.run``'s job. Otherwise polling accretes rows.
    """
    tid = _seed_hims_thesis(db, security_id)

    def _calls_count() -> int:
        with db.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM calls WHERE thesis_id = %s", (tid,))
            return cur.fetchone()["n"]

    assert _calls_count() == 0
    try:
        client = _client(db)
        client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"})
        client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"})  # a refetch
    finally:
        app.dependency_overrides.clear()
    assert _calls_count() == 0  # still nothing written


def test_list_and_get_thesis(db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    try:
        client = _client(db)
        listing = client.get("/theses").json()
        detail = client.get(f"/theses/{tid}").json()
    finally:
        app.dependency_overrides.clear()
    summary = next(t for t in listing if t["id"] == str(tid))
    assert summary["ticker"] == "HIMS" and summary["basket_size"] == 1
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


def test_edgar_url_built_from_issuer_cik_not_accession_prefix():
    # The 8-K accession prefix (1193125) is the filing AGENT (DFIN); the link must use the ISSUER CIK.
    url = edgar_url("8-k", "0001193125-26-234847", "1773751")
    assert url == (
        "https://www.sec.gov/Archives/edgar/data/1773751/"
        "000119312526234847/0001193125-26-234847-index.htm"
    )
    assert edgar_url("price", "price:HIMS:2026-06-01", "1773751") is None  # non-filing source
    assert edgar_url("form4", "0001773751-26-000086", None) is None  # issuer CIK unknown


def test_call_endpoint_surfaces_the_dilution_risk_with_a_resolving_link(db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    try:
        r = _client(db).get(f"/theses/{tid}/call", params={"asof": "2026-06-01"})
    finally:
        app.dependency_overrides.clear()
    body = r.json()
    assert body["state"] == "armed"  # the ~$402.5M overhang is non-blocking
    risks = body["risk_signals"]
    assert any("convertible notes" in rs["label"].lower() for rs in risks)
    # the dilution 8-K link resolves from the ISSUER cik (PR-1 fix), not the DFIN accession prefix
    urls = [p["url"] for rs in risks for p in rs["sources"] if p["url"]]
    assert any("sec.gov/Archives/edgar/data" in u and "0001193125-26-234847" in u for u in urls)
