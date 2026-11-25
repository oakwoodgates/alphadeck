"""Tests for ``app.warm_hook`` and ``app.routers.dashboards.warm`` (pure — no live database)."""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from fastapi.params import Param
from fastapi.routing import APIRoute

from app import call_cache, warm_hook
from app.auth import require_user
from app.main import app
from app.routers import theses as theses_router
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
    warm,
)
from dashboards.cache import CACHE, Generation
from domain.market_time import MarketClock, countdown_origin, serve_decisions_asof, serve_view
from domain.thesis import Thesis

NIGHT = date(2026, 6, 12)
ACTION_DAY = date(2026, 6, 15)
MCLOCK = MarketClock(night=NIGHT, action_day=ACTION_DAY, today=ACTION_DAY)
WS_A = UUID("11111111-1111-1111-1111-111111111111")
WS_B = UUID("22222222-2222-2222-2222-222222222222")


class _FakeCursor:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.executed: list[str] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append(sql)

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def __enter__(self) -> _FakeCursor:
        return self

    def __exit__(self, *exc: object) -> None:
        return None


class _FakeConn:
    def __init__(self, workspace_ids: list[UUID] | None = None) -> None:
        self._workspace_ids = workspace_ids or [WS_A]
        self.rollbacks = 0
        self.closed = False
        self.queries: list[str] = []

    def cursor(self) -> _FakeCursor:
        cur = _FakeCursor([{"id": ws} for ws in self._workspace_ids])
        orig_exec = cur.execute

        def _rec(sql: str, params: Any = None) -> None:
            self.queries.append(sql)
            orig_exec(sql, params)

        cur.execute = _rec  # type: ignore[method-assign]
        return cur

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


def _make_thesis(workspace: UUID, name: str = "AI Power") -> Thesis:
    return Thesis(
        id=uuid4(),
        tenant_id=workspace,
        name=name,
        ticker="PWR",
        narrative="Test thesis narrative",
    )


@pytest.fixture(autouse=True)
def _clean_warm_env(monkeypatch: pytest.MonkeyPatch):
    """Isolate cache, call cache, warm engine state, and clock for every test."""
    CACHE.clear()
    call_cache.CACHE.clear()
    warm.reset_state()

    gen = {"value": Generation(hard=("run-1", (None, 0)), soft=("snap-1", ()), stamps=None)}
    monkeypatch.setattr(CACHE, "_token_fn", lambda _conn: gen["value"])
    monkeypatch.setattr(warm, "market_clock", lambda: MCLOCK)
    monkeypatch.setattr(warm, "current_night", lambda: NIGHT)
    monkeypatch.setattr(
        warm, "latest_run_identity", lambda: ("2026-06-12T22:00:00Z", "ok", "2026-06-12T22:10:00Z")
    )
    monkeypatch.setattr(warm, "_probe_pass_lock", lambda: None)
    monkeypatch.setattr(warm, "_describe_holder", lambda _conn: None)
    monkeypatch.setattr(track_record, "_resolve_edition", lambda _conn, _ws, _ed: None)

    yield gen

    warm.reset_state()
    CACHE.clear()
    call_cache.CACHE.clear()
    warm_hook.register(warm.request_auto_warm)


def _install_stub_routers(
    monkeypatch: pytest.MonkeyPatch,
    *,
    theses_by_ws: dict[UUID, list[Thesis]] | None = None,
    peer_scopes: tuple[str | None, str | None, int] = ("business_type", "semis", 5),
    fail_on: str | None = None,
    on_step: Any = None,
) -> list[tuple[str, dict[str, Any]]]:
    """Patch all 25 sibling dashboard handlers + thesis_repo + read_call to record calls and populate caches."""
    recorded: list[tuple[str, dict[str, Any]]] = []
    theses_map = theses_by_ws if theses_by_ws is not None else {WS_A: [_make_thesis(WS_A)]}
    warmed_calls: set[tuple[UUID, date]] = set()

    def _assert_no_fastapi_defaults(kwargs: dict[str, Any]) -> None:
        for k, v in kwargs.items():
            assert not isinstance(v, Param), f"FastAPI Param leaked for argument {k!r}: {v!r}"

    def _put_cache(
        conn: Any, slug: str, params: tuple[Any, ...], asof: date, workspace: UUID, val: Any = "ok"
    ) -> None:
        CACHE.get(conn, slug, params, asof, lambda: val, workspace=workspace)

    def _make_stub(label: str, slug: str, params_fn: Any, ret_fn: Any = None):
        def _handler(**kwargs: Any) -> Any:
            _assert_no_fastapi_defaults(kwargs)
            recorded.append((label, dict(kwargs)))
            if on_step is not None:
                on_step(label, kwargs)
            if fail_on == label:
                raise RuntimeError(f"boom in {label}")
            asof = kwargs["asof"]
            conn = kwargs["conn"]
            ws = kwargs["workspace"]
            _, clock = serve_view(asof, night=MCLOCK.night)
            origin = countdown_origin(asof, MCLOCK)
            val = ret_fn(kwargs) if ret_fn else {"ok": True}
            _put_cache(conn, slug, params_fn(kwargs, clock, origin), asof, ws, val)
            return val

        return _handler

    default_scope, default_key, companies = peer_scopes
    scopes_obj = SimpleNamespace(
        default_scope=default_scope,
        default_key=default_key,
        companies=companies,
    )
    scopes_cache_val = SimpleNamespace(found=scopes_obj)

    monkeypatch.setattr(
        theme_leaderboard,
        "theme_leaderboard",
        _make_stub("theme-leaderboard", "theme-leaderboard", lambda kw, c, o: (c,)),
    )

    def _peer_scopes_handler(**kwargs: Any) -> Any:
        _assert_no_fastapi_defaults(kwargs)
        recorded.append(("peer-table/scopes", dict(kwargs)))
        if on_step is not None:
            on_step("peer-table/scopes", kwargs)
        if fail_on == "peer-table/scopes":
            raise RuntimeError("boom in peer-table/scopes")
        asof, conn, ws = kwargs["asof"], kwargs["conn"], kwargs["workspace"]
        _, clock = serve_view(asof, night=MCLOCK.night)
        _put_cache(conn, "peer-table", ("scopes", clock), asof, ws, scopes_cache_val)
        return scopes_obj

    def _peer_table_handler(**kwargs: Any) -> Any:
        _assert_no_fastapi_defaults(kwargs)
        scope = kwargs["scope"]
        key = kwargs.get("key")
        label = f"peer-table:{scope}"
        recorded.append((label, dict(kwargs)))
        if on_step is not None:
            on_step(label, kwargs)
        if fail_on == label:
            raise RuntimeError(f"boom in {label}")
        asof, conn, ws = kwargs["asof"], kwargs["conn"], kwargs["workspace"]
        _, clock = serve_view(asof, night=MCLOCK.night)
        pick = "" if scope == "all" else str(key or "")
        _put_cache(conn, "peer-table", (scope, pick, clock), asof, ws, {"scope": scope})
        return {"scope": scope}

    monkeypatch.setattr(peer_table, "peer_table_scopes", _peer_scopes_handler)
    monkeypatch.setattr(peer_table, "peer_table", _peer_table_handler)
    monkeypatch.setattr(
        market_map, "market_map", _make_stub("market-map", "market-map", lambda kw, c, o: (c,))
    )
    monkeypatch.setattr(
        insider_tracker,
        "insider_tracker_page",
        _make_stub(
            "insider-tracker",
            "insider-tracker",
            lambda kw, c, o: (int(kw["days"]), kw["silent"], c),
        ),
    )
    monkeypatch.setattr(
        daily_digest,
        "daily_digest_page",
        _make_stub("daily-digest", "daily-digest", lambda kw, c, o: (c, o)),
    )
    monkeypatch.setattr(
        trigger_log,
        "trigger_log_page",
        _make_stub("trigger-log", "trigger-log", lambda kw, c, o: (int(kw["days"]), kw["role"], c)),
    )
    monkeypatch.setattr(
        hot_board,
        "hot_board",
        _make_stub(
            "hot-board",
            "hot-board",
            lambda kw, c, o: (kw["rows"], c, o),
        ),
    )
    monkeypatch.setattr(
        track_record,
        "track_record",
        _make_stub(
            "track-record",
            "track-record",
            lambda kw, c, o: (0, not track_record.resolve_exclude_late(None), c),
        ),
    )
    monkeypatch.setattr(
        forming_quietly,
        "forming_quietly",
        _make_stub("forming-quietly", "forming-quietly", lambda kw, c, o: (c,)),
    )
    monkeypatch.setattr(
        entry_windows,
        "entry_windows_page",
        _make_stub("entry-windows", "entry-windows", lambda kw, c, o: (c, o)),
    )
    monkeypatch.setattr(leaders, "leaders", _make_stub("leaders", "leaders", lambda kw, c, o: (c,)))
    monkeypatch.setattr(
        rotation_map,
        "rotation_map",
        _make_stub("rotation-map", "rotation-map", lambda kw, c, o: (c,)),
    )
    monkeypatch.setattr(
        cross_radar, "cross_radar", _make_stub("cross-radar", "cross-radar", lambda kw, c, o: (c,))
    )
    monkeypatch.setattr(landing, "landing", _make_stub("landing", "landing", lambda kw, c, o: (c,)))
    monkeypatch.setattr(
        narrative_index,
        "narrative_index",
        _make_stub(
            "narrative-index", "narrative-index", lambda kw, c, o: (c, kw["include_silent"])
        ),
    )
    monkeypatch.setattr(
        read_through,
        "read_through",
        _make_stub("read-through", "read-through", lambda kw, c, o: (c,)),
    )
    monkeypatch.setattr(
        rotation_quadrant,
        "rotation_quadrant",
        _make_stub(
            "rotation-quadrant", "rotation-quadrant", lambda kw, c, o: (c, kw["include_silent"])
        ),
    )
    monkeypatch.setattr(etfs, "etfs", _make_stub("etfs", "etfs", lambda kw, c, o: (c,)))
    monkeypatch.setattr(
        overlap_map,
        "overlap_map",
        _make_stub("overlap-map", "overlap-map", lambda kw, c, o: (c, kw["include_silent"])),
    )
    monkeypatch.setattr(
        filing_stream,
        "filing_stream_cards",
        _make_stub("filing-stream/cards", "filing-stream", lambda kw, c, o: ("cards", c)),
    )
    monkeypatch.setattr(
        chain_map,
        "chain_map_cards",
        _make_stub("chain-map/cards", "chain-map", lambda kw, c, o: ("cards", c)),
    )
    monkeypatch.setattr(
        evidence_ledger,
        "evidence_ledger_cards",
        _make_stub("evidence-ledger/cards", "evidence-ledger", lambda kw, c, o: ("cards", c)),
    )
    monkeypatch.setattr(
        fundamentals_multiples,
        "multiples_cards",
        _make_stub(
            "fundamentals-multiples/cards", "fundamentals-multiples", lambda kw, c, o: ("cards", c)
        ),
    )
    monkeypatch.setattr(
        narrative_adoption,
        "adoption_cards",
        _make_stub("narrative-adoption/cards", "narrative-adoption", lambda kw, c, o: ("cards", c)),
    )
    monkeypatch.setattr(
        chain_wave,
        "chain_wave_cards",
        _make_stub("chain-wave/cards", "chain-wave", lambda kw, c, o: ("cards", c)),
    )

    monkeypatch.setattr(
        warm.thesis_repo,
        "list_all",
        lambda _conn, *, workspace, include_archived=False: list(theses_map.get(workspace, [])),
    )

    def _read_call_stub(
        _conn: Any, thesis: Thesis, asof: date, *, mclock: MarketClock
    ) -> dict[str, Any]:
        recorded.append(("board-call", {"thesis_id": thesis.id, "asof": asof, "mclock": mclock}))
        if on_step is not None:
            on_step("board-call", {"thesis_id": thesis.id, "asof": asof})
        if fail_on == "board-call":
            raise RuntimeError("boom in board-call")
        warmed_calls.add((thesis.id, asof))
        return {"thesis_id": str(thesis.id)}

    monkeypatch.setattr(theses_router, "read_call", _read_call_stub)
    monkeypatch.setattr(
        call_cache.CACHE,
        "is_warm_call",
        lambda _conn, *, thesis, asof, known_at, clock, decisions_asof=None, fn_token=0: (
            (thesis.id, asof) in warmed_calls
            and fn_token == id(theses_router.call_for_thesis)
            and decisions_asof == serve_decisions_asof(asof, MCLOCK)
        ),
    )
    return recorded


# --- warm_hook unit tests -------------------------------------------------------------------------------------


def test_warm_hook_returns_false_when_unregistered():
    warm_hook.register(None)
    assert warm_hook.request_warm(WS_A, NIGHT, reason="session") is False


def test_warm_hook_forwards_arguments_and_returns_handler_result():
    seen: list[tuple[UUID | None, date | None, str]] = []

    def _handler(ws: UUID | None, asof: date | None, reason: str) -> bool:
        seen.append((ws, asof, reason))
        return True

    warm_hook.register(_handler)
    assert warm_hook.request_warm(WS_A, NIGHT, reason="mutation") is True
    assert seen == [(WS_A, NIGHT, "mutation")]


def test_warm_hook_fails_open_when_handler_raises():
    def _boom(_ws: UUID | None, _asof: date | None, _reason: str) -> bool:
        raise RuntimeError("broken handler")

    warm_hook.register(_boom)
    assert warm_hook.request_warm(WS_A, NIGHT, reason="boot") is False


# --- route wiring & warm execution tests ----------------------------------------------------------------------


def _dep_calls(dependant: Any) -> set[Any]:
    out: set[Any] = set()
    for dep in dependant.dependencies:
        out.add(dep.call)
        out |= _dep_calls(dep)
    return out


def test_routes_mounted_behind_require_user_with_expected_contract():
    routes = {
        (method, r.path): r
        for r in app.routes
        if isinstance(r, APIRoute) and r.path.startswith("/dashboards/warm")
        for method in r.methods
    }
    assert set(routes) == {
        ("GET", "/dashboards/warm/status"),
        ("POST", "/dashboards/warm"),
    }
    status_route = routes[("GET", "/dashboards/warm/status")]
    trigger_route = routes[("POST", "/dashboards/warm")]
    assert status_route.response_model is warm.WarmStatusOut
    assert trigger_route.response_model is warm.WarmStatusOut
    assert trigger_route.status_code == 202
    assert require_user in _dep_calls(status_route.dependant)
    assert require_user in _dep_calls(trigger_route.dependant)

    schema = app.openapi()
    status_op = schema["paths"]["/dashboards/warm/status"]["get"]
    post_op = schema["paths"]["/dashboards/warm"]["post"]
    assert any(p["name"] == "asof" and p["in"] == "query" for p in status_op["parameters"])
    assert any(p["name"] == "asof" and p["in"] == "query" for p in post_op["parameters"])
    assert "202" in post_op["responses"]
    props = set(schema["components"]["schemas"]["WarmStatusOut"]["properties"])
    assert props == {
        "asof",
        "total",
        "warm",
        "calls_total",
        "calls_warm",
        "running",
        "current_page",
        "last_started_at",
        "last_finished_at",
        "last_wall_s",
        "last_reason",
        "error",
    }


def test_status_and_post_warm_full_pass_in_piece_sharing_order(monkeypatch: pytest.MonkeyPatch):
    t1, t2 = _make_thesis(WS_A, "Card One"), _make_thesis(WS_A, "Card Two")
    recorded = _install_stub_routers(monkeypatch, theses_by_ws={WS_A: [t1, t2]})
    job_conn = _FakeConn([WS_A])
    req_conn = _FakeConn([WS_A])

    monkeypatch.setattr(warm, "_connect_job", lambda: job_conn)
    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", lambda _job, run: run())

    before = warm.get_warm_status(asof=None, conn=req_conn, workspace=WS_A)
    body_before = before.model_dump(mode="json")
    assert set(body_before.keys()) == {
        "asof",
        "total",
        "warm",
        "calls_total",
        "calls_warm",
        "running",
        "current_page",
        "last_started_at",
        "last_finished_at",
        "last_wall_s",
        "last_reason",
        "error",
    }
    assert body_before["asof"] == NIGHT.isoformat()
    assert body_before["total"] == 26
    assert body_before["warm"] == 0
    assert body_before["calls_total"] == 2
    assert body_before["calls_warm"] == 0
    assert body_before["running"] is False

    triggered = warm.trigger_warm(asof=None, conn=req_conn, workspace=WS_A)
    body_after = triggered.model_dump(mode="json")
    assert body_after["asof"] == NIGHT.isoformat()
    # 26 base entries + 2 peer-table follow-ups (default leaf + scope="all")
    assert body_after["total"] == 28
    assert body_after["warm"] == 28
    assert body_after["calls_total"] == 2
    assert body_after["calls_warm"] == 2
    assert body_after["running"] is False
    assert body_after["current_page"] is None
    assert body_after["last_reason"] == "manual"
    assert body_after["last_started_at"] is not None
    assert body_after["last_finished_at"] is not None
    assert body_after["last_wall_s"] is not None
    assert body_after["error"] is None

    # Verify piece-sharing optimal order and exact Board step at the end.
    labels = [label for label, _ in recorded]
    assert labels == [
        "theme-leaderboard",
        "peer-table/scopes",
        "peer-table:business_type",
        "peer-table:all",
        "market-map",
        "insider-tracker",
        "daily-digest",
        "trigger-log",
        "hot-board",
        "hot-board",
        "track-record",
        "forming-quietly",
        "entry-windows",
        "leaders",
        "rotation-map",
        "cross-radar",
        "landing",
        "narrative-index",
        "read-through",
        "rotation-quadrant",
        "etfs",
        "overlap-map",
        "filing-stream/cards",
        "chain-map/cards",
        "evidence-ledger/cards",
        "fundamentals-multiples/cards",
        "narrative-adoption/cards",
        "chain-wave/cards",
        "board-call",
        "board-call",
    ]
    # Verify hot-board ran armed then all.
    hb_rows = [kw["rows"] for label, kw in recorded if label == "hot-board"]
    assert hb_rows == ["armed", "all"]
    assert job_conn.closed is True
    assert job_conn.rollbacks >= 30


def test_post_warm_supports_historical_asof(monkeypatch: pytest.MonkeyPatch):
    hist = date(2026, 5, 1)
    recorded = _install_stub_routers(monkeypatch, peer_scopes=("all", None, 4))
    job_conn = _FakeConn([WS_A])
    req_conn = _FakeConn([WS_A])

    monkeypatch.setattr(warm, "_connect_job", lambda: job_conn)
    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", lambda _job, run: run())

    resp = warm.trigger_warm(asof=hist, conn=req_conn, workspace=WS_A)
    body = resp.model_dump(mode="json")
    assert body["asof"] == hist.isoformat()
    # 26 base + 1 peer-table follow-up (scope="all" only when default_scope="all", default_key=None)
    assert body["total"] == 27
    assert body["warm"] == 27
    assert all(kw["asof"] == hist for _, kw in recorded)


def test_peer_table_skips_follow_ups_when_workspace_has_zero_companies(
    monkeypatch: pytest.MonkeyPatch,
):
    recorded = _install_stub_routers(
        monkeypatch, theses_by_ws={WS_A: []}, peer_scopes=(None, None, 0)
    )
    job_conn = _FakeConn([WS_A])
    req_conn = _FakeConn([WS_A])

    monkeypatch.setattr(warm, "_connect_job", lambda: job_conn)
    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", lambda _job, run: run())

    resp = warm.trigger_warm(asof=None, conn=req_conn, workspace=WS_A)
    body = resp.model_dump(mode="json")
    assert body["total"] == 26
    assert body["warm"] == 26
    assert body["calls_total"] == 0
    assert body["calls_warm"] == 0
    assert not any(label.startswith("peer-table:") for label, _ in recorded)


def test_single_flight_joins_in_flight_warm_job(monkeypatch: pytest.MonkeyPatch):
    _install_stub_routers(monkeypatch)
    job_conn = _FakeConn([WS_A])
    req_conn = _FakeConn([WS_A])
    queued: list[tuple[warm.WarmJob, Any]] = []

    monkeypatch.setattr(warm, "_connect_job", lambda: job_conn)
    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", lambda job, run: queued.append((job, run)))

    first = warm.trigger_warm(asof=None, conn=req_conn, workspace=WS_A)
    assert first.running is True
    assert len(queued) == 1

    second = warm.trigger_warm(asof=None, conn=req_conn, workspace=WS_A)
    assert second.running is True
    assert len(queued) == 1  # joined existing pass, never spawned a second

    # Execute the queued job now and verify status flips to idle & warm.
    queued[0][1]()
    done = warm.get_warm_status(asof=None, conn=req_conn, workspace=WS_A)
    assert done.running is False
    assert done.warm == done.total


def test_generation_move_aborts_warm_pass_cleanly(
    monkeypatch: pytest.MonkeyPatch, _clean_warm_env: dict[str, Any]
):
    gen = _clean_warm_env

    def _bump_after_market_map(label: str, _kwargs: dict[str, Any]) -> None:
        if label == "market-map":
            gen["value"] = Generation(hard=("run-2", (None, 0)), soft=("snap-1", ()), stamps=None)

    recorded = _install_stub_routers(monkeypatch, on_step=_bump_after_market_map)
    job_conn = _FakeConn([WS_A])

    monkeypatch.setattr(warm, "_connect_job", lambda: job_conn)
    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", lambda _job, run: run())

    assert warm.start_warm(WS_A, NIGHT, reason="manual") is True
    labels = [label for label, _ in recorded]
    assert labels == [
        "theme-leaderboard",
        "peer-table/scopes",
        "peer-table:business_type",
        "peer-table:all",
        "market-map",
    ]
    assert job_conn.closed is True
    assert warm._running is False
    assert warm._error is None
    assert (WS_A, NIGHT) not in warm._last_auto_warmed


def test_per_step_failure_is_fail_open_and_records_error(monkeypatch: pytest.MonkeyPatch):
    recorded = _install_stub_routers(monkeypatch, fail_on="insider-tracker")
    job_conn = _FakeConn([WS_A])

    monkeypatch.setattr(warm, "_connect_job", lambda: job_conn)
    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", lambda _job, run: run())

    assert warm.start_warm(WS_A, NIGHT, reason="manual") is True
    labels = [label for label, _ in recorded]
    assert "insider-tracker" in labels
    assert "daily-digest" in labels
    assert "board-call" in labels
    assert warm._running is False
    assert warm._error is not None and "insider-tracker" in warm._error
    assert job_conn.closed is True


# --- auto-warm safety guards, debounce, memoization, and multi-workspace boot --------------------------------


def test_auto_warm_skipped_under_pytest_and_kill_switches(monkeypatch: pytest.MonkeyPatch):
    launched: list[warm.WarmJob] = []
    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", lambda job, _run: launched.append(job))

    # PYTEST_CURRENT_TEST is set by pytest by default -> skipped.
    assert warm.request_auto_warm(WS_A, reason="session") is False
    assert launched == []

    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    for val in ("0", "false", "OFF", "no"):
        monkeypatch.setenv("ALPHADECK_AUTO_WARM", val)
        assert warm.request_auto_warm(WS_A, reason="session") is False
    monkeypatch.delenv("ALPHADECK_AUTO_WARM", raising=False)

    for val in ("0", "false", "off", "NO"):
        monkeypatch.setenv("ALPHADECK_WARM_DASHBOARDS", val)
        assert warm.request_auto_warm(WS_A, reason="session") is False
    monkeypatch.delenv("ALPHADECK_WARM_DASHBOARDS", raising=False)
    assert launched == []


def test_auto_warm_skips_when_nightly_pass_active_and_fails_open_on_probe_error(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    launched: list[warm.WarmJob] = []
    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", lambda job, _run: launched.append(job))

    # Cron run artifact says "running" -> skipped.
    monkeypatch.setattr(
        warm, "latest_run_identity", lambda: ("2026-06-12T22:00:00Z", "running", None)
    )
    assert warm.request_auto_warm(WS_A, reason="session") is False
    assert launched == []

    # Artifact idle, but Postgres advisory lock held -> skipped.
    monkeypatch.setattr(
        warm, "latest_run_identity", lambda: ("2026-06-12T22:00:00Z", "ok", "2026-06-12T22:10:00Z")
    )
    monkeypatch.setattr(warm, "_probe_pass_lock", lambda: "pid 42 (alphadeck-pass:cron)")
    assert warm.request_auto_warm(WS_A, reason="session") is False
    assert launched == []

    # Probe raises (DB unreachable) -> fail-open, launches job.
    def _broken_probe() -> str | None:
        raise OSError("db unavailable")

    monkeypatch.setattr(warm, "_probe_pass_lock", _broken_probe)
    assert warm.request_auto_warm(WS_A, reason="session") is True
    assert len(launched) == 1


def test_auto_warm_always_targets_current_night_debounces_and_short_circuits_warm_sessions(
    monkeypatch: pytest.MonkeyPatch, _clean_warm_env: dict[str, Any]
):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    gen = _clean_warm_env
    _install_stub_routers(monkeypatch)
    job_conn = _FakeConn([WS_A])
    monkeypatch.setattr(warm, "_connect_job", lambda: job_conn)

    now = {"t": 100.0}
    monkeypatch.setattr(warm, "_monotonic", lambda: now["t"])
    jobs: list[warm.WarmJob] = []

    def _inline(job: warm.WarmJob, run: Any) -> None:
        jobs.append(job)
        run()

    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", _inline)

    # Even if a historical date is passed, auto-warm targets current_night().
    assert warm_hook.request_warm(WS_A, date(2025, 1, 2), reason="session") is True
    assert len(jobs) == 1
    assert jobs[0].asof == NIGHT
    assert jobs[0].delay_s == 0.0

    # Immediate repeat is skipped (both warm-session memo and debounce).
    now["t"] += 2.0
    assert warm_hook.request_warm(WS_A, reason="session") is False
    assert len(jobs) == 1

    # Even after debounce expires, session trigger short-circuits in memory while generation is unchanged.
    now["t"] += warm.AUTO_WARM_DEBOUNCE_S + 5.0
    assert warm_hook.request_warm(WS_A, reason="session") is False
    assert len(jobs) == 1

    # Mutation clears the warm memo and triggers a fresh pass.
    assert warm_hook.request_warm(WS_A, reason="mutation") is True
    assert len(jobs) == 2

    # Rapid second mutation within debounce window is debounced.
    now["t"] += 3.0
    assert warm_hook.request_warm(WS_A, reason="mutation") is False
    assert len(jobs) == 2

    # After debounce window and a generation change, session trigger runs again.
    gen["value"] = Generation(hard=("run-2", (None, 0)), soft=("snap-2", ()), stamps=None)
    CACHE._observe(job_conn)
    now["t"] += warm.AUTO_WARM_DEBOUNCE_S + 5.0
    assert warm_hook.request_warm(WS_A, reason="session") is True
    assert len(jobs) == 3


def test_boot_auto_warm_uses_delay_and_warms_all_workspaces(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    t_a = _make_thesis(WS_A, "Workspace A Card")
    t_b = _make_thesis(WS_B, "Workspace B Card")
    recorded = _install_stub_routers(monkeypatch, theses_by_ws={WS_A: [t_a], WS_B: [t_b]})
    job_conn = _FakeConn([WS_A, WS_B])
    monkeypatch.setattr(warm, "_connect_job", lambda: job_conn)

    seen_jobs: list[warm.WarmJob] = []

    def _inline(job: warm.WarmJob, run: Any) -> None:
        seen_jobs.append(job)
        run()

    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", _inline)

    assert warm_hook.request_warm(None, reason="boot") is True
    assert len(seen_jobs) == 1
    assert seen_jobs[0].workspace is None
    assert seen_jobs[0].asof == NIGHT
    assert seen_jobs[0].delay_s == warm.BOOT_WARM_DELAY_S
    assert any("FROM tenant WHERE kind = 'workspace'" in q for q in job_conn.queries)

    warmed_workspaces = {kw["workspace"] for _, kw in recorded if "workspace" in kw}
    assert warmed_workspaces == {WS_A, WS_B}
    warmed_theses = {kw["thesis_id"] for label, kw in recorded if label == "board-call"}
    assert warmed_theses == {t_a.id, t_b.id}


def test_auto_warm_aborts_in_worker_if_pass_lock_held(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    recorded = _install_stub_routers(monkeypatch)
    job_conn = _FakeConn([WS_A])
    monkeypatch.setattr(warm, "_connect_job", lambda: job_conn)
    monkeypatch.setattr(warm, "_describe_holder", lambda _conn: "pid 99 (alphadeck-pass:cron)")
    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", lambda _job, run: run())

    assert warm.request_auto_warm(WS_A, reason="session") is True
    assert recorded == []
    assert warm._running is False
    assert warm._error is None
    assert job_conn.closed is True


def test_executor_spawn_error_resets_running_state(monkeypatch: pytest.MonkeyPatch):
    def _fail_spawn(_job: warm.WarmJob, _run: Any) -> None:
        raise RuntimeError("thread limit reached")

    monkeypatch.setattr(warm, "_DEFAULT_EXECUTOR", _fail_spawn)
    assert warm.start_warm(WS_A, NIGHT, reason="manual") is True
    assert warm._running is False
    assert warm._error is not None and "thread limit reached" in warm._error
