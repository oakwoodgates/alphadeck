"""Generation-gated in-process LRU + optional disk cache for ``read_call``, ``read_display_signals``,
and workspace-level reads like ``read_data_flags``.

The Board and Cockpit fetch ``GET /theses/calls?asof=``, ``GET /theses/{id}/call?asof=``,
``GET /theses/{id}/display-signals?asof=``, and ``GET /data-flags?asof=`` repeatedly across page
navigations, tab switches, and as-of slider scrubs. All of these routes are strictly read-only
deterministic projections of the thesis spine and bitemporal fact tables at ``(asof, view)``.

To keep ``app/routers/theses.py`` and ``app/routers/data_flags.py`` decoupled from ``dashboards/``
(``tests/dashboards/test_isolation.py``'s Leaf Rule forbids importing ``dashboards.*`` outside
``dashboards/`` and ``app/routers/dashboards/``), this module owns a bounded LRU cache with optional
fail-open disk persistence dedicated to those endpoints. Each lookup validates a fast (<1 ms)
workspace-level generation token over tiny metadata/log/workspace tables plus the latest cron run
artifact, so any fact ingest, split, price repair, insider hold/amendment decision, operator
decision, or roster change invalidates stale entries immediately without scanning fact tables.
"""

from __future__ import annotations

import functools
import hashlib
import os
import pickle
import tempfile
from collections import OrderedDict
from collections.abc import Callable
from datetime import date, datetime
from pathlib import Path
from threading import Lock
from typing import Any, TypeVar
from uuid import UUID

import psycopg
from pydantic import BaseModel

from domain.scopes import DATA_TENANT_ID
from domain.settings import get_settings
from domain.thesis import Thesis
from pipeline.cron_run_log import list_run_logs

MAX_CALL_CACHE_ENTRIES = 512

DEFAULT_CALL_DISK_DIR = Path(__file__).resolve().parents[2] / "data" / "call_cache"
DISK_CACHE_ENV = "ALPHADECK_DASHBOARD_DISK_CACHE"

T = TypeVar("T", bound=BaseModel)

# Fast (<1 ms) workspace-level generation token over tiny metadata, log, and workspace tables only.
# Never scans large fact tables (``fact_price_eod``, ``fact_insider_txn``, etc.) in production: every
# ingest and data mutation advances ``security_change``, ``data_repair``, or ``latest_run_identity()``.
_WORKSPACE_TOKEN_SQL = """
SELECT
    (SELECT count(*)::text || ':' || coalesce(max(seq)::text, '')
     FROM data_repair) AS repair_tok,
    (SELECT count(*)::text || ':' || coalesce(max(seq), 0)::text || ':' || coalesce(sum(seq), 0)::text
     FROM security_change) AS change_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM security_split
     WHERE tenant_id = %(data_tenant)s) AS split_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fundamentals_set_aside) AS set_aside_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_amendment_decision
     WHERE tenant_id = %(data_tenant)s) AS amend_dec_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_txn_hold_decision
     WHERE tenant_id = %(data_tenant)s) AS hold_dec_tok,
    (SELECT count(*)::text
     FROM security_cik_change
     WHERE tenant_id = %(data_tenant)s) AS cik_change_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM operator_decision
     WHERE tenant_id = %(workspace)s) AS decision_tok,
    (SELECT count(*)::text || ':' || coalesce(max(taken_at)::text, '')
     FROM basket_snapshot
     WHERE thesis_id IN (SELECT id FROM thesis WHERE tenant_id = %(workspace)s)) AS snapshot_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_thesis_catalyst
     WHERE tenant_id = %(workspace)s) AS thesis_cat_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_theme_conviction
     WHERE tenant_id = %(workspace)s) AS theme_conv_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_term_mentions
     WHERE tenant_id = %(workspace)s) AS term_mentions_tok,
    (SELECT count(*)::text || ':' || coalesce(
        string_agg(
            id::text || ':' || coalesce(archived_at::text, '') || ':' ||
            meta_index::text || ':' || name,
            '|' ORDER BY id
        ),
        ''
     )
     FROM thesis
     WHERE tenant_id = %(workspace)s) AS thesis_tok,
    (SELECT count(*)::text || ':' || coalesce(max(seq)::text, '')
     FROM calls) AS calls_tok,
    (SELECT count(*)::text || ':' || coalesce(max(number)::text, '') || ':' ||
            coalesce(max(created_at)::text, '')
     FROM edition
     WHERE tenant_id = %(workspace)s) AS edition_tok,
    (SELECT count(*)::text || ':' || coalesce(max(asof)::text, '')
     FROM edition_call
     WHERE edition_id IN (SELECT id FROM edition WHERE tenant_id = %(workspace)s)) AS edition_call_tok,
    (SELECT count(*)::text || ':' || coalesce(max(inked_at)::text, '')
     FROM edition_outcome
     WHERE edition_id IN (SELECT id FROM edition WHERE tenant_id = %(workspace)s)) AS edition_outcome_tok,
    (SELECT count(*)::text || ':' || coalesce(max(at)::text, '')
     FROM edition_event
     WHERE edition_id IN (SELECT id FROM edition WHERE tenant_id = %(workspace)s)) AS edition_event_tok
"""

# Pytest-only addendum: some integration tests insert directly into fact/master tables via raw SQL
# without calling ``db.change_stamp.mark_changed``. Only executed when ``PYTEST_CURRENT_TEST`` is set.
_PYTEST_FACT_TOKEN_SQL = """
SELECT
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_price_eod
     WHERE tenant_id = %(data_tenant)s) AS price_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_insider_txn
     WHERE tenant_id = %(data_tenant)s) AS insider_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_dilution
     WHERE tenant_id = %(data_tenant)s) AS dilution_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_catalyst
     WHERE tenant_id = %(data_tenant)s) AS catalyst_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_corporate_event
     WHERE tenant_id = %(data_tenant)s) AS corp_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_activist_stake
     WHERE tenant_id = %(data_tenant)s) AS activist_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_fundamentals
     WHERE tenant_id = %(data_tenant)s) AS fund_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_fund_shares
     WHERE tenant_id = %(data_tenant)s) AS fund_shares_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM fact_filing_index
     WHERE tenant_id = %(data_tenant)s) AS filing_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_amendment
     WHERE tenant_id = %(data_tenant)s) AS amend_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_amendment_link
     WHERE tenant_id = %(data_tenant)s) AS amend_link_tok,
    (SELECT count(*)::text || ':' || coalesce(max(recorded_at)::text, '')
     FROM insider_txn_hold
     WHERE tenant_id = %(data_tenant)s) AS hold_tok,
    (SELECT count(*)::text || ':' || coalesce(
        string_agg(
            id::text || ':' || coalesce(ticker, '') || ':' || coalesce(cik, '') || ':' ||
            coalesce(business_type, '') || ':' || coalesce(sector, ''),
            '|' ORDER BY id
        ),
        ''
     )
     FROM security_master
     WHERE tenant_id = %(data_tenant)s) AS master_tok,
    (SELECT count(*)::text || ':' || coalesce(
        string_agg(
            thesis_id::text || ':' || coalesce(security_id::text, '') || ':' || ticker,
            '|' ORDER BY thesis_id, ordinal
        ),
        ''
     )
     FROM basket_member
     WHERE tenant_id = %(workspace)s) AS basket_tok
"""


@functools.lru_cache(maxsize=1)
def code_fingerprint() -> tuple[str | None, int]:
    """Process-stable fingerprint of call/signal/data-flags code: ``(image_sha, max_mtime_ns)``.

    A new prod image SHA or any live file edit under ``--reload`` in dev moves this value so stale
    disk entries from an earlier build or edit are never served.
    """
    image_sha = get_settings().image_sha
    backend_dir = Path(__file__).resolve().parents[1]
    single_files = (
        Path(__file__).resolve(),
        backend_dir / "app" / "routers" / "theses.py",
        backend_dir / "app" / "routers" / "data_flags.py",
        backend_dir / "app" / "routers" / "scoreboard.py",
        backend_dir / "pipeline" / "call_for_thesis.py",
        backend_dir / "pipeline" / "data_flags.py",
    )
    roots = (
        backend_dir / "calls",
        backend_dir / "signals",
        backend_dir / "scoreboard",
    )
    max_mtime_ns = 0
    for path in single_files:
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            continue
        if mtime > max_mtime_ns:
            max_mtime_ns = mtime
    for root in roots:
        if not root.is_dir():
            continue
        for p in root.rglob("*.py"):
            try:
                mtime = p.stat().st_mtime_ns
            except OSError:
                continue
            if mtime > max_mtime_ns:
                max_mtime_ns = mtime
    return (image_sha, max_mtime_ns)


def _latest_run_identity() -> tuple[Any, ...] | None:
    """The newest cron run artifact's identity; ``None`` when no run has been recorded."""
    try:
        logs = list_run_logs(limit=1)
    except Exception:  # noqa: BLE001 — fail-open
        return None
    if not logs:
        return None
    doc = logs[0]
    return (doc.get("started_at"), doc.get("status"), doc.get("finished_at"))


def workspace_token(conn: psycopg.Connection, workspace: UUID) -> tuple[Any, ...] | None:
    """Return a fast (<1 ms) hashable generation tuple for ``workspace``'s call/display/data-flags
    dependencies, or ``None`` if ``conn`` is a test double or cannot execute the token probe
    (fail-open: skip caching)."""
    if not isinstance(conn, psycopg.Connection):
        return None
    params = {"data_tenant": DATA_TENANT_ID, "workspace": workspace}
    try:
        run_ident = _latest_run_identity()
        with conn.transaction(), conn.cursor() as cur:
            cur.execute(_WORKSPACE_TOKEN_SQL, params)
            row = cur.fetchone()
            if row is None:
                return None
            vals = tuple(row.values()) if isinstance(row, dict) else tuple(row)
            if os.environ.get("PYTEST_CURRENT_TEST"):
                cur.execute(_PYTEST_FACT_TOKEN_SQL, params)
                extra = cur.fetchone()
                if extra is not None:
                    extra_vals = tuple(extra.values()) if isinstance(extra, dict) else tuple(extra)
                    vals = vals + extra_vals
    except Exception:  # noqa: BLE001 — fail-open: skip caching
        return None
    return (run_ident, *vals)


class CallCache:
    """Thread-safe bounded LRU cache (with optional fail-open disk persistence) for
    ``CallCardResponse``, ``DisplaySignalsResponse``, and workspace-level reads like
    ``DataFlagsResponse``."""

    def __init__(
        self,
        max_entries: int = MAX_CALL_CACHE_ENTRIES,
        *,
        disk_dir: Path | None = None,
    ) -> None:
        self._max_entries = max_entries
        self._disk_dir: Path | None = disk_dir
        self._lock = Lock()
        self._entries: OrderedDict[tuple[Any, ...], Any] = OrderedDict()

    def _active_disk_dir(self) -> Path | None:
        if self._disk_dir is None:
            return None
        if self._disk_dir == DEFAULT_CALL_DISK_DIR:
            if os.environ.get("PYTEST_CURRENT_TEST") or os.environ.get(DISK_CACHE_ENV) == "0":
                return None
        return self._disk_dir

    def _disk_path(
        self, ddir: Path, token: tuple[Any, ...], disk_key: tuple[Any, ...]
    ) -> Path | None:
        try:
            raw = pickle.dumps(
                (code_fingerprint(), token, disk_key), protocol=pickle.HIGHEST_PROTOCOL
            )
        except (pickle.PickleError, TypeError, AttributeError):
            return None
        digest = hashlib.sha256(raw).hexdigest()
        return ddir / f"{digest}.pkl"

    def _read_disk(self, token: tuple[Any, ...], disk_key: tuple[Any, ...]) -> Any | None:
        ddir = self._active_disk_dir()
        if ddir is None:
            return None
        path = self._disk_path(ddir, token, disk_key)
        if path is None or not path.is_file():
            return None
        try:
            stored_gen, stored_key, value = pickle.loads(path.read_bytes())
        except (
            OSError,
            EOFError,
            pickle.PickleError,
            ValueError,
            TypeError,
            AttributeError,
            ImportError,
        ):
            return None
        if stored_gen == (code_fingerprint(), token) and stored_key == disk_key:
            return value
        return None

    def _prune_disk(self, ddir: Path) -> None:
        limit = max(self._max_entries, 0)
        entries: list[tuple[int, Path]] = []
        for p in ddir.glob("*.pkl"):
            try:
                entries.append((p.stat().st_mtime_ns, p))
            except OSError:
                continue
        if len(entries) <= limit:
            return
        entries.sort(key=lambda item: item[0])
        for _, victim in entries[: len(entries) - limit]:
            try:
                victim.unlink(missing_ok=True)
            except OSError:
                pass

    def _write_disk(self, token: tuple[Any, ...], disk_key: tuple[Any, ...], value: Any) -> None:
        ddir = self._active_disk_dir()
        if ddir is None:
            return
        path = self._disk_path(ddir, token, disk_key)
        if path is None:
            return
        tmp_name: str | None = None
        try:
            payload = pickle.dumps(
                ((code_fingerprint(), token), disk_key, value),
                protocol=pickle.HIGHEST_PROTOCOL,
            )
            ddir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=ddir, suffix=".tmp", delete=False) as tmp:
                tmp_name = tmp.name
                tmp.write(payload)
            os.replace(tmp_name, path)
            tmp_name = None
            self._prune_disk(ddir)
        except (OSError, pickle.PickleError, TypeError, AttributeError):
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    def _clear_disk(self) -> None:
        ddir = self._active_disk_dir()
        if ddir is None or not ddir.is_dir():
            return
        for p in ddir.glob("*.pkl"):
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass

    def clear(self) -> None:
        """Drop all cached call, display-signal, and workspace responses."""
        with self._lock:
            self._entries.clear()
        self._clear_disk()

    def size(self) -> int:
        with self._lock:
            return len(self._entries)

    @staticmethod
    def _disk_key(
        *,
        kind: str,
        thesis: Thesis,
        asof: date,
        known_at: datetime | None,
        clock: str,
        decisions_asof: date | None,
    ) -> tuple[Any, ...]:
        return (
            kind,
            thesis.id,
            thesis.tenant_id,
            thesis.model_dump_json(),
            asof,
            known_at,
            clock,
            decisions_asof,
        )

    @staticmethod
    def _key(
        *,
        kind: str,
        thesis: Thesis,
        asof: date,
        known_at: datetime | None,
        clock: str,
        decisions_asof: date | None,
        fn_token: int,
        token: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        return (
            kind,
            thesis.id,
            thesis.tenant_id,
            thesis.model_dump_json(),
            asof,
            known_at,
            clock,
            decisions_asof,
            fn_token,
            token,
        )

    @staticmethod
    def _workspace_disk_key(
        *,
        kind: str,
        workspace: UUID,
        subkey: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        return ("workspace", kind, workspace, subkey)

    @staticmethod
    def _workspace_key(
        *,
        kind: str,
        workspace: UUID,
        subkey: tuple[Any, ...],
        fn_token: int,
        token: tuple[Any, ...],
    ) -> tuple[Any, ...]:
        return ("workspace", kind, workspace, subkey, fn_token, token)

    def _put_mem(self, key: tuple[Any, ...], value: Any) -> None:
        with self._lock:
            self._entries[key] = value.model_copy(deep=True)
            self._entries.move_to_end(key)
            while len(self._entries) > self._max_entries:
                self._entries.popitem(last=False)

    def is_warm_call(
        self,
        conn: psycopg.Connection,
        *,
        thesis: Thesis,
        asof: date,
        known_at: datetime | None,
        clock: str,
        decisions_asof: date | None = None,
        fn_token: int = 0,
        token: tuple[Any, ...] | None = None,
    ) -> bool:
        """Return whether the cached ``call`` entry for ``thesis`` is present in memory or on disk
        for the current DB token."""
        tok = token if token is not None else workspace_token(conn, thesis.tenant_id)
        if tok is None:
            return False
        key = self._key(
            kind="call",
            thesis=thesis,
            asof=asof,
            known_at=known_at,
            clock=clock,
            decisions_asof=decisions_asof,
            fn_token=fn_token,
            token=tok,
        )
        with self._lock:
            if key in self._entries:
                return True
        disk_key = self._disk_key(
            kind="call",
            thesis=thesis,
            asof=asof,
            known_at=known_at,
            clock=clock,
            decisions_asof=decisions_asof,
        )
        return self._read_disk(tok, disk_key) is not None

    def get_cached_only(
        self,
        conn: psycopg.Connection,
        *,
        kind: str,
        thesis: Thesis,
        asof: date,
        known_at: datetime | None,
        clock: str,
        decisions_asof: date | None = None,
        fn_token: int = 0,
        token: tuple[Any, ...] | None = None,
    ) -> Any | None:
        """Return a deep copy of a cached entry from memory or disk (promoting disk hits to memory),
        or ``None`` on a miss without computing."""
        tok = token if token is not None else workspace_token(conn, thesis.tenant_id)
        if tok is None:
            return None
        key = self._key(
            kind=kind,
            thesis=thesis,
            asof=asof,
            known_at=known_at,
            clock=clock,
            decisions_asof=decisions_asof,
            fn_token=fn_token,
            token=tok,
        )
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached.model_copy(deep=True)
        disk_key = self._disk_key(
            kind=kind,
            thesis=thesis,
            asof=asof,
            known_at=known_at,
            clock=clock,
            decisions_asof=decisions_asof,
        )
        disk_hit = self._read_disk(tok, disk_key)
        if disk_hit is not None:
            self._put_mem(key, disk_hit)
            return disk_hit.model_copy(deep=True)
        return None

    def get_or_compute(
        self,
        conn: psycopg.Connection,
        *,
        kind: str,
        thesis: Thesis,
        asof: date,
        known_at: datetime | None,
        clock: str,
        decisions_asof: date | None = None,
        fn_token: int = 0,
        compute: Callable[[], T],
        token: tuple[Any, ...] | None = None,
    ) -> T:
        tok = token if token is not None else workspace_token(conn, thesis.tenant_id)
        if tok is None:
            return compute()
        key = self._key(
            kind=kind,
            thesis=thesis,
            asof=asof,
            known_at=known_at,
            clock=clock,
            decisions_asof=decisions_asof,
            fn_token=fn_token,
            token=tok,
        )
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached.model_copy(deep=True)
        disk_key = self._disk_key(
            kind=kind,
            thesis=thesis,
            asof=asof,
            known_at=known_at,
            clock=clock,
            decisions_asof=decisions_asof,
        )
        disk_hit = self._read_disk(tok, disk_key)
        if disk_hit is not None:
            self._put_mem(key, disk_hit)
            return disk_hit.model_copy(deep=True)
        result = compute()
        self._put_mem(key, result)
        self._write_disk(tok, disk_key, result)
        return result

    def get_or_compute_workspace(
        self,
        conn: psycopg.Connection,
        *,
        kind: str,
        workspace: UUID,
        subkey: tuple[Any, ...],
        compute: Callable[[], T],
        token: tuple[Any, ...] | None = None,
        fn_token: int = 0,
    ) -> T:
        """Generation-gated memory + disk cache for workspace-level reads (e.g. ``GET /data-flags``)."""
        tok = token if token is not None else workspace_token(conn, workspace)
        if tok is None:
            return compute()
        key = self._workspace_key(
            kind=kind,
            workspace=workspace,
            subkey=subkey,
            fn_token=fn_token,
            token=tok,
        )
        with self._lock:
            cached = self._entries.get(key)
            if cached is not None:
                self._entries.move_to_end(key)
                return cached.model_copy(deep=True)
        disk_key = self._workspace_disk_key(kind=kind, workspace=workspace, subkey=subkey)
        disk_hit = self._read_disk(tok, disk_key)
        if disk_hit is not None:
            self._put_mem(key, disk_hit)
            return disk_hit.model_copy(deep=True)
        result = compute()
        self._put_mem(key, result)
        self._write_disk(tok, disk_key, result)
        return result


CACHE = CallCache(disk_dir=DEFAULT_CALL_DISK_DIR)
