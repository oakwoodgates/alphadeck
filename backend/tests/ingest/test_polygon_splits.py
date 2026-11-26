"""Massive.com's split reference (split plan S1) over the REAL response pages (``tests/fixtures/splits/polygon``:
the diagnosis's 2025-08 ``/v3/reference/splits`` pull and the 2026-11-25 ``/stocks/v1/splits`` pull, each filtered
to its tickers) — paging, exact decimals, Massive.com's label (W6-1), the path setting, the per-day cache, the off
switch, and that neither a ticker nor the key ever reaches a URL or a message. No network."""

from __future__ import annotations

import dataclasses
import json
from datetime import date
from decimal import Decimal

import pytest

import ingest.polygon_splits as ps
from domain.settings import get_settings
from domain.splits import PolygonEvidence
from ingest import CacheMiss
from tests.split_fixtures import V1_PAGE, polygon_pages, polygon_row, polygon_source, v1_source


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


# --- W6-1: the reader on /stocks/v1/splits (the 2026-11-25 pull, ``v1_2026-11-25.json``) -----------------------


def _v1_rows() -> dict[tuple[str, str], PolygonEvidence]:
    rows, nxt, skipped = ps.parse_splits_page(V1_PAGE.read_text(encoding="utf-8"))
    assert (len(rows), nxt, skipped) == (42, None, 0)
    return {(r.ticker, r.execution_date.isoformat()): r for r in rows}


def test_a_v1_page_carries_massives_label_on_every_row():
    by = _v1_rows()
    # the three words Massive.com serves, each on the row the steward knows it from
    hon = by[("HON", "2025-10-30")]
    assert (hon.adjustment_type, hon.split_from, hon.split_to) == (
        "forward_split",  # 1000 -> 1061: labelled a split, though x1.061
        Decimal(1000),
        Decimal(1061),
    )
    assert by[("SNDK", "2026-11-20")].adjustment_type == "forward_split"
    assert by[("GNS", "2026-11-20")].adjustment_type == "reverse_split"
    assert (
        by[("MTH", "2025-01-03")].adjustment_type == "stock_dividend"
    )  # 1 -> 2: x2.0, still a dividend
    scco = by[("SCCO", "2026-11-03")]
    assert (scco.adjustment_type, scco.split_to) == ("stock_dividend", Decimal("1.0106"))
    # an integral v1 value (``16.0``) parses as the v3 reader's ``16``: the same stored numerics either way
    btoc = by[("BTOC", "2026-10-15")]
    assert (str(btoc.split_from), str(btoc.split_to), btoc.adjustment_type) == (
        "16",
        "1",
        "reverse_split",
    )
    assert by[("PBM", "2026-02-02")].split_from == Decimal("6.25")


def test_the_v1_and_v3_readers_parse_the_same_row_alike_apart_from_the_label():
    """Every v3 fixture row (the 2025-08 pull) is in the v1 pull under the same Massive.com id, with the same ticker,
    first session and ratio, to the digit: only the label is new."""
    v1 = {r.id: r for r in _v1_rows().values()}
    n = 0
    for page in polygon_pages():
        rows, _, _ = ps.parse_splits_page(page)
        for r in rows:
            n += 1
            assert r.adjustment_type is None  # the v3 endpoint states no label
            assert dataclasses.replace(v1[r.id], adjustment_type=None) == r, r.id
            assert (str(v1[r.id].split_from), str(v1[r.id].split_to)) == (
                str(r.split_from),
                str(r.split_to),
            )
    assert n == 32


def test_historical_adjustment_factor_is_never_read():
    """It is CUMULATIVE over later splits (GPUS 2024-12-13: 4.21827 = 1.139 x the later events), so it changes
    whenever a later split lands: never part of a row. A row without it (JZ's announced split) parses like any
    other, and a changed factor parses to the same rows."""
    assert "historical_adjustment_factor" not in {
        f.name for f in dataclasses.fields(PolygonEvidence)
    }
    by = _v1_rows()
    assert by[("JZ", "2026-12-24")].split_from == Decimal(15)  # one of the 97 rows with no factor
    body = json.loads(V1_PAGE.read_text(encoding="utf-8"))
    for r in body["results"]:
        if "historical_adjustment_factor" in r:
            r["historical_adjustment_factor"] = 999.0
    rows, _, skipped = ps.parse_splits_page(json.dumps(body))
    assert skipped == 0 and {(r.ticker, r.execution_date.isoformat()): r for r in rows} == by


def test_an_unknown_label_is_none_and_counted_never_a_skipped_row(tmp_path):
    # SYNTHESIZED (marked): no real row carries an unknown or missing label (the 3,521-row 2026-11-25 pull: 596
    # forward_split, 2,527 reverse_split, 398 stock_dividend). The real HON and SNDK rows, the label changed / dropped.
    body = json.loads(V1_PAGE.read_text(encoding="utf-8"))
    for r in body["results"]:
        if r["ticker"] == "HON":
            r["adjustment_type"] = "spin_off"  # SYNTHESIZED: a word Massive.com has never served
        if r["ticker"] == "SNDK":
            del r["adjustment_type"]  # SYNTHESIZED: a row with no label
    text = json.dumps(body)
    rows, _, skipped = ps.parse_splits_page(text)
    by = {r.ticker: r for r in rows}
    assert (
        skipped == 0 and len(rows) == 42
    )  # the row is kept: a label is evidence, never a reason to drop it
    assert by["HON"].adjustment_type is None and by["SNDK"].adjustment_type is None
    assert by["HON"].split_to == Decimal(1061)
    src = ps.PolygonSplitSource(
        api_key="k-test",
        cache_dir=tmp_path,
        fetch=lambda url: text,
        today=lambda: date(2026, 11, 25),
    )
    pull = src.splits_since(date(2024, 10, 1), allow_live=True)
    assert (len(pull.rows), pull.skipped, pull.unlabeled) == (42, 0, 2)


def test_the_v1_request_is_one_page_by_date(tmp_path):
    """The 2026-11-25 pull from 2024-10-01 was 3,521 rows in ONE response (``limit`` 5000, no ``next_url``)."""
    src, calls = v1_source(tmp_path, today=date(2026, 11, 25))
    pull = src.splits_since(date(2024, 10, 1), allow_live=True)
    base = get_settings().polygon_base
    assert calls == [
        f"{base}/stocks/v1/splits?execution_date.gte=2024-10-01&limit=5000&sort=execution_date.asc"
    ]
    assert (pull.pages, pull.live_pages, len(pull.rows), pull.skipped, pull.unlabeled) == (
        1,
        1,
        42,
        0,
        0,
    )
    assert ps.PAGE_LIMIT == 5000 > 3521


def test_the_path_setting_falls_back_to_the_v3_endpoint_and_its_parse(tmp_path, monkeypatch):
    """``ALPHADECK_POLYGON_SPLITS_PATH=/v3/reference/splits`` (while the deprecated endpoint lives): the v3 URL, its
    cursor pages, no label and none counted. The cache key is the URL, so the switch is a cache miss, never the
    other endpoint's page."""
    base = get_settings().polygon_base
    v1, _ = v1_source(tmp_path, today=date(2026, 10, 20))
    v1.splits_since(date(2025, 8, 1), allow_live=True)  # today's v1 page is cached
    monkeypatch.setenv("ALPHADECK_POLYGON_SPLITS_PATH", "/v3/reference/splits")
    get_settings.cache_clear()
    try:
        assert get_settings().polygon_splits_path == ps.SPLITS_PATH_V3
        cold = ps.PolygonSplitSource(
            api_key="k-test",
            cache_dir=v1._cache_dir,  # the SAME cache directory and day as the v1 pull
            fetch=lambda url: pytest.fail("cache-only"),
            today=lambda: date(2026, 10, 20),
        )
        with pytest.raises(CacheMiss):
            cold.splits_since(date(2025, 8, 1), allow_live=False)
        src, calls = polygon_source(tmp_path)  # the real v3 pages behind the fake socket
        pull = src.splits_since(date(2025, 8, 1), allow_live=True)
        assert calls[0] == (
            f"{base}/v3/reference/splits?execution_date.gte=2025-08-01&limit=1000&order=asc"
            "&sort=execution_date"
        )
        assert (pull.pages, len(pull.rows), pull.skipped, pull.unlabeled) == (3, 32, 0, 0)
        assert all(r.adjustment_type is None for r in pull.rows)
    finally:
        get_settings.cache_clear()
