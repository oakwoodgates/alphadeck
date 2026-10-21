"""W1 — the Workbench's per-name price pull takes the per-company write lock (``db/write_lock.py``).

It is a REQUEST, so a long writer must never hang it: while another writer (the nightly pass, the on-promote
job) holds the company, the pull waits at most ``Settings.ingest_lock_request_wait_s`` and answers 409 naming the
holder — and writes nothing. The moment the company is free, the same click lands its bars (count the table).
"""

from __future__ import annotations

import time
from datetime import date

import pytest

from db.session import connect, name_session
from db.write_lock import acquire_company_lock
from domain.settings import get_settings


def _bar(d, close):
    return {"d": d, "open": close, "high": close, "low": close, "close": close, "volume": 1000}


class _FakeSource:
    def get_bars(self, ticker, *, allow_live=True, force_refresh=False):
        return [_bar(date(2026, 7, 1), 10.0), _bar(date(2026, 7, 2), 11.0)]


@pytest.fixture
def short_request_wait(monkeypatch):
    monkeypatch.setenv("ALPHADECK_INGEST_LOCK_REQUEST_WAIT_S", "0.5")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _bars_stored(db, sid) -> int:
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM fact_price_eod WHERE security_id = %s", (sid,))
        n = cur.fetchone()["n"]
    db.rollback()
    return n


def test_the_price_pull_answers_409_while_another_writer_holds_the_company(
    client, db, security_id, monkeypatch, short_request_wait
):
    from ingest.prices import ingest_security as mod

    monkeypatch.setattr(mod, "YahooPriceSource", _FakeSource)
    nightly = connect()
    name_session(nightly, "alphadeck-writer:daily-pass")
    try:
        hold = acquire_company_lock(nightly, security_id, wait_s=0)
        t0 = time.monotonic()
        r = client.post(f"/workbench/securities/{security_id}/ingest-prices")
        assert time.monotonic() - t0 < 5  # bounded: the request never hangs behind the writer
        assert r.status_code == 409
        detail = r.json()["detail"]
        assert "another ingest is writing this company's facts right now" in detail
        assert "alphadeck-writer:daily-pass" in detail  # names who holds it
        assert _bars_stored(db, security_id) == 0  # nothing written

        hold.release()
        r2 = client.post(f"/workbench/securities/{security_id}/ingest-prices")
        assert r2.status_code == 200 and r2.json()["bars_appended"] == 2
        assert _bars_stored(db, security_id) == 2
    finally:
        nightly.close()


def test_the_price_pull_holds_the_company_through_its_commit(client, db, security_id, monkeypatch):
    """While the pull is mid-write, another writer is refused the company — and once it returns, the lock is
    gone (released only after the commit, never before)."""
    from ingest.prices import ingest_security as mod

    other = connect()
    seen: list[bool] = []

    class _Probe(_FakeSource):
        def get_bars(self, ticker, *, allow_live=True, force_refresh=False):
            try:
                acquire_company_lock(other, security_id, wait_s=0).release()
                seen.append(False)  # free mid-pull = the pull did NOT hold it
            except Exception:  # noqa: BLE001 — CompanyLockBusy: held, as it must be
                seen.append(True)
            return super().get_bars(ticker, allow_live=allow_live, force_refresh=force_refresh)

    monkeypatch.setattr(mod, "YahooPriceSource", _Probe)
    try:
        assert client.post(f"/workbench/securities/{security_id}/ingest-prices").status_code == 200
        assert seen == [True]
        acquire_company_lock(other, security_id, wait_s=0).release()  # released after the request
    finally:
        other.close()
