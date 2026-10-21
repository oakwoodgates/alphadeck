"""W1 — every fact writer serializes PER COMPANY (``db/write_lock.py``): the end-to-end proofs on the real test
Postgres, with the REAL ingest unit (``pipeline.ingest_thesis``) and only its two network seams faked.

The headline is the idempotency gate under CONCURRENCY: two writers ingesting the SAME company at once append
each fact exactly ONCE — asserted by COUNTING THE TABLE, because the as-of read dedups and a duplicate version
would hide behind a correct-looking read (the 2026-09-09 shape: two concurrent passes left 1,519 identical
version pairs ms apart on prod). A barrier inside the price append forces the interleaving deterministically
(both writers READ the stored tape before either APPENDS) — and the control test proves the barrier really
produces that race once the lock is taken away, so the headline cannot pass vacuously.

Then the nightly's side of the contract: it never blocks on (or fails because of) a company a Workbench ingest
holds — it DEFERS that company, ingests the rest, WAITS (bounded) and then ingests it; one still held after the
wait is reported (``lock_busy``), never an error, never a withheld call, never a dropped name (#9).
"""

from __future__ import annotations

import threading
import uuid
from datetime import date
from pathlib import Path

import pytest

from db.session import DEFAULT_TENANT_ID, connect, name_session
from db.write_lock import COMPANY_LOCK_NAMESPACE, acquire_company_lock, company_key
from domain.settings import get_settings
from ingest.prices import ingest_security as ingest_security_mod
from pipeline import cron_run_log, daily
from pipeline import ingest_thesis as IT
from pipeline.ingest_benchmarks import ingest_benchmarks
from pipeline.ingest_fundamentals import PassMemo, ingest_fundamentals_for_securities
from securities import master
from securities.benchmarks import seed_benchmarks

_XML = (Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)
_F4_PER_ACCESSION = 2  # form4_sample.xml carries two dated non-derivative txns
_BAR_DATES = (date(2026, 6, 8), date(2026, 6, 9))
_ASOF = date(2026, 6, 10)


# --- the two network seams, faked (the DB, the lock and every leg are real) ------------------------------


class _FakeClient:
    """Stands in for EdgarClient: the sample Form 4 as the REQUESTED company's own filing (its issuerCik is
    the CIK in the document URL, the shape R8-A requires)."""

    live_fetches = 0
    live_completed = 0  # O5 (#425) — the freeze detector's completed-fetch count, read by run_daily

    def __init__(self, **kwargs):
        pass

    def get_text(self, url: str, cache_key: str) -> str:
        cik = int(url.rsplit("/", 3)[1])
        return _XML.replace(
            "<issuerCik>0001234567</issuerCik>", f"<issuerCik>{cik:010d}</issuerCik>"
        )


def _subs(accessions=("ACC-1",)):
    rows = [("4", a, f"xslF345X05/{a}.xml", "2026-05-01", "") for a in accessions]
    return {
        "filings": {
            "recent": {
                "form": [r[0] for r in rows],
                "accessionNumber": [r[1] for r in rows],
                "primaryDocument": [r[2] for r in rows],
                "filingDate": [r[3] for r in rows],
                "items": [r[4] for r in rows],
            }
        }
    }


class _FakePrices:
    def get_bars(self, ticker, *, allow_live=False, force_refresh=False):
        return [
            {"d": d, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0, "volume": 100.0}
            for d in _BAR_DATES
        ]


def _offline(monkeypatch):
    monkeypatch.setattr(IT, "EdgarClient", _FakeClient)
    monkeypatch.setattr(IT, "fetch_submissions", lambda client, cik: _subs())
    monkeypatch.setattr(IT, "YahooPriceSource", lambda: _FakePrices())


@pytest.fixture
def lock_waits(monkeypatch):
    """Set the W1 waits (``ALPHADECK_*`` env + a settings cache clear) for one test."""

    def set_(**kw):
        for k, v in kw.items():
            monkeypatch.setenv(f"ALPHADECK_{k.upper()}", str(v))
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


@pytest.fixture
def held(db):
    """``held(sid, name)`` = another writer's session holding ``sid``'s company lock (closed after)."""
    opened = []

    def hold(sid: uuid.UUID, name: str = "alphadeck-writer:workbench-ingest"):
        c = connect()
        name_session(c, name)
        opened.append(c)
        return acquire_company_lock(c, sid, wait_s=0)

    yield hold
    for c in opened:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


# --- DB helpers -----------------------------------------------------------------------------------------


def _add_master(db, *, ticker, cik) -> uuid.UUID:
    sid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, cik, valid_from) "
            "VALUES (%s, %s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, cik, "2026-01-01"),
        )
    db.commit()
    return sid


def _thesis(db, members, name="Test thesis") -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
        for i, (ticker, sid) in enumerate(members):
            cur.execute(
                "INSERT INTO basket_member "
                "(id, tenant_id, thesis_id, ordinal, ticker, role, security_id) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (uuid.uuid4(), DEFAULT_TENANT_ID, tid, i, ticker, "—", sid),
            )
    db.commit()
    return tid


def _count(db, table: str, sid: uuid.UUID) -> int:
    db.rollback()  # a fresh snapshot — rows other sessions committed must be visible
    with db.cursor() as cur:
        cur.execute(
            f"SELECT count(*) AS n FROM {table} WHERE security_id = %s", (sid,)
        )  # noqa: S608
        n = cur.fetchone()["n"]
    db.rollback()
    return n


def _count_elsewhere(table: str, sid: uuid.UUID) -> int:
    """``_count`` on a connection of its own — for a snapshot taken WHILE the unit under test is using ``db``."""
    c = connect()
    try:
        return _count(c, table, sid)
    finally:
        c.close()


def _run_concurrently(*fns):
    errors: list[BaseException] = []

    def wrap(fn):
        def go():
            try:
                fn()
            except BaseException as e:  # noqa: BLE001 — surfaced by the assert below
                errors.append(e)

        return go

    threads = [threading.Thread(target=wrap(fn)) for fn in fns]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not any(t.is_alive() for t in threads), "a writer HUNG"
    assert not errors, errors


def _barrier_in_the_price_append(monkeypatch) -> threading.Barrier:
    """Make both writers READ the stored tape before EITHER appends: the price append waits (bounded) for
    the other writer to arrive. Under the lock the other writer never arrives (it is waiting for the
    company), the barrier times out, and the append proceeds — so the lock, not the barrier, decides.
    """
    barrier = threading.Barrier(2)
    real = ingest_security_mod.ingest_prices

    def gated(conn, security_id, rows, **kw):
        try:
            barrier.wait(timeout=3)
        except threading.BrokenBarrierError:
            pass
        return real(conn, security_id, rows, **kw)

    monkeypatch.setattr(ingest_security_mod, "ingest_prices", gated)
    return barrier


def _ingest_on_own_connection(
    tid, out: dict, *, name: str, start: threading.Barrier, lock_wait_s: float = 30
):
    """One writer on its own connection. ``start`` releases the writers TOGETHER once each has connected, so
    a slow connect under load can never serialize them by accident (they must genuinely overlap)."""

    def go():
        c = connect()
        name_session(c, name)
        start.wait(timeout=60)
        try:
            out[name] = IT.ingest_thesis(c, tid, allow_live=False, lock_wait_s=lock_wait_s)
        finally:
            c.close()

    return go


# --- 1. two concurrent writers, one company: each fact appended ONCE (count the table) --------------------


def test_two_CONCURRENT_ingests_of_the_SAME_company_append_each_fact_ONCE(db, monkeypatch):
    """The nightly's thesis and a Workbench promote's thesis both hold RACE and ingest it at the same moment.
    Count the TABLES: every bar and every insider row exactly once. Exactly one writer waited for the other.
    """
    _offline(monkeypatch)
    sid = _add_master(db, ticker="RACE", cik="0001234567")
    nightly_t = _thesis(db, [("RACE", sid)], "nightly's thesis")
    wb_t = _thesis(db, [("RACE", sid)], "workbench's thesis")
    _barrier_in_the_price_append(monkeypatch)
    start = threading.Barrier(2)
    out: dict = {}

    _run_concurrently(
        _ingest_on_own_connection(nightly_t, out, name="alphadeck-writer:daily-pass", start=start),
        _ingest_on_own_connection(wb_t, out, name="alphadeck-writer:workbench-ingest", start=start),
    )

    assert _count(db, "fact_price_eod", sid) == len(_BAR_DATES)  # NOT 2x — no duplicate version
    assert _count(db, "fact_insider_txn", sid) == _F4_PER_ACCESSION
    results = [r for rs in out.values() for r in rs]
    assert all(r.error is None and r.lock_busy is None for r in results)
    # one writer appended them all; the other found them stored
    assert sum(r.price_bars_appended for r in results) == len(_BAR_DATES)
    # they genuinely overlapped (released together): the second waited out the first's hold
    assert sorted(r.lock_waited_s > 0 for r in results) == [False, True]


def test_CONTROL_without_the_lock_the_same_race_DOES_duplicate(db, monkeypatch):
    """The control that keeps the headline honest: take the lock away (a no-op) and the very same two
    writers, interleaved by the very same barrier, append every bar TWICE — the 2026-09-09 shape. So the
    headline test above fails if the lock ever stops serializing; it is not passing by luck of timing.
    """

    class _NoLock:
        waited_s = 0.0

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

    _offline(monkeypatch)
    monkeypatch.setattr(IT, "acquire_company_lock", lambda conn, sid, **k: _NoLock())
    sid = _add_master(db, ticker="RACE", cik="0001234567")
    t1 = _thesis(db, [("RACE", sid)], "one")
    t2 = _thesis(db, [("RACE", sid)], "two")
    _barrier_in_the_price_append(monkeypatch)
    start = threading.Barrier(2)
    out: dict = {}

    _run_concurrently(
        _ingest_on_own_connection(t1, out, name="one", start=start),
        _ingest_on_own_connection(t2, out, name="two", start=start),
    )

    # the duplicate the lock prevents
    assert _count(db, "fact_price_eod", sid) == 2 * len(_BAR_DATES)


def test_opposite_basket_orders_never_DEADLOCK(db, monkeypatch):
    """Two writers walking the same three companies in OPPOSITE orders — the classic lock-order deadlock if
    a writer held one company while waiting for the next. It does not: ``ingest_thesis`` holds at most one
    company at a time, and every wait is a bounded poll. Both finish; every fact once."""
    _offline(monkeypatch)
    sids = [_add_master(db, ticker=f"T{i}", cik=f"000123456{i}") for i in range(3)]
    fwd = _thesis(db, [(f"T{i}", s) for i, s in enumerate(sids)], "forward")
    rev = _thesis(db, [(f"T{i}", s) for i, s in reversed(list(enumerate(sids)))], "reverse")
    start = threading.Barrier(2)
    out: dict = {}

    _run_concurrently(
        _ingest_on_own_connection(fwd, out, name="fwd", start=start),
        _ingest_on_own_connection(rev, out, name="rev", start=start),
    )

    for s in sids:
        assert _count(db, "fact_price_eod", s) == len(_BAR_DATES)
        assert _count(db, "fact_insider_txn", s) == _F4_PER_ACCESSION
    assert all(r.lock_busy is None for rs in out.values() for r in rs)


def test_every_leg_runs_holding_EXACTLY_its_own_company_lock(db, monkeypatch):
    """The structural half of "every fact writer takes it": each leg of the ingest unit (Form 4, 8-K, 13D/G,
    price, fund shares) runs while ITS session holds that company's lock — and no other company's (one
    company at a time is what makes the walk deadlock-free)."""
    _offline(monkeypatch)
    sids = [_add_master(db, ticker=f"L{i}", cik=f"000765432{i}") for i in range(2)]
    tid = _thesis(db, [(f"L{i}", s) for i, s in enumerate(sids)])
    seen: list[tuple[str, uuid.UUID, set[int]]] = []

    def held_by(conn) -> set[int]:
        rows = conn.execute(
            "SELECT objid FROM pg_locks WHERE locktype = 'advisory' AND granted "
            "AND pid = pg_backend_pid() AND classid = %s",
            (COMPANY_LOCK_NAMESPACE,),
        ).fetchall()
        return {int(r["objid"]) for r in rows}

    def probe(leg, fn):
        def wrapped(conn, *a, **k):
            sec = a[1] if leg in ("form4", "form8k", "schedule13") else a[0]
            seen.append((leg, sec.id, held_by(conn)))
            return fn(conn, *a, **k)

        return wrapped

    for leg, attr in (
        ("form4", "_form4_leg"),
        ("form8k", "_form8k_leg"),
        ("schedule13", "_schedule13_leg"),
        ("price", "ingest_bars_for_security"),
        ("fund_shares", "ingest_fund_shares_for_security"),
    ):
        monkeypatch.setattr(IT, attr, probe(leg, getattr(IT, attr)))

    IT.ingest_thesis(db, tid, allow_live=False)

    assert len(seen) == 5 * len(sids)
    for leg, sid, held in seen:
        assert held == {company_key(sid) & 0xFFFFFFFF}, (leg, sid, held)


# --- 2. the nightly vs a Workbench ingest holding a company ------------------------------------------------


class _ReleaseOnceWaited:
    """Spy on ``acquire_company_lock`` in the ingest unit: record every attempt, and when the unit starts its
    BOUNDED wait for ``sid`` (the deferred second pass), release the other writer's hold ``after_s`` later —
    so the wait is real and measured, never a race against the clock. ``at_wait`` snapshots what had already
    landed at that moment (the free companies, ingested first)."""

    def __init__(self, monkeypatch, sid, hold, *, after_s=0.5, snapshot=None):
        self.calls: list[tuple[uuid.UUID, float]] = []
        self.at_wait = None
        real = IT.acquire_company_lock

        def spy(conn, s, *, wait_s, **k):
            self.calls.append((s, wait_s))
            if s == sid and wait_s > 0 and self.at_wait is None:
                self.at_wait = snapshot() if snapshot else True
                threading.Timer(after_s, hold.release).start()
            return real(conn, s, wait_s=wait_s, **k)

        monkeypatch.setattr(IT, "acquire_company_lock", spy)


def test_the_nightly_DEFERS_a_held_company_ingests_the_rest_then_WAITS_and_ingests_it(
    db, monkeypatch, held
):
    _offline(monkeypatch)
    x = _add_master(db, ticker="HELD", cik="0001111111")
    y = _add_master(db, ticker="FREE", cik="0002222222")
    tid = _thesis(db, [("HELD", x), ("FREE", y)])  # the held company comes FIRST in basket order
    hold = held(x)  # a Workbench ingest is mid-company on HELD
    spy = _ReleaseOnceWaited(
        monkeypatch, x, hold, snapshot=lambda: _count_elsewhere("fact_price_eod", y)
    )

    results = IT.ingest_thesis(db, tid, allow_live=False, lock_wait_s=30)

    # tried HELD at once (busy -> deferred), did FREE, THEN waited for HELD
    assert [(s, w > 0) for s, w in spy.calls] == [(x, False), (y, False), (x, True)]
    assert spy.at_wait == len(_BAR_DATES)  # FREE's facts had already landed when the wait began
    by = {r.ticker: r for r in results}
    assert [r.ticker for r in results] == ["HELD", "FREE"]  # results keep basket order
    assert by["HELD"].lock_waited_s >= 0.4 and by["HELD"].lock_busy is None
    assert by["HELD"].error is None and by["HELD"].price_bars_appended == len(_BAR_DATES)
    assert by["FREE"].lock_waited_s == 0.0
    for s in (x, y):  # COUNT the tables
        assert _count(db, "fact_price_eod", s) == len(_BAR_DATES)
        assert _count(db, "fact_insider_txn", s) == _F4_PER_ACCESSION


def test_a_company_held_PAST_the_wait_is_reported_NOT_errored_and_NOT_dropped(
    db, monkeypatch, held
):
    _offline(monkeypatch)
    x = _add_master(db, ticker="HELD", cik="0001111111")
    y = _add_master(db, ticker="FREE", cik="0002222222")
    tid = _thesis(db, [("HELD", x), ("FREE", y)])
    held(x)  # held for the whole run

    results = IT.ingest_thesis(db, tid, allow_live=False, lock_wait_s=0.5)

    by = {r.ticker: r for r in results}
    assert set(by) == {"HELD", "FREE"}  # never dropped (#9)
    assert by["HELD"].error is None  # not an error ...
    assert by["HELD"].lock_busy and "alphadeck-writer:workbench-ingest" in by["HELD"].lock_busy
    assert by["HELD"].lock_waited_s >= 0.5
    assert (
        by["HELD"].form4_appended == 0 and by["HELD"].price_bars_appended == 0
    )  # ...no leg ran here
    assert _count(db, "fact_price_eod", x) == 0
    assert by["FREE"].price_bars_appended == len(_BAR_DATES)  # the free company was not held up


class _Quiet:
    def notify(self, event):  # pragma: no cover — a first-ever call has no prior, so no transition
        pass

    def notify_health(self, event):  # pragma: no cover
        pass

    def notify_spac_status(self, event):  # pragma: no cover
        pass


def _nightly(monkeypatch):
    """``run_daily`` (the nightly's per-thesis loop) with the REAL ingest unit and the real lock; only the
    network seams and the passenger fundamentals step are stubbed."""
    _offline(monkeypatch)
    monkeypatch.setattr(daily, "EdgarClient", _FakeClient)
    monkeypatch.setattr(daily, "_refresh_fundamentals", lambda *a, **k: [])


def _stamp(db, tid):
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT ingest_fresh, ingest_errors FROM calls WHERE thesis_id = %s ORDER BY seq DESC",
            (tid,),
        )
        rows = cur.fetchall()
    db.rollback()
    return rows


def test_the_NIGHTLY_waits_for_a_Workbench_held_company_then_ingests_it_and_RECORDS_fresh(
    db, monkeypatch, held, lock_waits
):
    _nightly(monkeypatch)
    lock_waits(ingest_lock_wait_s=30)
    x = _add_master(db, ticker="HELD", cik="0001111111")
    y = _add_master(db, ticker="FREE", cik="0002222222")
    tid = _thesis(db, [("HELD", x), ("FREE", y)], "Nightly thesis")
    hold = held(x)
    _ReleaseOnceWaited(monkeypatch, x, hold)

    [res] = daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())

    assert res.recorded is True and res.withheld_reason is None and res.error is None
    assert _stamp(db, tid) == [{"ingest_fresh": True, "ingest_errors": 0}]  # every name refreshed
    assert _count(db, "fact_price_eod", x) == len(_BAR_DATES)
    entry = cron_run_log.build_run_payload(
        [res], asof=_ASOF, allow_live=True, started_at=_now(), finished_at=_now()
    )["theses"][0]
    assert [(w["ticker"], w["ingested"]) for w in entry["company_lock_waits"]] == [("HELD", True)]
    assert entry["company_lock_waits"][0]["waited_s"] >= 0.4


def test_a_Workbench_hold_PAST_the_wait_never_withholds_or_fails_the_night(
    db, monkeypatch, held, lock_waits, capsys
):
    """The nightly must NEVER skip or fail a night because a Workbench ingest is running: a company held past
    the wait costs this pass that one company's refresh — the thesis still RECORDS, stamped
    ``ingest_fresh=false`` (it rests on a name this pass did not refresh) with ZERO ingest errors; the
    artifact names the holder; the report says it loudly. Even when the held company is the WHOLE basket.
    """
    _nightly(monkeypatch)
    lock_waits(ingest_lock_wait_s=0.5)
    x = _add_master(db, ticker="HELD", cik="0001111111")
    tid = _thesis(
        db, [("HELD", x)], "One-name thesis"
    )  # every name held: still not a "total failure"
    held(x)

    results = daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())

    [res] = results
    assert res.recorded is True and res.withheld_reason is None and res.error is None
    assert _stamp(db, tid) == [{"ingest_fresh": False, "ingest_errors": 0}]
    entry = cron_run_log.build_run_payload(
        results, asof=_ASOF, allow_live=True, started_at=_now(), finished_at=_now()
    )["theses"][0]
    assert entry["names_errored"] == 0 and entry["name_errors"] == []
    [w] = entry["company_lock_waits"]
    assert w["ingested"] is False and "alphadeck-writer:workbench-ingest" in w["holder"]
    daily._report(results)
    out = capsys.readouterr().out
    assert "COMPANY LOCK — 1 name(s) NOT ingested by this pass" in out and "HELD" in out


def _now():
    from datetime import datetime, timezone

    return datetime(2026, 6, 10, 22, 30, tzinfo=timezone.utc)


# --- 3. the Workbench on-promote job, the benchmarks leg, the fundamentals step -------------------------


def test_the_on_promote_job_reports_a_company_the_NIGHTLY_holds_visibly(
    db, monkeypatch, held, lock_waits
):
    from app.routers import workbench as wb
    from workbench.ingest_jobs import IngestJobError

    _offline(monkeypatch)
    lock_waits(ingest_lock_wait_s=0.5)
    x = _add_master(db, ticker="HELD", cik="0001111111")
    y = _add_master(db, ticker="FREE", cik="0002222222")
    tid = _thesis(db, [("HELD", x), ("FREE", y)])
    held(x, "alphadeck-writer:daily-pass")  # the nightly is mid-company on HELD

    with pytest.raises(IngestJobError) as exc:
        wb._run_member_ingest(tid, frozenset({x, y}))

    msg = str(exc.value)
    assert msg.startswith("ingested 1 of 2 new names")
    assert "not ingested here because another ingest was writing the same company" in msg
    assert "HELD" in msg and "alphadeck-writer:daily-pass" in msg
    assert _count(db, "fact_price_eod", y) == len(_BAR_DATES)  # the free name landed


def test_the_benchmarks_leg_reports_a_held_benchmark_and_refreshes_the_other(db, held, lock_waits):
    lock_waits(ingest_lock_wait_s=0.3)
    ids = seed_benchmarks(db)
    db.commit()
    held(ids["SPY"])

    by = {r.ticker: r for r in ingest_benchmarks(db, allow_live=False, source=_FakePrices())}

    assert by["SPY"].error and by["SPY"].error.startswith("not refreshed:")
    assert by["SPY"].bars_appended == 0 and _count(db, "fact_price_eod", ids["SPY"]) == 0
    assert by["IWM"].error is None and by["IWM"].bars_appended == len(_BAR_DATES)


def test_the_fundamentals_step_reports_a_held_company_and_lets_a_later_thesis_retry(
    db, held, lock_waits
):
    lock_waits(ingest_lock_wait_s=0.3)
    x = _add_master(db, ticker="HELD", cik="0001111111")
    sec = master.get(db, x, tenant_id=DEFAULT_TENANT_ID)
    held(x)
    memo = PassMemo()

    [r] = ingest_fundamentals_for_securities(
        db, [sec], client=_FakeClient(), tenant_id=DEFAULT_TENANT_ID, memo=memo
    )

    assert r.error and r.error.startswith("not refreshed:") and "workbench-ingest" in r.error
    assert (
        x not in memo.securities and not memo.companies
    )  # nothing cached: retried, not remembered


def test_the_nightly_fundamentals_step_skips_a_name_its_ingest_could_not_walk(db, monkeypatch):
    """A name the ingest reported ``lock_busy`` was not walked; the Q17 refresh right after must not wait out
    the same holder a second time — it skips it (the next pass refreshes it), and refreshes the rest.
    """
    from types import SimpleNamespace

    x = _add_master(db, ticker="HELD", cik="0001111111")
    y = _add_master(db, ticker="FREE", cik="0002222222")
    asked: list = []
    monkeypatch.setattr(
        "pipeline.ingest_fundamentals.ingest_fundamentals_for_securities",
        lambda conn, secs, **k: asked.extend(s.id for s in secs) or [],
    )
    names = [
        IT.NameResult("HELD", x, 0, 0, lock_busy="another writer holds this company"),
        IT.NameResult("FREE", y, 0, 0),
    ]

    daily._refresh_fundamentals(
        db, SimpleNamespace(tenant_id=DEFAULT_TENANT_ID), names, _FakeClient(), PassMemo()
    )

    assert asked == [y]


# --- 4. a repair refuses while a company writer is mid-company -------------------------------------------


def test_a_REPAIR_cli_exits_75_while_a_company_writer_is_mid_company(db, held, lock_waits, capsys):
    from pipeline import repair_impossible_txn_dates as repair

    lock_waits(repair_lock_wait_s=0.3)
    hold = held(uuid.uuid4())

    with pytest.raises(SystemExit) as exc:
        repair.main(["--apply"])
    assert exc.value.code == 75
    assert "fact writers" in capsys.readouterr().out
    hold.release()
    repair.main(["--apply"])  # drained -> it runs (nothing impossible stored: a no-op apply)
