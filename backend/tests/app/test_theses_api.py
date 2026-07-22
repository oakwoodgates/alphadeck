from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path

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


def test_call_endpoint_serves_armed_card_on_real_data(client, db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    r = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"})

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


def test_call_endpoint_warming_before_breakout(client, db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    r = client.get(f"/theses/{tid}/call", params={"asof": "2026-05-28"})
    assert r.status_code == 200
    body = r.json()
    assert body["state"] == "warming"
    assert body["confidence"] is None  # a not-yet card shows no confidence bar (§7)


def test_call_endpoint_is_deterministic(client, db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    a = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"}).json()
    b = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"}).json()
    assert a == b


def test_call_endpoint_does_not_write_to_the_calls_log(client, db, security_id):
    """The serve path is read-only: a GET (or a refetch / slider scrub) recomputes and writes no
    accountability row — that is the batch ``pipeline.run``'s job. Otherwise polling accretes rows.
    """
    tid = _seed_hims_thesis(db, security_id)

    def _calls_count() -> int:
        with db.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM calls WHERE thesis_id = %s", (tid,))
            return cur.fetchone()["n"]

    assert _calls_count() == 0
    client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"})
    client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"})  # a refetch
    assert _calls_count() == 0  # still nothing written


def test_list_and_get_thesis(client, db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    listing = client.get("/theses").json()
    detail = client.get(f"/theses/{tid}").json()
    summary = next(t for t in listing if t["id"] == str(tid))
    assert summary["ticker"] == "HIMS" and summary["basket_size"] == 1
    assert detail["ticker"] == "HIMS"
    assert detail["basket"][0]["ticker"] == "HIMS"
    assert "tenant_id" not in detail  # the wire schema must not leak the domain's tenant_id


def test_get_thesis_populates_attributed_position_security_id(client, db, security_id):
    """The #1 fix, at the API boundary: GET /theses/{id} threads the decisions-log-derived position
    onto the detail, so an ATTRIBUTED take's ``security_id`` reaches ``ThesisDetail.position`` — the
    field the Cockpit per-name panel gates its "Position · this name" block on. The masking test
    (Cockpit.panel.test.tsx) hand-injected this shape; the read path never emitted it, because
    ``_row_to_position`` built the position from only the seed columns (which carry no name). Prove
    the previously-broken path now works: the name AND the entry/opened come through."""
    tid = uuid.uuid4()
    thesis_repo.upsert(
        db,
        Thesis(
            id=tid,
            tenant_id=DEFAULT_TENANT_ID,
            name="attributed position",
            narrative="x",
            basket=[BasketMember(ticker="DEVCO", role="the name", security_id=security_id)],
        ),
    )
    db.commit()

    # detail before any fill: no decision rows, no seed position → position is null (the honest empty)
    assert client.get(f"/theses/{tid}").json()["position"] is None

    # a take logged ON the member (its security_id) — the attributed fill
    take = client.post(
        f"/theses/{tid}/decisions",
        json={
            "action": "take",
            "decision_date": str(date.today()),
            "security_id": str(security_id),
            "price": 12.5,
        },
    )
    assert take.status_code == 200

    detail = client.get(f"/theses/{tid}").json()
    assert detail["position"] is not None
    # the previously-structurally-null field, now populated from the log's authoritative position
    assert detail["position"]["security_id"] == str(security_id)
    assert detail["position"]["entry_price"] == 12.5
    assert detail["position"]["opened_on"] == str(date.today())


def test_call_endpoint_unknown_thesis_404(client):
    r = client.get(f"/theses/{uuid.uuid4()}/call", params={"asof": "2026-06-01"})
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


def test_call_endpoint_surfaces_the_dilution_risk_with_a_resolving_link(client, db, security_id):
    tid = _seed_hims_thesis(db, security_id)
    r = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"})
    body = r.json()
    assert body["state"] == "armed"  # the ~$402.5M overhang is non-blocking
    risks = body["risk_signals"]
    assert any("convertible notes" in rs["label"].lower() for rs in risks)
    # the dilution 8-K link resolves from the ISSUER cik (PR-1 fix), not the DFIN accession prefix
    urls = [p["url"] for rs in risks for p in rs["sources"] if p["url"]]
    assert any("sec.gov/Archives/edgar/data" in u and "0001193125-26-234847" in u for u in urls)
