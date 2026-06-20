"""The price-source seam + the fresh-data (force-refresh) fix. No DB, no network — a tmp cache dir +
a monkeypatched polite_get stand in for the live source, so these exercise the cache/force-refresh logic
deterministically (M2b's tests monkeypatch the fetch, so they can't surface the stale-cache bug; these do).
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone

from ingest.prices import eod_loader
from ingest.prices.eod_loader import fetch_eod, parse_stooq_csv
from ingest.prices.source import StooqPriceSource, YahooPriceSource

# A "stale" cache (bars through 06-15) vs a "fresh" live series (adds 06-16) — the daily re-ingest case.
_STALE = [date(2026, 6, 14), date(2026, 6, 15)]
_FRESH = [date(2026, 6, 14), date(2026, 6, 15), date(2026, 6, 16)]


def _payload(dates: list[date]) -> dict:
    """A minimal Yahoo chart JSON for the given bar dates (midnight-UTC timestamps)."""
    ts = [int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp()) for d in dates]
    n = len(dates)
    quote = {
        "open": [1.0] * n,
        "high": [1.0] * n,
        "low": [1.0] * n,
        "close": [float(i + 1) for i in range(n)],
        "volume": [100.0] * n,
    }
    return {"chart": {"result": [{"timestamp": ts, "indicators": {"quote": [quote]}}]}}


class _Resp:
    """A stand-in httpx Response with the bits fetch_eod uses."""

    def __init__(self, payload: dict):
        self._p = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._p


def _fake_live(payload: dict, calls: list):
    """A polite_get stub that records calls and returns ``payload`` (the 'live' server)."""

    def f(url, **kw):
        calls.append(url)
        return _Resp(payload)

    return f


def _write_cache(tmp_path, dates):
    (tmp_path / "DEVCO.yahoo.json").write_text(json.dumps(_payload(dates)), encoding="utf-8")


def _dates(bars):
    return [b["d"] for b in bars]


# --- the stale-cache REPRODUCTION + the force-refresh fix --------------------------------------------


def test_cache_first_returns_stale_without_force(tmp_path, monkeypatch):
    """REPRODUCTION: a cache hit returns the stored bars and NEVER re-pulls, even with allow_live — so the
    daily re-ingest would get frozen data. This is the latent bug the force-refresh fix targets."""
    _write_cache(tmp_path, _STALE)
    calls: list = []
    monkeypatch.setattr(eod_loader, "polite_get", _fake_live(_payload(_FRESH), calls))

    bars = YahooPriceSource(cache_dir=tmp_path).get_bars("DEVCO", allow_live=True)  # no force

    assert _dates(bars) == _STALE  # the stale cache wins
    assert calls == []  # the 'live' server was never hit


def test_force_refresh_repulls_and_overwrites_the_cache(tmp_path, monkeypatch):
    """THE FIX: force_refresh (+ allow_live) bypasses the cache hit, re-pulls, and overwrites the cache."""
    _write_cache(tmp_path, _STALE)
    calls: list = []
    monkeypatch.setattr(eod_loader, "polite_get", _fake_live(_payload(_FRESH), calls))

    bars = YahooPriceSource(cache_dir=tmp_path).get_bars(
        "DEVCO", allow_live=True, force_refresh=True
    )

    assert _dates(bars) == _FRESH  # fresh data, not the cache
    assert len(calls) == 1  # re-pulled exactly once
    # the cache was overwritten — a subsequent cache-first read now sees the fresh series
    again = YahooPriceSource(cache_dir=tmp_path).get_bars("DEVCO", allow_live=True)
    assert _dates(again) == _FRESH


def test_force_refresh_offline_stays_cache_first(tmp_path, monkeypatch):
    """--no-live / dev path: force_refresh without allow_live never hits the network — cache-first holds."""
    _write_cache(tmp_path, _STALE)
    calls: list = []
    monkeypatch.setattr(eod_loader, "polite_get", _fake_live(_payload(_FRESH), calls))

    bars = YahooPriceSource(cache_dir=tmp_path).get_bars(
        "DEVCO", allow_live=False, force_refresh=True
    )

    assert _dates(bars) == _STALE  # cache, not the network
    assert calls == []


def test_cache_miss_fetches_even_without_force(tmp_path, monkeypatch):
    """A new name's first ingest is a cache MISS, so it fetches fresh regardless of force_refresh."""
    calls: list = []
    monkeypatch.setattr(eod_loader, "polite_get", _fake_live(_payload(_FRESH), calls))

    bars = YahooPriceSource(cache_dir=tmp_path).get_bars("NEWCO", allow_live=True)  # no cache

    assert _dates(bars) == _FRESH
    assert len(calls) == 1


# --- the seam is behavior-preserving (a refactor, not a behavior change) -----------------------------


def test_yahoo_adapter_matches_fetch_eod_exactly(tmp_path):
    """Characterization: the Yahoo adapter yields IDENTICAL normalized bars to the pre-seam fetch_eod for
    the same input — the seam doesn't drift the bars (the n=19 trust-validated path is unchanged).
    """
    _write_cache(tmp_path, _STALE)
    via_adapter = YahooPriceSource(cache_dir=tmp_path).get_bars("DEVCO", allow_live=False)
    via_fetch = fetch_eod("DEVCO", cache_dir=tmp_path, allow_live=False)
    assert via_adapter == via_fetch


def test_stooq_adapter_matches_parse(tmp_path):
    csv_text = "Date,Open,High,Low,Close,Volume\n2026-06-15,1,2,0.5,1.5,1000\n"
    (tmp_path / "DEVCO.csv").write_text(csv_text, encoding="utf-8")
    bars = StooqPriceSource(cache_dir=tmp_path).get_bars("DEVCO", allow_live=False)
    assert bars == parse_stooq_csv(csv_text)
