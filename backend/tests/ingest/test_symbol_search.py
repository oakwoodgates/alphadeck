"""``ingest/prices/symbol_search.py`` — cache-first Yahoo symbol search. No DB; a tmp cache dir + a
monkeypatched ``polite_get`` stand in for the live vendor (the ``test_price_source`` idiom).
"""

from __future__ import annotations

import json

import pytest

from ingest import CacheMiss
from ingest.prices import symbol_search
from ingest.prices.symbol_search import search_quotes


class _Resp:
    def __init__(self, payload: dict):
        self._p = payload
        self.text = json.dumps(payload)

    def json(self) -> dict:
        return self._p


def _fake_live(payload: dict, calls: list):
    def f(url, **kw):
        calls.append(url)
        return _Resp(payload)

    return f


_PAYLOAD = {
    "quotes": [{"symbol": "FDCTD", "shortname": "First Digital Corp", "quoteType": "EQUITY"}]
}


def test_cache_hit_returns_without_fetch(tmp_path, monkeypatch):
    (tmp_path / "fdct.json").write_text(json.dumps(_PAYLOAD), encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(symbol_search, "polite_get", _fake_live({"quotes": []}, calls))

    out = search_quotes("FDCT", cache_dir=tmp_path, allow_live=True)

    assert out == _PAYLOAD
    assert calls == []  # the cache hit never hit the network


def test_cache_miss_offline_raises(tmp_path):
    with pytest.raises(CacheMiss):
        search_quotes("NOPE", cache_dir=tmp_path, allow_live=False)


def test_cache_miss_live_fetches_and_caches(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(symbol_search, "polite_get", _fake_live(_PAYLOAD, calls))

    out = search_quotes("FDCT", cache_dir=tmp_path, allow_live=True)

    assert out == _PAYLOAD
    assert len(calls) == 1 and "q=FDCT" in calls[0]  # fetched once, the query rode the URL
    # the payload was cached — a second cache-first read needs no network
    calls.clear()
    again = search_quotes("FDCT", cache_dir=tmp_path, allow_live=False)
    assert again == _PAYLOAD and calls == []


def test_name_query_slugs_to_a_stable_cache_key(tmp_path, monkeypatch):
    calls: list = []
    payload = {
        "quotes": [{"symbol": "VREOF", "shortname": "Vireo Growth Inc.", "quoteType": "EQUITY"}]
    }
    monkeypatch.setattr(symbol_search, "polite_get", _fake_live(payload, calls))

    search_quotes("Vireo Growth", cache_dir=tmp_path, allow_live=True)

    assert (tmp_path / "vireo-growth.json").exists()  # name → slugged, stable key


def test_blank_query_is_empty_without_fetch(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(symbol_search, "polite_get", _fake_live(_PAYLOAD, calls))
    assert search_quotes("   ", cache_dir=tmp_path, allow_live=True) == {"quotes": []}
    assert calls == []  # a blank name (uncovered) searches to nothing, no network
