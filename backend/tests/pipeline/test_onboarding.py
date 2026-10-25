"""X1 — the STAGED COLD-INGEST job (``pipeline/onboarding.py``), end to end on the real test Postgres with the REAL
ingest legs and only their network seams faked (the ``test_ingest_write_lock`` pattern).

What it proves, each by COUNTING THE TABLES (the as-of read dedups, so a duplicate would hide behind a correct
read):

- the queue: an enqueue is idempotent and never resets a done row; "cold" = no row and no other live basket;
- the job: a queued name is loaded leg by leg to ``done``; a CRASH mid-batch resumes at the next un-checkpointed
  leg and appends nothing twice; a history-leg failure re-queues, then fails after the attempt budget; a tail-leg
  failure never blocks ``done``; a held company is skipped, never waited for; a parked row is never loaded;
- the window: refused inside it, stopped on entering it between names, and the window math itself;
- the yield: refused while a pass holds the call-of-record lock, and stopped between legs when one starts;
- the budgets: names per run, the daily cap, one batch at a time;
- the nightly: a not-yet-warm name is walked with its per-filing legs WINDOWED (bounded), listed as onboarding,
  its thesis stamped ``ingest_fresh=false`` — never dropped (#9); once the job finishes it, the next nightly walks
  it in full and records fresh.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID, connect, name_session
from db.write_lock import acquire_company_lock
from domain.settings import get_settings
from ingest.edgar.submissions import filed_on_or_after
from pipeline import cron_run_log, daily, onboarding
from pipeline import ingest_thesis as IT
from pipeline.ingest_fundamentals import NameResult as FundResult
from pipeline.run_lock import acquire_pass_lock

_NY = ZoneInfo("America/New_York")
_XML = (Path(__file__).resolve().parents[1] / "fixtures" / "edgar" / "form4_sample.xml").read_text(
    encoding="utf-8"
)
_F4_PER_ACCESSION = 2  # form4_sample.xml carries two dated non-derivative txns
_BAR_DATES = (date(2026, 6, 8), date(2026, 6, 9))
_ASOF = date(2026, 6, 10)
_NOON = datetime(2026, 6, 10, 12, 0, tzinfo=_NY)  # well outside the nightly window
# two Form 4s per company: one inside the nightly's 30-day onboarding window, one long before it
_FILINGS = (("ACC-NEW", "2026-06-05"), ("ACC-OLD", "2026-01-02"))


# --- the network seams, faked (the DB, the locks and every leg are real) ---------------------------------


class _FakeClient:
    """Stands in for EdgarClient: the sample Form 4 as the REQUESTED company's own filing."""

    live_fetches = 0
    live_completed = 0
    allow_live = False
    cache_dir = Path(".")

    def __init__(self, **kwargs):
        pass

    def get_text(self, url: str, cache_key: str) -> str:
        cik = int(url.rsplit("/", 3)[1])
        return _XML.replace(
            "<issuerCik>0001234567</issuerCik>", f"<issuerCik>{cik:010d}</issuerCik>"
        )


def _subs(filings=_FILINGS):
    rows = [("4", a, f"xslF345X05/{a}.xml", filed, "") for a, filed in filings]
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


@pytest.fixture
def offline(monkeypatch):
    """Fake the network under every leg; the fundamentals step answers "nothing new" (a passenger here)."""
    monkeypatch.setattr(IT, "EdgarClient", _FakeClient)
    monkeypatch.setattr(IT, "fetch_submissions", lambda client, cik: _subs())
    monkeypatch.setattr(IT, "YahooPriceSource", lambda: _FakePrices())
    monkeypatch.setattr(
        onboarding,
        "ingest_fundamentals_for_securities",
        lambda conn, secs, **k: [FundResult(s.ticker, s.id) for s in secs],
    )


@pytest.fixture
def settings_env(monkeypatch):
    """Set ``ALPHADECK_*`` settings for one test (env + a settings-cache clear)."""

    def set_(**kw):
        for k, v in kw.items():
            monkeypatch.setenv(f"ALPHADECK_{k.upper()}", str(v))
        get_settings.cache_clear()

    yield set_
    get_settings.cache_clear()


@pytest.fixture
def other_session():
    """``other_session(name)`` = a second connection (closed after) — another writer or a running pass."""
    opened = []

    def make(name: str = "alphadeck-writer:workbench-ingest"):
        c = connect()
        name_session(c, name)
        opened.append(c)
        return c

    yield make
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


def _thesis(db, members, name="Test thesis", *, archived=False) -> uuid.UUID:
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative, archived_at) "
            "VALUES (%s, %s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n", datetime.now(_NY) if archived else None),
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


def _count(db, table: str, sid: uuid.UUID, accession: str | None = None) -> int:
    db.rollback()  # a fresh snapshot — rows other sessions committed must be visible
    with db.cursor() as cur:
        if accession is None:
            cur.execute(
                f"SELECT count(*) AS n FROM {table} WHERE security_id = %s", (sid,)
            )  # noqa: S608
        else:
            cur.execute(
                f"SELECT count(*) AS n FROM {table} WHERE security_id = %s AND accession = %s",  # noqa: S608
                (sid, accession),
            )
        n = cur.fetchone()["n"]
    db.rollback()
    return n


def _queue_rows(db) -> int:
    db.rollback()
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_onboarding")
        n = cur.fetchone()["n"]
    db.rollback()
    return n


def _row(db, sid) -> onboarding.OnboardingRow:
    db.rollback()
    [r] = onboarding.rows(db, security_ids=[sid])
    db.rollback()
    return r


def _queue(db, *sids, source="promote", thesis_id=None):
    onboarding.enqueue(db, sids, tenant_id=DEFAULT_TENANT_ID, source=source, thesis_id=thesis_id)
    db.commit()


def _batch(**kw) -> onboarding.BatchOutcome:
    kw.setdefault("now", lambda: _NOON)
    kw.setdefault("edgar_client", _FakeClient())
    kw.setdefault("price_source", _FakePrices())
    kw.setdefault(
        "split_source", None
    )  # the split-history fill: off here (tests/pipeline/test_split_history.py)
    return onboarding.run_batch(**kw)


# --- 1. the queue --------------------------------------------------------------------------------------------


def test_enqueue_is_idempotent_and_never_resets_a_done_row(db):
    a = _add_master(db, ticker="AAA", cik="0001000001")
    b = _add_master(db, ticker="BBB", cik="0001000002")

    first = onboarding.enqueue(db, [a, b], tenant_id=DEFAULT_TENANT_ID, source="promote")
    db.commit()
    again = onboarding.enqueue(db, [a, b, a], tenant_id=DEFAULT_TENANT_ID, source="cli")
    db.commit()

    assert first == {a, b} and again == set()
    assert _queue_rows(db) == 2  # COUNT THE TABLE: one row per security, whoever asks again
    db.execute("UPDATE security_onboarding SET status = 'done' WHERE security_id = %s", (a,))
    db.commit()
    onboarding.enqueue(db, [a], tenant_id=DEFAULT_TENANT_ID, source="promote")
    db.commit()
    assert _row(db, a).status == "done" and _queue_rows(db) == 2


def test_cold_means_no_row_and_no_OTHER_live_basket(db):
    """The promote's lane input: a name another LIVE basket holds was loaded by the nightly (legacy-warm); an
    archived basket is not walked, so it does not count; a done row is warm; a pending row is cold (queued).
    """
    fresh = _add_master(db, ticker="NEW", cik="0001000001")
    elsewhere = _add_master(db, ticker="ELSE", cik="0001000002")
    archived_only = _add_master(db, ticker="ARCH", cik="0001000003")
    done = _add_master(db, ticker="DONE", cik="0001000004")
    pending = _add_master(db, ticker="PEND", cik="0001000005")
    _thesis(db, [("ELSE", elsewhere)], "another live thesis")
    _thesis(db, [("ARCH", archived_only)], "an archived thesis", archived=True)
    promoting = _thesis(
        db,
        [(t, s) for t, s in (("NEW", fresh), ("ELSE", elsewhere), ("ARCH", archived_only))],
        "the promoting thesis",
    )
    _queue(db, done, pending)
    db.execute("UPDATE security_onboarding SET status = 'done' WHERE security_id = %s", (done,))
    db.commit()

    cold = onboarding.cold_ids(
        db,
        [fresh, elsewhere, archived_only, done, pending],
        tenant_id=DEFAULT_TENANT_ID,
        thesis_id=promoting,
    )

    assert cold == {fresh, archived_only, pending}


# --- 2. the job ----------------------------------------------------------------------------------------------


def test_a_queued_name_is_loaded_leg_by_leg_to_DONE(db, offline):
    a = _add_master(db, ticker="AAA", cik="0001000001")
    _thesis(db, [("AAA", a)])
    _queue(db, a)

    out = _batch()

    assert not out.refused and out.stopped == "done"
    assert [(n.ticker, n.outcome) for n in out.names] == [("AAA", "done")]
    row = _row(db, a)
    assert row.status == "done" and set(row.legs_done) == set(onboarding.LEGS)
    assert row.attempts == 0 and row.last_error is None and row.finished_at is not None
    # COUNT THE TABLES: the WHOLE history — both Form 4s, every bar
    assert _count(db, "fact_insider_txn", a) == len(_FILINGS) * _F4_PER_ACCESSION
    assert _count(db, "fact_price_eod", a) == len(_BAR_DATES)
    # its own artifact, pass_kind "onboard" — never in the nightly's cron_runs
    assert out.log_path is not None and out.log_path.parent.name == "onboard_runs_home"
    doc = onboarding.list_runs(runs_dir=out.log_path.parent)[0]
    assert doc["pass_kind"] == "onboard" and doc["status"] == "finished"
    assert doc["counts"] == {"done": 1} and doc["timings"]["theses"][0]["name"] == "AAA"
    # a second run finds nothing to do and writes nothing
    again = _batch()
    assert again.refused and again.stopped == "nothing_queued"
    assert len(onboarding.list_runs(runs_dir=out.log_path.parent)) == 1


class _Crash(BaseException):
    """A process death mid-leg (not an ordinary leg failure, which the job captures)."""


def test_a_CRASH_mid_batch_RESUMES_at_the_next_leg_and_appends_nothing_twice(
    db, offline, monkeypatch
):
    a = _add_master(db, ticker="AAA", cik="0001000001")
    b = _add_master(db, ticker="BBB", cik="0001000002")
    _thesis(db, [("AAA", a), ("BBB", b)])
    _queue(db, a)
    _queue(db, b)
    real_s13 = IT._schedule13_leg
    form4_calls: list[uuid.UUID] = []
    real_f4 = IT._form4_leg

    def f4_spy(conn, client, sec, **k):
        form4_calls.append(sec.id)
        return real_f4(conn, client, sec, **k)

    def crash_once(conn, client, sec, **k):
        raise _Crash("the VM went down")

    monkeypatch.setattr(IT, "_form4_leg", f4_spy)
    monkeypatch.setattr(IT, "_schedule13_leg", crash_once)

    with pytest.raises(_Crash):
        _batch()

    crashed = _row(db, a)
    assert crashed.status == "in_progress" and crashed.legs_done == ("form4", "form8k")
    assert _row(db, b).status == "queued"  # never reached
    f4_after_crash = _count(db, "fact_insider_txn", a)
    assert f4_after_crash == len(_FILINGS) * _F4_PER_ACCESSION
    runs = onboarding.list_runs(runs_dir=onboarding._DEFAULT_RUNS_DIR)
    assert runs[0]["status"] == "crashed"

    monkeypatch.setattr(IT, "_schedule13_leg", real_s13)
    out = _batch()

    assert [(n.ticker, n.outcome) for n in out.names] == [("AAA", "done"), ("BBB", "done")]
    # the resumed name ran ONLY the legs it had not finished: its Form 4 leg did not run again
    assert out.names[0].legs_run == ["schedule13", "price", "fund_shares", "fundamentals"]
    assert form4_calls == [a, b]
    # COUNT THE TABLES: nothing appended twice
    assert _count(db, "fact_insider_txn", a) == f4_after_crash
    assert _count(db, "fact_price_eod", a) == len(_BAR_DATES)
    assert _count(db, "fact_insider_txn", b) == len(_FILINGS) * _F4_PER_ACCESSION
    assert _row(db, a).status == _row(db, b).status == "done"
    assert _queue_rows(db) == 2


def test_a_history_leg_failure_REQUEUES_then_FAILS_after_the_attempt_budget_and_Retry_requeues(
    db, offline, monkeypatch, settings_env
):
    settings_env(onboard_max_attempts=2)
    a = _add_master(db, ticker="AAA", cik="0001000001")
    _thesis(db, [("AAA", a)])
    _queue(db, a)

    def boom(conn, client, sec, **k):
        raise RuntimeError("EDGAR said 503")

    monkeypatch.setattr(IT, "_form8k_leg", boom)

    first = _batch()
    assert first.names[0].outcome == "requeued"
    row = _row(db, a)
    assert (
        row.status == "queued" and row.attempts == 1 and "form8k: EDGAR said 503" in row.last_error
    )
    assert (
        "form8k" not in row.legs_done and "form4" in row.legs_done
    )  # the checkpoint kept the rest

    second = _batch()
    assert second.names[0].outcome == "failed"
    assert second.names[0].legs_run == ["form8k"]  # only the failing leg re-ran
    row = _row(db, a)
    assert row.status == "failed" and row.attempts == 2
    assert _batch().stopped == "nothing_queued"  # a failed name waits for the operator

    assert onboarding.retry(db, a, tenant_id=DEFAULT_TENANT_ID)
    db.commit()
    row = _row(db, a)
    assert row.status == "queued" and row.attempts == 0 and "form4" in row.legs_done


def test_a_TAIL_leg_failure_never_blocks_done(db, offline, monkeypatch):
    a = _add_master(db, ticker="AAA", cik="0001000001")
    _thesis(db, [("AAA", a)])
    _queue(db, a)

    class _Dead:
        def get_bars(self, *a, **k):
            raise RuntimeError("404 Not Found")

    out = _batch(price_source=_Dead())

    assert out.names[0].outcome == "done" and set(out.names[0].legs_failed) == {"price"}
    row = _row(db, a)
    assert row.status == "done" and "price" not in row.legs_done
    assert row.last_error.startswith("done; tail leg(s) failed") and "404" in row.last_error


def test_a_company_another_writer_holds_is_SKIPPED_not_waited_for(db, offline, other_session):
    a = _add_master(db, ticker="HELD", cik="0001000001")
    b = _add_master(db, ticker="FREE", cik="0001000002")
    _thesis(db, [("HELD", a), ("FREE", b)])
    _queue(db, a)
    _queue(db, b)
    acquire_company_lock(other_session(), a, wait_s=0)

    out = _batch()

    assert [(n.ticker, n.outcome) for n in out.names] == [("HELD", "busy"), ("FREE", "done")]
    assert "workbench-ingest" in out.names[0].stop_reason
    assert _row(db, a).status == "queued" and _count(db, "fact_price_eod", a) == 0


def test_a_PARKED_row_no_live_basket_holds_is_never_loaded(db, offline):
    """The cost thread: a name the operator removed (or whose thesis was archived) is not loaded for nothing;
    re-adding it resumes it."""
    a = _add_master(db, ticker="GONE", cik="0001000001")
    _thesis(db, [("GONE", a)], "archived", archived=True)
    _queue(db, a)

    out = _batch()

    assert out.refused and out.stopped == "nothing_queued"
    r = onboarding.readout(db, tenant_id=DEFAULT_TENANT_ID)
    assert r.counts["parked"] == 1 and r.counts["queued"] == 1
    _thesis(db, [("GONE", a)], "re-added")
    assert [n.outcome for n in _batch().names] == ["done"]


# --- 3. the window -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "hh, mm, inside",
    [
        (21, 29, False),
        (21, 30, True),
        (22, 30, True),
        (23, 29, True),
        (23, 30, False),
        (12, 0, False),
    ],
)
def test_the_window_is_an_hour_either_side_of_RUN_AT(hh, mm, inside):
    now = datetime(2026, 6, 10, hh, mm, tzinfo=_NY)
    assert (
        onboarding.in_nightly_window(now, run_at=time(22, 30), before_min=60, after_min=60)
        is inside
    )


def test_a_window_crossing_MIDNIGHT_is_found_from_either_side():
    run_at = time(23, 30)
    after_midnight = datetime(2026, 6, 11, 0, 15, tzinfo=_NY)
    before_midnight = datetime(2026, 6, 10, 22, 45, tzinfo=_NY)
    assert onboarding.in_nightly_window(after_midnight, run_at=run_at, before_min=60, after_min=60)
    assert onboarding.in_nightly_window(before_midnight, run_at=run_at, before_min=60, after_min=60)
    assert not onboarding.in_nightly_window(
        datetime(2026, 6, 11, 0, 31, tzinfo=_NY), run_at=run_at, before_min=60, after_min=60
    )
    assert not onboarding.in_nightly_window(
        after_midnight, run_at=run_at, before_min=0, after_min=0
    )  # 0/0 = no window


def test_the_job_REFUSES_to_start_inside_the_window(db, offline, settings_env):
    settings_env(onboard_window_before_min=60, onboard_window_after_min=60)
    a = _add_master(db, ticker="AAA", cik="0001000001")
    _thesis(db, [("AAA", a)])
    _queue(db, a)

    out = _batch(now=lambda: datetime(2026, 6, 10, 22, 0, tzinfo=_NY))

    assert out.refused and out.stopped == "window" and "21:30-23:30" in out.detail
    assert _row(db, a).status == "queued" and _count(db, "fact_price_eod", a) == 0
    assert (
        onboarding.list_runs(runs_dir=onboarding._DEFAULT_RUNS_DIR) == []
    )  # a refusal writes nothing
    assert [
        n.outcome for n in _batch(now=lambda: datetime(2026, 6, 10, 21, 29, tzinfo=_NY)).names
    ] == ["done"]


def test_the_job_STOPS_on_entering_the_window_between_names(db, offline, settings_env, monkeypatch):
    settings_env(onboard_window_before_min=60, onboard_window_after_min=60)
    a = _add_master(db, ticker="AAA", cik="0001000001")
    b = _add_master(db, ticker="BBB", cik="0001000002")
    _thesis(db, [("AAA", a), ("BBB", b)])
    _queue(db, a)
    _queue(db, b)
    clock = {"now": datetime(2026, 6, 10, 21, 20, tzinfo=_NY)}

    def advancing():
        return clock["now"]

    real = IT._form8k_leg

    def form8k_then_tick(conn, client, sec, **k):
        if sec.id == a:
            clock["now"] = datetime(
                2026, 6, 10, 21, 31, tzinfo=_NY
            )  # the clock crosses 21:30 mid-name
        return real(conn, client, sec, **k)

    monkeypatch.setattr(IT, "_form8k_leg", form8k_then_tick)
    out = _batch(now=advancing)

    # AAA stopped between legs (its checkpoint kept, resumable — not a failure); BBB never started
    assert [(n.ticker, n.outcome) for n in out.names] == [("AAA", "yielded")]
    assert out.stopped == "window"
    row = _row(db, a)
    assert (
        row.status == "in_progress" and row.legs_done == ("form4", "form8k") and row.attempts == 0
    )
    assert _row(db, b).status == "queued"


# --- 4. the yield to a running pass --------------------------------------------------------------------------


def test_the_job_REFUSES_while_a_pass_holds_the_call_of_record_lock(db, offline):
    a = _add_master(db, ticker="AAA", cik="0001000001")
    _thesis(db, [("AAA", a)])
    _queue(db, a)
    lock = acquire_pass_lock(holder="cron")
    try:
        out = _batch()
    finally:
        lock.release()

    assert out.refused and out.stopped == "pass_running" and "alphadeck-pass:cron" in out.detail
    assert _row(db, a).status == "queued"


def test_the_job_YIELDS_between_legs_when_a_pass_starts_and_never_takes_its_lock(
    db, offline, monkeypatch
):
    """A pass that starts mid-batch (an Admin "Run daily now", a long night's retry): the job stops at the next
    leg boundary — the pass can take its lock at once, because the job only READ pg_locks — and resumes later.
    """
    a = _add_master(db, ticker="AAA", cik="0001000001")
    b = _add_master(db, ticker="BBB", cik="0001000002")
    _thesis(db, [("AAA", a), ("BBB", b)])
    _queue(db, a)
    _queue(db, b)
    held: list = []
    real = IT._form8k_leg

    def form8k_then_pass_starts(conn, client, sec, **k):
        if not held:
            held.append(
                acquire_pass_lock(holder="manual")
            )  # never refused: the job does not hold it
        return real(conn, client, sec, **k)

    monkeypatch.setattr(IT, "_form8k_leg", form8k_then_pass_starts)
    try:
        out = _batch()
    finally:
        for lk in held:
            lk.release()

    assert [(n.ticker, n.outcome) for n in out.names] == [("AAA", "yielded")]
    assert out.stopped == "pass_running" and "alphadeck-pass:manual" in out.detail
    assert _row(db, a).legs_done == ("form4", "form8k") and _row(db, b).status == "queued"
    monkeypatch.setattr(IT, "_form8k_leg", real)
    assert [n.outcome for n in _batch().names] == ["done", "done"]  # resumed after the pass


# --- 5. the budgets ------------------------------------------------------------------------------------------


def test_the_names_budget_and_the_DAILY_cap_end_a_batch(db, offline):
    sids = [_add_master(db, ticker=f"N{i}", cik=f"000100000{i}") for i in range(3)]
    _thesis(db, [(f"N{i}", s) for i, s in enumerate(sids)])
    for s in sids:
        _queue(db, s)

    one = _batch(max_names=1)
    assert [n.outcome for n in one.names] == ["done"] and one.stopped == "budget_names"

    capped = _batch(daily_max=2)  # one name already attempted "today" -> room for one more
    assert [n.outcome for n in capped.names] == ["done"] and capped.stopped == "daily_cap"
    refused = _batch(daily_max=2)
    assert refused.refused and refused.stopped == "daily_cap"
    assert _row(db, sids[2]).status == "queued"


def test_ONE_batch_at_a_time(db, offline, other_session):
    from pipeline.run_lock import LOCK_NAMESPACE

    a = _add_master(db, ticker="AAA", cik="0001000001")
    _thesis(db, [("AAA", a)])
    _queue(db, a)
    other = other_session("alphadeck-writer:onboard")
    other.execute("SELECT pg_advisory_lock(%s, %s)", (LOCK_NAMESPACE, onboarding.ONBOARD_LOCK_ID))
    other.commit()

    out = _batch()

    assert out.refused and out.stopped == "already_running"
    assert _row(db, a).status == "queued"


# --- 6. the nightly's treatment of a not-yet-warm name -------------------------------------------------------


class _Quiet:
    def notify(self, event):  # pragma: no cover
        pass

    def notify_health(self, event):  # pragma: no cover
        pass

    def notify_spac_status(self, event):  # pragma: no cover
        pass


def _stamp(db, tid):
    db.rollback()
    with db.cursor() as cur:
        cur.execute(
            "SELECT ingest_fresh, ingest_errors FROM calls WHERE thesis_id = %s ORDER BY seq",
            (tid,),
        )
        rows = cur.fetchall()
    db.rollback()
    return rows


def _nightly(monkeypatch):
    monkeypatch.setattr(daily, "EdgarClient", _FakeClient)
    monkeypatch.setattr(daily, "_refresh_fundamentals", lambda *a, **k: [])


def _at(y, m, d, hh=22, mm=40):
    return datetime(y, m, d, hh, mm, tzinfo=_NY)


def test_the_NIGHTLY_walks_a_not_yet_warm_name_WINDOWED_lists_it_and_records_not_fresh(
    db, offline, monkeypatch, capsys
):
    _nightly(monkeypatch)
    warm = _add_master(db, ticker="WARM", cik="0001000001")
    cold = _add_master(db, ticker="COLD", cik="0001000002")
    tid = _thesis(db, [("WARM", warm), ("COLD", cold)], "Nightly thesis")
    _queue(db, cold, thesis_id=tid)

    [res] = daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())

    by = {r.ticker: r for r in res.ingested}
    assert set(by) == {"WARM", "COLD"}  # never dropped (#9)
    assert by["COLD"].onboarding == "queued"
    assert by["COLD"].filed_since == _ASOF - timedelta(days=30)
    assert by["WARM"].onboarding is None and by["WARM"].filed_since is None
    # BOUNDED: the onboarding name read only the Form 4 filed inside the window ...
    assert _count(db, "fact_insider_txn", cold, "ACC-NEW") == _F4_PER_ACCESSION
    assert _count(db, "fact_insider_txn", cold, "ACC-OLD") == 0
    # ... while its price leg (one request cold or warm) ran in full, and the warm name walked everything
    assert _count(db, "fact_price_eod", cold) == len(_BAR_DATES)
    assert _count(db, "fact_insider_txn", warm) == len(_FILINGS) * _F4_PER_ACCESSION
    # the record: still recorded, stamped not-fresh, zero errors (onboarding is not a failure)
    assert res.recorded is True and res.error is None and res.withheld_reason is None
    assert _stamp(db, tid) == [{"ingest_fresh": False, "ingest_errors": 0}]
    entry = cron_run_log.build_run_payload(
        [res],
        asof=_ASOF,
        allow_live=True,
        started_at=_at(2026, 6, 10),
        finished_at=_at(2026, 6, 10),
    )
    assert entry["pass_kind"] == "nightly"
    assert entry["theses"][0]["onboarding"] == [
        {
            "security_id": str(cold),
            "ticker": "COLD",
            "status": "queued",
            "filed_since": "2026-05-11",
            "deferred": False,
        }
    ]
    daily._report([res])
    out = capsys.readouterr().out
    assert "1 name(s) still onboarding" in out and "COLD" in out and "2026-05-11" in out

    # the job then loads the rest of COLD's history outside the window — the older Form 4, once
    assert [n.outcome for n in _batch().names] == ["done"]
    assert _count(db, "fact_insider_txn", cold, "ACC-OLD") == _F4_PER_ACCESSION
    assert _count(db, "fact_insider_txn", cold, "ACC-NEW") == _F4_PER_ACCESSION  # not twice

    # ... and the next nightly walks it in full and judges the thesis FRESH, with nothing appended twice
    # (spied at the write: record_if_changed appends a row only when the CARD changed, so the stamp the pass
    # computed is read where it is passed, never inferred from whether a row landed)
    stamps: list[bool] = []
    real_record = daily.calls_repo.record_if_changed

    def spy(conn, card, tenant_id, **kw):
        stamps.append(kw["ingest_fresh"])
        return real_record(conn, card, tenant_id, **kw)

    monkeypatch.setattr(daily.calls_repo, "record_if_changed", spy)
    [res2] = daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())
    assert all(r.onboarding is None and r.filed_since is None for r in res2.ingested)
    assert _count(db, "fact_insider_txn", cold) == len(_FILINGS) * _F4_PER_ACCESSION
    assert stamps == [True]


def test_the_pass_is_BOUNDED_past_its_onboarding_cap_the_rest_DEFERRED_listed_not_dropped(
    db, offline, monkeypatch, settings_env, capsys
):
    """However many names a promote queued, at most ``onboard_nightly_max_names`` DISTINCT ones get the windowed
    walk per pass; the rest are listed as onboarding but not walked at all (no fetch, no write) — and never page
    as a stopped tape (no price leg ran). A name met again in another thesis keeps its first treatment.
    """
    settings_env(onboard_nightly_max_names=1)
    _nightly(monkeypatch)
    a = _add_master(db, ticker="AAA", cik="0001000001")
    b = _add_master(db, ticker="BBB", cik="0001000002")
    t1 = _thesis(db, [("AAA", a), ("BBB", b)], "1 first thesis")
    t2 = _thesis(db, [("BBB", b), ("AAA", a)], "2 second thesis")  # BBB first here: still deferred
    _queue(db, a, b)

    results = daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())

    by_thesis = {r.thesis_id: {x.ticker: x for x in r.ingested} for r in results}
    for tid in (t1, t2):
        names = by_thesis[tid]
        assert set(names) == {"AAA", "BBB"}  # never dropped (#9)
        assert names["AAA"].onboarding == "queued" and not names["AAA"].onboarding_deferred
        assert names["BBB"].onboarding == "queued" and names["BBB"].onboarding_deferred
        assert names["BBB"].filed_since is None and names["BBB"].price_bars_appended == 0
    # COUNT THE TABLES: the deferred name was not touched at all; the walked one was, once
    assert _count(db, "fact_price_eod", b) == 0 and _count(db, "fact_insider_txn", b) == 0
    assert _count(db, "fact_price_eod", a) == len(_BAR_DATES)
    assert _count(db, "fact_insider_txn", a, "ACC-NEW") == _F4_PER_ACCESSION
    # the deferred name never reads as a stopped / never-priced tape
    assert all(s.security_id != b for r in results for s in r.tape_stale)
    assert _stamp(db, t1) == _stamp(db, t2) == [{"ingest_fresh": False, "ingest_errors": 0}]
    entry = cron_run_log.build_run_payload(
        results,
        asof=_ASOF,
        allow_live=True,
        started_at=_at(2026, 6, 10),
        finished_at=_at(2026, 6, 10),
    )
    rows = {r["ticker"]: r for r in entry["theses"][0]["onboarding"]}
    assert rows["BBB"]["deferred"] is True and rows["BBB"]["filed_since"] is None
    assert rows["AAA"]["deferred"] is False
    daily._report(results)
    out = capsys.readouterr().out
    assert "2 name(s) still onboarding" in out and "BBB (deferred)" in out
    assert "1 past tonight's onboarding cap were not ingested at all" in out


def test_a_night_with_NO_onboarding_name_is_unchanged(db, offline, monkeypatch, capsys):
    _nightly(monkeypatch)
    warm = _add_master(db, ticker="WARM", cik="0001000001")
    tid = _thesis(db, [("WARM", warm)])

    [res] = daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())

    assert _stamp(db, tid) == [{"ingest_fresh": True, "ingest_errors": 0}]
    daily._report([res])
    assert "onboarding" not in capsys.readouterr().out


def test_the_nightly_WINDOW_is_the_setting(db, offline, monkeypatch, settings_env):
    """0 days = only tonight's filings; a wide window reads the old one too."""
    _nightly(monkeypatch)
    cold = _add_master(db, ticker="COLD", cik="0001000002")
    _thesis(db, [("COLD", cold)])
    _queue(db, cold)

    settings_env(onboard_nightly_window_days=0)
    daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())
    assert _count(db, "fact_insider_txn", cold) == 0  # nothing was filed ON the as-of

    settings_env(onboard_nightly_window_days=400)
    daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())
    assert _count(db, "fact_insider_txn", cold) == len(_FILINGS) * _F4_PER_ACCESSION


def test_a_FAILED_onboarding_name_stays_windowed_and_is_named_loud_in_the_line(
    db, offline, monkeypatch, capsys
):
    _nightly(monkeypatch)
    cold = _add_master(db, ticker="COLD", cik="0001000002")
    _thesis(db, [("COLD", cold)])
    _queue(db, cold)
    db.execute("UPDATE security_onboarding SET status = 'failed' WHERE security_id = %s", (cold,))
    db.commit()

    [res] = daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())

    assert res.ingested[0].onboarding == "failed"
    assert _count(db, "fact_insider_txn", cold, "ACC-OLD") == 0
    daily._report([res])
    assert "COLD (onboarding FAILED)" in capsys.readouterr().out


def test_the_onboarding_queue_read_FAILS_OPEN_to_the_full_walk(db, offline, monkeypatch, capsys):
    _nightly(monkeypatch)
    cold = _add_master(db, ticker="COLD", cik="0001000002")
    _thesis(db, [("COLD", cold)])
    _queue(db, cold)

    def broken(conn):
        raise RuntimeError("relation does not exist")

    monkeypatch.setattr(onboarding, "pending_status", broken)
    [res] = daily.run_daily(db, asof=_ASOF, allow_live=True, notifier=_Quiet())

    assert "could not read the onboarding queue" in capsys.readouterr().out
    assert res.ingested[0].onboarding is None
    assert _count(db, "fact_insider_txn", cold) == len(_FILINGS) * _F4_PER_ACCESSION


def test_the_window_KEEPS_a_filing_whose_date_cannot_be_read():
    """The window may cost a fetch, never a filing (#9): an unreadable `filed` is read, not dropped."""
    filings = [
        {"accession": "A", "filed": "2026-06-05"},
        {"accession": "B", "filed": "2026-01-02"},
        {"accession": "C", "filed": ""},
        {"accession": "D", "filed": None},
    ]
    kept = filed_on_or_after(filings, date(2026, 5, 11))
    assert [f["accession"] for f in kept] == ["A", "C", "D"]
    assert filed_on_or_after(filings, None) == filings  # no window = every filing


def test_the_job_draws_from_the_global_SEC_budget_in_the_RECURRING_lane(monkeypatch):
    """The job is a BULK BACKGROUND consumer: its one EDGAR client is built on the recurring dial, which is what
    puts it in the global SEC budget's ``recurring`` lane (X2 — ``EdgarClient`` picks the lane from it), so the
    Workbench's interactive lane keeps its fair share against it. The lane is read off the client's REAL lane
    pick (``EdgarClient._sec_budget``); only the process-wide budget registry is stood in."""
    from ingest.edgar import client as client_mod
    from ingest.sec_budget import LANE_RECURRING

    asked: list[str] = []
    monkeypatch.setattr(client_mod, "sec_budget", lambda lane: asked.append(lane) or object())

    client = onboarding._edgar_client()
    client._sec_budget()

    assert client.recurring is True and client.allow_live is True
    assert asked == [LANE_RECURRING]


# --- 7. the runner: the sidecar's second schedule ------------------------------------------------------------


def test_the_SIDECAR_runs_the_job_as_its_own_run_kind_bounded_and_after_the_boot_block():
    """Static: the cron sidecar fires ``pipeline.onboarding --run-kind cron`` under ``timeout`` in a background
    loop started AFTER the boot block (never beside a boot catch-up) and never through ``run_pass`` (it is not
    a nightly pass); the cadence's shell default equals compose's fallback."""
    import re

    backend = Path(__file__).resolve().parents[2]
    script = (backend / "scripts" / "daily_cron.sh").read_text(encoding="utf-8")
    code = [ln.strip() for ln in script.splitlines() if not ln.strip().startswith("#")]
    runs = [ln for ln in code if "python -m pipeline.onboarding" in ln]
    assert runs == [
        'timeout -k 60 "${ONBOARD_TIMEOUT_S}" python -m pipeline.onboarding --run-kind cron'
    ]
    start = code.index("onboard_loop &")
    assert start > code.index('if [ "${CRON_EXIT_AFTER_BOOT:-}" = 1 ]; then')
    assert start < code.index("while :; do", start)  # ... and before the nightly loop
    compose = (backend.parent / "docker-compose.yml").read_text(encoding="utf-8")
    every = re.search(r"ONBOARD_EVERY_S: \$\{ALPHADECK_ONBOARD_EVERY_S:-(\d+)\}", compose)
    shell = re.search(r'ONBOARD_EVERY_S="\$\{ONBOARD_EVERY_S:-(\d+)\}"', script)
    assert every and shell and every.group(1) == shell.group(1) == "1800"


# --- 8. the fast lane's bookkeeping --------------------------------------------------------------------------


def test_mark_from_results_finishes_a_fully_ingested_name_and_leaves_a_failed_one_queued(
    db, offline
):
    a = _add_master(db, ticker="OK", cik="0001000001")
    b = _add_master(db, ticker="BAD", cik="0001000002")
    c = _add_master(db, ticker="HELD", cik="0001000003")
    warm = _add_master(db, ticker="WARM", cik="0001000004")
    _queue(db, a, b, c)
    results = [
        IT.NameResult("OK", a, 2, 2),
        IT.NameResult("BAD", b, 0, 2, "form4: boom", leg_errors=(("form4", "boom"),)),
        IT.NameResult("HELD", c, 0, 0, lock_busy="another writer holds this company"),
        IT.NameResult("WARM", warm, 0, 0),
    ]

    marked = onboarding.mark_from_results(
        db, results, tenant_id=DEFAULT_TENANT_ID, security_ids={a, b, c, warm}
    )

    assert marked == {a: "done", b: "queued"}
    assert _row(db, a).status == "done"
    bad = _row(db, b)
    assert bad.status == "queued" and bad.attempts == 0 and "form4" not in bad.legs_done
    assert "form8k" in bad.legs_done  # what did land is checkpointed
    assert _row(db, c).status == "queued" and _row(db, c).legs_done == ()
    assert _queue_rows(db) == 3  # a warm name is never given a row
