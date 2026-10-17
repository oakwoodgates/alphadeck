"""Q17 — the FILING-GATED companyfacts class + X8, its negative cache. Every gate test COUNTS THE FETCHES
(the stubbed ``_fetch`` records each URL it was asked for), never just the returned document: a re-pull that
happens to return the same bytes would hide behind a correct read.

The submissions documents are the real ``filings.recent`` parallel-array shape EDGAR serves (the same field
names the filing legs read); the companyfacts bodies are opaque here — the gate never parses them.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest

from ingest.edgar.annual_shares import _companyfacts_or_none
from ingest.edgar.client import RECURRING_CACHE_TTL_S, CachedNotFound, EdgarClient
from ingest.edgar.companyfacts_gate import (
    ACCEPTANCE_GRACE_S,
    BACKSTOP_S,
    XBRL_FORMS,
    companyfacts_due,
)

_CIK = 1234567
_CF_KEY = f"companyfacts/CIK{_CIK:010d}.json"
_SUBS_KEY = f"submissions/CIK{_CIK:010d}.json"
_CF_URL = f"https://data.sec.gov/api/xbrl/companyfacts/CIK{_CIK:010d}.json"
_H = 3600.0
_D = 24 * _H


def _iso(epoch: float) -> str:
    """An EDGAR ``acceptanceDateTime`` ("2026-10-16T21:02:59.000Z") for an epoch instant."""
    return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(epoch))


def _subs(*rows: tuple[str, str, float]) -> dict:
    """A submissions JSON whose ``recent`` arrays hold ``(form, accession, accepted_epoch)`` rows."""
    return {
        "cik": str(_CIK),
        "filings": {
            "recent": {
                "accessionNumber": [r[1] for r in rows],
                "filingDate": [time.strftime("%Y-%m-%d", time.gmtime(r[2])) for r in rows],
                "acceptanceDateTime": [_iso(r[2]) for r in rows],
                "form": [r[0] for r in rows],
                "primaryDocument": ["doc.htm" for _ in rows],
            }
        },
    }


def _write(cache: Path, key: str, body: str, *, age_s: float) -> Path:
    p = cache / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def _client(cache: Path, *, recurring: bool = True, fail_404: bool = False):
    """A live client whose network fetch is stubbed: it records every URL and returns a fresh body (or
    raises the real 404 shape ``polite_get`` raises)."""
    fetched: list[str] = []
    c = EdgarClient(
        cache_dir=cache,
        allow_live=True,
        user_agent="test ua",
        **({"cache_ttl_s": RECURRING_CACHE_TTL_S} if recurring else {}),
    )

    def _fetch(url: str) -> str:
        fetched.append(url)
        if fail_404:
            req = httpx.Request("GET", url)
            raise httpx.HTTPStatusError(
                "404 Not Found", request=req, response=httpx.Response(404, request=req)
            )
        return '{"fresh": true}'

    c._fetch = _fetch  # type: ignore[method-assign]
    return c, fetched


# --- (b) a new XBRL-bearing filing ------------------------------------------------------------------


def test_a_new_10Q_accepted_since_the_fetch_triggers_ONE_repull(tmp_path):
    """THE recall case: a 10-Q lands after the cached companyfacts was pulled → the recurring pass pulls
    again, once. The very next read is a cache hit (the fresh file postdates the filing by more than the
    grace), so the fetch count stays at one."""
    _write(tmp_path, _CF_KEY, '{"stale": true}', age_s=3 * _D)
    _write(
        tmp_path,
        _SUBS_KEY,
        json.dumps(_subs(("10-Q", "0000000001-26-000001", time.time() - 30 * _H))),
        age_s=_H,
    )
    client, fetched = _client(tmp_path)

    assert client.get_json(_CF_URL, _CF_KEY) == {"fresh": True}
    assert fetched == [_CF_URL] and client.live_fetches == 1
    assert client.get_json(_CF_URL, _CF_KEY) == {"fresh": True}
    assert fetched == [_CF_URL] and client.live_fetches == 1  # the second read cost nothing


def test_an_AMENDED_quarter_triggers_a_repull(tmp_path):
    """A 10-Q/A restates facts; it must re-pull exactly like the original filing."""
    _write(tmp_path, _CF_KEY, "{}", age_s=2 * _D)
    _write(
        tmp_path,
        _SUBS_KEY,
        json.dumps(
            _subs(
                ("10-Q/A", "0000000001-26-000009", time.time() - 6 * _H),
                ("10-Q", "0000000001-26-000002", time.time() - 40 * _D),
            )
        ),
        age_s=_H,
    )
    client, fetched = _client(tmp_path)
    client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL]


@pytest.mark.parametrize("form", ["10-K", "10-K/A", "20-F", "40-F", "6-K", "20-F/A", "8-K"])
def test_annual_and_FPI_forms_trigger_a_repull(tmp_path, form):
    _write(tmp_path, _CF_KEY, "{}", age_s=2 * _D)
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs((form, "acc-1", time.time() - _H))), age_s=_H)
    client, fetched = _client(tmp_path)
    client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL]


def test_no_new_XBRL_filing_means_no_repull_days_past_the_recurring_ttl(tmp_path):
    """The saving: three days old (far past the five-minute dial), but everything filed since is a form that
    carries no XBRL facts (Form 4s, a 13D, a 144) and the last 10-Q predates the fetch → served from cache,
    zero requests."""
    _write(tmp_path, _CF_KEY, '{"cached": true}', age_s=3 * _D)
    now = time.time()
    _write(
        tmp_path,
        _SUBS_KEY,
        json.dumps(
            _subs(
                ("4", "acc-f4a", now - _H),
                ("SC 13D", "acc-13d", now - 2 * _H),
                ("144", "acc-144", now - _D),
                ("4", "acc-f4b", now - 2 * _D),
                ("10-Q", "acc-q", now - 40 * _D),
            )
        ),
        age_s=_H,
    )
    client, fetched = _client(tmp_path)
    assert client.get_json(_CF_URL, _CF_KEY) == {"cached": True}
    assert fetched == [] and client.live_fetches == 0


def test_a_filing_accepted_JUST_BEFORE_the_fetch_still_triggers(tmp_path):
    """The grace: a fetch that landed after the acceptance but before SEC processed the XBRL must not be
    trusted to hold it. A 10-K accepted 2 h before the cached file was written is still due."""
    _write(tmp_path, _CF_KEY, "{}", age_s=_D)
    _write(
        tmp_path,
        _SUBS_KEY,
        json.dumps(_subs(("10-K", "acc-k", time.time() - _D - 2 * _H))),
        age_s=_H,
    )
    client, fetched = _client(tmp_path)
    client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL]


def test_the_gate_reads_the_index_from_DISK_and_never_fetches_it(tmp_path):
    """No extra SEC request: deciding costs a file read, not a submissions fetch."""
    _write(tmp_path, _CF_KEY, "{}", age_s=2 * _D)
    # the index is an hour old — far past the five-minute dial, so a get_json of it WOULD re-pull it
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs(("10-Q", "acc", time.time() - 2 * _H))), age_s=_H)
    client, fetched = _client(tmp_path)
    client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL]  # companyfacts only — the index was read from disk, not re-pulled


# --- (c) the weekly backstop + the fail-toward-fetching cases ------------------------------------


def test_the_weekly_backstop_repulls_a_quiet_company(tmp_path):
    """Nothing filed at all: inside the backstop it is served; past it, re-pulled."""
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs()), age_s=_H)
    _write(tmp_path, _CF_KEY, '{"cached": true}', age_s=BACKSTOP_S - _H)
    client, fetched = _client(tmp_path)
    assert client.get_json(_CF_URL, _CF_KEY) == {"cached": True} and fetched == []

    _write(tmp_path, _CF_KEY, '{"cached": true}', age_s=BACKSTOP_S + 1)
    client, fetched = _client(tmp_path)
    assert client.get_json(_CF_URL, _CF_KEY) == {"fresh": True} and fetched == [_CF_URL]


def test_the_backstop_is_under_a_week_so_the_same_weekday_is_due():
    assert 6 * 24 * 3600 < BACKSTOP_S < 7 * 24 * 3600


def test_no_cached_index_fails_toward_fetching(tmp_path):
    _write(tmp_path, _CF_KEY, "{}", age_s=2 * _D)  # no submissions file at all
    client, fetched = _client(tmp_path)
    client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL]


def test_an_unreadable_index_fails_toward_fetching(tmp_path):
    _write(tmp_path, _CF_KEY, "{}", age_s=2 * _D)
    _write(tmp_path, _SUBS_KEY, "{not json", age_s=_H)
    client, fetched = _client(tmp_path)
    client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL]


def test_a_cache_MISS_always_fetches(tmp_path):
    """(a): an uncached company (a new basket member) is pulled whatever the index says."""
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs()), age_s=_H)
    client, fetched = _client(tmp_path)
    assert client.get_json(_CF_URL, _CF_KEY) == {"fresh": True}
    assert fetched == [_CF_URL]


def test_the_gate_is_inert_offline(tmp_path):
    """allow_live=False (the suite, --no-live): a cached companyfacts is served, gate or no gate."""
    _write(tmp_path, _CF_KEY, '{"cached": true}', age_s=30 * _D)
    c = EdgarClient(cache_dir=tmp_path, allow_live=False, cache_ttl_s=RECURRING_CACHE_TTL_S)
    assert c.get_json(_CF_URL, _CF_KEY) == {"cached": True}


# --- the INTERACTIVE path keeps its behavior --------------------------------------------------------


def test_an_INTERACTIVE_client_keeps_the_plain_12h_ttl_for_companyfacts(tmp_path):
    """The Workbench extract builds the 12h default client: it must behave exactly as before Q17, in both
    directions. (1) 13 h old with NOTHING filed → re-pulled (a recurring client would serve it). (2) 1 h old
    WITH a 10-Q accepted since → served from cache (inside the 12h TTL, as before)."""
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs()), age_s=_H)
    _write(tmp_path, _CF_KEY, '{"cached": true}', age_s=13 * _H)
    interactive, fetched = _client(tmp_path, recurring=False)
    assert interactive.get_json(_CF_URL, _CF_KEY) == {"fresh": True} and fetched == [_CF_URL]

    _write(tmp_path, _CF_KEY, '{"cached": true}', age_s=13 * _H)
    recurring, rfetched = _client(tmp_path)
    assert recurring.get_json(_CF_URL, _CF_KEY) == {"cached": True} and rfetched == []

    _write(tmp_path, _CF_KEY, '{"cached": true}', age_s=_H)
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs(("10-Q", "acc", time.time() - 30 * 60))), age_s=60)
    interactive, fetched = _client(tmp_path, recurring=False)
    assert interactive.get_json(_CF_URL, _CF_KEY) == {"cached": True} and fetched == []


def test_only_the_companyfacts_class_is_gated(tmp_path):
    """The recurring dial is untouched for every other mutable key: a submissions index past five minutes
    still re-pulls, whatever companyfacts' gate would say."""
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs()), age_s=RECURRING_CACHE_TTL_S + 60)
    client, fetched = _client(tmp_path)
    client.get_json("https://data.sec.gov/subs", _SUBS_KEY)
    assert fetched == ["https://data.sec.gov/subs"]


def test_recurring_is_the_dial_itself():
    assert EdgarClient(cache_ttl_s=RECURRING_CACHE_TTL_S).recurring is True
    assert EdgarClient().recurring is False  # the 12h interactive default


# --- X8: the negative cache ---------------------------------------------------------------------


def test_a_404_is_negative_cached_and_not_re_asked_every_night(tmp_path):
    """The first 404 is a real request and raises the real error (loud: it is news). The next recurring read
    with nothing new filed answers from the marker — no request, no ``live_fetches`` count — and still reads
    as a 404 to the callers that already handle one."""
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs(("4", "acc", time.time() - _H))), age_s=_H)
    client, fetched = _client(tmp_path, fail_404=True)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL] and (tmp_path / f"{_CF_KEY}.404").exists()

    with pytest.raises(CachedNotFound) as exc:
        client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL] and client.live_fetches == 1  # nothing reached out the second time
    assert exc.value.response.status_code == 404


def test_a_negative_cached_company_is_re_asked_on_a_new_qualifying_filing(tmp_path):
    _write(tmp_path, f"{_CF_KEY}.404", _CF_URL, age_s=2 * _D)
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs(("20-F", "acc-20f", time.time() - _H))), age_s=_H)
    client, fetched = _client(tmp_path, fail_404=True)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL]


def test_a_negative_cached_company_is_re_asked_after_the_backstop(tmp_path):
    _write(tmp_path, f"{_CF_KEY}.404", _CF_URL, age_s=BACKSTOP_S + 1)
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs()), age_s=_H)
    client, fetched = _client(tmp_path)  # this time data.sec.gov has it
    assert client.get_json(_CF_URL, _CF_KEY) == {"fresh": True}
    assert fetched == [_CF_URL]
    assert not (tmp_path / f"{_CF_KEY}.404").exists()  # a success clears the marker


def test_an_INTERACTIVE_client_ignores_the_marker_and_always_re_asks(tmp_path):
    _write(tmp_path, f"{_CF_KEY}.404", _CF_URL, age_s=_H)
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs()), age_s=_H)
    client, fetched = _client(tmp_path, recurring=False, fail_404=True)
    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            client.get_json(_CF_URL, _CF_KEY)
    assert fetched == [_CF_URL, _CF_URL]


def test_a_non_404_failure_is_never_negative_cached(tmp_path):
    """Only a 404 means "this company has none". A 503 after the retries is a transient fault: no marker,
    so the next read asks again."""
    client, fetched = _client(tmp_path)

    def _boom(url: str) -> str:
        fetched.append(url)
        req = httpx.Request("GET", url)
        raise httpx.HTTPStatusError("503", request=req, response=httpx.Response(503, request=req))

    client._fetch = _boom  # type: ignore[method-assign]
    for _ in range(2):
        with pytest.raises(httpx.HTTPStatusError):
            client.get_json(_CF_URL, _CF_KEY)
    assert len(fetched) == 2 and not (tmp_path / f"{_CF_KEY}.404").exists()


def test_a_cached_404_reads_as_absent_to_the_existing_caller(tmp_path):
    """``annual_shares._companyfacts_or_none`` already treats a live companyfacts 404 as "this filer has
    none"; the negative cache's answer must read the same."""
    _write(tmp_path, f"{_CF_KEY}.404", _CF_URL, age_s=_H)
    _write(tmp_path, _SUBS_KEY, json.dumps(_subs()), age_s=_H)
    client, fetched = _client(tmp_path)
    assert _companyfacts_or_none(client, _CIK) is None and fetched == []


# --- the pure gate + the measured form set --------------------------------------------------------


def test_the_measured_form_set_covers_periodic_amended_and_FPI_forms():
    for form in (
        "10-Q",
        "10-K",
        "10-Q/A",
        "10-K/A",
        "10-KT",
        "10-QT",
        "20-F",
        "20-F/A",
        "40-F",
        "40-F/A",
        "6-K",
        "6-K/A",
        "8-K",
        "8-K/A",
        "10-QT/A",
        "F-10/A",
    ):
        assert form in XBRL_FORMS, form
    for form in ("4", "4/A", "3", "5", "144", "SC 13D", "SC 13G", "SCHEDULE 13D", "D", "25-NSE"):
        assert form not in XBRL_FORMS, form


def test_a_row_without_an_acceptance_falls_back_to_the_END_of_its_filing_date():
    """A row EDGAR serves with no acceptanceDateTime is dated at the LAST instant of its filing date, so it
    errs toward due."""
    fetched_at = datetime(2026, 10, 16, 12, 0, 0, tzinfo=timezone.utc).timestamp()
    subs = {
        "filings": {
            "recent": {
                "form": ["10-Q"],
                "accessionNumber": ["acc"],
                "acceptanceDateTime": [""],
                "filingDate": ["2026-10-15"],
            }
        }
    }
    d = companyfacts_due(fetched_at=fetched_at, now=fetched_at + _H, submissions=subs)
    assert d.due  # 2026-10-15T23:59:59Z is inside the grace before a 2026-10-16 12:00Z fetch
    late = fetched_at + ACCEPTANCE_GRACE_S + 2 * _D
    assert not companyfacts_due(
        fetched_at=late, now=late + _H, submissions=subs
    ).due  # long after: an old quarter


def test_an_undatable_qualifying_row_is_due():
    subs = {"filings": {"recent": {"form": ["10-K"], "accessionNumber": ["acc"]}}}
    assert companyfacts_due(fetched_at=1e9, now=1e9 + 60, submissions=subs).due
