from __future__ import annotations

import os
import time
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


# --- R1: the key-classed cache TTL (the #72 bug, on the EDGAR client's mutable prefixes) ---
#
# The submissions index is MUTABLE (new filings appear) but was cached forever — the cron structurally could
# not see a Form 4 filed after the cache date (Form 4 discovery frozen ~11 days; DELL live 07-14 vs cached
# 06-30). Same shape on efts/ (the discovery universe) and companyfacts/ (the extract's share counts). Fix:
# get_text refreshes a stale MUTABLE key when live; forms/ (a filing document, immutable) caches forever.


def _live_client(cache_dir: Path, *, ttl_s: float = 12 * 3600) -> tuple[EdgarClient, list[str]]:
    """A client whose network fetch is stubbed to a marker, so 'did it refetch?' is observable offline."""
    calls: list[str] = []
    c = EdgarClient(cache_dir=cache_dir, allow_live=True, user_agent="test ua", cache_ttl_s=ttl_s)
    c._fetch = lambda url: (calls.append(url), '{"fresh": true}')[1]  # type: ignore[method-assign]
    return c, calls


def _write_cache(cache_dir: Path, key: str, body: str, *, age_s: float) -> Path:
    p = cache_dir / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    old = time.time() - age_s
    os.utime(p, (old, old))  # backdate mtime so the file reads as `age_s` old
    return p


def test_REPRODUCE_stale_submissions_index_hides_a_new_filing(tmp_path):
    """THE BUG (reproduce-first, #72 discipline): a submissions index older than the TTL must NOT be served
    stale on a live/recurring run — before the fix it was, so a new Form 4 was invisible."""
    _write_cache(tmp_path, "submissions/CIK0000000001.json", '{"stale": true}', age_s=13 * 3600)
    client, fetched = _live_client(tmp_path)

    got = client.get_json(
        "https://data.sec.gov/submissions/CIK0000000001.json", "submissions/CIK0000000001.json"
    )

    assert got == {"fresh": True}  # the NEW index, not the stale one
    assert len(fetched) == 1  # it actually re-pulled
    # and the refetch overwrote the cache, so it now reads fresh
    assert (tmp_path / "submissions/CIK0000000001.json").read_text() == '{"fresh": true}'


def test_fresh_submissions_within_ttl_is_served_from_cache(tmp_path):
    """A manual re-run minutes later must NOT re-pull 250 indexes — a within-TTL hit stays cached."""
    _write_cache(tmp_path, "submissions/CIK2.json", '{"cached": true}', age_s=1 * 3600)
    client, fetched = _live_client(tmp_path)
    got = client.get_json("https://data.sec.gov/x", "submissions/CIK2.json")
    assert got == {"cached": True} and fetched == []  # fresh cache, no network


def test_filing_documents_are_immutable_never_refetched_even_when_ancient(tmp_path):
    """forms/<accession>/<doc> is a filing DOCUMENT — an accession never changes. It must cache FOREVER,
    keeping the politeness win, no matter how old."""
    _write_cache(tmp_path, "forms/acc-1/doc.htm", "the filing", age_s=365 * 24 * 3600)
    client, fetched = _live_client(tmp_path)
    got = client.get_text("https://sec.gov/acc-1/doc.htm", "forms/acc-1/doc.htm")
    assert got == "the filing" and fetched == []  # immutable → served stale-forever, no refetch


def test_all_three_mutable_prefixes_refresh_when_stale(tmp_path):
    """Scope (a): submissions, companyfacts AND efts all get the TTL — three freezes, one fix."""
    for key in ("submissions/CIK3.json", "companyfacts/CIK3.json", "efts/nuclear_0.json"):
        _write_cache(tmp_path, key, '{"stale": true}', age_s=13 * 3600)
    client, fetched = _live_client(tmp_path)
    for key in ("submissions/CIK3.json", "companyfacts/CIK3.json", "efts/nuclear_0.json"):
        assert client.get_json("https://sec.gov/x", key) == {"fresh": True}
    assert len(fetched) == 3  # every mutable prefix re-pulled


def test_stale_mutable_key_is_served_from_cache_when_live_DISABLED(tmp_path):
    """The offline guarantee: with allow_live=False a stale hit is STILL served (better stale than a
    CacheMiss) — so the whole test suite (fixtures older than any TTL) and --no-live keep working. The TTL
    only forces a refetch when we're allowed to fetch."""
    _write_cache(tmp_path, "submissions/CIK4.json", '{"cached": true}', age_s=13 * 3600)
    client = EdgarClient(cache_dir=tmp_path, allow_live=False)  # no network permitted
    assert client.get_json("https://sec.gov/x", "submissions/CIK4.json") == {"cached": True}


def test_a_cache_MISS_still_fetches_regardless_of_ttl(tmp_path):
    """A missing key always fetches when live — the TTL governs stale HITS, not misses."""
    client, fetched = _live_client(tmp_path)
    assert client.get_json("https://sec.gov/x", "submissions/CIK5.json") == {"fresh": True}
    assert len(fetched) == 1


def test_live_fetches_counter_counts_network_pulls_not_cache_hits(tmp_path):
    """The freeze detector (R3): `live_fetches` counts real network pulls — a miss and a stale-mutable
    refresh increment it; a fresh cache hit and an immutable-forever hit do NOT. 0 across a live run is the
    freeze fingerprint (a stale index served forever = no pulls)."""
    _write_cache(tmp_path, "submissions/stale.json", "{}", age_s=13 * 3600)  # will refresh → +1
    _write_cache(tmp_path, "submissions/fresh.json", "{}", age_s=1 * 3600)  # within TTL → no pull
    _write_cache(tmp_path, "forms/acc/doc.htm", "x", age_s=999 * 3600)  # immutable → no pull
    client, _ = _live_client(tmp_path)
    assert client.live_fetches == 0

    client.get_json("https://sec.gov/x", "submissions/miss.json")  # miss → +1
    client.get_json("https://sec.gov/x", "submissions/stale.json")  # stale refresh → +1
    client.get_json("https://sec.gov/x", "submissions/fresh.json")  # fresh hit → +0
    client.get_text("https://sec.gov/x", "forms/acc/doc.htm")  # immutable hit → +0
    assert client.live_fetches == 2  # only the two that hit the network
