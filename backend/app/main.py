from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.health import HealthResponse, compute_health, enforce_boot_visibility
from app.routers import admin, radar, scoreboard, theses, workbench
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
    yield


app = FastAPI(
    title="Alpha Deck",
    version="0.0.0",
    summary="Decision-support call-assembler API (advisory only; the CallCard is recomputed on read).",
    lifespan=lifespan,
)
app.include_router(theses.router)
app.include_router(workbench.router)
app.include_router(scoreboard.router)
app.include_router(admin.router)
app.include_router(radar.router)


@app.get("/health", tags=["meta"], response_model=HealthResponse)
def health() -> HealthResponse:
    # The compose healthcheck hits this; it only needs the 200. `degraded` is a body flag an operator/agent
    # SEES (e.g. `{"user_agent": "missing"}`), never a failed check — fail-open (app/health.py).
    return compute_health(get_settings().user_agent)
