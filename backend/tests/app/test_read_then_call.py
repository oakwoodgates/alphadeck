"""No request sits idle in a transaction across the network (security hardening PR 2, item 4).

Each route that reads and then calls SEC, a vendor or an LLM ends its (read-only) transaction before the first
outbound call, so the request connection's ``idle_in_transaction_session_timeout`` (``db.session.connect_request``)
can never cut off a legitimate request, and no snapshot is pinned (vacuum, DDL) while the network is slow.

Every outbound seam below is a fake that records the request connection's transaction status at the moment it is
called (the suite's ``client`` shares the test's ``db`` connection with the app, so ``db.info`` IS the request's).
"""

from __future__ import annotations

import uuid

import pytest
from psycopg.pq import TransactionStatus

from domain.extraction import ExtractionResult

DATA = "00000000-0000-0000-0000-000000000001"
IDLE = TransactionStatus.IDLE


class Seen:
    def __init__(self, db) -> None:
        self.db = db
        self.statuses: list[TransactionStatus] = []

    def mark(self) -> None:
        self.statuses.append(self.db.info.transaction_status)


@pytest.fixture
def seen(db) -> Seen:
    return Seen(db)


def _security(db, ticker: str, *, cik: str | None = "0000999101", exchange: str | None = None):
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, exchange, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, '2026-01-01')",
            (sid, DATA, ticker, f"{ticker} Inc", cik, exchange),
        )
    db.commit()
    return sid


def _thesis(client, sid: uuid.UUID | None = None, ticker: str = "RTC") -> uuid.UUID:
    basket = [] if sid is None else [{"ticker": ticker, "role": "r", "security_id": str(sid)}]
    r = client.post(
        "/workbench/theses",
        json={
            "name": "Read then call",
            "narrative": "grid batteries",
            "basket": basket,
            "segments": [],
        },
    )
    assert r.status_code == 200, r.text
    return uuid.UUID(r.json()["id"])


def test_extract_ends_the_read_before_sec(client, db, seen, monkeypatch):
    from app.routers import workbench as wb

    sid = _security(db, "RTC")
    tid = _thesis(client, sid)

    def fake(client_, cik):
        seen.mark()
        return ExtractionResult(facts=[])

    monkeypatch.setattr(wb, "extract_with_annual_fallback", fake)
    r = client.get(f"/workbench/securities/{sid}/extract", params={"thesis_id": str(tid)})
    assert r.status_code == 200, r.text
    assert seen.statuses == [IDLE]


def test_etf_holdings_ends_the_read_before_sec(client, db, seen, monkeypatch):
    from app.routers import workbench as wb

    sid = _security(db, "RTCF", cik=None)
    tid = _thesis(client)

    def fake(ticker, *, allow_live=False):
        seen.mark()
        return None

    monkeypatch.setattr(wb.fund_tickers, "resolve", fake)
    r = client.get(f"/workbench/securities/{sid}/etf-holdings", params={"thesis_id": str(tid)})
    assert r.status_code == 404, r.text
    assert seen.statuses == [IDLE]


def test_auto_confirm_ends_the_read_before_sec(client, db, seen, monkeypatch):
    from app.routers import workbench as wb

    sid = _security(db, "RTC")

    def fake(client_, cik):
        seen.mark()
        return []

    monkeypatch.setattr(wb, "extract_for_security", fake)
    r = client.post(
        "/workbench/facts/auto-confirm",
        json={"security_id": str(sid), "fact_type": "shares_outstanding"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["reason"] == "no_candidate"
    assert seen.statuses == [IDLE]


def test_ingest_prices_ends_the_read_before_the_resolver_and_the_vendor(
    client, db, seen, monkeypatch
):
    from app.routers import workbench as wb
    from ingest.prices import ingest_security as price_leg

    sid = _security(db, "RTCO", exchange="OTC")

    class _Proposal:
        tier = "NONE"
        proposed_symbol = None
        why = "fake"

    def fake_resolve(sec, *, allow_live=False):
        seen.mark()
        return _Proposal()

    class _Source:
        def get_bars(self, ticker, *, allow_live=True, force_refresh=False):
            seen.mark()
            return []

    monkeypatch.setattr(wb, "resolve_price_symbol", fake_resolve)
    monkeypatch.setattr(price_leg, "YahooPriceSource", _Source)
    r = client.post(f"/workbench/securities/{sid}/ingest-prices")
    assert r.status_code == 200, r.text
    assert seen.statuses == [IDLE, IDLE]


def test_terms_end_the_read_before_the_llm(client, db, seen, monkeypatch):
    from app.routers import workbench as wb

    tid = _thesis(client)

    def fake(llm, narrative, *, seeds=None, operator_terms=None):
        seen.mark()
        return []

    monkeypatch.setattr(wb, "produce_term_set", fake)
    r = client.post(f"/workbench/theses/{tid}/terms", json={"seeds": []})
    assert r.status_code == 200, r.text
    assert seen.statuses == [IDLE]


def test_recommend_tiers_ends_the_read_before_the_llm(client, db, seen, monkeypatch):
    from app.routers import workbench as wb

    tid = _thesis(client)

    def fake(llm, narrative, terms):
        seen.mark()
        return []

    monkeypatch.setattr(wb, "recommend_tiers", fake)
    r = client.post(f"/workbench/theses/{tid}/recommend-tiers")
    assert r.status_code == 200, r.text
    assert seen.statuses == [IDLE]


def test_resolve_etf_ends_the_read_before_openfigi_and_sec(client, db, seen, monkeypatch):
    def fake_figi(ticker, *, cache_dir=None, allow_live=False):
        seen.mark()
        return {"figi": "BBG000TEST01", "name": "Test Fund"}

    def fake_cik(ticker, *, cache_dir=None, allow_live=False):
        seen.mark()
        return None

    monkeypatch.setattr("securities.figi.map_ticker", fake_figi)
    monkeypatch.setattr("securities.sec_tickers.cik_for", fake_cik)
    r = client.post("/workbench/securities/resolve-etf", json={"ticker": "RTCX"})
    assert r.status_code == 200, r.text
    assert r.json()["instrument_kind"] == "etf"
    assert seen.statuses == [IDLE, IDLE]
