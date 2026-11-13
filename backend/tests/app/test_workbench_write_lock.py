"""W1 — the Workbench's per-name price pull takes the per-company write lock (``db/write_lock.py``).

It FETCHES the bars first, with the company free, and takes the lock only for the compare-and-append (security
hardening PR 1b), so a slow vendor never holds up the nightly. It is a REQUEST, so a long writer must never hang
it: while another writer (the nightly pass, the on-promote job) holds the company, the pull waits at most
``Settings.ingest_lock_request_wait_s`` and answers 409 naming the holder — and writes nothing. The moment the company is free, the same click lands its bars (count the table).
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
        assert "Another ingest is writing this company's facts right now" in detail
        assert "alphadeck-writer:daily-pass" in detail  # names who holds it
        assert _bars_stored(db, security_id) == 0  # nothing written

        hold.release()
        r2 = client.post(f"/workbench/securities/{security_id}/ingest-prices")
        assert r2.status_code == 200 and r2.json()["bars_appended"] == 2
        assert _bars_stored(db, security_id) == 2
    finally:
        nightly.close()


def test_the_price_pull_does_not_hold_the_company_during_the_vendor_fetch(
    client, db, security_id, monkeypatch
):
    """Fetch, then lock (security hardening PR 1b): a SLOW vendor fetch runs with the company FREE, so the nightly
    (which waits up to 180 s for the lock, then skips the company) is never held up by a user's click.
    """
    from ingest.prices import ingest_security as mod

    other = connect()
    seen: list[bool] = []

    class _SlowProbe(_FakeSource):
        def get_bars(self, ticker, *, allow_live=True, force_refresh=False):
            time.sleep(0.3)  # a slow vendor
            try:
                acquire_company_lock(other, security_id, wait_s=0).release()
                seen.append(False)  # free mid-fetch: the pull did NOT hold it
            except Exception:  # noqa: BLE001 — CompanyLockBusy: held, which it must not be
                seen.append(True)
            return super().get_bars(ticker, allow_live=allow_live, force_refresh=force_refresh)

    monkeypatch.setattr(mod, "YahooPriceSource", _SlowProbe)
    try:
        r = client.post(f"/workbench/securities/{security_id}/ingest-prices")
        assert r.status_code == 200 and r.json()["bars_appended"] == 2
        assert seen == [False]  # fetched exactly once, with the company free
    finally:
        other.close()


def test_the_price_pull_holds_the_company_through_its_compare_and_append(
    client, db, security_id, monkeypatch
):
    """The compare-and-append still runs under the lock, and the lock is gone once the request returns (released
    only after the commit, never before)."""
    from ingest.prices import ingest_security as mod

    other = connect()
    seen: list[bool] = []
    real_append = mod.ingest_prices

    def _probe_append(*a, **kw):
        try:
            acquire_company_lock(other, security_id, wait_s=0).release()
            seen.append(False)
        except Exception:  # noqa: BLE001 — CompanyLockBusy: held, as it must be
            seen.append(True)
        return real_append(*a, **kw)

    monkeypatch.setattr(mod, "YahooPriceSource", _FakeSource)
    monkeypatch.setattr(mod, "ingest_prices", _probe_append)
    try:
        assert client.post(f"/workbench/securities/{security_id}/ingest-prices").status_code == 200
        assert seen and all(seen)  # every append ran under the lock
        acquire_company_lock(other, security_id, wait_s=0).release()  # released after the request
    finally:
        other.close()
