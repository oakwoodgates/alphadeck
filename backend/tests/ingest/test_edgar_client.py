from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ingest import CacheMiss
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient

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


# --- G1: the RECURRING pass's TTL (the daytime-warmth gap) -------------------------------------------
#
# The 12h TTL is an INTERACTIVE default. On the nightly path it was a blind spot: any daytime read of a
# company's submissions index (Admin "Run daily now", a boot catch-up, an on-promote ingest, a Workbench
# pull) left the file fresh enough that the 22:30 pass served it off disk and never saw the afternoon's
# filings. The recurring callers therefore construct their client with RECURRING_CACHE_TTL_S — five
# MINUTES, not zero; the three-reads test below is the measurement that decided that.


def test_recurring_ttl_refetches_a_daytime_warm_key_the_default_ttl_serves(tmp_path):
    """THE GAP AND THE FIX, on ONE warm file — the SAME index a daytime run fetched 20 minutes ago:

    - the 12h DEFAULT serves it from cache with zero pulls (this is the night going blind);
    - the RECURRING TTL refetches it and overwrites the cache (the night seeing the afternoon).

    Both clients read the same key from the same dir, so the dial is the only difference. The file is aged
    PAST the five-minute recurring TTL on purpose: at two minutes this would pass under a zero TTL too and
    prove nothing about the daytime case it exists for."""
    key = "submissions/CIK0000000009.json"
    age = (
        RECURRING_CACHE_TTL_S + 20 * 60
    )  # fetched ~25 min ago: a real daytime read, long past the TTL
    p = _write_cache(tmp_path, key, '{"daytime": true}', age_s=age)

    warm, warm_fetched = _live_client(tmp_path)  # the 12h interactive default
    assert warm.get_json("https://sec.gov/" + key, key) == {"daytime": True}
    assert warm_fetched == [] and warm.live_fetches == 0  # blind: the stale daytime index served

    recurring, fetched = _live_client(tmp_path, ttl_s=RECURRING_CACHE_TTL_S)  # what the cron builds
    assert recurring.get_json("https://sec.gov/" + key, key) == {"fresh": True}
    assert len(fetched) == 1 and recurring.live_fetches == 1
    assert "daytime" not in p.read_text(encoding="utf-8")  # the cache was overwritten → fresh again


def test_the_recurring_ttl_is_NOT_zero_because_zero_triples_the_per_company_cost(tmp_path):
    """WHY THE CONSTANT IS FIVE MINUTES AND NOT ZERO — the measurement, as a test.

    ``ingest_thesis`` runs three filing legs per company back to back (``_form4_leg`` / ``_form8k_leg`` /
    ``_schedule13_leg``) and each reads the SAME ``submissions/CIK<10>.json`` key; the 8-K and 13D/G legs'
    docstrings promise those later reads cost "zero extra enumeration fetches". ``_is_stale`` is
    ``now - mtime > ttl``, so at ttl=0 a file written MILLISECONDS ago is already stale and each leg
    refetches: 3 live pulls per company instead of 1 — ~1,500 SEC requests a night on a 500-name universe,
    for no freshness gain, against an API whose politeness is a correctness requirement here.

    Five minutes keeps the same-pass re-reads free while still refetching anything a daytime run left
    behind (the test above). This test is the regression guard on that constant: if someone "tightens" it
    to zero, the promise those two docstrings make breaks silently and only the SEC notices."""
    key = "submissions/CIK0000000007.json"

    recurring, fetched = _live_client(tmp_path, ttl_s=RECURRING_CACHE_TTL_S)
    for _ in range(3):  # the three legs, back to back over one company's index
        assert recurring.get_json("https://sec.gov/" + key, key) == {"fresh": True}
    assert recurring.live_fetches == 1 and len(fetched) == 1  # the 2nd + 3rd reads are FREE

    zero_dir = tmp_path / "at_zero"
    at_zero, zero_fetched = _live_client(zero_dir, ttl_s=0)
    for _ in range(3):
        assert at_zero.get_json("https://sec.gov/" + key, key) == {"fresh": True}
    assert (
        at_zero.live_fetches == 3 and len(zero_fetched) == 3
    )  # what zero would have cost, per company


def test_the_recurring_ttl_still_caches_immutable_filing_documents_forever(tmp_path):
    """The COST BOUND: the recurring dial must not defeat the immutable class. forms/<accession>/<doc> is a
    filing document — an accession never changes — so the nightly pass still pays ZERO for the thousands of
    per-filing documents it reads; only the mutable per-company INDEX is re-pulled."""
    _write_cache(tmp_path, "forms/acc-9/doc.htm", "the filing", age_s=999 * 3600)
    client, fetched = _live_client(tmp_path, ttl_s=RECURRING_CACHE_TTL_S)

    assert client.get_text("https://sec.gov/acc-9/doc.htm", "forms/acc-9/doc.htm") == "the filing"
    assert fetched == [] and client.live_fetches == 0


def test_the_recurring_ttl_is_inert_offline_so_the_suite_and_no_live_are_unaffected(tmp_path):
    """allow_live=False + the recurring TTL still SERVES the stale hit (better stale than a CacheMiss) —
    the TTL only forces a refetch when a live pull is permitted. This is why the radar/sweep tests, which
    inject a fixture-cache client with allow_live=False, are untouched by the dial."""
    _write_cache(tmp_path, "submissions/CIK0000000008.json", '{"cached": true}', age_s=999 * 3600)
    client = EdgarClient(cache_dir=tmp_path, allow_live=False, cache_ttl_s=RECURRING_CACHE_TTL_S)
    assert client.get_json("https://sec.gov/x", "submissions/CIK0000000008.json") == {
        "cached": True
    }
    assert client.live_fetches == 0
