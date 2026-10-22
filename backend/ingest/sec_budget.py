"""X2 — THE GLOBAL SEC BUDGET: one request budget shared by every process that talks to SEC.

THE GAP. SEC's fair-access ceiling is 10 requests/second per egress IP, and EDGAR etiquette is a correctness
requirement here (``docs/DATA_SOURCES.md`` §EDGAR etiquette). Each ``EdgarClient`` used to carry only its own
in-process 8 req/s ``RateLimiter``: the cron's nightly pass, the backend's Workbench discovery fan-out and any
CLI each ran a separate 8/s, so two of them at once could exceed 10/s. The nightly now fetches concurrently
(``pipeline/fetch_ahead.py``), so the overlap is no longer hypothetical.

THE MECHANISM — the simplest that GUARANTEES the cap: ONE Postgres row (``sec_request_budget``, migration
0057) holding ``next_slot``, the earliest instant the next SEC request may start. Every live SEC request
reserves its own slot with ONE atomic upsert (``GREATEST(next_slot, now()) + 1/rate``, returning the slot),
then sleeps until it, outside the database. The row lock orders concurrent reservations, so ANY two reserved
slots are at least ``1/rate`` apart whichever processes took them: the cap holds by construction, with no
agreement needed between processes. One round trip (~1 ms) per SEC request; ~9 a second at most.

Considered and rejected:
- A STATIC SPLIT (e.g. cron 5/s + backend 4/s, documented). No coordination, but it guarantees nothing the
  moment a third process talks to SEC — a CLI ingest, the Admin "Run daily now" child, a second web worker —
  and it idles half the budget at 22:30, when the Workbench is quiet.
- A FILE lock on the shared ``/data`` volume. It does not reach a host-side CLI, and advisory file locks are
  unreliable across Docker Desktop's Windows bind mounts. Postgres is the one thing every writer already shares
  (W1's company locks live there too).

THE FAIR SHARE. Reservation is FIFO, so on its own a lane with more threads (discovery fans out 10) would take
proportionally more slots. Each process LANE — ``recurring`` (a recurring-dial client: the nightly pass, the
SPAC radar, the shell sweep, the successor scan) and ``interactive`` (everything else: the Workbench, discovery,
the on-promote ingest, a CLI) — may hold at most ``LANE_DEPTH`` reservations that are not yet due (a
process-local semaphore held from the reservation until its slot arrives). With both lanes busy the FIFO then
holds at most ``LANE_DEPTH`` slots of each and they alternate: each lane gets half the budget. A lane alone
takes all of it (its next reservation is made as its previous slot arrives, one interval later). Neither lane
can starve the other: a newcomer waits at most ``LANE_DEPTH`` intervals (~0.24 s) behind the other's queue.
The share is per (process, lane) — one process per lane in practice (the cron's pass, the backend).

FAIL-SAFE, toward politeness. If Postgres cannot be reached (or the statement times out), the request still
goes, behind a process-local limiter at HALF the global rate (the fair share assuming one other process),
logged once per outage and counted on the pass meter (``sec_budget_fallback``); the database is re-tried
after ``FALLBACK_RETRY_S``. A budget fault never fails a fetch.

RESIDUAL (documented, accepted): the budget is per DATABASE. The dev stack (``alphadeck_dev``) and prod run on
one host today and share its IP but not a database, so a dev discovery during the prod nightly can still push
the IP past 10/s; the VPS move (X10) gives prod its own IP. Forks never go live to SEC.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta

import psycopg

from ingest.http import RateLimiter
from ingest.instrument import note

_log = logging.getLogger("alphadeck.sec_budget")

LANE_RECURRING = "recurring"
LANE_INTERACTIVE = "interactive"

# How many not-yet-due reservations one (process, lane) may hold. 1 already lets a lane alone run at the full
# rate (each reservation is made as the previous slot arrives); 2 absorbs a thread's wake-up jitter without
# letting a lane queue more than ~0.24 s ahead of the other.
LANE_DEPTH = 2
# After a database fault, how long the process stays on the local fallback before re-trying Postgres.
FALLBACK_RETRY_S = 30.0
# A reservation is one indexed upsert; anything slower than this is a sick database — fall back, don't hang.
_STATEMENT_TIMEOUT_MS = 5000

# ONE statement, atomic: the upsert re-creates a missing row, and the row lock orders concurrent callers.
# On the INSERT path the slot is now(); on the UPDATE path it is GREATEST(old next_slot, now()).
_RESERVE_SQL = (
    "INSERT INTO sec_request_budget AS b (id, next_slot) VALUES (1, now() + %(step)s) "
    "ON CONFLICT (id) DO UPDATE SET next_slot = GREATEST(b.next_slot, now()) + %(step)s "
    "RETURNING b.next_slot - %(step)s AS slot, now() AS db_now"
)
_PENALIZE_SQL = (
    "INSERT INTO sec_request_budget AS b (id, next_slot) VALUES (1, now() + %(pause)s) "
    "ON CONFLICT (id) DO UPDATE SET next_slot = GREATEST(b.next_slot, now() + %(pause)s)"
)


def _default_connect() -> psycopg.Connection:
    from db.session import database_url  # read late: the suite points DATABASE_URL at its test DB

    conn = psycopg.connect(
        database_url(),
        autocommit=True,  # each reservation is its own transaction: the row lock is held ~1 ms
        connect_timeout=5,
        application_name="alphadeck-sec-budget",
    )
    conn.execute(f"SET statement_timeout = {_STATEMENT_TIMEOUT_MS}")
    return conn


class BudgetStore:
    """The process's budget connection: ONE dedicated autocommit session, never a writer's, used by one thread
    at a time under a mutex (the reservation is a single ~1 ms statement, so callers never queue on it for
    long). A fault closes the session; the next call opens a fresh one."""

    def __init__(self, connect: Callable[[], psycopg.Connection] | None = None) -> None:
        self._connect = connect or _default_connect
        self._conn: psycopg.Connection | None = None
        self._mutex = threading.Lock()

    def _run(self, sql: str, params: dict, *, fetch: bool) -> tuple | None:
        with self._mutex:
            try:
                if self._conn is None or self._conn.closed:
                    self._conn = self._connect()
                cur = self._conn.execute(sql, params)
                row = cur.fetchone() if fetch else None
            except Exception:
                self._close_locked()
                raise
        return tuple(row.values()) if isinstance(row, dict) else row

    def reserve(self, step_s: float) -> tuple[datetime, datetime]:
        """Reserve the next slot ``step_s`` after the previous one; returns ``(slot, the database's now)``."""
        row = self._run(_RESERVE_SQL, {"step": timedelta(seconds=step_s)}, fetch=True)
        if row is None:  # pragma: no cover — an upsert's RETURNING always yields the row
            raise RuntimeError("sec_request_budget: the reservation returned no row")
        return row[0], row[1]

    def penalize(self, seconds: float) -> None:
        """No slot is handed out for ``seconds`` from now — every process, every lane, pauses."""
        self._run(_PENALIZE_SQL, {"pause": timedelta(seconds=seconds)}, fetch=False)

    def _close_locked(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 — closing a broken session is best-effort
                pass

    def close(self) -> None:
        with self._mutex:
            self._close_locked()


class SecBudget:
    """One (process, lane)'s view of the global SEC budget. ``acquire`` before EVERY live SEC request (retries
    included — ``polite_get``'s ``pre``); ``penalize`` when SEC answers 429 / 503 (``on_throttle``).
    """

    def __init__(
        self,
        lane: str,
        *,
        rate_per_sec: float | None = None,
        store: BudgetStore | None = None,
        depth: int = LANE_DEPTH,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if rate_per_sec is None:
            from domain.settings import get_settings

            rate_per_sec = get_settings().sec_global_rate_per_sec
        if rate_per_sec <= 0:
            raise ValueError("the SEC budget's rate must be positive")
        self.lane = lane
        self.rate_per_sec = float(rate_per_sec)
        self._store = store if store is not None else shared_store()
        self._slots = threading.BoundedSemaphore(depth)
        self._sleep = sleep
        self._fallback = RateLimiter(self.rate_per_sec / 2)
        self._down_until = 0.0
        self._state = threading.Lock()
        self._degraded = False

    @property
    def degraded(self) -> bool:
        """Is this lane on the local fallback right now (the database could not be reached)?"""
        return self._degraded

    def acquire(self) -> datetime | None:
        """Wait for this request's slot. Returns the reserved slot (the database's clock), or ``None`` when the
        local fallback paced it. Never raises for a budget fault."""
        # the fair share: at most LANE_DEPTH not-yet-due reservations per (process, lane)
        with self._slots:
            reserved = self._reserve()
            if reserved is None:
                note(
                    "sec_budget_fallback"
                )  # X5 — a request paced by the local fallback, not the budget
                self._fallback.acquire()
                return None
            slot, db_now = reserved
            wait = (slot - db_now).total_seconds()
            if wait > 0:
                note("rate_wait_s", wait)  # X5 — the budget's share of the leg's time
                self._sleep(wait)
            return slot

    def _reserve(self) -> tuple[datetime, datetime] | None:
        if time.monotonic() < self._down_until:
            return None
        try:
            reserved = self._store.reserve(1.0 / self.rate_per_sec)
        except Exception as exc:  # noqa: BLE001 — fail toward politeness, never a failed fetch
            self._fell_back(exc)
            return None
        if self._degraded:
            with self._state:
                if self._degraded:
                    self._degraded = False
                    _log.warning("SEC budget: database reachable again — the global budget is back")
        return reserved

    def _fell_back(self, exc: BaseException) -> None:
        with self._state:
            self._down_until = time.monotonic() + FALLBACK_RETRY_S
            if not self._degraded:
                self._degraded = True
                _log.warning(
                    "SEC budget: database unavailable (%s: %s) — pacing SEC requests locally at %.2f/s "
                    "(half the global %.2f/s) and re-trying in %.0f s",
                    type(exc).__name__,
                    " ".join(str(exc).split())[:160],
                    self.rate_per_sec / 2,
                    self.rate_per_sec,
                    FALLBACK_RETRY_S,
                )

    def penalize(self, seconds: float) -> None:
        """SEC said "too many" (429 / 503): push the GLOBAL next slot out by ``seconds``, so every process
        pauses, not only this one. Falls back to the local limiter when the database is unreachable.
        """
        if seconds <= 0:
            return
        self._fallback.penalize(seconds)
        if time.monotonic() < self._down_until:
            return
        try:
            self._store.penalize(seconds)
        except Exception as exc:  # noqa: BLE001
            self._fell_back(exc)


# --- the process-wide registry: one store (connection), one budget per lane --------------------------------

_STORE: BudgetStore | None = None
_BUDGETS: dict[str, SecBudget] = {}
_REGISTRY = threading.Lock()


def shared_store() -> BudgetStore:
    """The process's one budget connection (opened lazily, on the first live SEC request)."""
    global _STORE
    with _REGISTRY:
        if _STORE is None:
            _STORE = BudgetStore()
        return _STORE


def sec_budget(lane: str) -> SecBudget:
    """The process-wide budget for ``lane`` (``LANE_RECURRING`` / ``LANE_INTERACTIVE``)."""
    with _REGISTRY:
        budget = _BUDGETS.get(lane)
    if budget is None:
        created = SecBudget(lane)  # outside the registry lock: it takes the store through it
        with _REGISTRY:
            budget = _BUDGETS.setdefault(lane, created)
    return budget


def reset_sec_budgets() -> None:
    """Forget every budget and close the shared connection (the next request reconnects). Tests only."""
    global _STORE
    with _REGISTRY:
        store, _STORE = _STORE, None
        _BUDGETS.clear()
    if store is not None:
        store.close()
