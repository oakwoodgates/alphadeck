"""In-process dashboard & Board call cache warm-up engine and ``/dashboards/warm`` API.

Display only (``docs/DASHBOARDS.md`` rule 1): every step executes the existing read-only
dashboard routers and ``app.routers.theses.read_call`` in piece-sharing optimal order so
``price_flags``, ``member:*``, and ``page:breadth`` pieces are computed once and reused by
later pages. Writes nothing to the database and never touches the LLM or notifications.

Single-flight globally: at most one warm pass runs at a time across the process. Auto-warm
triggers enter through ``app.warm_hook.request_warm`` (preserving the Leaf Rule) and are
guarded by kill switches, pytest detection, nightly-pass lock checks, debouncing, and
generation-aware short-circuiting so warm sessions add negligible overhead.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable, Hashable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from app import call_cache, warm_hook
from app.deps import get_conn, get_workspace
from app.routers import theses as theses_router
from dashboards.cache import CACHE, latest_run_identity
from db.session import connect_job, name_session
from domain.market_time import (
    MarketClock,
    countdown_origin,
    current_night,
    market_clock,
    serve_decisions_asof,
    serve_view,
)
from domain.text_types import AsofDate
from pipeline import run_lock
from repositories import thesis_repo

_log = logging.getLogger("alphadeck.dashboards.warm")

router = APIRouter(prefix="/warm")

AUTO_WARM_ENV = "ALPHADECK_AUTO_WARM"
WARM_DASHBOARDS_ENV = "ALPHADECK_WARM_DASHBOARDS"
AUTO_WARM_DEBOUNCE_S = 15.0
BOOT_WARM_DELAY_S = 5.0

_OFF_VALUES = frozenset({"0", "false", "off", "no"})
_SESSION_REASONS = frozenset({"auto", "session", "login"})


class WarmStatusOut(BaseModel):
    """Current warmth coverage and background warm-up status for a workspace and as-of date."""

    asof: date
    total: int
    warm: int
    calls_total: int
    calls_warm: int
    running: bool
    current_page: str | None = None
    last_started_at: str | None = None
    last_finished_at: str | None = None
    last_wall_s: float | None = None
    last_reason: str | None = None
    error: str | None = None


@dataclass(frozen=True)
class WarmJob:
    """Parameters of one single-flight background warm pass."""

    workspace: UUID | None
    asof: date
    reason: str
    is_auto: bool = False
    delay_s: float = 0.0


_Executor = Callable[[WarmJob, Callable[[], None]], None]

# --- mutable engine state (guarded by ``_lock``) --------------------------------------------------------------

_lock = threading.Lock()
_running: bool = False
_active_workspace: UUID | None = None
_active_asof: date | None = None
_current_page: str | None = None
_last_started_at: str | None = None
_last_finished_at: str | None = None
_last_wall_s: float | None = None
_last_reason: str | None = None
_error: str | None = None

# Per-(workspace, asof, reason) last auto-warm trigger timestamp (monotonic seconds) for debouncing.
_last_auto_trigger_at: dict[tuple[UUID | None, date, str], float] = {}
# Last generation token cleanly warmed per (workspace, asof) so warm sessions skip in <0.01 ms.
_last_auto_warmed: dict[tuple[UUID, date], Hashable] = {}


# --- injectable test seams ------------------------------------------------------------------------------------


def _thread_executor(job: WarmJob, run: Callable[[], None]) -> None:
    def _target() -> None:
        if job.delay_s > 0:
            _sleep(job.delay_s)
        run()

    thread = threading.Thread(
        target=_target,
        name=f"alphadeck-dashboard-warm-{job.reason}",
        daemon=True,
    )
    thread.start()


_DEFAULT_EXECUTOR: _Executor = _thread_executor
_connect_job: Callable[[], psycopg.Connection] = connect_job
_probe_pass_lock: Callable[[], str | None] = run_lock.probe_pass_lock
_describe_holder: Callable[[psycopg.Connection], str | None] = run_lock.describe_holder
_monotonic: Callable[[], float] = time.monotonic
_sleep: Callable[[float], None] = time.sleep


def reset_state() -> None:
    """Reset in-memory warm engine state (for tests)."""
    global _running, _active_workspace, _active_asof, _current_page
    global _last_started_at, _last_finished_at, _last_wall_s, _last_reason, _error
    with _lock:
        _running = False
        _active_workspace = None
        _active_asof = None
        _current_page = None
        _last_started_at = None
        _last_finished_at = None
        _last_wall_s = None
        _last_reason = None
        _error = None
        _last_auto_trigger_at.clear()
        _last_auto_warmed.clear()


# --- helpers --------------------------------------------------------------------------------------------------


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _end_tx(conn: Any) -> None:
    """End any read-only transaction so the background connection never stays idle-in-transaction."""
    rollback = getattr(conn, "rollback", None)
    if callable(rollback):
        try:
            rollback()
        except Exception:  # noqa: BLE001
            pass


def _close_conn(conn: Any) -> None:
    if conn is None:
        return
    close = getattr(conn, "close", None)
    if callable(close):
        try:
            close()
        except Exception:  # noqa: BLE001
            pass


def _cached_generation() -> Hashable | None:
    """Read the cache's currently observed generation in memory without touching the database."""
    with CACHE._lock:
        return CACHE._generation


def _env_disabled(env: Mapping[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in _OFF_VALUES


def _auto_warm_disabled(env: Mapping[str, str] | None = None) -> bool:
    environ = env if env is not None else os.environ
    if environ.get("PYTEST_CURRENT_TEST"):
        return True
    return _env_disabled(environ, AUTO_WARM_ENV) or _env_disabled(environ, WARM_DASHBOARDS_ENV)


def _nightly_pass_running() -> bool:
    """Return True if a nightly pass appears to be running (fail-open on errors)."""
    try:
        ident = latest_run_identity()
        if ident is not None and ident[1] == "running":
            return True
    except Exception:  # noqa: BLE001 — fail-open
        pass
    try:
        return _probe_pass_lock() is not None
    except Exception:  # noqa: BLE001 — fail-open
        _log.debug("pass lock probe failed during auto-warm check (fail-open)", exc_info=True)
        return False


def _list_workspaces(conn: psycopg.Connection) -> list[UUID]:
    """Return every ``kind='workspace'`` tenant id in a stable order."""
    with conn.cursor() as cur:
        cur.execute("SELECT id FROM tenant WHERE kind = 'workspace' ORDER BY id")
        rows = cur.fetchall()
    out: list[UUID] = []
    for row in rows:
        out.append(row["id"] if isinstance(row, Mapping) else row[0])
    return out


def _track_record_cache_params(
    conn: psycopg.Connection, workspace: UUID, clock: str
) -> tuple[Hashable, ...]:
    """Resolve the default ``track-record`` cache key params without computing the page."""
    from app.routers.dashboards import track_record

    try:
        chosen = track_record._resolve_edition(conn, workspace, None)
        if chosen is None:
            exclude_late = track_record.resolve_exclude_late(None)
            number = 0
        else:
            exclude_late = False
            number = chosen.number
    except Exception:  # noqa: BLE001 — fallback when test stubs do not back scoreboard editions
        number = 0
        exclude_late = track_record.resolve_exclude_late(None)
    return (number, not exclude_late, clock)


def _cached_peer_scope_follow_ups(
    conn: psycopg.Connection, workspace: UUID, target_asof: date, clock: str
) -> list[tuple[str, tuple[Hashable, ...]]]:
    """Inspect cached ``peer-table/scopes`` (without computing or mutating cache) for follow-up keys."""
    token = CACHE._observe(conn)
    scopes_key = (workspace, "peer-table", ("scopes", clock), target_asof)
    with CACHE._lock:
        entry = CACHE._entries.get(scopes_key) if CACHE._generation == token else None
    if entry is None:
        entry = CACHE._read_disk(token, scopes_key)
    if entry is None:
        return []
    scope_read = entry[0]
    found = getattr(scope_read, "found", scope_read)
    default_scope = getattr(found, "default_scope", None)
    default_key = getattr(found, "default_key", None)
    companies = getattr(found, "companies", 0) or 0

    out: list[tuple[str, tuple[Hashable, ...]]] = []
    seen_all = False
    if default_scope and default_key:
        pick = "" if default_scope == "all" else str(default_key)
        out.append(("peer-table", (str(default_scope), pick, clock)))
        if default_scope == "all":
            seen_all = True
    if companies > 0 and not seen_all:
        out.append(("peer-table", ("all", "", clock)))
    return out


def _expected_cache_keys(
    conn: psycopg.Connection,
    workspace: UUID,
    target_asof: date,
    mclock: MarketClock,
) -> list[tuple[str, tuple[Hashable, ...]]]:
    """All default dashboard cache ``(slug, params)`` entries warmed for ``(workspace, target_asof)``."""
    _, clock = serve_view(target_asof, night=mclock.night)
    origin = countdown_origin(target_asof, mclock)
    track_params = _track_record_cache_params(conn, workspace, clock)
    peer_follow_ups = _cached_peer_scope_follow_ups(conn, workspace, target_asof, clock)

    return [
        ("theme-leaderboard", (clock,)),
        ("peer-table", ("scopes", clock)),
        *peer_follow_ups,
        ("market-map", (clock,)),
        ("insider-tracker", (30, False, clock)),
        ("daily-digest", (clock, origin)),
        ("trigger-log", (90, "entry", clock)),
        ("hot-board", ("armed", clock, origin)),
        ("hot-board", ("all", clock, origin)),
        ("track-record", track_params),
        ("forming-quietly", (clock,)),
        ("entry-windows", (clock, origin)),
        ("leaders", (clock,)),
        ("rotation-map", (clock,)),
        ("cross-radar", (clock,)),
        ("landing", (clock,)),
        ("narrative-index", (clock, False)),
        ("read-through", (clock,)),
        ("rotation-quadrant", (clock, False)),
        ("etfs", (clock,)),
        ("overlap-map", (clock, False)),
        ("filing-stream", ("cards", clock)),
        ("chain-map", ("cards", clock)),
        ("evidence-ledger", ("cards", clock)),
        ("fundamentals-multiples", ("cards", clock)),
        ("narrative-adoption", ("cards", clock)),
        ("chain-wave", ("cards", clock)),
    ]


def _set_current_page(label: str | None) -> None:
    global _current_page
    with _lock:
        _current_page = label


def _generation_moved(conn: psycopg.Connection, start_gen: Hashable) -> bool:
    cur_gen = CACHE._observe(conn)
    _end_tx(conn)
    return cur_gen != start_gen


# --- warm execution (piece-sharing optimal order) -------------------------------------------------------------


def _warm_workspace(
    conn: psycopg.Connection,
    workspace: UUID,
    target_asof: date,
    mclock: MarketClock,
    start_gen: Hashable,
) -> tuple[bool, list[str]]:
    """Warm all default dashboards and live Board calls for one workspace.

    Returns ``(aborted, errors)``. Aborts cleanly before any step if the cache generation
    moved since the pass started.
    """
    from app.routers.dashboards import (
        chain_map,
        chain_wave,
        cross_radar,
        daily_digest,
        entry_windows,
        etfs,
        evidence_ledger,
        filing_stream,
        forming_quietly,
        fundamentals_multiples,
        hot_board,
        insider_tracker,
        landing,
        leaders,
        market_map,
        narrative_adoption,
        narrative_index,
        overlap_map,
        peer_table,
        read_through,
        rotation_map,
        rotation_quadrant,
        theme_leaderboard,
        track_record,
        trigger_log,
    )

    errors: list[str] = []

    def _run_step(label: str, fn: Callable[[], Any]) -> tuple[bool, Any]:
        if _generation_moved(conn, start_gen):
            _log.info("dashboard warm aborted before %s: cache generation moved", label)
            return True, None
        _set_current_page(label)
        try:
            return False, fn()
        except Exception as exc:  # noqa: BLE001 — fail-open per page so remaining pages warm
            _log.warning("dashboard warm step %s failed", label, exc_info=True)
            errors.append(f"{label}: {type(exc).__name__}: {exc}")
            return False, None
        finally:
            _end_tx(conn)

    # 1. theme-leaderboard (populates price_flags, member:* and page:breadth pieces first)
    aborted, _ = _run_step(
        "theme-leaderboard",
        lambda: theme_leaderboard.theme_leaderboard(
            asof=target_asof, conn=conn, workspace=workspace
        ),
    )
    if aborted:
        return True, errors

    # 2. peer-table/scopes + follow-up default scope and scope="all"
    aborted, scopes = _run_step(
        "peer-table/scopes",
        lambda: peer_table.peer_table_scopes(asof=target_asof, conn=conn, workspace=workspace),
    )
    if aborted:
        return True, errors
    if scopes is not None:
        default_scope = getattr(scopes, "default_scope", None)
        default_key = getattr(scopes, "default_key", None)
        companies = getattr(scopes, "companies", 0) or 0
        warmed_all = False
        if default_scope and default_key:
            aborted, _ = _run_step(
                f"peer-table:{default_scope}",
                lambda s=default_scope, k=default_key: peer_table.peer_table(
                    asof=target_asof, scope=s, key=k, conn=conn, workspace=workspace
                ),
            )
            if aborted:
                return True, errors
            if default_scope == "all":
                warmed_all = True
        if companies > 0 and not warmed_all:
            aborted, _ = _run_step(
                "peer-table:all",
                lambda: peer_table.peer_table(
                    asof=target_asof, scope="all", key=None, conn=conn, workspace=workspace
                ),
            )
            if aborted:
                return True, errors

    # 3-20. Remaining dashboards and card pickers in piece-sharing order
    steps: tuple[tuple[str, Callable[[], Any]], ...] = (
        (
            "market-map",
            lambda: market_map.market_map(asof=target_asof, conn=conn, workspace=workspace),
        ),
        (
            "insider-tracker",
            lambda: insider_tracker.insider_tracker_page(
                asof=target_asof,
                days=insider_tracker.InsiderTrackerDays(30),
                silent=False,
                conn=conn,
                workspace=workspace,
            ),
        ),
        (
            "daily-digest",
            lambda: daily_digest.daily_digest_page(
                asof=target_asof, conn=conn, workspace=workspace
            ),
        ),
        (
            "trigger-log",
            lambda: trigger_log.trigger_log_page(
                asof=target_asof,
                days=trigger_log.TriggerLogDays.D90,
                role="entry",
                conn=conn,
                workspace=workspace,
            ),
        ),
        (
            "hot-board:armed",
            lambda: hot_board.hot_board(
                asof=target_asof, rows="armed", conn=conn, workspace=workspace
            ),
        ),
        (
            "hot-board:all",
            lambda: hot_board.hot_board(
                asof=target_asof, rows="all", conn=conn, workspace=workspace
            ),
        ),
        (
            "track-record",
            lambda: track_record.track_record(
                asof=target_asof,
                edition=None,
                include_late=None,
                conn=conn,
                workspace=workspace,
            ),
        ),
        (
            "forming-quietly",
            lambda: forming_quietly.forming_quietly(
                asof=target_asof, conn=conn, workspace=workspace
            ),
        ),
        (
            "entry-windows",
            lambda: entry_windows.entry_windows_page(
                asof=target_asof, conn=conn, workspace=workspace
            ),
        ),
        (
            "leaders",
            lambda: leaders.leaders(
                asof=target_asof,
                include_silent=False,
                group_by="link",
                conn=conn,
                workspace=workspace,
            ),
        ),
        (
            "rotation-map",
            lambda: rotation_map.rotation_map(
                asof=target_asof,
                group_by="link",
                include_silent=False,
                conn=conn,
                workspace=workspace,
            ),
        ),
        (
            "cross-radar",
            lambda: cross_radar.cross_radar(
                asof=target_asof,
                group_by="link",
                include_silent=False,
                price_200d=False,
                conn=conn,
                workspace=workspace,
            ),
        ),
        (
            "landing",
            lambda: landing.landing(
                asof=target_asof,
                group_by="link",
                include_silent=False,
                conn=conn,
                workspace=workspace,
            ),
        ),
        (
            "narrative-index",
            lambda: narrative_index.narrative_index(
                asof=target_asof,
                window="1y",
                include_silent=False,
                conn=conn,
                workspace=workspace,
            ),
        ),
        (
            "read-through",
            lambda: read_through.read_through(asof=target_asof, conn=conn, workspace=workspace),
        ),
        (
            "rotation-quadrant",
            lambda: rotation_quadrant.rotation_quadrant(
                asof=target_asof,
                group_by="link",
                include_silent=False,
                conn=conn,
                workspace=workspace,
            ),
        ),
        (
            "etfs",
            lambda: etfs.etfs(asof=target_asof, window="1m", conn=conn, workspace=workspace),
        ),
        (
            "overlap-map",
            lambda: overlap_map.overlap_map(
                asof=target_asof, include_silent=False, conn=conn, workspace=workspace
            ),
        ),
        (
            "filing-stream/cards",
            lambda: filing_stream.filing_stream_cards(
                asof=target_asof, conn=conn, workspace=workspace
            ),
        ),
        (
            "chain-map/cards",
            lambda: chain_map.chain_map_cards(asof=target_asof, conn=conn, workspace=workspace),
        ),
        (
            "evidence-ledger/cards",
            lambda: evidence_ledger.evidence_ledger_cards(
                asof=target_asof, conn=conn, workspace=workspace
            ),
        ),
        (
            "fundamentals-multiples/cards",
            lambda: fundamentals_multiples.multiples_cards(
                asof=target_asof, conn=conn, workspace=workspace
            ),
        ),
        (
            "narrative-adoption/cards",
            lambda: narrative_adoption.adoption_cards(
                asof=target_asof, conn=conn, workspace=workspace
            ),
        ),
        (
            "chain-wave/cards",
            lambda: chain_wave.chain_wave_cards(asof=target_asof, conn=conn, workspace=workspace),
        ),
    )

    for label, fn in steps:
        aborted, _ = _run_step(label, fn)
        if aborted:
            return True, errors

    # 21. Board / call_cache step: warm read_call for every live thesis in the workspace
    aborted, theses = _run_step(
        "board-calls",
        lambda: thesis_repo.list_all(conn, workspace=workspace),
    )
    if aborted:
        return True, errors
    if theses:
        for thesis in theses:
            aborted, _ = _run_step(
                "board-calls",
                lambda t=thesis: theses_router.read_call(conn, t, target_asof, mclock=mclock),
            )
            if aborted:
                return True, errors

    return False, errors


def _run_warm_job(job: WarmJob) -> None:
    """Background worker body for a single-flight warm pass."""
    global _running, _active_workspace, _active_asof, _current_page
    global _last_finished_at, _last_wall_s, _error
    t0 = _monotonic()
    errors: list[str] = []
    conn: psycopg.Connection | None = None
    try:
        conn = _connect_job()
        if isinstance(conn, psycopg.Connection):
            name_session(conn, "alphadeck-dashboard-warm")
        if job.is_auto:
            try:
                holder = _describe_holder(conn)
            except Exception:  # noqa: BLE001 — fail-open
                holder = None
            finally:
                _end_tx(conn)
            if holder is not None:
                _log.info("skipping auto-warm: nightly pass active (%s)", holder)
                return

        if job.workspace is not None:
            workspaces = [job.workspace]
        else:
            workspaces = _list_workspaces(conn)
            _end_tx(conn)

        start_gen = CACHE._observe(conn)
        _end_tx(conn)
        mclock = market_clock()

        for ws in workspaces:
            if job.is_auto:
                with _lock:
                    if _last_auto_warmed.get((ws, job.asof)) == start_gen:
                        continue
            ws_aborted, ws_errors = _warm_workspace(conn, ws, job.asof, mclock, start_gen)
            errors.extend(ws_errors)
            if ws_aborted:
                break
            if not ws_errors:
                with _lock:
                    _last_auto_warmed[(ws, job.asof)] = start_gen
    except Exception as exc:  # noqa: BLE001 — never crash background thread silently
        _log.warning("dashboard warm job failed", exc_info=True)
        errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        _close_conn(conn)
        wall_s = round(_monotonic() - t0, 2)
        finished_at = _utc_now_iso()
        with _lock:
            _running = False
            _active_workspace = None
            _active_asof = None
            _current_page = None
            _last_finished_at = finished_at
            _last_wall_s = wall_s
            _error = errors[0] if errors else None


def _launch_job(job: WarmJob) -> None:
    """Invoke the configured executor outside ``_lock``, cleaning up state if spawning fails."""
    global _running, _active_workspace, _active_asof, _current_page, _error
    try:
        _DEFAULT_EXECUTOR(job, lambda: _run_warm_job(job))
    except Exception as exc:  # noqa: BLE001
        _log.warning("failed to launch dashboard warm job", exc_info=True)
        with _lock:
            _running = False
            _active_workspace = None
            _active_asof = None
            _current_page = None
            _error = f"{type(exc).__name__}: {exc}"


# --- public triggers ------------------------------------------------------------------------------------------


def start_warm(
    workspace: UUID | None,
    asof: date,
    *,
    reason: str = "manual",
) -> bool:
    """Start a single-flight warm job (or return ``False`` if one is already running)."""
    global _running, _active_workspace, _active_asof, _current_page
    global _last_started_at, _last_reason, _error
    with _lock:
        if _running:
            return False
        _running = True
        _active_workspace = workspace
        _active_asof = asof
        _current_page = None
        _last_started_at = _utc_now_iso()
        _last_reason = reason
        _error = None
    _launch_job(WarmJob(workspace=workspace, asof=asof, reason=reason, is_auto=False, delay_s=0.0))
    return True


def request_auto_warm(
    workspace: UUID | None = None,
    asof: date | None = None,
    reason: str = "auto",
) -> bool:
    """Auto-warm entry point registered with ``app.warm_hook``.

    Always targets ``current_night()`` (never historical dates), respects kill switches,
    skips during pytest or an active nightly pass, debounces rapid repeated triggers, and
    short-circuits in memory when ``(workspace, night, generation)`` is already warm.
    """
    global _running, _active_workspace, _active_asof, _current_page
    global _last_started_at, _last_reason, _error
    del asof  # auto-warm always targets the current night, never historical dates
    if _auto_warm_disabled():
        return False

    target_asof = current_night()
    now_mono = _monotonic()
    debounce_key = (workspace, target_asof, reason)

    with _lock:
        if _running:
            return False
        if reason in _SESSION_REASONS:
            if workspace is not None:
                mem_gen = _cached_generation()
                if (
                    mem_gen is not None
                    and _last_auto_warmed.get((workspace, target_asof)) == mem_gen
                ):
                    return False
        else:
            if workspace is None:
                _last_auto_warmed.clear()
            else:
                for key in [k for k in _last_auto_warmed if k[0] == workspace]:
                    del _last_auto_warmed[key]

        if reason not in ("boot", "admin_daily"):
            prev = _last_auto_trigger_at.get(debounce_key)
            if prev is not None and (now_mono - prev) < AUTO_WARM_DEBOUNCE_S:
                return False

    if _nightly_pass_running():
        return False

    with _lock:
        if _running:
            return False
        if reason not in ("boot", "admin_daily"):
            prev = _last_auto_trigger_at.get(debounce_key)
            if prev is not None and (now_mono - prev) < AUTO_WARM_DEBOUNCE_S:
                return False
            _last_auto_trigger_at[debounce_key] = now_mono
        _running = True
        _active_workspace = workspace
        _active_asof = target_asof
        _current_page = None
        _last_started_at = _utc_now_iso()
        _last_reason = reason
        _error = None

    delay_s = BOOT_WARM_DELAY_S if reason == "boot" else 0.0
    _launch_job(
        WarmJob(
            workspace=workspace,
            asof=target_asof,
            reason=reason,
            is_auto=True,
            delay_s=delay_s,
        )
    )
    return True


# --- status read & HTTP routes --------------------------------------------------------------------------------


def _build_status(
    conn: psycopg.Connection,
    workspace: UUID,
    target_asof: date,
    mclock: MarketClock,
) -> WarmStatusOut:
    expected = _expected_cache_keys(conn, workspace, target_asof, mclock)
    warm = CACHE.count_warm(conn, workspace, target_asof, expected)

    theses = thesis_repo.list_all(conn, workspace=workspace)
    known_at, clock = serve_view(target_asof, night=mclock.night)
    decisions_asof = serve_decisions_asof(target_asof, mclock)
    fn_token = id(theses_router.call_for_thesis)
    calls_warm = sum(
        1
        for t in theses
        if call_cache.CACHE.is_warm_call(
            conn,
            thesis=t,
            asof=target_asof,
            known_at=known_at,
            clock=clock,
            decisions_asof=decisions_asof,
            fn_token=fn_token,
        )
    )

    with _lock:
        return WarmStatusOut(
            asof=target_asof,
            total=len(expected),
            warm=warm,
            calls_total=len(theses),
            calls_warm=calls_warm,
            running=_running,
            current_page=_current_page,
            last_started_at=_last_started_at,
            last_finished_at=_last_finished_at,
            last_wall_s=_last_wall_s,
            last_reason=_last_reason,
            error=_error,
        )


@router.get("/status", response_model=WarmStatusOut)
def get_warm_status(
    asof: AsofDate | None = Query(
        None, description="as-of date to inspect (defaults to the server's current night)"
    ),
    conn: psycopg.Connection = Depends(get_conn),
    workspace: UUID = Depends(get_workspace),
) -> WarmStatusOut:
    """Inspect dashboard and Board call cache warmth for the workspace at ``asof``."""
    mclock = market_clock()
    target_asof = asof if asof is not None else mclock.night
    return _build_status(conn, workspace, target_asof, mclock)


@router.post("", response_model=WarmStatusOut, status_code=202)
def trigger_warm(
    asof: AsofDate | None = Query(
        None, description="as-of date to warm (defaults to the server's current night)"
    ),
    conn: psycopg.Connection = Depends(get_conn),
    workspace: UUID = Depends(get_workspace),
) -> WarmStatusOut:
    """Start (or join) a single-flight background warm-up for the workspace at ``asof``."""
    mclock = market_clock()
    target_asof = asof if asof is not None else mclock.night
    start_warm(workspace=workspace, asof=target_asof, reason="manual")
    return _build_status(conn, workspace, target_asof, mclock)


# Wire the dependency-inversion hook at module load so core modules stay decoupled from dashboards.
warm_hook.register(request_auto_warm)
