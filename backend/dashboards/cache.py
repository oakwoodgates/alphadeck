"""The ONE shared dashboard cache (``docs/DASHBOARDS.md`` rule 7): in-process, used only by dashboards, and
cleared by "something new happened", never by a clock.

How a dashboard uses it (its router, after ``serve_view(asof)``)::

    from dashboards.cache import cached

    got = cached(
        conn, "rotation-map", (), asof, lambda: read_heavy_inputs(conn, asof, known_at, clock),
        workspace=workspace,  # the caller's (app.deps.get_workspace): part of the key, never optional
    )
    got.value        # the computed value (shared between requests: treat it as read-only)
    got.status       # "hit" | "computed"
    got.compute_ms   # how long the compute took when it ran (a hit carries the stored figure)

The key is ``(workspace, dashboard slug, params tuple, asof)``: the WORKSPACE is the caller's (``app.deps.
get_workspace``) and is REQUIRED, so a page computed for one workspace's cards can never be served to another
(auth PR 2, docs/TENANT_SCOPES.md). ``params`` holds whatever else the cached value depends on besides the as-of,
and must be hashable. Cache the expensive READ, then derive the cheap views from it per
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

**Pieces: one company's result, shared by every page.** Many pages read the same per-company inputs (a
company's price-reaching data flags, a display member's reading). A piece is one such value, keyed by
``(workspace, kind, security_id, asof, clock, known_at)``; ``kind`` names the input (``"price_flags"``,
``"member:<name>"``), ``known_at`` is the caller's (``None`` on the live view), and a stored ``None`` is a
real value. Pieces are only read and written while a page's compute runs inside ``get`` (a piece session,
held in a context variable, so code run outside ``cached()`` computes exactly as before and stores
nothing); the session's workspace is the page's own, so two workspaces never share one. A compute's new
pieces are staged and stored only where its page entry would be: a compute that raised, or one during
which the generation moved, stores none of them. Pieces clear with the page entries (a generation move,
``clear()``), and at most ``MAX_PIECE_GROUPS`` groups of ``(workspace, asof, clock, known_at)`` are kept,
the least recently used group evicted whole. The pieces' readers live in ``dashboards.baskets``.

Display only (rule 1): this module READS the run artifacts, ``basket_snapshot`` and ``thesis``; it writes
nothing anywhere but its own in-memory dict.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Generic, Literal, TypeVar
from uuid import UUID

import psycopg

from pipeline.cron_run_log import list_run_logs

T = TypeVar("T")

MAX_ENTRIES = 64
# piece groups kept, each one ``(workspace, asof, clock, known_at)``: the least recently used goes whole
MAX_PIECE_GROUPS = 3

CacheStatus = Literal["hit", "computed"]
Key = tuple[UUID, str, tuple[Hashable, ...], date]
PieceGroup = tuple[UUID, date, str, datetime | None]  # (workspace, asof, clock, known_at)
PieceSub = tuple[str, UUID]  # (kind, security_id)


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
        self._pieces: OrderedDict[PieceGroup, dict[PieceSub, Any]] = OrderedDict()

    def _token(self, conn: psycopg.Connection) -> Hashable:
        return (self._token_fn or generation_token)(conn)

    def _sync(self, token: Hashable) -> None:
        """Clear everything when the generation moved (caller holds ``_lock``)."""
        if token != self._generation:
            self._entries.clear()
            self._pieces.clear()
            self._generation = token

    def _piece(self, token: Hashable, group: PieceGroup, sub: PieceSub) -> tuple[bool, Any]:
        """``(found, value)`` for a stored piece, its group marked most recently used; a miss when the
        generation is no longer ``token`` (the one the asking compute started under)."""
        with self._lock:
            if self._generation != token:
                return False, None
            got = self._pieces.get(group)
            if got is None or sub not in got:
                return False, None
            self._pieces.move_to_end(group)
            return True, got[sub]

    def _store_pieces(self, staged: dict[tuple[PieceGroup, PieceSub], Any]) -> None:
        """Merge a finished compute's staged pieces (caller holds ``_lock`` and has synced the generation)."""
        for (group, sub), value in staged.items():
            got = self._pieces.get(group)
            if got is None:
                got = self._pieces[group] = {}
            got[sub] = value
            self._pieces.move_to_end(group)
        while len(self._pieces) > MAX_PIECE_GROUPS:
            self._pieces.popitem(last=False)

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
        *,
        workspace: UUID,
    ) -> Cached[T]:
        if not isinstance(workspace, UUID):
            raise TypeError(
                f"a dashboard cache key needs the caller's workspace id, not {workspace!r}"
            )
        key: Key = (workspace, slug, tuple(params), asof)
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
                session = _PieceSession(self, workspace, token)
                active = _SESSION.set(session)
                try:
                    value = compute()
                finally:
                    _SESSION.reset(active)  # a nested ``cached()`` hands the outer session back
                ms = round((time.perf_counter() - started) * 1000)
                after = self._token(conn)
                with self._lock:
                    if after == token:
                        self._sync(token)
                        self._entries[key] = (value, ms)
                        self._entries.move_to_end(key)
                        while len(self._entries) > self._max:
                            self._entries.popitem(last=False)
                        self._store_pieces(session.staged)
                return Cached(value=value, status="computed", compute_ms=ms)
            finally:
                with self._lock:
                    if self._inflight.get(key) is flight:
                        del self._inflight[key]

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._pieces.clear()
            self._generation = None

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def piece_count(self) -> int:
        """How many pieces are stored, across every group (for tests and diagnostics)."""
        with self._lock:
            return sum(len(g) for g in self._pieces.values())


# The process-wide instance every dashboard shares.
CACHE = DashboardCache()


# --- pieces: one company's result, shared by every page of a generation ------------------------------------


@dataclass
class _PieceSession:
    """The piece session of one running compute: its cache, the page's workspace, the generation token read
    before the compute, and the pieces it computed (staged until the page entry is stored)."""

    cache: DashboardCache
    workspace: UUID
    token: Hashable
    staged: dict[tuple[PieceGroup, PieceSub], Any] = field(default_factory=dict)


_SESSION: ContextVar[_PieceSession | None] = ContextVar("dashboard_piece_session", default=None)


def piece_workspace() -> UUID | None:
    """The workspace of the active piece session (the page being computed), ``None`` outside one."""
    session = _SESSION.get()
    return session.workspace if session is not None else None


def get_piece(
    kind: str, security_id: UUID, asof: date, clock: str, known_at: datetime | None
) -> tuple[bool, Any]:
    """``(found, value)`` for one company's piece: this compute's own staged pieces first, then the shared
    store. Always a miss outside a piece session, or once the generation moved under the compute."""
    session = _SESSION.get()
    if session is None:
        return False, None
    group: PieceGroup = (session.workspace, asof, clock, known_at)
    sub: PieceSub = (kind, security_id)
    staged_key = (group, sub)
    if staged_key in session.staged:
        return True, session.staged[staged_key]
    return session.cache._piece(session.token, group, sub)


def put_piece(
    kind: str, security_id: UUID, asof: date, clock: str, known_at: datetime | None, value: Any
) -> None:
    """Stage one company's piece in the active session (stored with the page entry); a no-op outside one."""
    session = _SESSION.get()
    if session is None:
        return
    session.staged[((session.workspace, asof, clock, known_at), (kind, security_id))] = value


def cached(
    conn: psycopg.Connection,
    slug: str,
    params: tuple[Hashable, ...],
    asof: date,
    compute: Callable[[], T],
    *,
    workspace: UUID,
) -> Cached[T]:
    """Look ``(workspace, slug, params, asof)`` up in the shared cache, computing (once) on a miss."""
    return CACHE.get(conn, slug, params, asof, compute, workspace=workspace)
