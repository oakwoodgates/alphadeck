"""W1 — the per-company write lock (``db/write_lock.py``) and the repair gate (``run_lock.acquire_repair_lock``),
against the REAL test Postgres.

Everything that matters about an advisory lock is a property of real Postgres SESSIONS — a second session is
refused, the holder is named, a closed session leaves nothing behind, a session lock outlives the writer's own
commits — so every test here opens real extra connections. The end-to-end proofs (two concurrent ingests of one
company counted in the TABLE; the nightly deferring, waiting for and then ingesting a held company) live in
``tests/pipeline/test_ingest_write_lock.py``.
"""

from __future__ import annotations

import threading
import time
import uuid

import pytest
from psycopg.pq import TransactionStatus

from db.session import connect, name_session
from db.write_lock import (
    COMPANY_LOCK_NAMESPACE,
    FACT_GATE_ID,
    FACT_GATE_NAMESPACE,
    CompanyLockBusy,
    acquire_company_lock,
    company_key,
    describe_company_holder,
)
from pipeline.run_lock import (
    LOCK_ID_CALL_OF_RECORD,
    LOCK_NAMESPACE,
    FactWritersBusy,
    PassAlreadyRunning,
    acquire_pass_lock,
    acquire_repair_lock,
    probe_pass_lock,
)


@pytest.fixture
def session(db):
    """Open extra REAL sessions on the test DB (``db`` = migrated + truncated); every one is closed after."""
    opened = []

    def make(name: str | None = None):
        c = connect()
        if name:
            name_session(c, name)
        opened.append(c)
        return c

    yield make
    for c in opened:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def _advisory(conn, classid: int, objid: int) -> list[dict]:
    """The granted advisory locks on (classid, objid) across ALL sessions, from ``pg_locks``."""
    conn.rollback()
    rows = conn.execute(
        "SELECT pid, mode FROM pg_locks WHERE locktype = 'advisory' AND granted "
        "AND classid = %s AND objid = %s AND objsubid = 2 "
        "AND database = (SELECT oid FROM pg_database WHERE datname = current_database())",
        (classid, objid & 0xFFFFFFFF),
    ).fetchall()
    conn.rollback()
    return rows


# --- the company lock -----------------------------------------------------------------------------------


def test_a_second_writer_is_REFUSED_and_told_WHO_holds_the_company(session):
    wb, nightly = session("alphadeck-writer:workbench-ingest"), session()
    sid = uuid.uuid4()
    held = acquire_company_lock(wb, sid, wait_s=0)
    with pytest.raises(CompanyLockBusy) as exc:
        acquire_company_lock(nightly, sid, wait_s=0)
    assert exc.value.holder and "alphadeck-writer:workbench-ingest" in exc.value.holder
    assert exc.value.gate is False and exc.value.security_id == sid
    assert "another writer holds this company" in str(exc.value)
    assert describe_company_holder(nightly, sid) == exc.value.holder
    held.release()
    acquire_company_lock(nightly, sid, wait_s=0).release()  # free again the moment it is let go


def test_two_DIFFERENT_companies_never_wait_for_each_other(session):
    a, b = session(), session()
    x, y = uuid.uuid4(), uuid.uuid4()
    with acquire_company_lock(a, x, wait_s=0), acquire_company_lock(b, y, wait_s=0) as ly:
        assert ly.waited_s < 0.5


def test_the_wait_is_BOUNDED_and_ends_in_a_visible_refusal(session):
    a, b = session("alphadeck-writer:daily-pass"), session()
    sid = uuid.uuid4()
    held = acquire_company_lock(a, sid, wait_s=0)
    t0 = time.monotonic()
    with pytest.raises(CompanyLockBusy) as exc:
        acquire_company_lock(b, sid, wait_s=1.0, poll_s=0.1)
    elapsed = time.monotonic() - t0
    assert 1.0 <= exc.value.waited_s < 3.0 and elapsed < 3.0  # waited the bound, not forever
    assert "alphadeck-writer:daily-pass" in str(exc.value)
    held.release()


def test_a_waiter_gets_the_company_as_soon_as_the_holder_lets_go(session):
    a, b = session(), session()
    sid = uuid.uuid4()
    held = acquire_company_lock(a, sid, wait_s=0)
    threading.Timer(0.6, held.release).start()
    got = acquire_company_lock(b, sid, wait_s=10, poll_s=0.1)
    assert 0.5 <= got.waited_s < 5  # it waited for the release — and no longer
    got.release()


def test_the_lock_OUTLIVES_the_writers_own_commits_and_rollbacks(session):
    """The ingest legs commit (or roll back) one by one; the company must stay held across all of them —
    Postgres keeps a SESSION advisory lock through COMMIT and ROLLBACK alike."""
    conn, other = (
        session(),
        session(),
    )  # `conn`: a DB cursor's execute, not a backtest run (run_e2e scan)
    sid = uuid.uuid4()
    held = acquire_company_lock(conn, sid, wait_s=0)
    conn.execute("SELECT 1")
    conn.commit()
    conn.execute("SELECT 1")
    conn.rollback()
    with pytest.raises(CompanyLockBusy):
        acquire_company_lock(other, sid, wait_s=0)
    held.release()


def test_it_is_RE_ENTRANT_on_one_session_and_released_once_per_acquisition(session):
    a, b = session(), session()
    sid = uuid.uuid4()
    first = acquire_company_lock(a, sid, wait_s=0)
    second = acquire_company_lock(
        a, sid, wait_s=0
    )  # a nested writer on the SAME session: granted at once
    second.release()
    with pytest.raises(CompanyLockBusy):  # the outer hold still stands
        acquire_company_lock(b, sid, wait_s=0)
    first.release()
    acquire_company_lock(b, sid, wait_s=0).release()


def test_a_DEAD_writer_leaves_nothing_behind(session):
    """A writer whose process dies loses its session — and its locks with it. No stale lock, no TTL."""
    a, b = session(), session()
    sid = uuid.uuid4()
    acquire_company_lock(a, sid, wait_s=0)
    a.close()  # no unlock ran
    acquire_company_lock(b, sid, wait_s=0).release()


def test_attempts_are_TRANSACTION_NEUTRAL(session):
    """An idle connection stays idle through an acquire, a refusal and a release (so a writer never waits
    "idle in transaction"); a caller's OPEN transaction is never ended by an attempt."""
    a, b = session(), session()
    sid = uuid.uuid4()
    held = acquire_company_lock(a, sid, wait_s=0)
    assert a.info.transaction_status == TransactionStatus.IDLE
    with pytest.raises(CompanyLockBusy):
        acquire_company_lock(b, sid, wait_s=0)
    assert b.info.transaction_status == TransactionStatus.IDLE
    b.execute("SELECT 1")  # the caller's own open transaction...
    with pytest.raises(CompanyLockBusy):
        acquire_company_lock(b, sid, wait_s=0)
    assert b.info.transaction_status == TransactionStatus.INTRANS  # ...is left open, not ended
    b.rollback()
    held.release()
    assert a.info.transaction_status == TransactionStatus.IDLE


def test_a_release_out_of_an_ABORTED_transaction_still_frees_the_company(session):
    a, b = session(), session()
    sid = uuid.uuid4()
    held = acquire_company_lock(a, sid, wait_s=0)
    with pytest.raises(Exception):
        a.execute("SELECT 1/0")  # the leg failed: the transaction is aborted
    assert a.info.transaction_status == TransactionStatus.INERROR
    held.release()
    acquire_company_lock(b, sid, wait_s=0).release()


def test_the_key_is_a_stable_signed_int4_and_shows_in_pg_locks(session):
    sid = uuid.UUID("ffffffff-0000-4000-8000-000000000000")
    assert company_key(sid) == -1  # the first 32 bits as a SIGNED int4
    assert company_key(uuid.UUID(str(sid))) == company_key(sid)
    a = session()
    held = acquire_company_lock(a, sid, wait_s=0)
    rows = _advisory(session(), COMPANY_LOCK_NAMESPACE, company_key(sid))
    assert [r["mode"] for r in rows] == ["ExclusiveLock"]
    # ...and the writer holds the fact-writer gate SHARED alongside it
    assert "ShareLock" in {
        r["mode"] for r in _advisory(session(), FACT_GATE_NAMESPACE, FACT_GATE_ID)
    }
    held.release()
    assert _advisory(session(), COMPANY_LOCK_NAMESPACE, company_key(sid)) == []


# --- the repair gate + the lock order --------------------------------------------------------------------


def test_a_REPAIR_waits_for_company_writers_then_REFUSES_holding_nothing(session):
    """A repair takes the call-of-record lock, then the gate EXCLUSIVELY; a company writer mid-company keeps
    the gate shared, so the repair waits (bounded) and then refuses as a ``PassAlreadyRunning`` — the
    repairs' existing "nothing written, exit 75" path — giving the call-of-record lock back."""
    wb = session("alphadeck-writer:workbench-ingest")
    held = acquire_company_lock(wb, uuid.uuid4(), wait_s=0)
    t0 = time.monotonic()
    with pytest.raises(FactWritersBusy) as exc:
        acquire_repair_lock(holder="test-repair", wait_s=0.5, poll_s=0.1)
    assert time.monotonic() - t0 < 3
    assert isinstance(exc.value, PassAlreadyRunning)  # every repair main maps this to exit 75
    assert "alphadeck-writer:workbench-ingest" in str(exc.value)
    assert probe_pass_lock() is None  # the call-of-record lock was given back
    held.release()
    with acquire_repair_lock(holder="test-repair", wait_s=0) as lock:  # writers drained -> it runs
        assert lock.gate


def test_while_a_REPAIR_runs_NO_company_writer_starts_and_the_repair_is_named(session):
    lock = acquire_repair_lock(holder="test-repair", wait_s=0)
    b = session()
    sid = uuid.uuid4()
    try:
        with pytest.raises(CompanyLockBusy) as exc:
            acquire_company_lock(b, sid, wait_s=0.3, poll_s=0.1)
        assert exc.value.gate is True
        assert exc.value.holder and "alphadeck-pass:test-repair" in exc.value.holder
        assert "a fact repair holds every company" in str(exc.value)
    finally:
        lock.release()
    acquire_company_lock(b, sid, wait_s=0).release()  # and the moment it ends, writers resume


def test_the_LOCK_ORDER_call_of_record_then_gate_then_company_admits_no_cycle(session):
    """THE DEADLOCK RULE: call-of-record -> gate -> company, never the reverse. Concretely, with the nightly
    holding the call-of-record lock:
    - a Workbench writer and the nightly each take a company concurrently — neither needs the other's lock;
    - the nightly can take a company the Workbench just let go (it waits only on the company, holding no
      company of its own while it waits);
    - a repair is refused at its FIRST lock (the call-of-record lock) — so it never holds the gate while a
      pass runs, and it never waits on anything while holding a company (it holds none);
    - no company writer ever takes the call-of-record lock: the pass lock stays exactly the nightly's.
    Every acquisition is a try (or a bounded poll of tries), so no session ever BLOCKS in Postgres on an
    advisory lock and the server's waits-for graph has no advisory edge at all."""
    nightly_pass = acquire_pass_lock(holder="cron")
    try:
        wb, nightly = session("alphadeck-writer:workbench-ingest"), session(
            "alphadeck-writer:daily"
        )
        x, y = uuid.uuid4(), uuid.uuid4()
        wb_x = acquire_company_lock(wb, x, wait_s=0)
        n_y = acquire_company_lock(nightly, y, wait_s=0)  # concurrent, no wait
        n_y.release()
        threading.Timer(0.3, wb_x.release).start()
        n_x = acquire_company_lock(nightly, x, wait_s=5, poll_s=0.05)  # waits on the company only
        with pytest.raises(PassAlreadyRunning) as exc:
            acquire_repair_lock(holder="repair", wait_s=5)
        assert not isinstance(exc.value, FactWritersBusy)  # refused at the FIRST lock, not the gate
        # the repair holds nothing; the only call-of-record holder is the nightly pass
        probe = session()
        assert [r["mode"] for r in _advisory(probe, FACT_GATE_NAMESPACE, FACT_GATE_ID)] == [
            "ShareLock"  # the nightly's shared gate, no exclusive (repair) hold anywhere
        ]
        assert len(_advisory(probe, LOCK_NAMESPACE, LOCK_ID_CALL_OF_RECORD)) == 1
        n_x.release()
    finally:
        nightly_pass.release()


def test_a_company_writer_NEVER_makes_a_pass_refuse(session):
    """A Workbench ingest mid-company must never make a pass exit 75: the pass lock is free and takes."""
    wb = session("alphadeck-writer:workbench-ingest")
    held = acquire_company_lock(wb, uuid.uuid4(), wait_s=0)
    try:
        assert probe_pass_lock() is None
        acquire_pass_lock(holder="cron").release()
    finally:
        held.release()
