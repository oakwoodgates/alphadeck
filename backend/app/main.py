from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers import theses, workbench
from workbench.draft_jobs import assert_single_worker


@asynccontextmanager
async def lifespan(app: FastAPI):
    # The in-process draft-job + research registries are per-process (workbench/draft_jobs,
    # workbench/research_runner): >1 worker silently breaks the 409 guard and job polls, so refuse to boot
    # when the env asks for it. Env-driven scaling only — a hand-typed CLI `--workers 2` is invisible here;
    # the Dockerfile CMD's explicit `--workers 1` is the production mitigation.
    assert_single_worker(os.environ)
    yield


app = FastAPI(
    title="Alpha Deck",
    version="0.0.0",
    summary="Decision-support call-assembler API (advisory only; the CallCard is recomputed on read).",
    lifespan=lifespan,
)
app.include_router(theses.router)
app.include_router(workbench.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
