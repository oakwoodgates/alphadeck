from __future__ import annotations

from pathlib import Path

import pytest

from ingest import CacheMiss
from ingest.edgar.client import EdgarClient

_CACHE = Path(__file__).resolve().parent.parent / "fixtures" / "edgar"


def _client() -> EdgarClient:
    return EdgarClient(cache_dir=_CACHE, allow_live=False)


def test_cache_hit_returns_content():
    data = _client().get_json("https://data.sec.gov/ignored", "cached_sample.json")
    assert data["filings"]["recent"]["form"] == ["4"]


def test_cache_miss_raises_when_live_disabled():
    with pytest.raises(CacheMiss):
        _client().get_text("https://data.sec.gov/ignored", "does_not_exist.json")
