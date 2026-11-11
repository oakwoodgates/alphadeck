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

- **Generation, not a clock — in two levels.** Every lookup first reads a cheap generation token
  (``Generation``), and each part clears only what its event can have changed:
  - HARD (clears page entries AND every piece): the newest cron run artifact's identity (``started_at`` /
    ``status`` / ``finished_at``, so a pass both starting and finishing moves it) and the newest
    ``data_repair`` journal row (every repair CLI's ``--apply`` / ``--revoke`` appends one);
  - SOFT (clears page entries only; pieces survive, since no company's data changed): the newest basket
    snapshot (``max(taken_at)``, which moves on every roster edit) and the live (unarchived) cards' ids,
    names and meta index switch (archiving, restoring, creating, renaming or flipping a card moves it);
  - the STAMPS (clears page entries and the pieces of exactly the companies that changed): the fingerprint
    of the per-company change log (``db.change_stamp``, an append-only log, one aggregate read; a prune moves
    it too, and a pruned company reads as gone). When it moves, the stamp map (each company's newest seq) is
    read and compared with the one the cache remembers; every piece (all groups, kinds and workspaces) of a
    company whose stamp is new, different or gone is dropped, and every piece when one of them is a
    benchmark (each company's relative strength is priced against the benchmarks). Every state change
    reaches the stamp through a known event: the company write lock's release (every fact ingest) and the
    narrow writers outside that lock, which call ``db.change_stamp.mark_changed`` themselves.
  There is no TTL, no timer and no polling: the run schedule will grow (market open, close, nightly,
  pre-market) and each new run is its own generation.
- **Bounded.** At most ``MAX_ENTRIES`` entries, least recently used evicted first.
- **Single-flight.** A per-key lock: two simultaneous cold requests for one key compute once; the second
  waits and is served the first one's value. The app runs one worker (``assert_single_worker`` in
  ``app/main.py``), so in-process is the whole cache.
- **Never stores across a generation.** The token is read again after a compute; a value computed while
  either level moved (a run started or finished, a roster edit, a company's stamp) is returned to its caller
  but never stored, and neither is any piece it staged.

**Pieces: one company's result, shared by every page.** Many pages read the same per-company inputs (a
company's price-reaching data flags, a display member's reading). A piece is one such value, keyed by
``(workspace, kind, security_id, asof, clock, known_at)``; ``kind`` names the input (``"price_flags"``,
``"member:<name>"``), ``known_at`` is the caller's (``None`` on the live view), and a stored ``None`` is a
real value. Pieces are only read and written while a page's compute runs inside ``get`` (a piece session,
held in a context variable, so code run outside ``cached()`` computes exactly as before and stores
nothing); the session's workspace is the page's own, so two workspaces never share one. A compute's new
pieces are staged and stored only where its page entry would be: a compute that raised, or one during
which the generation moved, stores none of them. Pieces clear on a hard move and on ``clear()``; a company's
pieces drop when its stamp moves (above); a soft move keeps them. At most ``MAX_PIECE_GROUPS`` groups of
``(workspace, asof, clock, known_at)`` are kept, the least recently used group evicted whole. The pieces' readers live in ``dashboards.baskets``.

Display only (rule 1): this module READS the run artifacts, ``data_repair``, ``basket_snapshot``, ``thesis``,
``security_change`` and (for the benchmark ids) ``security_master``; it writes nothing anywhere but its own
in-memory dict.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Generic, Literal, NamedTuple, TypeVar
from uuid import UUID

import psycopg

from db.change_stamp import stamp_fingerprint, stamp_map
from domain.scopes import DATA_TENANT_ID
from pipeline.cron_run_log import list_run_logs
from signals.display.relative_strength import BENCHMARKS

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


def live_cards(conn: psycopg.Connection) -> tuple[tuple[Any, str, bool], ...]:
    """The live (unarchived) cards, sorted by id, with their names and the meta index switch (a rename or a
    flip of the switch shows on the next lookup)."""
    with conn.cursor() as cur:
        cur.execute("SELECT id, name, meta_index FROM thesis WHERE archived_at IS NULL ORDER BY id")
        return tuple((r["id"], r["name"], r["meta_index"]) for r in cur.fetchall())


def latest_repair(conn: psycopg.Connection) -> tuple[Any, int]:
    """``(max(seq), count(*))`` over the ``data_repair`` journal: every repair CLI's ``--apply`` / ``--revoke``
    appends one row, and a repair can rewrite any company's stored data, so a new row clears everything.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT max(seq) AS m, count(*) AS n FROM data_repair")
        row = cur.fetchone()
    return (row["m"], int(row["n"])) if row else (None, 0)


def benchmark_security_ids(conn: psycopg.Connection) -> frozenset[UUID]:
    """The benchmark securities' ids (``signals.display.relative_strength.BENCHMARKS``, resolved by ticker in
    the DATA tenant the way ``PointInTimeData.benchmark_prices`` resolves them). Every company's relative
    strength reading is priced against them, so a change to one drops every piece. Read only when a stamp
    moved."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM security_master "
            "WHERE tenant_id = %s AND ticker = ANY(%s) AND instrument_kind = 'etf'",
            (DATA_TENANT_ID, list(BENCHMARKS)),
        )
        return frozenset(r["id"] for r in cur.fetchall())


class Generation(NamedTuple):
    """The generation token, in two levels (the module docstring):

    - ``hard`` — the newest cron run's identity and the newest data repair: a move clears page entries AND
      every piece;
    - ``soft`` — the newest basket snapshot and the live cards: a move clears page entries only;
    - ``stamps`` — the per-company change stamp's fingerprint (``db.change_stamp``): a move clears page
      entries and drops the pieces of exactly the companies whose stamp changed (every piece when one of them
      is a benchmark). ``None`` = no stamp read (an injected test token)."""

    hard: Hashable
    soft: Hashable = None
    stamps: Hashable = None


def generation_token(conn: psycopg.Connection) -> Generation:
    return Generation(
        hard=(latest_run_identity(), latest_repair(conn)),
        soft=(latest_basket_snapshot(conn), live_cards(conn)),
        stamps=stamp_fingerprint(conn),
    )


def _as_generation(token: Hashable) -> Generation:
    """An injected token that is not a ``Generation`` is a hard-only token (a move clears everything)."""
    return token if isinstance(token, Generation) else Generation(hard=token)


# --- the cache --------------------------------------------------------------------------------------------


class DashboardCache:
    """See the module docstring. ``token`` is injectable for tests; production uses ``generation_token``. An
    injected token may return a ``Generation`` (both levels) or any other hashable (a hard-only token).
    """

    def __init__(
        self,
        *,
        max_entries: int = MAX_ENTRIES,
        token: Callable[[psycopg.Connection], Hashable] | None = None,
    ) -> None:
        self._max = max_entries
        self._token_fn = token
        self._lock = threading.Lock()  # guards everything below
        self._generation: Generation | None = None
        # the stamp map (``{security_id: seq}``) the current generation was synced to; None = not read
        self._stamps: dict[UUID, int] | None = None
        self._entries: OrderedDict[Key, tuple[Any, int]] = OrderedDict()
        self._inflight: dict[Key, threading.Lock] = {}
        self._pieces: OrderedDict[PieceGroup, dict[PieceSub, Any]] = OrderedDict()

    def _token(self, conn: psycopg.Connection) -> Generation:
        return _as_generation((self._token_fn or generation_token)(conn))

    def _observe(self, conn: psycopg.Connection) -> Generation:
        """Read the token and bring the cache to it. The stamp map and the benchmark ids are read (OUTSIDE the
        lock) only when the stamp fingerprint or the hard part moved."""
        gen = self._token(conn)
        with self._lock:
            cur = self._generation
            need_map = gen.stamps is not None and (
                cur is None or cur.hard != gen.hard or cur.stamps != gen.stamps
            )
        smap = stamp_map(conn) if need_map else None
        bench = benchmark_security_ids(conn) if need_map else frozenset()
        with self._lock:
            self._sync(gen, smap, bench)
        return gen

    def _sync(self, gen: Generation, smap: dict[UUID, int] | None, bench: frozenset[UUID]) -> None:
        """Bring the cache to ``gen`` (caller holds ``_lock``): a hard move clears everything; a soft move
        clears the page entries; a stamp move clears the page entries and drops the pieces of each company
        whose stamp is new, changed or gone (every piece when one of them is a benchmark, or when the
        remembered map is missing)."""
        cur = self._generation
        if cur == gen:
            return
        self._entries.clear()
        if cur is None or cur.hard != gen.hard:
            self._pieces.clear()
        elif cur.stamps != gen.stamps:
            if smap is None or self._stamps is None:
                self._pieces.clear()
            else:
                old = self._stamps
                changed = {s for s in old.keys() | smap.keys() if old.get(s) != smap.get(s)}
                if changed & bench:
                    self._pieces.clear()
                elif changed:
                    for group in list(self._pieces):
                        subs = self._pieces[group]
                        for sub in [k for k in subs if k[1] in changed]:
                            del subs[sub]
                        if not subs:
                            del self._pieces[group]
        if smap is not None or cur is None or cur.hard != gen.hard:
            self._stamps = smap
        self._generation = gen

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
        token = self._observe(conn)
        with self._lock:
            hit = self._hit(key) if self._generation == token else None
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
                    # stored only when neither level moved during the compute AND the cache is still synced
                    # to the generation the compute started under (another request may have moved it)
                    if after == token and self._generation == token:
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
            self._stamps = None

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
