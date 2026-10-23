"""SEC's 2xx-wrapped ERROR REPLIES never reach the cache, never count as data, and are never served.

MEASURED 2026-10-23 (prod, a Peptide Compounding draft): EFTS answered several pages with a success status
and the body ``{"errorType":"ConnectionError","errorMessage":"getaddrinfo EBUSY ...","trace":[...]}``.
``EdgarClient.get_text`` wrote it to the disk cache and counted it as a completed fetch, so the error was
re-served for the 12h TTL and read downstream as an empty page. These tests pin the client half of the fix
(the EFTS half is in ``test_fulltext.py``): a body that fails the generic error-reply guard, or the caller's
``validate``, is rejected BEFORE the cache write and the completion count; a cached copy that fails is a
MISS (re-fetched when live, raised when not). No network: ``_fetch`` is stubbed per test.
"""

from __future__ import annotations

import gzip
import json
import os
import time
from pathlib import Path

import pytest

from ingest import CacheMiss
from ingest.edgar.client import EdgarClient, InvalidBody, _error_reply

# The exact shape SEC sent (trimmed): a top-level JSON object carrying ``errorType``, no ``hits``.
_ERROR_BODY = json.dumps(
    {
        "errorType": "ConnectionError",
        "errorMessage": "getaddrinfo EBUSY vpc-efts-prod-opensearchservice.example",
        "trace": ["ConnectionError: getaddrinfo EBUSY ..."],
    }
)

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _live(cache_dir: Path, bodies: list[str], *, ttl_s: float = 12 * 3600):
    """A live client whose network answers ``bodies`` in order (the last one repeats). Returns it plus the
    list of fetched URLs."""
    fetched: list[str] = []
    c = EdgarClient(cache_dir=cache_dir, allow_live=True, user_agent="test ua", cache_ttl_s=ttl_s)

    def _fetch(url: str) -> str:
        fetched.append(url)
        return bodies[min(len(fetched), len(bodies)) - 1]

    c._fetch = _fetch  # type: ignore[method-assign]
    return c, fetched


def _cache(cache_dir: Path, key: str, body: str, *, age_s: float = 60) -> Path:
    p = cache_dir / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    old = time.time() - age_s
    os.utime(p, (old, old))
    return p


# --- a FRESH error reply: rejected before the cache write and the completion count -------------------


def test_a_2xx_error_body_is_never_written_to_the_cache(tmp_path):
    client, fetched = _live(tmp_path, [_ERROR_BODY])
    with pytest.raises(InvalidBody, match="errorType='ConnectionError'"):
        client.get_json("https://efts.sec.gov/x", "efts/BPC-157_0.json")
    assert len(fetched) == 1
    assert not (tmp_path / "efts" / "BPC-157_0.json").exists()  # nothing to re-serve for 12h
    assert list(tmp_path.rglob("*.json")) == []


def test_a_rejected_body_is_an_ATTEMPT_but_never_a_COMPLETION(tmp_path):
    """O5: ``live_completed`` (what the freeze detector reads) means EDGAR DATA reached the pass. An error
    reply is not data: it stays an attempt (``live_fetches``) and is not a completion, globally or on the
    calling thread. A good body afterwards counts as usual."""
    client, _ = _live(tmp_path, [_ERROR_BODY, '{"filings": {}}'])
    with pytest.raises(InvalidBody):
        client.get_json("https://data.sec.gov/x", "submissions/CIK0000000001.json")
    assert (client.live_fetches, client.live_completed, client.thread_completed()) == (1, 0, 0)

    assert client.get_json("https://data.sec.gov/x", "submissions/CIK0000000001.json") == {
        "filings": {}
    }
    assert (client.live_fetches, client.live_completed, client.thread_completed()) == (2, 1, 1)


def test_the_generic_guard_covers_EVERY_key_not_only_efts(tmp_path):
    """The errorType guard is key-agnostic: a submissions index (or a filing document) answered with the
    error reply is rejected the same way, with no ``validate`` from the caller."""
    client, _ = _live(tmp_path, [_ERROR_BODY])
    for key in ("submissions/CIK0000000002.json", "forms/acc-1/doc.json", "companyfacts/CIK2.json"):
        with pytest.raises(InvalidBody):
            client.get_text("https://sec.gov/x", key)
        assert not (tmp_path / key).exists()
    assert client.live_completed == 0


def test_a_callers_validate_rejects_before_the_cache_write(tmp_path):
    """``validate`` on ``get_text`` gets the raw text; raising ``InvalidBody`` keeps it out of the cache."""

    def _must_mention_hits(text: str) -> None:
        if "hits" not in text:
            raise InvalidBody("not a hits page")

    client, _ = _live(tmp_path, ['{"something": "else"}'])
    with pytest.raises(InvalidBody, match="not a hits page"):
        client.get_text("https://sec.gov/x", "efts/x_0.json", validate=_must_mention_hits)
    assert not (tmp_path / "efts" / "x_0.json").exists()
    assert client.live_completed == 0


def test_get_json_validate_receives_the_PARSED_document_and_rejects_non_json(tmp_path):
    seen: list[object] = []

    def _record(doc: object) -> None:
        seen.append(doc)

    client, _ = _live(tmp_path, ['{"hits": {"hits": []}}'])
    assert client.get_json("https://sec.gov/x", "efts/a_0.json", validate=_record) == {
        "hits": {"hits": []}
    }
    assert seen == [{"hits": {"hits": []}}]
    assert (tmp_path / "efts" / "a_0.json").exists()  # a valid body is cached as before

    garbled, _ = _live(tmp_path, ["<html>gateway timeout</html>"])
    with pytest.raises(InvalidBody, match="not a JSON document"):
        garbled.get_json("https://sec.gov/x", "efts/b_0.json", validate=_record)
    assert not (tmp_path / "efts" / "b_0.json").exists()


def test_a_rejected_companyfacts_body_leaves_the_404_marker_alone(tmp_path):
    """Only a REAL document proves "the company has companyfacts now" and clears the X8 marker; an error
    reply proves nothing, so the marker stays and no document is written."""
    marker = tmp_path / "companyfacts" / "CIK0000000003.json.404"
    marker.parent.mkdir(parents=True)
    marker.write_text("https://data.sec.gov/x", encoding="utf-8")
    client, _ = _live(tmp_path, [_ERROR_BODY])  # interactive: ignores the marker, asks the network
    with pytest.raises(InvalidBody):
        client.get_json("https://data.sec.gov/x", "companyfacts/CIK0000000003.json")
    assert marker.exists()
    assert not (tmp_path / "companyfacts" / "CIK0000000003.json").exists()


# --- a CACHED error reply (poisoned before this fix): a MISS, never served ---------------------------


def test_a_cached_error_body_is_REFETCHED_when_live_even_within_the_ttl(tmp_path):
    """One minute old, so the TTL alone would serve it: the poisoned copy is treated as a miss, re-fetched,
    and overwritten by the good body."""
    path = _cache(tmp_path, "efts/GHK-Cu_0.json", _ERROR_BODY, age_s=60)
    good = '{"hits": {"total": {"value": 8}, "hits": []}}'
    client, fetched = _live(tmp_path, [good])
    assert client.get_text("https://efts.sec.gov/x", "efts/GHK-Cu_0.json") == good
    assert len(fetched) == 1
    assert path.read_text(encoding="utf-8") == good
    assert (client.live_fetches, client.live_completed) == (1, 1)


def test_a_cached_error_body_on_an_IMMUTABLE_key_is_refetched_too(tmp_path):
    """``forms/*`` caches forever — which made a poisoned filing document permanent. The guard runs before
    the freshness rule can serve it."""
    _cache(tmp_path, "forms/acc-9/doc.json", _ERROR_BODY, age_s=365 * 24 * 3600)
    client, fetched = _live(tmp_path, ['{"real": "document"}'])
    assert client.get_json("https://www.sec.gov/x", "forms/acc-9/doc.json") == {"real": "document"}
    assert len(fetched) == 1


def test_a_cached_error_body_RAISES_offline_never_served_never_a_silent_cache_miss(tmp_path):
    path = _cache(tmp_path, "submissions/CIK0000000004.json", _ERROR_BODY)
    client = EdgarClient(cache_dir=tmp_path, allow_live=False)
    with pytest.raises(InvalidBody) as caught:
        client.get_json("https://data.sec.gov/x", "submissions/CIK0000000004.json")
    assert not isinstance(caught.value, CacheMiss)
    assert path.exists()  # left on disk (offline never deletes); every read re-checks it
    assert client.live_fetches == 0


def test_a_cached_body_failing_the_callers_validate_is_a_miss_too(tmp_path):
    _cache(tmp_path, "efts/peptide_therapy_100.json", '{"no": "hits"}')

    def _needs_hits(doc: object) -> None:
        if not (isinstance(doc, dict) and "hits" in doc):
            raise InvalidBody("no hits")

    client, fetched = _live(tmp_path, ['{"hits": {"hits": []}}'])
    got = client.get_json(
        "https://efts.sec.gov/x", "efts/peptide_therapy_100.json", validate=_needs_hits
    )
    assert got == {"hits": {"hits": []}} and len(fetched) == 1

    offline = EdgarClient(cache_dir=tmp_path, allow_live=False)
    _cache(tmp_path, "efts/peptide_therapy_200.json", '{"no": "hits"}')
    with pytest.raises(InvalidBody, match="no hits"):
        offline.get_json("https://x", "efts/peptide_therapy_200.json", validate=_needs_hits)


def test_an_uncached_key_offline_is_still_a_plain_CacheMiss(tmp_path):
    """Unchanged: no file at all, no network -> ``CacheMiss`` (the suite's etiquette guard)."""
    client = EdgarClient(cache_dir=tmp_path, allow_live=False)
    with pytest.raises(CacheMiss):
        client.get_json("https://x", "efts/never_0.json", validate=lambda doc: None)


# --- the guard's reach: only a TOP-LEVEL errorType object ---------------------------------------------


@pytest.mark.parametrize(
    "body",
    [
        '{"filings": {"recent": {"form": ["4"]}}}',  # an ordinary JSON document
        '{"data": {"errorType": "nested, not top-level"}}',  # errorType below the top level
        '[{"errorType": "inside a list"}]',  # not an object
        '{ "errorType" broken json',  # starts like one but is not JSON: the caller's parse decides
        "<ownershipDocument>errorType</ownershipDocument>",  # a filing document that mentions the word
        "",
    ],
)
def test_bodies_that_are_not_an_error_reply_pass_the_guard(body):
    assert _error_reply(body) is None


def test_the_error_reply_shape_is_recognized_with_leading_whitespace():
    assert _error_reply("\n  " + _ERROR_BODY) == "ConnectionError"


def test_NO_sec_fixture_in_the_repo_would_be_rejected_by_the_generic_guard():
    """The claim the guard rests on — no real SEC document has a top-level ``errorType`` — checked against
    every fixture in ``tests/fixtures`` (EDGAR submissions/companyfacts/Form 4 XML/8-K text/N-PORT, gzipped
    ones decompressed). Not vacuous: it asserts JSON documents were actually among those scanned."""
    scanned = json_docs = 0
    for path in sorted(p for p in _FIXTURES.rglob("*") if p.is_file()):
        raw = path.read_bytes()
        if path.suffix == ".gz":
            raw = gzip.decompress(raw)
        text = raw.decode("utf-8", errors="replace")
        scanned += 1
        json_docs += text.lstrip().startswith("{")
        assert _error_reply(text) is None, f"{path.relative_to(_FIXTURES)} would be rejected"
    assert scanned > 100 and json_docs > 20
