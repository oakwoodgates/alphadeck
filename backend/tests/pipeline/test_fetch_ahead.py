"""The fetch-ahead (``pipeline/fetch_ahead.py``): a thesis's vendor reads run CONCURRENTLY ahead of the one
serial writer, and the outcome must be indistinguishable from the serial walk.

THE HEADLINE — serial == concurrent, on the same fixture data, with only the HTTP layer stubbed (the REAL
``EdgarClient`` with its real cache / TTL / counters, the REAL ingest legs, the REAL W1 lock, the REAL R8 4/A
linker on real ATCH + FLS documents). Each run starts from empty fact tables and an empty cache; the stubbed
vendors sleep a random few milliseconds so the concurrent run's fetches really complete OUT of order. Then:

- every table in the database, COUNTED and compared row by row (volatile stamps and generated ids aside) —
  the as-of read dedups, so only the table can prove no duplicate or missing version;
- the ``NameResult`` of every basket row, field by field (errors, skips, edges, rename facts);
- the printed lines, in order;
- the network: the SAME requests, the same number of times (no extra request, none skipped);
- the freeze detector's counters (``live_fetches`` / ``live_completed``);
- the CALL CARD the call machinery assembles from those facts (``calls_repo._canonical``).

The basket covers every shape the handoff must get right: two companies with real 4/As (applied by the R8-B
system decision), a per-filing 404 (skipped and counted), a company whose submissions index fails (every
filing leg errors visibly), the SAME company in two segments, two securities sharing one CIK, a no-CIK
member, a no-ticker member and a price 404 (``price_not_found``).
"""

from __future__ import annotations

import collections
import dataclasses
import json
import random
import re
import threading
import time
import uuid
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest

from db.session import DEFAULT_TENANT_ID, connect, name_session
from db.write_lock import acquire_company_lock
from domain.settings import get_settings
from ingest.edgar.client import RECURRING_CACHE_TTL_S, EdgarClient
from pipeline import fetch_ahead as FA
from pipeline import ingest_thesis as IT
from pipeline import pass_timings
from pipeline.call_for_thesis import call_for_thesis
from repositories.calls_repo import _canonical
from tests import form4a_fixtures as F4A

_SAMPLE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml"
).read_text(encoding="utf-8")
_ASOF = date(2026, 10, 5)  # ATCH's five recoded P-buys (2026-09-28) are live; FLS's pair is history

# synthetic companies (the real ATCH / FLS come from the 4/A fixture corpus)
_SYN = {
    "AAA": "0009000001",
    "DDD": "0009000001",  # a second security on AAA's CIK (another share class)
    "BBB": "0009000002",  # one Form 4 document 404s (skipped + counted)
    "CCC": "0009000003",  # its submissions index answers 503 every time (every filing leg errors)
    "GGG": "0009000007",  # no ticker: filing legs only
}
_FAIL_CIK = 9000003
_F404 = "0009000002-26-000404"


# --- the stubbed vendors ----------------------------------------------------------------------------------


class _NoBudget:
    """The global SEC budget, stood in: its own tests are ``tests/ingest/test_sec_budget.py``."""

    def acquire(self):
        return None

    def penalize(self, seconds):
        pass


def _status(code: int, url: str) -> httpx.HTTPStatusError:
    req = httpx.Request("GET", url)
    return httpx.HTTPStatusError(
        f"{code} for url '{url}'", request=req, response=httpx.Response(code, request=req)
    )


def _subs_doc(rows: list[tuple[str, str, str, str, str]]) -> dict:
    """``rows`` = (form, accession, primary doc, filed, accepted) — EDGAR's parallel ``recent`` arrays."""
    return {
        "name": "fixture co",
        "tickers": [],
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [r[1] for r in rows],
                "primaryDocument": [r[2] for r in rows],
                "filingDate": [r[3] for r in rows],
                "acceptanceDateTime": [r[4] for r in rows],
                "items": ["2.02,9.01" if r[0] == "8-K" else "" for r in rows],
            }
        },
    }


def _real_rows(group: str) -> list[tuple[str, str, str, str, str]]:
    rows = [
        (
            f.form,
            acc,
            f"xslF345X05/{acc}.xml",
            f.filed.isoformat(),
            f.accepted.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        )
        for acc, f in F4A.FILINGS.items()
        if f.key == group
    ]
    return sorted(rows, key=lambda r: r[4], reverse=True)  # the feed is newest-first


def _syn_rows(cik: str, n: int, extra=()) -> list[tuple[str, str, str, str, str]]:
    rows = [
        ("4", f"{cik}-26-{i:06d}", f"xslF345X05/{cik}-26-{i:06d}.xml", "2026-06-02", "")
        for i in range(1, n + 1)
    ]
    rows.append(("8-K", f"{cik}-26-900001", "ek.htm", "2026-06-03", ""))
    return list(extra) + rows


def _submissions() -> dict[int, dict]:
    return {
        int(F4A.CIKS["ATCH"]): _subs_doc(_real_rows("ATCH")),
        int(F4A.CIKS["FLS"]): _subs_doc(_real_rows("FLS")),
        9000001: _subs_doc(_syn_rows("0009000001", 2)),
        9000002: _subs_doc(
            _syn_rows(
                "0009000002",
                1,
                extra=[("4", _F404, f"xslF345X05/{_F404}.xml", "2001-03-01", "")],
            )
        ),
        9000007: _subs_doc(_syn_rows("0009000007", 1)),
    }


class _Sec:
    """The SEC's HTTP layer (``EdgarClient._fetch``): submissions JSON per CIK, the real 4/A corpus, the
    sample Form 4 re-issued per company (its issuer = the CIK in the URL, the shape R8-A requires). Sleeps a
    random few ms, records every URL and the peak number of requests in flight."""

    def __init__(self, seed: int) -> None:
        self._rand = random.Random(seed)
        self._subs = _submissions()
        self._lock = threading.Lock()
        self.urls: list[str] = []
        self._in_flight = 0
        self.max_in_flight = 0

    def __call__(self, url: str) -> str:
        with self._lock:
            self.urls.append(url)
            self._in_flight += 1
            self.max_in_flight = max(self.max_in_flight, self._in_flight)
            pause = self._rand.uniform(0, 0.012)
        try:
            time.sleep(pause)
            return self._serve(url)
        finally:
            with self._lock:
                self._in_flight -= 1

    def _serve(self, url: str) -> str:
        m = re.search(r"/submissions/CIK(\d{10})\.json$", url)
        if m:
            cik = int(m.group(1))
            if cik == _FAIL_CIK:
                raise _status(503, url)
            return json.dumps(self._subs[cik])
        m = re.search(r"/data/(\d+)/[0-9]+/([^/]+)\.xml$", url)
        if not m:
            raise _status(404, url)  # e.g. a 13D identity document: not served (deterministically)
        cik, acc = int(m.group(1)), m.group(2)
        if acc == _F404:
            raise _status(404, url)
        if acc in F4A.FILINGS:
            return F4A.xml(acc)
        return _SAMPLE.replace(
            "<issuerCik>0001234567</issuerCik>", f"<issuerCik>{cik:010d}</issuerCik>"
        )


class _Prices:
    """The price vendor: a deterministic two-year series per symbol (FFF answers 404); random few-ms pause."""

    def __init__(self, seed: int) -> None:
        self._rand = random.Random(seed)
        self._lock = threading.Lock()
        self.calls: list[tuple[str, bool, bool]] = []

    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        with self._lock:
            self.calls.append((ticker, allow_live, force_refresh))
            pause = self._rand.uniform(0, 0.012)
        time.sleep(pause)
        if ticker == "FFF":
            raise _status(404, f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}")
        seed = sum(map(ord, ticker))
        start = date(2024, 10, 1)
        out = []
        for i in range(520):
            d = start + timedelta(days=i * 7 // 5)
            if d > date(2026, 10, 2) or d.weekday() >= 5:
                continue
            close = round(5 + (seed % 17) + i * 0.013 + (i % 9) * 0.07, 4)
            out.append(
                {
                    "d": d,
                    "open": close,
                    "high": close * 1.01,
                    "low": close * 0.99,
                    "close": close,
                    "volume": 100_000.0 + (i * seed) % 50_000,
                }
            )
        return out


# --- the fixture thesis ------------------------------------------------------------------------------------


def _security(db, ticker: str | None, cik: str | None) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, name, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, f"{ticker or cik} (fixture)", "2020-01-01"),
        )
    db.commit()
    return sid


def _thesis(db, members: list[tuple[str, uuid.UUID]]) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, "Fetch-ahead fixture", "n"),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member (id, tenant_id, thesis_id, ordinal, ticker, role, "
                "security_id) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid


def _basket(db) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    s = {
        "ATCH": F4A.make_security(db, "ATCH"),
        "FLS": F4A.make_security(db, "FLS"),
        "AAA": _security(db, "AAA", _SYN["AAA"]),
        "BBB": _security(db, "BBB", _SYN["BBB"]),
        "CCC": _security(db, "CCC", _SYN["CCC"]),
        "DDD": _security(db, "DDD", _SYN["DDD"]),
        "EEE": _security(db, "EEE", None),
        "FFF": _security(db, "FFF", None),
        "GGG": _security(db, None, _SYN["GGG"]),
    }
    order = ["ATCH", "AAA", "BBB", "CCC", "AAA", "DDD", "FLS", "EEE", "FFF", "GGG", "ATCH"]
    return _thesis(db, [(t, s[t]) for t in order]), s


# --- the snapshot: every table, every row ------------------------------------------------------------------

_NOT_STATE = {"schema_migrations", "sec_request_budget"}


def _volatile(db) -> dict[str, set[str]]:
    """Per table, the columns a re-run legitimately changes: DB-clock stamps and generated ids."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name, column_name, column_default, data_type FROM information_schema.columns "
            "WHERE table_schema = 'public'"
        )
        out: dict[str, set[str]] = collections.defaultdict(set)
        for r in cur.fetchall():
            default = r["column_default"] or ""
            if any(
                k in default for k in ("now()", "gen_random_uuid", "nextval", "clock_timestamp")
            ):
                out[r["table_name"]].add(r["column_name"])
            if r["data_type"].startswith("timestamp") and r["column_name"].endswith("_at"):
                if r["column_name"] in ("recorded_at", "created_at", "updated_at", "decided_at"):
                    out[r["table_name"]].add(r["column_name"])
    return out


def _tables(db) -> list[str]:
    with db.cursor() as cur:
        cur.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename"
        )
        return [r["tablename"] for r in cur.fetchall() if r["tablename"] not in _NOT_STATE]


def _snapshot(db) -> dict[str, list[tuple]]:
    db.rollback()
    volatile = _volatile(db)
    snap: dict[str, list[tuple]] = {}
    for table in _tables(db):
        with db.cursor() as cur:
            cur.execute(f'SELECT * FROM "{table}"')  # noqa: S608 — names from pg_tables
            rows = cur.fetchall()
        drop = volatile.get(table, set())
        snap[table] = sorted(
            tuple(sorted((k, repr(v)) for k, v in row.items() if k not in drop)) for row in rows
        )
    db.rollback()
    return snap


def _changed(before: dict, after: dict) -> list[str]:
    return [t for t in after if after[t] != before.get(t)]


def _reset(db, tables: list[str]) -> None:
    with db.cursor() as cur:
        cur.execute(
            "TRUNCATE " + ", ".join(f'"{t}"' for t in tables) + " RESTART IDENTITY"
        )  # noqa: S608
    db.commit()


@pytest.fixture
def concurrency(monkeypatch):
    def set_(n: int) -> None:
        monkeypatch.setenv("ALPHADECK_INGEST_FETCH_CONCURRENCY", str(n))
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


def _walk(db, tid, *, cache_dir, seed: int, capsys, client_kw=None):
    sec, prices = _Sec(seed), _Prices(seed + 1)
    client = EdgarClient(
        cache_dir=cache_dir,
        allow_live=True,
        user_agent="fixture ua",
        cache_ttl_s=RECURRING_CACHE_TTL_S,
        budget=_NoBudget(),
        **(client_kw or {}),
    )
    client._fetch = sec  # type: ignore[method-assign]  # ONLY the HTTP layer is stubbed
    capsys.readouterr()
    results = IT.ingest_thesis(
        db, tid, allow_live=True, force_refresh=True, edgar_client=client, price_source=prices
    )
    printed = capsys.readouterr().out.splitlines()
    return results, printed, client, sec, prices


def _comparable(results) -> list[dict]:
    return [dataclasses.asdict(r) for r in results]


# --- the headline ------------------------------------------------------------------------------------------


def test_CONCURRENT_fetch_writes_exactly_what_the_SERIAL_walk_writes(
    db, tmp_path, capsys, concurrency
):
    tid, _ = _basket(db)
    baseline = _snapshot(db)

    concurrency(1)
    r1, out1, c1, sec1, px1 = _walk(db, tid, cache_dir=tmp_path / "serial", seed=7, capsys=capsys)
    card1 = _canonical(call_for_thesis(db, tid, _ASOF, record=False))
    snap1 = _snapshot(db)
    written = _changed(baseline, snap1)
    assert all(not baseline.get(t) for t in written)  # every table the walk wrote started empty
    _reset(db, written)
    assert _snapshot(db) == baseline

    concurrency(6)
    r2, out2, c2, sec2, px2 = _walk(
        db, tid, cache_dir=tmp_path / "concurrent", seed=11, capsys=capsys
    )
    card2 = _canonical(call_for_thesis(db, tid, _ASOF, record=False))
    snap2 = _snapshot(db)

    # NOT VACUOUS: the walk wrote real facts, printed real skips, and the concurrent run really overlapped
    assert {"fact_insider_txn", "fact_price_eod", "insider_amendment"} <= set(written)
    assert any("form4" in line and _F404 in line for line in out1)
    assert sec1.max_in_flight == 1 and sec2.max_in_flight >= 2

    # COUNT + COMPARE EVERY TABLE — the same facts, the same versions
    assert {t: len(rows) for t, rows in snap2.items()} == {
        t: len(rows) for t, rows in snap1.items()
    }
    assert snap2 == snap1
    # every basket row's outcome, field by field — including the per-name errors (#9)
    assert _comparable(r2) == _comparable(r1)
    by = {r.ticker: r for r in r1}
    assert by["CCC"].error and "form4" in by["CCC"].error and "503" in by["CCC"].error
    assert by["CCC"].price_bars_appended > 0  # its other legs still ran
    assert by["FFF"].price_not_found is True
    assert by["BBB"].form4_skipped == 1
    # the printed report, line for line and in order
    assert out2 == out1
    # the network: the same requests, each the same number of times — none extra, none skipped
    assert collections.Counter(sec2.urls) == collections.Counter(sec1.urls)
    assert collections.Counter(px2.calls) == collections.Counter(px1.calls)
    # the freeze detector's counters (thread-safe increments)
    assert (c2.live_fetches, c2.live_completed) == (c1.live_fetches, c1.live_completed)
    assert c1.live_fetches == len(sec1.urls)
    # ...and the freeze FLOOR's per-company fact: every completed fetch attributed to exactly the company
    # whose reads made it, though the workers' fetches overlap other companies' legs (per-thread counts)
    assert [r.edgar_completed for r in r2] == [r.edgar_completed for r in r1]
    assert sum(r.edgar_completed for r in r2) == c2.live_completed
    assert r1[0].ticker == "ATCH" and r1[0].edgar_completed > 0  # its first row fetched...
    assert r1[-1].ticker == "ATCH" and r1[-1].edgar_completed == 0  # ...its second read the cache
    assert by["CCC"].edgar_completed == 0  # every SEC read of CCC failed: nothing completed
    # ...and the call the machinery assembles from those facts
    assert card2 == card1
    assert json.loads(card1)["state"] != "incubating"  # a real, opinionated card was compared


def test_concurrency_ONE_is_the_serial_walk_no_pool_no_fetch_ahead(
    db, tmp_path, capsys, concurrency, monkeypatch
):
    """The kill switch: ``ingest_fetch_concurrency = 1`` never builds a fetch-ahead, and every vendor read
    happens IN the walk — name by name, in basket order, each company's reads before the next one's.
    """

    def _refuse(*a, **k):
        raise AssertionError("the serial walk must not build a fetch-ahead")

    monkeypatch.setattr(IT, "FetchAhead", _refuse)
    tid, s = _basket(db)
    concurrency(1)
    results, _, client, sec, prices = _walk(db, tid, cache_dir=tmp_path, seed=3, capsys=capsys)

    assert len(results) == 11 and sec.max_in_flight == 1
    # the submissions reads appear in basket order of first appearance (one company at a time)
    ciks = [int(m.group(1)) for u in sec.urls if (m := re.search(r"CIK(\d{10})", u))]
    first_seen = list(dict.fromkeys(ciks))
    assert first_seen == [
        int(F4A.CIKS["ATCH"]),
        9000001,
        9000002,
        9000003,
        int(F4A.CIKS["FLS"]),
        9000007,
    ]
    # the price pulls in basket order too (the AAA and ATCH rows re-pull, exactly as before)
    assert [c[0] for c in prices.calls] == [
        "ATCH", "AAA", "BBB", "CCC", "AAA", "DDD", "FLS", "EEE", "FFF", "ATCH",
    ]  # fmt: skip
    assert client.live_fetches == len(sec.urls)


def test_a_cache_only_walk_never_builds_a_fetch_ahead(db, monkeypatch, concurrency, tmp_path):
    """No round trip, nothing to overlap: a --no-live walk is the serial walk whatever the dial says."""

    def _refuse(*a, **k):
        raise AssertionError("a cache-only walk must not build a fetch-ahead")

    monkeypatch.setattr(IT, "FetchAhead", _refuse)
    concurrency(6)
    assert FA.fetch_workers(allow_live=False) == 1
    assert FA.fetch_workers(allow_live=True) == 6  # the dial, as set
    tid, _ = _basket(db)
    results = IT.ingest_thesis(  # cold cache: the filing legs miss per name; no pool is built
        db,
        tid,
        allow_live=False,
        edgar_client=EdgarClient(cache_dir=tmp_path, budget=_NoBudget()),
        price_source=_Prices(1),
    )
    assert len(results) == 11


# --- X5: the counters stay exact under concurrency --------------------------------------------------------


def test_X5_counts_are_identical_and_worker_time_is_reported_apart(
    db, tmp_path, capsys, concurrency
):
    """Every request is counted ONCE, on the leg the serial walk counts it on (the worker's child meter is
    absorbed by the writer); worker seconds go to ``concurrent_s`` and the exclusive legs still sum to the
    thesis's total."""
    tid, _ = _basket(db)
    baseline = _snapshot(db)

    def metered(n: int, where: str):
        concurrency(n)
        timer = pass_timings.start()
        token = pass_timings.activate(timer)
        try:
            for _ in pass_timings.per_thesis([type("T", (), {"id": tid, "name": "fx"})()]):
                _walk(db, tid, cache_dir=tmp_path / where, seed=5, capsys=capsys)
        finally:
            pass_timings.deactivate(token)
        return timer.snapshot()

    serial = metered(1, "serial")
    _reset(db, _changed(baseline, _snapshot(db)))
    concurrent = metered(6, "concurrent")

    def network(snap):  # every count that is a request or a cache decision (not seconds)
        return {
            leg: {k: v for k, v in kinds.items() if not k.endswith("_s")}
            for leg, kinds in snap["counts"].items()
        }

    assert network(concurrent) == network(serial)
    assert network(serial)["submissions"]["sec_live"] > 0
    t = concurrent["theses"][0]
    assert "fetch_wait" in t["legs"] and t["concurrent_s"]
    assert {"submissions", "price", "form4"} <= set(t["concurrent_s"])
    assert abs(sum(t["legs"].values()) - t["total_s"]) < 0.05  # exclusive time still sums
    assert serial["theses"][0]["concurrent_s"] == {}
    assert concurrent["v"] == pass_timings.SCHEMA_VERSION == 2


def test_the_EDGAR_clients_counters_are_exact_under_threads(tmp_path):
    """``live_fetches`` (attempts) and ``live_completed`` (2xx bodies) are the freeze detector's inputs; with
    one client driven by many threads, no increment may be lost."""
    lock = threading.Lock()
    calls = {"n": 0}

    def _fetch(url: str) -> str:
        with lock:
            calls["n"] += 1
            n = calls["n"]
        if n % 7 == 0:
            raise _status(503, url)
        return "{}"

    client = EdgarClient(cache_dir=tmp_path, allow_live=True, user_agent="ua", budget=_NoBudget())
    client._fetch = _fetch  # type: ignore[method-assign]
    barrier = threading.Barrier(16)

    def worker(w: int) -> None:
        barrier.wait()
        for i in range(60):
            try:
                client.get_text(f"https://data.sec.gov/x/{w}/{i}", f"misc/{w}/{i}.json")
            except httpx.HTTPStatusError:
                pass

    threads = [threading.Thread(target=worker, args=(w,)) for w in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    failed = sum(1 for n in range(1, 961) if n % 7 == 0)
    assert client.live_fetches == 960 == calls["n"]
    assert client.live_completed == 960 - failed


# --- W1: the per-company write lock still governs every write ---------------------------------------------


def test_W1_a_company_held_by_another_writer_is_deferred_never_written_while_held(
    db, tmp_path, capsys, concurrency, monkeypatch
):
    """With the fetch-ahead running, a company another writer holds is still DEFERRED (its fetched reads are
    dropped), the rest are ingested, and nothing is written for it until the holder lets go — then exactly
    once (count the table)."""
    concurrency(6)
    monkeypatch.setenv(
        "ALPHADECK_INGEST_LOCK_WAIT_S", "120"
    )  # generous: the holder lets go long before
    get_settings.cache_clear()
    tid, s = _basket(db)
    holder = connect()
    name_session(holder, "alphadeck-writer:workbench-ingest")
    lock = acquire_company_lock(holder, s["FLS"], wait_s=0)
    peek = connect()  # opened up front: the check never waits on a slow connect
    seen_while_held: dict[str, int] = {}
    eee_done = threading.Event()  # EEE sits AFTER FLS in the basket (FLS is row 6, EEE row 7)
    real_one = IT._ingest_one

    def spy(conn, client, source, m, **kw):
        r = real_one(conn, client, source, m, **kw)
        if m.ticker == "EEE":
            eee_done.set()
        return r

    monkeypatch.setattr(IT, "_ingest_one", spy)

    def count(table, sid):
        peek.rollback()  # a fresh snapshot: the writer's commits must be visible
        with peek.cursor() as cur:
            cur.execute(f"SELECT count(*) n FROM {table} WHERE security_id = %s", (sid,))
            return cur.fetchone()["n"]

    def release_after_the_free_ones():
        # once a company AFTER FLS has been written, look at FLS, then let go
        eee_done.wait(timeout=120)
        seen_while_held["eee_written"] = int(eee_done.is_set())
        seen_while_held["fls_prices"] = count("fact_price_eod", s["FLS"])
        seen_while_held["fls_insider"] = count("fact_insider_txn", s["FLS"])
        lock.release()

    t = threading.Thread(target=release_after_the_free_ones)
    t.start()
    try:
        results, _, _, _, _ = _walk(db, tid, cache_dir=tmp_path, seed=9, capsys=capsys)
    finally:
        t.join(timeout=150)
        holder.close()
        peek.close()

    # nothing was written for it while the other writer held it — though a company AFTER it in the basket
    # already had been: it was DEFERRED, not merely reached late...
    assert seen_while_held == {"eee_written": 1, "fls_prices": 0, "fls_insider": 0}
    # ...and once let go it was ingested by this walk, in its basket slot, never dropped (#9)
    fls = [r for r in results if r.ticker == "FLS"]
    assert [r.ticker for r in results].index("FLS") == 6
    assert len(fls) == 1 and fls[0].lock_busy is None and fls[0].error is None
    assert fls[0].price_bars_appended > 0 and fls[0].form4_appended > 0
    with db.cursor() as cur:
        cur.execute(
            "SELECT count(*) n, count(DISTINCT d) d FROM fact_price_eod WHERE security_id = %s",
            (s["FLS"],),
        )
        row = cur.fetchone()
    assert row["n"] == row["d"] == fls[0].price_bars_appended  # each bar exactly once


def test_W1_every_leg_runs_holding_exactly_its_own_company_lock_under_concurrency(
    db, tmp_path, capsys, concurrency, monkeypatch
):
    """The structural half, with the pool running: every leg's writes happen on the writer's session while
    it holds that company's lock and no other — the workers never write."""
    concurrency(6)
    tid, s = _basket(db)
    seen: list[tuple[str, uuid.UUID, set[int], str]] = []

    def held_by(conn) -> set[int]:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT objid FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() "
                "AND classid = %s AND granted",
                (0x414443,),
            )
            return {r["objid"] for r in cur.fetchall()}

    def spy(leg, real):
        def wrapped(conn, *a, **k):
            sec = a[1] if leg == "form4" else (a[0] if leg == "price" else a[1])
            seen.append((leg, sec.id, held_by(conn), threading.current_thread().name))
            return real(conn, *a, **k)

        return wrapped

    monkeypatch.setattr(IT, "_form4_leg", spy("form4", IT._form4_leg))
    monkeypatch.setattr(IT, "ingest_bars_for_security", spy("price", IT.ingest_bars_for_security))
    _walk(db, tid, cache_dir=tmp_path, seed=13, capsys=capsys)

    from db.write_lock import _as_oid, company_key

    assert seen
    main = threading.main_thread().name
    for leg, sid, held, thread in seen:
        assert held == {_as_oid(company_key(sid))}, (leg, sid, held)
        assert thread == main  # every write is the writer's, never a worker's
