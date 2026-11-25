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

import dataclasses
import uuid
from datetime import date, datetime, time, timedelta, timezone
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
    # COUNT THE TABLES: the WHOLE history — both Form 4s, every bar, the whole filing index
    assert _count(db, "fact_insider_txn", a) == len(_FILINGS) * _F4_PER_ACCESSION
    assert _count(db, "fact_price_eod", a) == len(_BAR_DATES)
    assert _count(db, "fact_filing_index", a) == len(_FILINGS)
    assert out.names[0].appended["filing_index"] == len(_FILINGS)
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
    assert out.names[0].legs_run == [
        "schedule13",
        "filing_index",
        "price",
        "fund_shares",
        "fundamentals",
    ]
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


def test_a_FILING_INDEX_failure_is_a_tail_leg_failure_and_never_blocks_done(
    db, offline, monkeypatch
):
    """The filing index (0070) is the FIRST tail leg: display data, so its failure is recorded as a tail-leg
    failure (the name still goes ``done``) and the leg is not checkpointed — the nightly re-runs it.
    """
    a = _add_master(db, ticker="AAA", cik="0001000001")
    _thesis(db, [("AAA", a)])
    _queue(db, a)

    def boom(conn, client, sec, **k):
        raise RuntimeError("index exploded")

    monkeypatch.setattr(IT, "_filing_index_leg", boom)
    out = _batch()

    assert out.names[0].outcome == "done" and set(out.names[0].legs_failed) == {"filing_index"}
    assert out.names[0].legs_run.index("filing_index") == 3  # right after the three history legs
    row = _row(db, a)
    assert row.status == "done" and "filing_index" not in row.legs_done
    assert "filing_index: index exploded" in row.last_error
    assert _count(db, "fact_filing_index", a) == 0
    assert _count(db, "fact_insider_txn", a) == len(_FILINGS) * _F4_PER_ACCESSION


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


def test_is_parked_is_a_waiting_name_in_no_live_basket_and_nothing_else():
    """The ONE rule behind the readout's count, its waiting list and each row's ``parked`` field."""
    base = onboarding.OnboardingRow(
        tenant_id=DEFAULT_TENANT_ID,
        security_id=uuid.uuid4(),
        status="queued",
        legs_done=(),
        attempts=0,
        last_error=None,
        source="promote",
        thesis_id=None,
        enqueued_at=datetime(2026, 11, 1, tzinfo=timezone.utc),
        last_attempt_at=None,
        finished_at=None,
    )
    for status, live, parked in [
        ("queued", False, True),
        ("in_progress", False, True),
        ("queued", True, False),
        ("in_progress", True, False),
        ("failed", False, False),  # failed is loud on its own; the count never called it parked
        ("done", False, False),
    ]:
        row = dataclasses.replace(base, status=status, in_live_basket=live)
        assert onboarding.is_parked(row) is parked, (status, live)


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
    assert "filing_index" in _row(db, a).legs_done  # the passenger leg ran clean: checkpointed


def test_mark_from_results_checkpoints_the_FILING_INDEX_only_when_it_succeeded(db, offline):
    """The filing index is a passenger in ``ingest_thesis`` (its failure rides ``filing_index_error``, never
    ``leg_errors``), so the fast lane checkpoints it only when NO result for the name carries an error — and
    the name still finishes ``done`` (a tail leg), with the failure noted."""
    a = _add_master(db, ticker="OK", cik="0001000001")
    b = _add_master(db, ticker="IDX", cik="0001000002")
    _queue(db, a, b)
    results = [
        IT.NameResult("OK", a, 2, 2, filing_index_appended=4),
        IT.NameResult("IDX", b, 2, 2),  # one basket row clean...
        IT.NameResult(
            "IDX", b, 0, 0, filing_index_error="index exploded"
        ),  # ...its other row failed
    ]

    marked = onboarding.mark_from_results(
        db, results, tenant_id=DEFAULT_TENANT_ID, security_ids={a, b}
    )

    assert marked == {a: "done", b: "done"}
    assert "filing_index" in _row(db, a).legs_done
    bad = _row(db, b)
    assert "filing_index" not in bad.legs_done and "form4" in bad.legs_done
    assert "filing_index: index exploded" in bad.last_error


# --- 9. the new-company extraction step (V2 W3e) -------------------------------------------------------------
#
# After the queue's batch (approved B5 (a), plan correction 5): the job extracts the platform values of every
# NEVER-extracted company on a live card, wherever it came from: a queued cold name the batch just loaded, a
# FAST-LANED promote (marked done without the job running a leg), or a company that joined from another card.
# A company already extracted (a new filing, a retry) is the NIGHTLY's. No SEC request: the extraction is faked at
# ``platform_values.extraction`` and the client serves an empty submissions index.


class _ExtractClient(_FakeClient):
    """The batch's ONE client: the Form 4 sample for the legs, an empty submissions index for the extraction step
    (counted, so \"no fetch\" is measured)."""

    def __init__(self, **kwargs):
        self.json_reads: list[str] = []
        self.live_fetches = 0

    def get_json(self, url: str, cache_key: str, **_):
        self.json_reads.append(cache_key)
        self.live_fetches += 1
        return {"filings": {"recent": {}}}


class _FakeExtract:
    """Stands in for ``extract_with_annual_fallback`` (``facts=[]``: nothing found). Records the CIK, the client,
    the order against the batch's legs, and whether ANY session held ANY company lock while it 'fetched'.
    """

    def __init__(self, events: list, *, on_call=None):
        from domain.extraction import ExtractionResult

        self.events = events
        self.calls: list[int] = []
        self.clients: list = []
        self.company_locks_held: list[int] = []
        self.on_call = on_call
        self._result = ExtractionResult(facts=[])

    def __call__(self, client, cik, *, values_only=False, **_):
        from db.write_lock import COMPANY_LOCK_NAMESPACE

        self.calls.append(int(cik))
        self.clients.append(client)
        self.events.append(("extract", int(cik)))
        probe = connect()
        try:
            n = probe.execute(
                # pg_locks is SERVER-wide: count THIS database's locks only, or another xdist worker's
                # company lock (its own database, the same server) is counted as ours.
                "SELECT count(*) AS n FROM pg_locks WHERE locktype = 'advisory' AND classid = %s "
                "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())",
                (COMPANY_LOCK_NAMESPACE,),
            ).fetchone()["n"]
        finally:
            probe.close()
        self.company_locks_held.append(int(n))
        if self.on_call is not None:
            self.on_call(int(cik))
        return self._result


@pytest.fixture
def extraction(monkeypatch):
    """``extraction()`` installs the fake extractor (and records the batch's names in the same event list)."""
    from platform_values import extraction as ex

    events: list = []
    real_one = onboarding.onboard_one

    def recording_one(conn, row, **k):
        out = real_one(conn, row, **k)
        events.append(("name", row.ticker))
        return out

    monkeypatch.setattr(onboarding, "onboard_one", recording_one)

    def install(on_call=None) -> _FakeExtract:
        fake = _FakeExtract(events, on_call=on_call)
        monkeypatch.setattr(ex, "extract_with_annual_fallback", fake)
        return fake

    return install


def _backfill_applied(db) -> uuid.UUID:
    """Approved B4 (a): the steps take never-extracted companies only once a ``done`` backfill attempt exists."""
    from repositories import platform_extraction_repo as repo

    decoy = _add_master(db, ticker="BKFL", cik="0009999999")
    repo.append_attempt(
        db, security_id=decoy, run_kind="backfill", inputs_key="9999999||||", status="done"
    )
    db.commit()
    return decoy


def _attempts(db, sid) -> list[dict]:
    from repositories import platform_extraction_repo as repo

    db.rollback()
    out = repo.latest_attempts(db, [sid], per_company=50).get(sid, [])
    db.rollback()
    return out


def _all_attempts(db) -> int:
    db.rollback()
    n = db.execute("SELECT count(*) AS n FROM platform_extraction_attempt").fetchone()["n"]
    db.rollback()
    return n


def _queue_state(db) -> list[tuple]:
    db.rollback()
    out = [
        tuple(r.values())
        for r in db.execute(
            "SELECT security_id, status, legs_done, attempts, last_error, finished_at "
            "FROM security_onboarding ORDER BY security_id"
        ).fetchall()
    ]
    db.rollback()
    return out


def _thesis_state(db) -> list[tuple]:
    db.rollback()
    out = [tuple(r.values()) for r in db.execute("SELECT * FROM thesis ORDER BY id").fetchall()]
    db.rollback()
    return out


def _fast_lane(db, *pairs: tuple[str, uuid.UUID], name: str = "promoted") -> uuid.UUID:
    """A Workbench promote of a FEW new names: the fast lane ingests them at once and marks them done from the
    run's results, so the job never runs a leg for them (correction 5)."""
    tid = _thesis(db, list(pairs), name)
    sids = [s for _, s in pairs]
    plan = onboarding.plan_promote(db, sids, tenant_id=DEFAULT_TENANT_ID, thesis_id=tid, now=_NOON)
    db.commit()
    assert plan.fast and plan.cold == frozenset(sids)
    onboarding.mark_from_results(
        db,
        [IT.NameResult(t, s, 2, 2) for t, s in pairs],
        tenant_id=DEFAULT_TENANT_ID,
        security_ids=set(sids),
    )
    db.commit()
    assert {_row(db, s).status for s in sids} == {"done"}
    return tid


def test_a_FAST_LANED_promote_is_extracted_by_the_next_batch_with_nothing_queued(db, extraction):
    """Correction 5: a promote of five or fewer new names is fast-laned (no leg of the job runs for them). The
    job's step after the queue still extracts them on its next run (within ~30 minutes), even though nothing is
    queued; the queue and the batch's own refusal are untouched."""
    a = _add_master(db, ticker="FAST1", cik="0001000001")
    b = _add_master(db, ticker="FAST2", cik="0001000002")
    _fast_lane(db, ("FAST1", a), ("FAST2", b))
    _backfill_applied(db)
    queue_before, theses_before = _queue_state(db), _thesis_state(db)
    fake = extraction()
    client = _ExtractClient()

    out = _batch(edgar_client=client)

    assert out.refused and out.stopped == "nothing_queued"  # the batch's own result, as before
    assert sorted(fake.calls) == [1000001, 1000002], out.platform_extraction
    assert all(
        c is client for c in fake.clients
    )  # the batch's ONE recurring client (the SEC budget lane)
    for sid in (a, b):
        [att] = _attempts(db, sid)
        assert att["run_kind"] == "onboarding" and att["status"] == "done", att
    assert _queue_state(db) == queue_before and _thesis_state(db) == theses_before
    pe = out.platform_extraction
    assert pe["status"] == "ran" and pe["attempted"] == 2 and pe["extracted"] == 2
    assert "2 extracted" in out.platform_extraction_line
    # a second batch: they are no longer never-extracted, so nothing is fetched again (the nightly owns new filings)
    again = _batch(edgar_client=client)
    assert sorted(fake.calls) == [1000001, 1000002], out.platform_extraction
    assert again.platform_extraction["attempted"] == 0


def test_the_step_runs_AFTER_the_batch_with_every_company_lock_released(db, offline, extraction):
    """A queued cold name: the batch loads it first (under its company lock), and only then, with every company
    free, does the step fetch its values (correction 6: never fetch while holding a company lock).
    """
    q = _add_master(db, ticker="QUEUED", cik="0001000001")
    f = _add_master(db, ticker="FAST", cik="0001000002")
    _thesis(db, [("QUEUED", q)], "big promote")
    _queue(db, q)
    _fast_lane(db, ("FAST", f))
    _backfill_applied(db)
    fake = extraction()

    out = _batch(edgar_client=_ExtractClient())

    assert [(n.ticker, n.outcome) for n in out.names] == [("QUEUED", "done")]
    assert fake.events[0] == ("name", "QUEUED")  # the batch's names first ...
    assert sorted(fake.events[1:]) == [("extract", 1000001), ("extract", 1000002)]
    assert fake.company_locks_held == [
        0,
        0,
    ]  # ... then every fetch with NO company lock held anywhere
    assert out.platform_extraction["attempted"] == 2
    assert _row(db, q).status == "done" and _row(db, f).status == "done"


def test_a_company_already_on_ANOTHER_card(db, extraction):
    """Correction 5: a company already on another live card is never queued. Never extracted (it joined before
    any extraction reached it): THIS step extracts it. Already extracted: it is not new; its newer filing is the
    NIGHTLY's (this step takes only never-extracted companies), and nothing is fetched for it here.
    """
    from platform_values.extraction import inputs_key
    from repositories import platform_extraction_repo as repo

    never = _add_master(db, ticker="NEVER", cik="0001000001")
    known = _add_master(db, ticker="KNOWN", cik="0001000002")
    _thesis(db, [("NEVER", never), ("KNOWN", known)], "the first card")
    repo.append_attempt(
        db,
        security_id=known,
        run_kind="backfill",
        inputs_key=inputs_key("0001000002", []),
        status="done",
    )
    accepted = _NOON - timedelta(
        hours=30
    )  # a 10-Q public for 30 h at the batch's clock: a NEW FILING
    db.execute(
        "INSERT INTO fact_filing_index (tenant_id, security_id, form, accession, filed, primary_doc, accepted, "
        "source_ref, valid_from) VALUES (%s, %s, '10-Q', 'ACC-Q', %s, 'q.htm', %s, 'https://x', %s)",
        (DEFAULT_TENANT_ID, known, accepted.date(), accepted, accepted.date()),
    )
    db.commit()
    # the operator adds both to a second card: neither is cold, so neither is queued
    second = _thesis(db, [("NEVER", never), ("KNOWN", known)], "the second card")
    plan = onboarding.plan_promote(
        db, [never, known], tenant_id=DEFAULT_TENANT_ID, thesis_id=second, now=_NOON
    )
    db.commit()
    assert plan.cold == frozenset() and _queue_rows(db) == 0
    fake = extraction()

    out = _batch(edgar_client=_ExtractClient())

    assert fake.calls == [1000001]  # NEVER: this step; KNOWN: not fetched here
    assert [a["run_kind"] for a in _attempts(db, never)] == ["onboarding"]
    assert [a["run_kind"] for a in _attempts(db, known)] == ["backfill"]
    assert (
        out.platform_extraction["left_for_nightly"] == 1
    )  # KNOWN's new filing waits for the nightly
    assert _queue_rows(db) == 0  # the step never queues anything


def test_before_any_BACKFILL_the_step_counts_new_companies_and_fetches_nothing(db, extraction):
    """Approved B4 (a): until one ``done`` backfill attempt exists, never-extracted companies are held back:
    counted, not fetched, no attempt written, nothing in data/extraction_runs/."""
    from pipeline import extraction_run_log

    a = _add_master(db, ticker="FAST1", cik="0001000001")
    b = _add_master(db, ticker="FAST2", cik="0001000002")
    _fast_lane(db, ("FAST1", a), ("FAST2", b))
    fake = extraction()
    client = _ExtractClient()

    out = _batch(edgar_client=client)

    assert fake.calls == [] and client.json_reads == [] and _all_attempts(db) == 0
    pe = out.platform_extraction
    assert pe["status"] == "ran" and pe["attempted"] == 0 and pe["withheld_never"] == 2
    assert "2 held back until a backfill extracts a company" in out.platform_extraction_line
    assert extraction_run_log.latest_extraction_run() is None  # an idle step writes no artifact


def _world(db, queued: int = 0, fast: int = 0) -> list[uuid.UUID]:
    sids = []
    if queued:
        qs = [
            (f"Q{i}", _add_master(db, ticker=f"Q{i}", cik=f"000100010{i}")) for i in range(queued)
        ]
        _thesis(db, qs, "queued promote")
        for _, s in qs:
            _queue(db, s)
        sids += [s for _, s in qs]
    if fast:
        fs = [(f"F{i}", _add_master(db, ticker=f"F{i}", cik=f"000100020{i}")) for i in range(fast)]
        _fast_lane(db, *fs)
        sids += [s for _, s in fs]
    _backfill_applied(db)
    return sids


def _snapshot(db, out: onboarding.BatchOutcome) -> dict:
    """The batch's OWN result, made comparable across two runs (ids, instants and timings dropped)."""
    doc = onboarding.list_runs(runs_dir=out.log_path.parent)[0] if out.log_path else None
    if doc is not None:
        doc = {
            k: v
            for k, v in doc.items()
            if k not in ("started_at", "finished_at", "duration_s", "timings")
        }
        doc["names"] = [
            {k: v for k, v in n.items() if k not in ("security_id", "seconds")}
            for n in doc["names"]
        ]
    db.rollback()
    queue = [
        tuple(r.values())
        for r in db.execute(
            "SELECT sm.ticker, so.status, so.legs_done, so.attempts, so.last_error, so.source "
            "FROM security_onboarding so JOIN security_master sm ON sm.id = so.security_id "
            "ORDER BY sm.ticker"
        ).fetchall()
    ]
    theses = [
        tuple(r.values())
        for r in db.execute("SELECT name, archived_at FROM thesis ORDER BY name").fetchall()
    ]
    db.rollback()
    return {
        "refused": out.refused,
        "stopped": out.stopped,
        "detail": out.detail,
        "queued_at_start": out.queued_at_start,
        "names": [(n.ticker, n.outcome, n.legs_run, n.legs_failed, n.appended) for n in out.names],
        "counts": out.counts(),
        "artifact": doc,
        "queue": queue,
        "theses": theses,
    }


def _reset(db) -> None:
    """The ``db`` fixture's own clean slate, again, inside one test."""
    db.rollback()
    db.execute(
        "TRUNCATE thesis, fact_insider_txn, fact_price_eod, security_master, edition, data_repair, "
        "security_change CASCADE"
    )
    db.commit()


def test_the_step_RAISING_leaves_the_batch_its_exit_and_its_queue_exactly_as_without_it(
    db, offline, extraction, monkeypatch, capsys
):
    """Fail-open: the same world run once with the step ABSENT and once with it RAISING gives the same batch
    (names, counts, stop reason, artifact), the same queue and theses, and ``main`` returns normally (exit 0)
    both times; the raising run only adds the step's one FAILED line."""
    import re
    from functools import partial

    from platform_values import extraction as ex

    real_batch = onboarding.run_batch
    real_step = onboarding._extraction_step
    real_work_list = ex.work_list
    extraction()

    def boom(*a, **k):
        raise RuntimeError("the step blew up")

    def run(mode: str) -> tuple[dict, str, onboarding.BatchOutcome]:
        _reset(db)
        _world(db, queued=2, fast=1)
        if mode == "absent":
            monkeypatch.setattr(onboarding, "_extraction_step", lambda *a, **k: None)
            monkeypatch.setattr(ex, "work_list", real_work_list)
        else:
            monkeypatch.setattr(onboarding, "_extraction_step", real_step)
            monkeypatch.setattr(ex, "work_list", boom)
        seen: list[onboarding.BatchOutcome] = []

        def recording_batch(**kw):
            out = partial(
                real_batch,
                now=lambda: _NOON,
                edgar_client=_ExtractClient(),
                price_source=_FakePrices(),
                split_source=None,
            )(**kw)
            seen.append(out)
            return out

        monkeypatch.setattr(onboarding, "run_batch", recording_batch)
        capsys.readouterr()
        assert onboarding.main([]) is None  # no SystemExit: exit status 0
        printed = capsys.readouterr().out
        monkeypatch.setattr(onboarding, "run_batch", real_batch)
        return _snapshot(db, seen[0]), printed, seen[0]

    absent, printed_absent, out_absent = run("absent")
    raising, printed_raising, out_raising = run("raising")

    assert raising == absent
    assert [n[:2] for n in absent["names"]] == [("Q0", "done"), ("Q1", "done")]
    assert out_absent.platform_extraction is None
    assert out_raising.platform_extraction["status"] == "error"
    assert "the step blew up" in out_raising.platform_extraction["detail"]

    def lines(text: str) -> list[str]:
        # durations vary, and the timings line also ORDERS its legs by duration, so its text differs run to run
        return [
            re.sub(r"\d+(\.\d+)?", "N", ln)
            for ln in text.splitlines()
            if not ln.startswith("timings:")
        ]

    extra = [ln for ln in lines(printed_raising) if ln not in lines(printed_absent)]
    assert len(extra) == 1 and extra[0].startswith("Shared facts, new companies: FAILED")
    assert [ln for ln in lines(printed_raising) if ln not in extra] == lines(printed_absent)
    assert _all_attempts(db) == 1  # only the B4 marker: the raising step wrote nothing


def test_the_step_gets_ONLY_what_is_left_of_the_batch_budget(db, offline, extraction, monkeypatch):
    """The batch's name spends 90 of its 100 s: the step has 10 s, takes one company (8 s) and never starts one
    it cannot afford. A batch whose names spend the whole budget leaves the step nothing: not run, no fetch.
    """
    _world(db, queued=1, fast=2)
    t = {"now": 0.0}
    recording_one = onboarding.onboard_one

    def slow_one(conn, row, **k):
        out = recording_one(conn, row, **k)
        t["now"] += 90.0
        return out

    monkeypatch.setattr(onboarding, "onboard_one", slow_one)
    fake = extraction(on_call=lambda cik: t.__setitem__("now", t["now"] + 8.0))

    out = _batch(edgar_client=_ExtractClient(), budget_s=100.0, clock=lambda: t["now"])

    pe = out.platform_extraction
    assert pe["budget_s"] == pytest.approx(10.0)
    assert len(fake.calls) == 1 and pe["attempted"] == 1
    assert pe["stopped_by"] == "budget" and pe["deferred"] == 2

    late = _add_master(db, ticker="LATE", cik="0001000301")
    _thesis(db, [("LATE", late)], "a later promote")
    _queue(db, late)
    spent = _batch(edgar_client=_ExtractClient(), budget_s=80.0, clock=lambda: t["now"])

    assert [(n.ticker, n.outcome) for n in spent.names] == [("LATE", "done")]
    assert spent.platform_extraction["status"] == "skipped"
    assert "time budget is spent" in spent.platform_extraction_line
    assert len(fake.calls) == 1  # nothing more fetched


def test_the_step_takes_the_ONBOARDING_cap_and_the_KILL_SWITCH_turns_it_off(
    db, extraction, settings_env
):
    _world(db, fast=3)
    settings_env(platform_extraction_max_names=0)
    fake = extraction()
    client = _ExtractClient()

    off = _batch(edgar_client=client)

    assert off.platform_extraction["status"] == "off" and fake.calls == []
    assert client.json_reads == [] and _all_attempts(db) == 1
    assert "kill switch" in off.platform_extraction_line

    settings_env(platform_extraction_max_names=300, platform_extraction_onboard_max_names=2)
    capped = _batch(edgar_client=client)

    pe = capped.platform_extraction
    assert len(fake.calls) == 2 and pe["attempted"] == 2
    assert pe["stopped_by"] == "cap" and pe["deferred"] == 1 and pe["max_names"] == 2


def test_the_step_yields_to_a_PASS_and_to_the_NIGHTLY_WINDOW_between_companies(
    db, extraction, settings_env
):
    from pipeline.run_lock import acquire_pass_lock

    _world(db, fast=3)
    held: list = []

    def pass_starts(cik):
        if not held:
            held.append(
                acquire_pass_lock(holder="manual")
            )  # never refused: the job does not hold it

    fake = extraction(on_call=pass_starts)
    try:
        out = _batch(edgar_client=_ExtractClient())
    finally:
        for lk in held:
            lk.release()
    assert len(fake.calls) == 1
    assert (
        out.platform_extraction["stopped_by"] == "stop" and out.platform_extraction["deferred"] == 2
    )

    settings_env(onboard_window_before_min=60, onboard_window_after_min=60)
    clock = {"now": datetime(2026, 6, 10, 21, 20, tzinfo=_NY)}
    fake = extraction(
        on_call=lambda cik: clock.__setitem__("now", datetime(2026, 6, 10, 21, 31, tzinfo=_NY))
    )
    out = _batch(edgar_client=_ExtractClient(), now=lambda: clock["now"])
    assert len(fake.calls) == 1 and out.platform_extraction["stopped_by"] == "stop"

    # inside the window the job refuses before the queue: the step never runs, nothing is fetched
    fake = extraction()
    refused = _batch(edgar_client=_ExtractClient(), now=lambda: clock["now"])
    assert refused.refused and refused.stopped == "window"
    assert refused.platform_extraction is None and fake.calls == []


def test_the_step_is_ONE_line_in_the_jobs_output_and_its_status(
    db, extraction, monkeypatch, capsys
):
    """Its summary is written through ``pipeline/extraction_run_log.py`` (run kind ``onboarding``) and shows as
    one line in the batch's output and in ``python -m pipeline.onboarding --status``."""
    from functools import partial

    from pipeline import extraction_run_log

    _world(db, fast=1)
    extraction()
    onboarding.main(["--status"])
    assert "Shared facts, new companies: no extraction run yet" in capsys.readouterr().out

    real = onboarding.run_batch
    monkeypatch.setattr(
        onboarding,
        "run_batch",
        partial(real, now=lambda: _NOON, edgar_client=_ExtractClient(), split_source=None),
    )
    onboarding.main([])
    printed = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("Shared facts")]
    assert len(printed) == 1 and "1 extracted" in printed[0]

    doc = extraction_run_log.latest_extraction_run(run_kind="onboarding")
    assert doc is not None and doc["admin_line"] == printed[0] and doc["attempted"] == 1

    onboarding.main(["--status"])
    status = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("Shared facts")]
    assert len(status) == 1 and status[0].startswith(printed[0])
