"""The per-company change stamp (``db/change_stamp.py``, migration 0081) and the company write lock's release
moving it (``db/write_lock.CompanyLock.release``), against the REAL test Postgres.

The stamp is what the dashboards' cache reads to drop one company's pieces (docs/DASHBOARDS.md rule 7). These
tests COUNT THE TABLE and read it from a SECOND session, so "moved" always means "committed and visible".
"""

from __future__ import annotations

import logging
import uuid
from datetime import date

import pytest
from psycopg.pq import TransactionStatus

from db import write_lock
from db.change_stamp import mark_changed, stamp_fingerprint, stamp_map
from db.session import DEFAULT_TENANT_ID, connect
from db.write_lock import acquire_company_lock


@pytest.fixture
def session(db):
    """Extra REAL sessions on the test DB (``db`` = migrated + truncated); every one is closed after."""
    opened = []

    def make():
        c = connect()
        opened.append(c)
        return c

    yield make
    for c in opened:
        try:
            c.close()
        except Exception:  # noqa: BLE001
            pass


def _company(conn, ticker: str) -> uuid.UUID:
    sid = uuid.uuid4()
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO security_master (id, tenant_id, ticker, valid_from) VALUES (%s, %s, %s, %s)",
            (sid, DEFAULT_TENANT_ID, ticker, date(2020, 1, 1)),
        )
    conn.commit()
    return sid


def _rows(conn) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM security_change")
        n = cur.fetchone()["n"]
    conn.rollback()
    return n


def _seq(conn, sid) -> int | None:
    got = stamp_map(conn).get(sid)
    conn.rollback()
    return got


# --- mark_changed --------------------------------------------------------------------------------------------


def test_mark_changed_inserts_then_raises_the_seq(db):
    a = _company(db, "AAA")
    assert _rows(db) == 0
    assert mark_changed(db, [a]) == 1
    db.commit()
    first = _seq(db, a)
    assert _rows(db) == 1 and first is not None
    assert mark_changed(db, [a]) == 1
    db.commit()
    assert _rows(db) == 1  # updated in place: one row per company, never a second
    assert _seq(db, a) > first


def test_mark_changed_stamps_each_company_once_in_sorted_id_order(db):
    ids = [_company(db, f"T{i}") for i in range(4)]
    assert mark_changed(db, [ids[2], ids[0], ids[2], None, ids[3], ids[1]]) == 4
    db.commit()
    got = stamp_map(db)
    db.rollback()
    assert _rows(db) == 4
    # the rows were drawn in sorted id order: the seq order IS the id order
    assert sorted(ids, key=lambda s: got[s]) == sorted(ids)


def test_an_empty_input_is_a_no_op_and_an_unknown_company_stamps_nothing(db):
    assert mark_changed(db, []) == 0
    assert mark_changed(db, [None]) == 0
    assert mark_changed(db, [uuid.uuid4()]) == 0  # not in the master
    db.commit()
    assert _rows(db) == 0
    assert stamp_fingerprint(db) == (0, 0, 0)


def test_the_fingerprint_rises_on_every_committed_change(db):
    a, b = _company(db, "AAA"), _company(db, "BBB")
    seen = [stamp_fingerprint(db)]
    for sid in (a, b, a, a, b):
        mark_changed(db, [sid])
        db.commit()
        seen.append(stamp_fingerprint(db))
    db.rollback()
    assert len(set(seen)) == len(seen)  # every committed change moved it
    assert [s[1] for s in seen] == sorted(s[1] for s in seen)  # the sum only ever rises


def test_the_fingerprint_sees_an_OUT_OF_ORDER_commit(db, session):
    """A transaction that drew the LOWER seq commits AFTER one that drew a higher seq: ``max(seq)`` alone
    would miss it, the sum does not."""
    a, b = _company(db, "AAA"), _company(db, "BBB")
    early, late = session(), session()
    mark_changed(early, [a])  # draws the lower seq, not committed yet
    mark_changed(late, [b])  # draws the higher seq
    late.commit()
    before = stamp_fingerprint(db)
    db.rollback()
    early.commit()
    after = stamp_fingerprint(db)
    db.rollback()
    assert after[0] == before[0]  # the max did not move...
    assert after != before and after[1] > before[1]  # ...the fingerprint did
    assert stamp_map(db)[a] < stamp_map(db)[b]


# --- the lock release moves the stamp -------------------------------------------------------------------------


def test_a_release_on_an_IDLE_connection_commits_the_stamp(db, session):
    sid = _company(db, "AAA")
    w = session()
    lock = acquire_company_lock(w, sid, wait_s=0)
    assert w.info.transaction_status == TransactionStatus.IDLE
    lock.release()
    assert w.info.transaction_status == TransactionStatus.IDLE  # still transaction-neutral
    assert _seq(db, sid) is not None and _rows(db) == 1  # visible from ANOTHER session: committed
    acquire_company_lock(session(), sid, wait_s=0).release()  # and the company is free


def test_a_release_inside_the_callers_OPEN_transaction_leaves_it_open(db, session):
    """The stamp rides a SAVEPOINT: the caller's transaction stays open, its uncommitted write stays
    uncommitted, and the stamp lands with the caller's own commit."""
    sid = _company(db, "AAA")
    w = session()
    lock = acquire_company_lock(w, sid, wait_s=0)
    with w.cursor() as cur:
        cur.execute("UPDATE security_master SET name = 'written' WHERE id = %s", (sid,))
    lock.release()
    assert w.info.transaction_status == TransactionStatus.INTRANS  # not ended
    with db.cursor() as cur:
        cur.execute("SELECT name FROM security_master WHERE id = %s", (sid,))
        assert cur.fetchone()["name"] is None  # the caller's write is still uncommitted
    db.rollback()
    assert _rows(db) == 0  # and so is the stamp
    acquire_company_lock(session(), sid, wait_s=0).release()  # the unlock itself happened
    w.commit()
    assert _rows(db) == 1 and _seq(db, sid) is not None
    with db.cursor() as cur:
        cur.execute("SELECT name FROM security_master WHERE id = %s", (sid,))
        assert cur.fetchone()["name"] == "written"
    db.rollback()


def test_the_release_restores_the_callers_lock_timeout(db, session):
    sid = _company(db, "AAA")
    w = session()
    lock = acquire_company_lock(w, sid, wait_s=0)
    with w.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout = '7s'")
    lock.release()
    with w.cursor() as cur:
        cur.execute("SELECT current_setting('lock_timeout') AS v")
        assert cur.fetchone()["v"] == "7s"
    w.rollback()


def test_a_FAILED_stamp_never_raises_never_blocks_the_unlock_and_keeps_the_callers_transaction(
    db, session, monkeypatch, caplog
):
    sid = _company(db, "AAA")

    def broken(conn, ids):
        conn.execute("SELECT 1 / 0")  # a real database error inside the stamp's savepoint

    monkeypatch.setattr(write_lock, "mark_changed", broken)
    w = session()
    lock = acquire_company_lock(w, sid, wait_s=0)
    with w.cursor() as cur:
        cur.execute("UPDATE security_master SET name = 'kept' WHERE id = %s", (sid,))
    with caplog.at_level(logging.WARNING, logger="alphadeck.write_lock"):
        lock.release()  # does not raise
    assert any(str(sid) in r.getMessage() and "stale" in r.getMessage() for r in caplog.records)
    assert w.info.transaction_status == TransactionStatus.INTRANS  # not poisoned, not ended
    acquire_company_lock(session(), sid, wait_s=0).release()  # the unlock still happened
    w.commit()  # the caller's own write still commits
    with db.cursor() as cur:
        cur.execute("SELECT name FROM security_master WHERE id = %s", (sid,))
        assert cur.fetchone()["name"] == "kept"
    db.rollback()
    assert _rows(db) == 0  # and no stamp landed


def test_a_failed_stamp_on_an_idle_connection_leaves_it_idle(db, session, monkeypatch):
    sid = _company(db, "AAA")

    def broken(conn, ids):
        raise RuntimeError("no stamp today")

    monkeypatch.setattr(write_lock, "mark_changed", broken)
    w = session()
    lock = acquire_company_lock(w, sid, wait_s=0)
    lock.release()
    assert w.info.transaction_status == TransactionStatus.IDLE
    acquire_company_lock(session(), sid, wait_s=0).release()


def test_a_SECOND_release_does_not_stamp_again(db, session):
    sid = _company(db, "AAA")
    w = session()
    lock = acquire_company_lock(w, sid, wait_s=0)
    lock.release()
    once = _seq(db, sid)
    lock.release()
    with lock:  # the context manager's exit is a release too
        pass
    assert _seq(db, sid) == once and _rows(db) == 1


def test_a_release_on_a_CLOSED_connection_stamps_nothing(db, session):
    sid = _company(db, "AAA")
    w = session()
    lock = acquire_company_lock(w, sid, wait_s=0)
    w.close()
    lock.release()
    assert _rows(db) == 0


def test_a_release_out_of_an_ABORTED_transaction_still_stamps(db, session):
    sid = _company(db, "AAA")
    w = session()
    lock = acquire_company_lock(w, sid, wait_s=0)
    with pytest.raises(Exception):
        w.execute("SELECT 1 / 0")
    assert w.info.transaction_status == TransactionStatus.INERROR
    lock.release()
    assert w.info.transaction_status == TransactionStatus.IDLE
    assert _rows(db) == 1
