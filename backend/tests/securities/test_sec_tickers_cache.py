"""The cached SEC ticker map — the atomic replace the weekly refresh writes, and the cache-only, per-file-version
memo the nightly's rename guard reads (``sec_tickers.cached_tickers_for_cik``). Pure: tmp dirs, no network.
"""

from __future__ import annotations

import json
import os

import pytest

from ingest import CacheMiss
from securities import fund_tickers, sec_tickers


def _file(*rows) -> dict:
    return {"fields": ["cik", "name", "ticker", "exchange"], "data": [list(r) for r in rows]}


def test_the_map_by_cik_is_cache_only_and_follows_a_replaced_file(tmp_path):
    assert sec_tickers.cached_tickers_by_cik(tmp_path) is None  # no copy: "not read", never a fetch
    assert sec_tickers.cached_tickers_for_cik("0000000001", tmp_path) is None
    sec_tickers.store_raw(
        _file((1, "Old", "PSKY", "Nasdaq"), (1, "Old", "PSKYW", "Nasdaq")), tmp_path
    )
    assert sec_tickers.cached_tickers_for_cik("1", tmp_path) == (
        "PSKY",
        "PSKYW",
    )  # zero-padded match
    assert sec_tickers.cached_tickers_for_cik("0000000002", tmp_path) == ()  # the map lists none
    path = tmp_path / "company_tickers_exchange.json"
    st = path.stat()
    sec_tickers.store_raw(_file((1, "New", "SKYD", "Nasdaq")), tmp_path)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))  # a NEW file version
    assert sec_tickers.cached_tickers_for_cik("0000000001", tmp_path) == ("SKYD",)
    assert not list(tmp_path.glob("*.tmp"))  # the atomic replace leaves no temp behind


def test_read_cached_raw_is_a_cache_miss_without_a_copy(tmp_path):
    with pytest.raises(CacheMiss):
        sec_tickers.read_cached_raw(tmp_path)
    assert sec_tickers.cached_at(tmp_path) is None
    raw = _file((320193, "Apple Inc.", "AAPL", "Nasdaq"))
    sec_tickers.store_raw(raw, tmp_path)
    assert sec_tickers.read_cached_raw(tmp_path) == raw
    assert sec_tickers.quadruples(raw) == [("0000320193", "AAPL", "Apple Inc.", "Nasdaq")]
    assert sec_tickers.cached_at(tmp_path) is not None


def test_the_fund_list_refresh_replaces_the_cached_copy(tmp_path, monkeypatch):
    old = {"fields": ["cik", "seriesId", "classId", "symbol"], "data": [[1, "S1", "C1", "OLDF"]]}
    new = {
        "fields": ["cik", "seriesId", "classId", "symbol"],
        "data": [[1, "S1", "C1", "OLDF"], [2, "S2", "C2", "WGMI"]],
    }
    (tmp_path / "company_tickers_mf.json").write_text(json.dumps(old), encoding="utf-8")
    assert (
        fund_tickers.resolve("WGMI", cache_dir=tmp_path) is None
    )  # launched after the cached copy
    monkeypatch.setattr(fund_tickers, "_fetch_live", lambda ua: new)
    assert fund_tickers.refresh(cache_dir=tmp_path) == 2
    hit = fund_tickers.resolve("WGMI", cache_dir=tmp_path)
    assert hit is not None and hit.series_id == "S2"
    monkeypatch.setattr(fund_tickers, "_fetch_live", lambda ua: {"oops": True})
    with pytest.raises(ValueError):
        fund_tickers.refresh(cache_dir=tmp_path)
    assert (
        fund_tickers.resolve("WGMI", cache_dir=tmp_path) is not None
    )  # a bad payload keeps the copy
