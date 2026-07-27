"""F1 — the fund-page parsers (REAL page snippets, saved 2026-07-26) + the cache/freshness contract.

The fixtures are literal excerpts of the live pages the adapters scrape (globalxetfs.com/funds/ura/,
stockanalysis.com/etf/URA/), so the parse anchors are proven against the real shapes offline; a live
smoke per adapter runs separately (not in the suite). The cache tests mirror test_price_source.py —
the fund cache is a homogeneous daily cache on the PRICES freshness mechanism (force_refresh), not
EDGAR's key-classed TTL.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from ingest import CacheMiss
from ingest.funds import snapshot_loader
from ingest.funds.snapshot_loader import (
    fetch_globalx,
    fetch_stockanalysis,
    parse_globalx,
    parse_stockanalysis,
)

_FIXT = Path(__file__).resolve().parents[2] / "fixtures" / "funds"
_GX_URA = (_FIXT / "globalx_ura_snippet.html").read_text(encoding="utf-8")
_SA_URA = (_FIXT / "stockanalysis_ura_snippet.html").read_text(encoding="utf-8")


# --- the parsers, against the REAL page shapes -------------------------------------------------------


def test_parse_globalx_extracts_exact_count_and_the_issuers_stated_asof():
    snap = parse_globalx(_GX_URA)
    assert snap == {"d": date(2026, 7, 24), "shares_out": 138771666.0}


def test_parse_stockanalysis_extracts_rounded_count_and_the_trading_date():
    snap = parse_stockanalysis(_SA_URA)
    # "138.77M" — the aggregator's ~10k-share rounded resolution (the source column records which)
    assert snap == {"d": date(2026, 7, 24), "shares_out": 138770000.0}


def test_parse_stockanalysis_suffix_math():
    html = 'quote:{c:1,td:"2026-07-24",v:9}, sharesOut:"21.86M"'
    assert parse_stockanalysis(html)["shares_out"] == 21860000.0
    html_b = 'quote:{c:1,td:"2026-07-24",v:9}, sharesOut:"1.5B"'
    assert parse_stockanalysis(html_b)["shares_out"] == 1500000000.0


def test_parse_globalx_misses_are_none_never_a_guess():
    assert parse_globalx("<html>no fund blob here</html>") is None
    # a count WITHOUT the blob's own stated date is not a sample (#1: never assume "today")
    assert parse_globalx('ETF_DETAILS\\":{\\"SHARES_OUTSTANDING\\":123}') is None


def test_parse_stockanalysis_misses_are_none_never_a_guess():
    assert parse_stockanalysis("<html>nothing embedded</html>") is None
    # a count without the quote's trading date is not a sample
    assert parse_stockanalysis('sharesOut:"12.3M"') is None
    # etd (extended-hours date) must not satisfy the td anchor
    assert parse_stockanalysis('quote:{c:1,etd:"2026-07-24"}, sharesOut:"12.3M"') is None


# --- the cache/freshness contract (the fetch_eod semantics, shared by both fetchers) -----------------


class _Resp:
    def __init__(self, text: str):
        self.text = text


def _fake_live(text: str, calls: list):
    def f(url, **kw):
        calls.append(url)
        return _Resp(text)

    return f


def test_cache_first_returns_stale_without_force(tmp_path, monkeypatch):
    (tmp_path / "URA.globalx.html").write_text("STALE", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(snapshot_loader, "polite_get", _fake_live("FRESH", calls))

    html = fetch_globalx("URA", cache_dir=tmp_path, allow_live=True)  # no force

    assert html == "STALE" and calls == []


def test_force_refresh_repulls_and_overwrites_the_cache(tmp_path, monkeypatch):
    (tmp_path / "URA.globalx.html").write_text("STALE", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(snapshot_loader, "polite_get", _fake_live("FRESH", calls))

    html = fetch_globalx("URA", cache_dir=tmp_path, allow_live=True, force_refresh=True)

    assert html == "FRESH" and len(calls) == 1
    # overwritten — a subsequent cache-first read sees the fresh page
    assert fetch_globalx("URA", cache_dir=tmp_path, allow_live=True) == "FRESH"


def test_force_refresh_offline_stays_cache_first(tmp_path, monkeypatch):
    (tmp_path / "URA.stockanalysis.html").write_text("STALE", encoding="utf-8")
    calls: list = []
    monkeypatch.setattr(snapshot_loader, "polite_get", _fake_live("FRESH", calls))

    html = fetch_stockanalysis("URA", cache_dir=tmp_path, allow_live=False, force_refresh=True)

    assert html == "STALE" and calls == []


def test_cache_miss_fetches_even_without_force(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(snapshot_loader, "polite_get", _fake_live("FRESH", calls))

    assert fetch_stockanalysis("NEWFUND", cache_dir=tmp_path, allow_live=True) == "FRESH"
    assert len(calls) == 1


def test_cold_cache_offline_raises_cache_miss(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshot_loader, "polite_get", _fake_live("FRESH", []))
    with pytest.raises(CacheMiss):
        fetch_globalx("URA", cache_dir=tmp_path, allow_live=False)
