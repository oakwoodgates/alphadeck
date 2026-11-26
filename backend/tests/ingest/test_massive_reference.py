"""Massive.com's ticker OVERVIEW (W6-4, the reference evidence on a stale price row) over REAL bodies.

``tests/fixtures/massive/reference/<T>.json`` are the bodies the 2026-11-25 read-only probe of the free key cached
for ``/v3/reference/tickers/<T>`` (SNDK ``CS``, SCNI ``ADRC``, ACLEW and IXQWF OTC ``WARRANT``), copied whole.
``INDV.json`` is BUILT, not read (its ``_note`` says so): the probe never read the overview of a delisted ticker, so
its ``results`` are INDV's row of the delisted list (``/v3/reference/tickers?active=false``) as recorded that night.
``not_found_TMRC.json`` is the 404 body the probe recorded for TMRC. No network: a fetch is a fake, or
``httpx.get`` is patched.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import httpx
import pytest

import ingest.massive_reference as mr
from domain.settings import get_settings
from ingest import CacheMiss, massive_bars
from ingest.funds import polygon as funds_polygon

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "massive" / "reference"
NOV25 = date(2026, 11, 25)
KEY = "sekrit-key-123"


def _text(name: str) -> str:
    return (FIX / f"{name}.json").read_text(encoding="utf-8")


class _Limiter:
    """The shared limiter's seam: counts the slots taken (one before EVERY attempt, retries included)."""

    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self) -> None:
        self.acquired += 1


def _source(tmp_path, *, fetch=None, **kw) -> mr.MassiveReferenceSource:
    return mr.MassiveReferenceSource(
        api_key=kw.pop("api_key", "k-test"), cache_dir=tmp_path, fetch=fetch, **kw
    )


def _answers(monkeypatch, *responses):
    """Patch ``httpx.get`` (what ``polite_get`` calls) to answer ``responses`` in order; returns the URLs asked."""
    asked: list[str] = []
    queue = list(responses)

    def fake_get(url, **_kw):
        asked.append(url)
        status, body = queue.pop(0)
        req = httpx.Request("GET", url)
        if isinstance(body, str):
            return httpx.Response(status, text=body, request=req)
        return httpx.Response(status, json=body, request=req)

    monkeypatch.setattr(httpx, "get", fake_get)
    return asked


# --- the parse ------------------------------------------------------------------------------------------------


def test_the_measured_overviews_parse_to_the_four_fields_kept():
    sndk = mr.parse_overview(_text("SNDK"))
    assert sndk == mr.TickerOverview(ticker="SNDK", active=True, type="CS", delisted_utc=None)
    assert mr.parse_overview(_text("SCNI")).type == "ADRC"
    # ACLEW and IXQWF: stale price rows on prod that Massive.com lists as OTC WARRANTS, active
    for name in ("ACLEW", "IXQWF"):
        o = mr.parse_overview(_text(name))
        assert (o.ticker, o.active, o.type, o.delisted_utc) == (name, True, "WARRANT", None)


def test_a_delisted_row_parses_its_delisted_date_BUILT_fixture():
    """INDV (BUILT from the recorded delisted-list row): inactive, ``delisted_utc`` 2026-11-03T00:00:00Z -> the
    date. The probe never read a delisted ticker's overview, so this shape is the list's, unverified there.
    """
    assert "BUILT" in json.loads(_text("INDV"))["_note"]
    o = mr.parse_overview(_text("INDV"))
    assert (o.ticker, o.active, o.type, o.delisted_utc) == ("INDV", False, "CS", date(2026, 11, 3))


def test_an_unreadable_or_odd_body_is_a_ValueError_and_odd_fields_read_None():
    for bad in ("<html>gateway</html>", "[]", '{"status": "OK"}', '{"results": []}'):
        with pytest.raises(ValueError):
            mr.parse_overview(bad)
    o = mr.parse_overview(
        json.dumps(
            {
                "results": {
                    "ticker": "TKAB",
                    "active": "yes",  # not a bool: unknown, never guessed
                    "type": "<script>",  # not a type code: dropped
                    "delisted_utc": "soon",
                }
            }
        )
    )
    assert (o.ticker, o.active, o.type, o.delisted_utc) == ("TKAB", None, None, None)


# --- the read ---------------------------------------------------------------------------------------------------


def test_the_url_is_key_stripped_and_the_symbol_is_checked():
    assert mr.overview_url("SNDK") == f"{get_settings().polygon_base}/v3/reference/tickers/SNDK"
    assert mr.overview_url("BRK.B").endswith("/v3/reference/tickers/BRK.B")
    for bad in ("", "../etc", "SN DK", "sndk/x", "A" * 30):
        with pytest.raises(ValueError):
            mr.overview_url(bad)


def test_a_live_read_is_cached_per_symbol_and_market_day_and_a_hit_makes_no_request(tmp_path):
    asked: list[str] = []

    def fetch(url):
        asked.append(url)
        return _text("ACLEW")

    src = _source(tmp_path, fetch=fetch)
    first = src.overview("ACLEW", day=NOV25, allow_live=True)
    assert first.type == "WARRANT" and asked == [mr.overview_url("ACLEW")]
    (cached,) = list(tmp_path.iterdir())
    assert (
        cached.name == "ACLEW.2026-11-25.json"
    )  # (symbol, market day): never the key, never the URL
    assert "AEDIS" not in cached.read_text(encoding="utf-8")  # the fields kept, not the vendor body
    assert src.overview("ACLEW", day=NOV25, allow_live=True) == first and len(asked) == 1
    assert src.overview("ACLEW", day=NOV25, allow_live=False) == first  # a cache-only read hits too
    # a new market day is a structural miss
    src.overview("ACLEW", day=date(2026, 11, 26), allow_live=True)
    assert len(asked) == 2
    with pytest.raises(CacheMiss):
        src.overview("SNDK", day=NOV25, allow_live=False)
    assert len(asked) == 2


def test_a_404_is_None_and_is_cached_for_the_day(tmp_path, monkeypatch):
    """TMRC, NOTVQ, MTMT, EULL answered 404 "Ticker not found." (measured): Massive.com does not list the symbol —
    a clean miss, never an error, and not asked again that day."""
    asked = _answers(monkeypatch, (404, _text("not_found_TMRC")))
    src = _source(tmp_path, api_key=KEY, limiter=_Limiter(), sleep=lambda s: None)
    assert src.overview("TMRC", day=NOV25, allow_live=True) is None
    assert src.overview("TMRC", day=NOV25, allow_live=True) is None
    assert len(asked) == 1  # the 404 is the day's answer
    (cached,) = list(tmp_path.iterdir())
    assert cached.name == "TMRC.2026-11-25.json" and KEY not in cached.read_text(encoding="utf-8")


def test_a_429_is_retried_on_the_ONE_Massive_budget_after_the_minute(tmp_path, monkeypatch):
    asked = _answers(monkeypatch, (429, {"status": "ERROR"}), (200, _text("SNDK")))
    limiter, waits = _Limiter(), []
    src = _source(tmp_path, api_key=KEY, limiter=limiter, sleep=waits.append)
    assert src.overview("SNDK", day=NOV25, allow_live=True).type == "CS"
    assert limiter.acquired == 2  # a slot before EVERY attempt, the retry included
    assert waits == [mr.RETRY_WAIT_S]
    assert all(u.endswith(f"?apiKey={KEY}") for u in asked)  # the key only at the socket
    # shared with the grouped bars, the split reference and the fund shares
    assert mr.LIMITER is massive_bars.LIMITER is funds_polygon.LIMITER
    assert mr.RETRY_WAIT_S == funds_polygon.RETRY_WAIT_S


@pytest.mark.parametrize("status", [401, 403, 500])
def test_a_failed_read_is_an_error_that_never_carries_the_key(tmp_path, monkeypatch, status):
    _answers(monkeypatch, *[(status, {"status": "ERROR", "message": f"x {KEY}"})] * 4)
    src = _source(tmp_path, api_key=KEY, limiter=_Limiter(), sleep=lambda s: None)
    with pytest.raises(mr.MassiveReferenceError) as err:
        src.overview("SNDK", day=NOV25, allow_live=True)
    assert f"HTTP {status}" in str(err.value) and KEY not in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__
    assert list(tmp_path.iterdir()) == []  # a failure is never cached


def test_a_transport_failure_and_an_unreadable_body_never_leak_the_key(tmp_path, monkeypatch):
    def boom(url, **_kw):
        raise RuntimeError(f"connection failed for {url}")

    monkeypatch.setattr(mr, "polite_get", boom)
    src = _source(tmp_path, api_key=KEY)
    with pytest.raises(mr.MassiveReferenceError) as err:
        src.overview("SNDK", day=NOV25, allow_live=True)
    assert KEY not in str(err.value) and "***" in str(err.value)
    assert err.value.__cause__ is None
    bad = _source(tmp_path, fetch=lambda url: "<html>gateway</html>")
    with pytest.raises(mr.MassiveReferenceError, match="unreadable"):
        bad.overview("SNDK", day=NOV25, allow_live=True)
    assert list(tmp_path.iterdir()) == []


def test_days_older_than_30_are_pruned_on_write(tmp_path):
    old = tmp_path / "SNDK.2026-10-25.json"
    edge = tmp_path / "SNDK.2026-10-26.json"  # exactly 30 days back: kept
    other = tmp_path / "notes.txt"  # not a cached read: never touched
    for p in (old, edge, other):
        p.write_text("{}", encoding="utf-8")
    _source(tmp_path, fetch=lambda url: _text("SNDK")).overview("SNDK", day=NOV25, allow_live=True)
    assert not old.exists() and edge.exists() and other.exists()


def test_no_key_is_the_off_switch(monkeypatch):
    class _S:
        polygon_api_key = None

    monkeypatch.setattr(mr, "get_settings", lambda: _S())
    assert mr.default_reference_source() is None
    with pytest.raises(ValueError):
        mr.MassiveReferenceSource(api_key="")


# --- the evidence a stale row carries ---------------------------------------------------------------------------


def test_the_evidence_from_a_read_a_404_and_a_trade_and_its_words():
    warrant = mr.MassiveRef.read("ACLEW", NOV25, mr.parse_overview(_text("ACLEW")))
    assert (warrant.read_on, warrant.listed, warrant.active, warrant.type) == (
        NOV25,
        True,
        True,
        "WARRANT",
    )
    assert warrant.describe() == "Massive.com: WARRANT, active"
    delisted = mr.MassiveRef.read("INDV", NOV25, mr.parse_overview(_text("INDV")))
    assert delisted.describe() == "Massive.com: CS, inactive, delisted 2026-11-03"
    missing = mr.MassiveRef.read("TMRC", NOV25, None)  # the 404
    assert (missing.listed, missing.active, missing.type) == (False, None, None)
    assert missing.describe() == "Massive.com lists no ticker TMRC"
    traded = mr.MassiveRef.read("SNDK", NOV25, mr.parse_overview(_text("SNDK"))).traded(NOV25)
    assert traded.describe() == "Massive.com: CS, active, traded 2026-11-25"
    unread = mr.MassiveRef("SCNI", traded_on=NOV25)  # a trade seen, the overview not read (yet)
    assert unread.read_on is None and unread.describe() == "Massive.com: traded 2026-11-25"
    assert mr.MassiveRef("SCNI").describe() == ""  # nothing known, nothing said


def test_the_evidence_round_trips_through_the_artifact_and_reads_fail_soft():
    for ref in (
        mr.MassiveRef.read("INDV", NOV25, mr.parse_overview(_text("INDV"))).traded(None),
        mr.MassiveRef.read("TMRC", NOV25, None),
        mr.MassiveRef("SNDK", traded_on=NOV25),
    ):
        assert mr.MassiveRef.from_json(json.loads(json.dumps(ref.to_json()))) == ref
    assert mr.MassiveRef.read("INDV", NOV25, mr.parse_overview(_text("INDV"))).to_json() == {
        "symbol": "INDV",
        "read_on": "2026-11-25",
        "listed": True,
        "active": False,
        "type": "CS",
        "delisted_utc": "2026-11-03",
        "traded_on": None,
    }
    for bad in (None, "x", [], {}, {"symbol": 7}, {"symbol": "../x"}):
        assert mr.MassiveRef.from_json(bad) is None
    odd = mr.MassiveRef.from_json(
        {"symbol": "SNDK", "read_on": "nope", "listed": "yes", "type": "<b>", "active": 1}
    )
    assert odd == mr.MassiveRef("SNDK")
