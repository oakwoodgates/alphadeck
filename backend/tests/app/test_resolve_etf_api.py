from __future__ import annotations

import uuid
from datetime import date

from db.session import DEFAULT_TENANT_ID

# The surface-ETF endpoint (ETF Sleeve, Slice 1): POST /workbench/securities/resolve-etf resolves an
# operator-supplied ETF ticker to a security_master row marked instrument_kind='etf', which the FE then
# adds to the basket as a `fund` sleeve. Two branches: a ticker ALREADY present (marked in place, offline)
# and one ABSENT from SEC (created cik=None, figi/SEC monkeypatched so the suite never hits live).


def _insert_equity(db, ticker, name, cik):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, name, cik, date(2026, 1, 1)),
        )
    db.commit()
    return sid


def _kind(db, ticker):
    with db.cursor() as cur:
        cur.execute("SELECT instrument_kind FROM security_master WHERE ticker = %s", (ticker,))
        return cur.fetchone()["instrument_kind"]


def _count(db, ticker):
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_master WHERE ticker = %s", (ticker,))
        return cur.fetchone()["n"]


def _promote_fund_sleeve(client, security_id, ticker):
    return client.post(
        "/workbench/theses",
        json={
            "name": "ETF-sleeve theme",
            "narrative": "hold the theme, skip picking names.",
            "ticker": None,
            "segments": [],
            "basket": [
                {
                    "ticker": ticker,
                    "role": "ETF sleeve",
                    "archetype": "fund",
                    "security_id": str(security_id),
                    "segment": None,
                    "authored_by": "operator_set",
                }
            ],
        },
    )


def test_resolve_etf_marks_present_row_and_returns_etf(client, db):
    """The HIT path (offline — the row is already present, so no live OpenFIGI): an equity row already in
    the master (SPY — the few mega-ETFs present as equities) is flipped to instrument_kind='etf' IN PLACE
    and returned as a SecurityMatchOut carrying the new kind (case-insensitive ticker). Count-the-table:
    the mark never duplicates the row."""
    sid = _insert_equity(db, "SPY", "SPDR S&P 500 ETF Trust", "0000884394")

    r = client.post("/workbench/securities/resolve-etf", json={"ticker": "spy"})
    assert r.status_code == 200
    body = r.json()
    assert body["security_id"] == str(sid)  # the SAME row, not a new insert
    assert body["ticker"] == "SPY"
    assert body["instrument_kind"] == "etf"  # flipped + surfaced on the wire

    assert _kind(db, "SPY") == "etf"  # marked in place
    assert _count(db, "SPY") == 1  # UPDATE-in-place — no duplicate append


def test_resolve_etf_present_row_promotes_as_fund_sleeve(client, db):
    """After surfacing, a `fund` basket member referencing the marked row PROMOTES through the exists +
    identity-coherence guards (the ETF attaches UNDER a thesis — #2) and the sleeve archetype persists.
    """
    sid = _insert_equity(db, "GLD", "SPDR Gold Shares", "0001222333")
    assert (
        client.post("/workbench/securities/resolve-etf", json={"ticker": "GLD"}).status_code == 200
    )

    promote = _promote_fund_sleeve(client, sid, "GLD")
    assert promote.status_code == 200
    detail = client.get(f"/theses/{promote.json()['id']}").json()
    assert (
        detail["basket"][0]["archetype"] == "fund"
    )  # the sleeve member persisted under the thesis


def test_resolve_etf_creates_null_cik_row_and_promotes(client, db, monkeypatch):
    """The CREATE path (the representative thematic-ETF case): a fund absent from SEC (URA — a fund-trust
    series, not an operating-company CIK) is INSERTED marked 'etf' with cik=None; OpenFIGI names it. Then a
    `fund` member promotes through the identity-coherence guard — which filters cik IS NOT NULL, so a
    null-cik sleeve passes cleanly. figi/SEC are monkeypatched OFFLINE (the suite never hits live).
    """
    monkeypatch.setattr(
        "securities.figi.map_ticker",
        lambda ticker, **kw: {
            "ticker": ticker,
            "figi": "BBG000000URA",
            "name": "Global X Uranium ETF",
        },
    )
    monkeypatch.setattr("securities.sec_tickers.cik_for", lambda ticker, **kw: None)  # SEC-absent

    r = client.post("/workbench/securities/resolve-etf", json={"ticker": "URA"})
    assert r.status_code == 200
    body = r.json()
    assert body["instrument_kind"] == "etf"
    assert body["cik"] is None  # cik=None is fine for a price-only sleeve
    assert body["name"] == "Global X Uranium ETF"
    assert _kind(db, "URA") == "etf" and _count(db, "URA") == 1

    promote = _promote_fund_sleeve(client, body["security_id"], "URA")
    assert promote.status_code == 200
    detail = client.get(f"/theses/{promote.json()['id']}").json()
    assert detail["basket"][0]["archetype"] == "fund"


def test_resolve_etf_blank_ticker_is_422(client):
    """A blank/whitespace ticker is rejected — nothing to resolve."""
    assert (
        client.post("/workbench/securities/resolve-etf", json={"ticker": "  "}).status_code == 422
    )
