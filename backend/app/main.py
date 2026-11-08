from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_redoc_html, get_swagger_ui_html
from fastapi.responses import HTMLResponse, JSONResponse

from app.auth import require_super_admin, require_user
from app.health import HealthResponse, compute_health, enforce_boot_visibility
from app.routers import (
    account,
    admin,
    admin_insider_amendments,
    admin_onboarding,
    admin_price_feed,
    admin_splits,
    auth,
    backtest,
    clock,
    dashboards,
    data_flags,
    fund_radar,
    oauth_consent,
    radar,
    scoreboard,
    theses,
    workbench,
)
from domain.settings import get_settings
from workbench.draft_jobs import assert_single_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The in-process draft-job + research registries are per-process (workbench/draft_jobs,
    # workbench/research_runner): >1 worker silently breaks the 409 guard and job polls, so refuse to boot
    # when the env asks for it. Env-driven scaling only — a hand-typed CLI `--workers 2` is invisible here;
    # the Dockerfile CMD's explicit `--workers 1` is the production mitigation.
    assert_single_worker(os.environ)
    # Boot-visibility guard: surface a missing SEC User-Agent LOUDLY at boot (an ERROR log + a /health
    # `degraded` flag), rather than 11 filings deep at the first EDGAR call. Fail-open by default; opt into
    # a hard boot failure with ALPHADECK_REQUIRE_UA=true. See app/health.py.
    _settings = get_settings()
    enforce_boot_visibility(_settings.user_agent, require_ua=_settings.require_ua)
    # The two tenant scopes (docs/TENANT_SCOPES.md): every feed write and DATA read names the data tenant, so
    # refuse to boot — loudly, never a silent default — when its row is missing or not `kind='data'` (0076).
    # Imported here so this guard stays inside the lifespan body (the module's imports are untouched).
    from db.session import connect as _connect
    from domain.scopes import assert_data_tenant

    _conn = _connect()
    try:
        assert_data_tenant(_conn)
    finally:
        _conn.close()
    # The hosted agent tools endpoint (docs/AGENT_TOOLS.md §Hosted endpoint): mounted below only when
    # ALPHADECK_PUBLIC_ORIGIN is set; its session manager's task group lives exactly as long as the API.
    hosted = getattr(app.state, "hosted", None)
    if hosted is None:
        yield
    else:
        async with hosted.lifespan():
            yield


app = FastAPI(
    title="Alpha Deck",
    version="0.0.0",
    summary="Decision-support call-assembler API (advisory only; the CallCard is recomputed on read).",
    lifespan=lifespan,
    # The schema pages are served from gated routes below (docs/AUTH.md), never open: the built-in ones are off.
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
# The login door (docs/AUTH.md): EVERY router is mounted behind `require_user` (or `require_super_admin` for the
# steward routers), on the ROUTER, never inside a handler, so a request with no session answers 401 before any
# parameter is validated. tests/app/test_auth_gate.py walks app.routes: a router mounted open fails it.
_USER = [Depends(require_user)]
_STEWARD = [Depends(require_super_admin)]
# /auth decides per route: POST /auth/login is open, the rest carry require_user on their own decorators.
app.include_router(auth.router)
# the signed-in user's own settings (the Settings page, docs/AUTH.md)
app.include_router(account.router, dependencies=_USER)
# the OAuth consent page's read + decision (docs/CONNECTORS.md): the browser session only, checked per route
app.include_router(oauth_consent.router, dependencies=_USER)
app.include_router(theses.router, dependencies=_USER)
# F1 — the server's current night / action day / today: the app's default as-of, never the browser's date
app.include_router(clock.router, dependencies=_USER)
app.include_router(workbench.router, dependencies=_USER)
app.include_router(scoreboard.router, dependencies=_USER)
app.include_router(admin.router, dependencies=_STEWARD)
# T2 + Q8 — the operator's price-feed repairs (Adopt / Set / Acknowledge, each with its exact inverse)
app.include_router(admin_price_feed.router, dependencies=_STEWARD)
app.include_router(admin_insider_amendments.router, dependencies=_STEWARD)  # R8-A
# split plan S1 — the split list's review panel
app.include_router(admin_splits.router, dependencies=_STEWARD)
# X1 — the staged cold-ingest queue
app.include_router(admin_onboarding.router, dependencies=_STEWARD)
# split plan S3 — the shared "is this real?" data flags (compute-on-read, display-only)
app.include_router(data_flags.router, dependencies=_USER)
app.include_router(radar.router, dependencies=_USER)
# the fund launch radar display (docs/FUND_RADAR.md)
app.include_router(fund_radar.router, dependencies=_USER)
# The Dashboards (docs/DASHBOARDS.md) — experimental, display-only cross-card views. ONE include: each
# dashboard mounts its own router inside app/routers/dashboards/, never here.
app.include_router(dashboards.router, dependencies=_USER)
# /backtest is ARTIFACT-served and available BY DATA AVAILABILITY -- it is registered
# unconditionally and reports `available: false` where no store is mounted, so there is no build
# flag or env var anyone can get wrong. Dev/sig mount the live store; prod mounts a frozen archive.
# It never writes and is never linked from Board/Cockpit/Scoreboard.
app.include_router(backtest.router, dependencies=_STEWARD)


@app.get("/health", tags=["meta"], response_model=HealthResponse)
def health() -> HealthResponse:
    # The compose healthcheck hits this; it only needs the 200. `degraded` is a body flag an operator/agent
    # SEES (e.g. `{"user_agent": "missing"}`), never a failed check — fail-open (app/health.py).
    return compute_health(get_settings().user_agent)


# The schema pages, behind the login (docs/AUTH.md). `app.openapi_export` builds the contract in-process from
# `app.openapi()`, so it does not go through these routes.
@app.get("/openapi.json", include_in_schema=False, dependencies=_USER)
def openapi_json() -> JSONResponse:
    return JSONResponse(app.openapi())


@app.get("/docs", include_in_schema=False, dependencies=_USER)
def swagger_docs(request: Request) -> HTMLResponse:
    root = request.scope.get("root_path", "")
    return get_swagger_ui_html(openapi_url=f"{root}/openapi.json", title=f"{app.title} - docs")


@app.get("/redoc", include_in_schema=False, dependencies=_USER)
def redoc_docs(request: Request) -> HTMLResponse:
    root = request.scope.get("root_path", "")
    return get_redoc_html(openapi_url=f"{root}/openapi.json", title=f"{app.title} - ReDoc")


def _mount_hosted() -> None:
    """ALPHADECK_PUBLIC_ORIGIN set = the agent tools server answers at /mcp (plus its RFC 9728 document) and our own
    OAuth authorization server answers at /authorize, /token, /register, /revoke and its RFC 8414 document, all
    appended AFTER every route above (docs/AGENT_TOOLS.md §Hosted endpoint, docs/CONNECTORS.md). Unset = nothing
    is imported or mounted. The imports are lazy: with the origin set and the `mcp` package missing, boot FAILS
    with one clear line.
    """
    settings = get_settings()
    if not settings.public_origin:
        return
    try:
        from agent_tools.hosted import mount_hosted
        from app.oauth.server import mount_oauth
    except ModuleNotFoundError as exc:
        if exc.name and (exc.name == "mcp" or exc.name.startswith("mcp.")):
            raise RuntimeError(
                "ALPHADECK_PUBLIC_ORIGIN is set but the `mcp` package (mcp==2.3.0) is not installed: "
                "the hosted /mcp endpoint cannot be mounted"
            ) from exc
        raise
    mount_hosted(app, settings)
    mount_oauth(app, settings)


_mount_hosted()
