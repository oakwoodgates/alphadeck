from __future__ import annotations

import json

import pytest

from domain.settings import get_settings
from ingest import CacheMiss
from securities import figi

# map_cusip — the ETF overlap's CUSIP→US-ticker crosswalk (ETF Sleeve, Slice 2a+): batched OpenFIGI
# ID_CUSIP jobs, cache-first PER CUSIP with the negative answer cached too (a stable identifier map —
# both answers are paid for once). The live transport is faked at httpx.post (imported inside the
# fetcher); the RateLimiter is nooped where a multi-chunk test would otherwise sleep out the no-key
# request spacing.


def _seed(cache_dir, cusip: str, ticker: str | None) -> None:
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / f"cusip-{cusip}.json").write_text(
        json.dumps({"cusip": cusip, "ticker": ticker}), encoding="utf-8"
    )


class _NoopLimiter:
    def __init__(self, *a, **k) -> None: ...

    def acquire(self) -> None: ...


@pytest.fixture
def figi_post(monkeypatch):
    """Capture OpenFIGI POST bodies; serve a canned per-job answer (position-parallel, like the real
    /mapping). NVDA-style match for 67066G104; 'no identifier found' for everything else."""
    calls: list[dict] = []

    class _Resp:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self) -> None: ...

        def json(self):
            out = []
            for job in self._body:
                if job["idValue"] == "67066G104":
                    out.append({"data": [{"ticker": "NVDA", "name": "NVIDIA CORP"}]})
                else:
                    out.append({"error": "No identifier found."})
            return out

    def _post(url, json=None, headers=None, timeout=None):
        calls.append({"url": url, "body": json, "headers": headers})
        return _Resp(json)

    monkeypatch.setattr("httpx.post", _post)
    monkeypatch.setattr("ingest.http.RateLimiter", _NoopLimiter)
    return calls


def test_cache_first_serves_offline_and_omits_the_cached_negative(tmp_path):
    _seed(tmp_path, "67066G104", "NVDA")
    _seed(tmp_path, "11135F101", None)  # the cached NO-MATCH — asked once, never again
    got = figi.map_cusip(["67066G104", "11135F101"], cache_dir=tmp_path, allow_live=False)
    assert got == {
        "67066G104": "NVDA"
    }  # the negative is omitted, not an error (#9: shown unresolved)


def test_uncached_with_live_disabled_raises_cachemiss(tmp_path):
    _seed(tmp_path, "67066G104", "NVDA")
    with pytest.raises(CacheMiss):
        figi.map_cusip(["67066G104", "999999999"], cache_dir=tmp_path, allow_live=False)


def test_live_fetch_maps_caches_both_answers_and_never_reasks(tmp_path, figi_post):
    got = figi.map_cusip(["67066G104", "874039100"], cache_dir=tmp_path, allow_live=True)
    assert got == {"67066G104": "NVDA"}
    assert len(figi_post) == 1  # one batch POST for both jobs
    [call] = figi_post
    assert call["body"] == [
        {"idType": "ID_CUSIP", "idValue": "67066G104", "exchCode": "US"},
        {"idType": "ID_CUSIP", "idValue": "874039100", "exchCode": "US"},
    ]
    # BOTH answers cached — incl. the negative — so a re-click re-asks NOTHING
    assert json.loads((tmp_path / "cusip-67066G104.json").read_text()) == {
        "cusip": "67066G104",
        "ticker": "NVDA",
    }
    assert json.loads((tmp_path / "cusip-874039100.json").read_text())["ticker"] is None
    again = figi.map_cusip(["67066G104", "874039100"], cache_dir=tmp_path, allow_live=True)
    assert again == {"67066G104": "NVDA"}
    assert len(figi_post) == 1  # no second POST


def test_no_key_batches_in_tens_with_key_in_hundreds(tmp_path, figi_post, monkeypatch):
    cusips = [f"{i:09d}" for i in range(12)]
    monkeypatch.setattr(get_settings(), "openfigi_api_key", None)
    figi.map_cusip(cusips, cache_dir=tmp_path / "a", allow_live=True)
    assert [len(c["body"]) for c in figi_post] == [10, 2]  # the documented no-key jobs cap
    assert "X-OPENFIGI-APIKEY" not in figi_post[0]["headers"]
    figi_post.clear()
    monkeypatch.setattr(get_settings(), "openfigi_api_key", "test-key")
    figi.map_cusip(cusips, cache_dir=tmp_path / "b", allow_live=True)
    assert [len(c["body"]) for c in figi_post] == [12]  # one POST under the with-key cap
    assert figi_post[0]["headers"]["X-OPENFIGI-APIKEY"] == "test-key"


def test_input_is_deduped_normalized_and_blank_safe(tmp_path, figi_post):
    got = figi.map_cusip(
        ["67066g104", "67066G104", "", None, "  "],  # type: ignore[list-item]
        cache_dir=tmp_path,
        allow_live=True,
    )
    assert got == {"67066G104": "NVDA"}
    assert [len(c["body"]) for c in figi_post] == [1]  # one job — dupes/blanks never reach the wire
