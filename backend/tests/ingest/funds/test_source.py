"""F1 — the FundSharesSource adapters + the primary-first composite (no DB, no network).

The adapters are exercised through seeded cache files (the fetchers are cache-first, so a warm cache
IS the offline path); the composite's policy — primary hit wins / clean miss falls through quietly /
primary ERROR warns visibly then falls back / both legs failing raises with both stories — is walked
with stub sources. ``default_fund_source`` composes POLYGON-primary when POLYGON_API_KEY is present
(the operator's 2026-07-26 decision) and stays the scraper pair keyless — both shapes pinned here.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from domain.settings import get_settings
from ingest.funds import snapshot_loader
from ingest.funds.polygon import PolygonFundSource
from ingest.funds.source import (
    FundSharesUnavailable,
    GlobalXFundSource,
    IssuerFirstFundSource,
    StockAnalysisFundSource,
    default_fund_source,
)

_FIXT = Path(__file__).resolve().parents[2] / "fixtures" / "funds"


def _seed(tmp_path, name: str, fixture: str) -> None:
    (tmp_path / name).write_text((_FIXT / fixture).read_text(encoding="utf-8"), encoding="utf-8")


# --- the two adapters (through their cache-first fetchers) -------------------------------------------


def test_globalx_adapter_normalizes_the_snapshot(tmp_path):
    _seed(tmp_path, "URA.globalx.html", "globalx_ura_snippet.html")
    snap = GlobalXFundSource(cache_dir=tmp_path).get_snapshot("URA")
    assert snap == {
        "d": date(2026, 7, 24),
        "shares_out": 138771666.0,
        "source": "globalx",
        "source_ref": "https://www.globalxetfs.com/funds/ura",
    }


def test_stockanalysis_adapter_normalizes_the_snapshot(tmp_path):
    _seed(tmp_path, "URA.stockanalysis.html", "stockanalysis_ura_snippet.html")
    snap = StockAnalysisFundSource(cache_dir=tmp_path).get_snapshot("URA")
    assert snap == {
        "d": date(2026, 7, 24),
        "shares_out": 138770000.0,
        "source": "stockanalysis",
        "source_ref": "https://stockanalysis.com/etf/ura/",
    }


def test_adapter_404_is_a_clean_miss_not_an_error(tmp_path, monkeypatch):
    """A 404 = "this fund is not on this site" (another family's fund on the issuer page) — None, so
    the composite quietly falls through to the fallback."""

    def raise_404(url, **kw):
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("404", request=req, response=httpx.Response(404, request=req))

    monkeypatch.setattr(snapshot_loader, "polite_get", raise_404)
    assert GlobalXFundSource(cache_dir=tmp_path).get_snapshot("SMH", allow_live=True) is None


def test_adapter_page_without_the_blob_is_a_miss(tmp_path):
    (tmp_path / "XYZ.globalx.html").write_text("<html>a redesign</html>", encoding="utf-8")
    assert GlobalXFundSource(cache_dir=tmp_path).get_snapshot("XYZ") is None


# --- the issuer-first composite ----------------------------------------------------------------------


class _Stub:
    def __init__(self, result=None, error: Exception | None = None):
        self.result, self.error, self.calls = result, error, 0

    def get_snapshot(self, ticker, *, allow_live=False, force_refresh=False):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


_SNAP = {"d": date(2026, 7, 24), "shares_out": 1.0, "source": "globalx", "source_ref": "u"}


def test_issuer_hit_wins_and_the_fallback_is_never_spent():
    issuer, fallback = _Stub(result=_SNAP), _Stub(result={**_SNAP, "source": "stockanalysis"})
    snap = IssuerFirstFundSource(issuer, fallback).get_snapshot("URA")
    assert snap["source"] == "globalx"
    assert fallback.calls == 0  # issuer-first means the fallback GET is not even made


def test_issuer_miss_falls_through_quietly(capsys):
    issuer, fallback = _Stub(result=None), _Stub(result={**_SNAP, "source": "stockanalysis"})
    snap = IssuerFirstFundSource(issuer, fallback).get_snapshot("SMH")
    assert snap["source"] == "stockanalysis"
    assert "warn" not in capsys.readouterr().out  # a clean miss is the common case — quiet


def test_issuer_error_warns_visibly_then_falls_back(capsys):
    issuer = _Stub(error=RuntimeError("issuer redesign broke the parse"))
    fallback = _Stub(result={**_SNAP, "source": "stockanalysis"})
    snap = IssuerFirstFundSource(issuer, fallback).get_snapshot("URA")
    assert snap["source"] == "stockanalysis"  # the sample still lands
    out = capsys.readouterr().out
    assert "warn" in out and "issuer" in out  # …but the breakage never hides


def test_both_legs_failing_raises_with_both_stories():
    composite = IssuerFirstFundSource(_Stub(error=RuntimeError("issuer down")), _Stub(result=None))
    with pytest.raises(FundSharesUnavailable, match="issuer down.*fallback: no coverage"):
        composite.get_snapshot("GHOST")


def test_default_fund_source_keyless_is_globalx_then_stockanalysis(monkeypatch):
    """No POLYGON_API_KEY → exactly the pre-polygon behavior (the absence of the key is the off switch)."""
    monkeypatch.delenv("POLYGON_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        composite = default_fund_source()
        assert isinstance(composite._issuer, GlobalXFundSource)
        assert isinstance(composite._fallback, StockAnalysisFundSource)
    finally:
        get_settings.cache_clear()


def test_default_fund_source_goes_polygon_primary_when_the_key_is_present(monkeypatch):
    """With the key: polygon leads, and its fallback IS the whole scraper composite, nested — a polygon
    miss/gap degrades to exactly the keyless chain."""
    monkeypatch.setenv("POLYGON_API_KEY", "k-test")
    get_settings.cache_clear()
    try:
        composite = default_fund_source()
        assert isinstance(composite._issuer, PolygonFundSource)
        scrapers = composite._fallback
        assert isinstance(scrapers, IssuerFirstFundSource)
        assert isinstance(scrapers._issuer, GlobalXFundSource)
        assert isinstance(scrapers._fallback, StockAnalysisFundSource)
    finally:
        get_settings.cache_clear()


def test_polygon_gap_falls_through_the_whole_scraper_chain():
    """The nested composition end-to-end: polygon None (a null-count gap date) → globalx None (not a
    Global X fund) → the aggregator lands the sample."""
    poly, gx = _Stub(result=None), _Stub(result=None)
    sa = _Stub(result={**_SNAP, "source": "stockanalysis"})
    snap = IssuerFirstFundSource(poly, IssuerFirstFundSource(gx, sa)).get_snapshot("SMH")
    assert snap["source"] == "stockanalysis"
    assert (poly.calls, gx.calls, sa.calls) == (1, 1, 1)


def test_composite_labels_the_primary_leg_by_its_adapter_name(capsys):
    """A polygon failure warns AS polygon (never masquerading as 'issuer' trouble) and its story carries
    the name — the operator debugging a bad key sees which leg broke."""

    class _NamedStub(_Stub):
        name = "polygon"

    composite = IssuerFirstFundSource(
        _NamedStub(error=RuntimeError("HTTP 401")), _Stub(result=None)
    )
    with pytest.raises(FundSharesUnavailable, match="polygon: HTTP 401"):
        composite.get_snapshot("URA")
    assert "fund-shares polygon leg failed" in capsys.readouterr().out
