from __future__ import annotations

import json
import uuid
from datetime import date, datetime, time, timezone
from pathlib import Path

from app.openapi_export import export
from app.routers import theses as theses_router
from app.schemas_api import CallCardResponse, edgar_url
from db.session import DEFAULT_TENANT_ID
from domain.market_time import known_at_for_asof, market_today
from domain.thesis import BasketMember, Thesis
from ingest.edgar.converts import clean_filing_text, ingest_convert_terms, parse_convert_terms
from ingest.edgar.form4 import ingest_form4
from ingest.prices.eod_loader import ingest_prices, ingest_prices_backfill, parse_yahoo_chart
from pipeline.call_for_thesis import call_for_thesis
from repositories import thesis_repo
from securities import master
from tests.form4a_fixtures import as_issuer

_SEED = Path(__file__).resolve().parent.parent.parent / "seed_data"
_WELLS_ACCESSION = "0001773751-26-000086"
# The Wells purchase is dated 2026-05-26; a Form 4 is due within two business days, so the filing — the
# instant the buy became knowable — is stamped at that deadline. Every asof this file reads is >= 05-28.
_WELLS_FILED = datetime(2026, 5, 28, 12, 0, tzinfo=timezone.utc)
# The measured serve-path leak's shape: facts dated in the past, INGESTED long after (the 2026-09-01 thaw
# stamped TPCS's August bars). Any instant after the June as-ofs' day-end works; this is the real one.
_THAW = datetime(2026, 9, 1, 18, 13, 31, tzinfo=timezone.utc)


def _seed_hims_thesis(
    db, security_id, *, recorded_at: datetime | None = None, accepted: datetime = _WELLS_FILED
) -> uuid.UUID:
    """The seeded HIMS thesis, with HONEST knowability on the transaction axis.

    ``recorded_at=None`` (the default) stamps each fact at the instant it became knowable in the world —
    the historical-backfill contract ``ingest_prices_backfill`` states (a bar on its own date; the Form 4
    at its filing deadline; the convert terms from the 8-K's issue date) — so a scrubbed-back ``/call`` at
    2026-06-01 reads exactly what was on file that evening (invariant #1, BOTH axes). Leaving the column
    at the DB default ``now()`` (the LIVE ingest's stamp) would make every June fact "learned today", and a
    06-01 recompute must NOT see that — which is precisely the serve-path leak. An explicit ``recorded_at``
    stamps EVERY fact at that instant: how the leak test builds the measured thaw shape on purpose.

    ``accepted`` is the Wells Form 4's SEC acceptance — its PUBLIC instant, the clock a past-asof recompute
    judges it on (``db.public_clock``); a test moves it past the as-of to publish the filing late.
    """
    as_issuer(
        db, security_id, "0001773751"
    )  # R8-A: the security IS the Wells Form 4's issuer (HIMS)
    ingest_form4(
        db,
        security_id,
        (_SEED / "edgar" / "hims_wells_form4.xml").read_text(encoding="utf-8"),
        _WELLS_ACCESSION,
        recorded_at=recorded_at or _WELLS_FILED,
        accepted=accepted,
    )
    bars = parse_yahoo_chart(
        json.loads((_SEED / "prices" / "HIMS.yahoo.json").read_text(encoding="utf-8"))
    )
    if recorded_at is None:
        ingest_prices_backfill(db, security_id, bars)  # recorded_at = each bar's own date
    else:
        ingest_prices(db, security_id, bars, recorded_at=recorded_at)
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
        # the 8-K's terms were public from the notes' issue date (the fact's own valid_from)
        recorded_at=recorded_at
        or datetime.combine(terms.issued_date, time.min, tzinfo=timezone.utc),
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
            # the PRODUCTION clock — the route's guard validates against `market_today()`, so an
            # ambient `date.today()` reads as future-dated (422) on any runner ahead of market time
            "decision_date": str(market_today()),
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
    assert detail["position"]["opened_on"] == str(market_today())


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


# --- invariant #1 on the SERVE path, judged on the PUBLIC clock (operator decision 2026-10-21) --------------
#
# A scrub-back is a HISTORY view: each fact counts from when it became PUBLIC (a Form 4 from its SEC
# acceptance, a bar from its settled session, a correction from its own publication), never from when our
# database happened to ingest it. The record clock this replaced (``recorded_at <= end of the as-of day``)
# hid the measured case's facts — TPCS bars dated August, ingested at the 2026-09-01 thaw — which only
# replayed our own outage; on the public clock those bars were public, so a past view reads them. What a
# past view can never read is a filing PUBLISHED after the as-of.


def test_call_scrub_back_reads_what_was_public_even_when_ingested_late(client, db, security_id):
    """The thaw shape: every HIMS fact dated May/June but INGESTED 2026-09-01. On the public clock a 06-01
    recompute reads them exactly as public that night — the Form 4 accepted 05-28, the bars settled on their
    own days, the convert terms from their issue — so it is the SAME armed card the honestly-stamped seed
    arms (``test_call_endpoint_serves_armed_card_on_real_data``). The ingest instant moves nothing.
    """
    tid = _seed_hims_thesis(db, security_id, recorded_at=_THAW)
    body = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"}).json()
    # exactly the honestly-stamped seed's card (the positive control's assertions)
    assert body["state"] == "armed" and body["verdict"] == "starter_entry"
    assert body["arm_until"] == "2026-06-11"
    assert body["armed_security_id"] == str(security_id)
    assert body["confidence"] is not None


def test_call_scrub_back_hides_a_filing_published_after_the_asof(client, db, security_id):
    """The no-lookahead half: the SAME Wells buy (traded 05-26), but its Form 4 ACCEPTED 06-03 — after the
    06-01 as-of. A 06-01 recompute cannot see it (no conviction trigger, never armed on it), however early
    the row was ingested; a 06-05 recompute can. The transaction date is not the clock; the acceptance is.
    """
    accepted = datetime(2026, 6, 3, 20, 0, tzinfo=timezone.utc)
    tid = _seed_hims_thesis(db, security_id, accepted=accepted)
    before = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"}).json()
    assert before["state"] != "armed"
    assert not [t for t in before["triggers_fired"] if t["kind"] == "insider"]
    after = client.get(f"/theses/{tid}/call", params={"asof": "2026-06-05"}).json()
    assert [t for t in after["triggers_fired"] if t["kind"] == "insider"]


def test_call_threads_the_public_clock_for_a_past_view_and_the_live_read_for_today(
    client, db, security_id, monkeypatch
):
    """The per-site TEMPLATE (a future serve site that forgets the clock fails a copy of this): the route
    threads ``(known_at_for_asof(asof), "public")`` — the end of that MARKET day, on the public clock — into
    ``call_for_thesis`` for a past asof, and ``(None, "record")`` for a live one. None, not now, on purpose:
    the live path is the exact code it was (the PIT's own now for the fact reads, the DATABASE clock for the
    decisions log — ``decisions_repo``'s one-clock rule, whose two-clock flake a host-clock now reopens).
    """
    tid = _seed_hims_thesis(db, security_id)
    seen: list[tuple[datetime | None, str]] = []
    real = theses_router.call_for_thesis

    def spy(conn, thesis_id, asof, **kw):
        seen.append((kw.get("known_at"), kw.get("clock", "record")))
        return real(conn, thesis_id, asof, **kw)

    monkeypatch.setattr(theses_router, "call_for_thesis", spy)
    assert client.get(f"/theses/{tid}/call", params={"asof": "2026-06-01"}).status_code == 200
    today = market_today()
    assert client.get(f"/theses/{tid}/call", params={"asof": today.isoformat()}).status_code == 200
    assert seen == [(known_at_for_asof(date(2026, 6, 1)), "public"), (None, "record")]
    assert seen[0][0] == datetime(
        2026, 6, 2, 9, 59, 59, 999999, tzinfo=timezone.utc
    )  # 06-02 05:59 EDT: Monday's night ends at EDGAR's next open (F6)


def test_call_live_view_is_identical_to_the_uncapped_read(client, db, security_id):
    """asof = today produces the SAME card as the pre-fix call (no ``known_at`` at all): the guard that
    the fix touched the past without touching the present. The thaw-stamped fixture IS a live ingest from
    today's point of view (recorded in the past, dated in the past), so whatever the uncapped read holds
    — a stale June breakout, a standing overhang, or nothing — the route must hold it identically.
    """
    tid = _seed_hims_thesis(db, security_id, recorded_at=_THAW)
    today = market_today()
    via_route = client.get(f"/theses/{tid}/call", params={"asof": today.isoformat()}).json()
    card = call_for_thesis(db, tid, today, record=False)  # the pre-fix signature: known_at -> now
    sec_ids = (
        {t.security_id for t in card.triggers_fired}
        | {r.security_id for r in card.risk_signals}
        | {m.security_id for m in card.armed_members}
        | {m.security_id for m in card.watch_members}
    )
    uncapped = CallCardResponse.from_card(
        card,
        master.ciks_for(db, sec_ids, tenant_id=DEFAULT_TENANT_ID),
        master.tickers_for(db, sec_ids, tenant_id=DEFAULT_TENANT_ID),
    ).model_dump(mode="json")
    assert via_route == uncapped
