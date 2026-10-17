"""C1 — the cross-process call-of-record lock, against the REAL test Postgres.

The lock is a session-level advisory lock, so everything that matters about it is a property of real
Postgres sessions: a second session is refused, closing (or killing) the holder releases it, and two
overlapping passes leave exactly ONE set of rows. Every row assertion here COUNTS THE TABLE — the as-of read
dedups, so a duplicate append from an overlap would hide behind a correct-looking read.

Cross-PROCESS holders are real OS processes (``subprocess`` running the same ``acquire_pass_lock``), not a
second connection in this process, because "another process holds it" is the case the in-process Admin slot
could never see.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time as _time
import uuid
from datetime import date, datetime, time
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from db.session import DEFAULT_TENANT_ID
from pipeline import backfill, cron_run_log, daily, run_lock
from pipeline.run_lock import (
    EXIT_ALREADY_RUNNING,
    PassAlreadyRunning,
    acquire_pass_lock,
    probe_pass_lock,
)

_ASOF = date(2026, 6, 10)
_BACKEND = Path(__file__).resolve().parents[2]
_NY = ZoneInfo("America/New_York")


def _thesis(db, name):
    tid = uuid.uuid4()
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO thesis (id, tenant_id, name, narrative) VALUES (%s, %s, %s, %s)",
            (tid, DEFAULT_TENANT_ID, name, "n"),
        )
    db.commit()
    return tid


def _count_calls(db) -> int:
    db.rollback()  # a fresh snapshot — rows another session committed must be visible
    with db.cursor() as cur:
        cur.execute("SELECT count(*) AS n FROM calls")
        return cur.fetchone()["n"]


class _Recorder:
    def __init__(self):
        self.health = []

    def notify(self, event):
        pass

    def notify_health(self, event):
        self.health.append(event)

    def notify_spac_status(self, event):
        pass


def _offline_pass(monkeypatch, tmp_path, *, ingest=None):
    """Make a LIVE pass network-free while keeping every DB touch real: the refresh / sweep / radar legs
    are no-ops, the per-thesis ingest is ``ingest`` (default: nothing to ingest), and the run artifacts
    land in ``tmp_path``. The lock, the loop, ``record_if_changed`` and the self-audit all run for real.
    """
    monkeypatch.setattr(daily, "ingest_thesis", ingest or (lambda *a, **k: []))
    monkeypatch.setattr("pipeline.ingest_benchmarks.ingest_benchmarks", lambda conn, **k: [])
    monkeypatch.setattr("pipeline.ingest_fundamentals.ingest_fundamentals", lambda conn, **k: [])
    monkeypatch.setattr(
        "radar.shell_sweep.run_shell_sweep",
        lambda conn, **k: SimpleNamespace(summary="stub", errors=[]),
    )
    monkeypatch.setattr(
        "radar.spac.run_spac_radar", lambda conn, **k: SimpleNamespace(summary="stub", errors=[])
    )
    monkeypatch.setattr(cron_run_log, "_DEFAULT_CRON_RUNS", tmp_path)
    # the CLI resolves its own notifier — never a real Slack push from a test
    monkeypatch.setattr(daily, "get_notifier", lambda: _Recorder())


def _artifacts(tmp_path) -> list[Path]:
    return sorted(tmp_path.glob("*.json"))


def _status(path: Path) -> str:
    return cron_run_log.run_status(json.loads(path.read_text(encoding="utf-8")))


def _on_the_night(monkeypatch, asof: date) -> None:
    """Pin the CLI's market clock to 23:00 ET on ``asof`` — a hand-typed pass for TONIGHT, which C3 lets
    through (a past ``--asof`` is refused before the lock is ever tried)."""
    monkeypatch.setattr(
        daily, "market_now", lambda: datetime.combine(asof, time(23, 0), tzinfo=_NY)
    )


# --- the lock itself --------------------------------------------------------------------------------


def test_a_second_session_is_REFUSED_and_told_who_holds_it(db):
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(PassAlreadyRunning) as exc:
            acquire_pass_lock(holder="manual")
        # the refusal names the holder: its pid + the application name the lock session set
        assert exc.value.holder and "alphadeck-pass:cron" in exc.value.holder
        assert "already running" in str(exc.value)
        assert probe_pass_lock() is not None  # the Admin probe sees the same holder
    finally:
        held.release()
    # released -> free again (the probe takes and drops it)
    assert probe_pass_lock() is None


def test_release_is_IDEMPOTENT_and_a_context_manager(db):
    with acquire_pass_lock(holder="manual") as lock:
        assert probe_pass_lock() is not None
    assert lock.released
    lock.release()  # a second release is a no-op, never an error
    assert probe_pass_lock() is None


def test_CLOSING_the_holder_session_releases_the_lock_without_an_unlock(db):
    """Session-level means the lock dies with its session: a pass whose connection drops (a crash) can
    never leave a stale lock blocking the next night. Closed WITHOUT pg_advisory_unlock."""
    lock = acquire_pass_lock(holder="cron")
    lock.conn.close()  # the session ends; no explicit unlock ran
    again = acquire_pass_lock(holder="cron")  # must succeed — nothing stale left behind
    again.release()


def _spawn_holder(holder: str) -> subprocess.Popen:
    """A real second OS PROCESS that takes the lock and holds it until its stdin closes (or it is killed)."""
    code = (
        "import sys\n"
        "from pipeline.run_lock import acquire_pass_lock\n"
        "lock = acquire_pass_lock(holder=sys.argv[1])\n"
        "print('held', flush=True)\n"
        "sys.stdin.read()\n"
        "lock.release()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code, holder],
        cwd=_BACKEND,
        env={**os.environ},
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    line = proc.stdout.readline().strip()
    assert line == "held", f"holder process did not take the lock (got {line!r})"
    return proc


def _wait_free(timeout_s: float = 15.0) -> bool:
    end = _time.monotonic() + timeout_s
    while _time.monotonic() < end:
        if probe_pass_lock() is None:
            return True
        _time.sleep(0.2)
    return False


def test_a_KILLED_holder_process_releases_the_lock(db):
    """The 10-12 shape: the pass's process dies (no finally, no unlock). Postgres ends its session and the
    lock goes with it — the next pass is not blocked."""
    proc = _spawn_holder("cron")
    try:
        with pytest.raises(PassAlreadyRunning):
            acquire_pass_lock(holder="manual")
    finally:
        proc.kill()
        proc.wait(timeout=30)
    assert _wait_free(), "the killed holder's lock was never released"


# --- two passes, one record ---------------------------------------------------------------------------


def test_two_CONCURRENT_passes_leave_exactly_ONE_set_of_rows(db, monkeypatch, tmp_path, capsys):
    """THE headline. Pass A (the sidecar) holds the lock and is mid-loop; pass B (an Admin click, a hand
    `pipeline.daily`) starts meanwhile. B must refuse — no rows, no artifact, no page — and the table must
    hold exactly one call-of-record per thesis afterwards. COUNTED, never read through the dedup.

    M1: while A is mid-loop its own START artifact is on disk reading ``running`` (B added nothing beside
    it), and once A finishes that SAME file reads ``finished`` — one file per pass."""
    t1, t2 = _thesis(db, "lock-A"), _thesis(db, "lock-B")
    _on_the_night(monkeypatch, _ASOF)  # B's hand-typed CLI run is for tonight (C3 lets it through)
    a_inside = threading.Event()
    a_go = threading.Event()

    def _blocking_ingest(conn, thesis_id, **k):
        a_inside.set()  # A holds the lock by now (taken before the pre-legs)
        assert a_go.wait(timeout=60), "test never released pass A"
        return []

    _offline_pass(monkeypatch, tmp_path, ingest=_blocking_ingest)
    rec_a, rec_b = _Recorder(), _Recorder()
    errors: list[BaseException] = []

    def _pass_a():
        try:
            daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=rec_a, run_kind="cron")
        except BaseException as e:  # pragma: no cover — surfaced by the assert below
            errors.append(e)

    a = threading.Thread(target=_pass_a)
    a.start()
    try:
        assert a_inside.wait(timeout=60), "pass A never reached its loop"
        before = _count_calls(db)
        # B through the pass entry point: refused before anything runs
        with pytest.raises(PassAlreadyRunning):
            daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=rec_b, run_kind="manual")
        # B through the CLI: one loud line, the distinct exit code, nothing written
        with pytest.raises(SystemExit) as exc:
            daily.main(["--asof", _ASOF.isoformat()])
        assert exc.value.code == EXIT_ALREADY_RUNNING
        assert "ALREADY RUNNING" in capsys.readouterr().out
        assert _count_calls(db) == before  # B wrote no row
        # …and no artifact: the only file is A's own START artifact, still `running` (M1)
        mid = _artifacts(tmp_path)
        assert len(mid) == 1 and _status(mid[0]) == "running"
        assert rec_b.health == []  # a refusal is not a crash: nothing paged
    finally:
        a_go.set()
        a.join(timeout=120)
    assert not errors, errors
    # exactly ONE set: one call-of-record per thesis, one artifact (A's)
    with db.cursor() as cur:
        cur.execute(
            "SELECT thesis_id, count(*) AS n FROM calls WHERE thesis_id = ANY(%s) GROUP BY thesis_id",
            ([t1, t2],),
        )
        per = {r["thesis_id"]: r["n"] for r in cur.fetchall()}
    assert per == {t1: 1, t2: 1}
    done = _artifacts(tmp_path)
    assert done == mid and _status(done[0]) == "finished"  # the SAME file, finalized
    assert probe_pass_lock() is None  # A released it when it finished


def test_a_pass_RELEASES_the_lock_even_when_it_crashes(db, monkeypatch, tmp_path):
    _offline_pass(monkeypatch, tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("loop exploded")

    monkeypatch.setattr(daily, "run_daily", _boom)
    with pytest.raises(RuntimeError, match="loop exploded"):
        daily.run_daily_pass(asof=_ASOF, allow_live=True, notifier=_Recorder())
    assert probe_pass_lock() is None


def test_the_CLI_exits_75_while_ANOTHER_PROCESS_holds_the_lock(db, monkeypatch, tmp_path, capsys):
    """The sidecar's view: a scheduled `pipeline.daily` while a different process holds the lock exits 75
    (not 0 — it produced no record; not 1 — the night is not failed, it is being made) and writes nothing.
    """
    _thesis(db, "lock-cli")
    _offline_pass(monkeypatch, tmp_path)
    before = _count_calls(db)
    # the sidecar's scheduled argv, verbatim since C3 (`--late-ok`: its target is fixed before the sleep)
    sidecar = ["--run-kind", "cron", "--late-ok", "--asof", _ASOF.isoformat()]
    proc = _spawn_holder("manual")
    try:
        with pytest.raises(SystemExit) as exc:
            daily.main(sidecar)
        assert exc.value.code == EXIT_ALREADY_RUNNING == 75
        out = capsys.readouterr().out
        assert "ALREADY RUNNING" in out and "alphadeck-pass:manual" in out
    finally:
        proc.stdin.close()
        proc.wait(timeout=30)
    assert _count_calls(db) == before
    assert _artifacts(tmp_path) == []  # refused before the lock: not even a start artifact
    # and once the holder is gone the same command runs and records
    daily.main(sidecar)
    assert _count_calls(db) == before + 1


def test_the_BACKFILL_takes_the_same_lock(db, monkeypatch, tmp_path, capsys):
    """`pipeline.backfill` writes calls rows too: while a pass holds the lock it refuses (exit 75, no
    row, no artifact); a --dry-run writes nothing and so takes no lock."""
    _thesis(db, "lock-backfill")
    monkeypatch.setattr(backfill, "market_today", lambda: date(2026, 6, 12))
    before = _count_calls(db)
    held = acquire_pass_lock(holder="cron")
    try:
        with pytest.raises(SystemExit) as exc:
            backfill.main(["--asof", "2026-06-10", "--known-at", "2026-06-11T13:00:00Z"])
        assert exc.value.code == EXIT_ALREADY_RUNNING
        assert "ALREADY RUNNING" in capsys.readouterr().out
        assert _count_calls(db) == before
        # a dry run is still allowed — it writes nothing, so it needs no lock
        backfill.main(["--asof", "2026-06-10", "--known-at", "2026-06-11T13:00:00Z", "--dry-run"])
        assert _count_calls(db) == before
    finally:
        held.release()


def test_the_lock_key_is_the_documented_one():
    """The key is what an operator greps `pg_locks` for (classid 4277323 = 'ADK', objid 1)."""
    assert (run_lock.LOCK_NAMESPACE, run_lock.LOCK_ID_CALL_OF_RECORD) == (0x41444B, 1)
    assert run_lock.LOCK_NAMESPACE == 4277323
