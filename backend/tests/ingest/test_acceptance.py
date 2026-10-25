"""THE ACCEPTANCE CLOCK (``ingest.edgar.acceptance``) — which SEC acceptance a Form 4 / 4/A stores, so the
filing lands on the right night and never early.

The rule: trust the listed ``acceptanceDateTime`` when its ET date is the filing date AND its ET time is within
EDGAR's 06:00-22:00; otherwise the filing's own header ``ACCEPTANCE-DATETIME`` (ET wall clock); header
unreadable -> the end of the filing date in ET. Pinned on the four accessions checked against SEC's header on
2026-10-24 (values only — no network in any test), EST and EDT, and both edges of EDGAR's hours.
"""

from __future__ import annotations

import os
import time as _time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest

from domain.market_time import known_at_for_asof
from ingest.edgar.acceptance import (
    EDGAR_TZ,
    SOURCE_BOUND,
    SOURCE_HEADER,
    SOURCE_LISTED,
    SOURCE_MISSING,
    SOURCE_UNCHECKED,
    AcceptanceTally,
    filing_date_bound,
    header_cache_key,
    header_url,
    listed_is_trusted,
    parse_header_acceptance,
    read_header_acceptance,
    request_free_acceptance,
    resolve_acceptance,
    resolve_filing_acceptance,
    trusted_listed,
)
from ingest.edgar.client import EdgarClient

UTC = timezone.utc


def _hdr(stamp: str, *, escaped: bool = False) -> str:
    """A minimal ``-index-headers.html`` body carrying one ``ACCEPTANCE-DATETIME`` (the shape SEC serves:
    raw inside the comment, HTML-escaped inside the ``<PRE>``)."""
    if escaped:
        return f"<PRE>&lt;SEC-HEADER&gt;x.hdr.sgml\n&lt;ACCEPTANCE-DATETIME&gt;{stamp}\n</PRE>"
    return f"<!--\n<SEC-HEADER>x.hdr.sgml\n<ACCEPTANCE-DATETIME>{stamp}\n-->"


def _never() -> datetime | None:
    raise AssertionError("the header must not be read for a trusted listed time")


def _night(at: datetime) -> date:
    """The night a public-clock view first sees an instant: its ET date — that night's cap covers it and the
    previous night's does not (``known_at_for_asof``, the history views' cap)."""
    d = at.astimezone(EDGAR_TZ).date()
    far = datetime(2100, 1, 1, tzinfo=UTC)
    assert known_at_for_asof(d, now=far) >= at > known_at_for_asof(d - timedelta(days=1), now=far)
    return d


# --- the four real cases (SEC's header, read 2026-10-24) ----------------------------------------------


def test_a_consistent_listed_time_is_kept_0000001750_26_000104():
    """CIK 1750 — filed 2026-10-22, header 20261022142931 (14:29:31 EDT); the list agrees: kept, no read."""
    a = resolve_acceptance("2026-10-22T18:29:31.000Z", "2026-10-22", _never)
    assert a.source == SOURCE_LISTED
    assert a.at == datetime(2026, 10, 22, 18, 29, 31, tzinfo=UTC)
    assert a.at == parse_header_acceptance(_hdr("20261022142931"))
    assert not a.rejected


def test_a_consistent_listed_time_is_kept_0000093556_24_000113():
    """CIK 93556 — filed 2024-06-21, header 20240621133803 (13:38:03 EDT): kept."""
    a = resolve_acceptance("2024-06-21T17:38:03.000Z", "2024-06-21", _never)
    assert a.source == SOURCE_LISTED and a.at == parse_header_acceptance(_hdr("20240621133803"))


def test_a_late_listed_time_on_the_right_night_is_kept_0000002488_26_000145():
    """CIK 2488 — filed 2026-08-18, header 20260818162536 (16:25:36 EDT). Today's list says
    2026-08-19T00:25:36Z = 20:25:36 EDT: four hours late but the same date within EDGAR's hours, so it is
    trusted — and it is on the filing's own night, never before the header."""
    header = parse_header_acceptance(_hdr("20260818162536"))
    a = resolve_acceptance("2026-08-19T00:25:36.000Z", "2026-08-18", _never)
    assert a.source == SOURCE_LISTED
    assert _night(a.at) == _night(header) == date(2026, 8, 18)
    assert a.at >= header


def test_a_listed_time_a_night_late_is_replaced_by_the_header_0000902664_25_005199():
    """CIK 1560327 — filed 2025-12-04, header 20251204190020 (19:00:20 EST). Today's list says
    2025-12-05T05:00:20Z = 00:00:20 EST on 12-05 — a night late: the header wins."""
    a = resolve_acceptance(
        "2025-12-05T05:00:20.000Z",
        "2025-12-04",
        lambda: parse_header_acceptance(_hdr("20251204190020")),
    )
    assert a.source == SOURCE_HEADER and a.rejected
    assert a.at == datetime(2025, 12, 5, 0, 0, 20, tzinfo=UTC)  # 19:00:20 EST
    assert a.listed == datetime(2025, 12, 5, 5, 0, 20, tzinfo=UTC)
    assert _night(a.at) == date(2025, 12, 4)


def test_the_same_case_with_no_header_is_bounded_to_the_end_of_its_filing_date():
    """Header unreadable -> 23:59:59 EST on 2025-12-04: never early (after the header's 19:00:20), on the
    filing date's night — never NULL."""
    a = resolve_acceptance("2025-12-05T05:00:20.000Z", "2025-12-04", lambda: None)
    assert a.source == SOURCE_BOUND and a.rejected
    assert a.at == datetime(2025, 12, 5, 4, 59, 59, tzinfo=UTC)
    assert a.at >= parse_header_acceptance(_hdr("20251204190020"))
    assert _night(a.at) == date(2025, 12, 4)


# --- the trust test: dates, hours, both edges -----------------------------------------------------------


def test_a_listed_time_on_the_next_ET_date_reads_the_header():
    hdr = datetime(2026, 5, 15, 20, 27, 48, tzinfo=UTC)  # 16:27:48 EDT
    a = resolve_acceptance(
        "2026-05-16T04:27:48.000Z", "2026-05-15", lambda: hdr
    )  # 00:27:48 EDT 05-16
    assert a.source == SOURCE_HEADER and a.at == hdr


def test_a_listed_time_outside_EDGARs_hours_on_the_right_date_reads_the_header():
    hdr = datetime(2026, 5, 15, 13, 0, tzinfo=UTC)  # 09:00 EDT
    a = resolve_acceptance("2026-05-15T08:00:00.000Z", "2026-05-15", lambda: hdr)  # 04:00 EDT
    assert a.source == SOURCE_HEADER and a.at == hdr


@pytest.mark.parametrize(
    ("listed", "trusted"),
    [
        ("2026-05-15T10:00:00.000Z", True),  # 06:00:00 EDT — EDGAR opens, inclusive
        ("2026-05-15T09:59:59.000Z", False),  # 05:59:59 EDT
        ("2026-05-16T02:00:00.000Z", True),  # 22:00:00 EDT — EDGAR closes, inclusive
        ("2026-05-16T02:00:01.000Z", False),  # 22:00:01 EDT
        ("2025-12-04T11:00:00.000Z", True),  # 06:00:00 EST (winter: UTC-5)
        ("2025-12-05T03:00:00.000Z", True),  # 22:00:00 EST
        ("2025-12-05T03:00:01.000Z", False),  # 22:00:01 EST
    ],
)
def test_EDGARs_hours_are_inclusive_at_both_ends_in_EST_and_EDT(listed, trusted):
    filed = "2026-05-15" if listed.startswith("2026") else "2025-12-04"
    a = resolve_acceptance(listed, filed, lambda: None)
    assert (a.source == SOURCE_LISTED) is trusted
    assert listed_is_trusted(a.listed, date.fromisoformat(filed)) is trusted


def test_the_header_reads_EST_and_EDT_correctly():
    assert parse_header_acceptance(_hdr("20251204190020")) == datetime(
        2025, 12, 5, 0, 0, 20, tzinfo=UTC
    )  # EST, UTC-5
    assert parse_header_acceptance(_hdr("20260818162536")) == datetime(
        2026, 8, 18, 20, 25, 36, tzinfo=UTC
    )  # EDT, UTC-4
    assert parse_header_acceptance(_hdr("20260331164759", escaped=True)) == datetime(
        2026, 3, 31, 20, 47, 59, tzinfo=UTC
    )  # the <PRE> copy, HTML-escaped
    assert parse_header_acceptance("<HTML>no header here</HTML>") is None
    assert parse_header_acceptance(_hdr("20261399999999")) is None  # not a real time


def test_the_bound_is_the_end_of_the_filing_date_in_ET():
    assert filing_date_bound(date(2025, 12, 4)) == datetime(2025, 12, 5, 4, 59, 59, tzinfo=UTC)
    assert filing_date_bound(date(2026, 8, 18)) == datetime(2026, 8, 19, 3, 59, 59, tzinfo=UTC)


def test_the_degenerate_rows_keep_their_old_behavior_with_no_header_read():
    """No listed time: NULL, as before (#9 — the NULL backfill's job), never a header read. A listed time with
    no filing date to judge it by: kept as given."""
    for filed in ("2026-05-15", "", None):
        a = resolve_acceptance(None, filed, _never)
        assert (a.at, a.source, a.rejected) == (None, SOURCE_MISSING, False)
        assert resolve_acceptance("", filed, _never) == a
    unchecked = resolve_acceptance("2026-05-16T04:27:48.000Z", None, _never)
    assert unchecked.source == SOURCE_UNCHECKED  # no filing date to judge by: kept as given
    assert unchecked.at == datetime(2026, 5, 16, 4, 27, 48, tzinfo=UTC)


def test_the_request_free_reads():
    """``trusted_listed`` (the linker's order map): trusted or nothing. ``request_free_acceptance`` (the
    companyfacts gate): the earlier of the listed time and the bound — the bound when the list pushed the time
    past the filing date, the listed time for an after-17:30 XBRL filing dated the next business day.
    """
    assert trusted_listed("2026-10-22T18:29:31.000Z", "2026-10-22") == datetime(
        2026, 10, 22, 18, 29, 31, tzinfo=UTC
    )
    assert trusted_listed("2025-12-05T05:00:20.000Z", "2025-12-04") is None
    assert request_free_acceptance("2025-12-05T05:00:20.000Z", "2025-12-04") == filing_date_bound(
        date(2025, 12, 4)
    )
    after_close = "2026-05-15T22:10:00.000Z"  # 18:10 EDT Friday, a 10-Q filed as of Monday
    assert request_free_acceptance(after_close, "2026-05-18") == datetime(
        2026, 5, 15, 22, 10, tzinfo=UTC
    )
    assert request_free_acceptance("", "2026-05-18") == filing_date_bound(date(2026, 5, 18))
    assert request_free_acceptance(after_close, "") == datetime(2026, 5, 15, 22, 10, tzinfo=UTC)
    assert request_free_acceptance("", "") is None


def test_the_tally_counts_only_rejected_times():
    t = AcceptanceTally()
    t.add(resolve_acceptance("2026-10-22T18:29:31.000Z", "2026-10-22", _never))
    t.add(resolve_acceptance("2025-12-05T05:00:20.000Z", "2025-12-04", lambda: None))
    t.add(
        resolve_acceptance(
            "2025-12-05T05:00:20.000Z", "2025-12-04", lambda: datetime(2025, 12, 5, tzinfo=UTC)
        )
    )
    assert (t.header, t.bound, t.rejected) == (1, 1, 2)


# --- the header read, through the real client ---------------------------------------------------------


_ACC = "0000902664-25-005199"
_CIK = "1560327"


def _client(tmp_path: Path, *, live: bool, fetch=None) -> tuple[EdgarClient, list[str]]:
    urls: list[str] = []
    c = EdgarClient(cache_dir=tmp_path, allow_live=live, user_agent="test ua")

    def _fetch(url: str) -> str:
        urls.append(url)
        return fetch(url) if fetch else _hdr("20251204190020")

    c._fetch = _fetch  # type: ignore[method-assign]
    return c, urls


def test_the_header_page_is_fetched_once_and_cached_forever(tmp_path):
    """The header lives under ``forms/<accession>/`` — the IMMUTABLE key class: one live fetch, then a cache
    hit even when the cached copy is far older than any TTL."""
    client, urls = _client(tmp_path, live=True)
    assert header_cache_key(_ACC) == f"forms/{_ACC}/{_ACC}-index-headers.html"
    assert header_url(_CIK, _ACC) == (
        f"https://www.sec.gov/Archives/edgar/data/1560327/000090266425005199/{_ACC}-index-headers.html"
    )
    assert read_header_acceptance(client, _CIK, _ACC) == datetime(2025, 12, 5, 0, 0, 20, tzinfo=UTC)
    assert urls == [header_url(_CIK, _ACC)]
    p = tmp_path / header_cache_key(_ACC)
    old = _time.time() - 400 * 24 * 3600
    os.utime(p, (old, old))
    assert read_header_acceptance(client, _CIK, _ACC) == datetime(2025, 12, 5, 0, 0, 20, tzinfo=UTC)
    assert len(urls) == 1 and client.live_fetches == 1


def test_an_unreadable_header_reads_None(tmp_path):
    """No live pulls and nothing cached, a 404, a page with no field: ``None`` (the caller bounds)."""
    cold, urls = _client(tmp_path, live=False)
    assert read_header_acceptance(cold, _CIK, _ACC) is None and urls == []

    def _404(url: str) -> str:
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError(
            "404 Not Found", request=req, response=httpx.Response(404, request=req)
        )

    gone, _ = _client(tmp_path / "a", live=True, fetch=_404)
    assert read_header_acceptance(gone, _CIK, _ACC) is None
    blank, _ = _client(tmp_path / "b", live=True, fetch=lambda url: "<HTML></HTML>")
    assert read_header_acceptance(blank, _CIK, _ACC) is None
    assert read_header_acceptance(blank, None, _ACC) is None  # no CIK, no URL


def test_a_systemic_fault_is_not_absorbed(tmp_path):
    """A missing User-Agent is not one filing's fault: it raises, never a silent bound. (The client's own
    refusal, raised from a stubbed fetch — no test ever reaches the network.)"""

    def _no_ua(url: str) -> str:
        raise RuntimeError(
            "set ALPHADECK_USER_AGENT (SEC requires a declared User-Agent with contact)"
        )

    c, _ = _client(tmp_path, live=True, fetch=_no_ua)
    with pytest.raises(RuntimeError, match="User-Agent"):
        read_header_acceptance(c, _CIK, _ACC)


def test_resolve_filing_acceptance_reads_the_header_only_when_the_list_is_rejected(tmp_path):
    client, urls = _client(tmp_path, live=True)
    tally = AcceptanceTally()
    ok = {"accession": "0000001750-26-000104", "filed": "2026-10-22"}
    ok["accepted"] = "2026-10-22T18:29:31.000Z"
    assert resolve_filing_acceptance(client, "1750", ok, tally=tally).source == SOURCE_LISTED
    assert urls == []
    late = {"accession": _ACC, "filed": "2025-12-04", "accepted": "2025-12-05T05:00:20.000Z"}
    a = resolve_filing_acceptance(client, _CIK, late, tally=tally)
    assert a.source == SOURCE_HEADER and a.at == datetime(2025, 12, 5, 0, 0, 20, tzinfo=UTC)
    assert urls == [header_url(_CIK, _ACC)]
    assert (tally.header, tally.bound) == (1, 0)
