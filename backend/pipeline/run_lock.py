"""C1 — the CROSS-PROCESS run lock: one call-of-record writer at a time.

``record_if_changed`` is read-compare-append with no lock (the ``calls`` log is immutable and its
``(thesis_id, asof)`` index is non-unique, so an UPSERT is impossible). Two passes that overlap both read
"no row yet / a different row" and both append — MEASURED: real overlaps on 2026-09-01 and 2026-09-09
(the sidecar's late pass and an Admin "Run daily now" ran concurrently; 17 rows for one night across 12
theses). The Admin's in-process single slot (``pipeline/daily_job.py``) could never see the sidecar's run
in its own container, and the sidecar could never see the Admin's. This module is the guard both share.

THE MECHANISM — a Postgres SESSION-LEVEL advisory lock, held on a DEDICATED connection for the whole pass:

- **Session-level, never transaction-level.** ``pg_try_advisory_lock`` is held by the SESSION until it is
  explicitly released or the session ends. So a pass that is killed (``docker kill``, an OOM, a WSL VM
  death — the 10-12 shape) loses its connection and Postgres releases the lock with it: a crash can never
  leave a stale lock behind to block the next night. No lock file, no PID file, no TTL to tune.
- **A dedicated connection, in AUTOCOMMIT.** The lock must outlive every per-thesis commit/rollback of the
  pass's working connections, and an autocommit session holds no snapshot and is never "idle in
  transaction" (which would pin ``xmin`` for the whole pass and could be reaped by
  ``idle_in_transaction_session_timeout``).
- **TRY, never wait.** ``pg_try_advisory_lock`` returns at once. A second pass does not queue behind the
  first (a queued pass would run the whole night again the moment the first finished): it raises
  ``PassAlreadyRunning`` with the holder's identity, prints one loud line, writes nothing, and exits with
  its own code (``EXIT_ALREADY_RUNNING``) — see ``pipeline.daily``'s exit-code contract.
- **Scoped to the database.** Postgres keys advisory locks on (database, key), so prod and dev (two
  databases on two servers anyway) and every per-worker test DB are isolated by construction.

WHO TAKES IT: every writer of the call-of-record — ``pipeline.daily.run_daily_pass`` (the nightly pass,
its retries and catch-ups, the Admin "Run daily now") and ``pipeline.backfill`` (it writes ``calls`` rows
too). The Workbench's on-promote ingest does NOT (it writes facts, never calls; out of scope for C1).

LAYERING: this module imports only ``db.session`` + psycopg, so ``pipeline.backfill`` (whose import guard
forbids ``pipeline.daily``, ``notify``, ``ingest`` …) can take the same lock.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import psycopg

from db.session import connect

_log = logging.getLogger("alphadeck.run_lock")

# The advisory key — the TWO-int form, (namespace, lock id). The namespace is "ADK" (0x41444B) so the key
# reads as ours in ``pg_locks`` (classid = 4277323, objid = 1) and cannot collide with a future lock that
# picks a small integer. One id for the whole call-of-record writer set: the nightly pass and the backfill
# exclude EACH OTHER, which is the point (both write ``calls``).
LOCK_NAMESPACE = 0x41444B
LOCK_ID_CALL_OF_RECORD = 1

# The exit code a CLI uses when another process holds the lock (EX_TEMPFAIL, sysexits.h: "a temporary
# failure; the user is invited to retry"). DISTINCT from 1 ("this night has no record") on purpose: this
# invocation did not run at all, and the night's record is the RUNNING pass's to make.
EXIT_ALREADY_RUNNING = 75

# What the holder's ``application_name`` starts with, so ``pg_stat_activity`` (and the refusal message)
# says which kind of pass holds the lock: ``alphadeck-pass:cron`` / ``:manual`` / ``:backfill``.
_APP_PREFIX = "alphadeck-pass:"


class PassAlreadyRunning(RuntimeError):
    """Another process holds the call-of-record lock. ``holder`` is a plain-English description of who
    (pid, application name, since when) — or ``None`` when it could not be read (the refusal stands
    regardless: the lock said no)."""

    def __init__(self, holder: str | None) -> None:
        self.holder = holder
        who = f" — held by {holder}" if holder else ""
        super().__init__(f"another daily pass or backfill is already running{who}")


@dataclass
class PassLock:
    """A HELD call-of-record lock. ``release()`` is idempotent and fail-open (closing the connection
    releases a session lock anyway, so the explicit unlock is belt-and-suspenders). A context manager.
    """

    conn: psycopg.Connection
    holder: str
    _released: bool = field(default=False, repr=False)

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        try:
            self.conn.execute(
                "SELECT pg_advisory_unlock(%s, %s)", (LOCK_NAMESPACE, LOCK_ID_CALL_OF_RECORD)
            )
        except Exception:  # noqa: BLE001 — the close below releases a session lock regardless
            _log.warning("run lock: explicit unlock failed (closing the session)", exc_info=True)
        finally:
            try:
                self.conn.close()
            except (
                Exception
            ):  # noqa: BLE001 — a close that fails leaves a dead session; Postgres reaps it
                _log.warning("run lock: closing the lock session failed", exc_info=True)

    def __enter__(self) -> PassLock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def describe_holder(conn: psycopg.Connection) -> str | None:
    """Who holds the call-of-record lock right now, as a plain-English line — or ``None`` when nobody
    does (or it cannot be read). READ-ONLY (``pg_locks`` × ``pg_stat_activity``); never raises. The
    two-int advisory form shows in ``pg_locks`` as ``classid`` = key1, ``objid`` = key2, ``objsubid`` = 2.
    """
    try:
        row = conn.execute(
            """SELECT a.pid, a.application_name, a.backend_start
                 FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
                WHERE l.locktype = 'advisory' AND l.granted
                  AND l.classid = %s AND l.objid = %s AND l.objsubid = 2
                  AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
                LIMIT 1""",
            (LOCK_NAMESPACE, LOCK_ID_CALL_OF_RECORD),
        ).fetchone()
    except Exception:  # noqa: BLE001 — a descriptive read never changes the refusal
        return None
    if row is None:
        return None
    app = row["application_name"] or "unnamed session"
    since = row["backend_start"].isoformat() if row["backend_start"] else "unknown"
    return f"pid {row['pid']} ({app}, connected {since})"


def acquire_pass_lock(*, holder: str) -> PassLock:
    """TRY to take the call-of-record lock on a fresh, dedicated, autocommit connection; return the held
    ``PassLock``. Raises ``PassAlreadyRunning`` (the connection closed, nothing written) when another
    session holds it. ``holder`` names this pass in ``pg_stat_activity`` (``alphadeck-pass:<holder>``) —
    the run kind (``cron`` / ``manual``) or ``backfill``.

    A DB that cannot be reached raises the connect error unchanged: a pass with no database can record
    nothing, and the caller's crash path (``run_daily_pass``'s H3 page, the CLI's non-zero exit) is the
    honest report — this function never fails open into an unlocked pass."""
    conn = connect()
    try:
        conn.autocommit = True
        conn.execute(
            "SELECT set_config('application_name', %s, false)", (f"{_APP_PREFIX}{holder}"[:63],)
        )
        got = conn.execute(
            "SELECT pg_try_advisory_lock(%s, %s) AS got", (LOCK_NAMESPACE, LOCK_ID_CALL_OF_RECORD)
        ).fetchone()["got"]
    except BaseException:
        conn.close()
        raise
    if not got:
        who = describe_holder(conn)
        conn.close()
        raise PassAlreadyRunning(who)
    return PassLock(conn=conn, holder=holder)


def probe_pass_lock() -> str | None:
    """Is the lock free RIGHT NOW? ``None`` = free (the probe took it and released it at once); else the
    holder description (``"another session"`` when it cannot be named). The Admin "Run daily now" uses
    this to answer 409 across processes before it starts a job; the job's own pass then takes the lock
    for real, so the probe is a courtesy, never the guard (a race between the two is lost VISIBLY — the
    job fails with ``PassAlreadyRunning``). Raises the connect error when the DB is unreachable."""
    try:
        lock = acquire_pass_lock(holder="probe")
    except PassAlreadyRunning as exc:
        return exc.holder or "another session"
    lock.release()
    return None
