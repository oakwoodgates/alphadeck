"""The Polygon fund-shares adapter — REAL captured responses (2026-07-26, key-stripped), no network.

The three fixtures pin the three live shapes the adapter must read honestly: a populated date (URA —
138,771,666, the exact issuer-page count), a null-gap date (URA @ 2026-06-15 — the count field is
ABSENT on the wire), and a non-ETF ticker (AAPL — type ``CS`` with a fully populated count, so the
TYPE gate is what rejects it, not a missing number). The politeness contract (5/min spacing + the
61s 429 wait) and the key-never-leaks rule are walked with stubs; the live smoke runs separately.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

from domain.settings import Settings, get_settings
from ingest import CacheMiss
from ingest.funds import polygon as poly_mod
from ingest.funds.polygon import (
    PolygonError,
    PolygonFundSource,
    parse_polygon,
    polygon_ticker_url,
)

_FIXT = Path(__file__).resolve().parents[2] / "fixtures" / "funds"
_URA = (_FIXT / "polygon_ura.json").read_text(encoding="utf-8")
_URA_GAP = (_FIXT / "polygon_ura_null_gap.json").read_text(encoding="utf-8")
_AAPL = (_FIXT / "polygon_aapl_non_etf.json").read_text(encoding="utf-8")

_D = date(2026, 7, 26)
_KEY = "k-test-secret"


def _src(tmp_path) -> PolygonFundSource:
    return PolygonFundSource(api_key=_KEY, cache_dir=tmp_path)


def _seed(tmp_path, ticker: str, d: date, body: str) -> None:
    (tmp_path / f"{ticker}.polygon.{d.isoformat()}.json").write_text(body, encoding="utf-8")


class _NoWaitLimiter:
    """Stands in for the module limiter: records acquires, never sleeps (tests must not wait 13s)."""

    def __init__(self):
        self.acquires = 0

    def acquire(self):
        self.acquires += 1


@pytest.fixture
def limiter(monkeypatch) -> _NoWaitLimiter:
    lim = _NoWaitLimiter()
    monkeypatch.setattr(poly_mod, "_LIMITER", lim)
    return lim


# --- the parser, against the REAL captured shapes ----------------------------------------------------


def test_parse_populated_date_extracts_the_exact_integer_count():
    assert parse_polygon(_URA) == 138771666.0  # the true count — matches the issuer page exactly


def test_parse_null_gap_date_is_none_never_a_zero():
    """The measured gap shape: the count field is ABSENT from the wire on that date — no sample (#9)."""
    assert parse_polygon(_URA_GAP) is None
    # the literal-null rendering of the same gap parses the same way
    literal_null = _URA_GAP.replace('"round_lot":100', '"share_class_shares_outstanding":null')
    assert parse_polygon(literal_null) is None


def test_parse_non_etf_type_is_rejected_by_the_type_gate_not_the_count():
    """AAPL carries a fully populated count (14,687,356,000) — the ``type != 'ETF'`` gate is what
    refuses it: the share-class read is only trusted for funds."""
    assert json.loads(_AAPL)["results"]["share_class_shares_outstanding"] > 0  # count IS there
    assert parse_polygon(_AAPL) is None


def test_parse_malformed_payloads_are_misses_never_guesses():
    assert parse_polygon("not json") is None
    assert parse_polygon('{"status":"OK"}') is None  # no results
    assert parse_polygon('{"results":[]}') is None  # wrong shape
    assert parse_polygon('{"results":{"type":"ETF","share_class_shares_outstanding":true}}') is None


# --- the adapter (through its cache-first fetcher) ---------------------------------------------------


def test_get_snapshot_at_stamps_the_queried_date_and_a_keyless_source_ref(tmp_path):
    _seed(tmp_path, "URA", _D, _URA)
    snap = _src(tmp_path).get_snapshot_at("URA", _D)
    assert snap == {
        "d": _D,  # the QUERIED date — polygon states no effective date of its own
        "shares_out": 138771666.0,
        "source": "polygon",
        "source_ref": "https://api.polygon.io/v3/reference/tickers/URA?date=2026-07-26",
    }
    assert _KEY not in snap["source_ref"]  # the stored provenance never carries the key


def test_get_snapshot_queries_today(tmp_path):
    today = date.today()
    _seed(tmp_path, "URA", today, _URA)
    snap = _src(tmp_path).get_snapshot("URA")  # the forward daily sample
    assert snap is not None and snap["d"] == today


def test_gap_and_non_etf_responses_are_clean_misses(tmp_path):
    _seed(tmp_path, "URA", _D, _URA_GAP)
    _seed(tmp_path, "AAPL", _D, _AAPL)
    assert _src(tmp_path).get_snapshot_at("URA", _D) is None
    assert _src(tmp_path).get_snapshot_at("AAPL", _D) is None


def test_url_builder_uppercases_and_reads_the_settings_base(monkeypatch):
    assert (
        polygon_ticker_url("ura", _D)
        == "https://api.polygon.io/v3/reference/tickers/URA?date=2026-07-26"
    )
    monkeypatch.setenv("ALPHADECK_POLYGON_BASE", "https://mirror.example")
    get_settings.cache_clear()
    try:
        assert polygon_ticker_url("URA", _D).startswith("https://mirror.example/v3/")
    finally:
        get_settings.cache_clear()


def test_settings_reads_the_unprefixed_env_name(monkeypatch):
    monkeypatch.setenv("POLYGON_API_KEY", "abc")
    assert Settings().polygon_api_key == "abc"


def test_empty_api_key_refuses_construction():
    with pytest.raises(ValueError, match="api_key"):
        PolygonFundSource(api_key="")


# --- the cache/freshness contract (the fetch_eod semantics) ------------------------------------------


class _Resp:
    def __init__(self, text: str):
        self.text = text


def test_cache_first_returns_the_same_day_hit_without_force(tmp_path, monkeypatch, limiter):
    _seed(tmp_path, "URA", _D, "STALE")
    calls: list = []
    monkeypatch.setattr(poly_mod, "polite_get", lambda url, **kw: calls.append(url) or _Resp(_URA))
    assert _src(tmp_path)._fetch("u", "URA", _D, allow_live=True, force_refresh=False) == "STALE"
    assert calls == [] and limiter.acquires == 0  # a cache hit spends no slot


def test_force_refresh_repulls_and_overwrites(tmp_path, monkeypatch, limiter):
    _seed(tmp_path, "URA", _D, "STALE")
    monkeypatch.setattr(poly_mod, "polite_get", lambda url, **kw: _Resp(_URA))
    snap = _src(tmp_path).get_snapshot_at("URA", _D, allow_live=True, force_refresh=True)
    assert snap["shares_out"] == 138771666.0
    # overwritten — the next cache-first read serves the fresh body
    assert (tmp_path / "URA.polygon.2026-07-26.json").read_text(encoding="utf-8") == _URA


def test_a_new_day_is_a_structural_cache_miss(tmp_path, monkeypatch, limiter):
    """The file is keyed by (ticker, queried date): yesterday's cache can never serve today's sample —
    the daily forward pull re-fetches with or without force_refresh."""
    _seed(tmp_path, "URA", _D, _URA)  # yesterday's file
    calls: list = []
    monkeypatch.setattr(poly_mod, "polite_get", lambda url, **kw: calls.append(url) or _Resp(_URA))
    _src(tmp_path).get_snapshot_at("URA", date(2026, 7, 27), allow_live=True)  # no force
    assert len(calls) == 1


def test_offline_cold_cache_raises_cache_miss(tmp_path):
    with pytest.raises(CacheMiss):
        _src(tmp_path).get_snapshot_at("URA", _D, allow_live=False)


def test_offline_force_refresh_stays_cache_first(tmp_path, monkeypatch, limiter):
    _seed(tmp_path, "URA", _D, _URA)
    calls: list = []
    monkeypatch.setattr(poly_mod, "polite_get", lambda url, **kw: calls.append(url) or _Resp(_URA))
    snap = _src(tmp_path).get_snapshot_at("URA", _D, allow_live=False, force_refresh=True)
    assert snap["shares_out"] == 138771666.0 and calls == []


# --- politeness + the key-never-leaks rule -----------------------------------------------------------


def test_live_fetch_wires_the_limiter_the_61s_backoff_and_the_keyed_url(
    tmp_path, monkeypatch, limiter
):
    seen: dict = {}

    def rec(url, **kw):
        seen["url"], seen["kw"] = url, kw
        return _Resp(_URA)

    monkeypatch.setattr(poly_mod, "polite_get", rec)
    snap = _src(tmp_path).get_snapshot_at("URA", _D, allow_live=True)
    assert snap["shares_out"] == 138771666.0
    assert seen["url"].endswith(f"&apiKey={_KEY}")  # the socket DOES authenticate…
    assert _KEY not in snap["source_ref"]  # …but nothing stored does
    assert seen["kw"]["pre"] == limiter.acquire  # the 5/min spacing fronts every attempt
    assert seen["kw"]["backoff_base"] == 61.0 and seen["kw"]["backoff_cap"] == 61.0


def test_429_waits_the_minute_out_and_retries(tmp_path, monkeypatch, limiter):
    """The real polite_get path: a 429 sleeps ~61s (the per-minute window, injected sleeper) and the
    retry lands the sample."""
    req = httpx.Request("GET", "https://api.polygon.io/x")
    responses = [httpx.Response(429, request=req), httpx.Response(200, request=req, text=_URA)]
    monkeypatch.setattr(httpx, "get", lambda url, **kw: responses.pop(0))
    slept: list[float] = []
    monkeypatch.setattr(poly_mod, "_SLEEP", slept.append)

    snap = _src(tmp_path).get_snapshot_at("URA", _D, allow_live=True)

    assert snap["shares_out"] == 138771666.0
    assert slept == [61.0]  # waited the window, not an exponential 1-2s
    assert limiter.acquires == 2  # the spacing fronted BOTH attempts


def test_401_and_403_surface_clearly_without_the_key(tmp_path, monkeypatch, limiter):
    def raise_status(code):
        def f(url, **kw):
            req = httpx.Request("GET", url)
            raise httpx.HTTPStatusError(
                f"{code} for url '{url}'", request=req, response=httpx.Response(code, request=req)
            )

        return f

    monkeypatch.setattr(poly_mod, "polite_get", raise_status(401))
    with pytest.raises(PolygonError, match="HTTP 401.*bad POLYGON_API_KEY") as ei:
        _src(tmp_path).get_snapshot_at("URA", _D, allow_live=True)
    assert _KEY not in str(ei.value) and ei.value.__cause__ is None  # no key, no chained leak

    monkeypatch.setattr(poly_mod, "polite_get", raise_status(403))
    with pytest.raises(PolygonError, match="HTTP 403.*plan"):
        _src(tmp_path).get_snapshot_at("URA", _D, allow_live=True)


def test_404_is_a_clean_miss_not_an_error(tmp_path, monkeypatch, limiter):
    def raise_404(url, **kw):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))

    monkeypatch.setattr(poly_mod, "polite_get", raise_404)
    assert _src(tmp_path).get_snapshot_at("GHOST", _D, allow_live=True) is None


def test_network_error_messages_are_key_scrubbed(tmp_path, monkeypatch, limiter):
    def boom(url, **kw):
        raise RuntimeError(f"connect trouble at {url}")  # a message that embeds the keyed URL

    monkeypatch.setattr(poly_mod, "polite_get", boom)
    with pytest.raises(PolygonError) as ei:
        _src(tmp_path).get_snapshot_at("URA", _D, allow_live=True)
    assert _KEY not in str(ei.value) and "***" in str(ei.value)
