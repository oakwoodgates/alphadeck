"""F1 — the FundSharesSource adapters + the issuer-first composite (no DB, no network).

The adapters are exercised through seeded cache files (the fetchers are cache-first, so a warm cache
IS the offline path); the composite's policy — issuer hit wins / clean miss falls through quietly /
issuer ERROR warns visibly then falls back / both legs failing raises with both stories — is walked
with stub sources.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from ingest.funds import snapshot_loader
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


def test_default_fund_source_is_globalx_then_stockanalysis():
    composite = default_fund_source()
    assert isinstance(composite._issuer, GlobalXFundSource)
    assert isinstance(composite._fallback, StockAnalysisFundSource)
