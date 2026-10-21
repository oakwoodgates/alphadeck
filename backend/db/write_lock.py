"""W1 — the PER-COMPANY write lock: one fact writer per company at a time, across processes.

THE GAP. Every fact ingest is read-compare-append: read what is stored (``existing_accessions``,
``latest_bar_date``, the stored versions), then append what is new or changed. That is idempotent only when
nothing else appends for the same company in between. The nightly pass excludes OTHER passes with the
call-of-record lock (``pipeline/run_lock.py``, C1), but the Workbench's on-promote ingest, its per-name price
pull and a hand-run ``pipeline.ingest_thesis`` took no lock at all, so two of them walking the SAME company at
once could each read "not stored yet" and each append — a duplicate version that hides behind the as-of read's
dedup while the table grows (the "count the table" rule). MEASURED on prod 2026-10-21 (read-only): 1,519
identical-payload version pairs appended within 5 s of each other — 1,128 ``fact_price_eod`` bars across 217
securities, 354 ``fact_insider_txn`` rows across 33, 28 ``fact_corporate_event``, 6 ``fact_activist_stake``, 1
``fact_fund_shares`` — every one of them 2026-09-09 13:15-13:22 UTC, the overlap of two daily passes C1 now
refuses. The writer kinds this module adds to the guard (the Workbench, the CLI) left none yet; intraday open /
close runs will make the overlap routine, which is why the lock is per COMPANY, not per pass.

THE MECHANISM — two Postgres SESSION-level advisory locks, taken TOGETHER on the writer's OWN connection:

- **The company lock** ``(COMPANY_LOCK_NAMESPACE, company_key(security_id))``, EXCLUSIVE: one writer per
  company. Two writers on DIFFERENT companies never wait for each other — the nightly and a Workbench ingest
  run side by side and serialize only on a company both are writing at that moment.
- **The fact-writer gate** ``(FACT_GATE_NAMESPACE, FACT_GATE_ID)``, SHARED by every company writer and
  EXCLUSIVE for a fact REPAIR (``run_lock.acquire_repair_lock``): a repair reads a whole table up front and
  rewrites it, so it must exclude every company writer for its whole run, and a company writer must never
  start under a running repair.
- **Session-level, on the WORKING connection.** The ingest legs commit per leg; a session lock outlives those
  commits (and rollbacks — Postgres does not release a session advisory lock on rollback), so the company stays
  held across all of its legs, and a writer that dies loses its session and so its locks: nothing stale is
  ever left behind. Held on the SAME connection that writes, the lock is re-entrant (a nested acquisition on
  that session is granted at once), and the commit of a leg's writes always happens before the release.
- **TRY with a bounded poll, never a blocking wait.** ``pg_try_advisory_lock`` returns at once; a writer that
  finds a company held re-tries every ``poll_s`` until ``wait_s``, then raises ``CompanyLockBusy`` naming the
  holder. No session ever BLOCKS inside Postgres on these locks, so no deadlock involving them can form (the
  waits-for graph has no advisory edges), and every wait has a ceiling that ends in a visible outcome.
- **Transaction-neutral.** An attempt on an idle connection opens a transaction only for the lock call and
  ends it at once, so a writer waiting for a company never sits "idle in transaction".

THE LOCK ORDER (the deadlock rule; ``tests/db/test_write_lock.py`` pins it): **call-of-record lock -> the
fact-writer gate -> a company lock**, never the reverse. The nightly pass holds the call-of-record lock and
takes gate(shared)+company per company; a repair holds the call-of-record lock and then takes the gate
(exclusive); the Workbench and the CLI ingest take only gate(shared)+company. Nothing that holds a company lock
ever goes on to wait for the call-of-record lock or the gate in exclusive mode. And one writer unit uses ONE
connection for its locks and its writes: a process that held a company on connection A and then waited for
the same company on connection B would wait out its own lock (bounded, but pointless).

WHAT THE WAITS ARE (``domain.settings``):
- ``ingest_lock_wait_s`` (180 s) — a background or batch writer (the nightly, the Workbench on-promote job, the
  CLI). ``pipeline.ingest_thesis`` first tries every company WITHOUT waiting and DEFERS a held one to the end of
  its walk, so the wait is paid only after every free company is done; a company still held after the wait is
  NOT ingested by that unit (``NameResult.lock_busy`` — visible, never an error, never a dropped name).
- ``ingest_lock_request_wait_s`` (15 s) — an interactive request (the Workbench per-name price pull): a 409
  naming the holder, never a hung request.
- ``repair_lock_wait_s`` (60 s) — a repair waiting for the company writers to drain before it takes the gate.

LAYERING: this module imports only psycopg, so ``ingest/*`` and ``pipeline/*`` writers can both take it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from uuid import UUID

import psycopg
from psycopg.pq import TransactionStatus
from psycopg.rows import dict_row

_log = logging.getLogger("alphadeck.write_lock")

# The company locks' namespace — "ADC" (Alpha Deck Company), DISTINCT from the run-lock namespace so a company
# key can never equal the call-of-record lock's id. In ``pg_locks``: classid = 4277315, objid = the key as an
# unsigned 32-bit int, objsubid = 2 (the two-int form).
COMPANY_LOCK_NAMESPACE = 0x414443
# The fact-writer gate shares the run-lock namespace ("ADK", ``pipeline.run_lock.LOCK_NAMESPACE``): id 1 is the
# call-of-record lock, id 2 is this gate. Defined here (not imported) so this module stays below ``pipeline``.
FACT_GATE_NAMESPACE = 0x41444B
FACT_GATE_ID = 2

# How often a waiting writer re-tries. Small against every wait above, large against a lock round trip.
DEFAULT_POLL_S = 0.5


def company_key(security_id: UUID) -> int:
    """The company's advisory key: the first 32 bits of its security id as a SIGNED int4 (the two-int form's
    second argument). Deterministic in every process. Two companies sharing a key (p = 2^-32 per pair) only
    serialize with each other — a wasted wait, never a missed one."""
    return int.from_bytes(security_id.bytes[:4], "big", signed=True)


def _as_oid(key: int) -> int:
    """``pg_locks.objid`` shows an int4 key as an unsigned oid."""
    return key & 0xFFFFFFFF


class CompanyLockBusy(RuntimeError):
    """Another writer held the company (or a repair held the fact-writer gate) for the whole bounded wait.
    ``holder`` is a plain-English description of who (pid, application name, since when), or ``None`` when it
    could not be read; ``waited_s`` how long this writer tried."""

    def __init__(
        self, security_id: UUID, holder: str | None, waited_s: float, *, gate: bool = False
    ) -> None:
        self.security_id = security_id
        self.holder = holder
        self.waited_s = waited_s
        self.gate = gate  # True = a REPAIR held the gate (every company), not this one company
        what = "a fact repair holds every company" if gate else "another writer holds this company"
        who = f" — {holder}" if holder else ""
        super().__init__(f"{what}{who} (waited {waited_s:.0f}s)")


@dataclass
class CompanyLock:
    """A HELD company lock (gate shared + company exclusive) on ``conn``'s session. ``release()`` is
    idempotent and fail-open (closing the session releases both anyway). Release it only AFTER the writes it
    guards are committed — a release before the commit reopens the very race it closes. A context manager.
    """

    conn: psycopg.Connection
    security_id: UUID
    waited_s: float = 0.0
    _released: bool = field(default=False, repr=False)

    @property
    def released(self) -> bool:
        return self._released

    def release(self) -> None:
        if self._released:
            return
        self._released = True
        conn = self.conn
        try:
            if conn.closed:
                return  # the session is gone, and its locks with it
            if conn.info.transaction_status == TransactionStatus.INERROR:
                # an aborted transaction refuses every statement; it can commit nothing, so ending it loses
                # nothing — and without it the unlock below could not run
                conn.rollback()
            was_idle = _is_idle(conn)
            conn.execute(
                "SELECT pg_advisory_unlock(%s, %s), pg_advisory_unlock_shared(%s, %s)",
                (
                    COMPANY_LOCK_NAMESPACE,
                    company_key(self.security_id),
                    FACT_GATE_NAMESPACE,
                    FACT_GATE_ID,
                ),
            )
            if was_idle:
                conn.rollback()  # end the transaction the unlock opened; the unlock itself stands
        except Exception:  # noqa: BLE001 — the session's end releases the lock regardless
            _log.warning(
                "company lock: explicit unlock failed for %s (it ends with the session)",
                self.security_id,
                exc_info=True,
            )

    def __enter__(self) -> CompanyLock:
        return self

    def __exit__(self, *exc: object) -> None:
        self.release()


def _is_idle(conn: psycopg.Connection) -> bool:
    """No transaction open (and not autocommit): an attempt opens one only for the lock call, so it may end
    it at once. An OPEN caller transaction is never ended here — that is the caller's to commit."""
    return not conn.autocommit and conn.info.transaction_status == TransactionStatus.IDLE


def _try_both(conn: psycopg.Connection, security_id: UUID) -> tuple[bool, bool]:
    """ONE attempt: the gate (shared) first, then the company (exclusive) — the lock order. Both or neither:
    a held gate with a busy company is given back at once. Returns ``(gate_ok, company_ok)``."""
    key = company_key(security_id)
    was_idle = _is_idle(conn)
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT pg_try_advisory_lock_shared(%s, %s) AS got",
                (FACT_GATE_NAMESPACE, FACT_GATE_ID),
            )
            gate_ok = bool(cur.fetchone()["got"])
            company_ok = False
            if gate_ok:
                cur.execute(
                    "SELECT pg_try_advisory_lock(%s, %s) AS got", (COMPANY_LOCK_NAMESPACE, key)
                )
                company_ok = bool(cur.fetchone()["got"])
                if not company_ok:
                    cur.execute(
                        "SELECT pg_advisory_unlock_shared(%s, %s)",
                        (FACT_GATE_NAMESPACE, FACT_GATE_ID),
                    )
    finally:
        if was_idle and not conn.closed:
            conn.rollback()  # session locks survive a rollback; the attempt's transaction does not linger
    return gate_ok, company_ok


def acquire_company_lock(
    conn: psycopg.Connection,
    security_id: UUID,
    *,
    wait_s: float,
    poll_s: float = DEFAULT_POLL_S,
) -> CompanyLock:
    """Take ``security_id``'s company lock (and the fact-writer gate, shared) on ``conn``'s session, re-trying
    for up to ``wait_s`` seconds; return the held ``CompanyLock``. ``wait_s <= 0`` = exactly one attempt.
    Raises ``CompanyLockBusy`` (nothing held) when the wait runs out; ``waited_s`` on the result says how long
    it took. Re-entrant: a session that already holds the company is granted it again at once (release it once
    per acquisition)."""
    start = time.monotonic()
    while True:
        gate_ok, company_ok = _try_both(conn, security_id)
        if gate_ok and company_ok:
            return CompanyLock(conn, security_id, waited_s=time.monotonic() - start)
        waited = time.monotonic() - start
        if waited >= wait_s:
            if not gate_ok:
                raise CompanyLockBusy(security_id, describe_gate_holder(conn), waited, gate=True)
            raise CompanyLockBusy(security_id, describe_company_holder(conn, security_id), waited)
        time.sleep(max(0.0, min(poll_s, wait_s - waited)))


_HOLDER_SQL = """SELECT a.pid, a.application_name, a.backend_start, l.mode
                   FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
                  WHERE l.locktype = 'advisory' AND l.granted
                    AND l.classid = %s AND l.objid = %s AND l.objsubid = 2
                    AND l.database = (SELECT oid FROM pg_database WHERE datname = current_database())
                    AND l.pid <> pg_backend_pid() {mode}
                  ORDER BY a.backend_start LIMIT 1"""


def _describe(conn: psycopg.Connection, classid: int, objid: int, mode: str = "") -> str | None:
    was_idle = _is_idle(conn)
    try:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_HOLDER_SQL.format(mode=mode), (classid, objid))
            row = cur.fetchone()
    except Exception:  # noqa: BLE001 — a descriptive read never changes the outcome
        return None
    finally:
        if was_idle and not conn.closed:
            try:
                conn.rollback()
            except Exception:  # noqa: BLE001
                pass
    if row is None:
        return None
    app = row["application_name"] or "unnamed session"
    since = row["backend_start"].isoformat() if row["backend_start"] else "unknown"
    return f"pid {row['pid']} ({app}, connected {since})"


def describe_company_holder(conn: psycopg.Connection, security_id: UUID) -> str | None:
    """Who holds ``security_id``'s company lock right now (another session), as a plain-English line — or
    ``None`` when nobody does or it cannot be read. READ-ONLY (``pg_locks`` x ``pg_stat_activity``).
    """
    return _describe(conn, COMPANY_LOCK_NAMESPACE, _as_oid(company_key(security_id)))


def describe_gate_holder(conn: psycopg.Connection) -> str | None:
    """Who holds the fact-writer gate EXCLUSIVELY (a repair) right now, or ``None``. READ-ONLY."""
    return _describe(conn, FACT_GATE_NAMESPACE, FACT_GATE_ID, "AND l.mode = 'ExclusiveLock'")


def describe_gate_writers(conn: psycopg.Connection) -> str | None:
    """Who holds the fact-writer gate SHARED (a company writer mid-company) right now — the first of them —
    or ``None``. READ-ONLY. What a repair waiting for the gate names when the writers do not drain.
    """
    return _describe(conn, FACT_GATE_NAMESPACE, FACT_GATE_ID, "AND l.mode = 'ShareLock'")
