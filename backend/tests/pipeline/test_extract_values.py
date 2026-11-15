"""V2 W3d: THE BACKFILL CLI (``python -m pipeline.extract_values``): dry run by default, cache-only, --apply.

No SEC request anywhere: the dry run is proven to construct no client and to call none; the apply tests either
inject a FAKE client (the ``tests/platform_values/test_extraction_run.py`` pattern, with the extraction itself
monkeypatched) or a real ``EdgarClient(allow_live=False)`` over a cache laid out in ``tmp_path`` from the repo's
extractor fixtures. Every artifact directory is a ``tmp_path``. Each test COUNTS THE TABLES.

What is pinned:
- the DRY RUN reads the work list and the cache on disk only: every table count unchanged, no client built or
  called, no file written; its request estimate is exact over a tmp cache (a fresh index and cached documents
  cost nothing; a stale index costs one; a missing values document costs one; a newer 10-K is the one document
  read; a 40-F adds a bound for the exhibit search; a company with no cached index is a range);
- ``--apply`` refuses inside the nightly window, while another backfill holds lock 7, and while a pass holds the
  call-of-record lock; it stops between companies when a pass starts; it is resumable (a second run writes
  nothing for finished companies) and bounded by ``--max-names``; ``--ticker`` scopes it and an unknown ticker
  refuses it;
- ``--no-live`` end to end: a company laid out as a cache extracts and its values land in the data tenant; a
  company whose documents are missing gets an ``error`` attempt (``not cached``) and no fact;
- the run artifact (``data/extraction_runs/``, redirected to tmp) carries ``admin_line``; the summary and the B4
  line are printed.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest

from domain.extraction import ExtractedFact, ExtractionResult, Tier
from domain.market_time import market_now, market_today
from domain.scopes import DATA_TENANT_ID
from domain.settings import get_settings
from pipeline import extract_values as xv
from pipeline.run_lock import (
    LOCK_ID_EXTRACTION,
    LOCK_ID_TERM_MENTIONS,
    acquire_job_lock,
    acquire_pass_lock,
)
from platform_values import extraction as ex
from tests.workspaces import new_workspace

_FX = Path(__file__).resolve().parent.parent / "fixtures" / "sec_extractor"


def _now() -> datetime:
    """The clock, read INSIDE a test (never at import)."""
    return market_now().astimezone(timezone.utc)


def _old() -> datetime:
    return _now() - timedelta(days=60)


# --- the world ---------------------------------------------------------------------------------------------------


def _company(db, ticker: str, cik: str | None, kind: str = "equity") -> UUID:
    sid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, name, cik, instrument_kind, valid_from) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (sid, DATA_TENANT_ID, ticker, f"{ticker} Corp", cik, kind, date(2026, 1, 1)),
        )
    return sid


def _card(db, workspace: UUID, sids: list[UUID]) -> UUID:
    tid = uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, 'n')",
            (tid, workspace, f"card {tid.hex[:6]}"),
        )
        for i, sid in enumerate(sids):
            cur.execute(
                "INSERT INTO basket_member (tenant_id, thesis_id, ordinal, ticker, role, security_id, segment) "
                "VALUES (%s, %s, %s, 'T', 'the name', %s, 'Fuel')",
                (workspace, tid, i, sid),
            )
    return tid


def _world(db, tickers: list[tuple[str, str | None]]) -> dict[str, UUID]:
    """Companies on live cards: the first on three cards, the second on two, the rest on one (the run order)."""
    w = new_workspace(db, "W")
    ids = {t: _company(db, t, cik) for t, cik in tickers}
    order = list(ids.values())
    _card(db, w, order)
    _card(db, new_workspace(db, "W2"), order[:2])
    _card(db, new_workspace(db, "W3"), order[:1])
    db.commit()
    return ids


def _all_tables(db) -> dict[str, int]:
    """Every base table's row count (the dry run must leave every one unchanged)."""
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name"
        )
        names = [r["table_name"] for r in cur.fetchall()]
        out = {}
        for n in names:
            cur.execute(f'SELECT count(*) AS n FROM "{n}"')  # noqa: S608 — names from the catalog
            out[n] = cur.fetchone()["n"]
    db.rollback()
    return out


def _counts(db) -> dict[str, int]:
    db.rollback()
    out = {}
    with db.cursor() as cur:
        for t in (
            "fact_shares_outstanding",
            "fact_cash_burn",
            "extraction_candidate",
            "platform_extraction_attempt",
        ):
            cur.execute(f"SELECT count(*) AS n FROM {t}")  # noqa: S608 — fixed names
            out[t] = cur.fetchone()["n"]
    db.rollback()
    return out


def _attempts(db) -> list[dict]:
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT sm.ticker, a.run_kind, a.status, a.error, a.shares_reason, a.cash_reason "
            "FROM platform_extraction_attempt a JOIN security_master sm ON sm.id = a.security_id "
            "ORDER BY a.recorded_at, sm.ticker"
        )
        rows = [dict(r) for r in cur.fetchall()]
    db.rollback()
    return rows


# --- a cache on disk ---------------------------------------------------------------------------------------------


def _put(cache: Path, key: str, text: str, *, age_s: float = 0.0) -> Path:
    p = cache / key
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    if age_s:
        t = time.time() - age_s
        os.utime(p, (t, t))
    return p


def _subs(rows: list[tuple[str, str, str, datetime, int]]) -> dict:
    """A submissions index, newest first: (form, accession, primary document, accepted, size in bytes)."""
    rows = sorted(rows, key=lambda r: r[3], reverse=True)
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [r[1] for r in rows],
                "primaryDocument": [r[2] for r in rows],
                "filingDate": [r[3].date().isoformat() for r in rows],
                "reportDate": [(r[3] - timedelta(days=40)).date().isoformat() for r in rows],
                "acceptanceDateTime": [r[3].strftime("%Y-%m-%dT%H:%M:%S.000Z") for r in rows],
                "size": [r[4] for r in rows],
            }
        }
    }


def _snapshot(root: Path) -> set[tuple[str, int, float]]:
    return {
        (str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime)
        for p in root.rglob("*")
        if p.is_file()
    }


class Exploding:
    """A client the dry run must never touch."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_json(self, url: str, cache_key: str, **_: Any) -> dict:
        self.calls.append(cache_key)
        raise AssertionError(f"the dry run read {cache_key}")

    def get_text(self, url: str, cache_key: str, **_: Any) -> str:
        self.calls.append(cache_key)
        raise AssertionError(f"the dry run read {cache_key}")


def _dry_cache(tmp_path: Path) -> Path:
    """The six cases the estimate must get exactly right (the module docstring's list)."""
    cache = tmp_path / "edgar_cache"
    old = _old()
    cf = json.dumps({"facts": {}})
    # FRESH: a fresh index, current companyfacts, the 10-Q document cached -> 0 requests
    _put(
        cache,
        "submissions/CIK0000000101.json",
        json.dumps(_subs([("10-Q", "q-101", "f.htm", old, 50_000)])),
    )
    _put(cache, "companyfacts/CIK0000000101.json", cf)
    _put(cache, "forms/q-101/f.htm", "the 10-Q")
    # STALE: an index older than the recurring 5 minutes (1) + the 10-Q document missing (1) -> 2
    _put(
        cache,
        "submissions/CIK0000000102.json",
        json.dumps(_subs([("10-Q", "q-102", "s.htm", old, 120_000)])),
        age_s=3600,
    )
    _put(cache, "companyfacts/CIK0000000102.json", cf)
    # NEWK: a 10-K newer than the 10-Q is the ONE document read (values only); it is missing -> index + 10-K = 2
    _put(
        cache,
        "submissions/CIK0000000103.json",
        json.dumps(
            _subs(
                [
                    ("10-Q", "q-103", "q.htm", old - timedelta(days=30), 80_000),
                    ("10-K", "k-103", "k.htm", old, 900_000),
                ]
            )
        ),
        age_s=3600,
    )
    _put(cache, "companyfacts/CIK0000000103.json", cf)
    _put(cache, "forms/q-103/q.htm", "the older 10-Q")
    # FORTY: a 40-F filer with its document cached -> 0 exact, plus the exhibit search's bound
    _put(
        cache,
        "submissions/CIK0000000104.json",
        json.dumps(_subs([("40-F", "f-104", "a.htm", old, 4_000_000)])),
    )
    _put(cache, "companyfacts/CIK0000000104.json", cf)
    _put(cache, "forms/f-104/a.htm", "the 40-F wrapper")
    # NOINDEX: nothing on disk -> 1 known request, the rest unknown until the index is read
    return cache


def test_the_DRY_RUN_reads_only_the_work_list_and_the_disk_and_estimates_exactly(
    db, tmp_path, monkeypatch, capsys
):
    ids = _world(
        db,
        [
            ("FRESH", "101"),
            ("STALE", "102"),
            ("NEWK", "103"),
            ("FORTY", "104"),
            ("NOINDEX", "105"),
            ("NOCIK", None),
        ],
    )
    cache = _dry_cache(tmp_path)
    runs = tmp_path / "runs"
    before_tables, before_files = _all_tables(db), _snapshot(cache)

    def no_client(*_: Any, **__: Any):
        raise AssertionError("the dry run built an EDGAR client")

    monkeypatch.setattr(xv, "EdgarClient", no_client)
    fake = Exploding()

    rc = xv.run(apply=False, live=True, cache_dir=cache, runs_dir=runs, client=fake)
    out = capsys.readouterr().out

    assert rc == 0
    assert fake.calls == []  # no client read, so no SEC request
    assert _all_tables(db) == before_tables  # no row written anywhere
    assert _snapshot(cache) == before_files and not runs.exists()  # no file written

    plans = {
        p.ticker: p
        for p in xv.plan_work(
            ex.work_list(db, include_never=True).items, cache_dir=cache, now=_now()
        )
    }
    db.rollback()
    got = {t: (p.path, p.live_requests, p.max_requests, p.from_cache) for t, p in plans.items()}
    bound = (
        xv.exhibit_bound()
    )  # the annual exhibit search: index headers + filing index + EX-99 documents
    assert got == {
        "FRESH": ("periodic", 0, 0, 3),
        "STALE": (
            "periodic",
            2,
            2,
            1,
        ),  # the index refresh + the 10-Q document; companyfacts is current
        "NEWK": (
            "periodic",
            2,
            2,
            1,
        ),  # the index refresh + the newer 10-K; the cached 10-Q is never read
        "FORTY": ("annual", 0, bound, 3),
        "NOINDEX": ("index not cached", 1, 3 + bound, 0),
        "NOCIK": ("no CIK", 0, 0, 0),
    }
    # the newer 10-K is the document a real run reads (values only), never the cached 10-Q
    assert [d.key for d in plans["NEWK"].docs if d.kind == "document"] == ["forms/k-103/k.htm"]
    # bytes: the index's filing size bounds a missing document; a cached index's size estimates its refresh
    assert plans["STALE"].bytes_bound == 120_000 and plans["NEWK"].bytes_bound == 900_000
    # the totals line: 5 requests known; up to 5 + FORTY's bound + NOINDEX's (2 more + bound) with the unknowns
    assert f"{5:,} SEC requests" in out
    assert f"up to {5 + bound + 2 + bound:,}" in out
    assert "DRY RUN" in out and "nothing written" in out
    assert "B4" in out and "never-extracted" in out
    assert "CANNOT KNOW" in out
    assert ids  # (the world exists)


def _item(ticker: str, cik: str | None, cards: int = 1) -> ex.WorkItem:
    return ex.WorkItem(
        security_id=uuid4(),
        ticker=ticker,
        cik=cik,
        instrument_kind="equity",
        cards=cards,
        pen_only=False,
        reason="never",
        note="never attempted",
        inputs_key="",
    )


def test_the_plan_is_read_from_disk_alone_and_a_cache_only_plan_names_who_would_fail(tmp_path):
    """No database: the estimate is a pure read of the cache on disk."""
    cache = _dry_cache(tmp_path)
    before = _snapshot(cache)
    items = [
        _item("FRESH", "101"),
        _item("STALE", "102"),
        _item("NEWK", "103"),
        _item("FORTY", "104"),
        _item("NOINDEX", "105"),
        _item("NOCIK", None),
    ]
    plans = xv.plan_work(items, cache_dir=cache, now=_now())
    assert _snapshot(cache) == before
    bound = xv.exhibit_bound()
    assert bound == 8  # index headers + filing index + 6 EX-99 documents (exhibit_scan_max)
    by = {p.ticker: p for p in plans}
    assert [(p.ticker, p.live_requests, p.max_requests) for p in plans] == [
        ("FRESH", 0, 0),
        ("STALE", 2, 2),
        ("NEWK", 2, 2),
        ("FORTY", 0, bound),
        ("NOINDEX", 1, 3 + bound),
        ("NOCIK", 0, 0),
    ]
    assert by["NEWK"].filing == "10-K k-103" and by["FORTY"].filing == "40-F f-104"
    # cache only: a stale copy is served, a missing one is 'not cached'
    t = xv.PlanTotals.of(plans)
    assert (t.offline_ok, t.offline_missing, t.no_cik) == (2, 3, 1)
    assert [p.ticker for p in plans if not p.offline_ok and p.path != "no CIK"] == [
        "STALE",
        "NEWK",
        "NOINDEX",
    ]
    lines: list[str] = []
    xv._print_plan(plans, plans, live=False, out=lines.append)
    text = "\n".join(lines)
    assert "STALE (never, 1 card): periodic 10-Q q-102: NOT CACHED (10-Q q-102)" in text
    assert "0 SEC requests" in text
    # bytes: the filing sizes bound the missing documents; refreshes are estimated from the cached copies
    assert (t.bytes_bound, t.unknown_size) == (
        120_000 + 900_000,
        1,
    )  # NOINDEX's index: size unknown
    assert t.refresh_bytes == sum(
        (cache / k).stat().st_size
        for k in ("submissions/CIK0000000102.json", "submissions/CIK0000000103.json")
    )


def test_a_cached_404_for_companyfacts_costs_no_request_and_a_due_one_is_refetched(tmp_path):
    cache = tmp_path / "c"
    _put(
        cache,
        "submissions/CIK0000000201.json",
        json.dumps(_subs([("10-Q", "q-201", "d.htm", _old(), 1)])),
    )
    _put(cache, "companyfacts/CIK0000000201.json.404", "url")
    _put(cache, "forms/q-201/d.htm", "doc")
    (p,) = xv.plan_work([_item("GONE", "201")], cache_dir=cache, now=_now())
    cf = next(d for d in p.docs if d.kind == "companyfacts")
    assert cf.known_404 and not cf.live and p.live_requests == 0
    # a companyfacts copy older than a new XBRL filing is due again (the gate, judged on the cached index)
    _put(
        cache,
        "submissions/CIK0000000202.json",
        json.dumps(_subs([("10-Q", "q-202", "d.htm", _now() - timedelta(hours=2), 1)])),
    )
    _put(cache, "companyfacts/CIK0000000202.json", "{}", age_s=3 * 86400)
    _put(cache, "forms/q-202/d.htm", "doc")
    (p2,) = xv.plan_work([_item("NEWQ", "202")], cache_dir=cache, now=_now())
    assert [d.kind for d in p2.docs if d.live] == ["companyfacts"]


# --- --apply -----------------------------------------------------------------------------------------------------


class FakeClient:
    """Serves each CIK's submissions index; records every read; counts a 'live fetch' per read."""

    def __init__(self, cache_dir: Path, subs: dict[int, dict]) -> None:
        self.cache_dir = cache_dir
        self.subs = subs
        self.reads: list[str] = []
        self.live_fetches = 0

    def get_json(self, url: str, cache_key: str, **_: Any) -> dict:
        self.reads.append(cache_key)
        self.live_fetches += 1
        cik = int(cache_key.split("CIK")[1].split(".")[0])
        return self.subs.get(cik, {"filings": {"recent": {}}})

    def get_text(self, url: str, cache_key: str, **_: Any) -> str:  # pragma: no cover - faked away
        raise AssertionError(f"unexpected document read {cache_key}")


class FakeExtractor:
    """Stands in for ``extract_with_annual_fallback`` at the extraction module (no SEC request)."""

    def __init__(self, results: dict[int, ExtractionResult | BaseException]) -> None:
        self.results = results
        self.calls: list[int] = []
        self.on_call: Any = None

    def __call__(self, client, cik, *, values_only: bool = False, **_: Any) -> ExtractionResult:
        assert values_only is True
        self.calls.append(int(cik))
        if self.on_call is not None:
            self.on_call()
        out = self.results[int(cik)]
        if isinstance(out, BaseException):
            raise out
        return out


def _filing(db, sid: UUID, acc: str, accepted: datetime) -> None:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO fact_filing_index (tenant_id, security_id, form, accession, filed, primary_doc, "
            "accepted, source_ref, valid_from) VALUES (%s, %s, '10-Q', %s, %s, 'd.htm', %s, %s, %s)",
            (
                DATA_TENANT_ID,
                sid,
                acc,
                accepted.date(),
                accepted,
                f"https://www.sec.gov/Archives/edgar/data/1/{acc}-index.htm",
                accepted.date(),
            ),
        )


def _three(db, tmp_path, monkeypatch) -> tuple[dict[str, UUID], FakeClient, FakeExtractor]:
    """AAA (3 cards, an AUTO result), BBB (2 cards, nothing found), CCC (1 card, nothing found)."""
    ids = _world(db, [("AAA", "301"), ("BBB", "302"), ("CCC", "303")])
    subs = {}
    for t, cik in (("AAA", 301), ("BBB", 302), ("CCC", 303)):
        acc = f"000{cik}-26-000001"  # an EDGAR-shaped accession
        _filing(db, ids[t], acc, _old())
        subs[cik] = _subs([("10-Q", acc, "d.htm", _old(), 1)])
    db.commit()
    ref = "https://www.sec.gov/Archives/edgar/data/301/000301-26-000001/d.htm"
    period = market_today() - timedelta(days=75)
    auto = [
        ExtractedFact(
            fact_type="shares_outstanding",
            tier=Tier.AUTO,
            source="10-q-cover",
            source_ref=ref,
            event_date=period,
            value=30_000_000.0,
            note="Cover-page shares outstanding (single class).",
        ),
        ExtractedFact(
            fact_type="cash_burn",
            tier=Tier.AUTO,
            source="10-q",
            source_ref=ref,
            event_date=period,
            cash_usd=30_000_000.0,
            quarterly_burn_usd=3_000_000.0,
            note="Cash; operating cash flow.",
        ),
    ]
    fake = FakeExtractor(
        {
            301: ExtractionResult(facts=auto),
            302: ExtractionResult(facts=[]),
            303: ExtractionResult(facts=[]),
        }
    )
    monkeypatch.setattr(ex, "extract_with_annual_fallback", fake)
    return ids, FakeClient(tmp_path / "no-cache", subs), fake


def _apply(tmp_path, client, **kw) -> int:
    return xv.run(apply=True, live=True, runs_dir=tmp_path / "runs", client=client, **kw)


def _artifacts(tmp_path) -> list[dict]:
    d = tmp_path / "runs"
    if not d.exists():
        return []
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(d.glob("*.json"))]


def test_apply_is_ordered_bounded_and_resumable_and_a_rerun_writes_nothing(
    db, tmp_path, monkeypatch, capsys
):
    ids, client, fake = _three(db, tmp_path, monkeypatch)
    assert ex.backfill_applied(db) is False
    db.rollback()

    assert _apply(tmp_path, client, max_names=2) == 0
    out = capsys.readouterr().out
    # the work list's order: most cards first; the cap leaves the third due
    assert [(a["ticker"], a["run_kind"], a["status"]) for a in _attempts(db)] == [
        ("AAA", "backfill", "done"),
        ("BBB", "backfill", "done"),
    ]
    assert fake.calls == [301, 302]
    first = _counts(db)
    assert first["fact_shares_outstanding"] == 1 and first["fact_cash_burn"] == 1
    assert first["platform_extraction_attempt"] == 2
    (doc,) = _artifacts(tmp_path)
    assert doc["kind"] == "platform_extraction" and doc["run_kind"] == "backfill"
    assert doc["admin_line"].startswith("Shared facts")
    assert (doc["attempted"], doc["deferred"], doc["stopped_by"]) == (2, 1, "cap")
    assert doc["companies"]["applied"] == 1 and doc["companies"]["nothing_found"] == 1
    assert doc["mode"] == "live" and doc["backfill_applied_after"] is True
    assert "applied 1" in out and "candidates 1 (0 flagged to the steward, 1 nothing found)" in out
    assert "deferred 1 (cap)" in out and "live SEC fetches" in out
    assert "B4: this run recorded the FIRST backfill attempts" in out
    assert ex.backfill_applied(db) is True
    db.rollback()

    # the second run does only what is still due
    assert _apply(tmp_path, client) == 0
    assert fake.calls == [301, 302, 303]
    second = _counts(db)
    assert second["platform_extraction_attempt"] == 3
    assert "already unlocked before this run" in capsys.readouterr().out

    # a third run: nothing due, NOTHING written (count the tables), no artifact
    time.sleep(1.1)  # artifact names are per second: a third file would be visible
    assert _apply(tmp_path, client) == 0
    assert _counts(db) == second and fake.calls == [301, 302, 303]
    assert len(_artifacts(tmp_path)) == 2
    assert "nothing due" in capsys.readouterr().out
    assert ids


def test_ticker_scopes_the_run_and_an_unknown_ticker_refuses_it(db, tmp_path, monkeypatch, capsys):
    ids, client, fake = _three(db, tmp_path, monkeypatch)
    before = _counts(db)
    assert _apply(tmp_path, client, tickers=["NOPE", "bbb"]) == 2
    assert xv.run(apply=False, tickers=["NOPE"], cache_dir=tmp_path / "c", client=client) == 2
    assert "unknown ticker(s) NOPE" in capsys.readouterr().out
    assert _counts(db) == before and fake.calls == [] and not _artifacts(tmp_path)

    assert _apply(tmp_path, client, tickers=["bbb"]) == 0  # case-insensitive
    assert [a["ticker"] for a in _attempts(db)] == ["BBB"] and fake.calls == [302]
    assert _apply(tmp_path, client, tickers=["BBB"]) == 0  # finished: not due, nothing written
    assert "BBB is not due" in capsys.readouterr().out
    assert fake.calls == [302]
    assert ids


def test_apply_refuses_in_the_window_beside_another_backfill_and_while_a_pass_runs(
    db, tmp_path, monkeypatch, capsys
):
    ids, client, fake = _three(db, tmp_path, monkeypatch)
    before = _counts(db)
    # its own id: never the call-of-record lock (1), the gate (2), onboarding (3), editions (4), the master
    # refresh (5) or the term mentions (6)
    assert LOCK_ID_EXTRACTION == 7 and LOCK_ID_TERM_MENTIONS == 6
    held = acquire_job_lock(lock_id=LOCK_ID_EXTRACTION, holder="other", what="x")
    try:
        assert _apply(tmp_path, client) == 75
    finally:
        held.release()
    assert "already running" in capsys.readouterr().out
    lock = acquire_pass_lock(holder="cron")
    try:
        assert _apply(tmp_path, client) == 75
    finally:
        lock.release()
    assert "yields to it" in capsys.readouterr().out
    monkeypatch.setenv("ALPHADECK_ONBOARD_WINDOW_BEFORE_MIN", "60")
    monkeypatch.setenv("ALPHADECK_ONBOARD_WINDOW_AFTER_MIN", "60")
    get_settings.cache_clear()
    try:
        late = datetime(2026, 10, 23, 2, 15, tzinfo=timezone.utc)  # 22:15 ET
        assert _apply(tmp_path, client, now=lambda: late) == 75
        assert "inside the nightly window" in capsys.readouterr().out
        # a dry run is only a look: allowed in the window
        assert xv.run(apply=False, cache_dir=tmp_path / "c", client=client, now=lambda: late) == 0
    finally:
        get_settings.cache_clear()
    assert fake.calls == [] and client.reads == []
    assert _counts(db) == before and not _artifacts(tmp_path)
    assert ids


def test_apply_stops_between_companies_when_a_pass_starts_and_the_rest_stays_due(
    db, tmp_path, monkeypatch, capsys
):
    ids, client, fake = _three(db, tmp_path, monkeypatch)
    started: list = []

    def a_pass_starts() -> None:
        if not started:
            started.append(acquire_pass_lock(holder="cron"))

    fake.on_call = a_pass_starts
    try:
        assert _apply(tmp_path, client) == 75
    finally:
        for lk in started:
            lk.release()
    assert [a["ticker"] for a in _attempts(db)] == ["AAA"]
    (doc,) = _artifacts(tmp_path)
    assert (doc["stopped_by"], doc["deferred"]) == ("stop", 2)
    assert "call-of-record" in doc["stop_detail"]
    assert "STOPPED" in capsys.readouterr().out
    # resumable: the next run takes the two left
    time.sleep(1.1)
    assert _apply(tmp_path, client) == 0
    assert [a["ticker"] for a in _attempts(db)] == ["AAA", "BBB", "CCC"]
    assert ids


def _oklo_cache(cache: Path) -> None:
    """OKLO laid out as an EDGAR cache from the repo's fixtures: its index, companyfacts and newest 10-Q."""
    acc, doc = "0001628280-26-034095", "oklo-20260331.htm"
    subs = {
        "filings": {
            "recent": {
                "form": ["10-Q", "10-K"],
                "accessionNumber": [acc, "0001628280-26-018698"],
                "primaryDocument": [doc, "oklo-20251231.htm"],
                "filingDate": ["2026-05-12", "2026-03-17"],
                "reportDate": ["2026-03-31", "2025-12-31"],
                "acceptanceDateTime": ["2026-05-12T21:05:00.000Z", "2026-03-17T21:05:00.000Z"],
            }
        }
    }
    _put(cache, "submissions/CIK0001849056.json", json.dumps(subs))
    _put(
        cache,
        "companyfacts/CIK0001849056.json",
        (_FX / "CIK0001849056.json").read_text(encoding="utf-8"),
    )
    _put(cache, f"forms/{acc}/{doc}", (_FX / "OKLO-10q.txt").read_text(encoding="utf-8"))


def test_NO_LIVE_extracts_a_cached_company_and_records_not_cached_for_a_missing_one(
    db, tmp_path, monkeypatch, capsys
):
    """End to end through the REAL extractor and a REAL cache-only ``EdgarClient`` (built by the CLI)."""
    ids = _world(db, [("OKLO", "1849056"), ("MISS", "999")])
    _filing(
        db,
        ids["OKLO"],
        "0001628280-26-034095",
        datetime(2026, 5, 12, 21, 5, tzinfo=timezone.utc),
    )
    db.commit()
    cache = tmp_path / "edgar_cache"
    _oklo_cache(cache)
    fetched: list[str] = []

    def no_network(self, url: str) -> str:
        fetched.append(url)
        raise AssertionError(f"a cache-only run reached the network: {url}")

    from ingest.edgar import client as client_mod

    monkeypatch.setattr(client_mod.EdgarClient, "_fetch", no_network)

    rc = xv.run(apply=True, live=False, cache_dir=cache, runs_dir=tmp_path / "runs")
    out = capsys.readouterr().out

    assert rc == 0 and fetched == []
    got = {a["ticker"]: a for a in _attempts(db)}
    assert got["MISS"]["status"] == "error" and got["MISS"]["error"] == "not cached"
    assert got["OKLO"]["status"] == "done" and got["OKLO"]["shares_reason"] is not None
    db.rollback()
    rows: list[dict] = []
    with db.cursor() as cur:
        for t in ("fact_shares_outstanding", "fact_cash_burn", "extraction_candidate"):
            cur.execute(
                f"SELECT security_id, tenant_id FROM {t} WHERE security_id = ANY(%s)",  # noqa: S608
                ([ids["OKLO"], ids["MISS"]],),
            )
            rows += [dict(r) for r in cur.fetchall()]
    db.rollback()
    assert rows and {r["security_id"] for r in rows} == {ids["OKLO"]}  # MISS wrote nothing else
    assert {r["tenant_id"] for r in rows} == {DATA_TENANT_ID}  # the data tenant, never a workspace
    (doc,) = _artifacts(tmp_path)
    assert doc["mode"] == "cache_only" and doc["live_fetches"] == 0
    assert doc["errors_by_reason"] == {"not cached": 1}
    assert "failed 1 (not cached 1)" in out and "0 live SEC fetches" in out
    assert "a cache-only run counts" in out


def test_bad_arguments_are_refused_before_anything_runs():
    with pytest.raises(SystemExit) as e:
        xv.main(["--max-names", "0"])
    assert e.value.code == 2
    with pytest.raises(SystemExit) as e:
        xv.main(["--budget-s", "-1"])
    assert e.value.code == 2


def test_nothing_here_feeds_the_call():
    """The backfill is platform bookkeeping: no call, replay or editions module names it."""
    root = Path(__file__).resolve().parents[2]
    for pkg in ("calls", "signals", "editions", "backtest", "replay"):
        for p in (root / pkg).rglob("*.py"):
            assert "extract_values" not in p.read_text(encoding="utf-8"), p
