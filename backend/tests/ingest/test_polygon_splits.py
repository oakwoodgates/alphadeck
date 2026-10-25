"""Massive.com's split reference (split plan S1) over the REAL response pages (``tests/fixtures/splits/polygon``,
the diagnosis's 2025-08 pull filtered to its tickers) — paging, exact decimals, the per-day cache, the off
switch, and that neither a ticker nor the key ever reaches a URL or a message. No network."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

import ingest.polygon_splits as ps
from ingest import CacheMiss
from tests.split_fixtures import polygon_pages, polygon_row, polygon_source


def test_a_real_page_parses_exactly():
    rows, nxt, skipped = ps.parse_splits_page(polygon_pages()[0])
    assert len(rows) == 11 and skipped == 0
    assert nxt and nxt.startswith("https://api.polygon.io/v3/reference/splits?cursor=")
    smx = next(r for r in rows if r.ticker == "SMX" and r.execution_date == date(2025, 11, 18))
    assert (smx.split_from, smx.split_to) == (Decimal(8), Decimal(1))
    # fractional ratios stay exact decimals, never floats
    pbm = polygon_row("PBM", "2026-02-02")
    assert (pbm.split_from, pbm.split_to) == (Decimal("6.25"), Decimal(1))
    assert polygon_row("SMX", "2026-02-17").split_from == Decimal("4.8828125")


def test_all_pages_follow_the_cursor_and_cache_per_day(tmp_path):
    src, calls = polygon_source(tmp_path)
    pull = src.splits_since(date(2025, 8, 1), allow_live=True)
    assert (pull.pages, pull.live_pages, len(pull.rows), pull.skipped) == (3, 3, 32, 0)
    assert len(calls) == 3
    # the request carries a DATE only — no ticker, no thesis term — and never the key
    assert all("ticker" not in u and "apiKey" not in u for u in calls)
    assert "execution_date.gte=2025-08-01" in calls[0]
    # the same day: every page from the cache, no request
    again = src.splits_since(date(2025, 8, 1), allow_live=True)
    assert (again.pages, again.live_pages, len(again.rows)) == (3, 0, 32) and len(calls) == 3
    # cache-only on a cold day is a CacheMiss, never a silent empty list
    cold, _ = polygon_source(tmp_path, today=date(2026, 10, 21))
    with pytest.raises(CacheMiss):
        cold.splits_since(date(2025, 8, 1), allow_live=False)


def test_an_unreadable_row_is_skipped_and_counted():
    rows, nxt, skipped = ps.parse_splits_page(
        '{"results": [{"id": "E1", "ticker": "ABC", "execution_date": "2026-01-02", "split_from": 10, '
        '"split_to": 1}, {"id": "E2", "ticker": "XYZ", "execution_date": "2026-01-02", "split_from": 0, '
        '"split_to": 1}, {"ticker": "NOID", "execution_date": "2026-01-02"}], "status": "OK"}'
    )
    assert [r.ticker for r in rows] == ["ABC"] and skipped == 2 and nxt is None


def test_a_runaway_cursor_is_refused(tmp_path):
    page = '{"results": [], "next_url": "https://api.polygon.io/v3/reference/splits?cursor=again"}'
    n = {"i": 0}

    def fetch(url):
        n["i"] += 1
        return page.replace("again", f"again{n['i']}")

    src = ps.PolygonSplitSource(api_key="k-test", cache_dir=tmp_path, fetch=fetch)
    with pytest.raises(ps.PolygonSplitsError, match="runaway cursor"):
        src.splits_since(date(2025, 8, 1), allow_live=True)
    assert n["i"] == ps.MAX_PAGES


def test_the_key_never_leaks_into_an_error(tmp_path, monkeypatch):
    def boom(url, **_kw):
        raise RuntimeError(f"connection failed for {url}")

    monkeypatch.setattr(ps, "polite_get", boom)
    src = ps.PolygonSplitSource(api_key="sekrit-key-123", cache_dir=tmp_path)
    with pytest.raises(ps.PolygonSplitsError) as err:
        src.splits_since(date(2025, 8, 1), allow_live=True)
    assert "sekrit-key-123" not in str(err.value) and "***" in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__


def test_no_key_is_the_off_switch(monkeypatch):
    class _S:
        polygon_api_key = None

    monkeypatch.setattr(ps, "get_settings", lambda: _S())
    assert ps.default_split_source() is None
