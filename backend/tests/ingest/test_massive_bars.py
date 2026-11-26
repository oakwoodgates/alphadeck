"""Massive.com's grouped raw daily bars (W6-3a, the nightly tape cross-check's one read) over REAL rows.

``tests/fixtures/massive/grouped/<session>.json`` are cut from the cached grouped days of the 2026-11-25 read-only
probe of the free key (read at 21:05-21:17 ET with ``adjusted=false&include_otc=true``):
each row as Massive.com served it, the envelope's counts set to the rows kept. 11-25 is the session served the
same evening (``status`` ``DELAYED``). No network: a fetch is a fake, or ``httpx.get`` is patched.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

import ingest.massive_bars as mb
from domain.settings import get_settings
from ingest import CacheMiss
from ingest.funds import polygon as funds_polygon

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "massive" / "grouped"
NOV25 = date(2026, 11, 25)
# The 403 Massive.com answered for a session not yet served (measured 2026-11-25 21:24 ET for 11-26: status and
# message as the probe recorded them; the full body was not saved).
NOT_SERVED = {
    "status": "NOT_AUTHORIZED",
    "message": "Attempted to request today's data before end of day",
}


def _text(day: str) -> str:
    return (FIX / f"{day}.json").read_text(encoding="utf-8")


class _Limiter:
    """The shared limiter's seam: counts the slots taken (one before EVERY attempt, retries included)."""

    def __init__(self) -> None:
        self.acquired = 0

    def acquire(self) -> None:
        self.acquired += 1


def _source(tmp_path, *, fetch=None, today=NOV25, **kw) -> mb.MassiveBarSource:
    return mb.MassiveBarSource(
        api_key=kw.pop("api_key", "k-test"),
        cache_dir=tmp_path,
        fetch=fetch,
        today=lambda: today,
        **kw,
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


def test_a_grouped_day_parses_exact_decimals_with_the_otc_flag_and_class_shares():
    day = mb.parse_grouped(_text("2026-11-25"), session=NOV25)
    assert (day.session, day.status, len(day.bars), day.skipped) == (NOV25, "DELAYED", 7, 0)
    sndk = day.bars["SNDK"]
    assert (sndk.close, sndk.volume, sndk.otc) == (
        Decimal("73.61"),
        Decimal("124408329.535187"),
        False,
    )
    assert (sndk.open, sndk.high, sndk.low) == (
        Decimal("71.315"),
        Decimal("73.77"),
        Decimal("67.02"),
    )
    hcmc = day.bars["HCMC"]  # sub-penny OTC: exact, never a float
    assert hcmc.otc is True and hcmc.close == Decimal("0.000001")
    # Massive.com writes a class share with a dot; the master (and Yahoo) with a dash
    assert day.bars["BRK.B"].close == Decimal("579.32")
    assert day.get(mb.massive_symbol("BRK-B")) is day.bars["BRK.B"]
    assert day.get(None) is None and day.get("GNSX") is None


def test_a_row_missing_its_ticker_close_or_volume_is_skipped_and_counted():
    text = (
        '{"results": [{"T": "AAA", "c": 1.5, "v": 100, "o": 1.4, "h": 1.6, "l": 1.3},'
        ' {"T": "NOC", "v": 10}, {"c": 2, "v": 10}, {"T": "NOV", "c": 2.5},'
        ' {"T": "BAD", "c": "x", "v": 1}, {"T": "NEG", "c": -1, "v": 1}],'
        ' "status": "OK", "resultsCount": 6}'
    )
    day = mb.parse_grouped(text, session=NOV25)
    assert list(day.bars) == ["AAA"] and day.skipped == 5
    assert day.bars["AAA"].otc is False  # a row without the flag is a listed (non-OTC) row


def test_massive_symbol_writes_class_shares_with_a_dot_and_leaves_the_rest():
    assert mb.massive_symbol("BRK-B") == "BRK.B"  # measured: Massive.com BRK.B = Yahoo BRK-B
    assert mb.massive_symbol(" brk-b ") == "BRK.B"
    assert mb.massive_symbol("BRK.B") == "BRK.B"
    assert mb.massive_symbol("SNDK") == "SNDK"
    assert mb.massive_symbol(None) is None and mb.massive_symbol("  ") is None


def test_the_url_is_key_stripped_raw_and_carries_otc():
    assert mb.grouped_url(NOV25) == (
        f"{get_settings().polygon_base}/v2/aggs/grouped/locale/us/market/stocks/2026-11-25"
        "?adjusted=false&include_otc=true"
    )


def test_a_live_day_is_cached_compact_and_a_cache_hit_makes_no_request(tmp_path):
    asked: list[str] = []

    def fetch(url):
        asked.append(url)
        return _text("2026-11-25")

    src = _source(tmp_path, fetch=fetch)
    day = src.day(NOV25, allow_live=True)
    assert day.live is True and len(asked) == 1
    assert asked[0] == mb.grouped_url(NOV25) and "apiKey" not in asked[0]
    (cached,) = list(tmp_path.iterdir())
    assert (
        cached.name == "2026-11-25.json"
    )  # the session names the file: never the key, never the URL
    assert cached.stat().st_size < len(_text("2026-11-25"))  # the compact form, not the vendor body
    again = src.day(NOV25, allow_live=True)
    assert again.live is False and len(asked) == 1  # a cache hit makes no request
    assert again.bars == day.bars and (again.status, again.skipped) == ("DELAYED", 0)
    # cache-only on a day never pulled is a CacheMiss, never an empty day
    with pytest.raises(CacheMiss):
        src.day(date(2026, 11, 24), allow_live=False)
    assert len(asked) == 1


def test_files_older_than_30_days_are_pruned_on_write(tmp_path):
    old = tmp_path / "2026-10-25.json"
    edge = tmp_path / "2026-10-26.json"  # exactly 30 days back: kept
    other = tmp_path / "notes.txt"  # not a cached day: never touched
    for p in (old, edge, other):
        p.write_text("{}", encoding="utf-8")
    _source(tmp_path, fetch=lambda url: _text("2026-11-25")).day(NOV25, allow_live=True)
    assert not old.exists() and edge.exists() and other.exists()
    assert (tmp_path / "2026-11-25.json").exists()


def test_a_403_before_end_of_day_is_NotServedYet_a_status_never_an_error(tmp_path, monkeypatch):
    asked = _answers(monkeypatch, (403, NOT_SERVED))
    src = _source(tmp_path, api_key="sekrit-key-123", limiter=_Limiter(), sleep=lambda s: None)
    with pytest.raises(mb.NotServedYet) as err:
        src.day(date(2026, 11, 26), allow_live=True)
    assert not isinstance(err.value, mb.MassiveBarsError)  # a status, not a failure
    assert "sekrit-key-123" not in str(err.value) and "2026-11-26" in str(err.value)
    assert len(asked) == 1 and list(tmp_path.iterdir()) == []  # never retried, never cached
    # a 403 for anything else (the key's plan) IS an error, scrubbed
    _answers(monkeypatch, (403, {"status": "NOT_AUTHORIZED", "message": "plan"}))
    with pytest.raises(mb.MassiveBarsError) as err:
        src.day(date(2026, 11, 26), allow_live=True)
    assert "HTTP 403" in str(err.value) and "sekrit-key-123" not in str(err.value)


def test_a_429_is_retried_on_the_shared_limiter_after_the_minute(tmp_path, monkeypatch):
    asked = _answers(monkeypatch, (429, {"status": "ERROR"}), (200, _text("2026-11-25")))
    limiter, waits = _Limiter(), []
    src = _source(tmp_path, api_key="sekrit-key-123", limiter=limiter, sleep=waits.append)
    day = src.day(NOV25, allow_live=True)
    assert len(day.bars) == 7 and day.live
    assert limiter.acquired == 2  # a slot before EVERY attempt, the retry included
    assert waits == [mb.RETRY_WAIT_S]  # the 429 waits the free tier's minute out
    assert all(u.endswith("&apiKey=sekrit-key-123") for u in asked)  # the key only at the socket
    (cached,) = list(tmp_path.iterdir())
    assert "sekrit-key-123" not in cached.read_text(encoding="utf-8")


def test_the_live_read_paces_on_the_ONE_Massive_budget():
    assert (
        mb.LIMITER is funds_polygon.LIMITER
    )  # shared with the split reference and the fund shares
    assert mb.RETRY_WAIT_S == funds_polygon.RETRY_WAIT_S


def test_the_key_never_leaks_into_an_error(tmp_path, monkeypatch):
    def boom(url, **_kw):
        raise RuntimeError(f"connection failed for {url}")

    monkeypatch.setattr(mb, "polite_get", boom)
    src = _source(tmp_path, api_key="sekrit-key-123")
    with pytest.raises(mb.MassiveBarsError) as err:
        src.day(NOV25, allow_live=True)
    assert "sekrit-key-123" not in str(err.value) and "***" in str(err.value)
    assert err.value.__cause__ is None and err.value.__suppress_context__
    # an unreadable body is an error too, never an empty day
    bad = _source(tmp_path, fetch=lambda url: "<html>gateway</html>")
    with pytest.raises(mb.MassiveBarsError, match="unreadable"):
        bad.day(NOV25, allow_live=True)
    assert list(tmp_path.iterdir()) == []


def test_no_key_is_the_off_switch(monkeypatch):
    class _S:
        polygon_api_key = None

    monkeypatch.setattr(mb, "get_settings", lambda: _S())
    assert mb.default_bar_source() is None
