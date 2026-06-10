from __future__ import annotations

from fastapi import FastAPI

from app.routers import theses, workbench

app = FastAPI(
    title="Alpha Deck",
    version="0.0.0",
    summary="Decision-support call-assembler API (advisory only; the CallCard is recomputed on read).",
)
app.include_router(theses.router)
app.include_router(workbench.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
