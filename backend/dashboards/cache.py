"""The ONE shared dashboard cache (``docs/DASHBOARDS.md`` rule 7): in-process, used only by dashboards, and
cleared by "something new happened", never by a clock.

How a dashboard uses it (its router, after ``serve_view(asof)``)::

    from dashboards.cache import cached

    got = cached(conn, "rotation-map", (), asof, lambda: read_heavy_inputs(conn, asof, known_at, clock))
    got.value        # the computed value (shared between requests: treat it as read-only)
    got.status       # "hit" | "computed"
    got.compute_ms   # how long the compute took when it ran (a hit carries the stored figure)

The key is ``(dashboard slug, params tuple, asof)``; ``params`` holds whatever the cached value depends on
besides the as-of, and must be hashable. Cache the expensive READ, then derive the cheap views from it per
request, so one cold compute serves every grouping and toggle.

The rules it keeps:

- **Generation, not a clock.** Every lookup first reads a cheap generation token: the newest cron run
  artifact's identity (``started_at`` / ``status`` / ``finished_at``, so a pass both starting and finishing
  moves it), the newest basket snapshot (``max(taken_at)``, which moves on every roster edit), and the live
  (unarchived) cards' ids and names (archiving, restoring, creating or renaming a card moves it). A token
  that differs from the one the cache holds clears the WHOLE cache. There is no TTL: the run schedule will
  grow (market open, close, nightly, pre-market) and each new run is its own generation.
- **Bounded.** At most ``MAX_ENTRIES`` entries, least recently used evicted first.
- **Single-flight.** A per-key lock: two simultaneous cold requests for one key compute once; the second
  waits and is served the first one's value. The app runs one worker (``assert_single_worker`` in
  ``app/main.py``), so in-process is the whole cache.
- **Never stores across a generation.** The token is read again after a compute; a value computed while a
  run started or finished is returned to its caller but never stored.

Display only (rule 1): this module READS the run artifacts, ``basket_snapshot`` and ``thesis``; it writes
nothing anywhere but its own in-memory dict.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from dataclasses import dataclass
from datetime import date
from typing import Any, Generic, Literal, TypeVar

import psycopg

from pipeline.cron_run_log import list_run_logs

T = TypeVar("T")

MAX_ENTRIES = 64

CacheStatus = Literal["hit", "computed"]
Key = tuple[str, tuple[Hashable, ...], date]


@dataclass(frozen=True)
class Cached(Generic[T]):
    value: T
    status: CacheStatus
    compute_ms: int


# --- the generation token: three cheap reads ------------------------------------------------------------


def latest_run_identity() -> tuple[Any, ...] | None:
    """The newest cron run artifact's identity (``list_run_logs`` reads newest-first); ``None`` when no run
    has been recorded. ``status`` and ``finished_at`` ride along so a pass's start AND its finish each move
    the token (a value computed mid-pass must not outlive the pass)."""
    logs = list_run_logs(limit=1)
    if not logs:
        return None
    doc = logs[0]
    return (doc.get("started_at"), doc.get("status"), doc.get("finished_at"))


def latest_basket_snapshot(conn: psycopg.Connection) -> Any:
    """``max(taken_at)`` over ``basket_snapshot``: every roster edit appends one (an unchanged re-promote is
    deduped, and changes nothing a dashboard reads)."""
    with conn.cursor() as cur:
        cur.execute("SELECT max(taken_at) AS m FROM basket_snapshot")
        row = cur.fetchone()
    return row["m"] if row else None


def live_cards(conn: psycopg.Connection) -> tuple[tuple[Any, str], ...]:
    """The live (unarchived) cards, sorted by id, with their names (a rename shows on the next lookup)."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name FROM thesis WHERE archived_at IS NULL ORDER BY id")
        return tuple((r["id"], r["name"]) for r in cur.fetchall())


def generation_token(conn: psycopg.Connection) -> tuple[Any, ...]:
    return (latest_run_identity(), latest_basket_snapshot(conn), live_cards(conn))


# --- the cache --------------------------------------------------------------------------------------------


class DashboardCache:
    """See the module docstring. ``token`` is injectable for tests; production uses ``generation_token``."""

    def __init__(
        self,
        *,
        max_entries: int = MAX_ENTRIES,
        token: Callable[[psycopg.Connection], Hashable] | None = None,
    ) -> None:
        self._max = max_entries
        self._token_fn = token
        self._lock = threading.Lock()  # guards everything below
        self._generation: Hashable | None = None
        self._entries: OrderedDict[Key, tuple[Any, int]] = OrderedDict()
        self._inflight: dict[Key, threading.Lock] = {}

    def _token(self, conn: psycopg.Connection) -> Hashable:
        return (self._token_fn or generation_token)(conn)

    def _sync(self, token: Hashable) -> None:
        """Clear everything when the generation moved (caller holds ``_lock``)."""
        if token != self._generation:
            self._entries.clear()
            self._generation = token

    def _hit(self, key: Key) -> Cached[Any] | None:
        """The stored entry for ``key``, marked most recently used (caller holds ``_lock``)."""
        got = self._entries.get(key)
        if got is None:
            return None
        self._entries.move_to_end(key)
        return Cached(value=got[0], status="hit", compute_ms=got[1])

    def get(
        self,
        conn: psycopg.Connection,
        slug: str,
        params: tuple[Hashable, ...],
        asof: date,
        compute: Callable[[], T],
    ) -> Cached[T]:
        key: Key = (slug, tuple(params), asof)
        token = self._token(conn)
        with self._lock:
            self._sync(token)
            hit = self._hit(key)
            if hit is not None:
                return hit
            flight = self._inflight.setdefault(key, threading.Lock())
        with flight:
            try:
                with self._lock:
                    # another request may have computed it while this one waited on the flight lock
                    if self._generation == token:
                        hit = self._hit(key)
                        if hit is not None:
                            return hit
                started = time.perf_counter()
                value = compute()
                ms = round((time.perf_counter() - started) * 1000)
                after = self._token(conn)
                with self._lock:
                    if after == token:
                        self._sync(token)
                        self._entries[key] = (value, ms)
                        self._entries.move_to_end(key)
                        while len(self._entries) > self._max:
                            self._entries.popitem(last=False)
                return Cached(value=value, status="computed", compute_ms=ms)
            finally:
                with self._lock:
                    if self._inflight.get(key) is flight:
                        del self._inflight[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._generation = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


# The process-wide instance every dashboard shares.
CACHE = DashboardCache()


def cached(
    conn: psycopg.Connection,
    slug: str,
    params: tuple[Hashable, ...],
    asof: date,
    compute: Callable[[], T],
) -> Cached[T]:
    """Look ``(slug, params, asof)`` up in the shared cache, computing (once) on a miss."""
    return CACHE.get(conn, slug, params, asof, compute)
