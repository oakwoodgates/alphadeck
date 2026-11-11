"""The per-company change log (``db/change_stamp.py``, migration 0081: append-only), its prune and CLI, and the
company write lock's release appending to it (``db/write_lock.CompanyLock.release``), against the REAL test
Postgres.

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
from db.change_stamp import mark_changed, prune, stamp_fingerprint, stamp_map
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


def test_mark_changed_appends_a_row_per_change_and_the_map_reads_the_newest(db):
    a = _company(db, "AAA")
    assert _rows(db) == 0
    assert mark_changed(db, [a]) == 1
    db.commit()
    first = _seq(db, a)
    assert _rows(db) == 1 and first is not None
    assert mark_changed(db, [a]) == 1
    db.commit()
    assert _rows(db) == 2  # append-only: a second change is a second row, never an update
    assert _seq(db, a) > first  # the map reads the company's newest seq


def test_mark_changed_stamps_each_company_once_per_call(db):
    ids = [_company(db, f"T{i}") for i in range(4)]
    assert mark_changed(db, [ids[2], ids[0], ids[2], None, ids[3], ids[1]]) == 4
    db.commit()
    assert _rows(db) == 4 and set(stamp_map(db)) == set(ids)
    db.rollback()


def test_two_sessions_stamp_the_SAME_company_at_once_and_neither_waits(db, session):
    """A stamp is an INSERT into an append-only log: it shares no row lock, so a second stamp of a company
    whose first stamp is still uncommitted returns at once (here under a 1 s statement timeout, which a
    blocked statement would hit). A rolled-back stamp hides nothing the other committed."""
    a = _company(db, "AAA")
    first, second = session(), session()
    mark_changed(first, [a])  # held open, uncommitted
    with second.cursor() as cur:
        cur.execute("SET statement_timeout = '1s'")
    assert mark_changed(second, [a]) == 1  # returns immediately: no wait
    second.commit()
    first.rollback()  # the other transaction rolls back...
    assert _rows(db) == 1 and _seq(db, a) is not None  # ...and the committed stamp still stands


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
    # two rows now: the probe's own release above stamped one, the caller's commit landed the other
    assert _rows(db) == 2 and _seq(db, sid) is not None
    with db.cursor() as cur:
        cur.execute("SELECT name FROM security_master WHERE id = %s", (sid,))
        assert cur.fetchone()["name"] == "written"
    db.rollback()


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


# --- the prune and its CLI ----------------------------------------------------------------------------------


def _age(conn, sid, hours: float) -> None:
    """Back-date ``sid``'s log rows by ``hours`` (the test's way to make a row old; bookkeeping only)."""
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE security_change SET changed_at = now() - make_interval(secs => %s) "
            "WHERE security_id = %s",
            (hours * 3600, sid),
        )
    conn.commit()


def test_prune_deletes_only_the_rows_older_than_the_cut(db):
    old, edge, fresh = _company(db, "OLD"), _company(db, "EDGE"), _company(db, "NEW")
    mark_changed(db, [old, edge, fresh])
    db.commit()
    _age(db, old, 30)
    _age(db, edge, 23)
    assert prune(db, 24) == 1
    db.commit()
    assert set(stamp_map(db)) == {edge, fresh} and _rows(db) == 2
    db.rollback()
    with pytest.raises(ValueError):
        prune(db, -1)


def test_a_cache_synced_before_a_prune_serves_correctly_after_it(db, monkeypatch):
    """The prune lowers the fingerprint: the cache reads it as a move, the pruned company reads as gone and only
    its pieces drop; the surviving company's piece is kept and the next page shows the stored data.
    """
    from dashboards import cache as cache_mod
    from dashboards.cache import DashboardCache, get_piece, put_piece

    monkeypatch.setattr(cache_mod, "latest_run_identity", lambda: ("s", "finished", "f"))
    old, kept = _company(db, "OLD"), _company(db, "KEEP")
    mark_changed(db, [old, kept])
    db.commit()
    _age(db, old, 30)
    reads: list = []

    def page():
        out = {}
        for sid in (old, kept):
            found, value = get_piece("bars", sid, date(2026, 6, 1), "public", None)
            if not found:
                reads.append(sid)
                with db.cursor() as cur:
                    cur.execute("SELECT ticker FROM security_master WHERE id = %s", (sid,))
                    value = cur.fetchone()["ticker"]
                put_piece("bars", sid, date(2026, 6, 1), "public", None, value)
            out[sid] = value
        return out

    cache = DashboardCache()
    ws = DEFAULT_TENANT_ID
    assert cache.get(db, "p", (), date(2026, 6, 1), page, workspace=ws).value == {
        old: "OLD",
        kept: "KEEP",
    }
    assert prune(db, 24) == 1
    db.commit()
    again = cache.get(db, "p", (), date(2026, 6, 1), page, workspace=ws)
    assert again.status == "computed" and again.value == {old: "OLD", kept: "KEEP"}
    assert reads == [old, kept, old]  # only the pruned company was read again
    assert cache.get(db, "p", (), date(2026, 6, 1), page, workspace=ws).status == "hit"


def test_the_prune_CLI_commits_and_prints_one_line(db, capsys):
    from db import change_stamp

    a = _company(db, "AAA")
    mark_changed(db, [a])
    db.commit()
    _age(db, a, 48)
    assert change_stamp.main(["--prune"]) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out == ["change log: pruned 1 row(s) older than 24 h"]
    assert _rows(db) == 0  # committed: visible from another session
    assert change_stamp.main(["--prune", "--older-than-hours", "1"]) == 0
    assert capsys.readouterr().out.strip() == "change log: pruned 0 row(s) older than 1 h"
    with pytest.raises(SystemExit) as nothing:
        change_stamp.main([])
    assert nothing.value.code == 2
