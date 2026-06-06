# Alpha Deck — backend

Python project root. Packages (built through M3a):

- `domain/` — the spine: `Thesis`, `SignalEvent`, `CallCard`, `CallConfig` (Pydantic).
- `calls/` — the **call-assembler**: pure, deterministic, golden-tested `assemble_call(...)`.
- `signals/` — pure detectors: `insider_conviction` (Key 1, warms), `volume_breakout` (Key 2, arms), `scan`.
- `ingest/` — cached/polite EDGAR client + Form 4 parse; EOD prices (Yahoo, Stooq parser as fallback).
- `db/` — bitemporal Postgres store: migrations, `as_of(asof, known_at)` reads, append-only facts.
- `securities/` — canonical security master + entity resolution (OpenFIGI + SEC CIK, cache-first).
- `repositories/` — row↔domain mappers + `thesis_repo` / `calls_repo` (raw rows never escape).
- `pipeline/` — `call_for_thesis` (re-derive signals from facts → assemble → log), `seed`, `run` CLIs.
- `app/` — FastAPI: `GET /theses`, `/theses/{id}`, `/theses/{id}/call?asof=` + the `schemas_api` wire contract.
- (later) `llm/` (M4b), `replay/` (M5).

## Dev setup

`uv` is not installed on this machine, so we use a stdlib venv + pip (pyproject stays the source of truth).

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install "pydantic>=2.6" "psycopg[binary]>=3.1" "httpx>=0.27" "fastapi>=0.110" "uvicorn>=0.29" pytest ruff black
```

## Database

Postgres runs via Docker Compose (host port 5544 to avoid clashing with a local Postgres):

```powershell
docker compose -f ..\infra\docker-compose.yml up -d
```

`DATABASE_URL` defaults to `postgresql://alphadeck:alphadeck@localhost:5544/alphadeck`. Migrations are
idempotent and apply from `db/migrations/`:

```powershell
$env:PYTHONPATH = "backend"; backend\.venv\Scripts\python -m db.migrate
```

DB-backed tests **skip automatically** if Postgres isn't reachable; CI runs them against a Postgres service.

## Run (from the repo root)

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m pytest backend\tests      # tests (DB tests skip if no Postgres)
backend\.venv\Scripts\python -m ruff check backend         # lint
backend\.venv\Scripts\python -m black --check backend      # format check
```

## Seed + serve the API

After Postgres is up and migrations applied (`python -m db.migrate`):

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m pipeline.seed                  # seed the HIMS demo thesis (idempotent)
backend\.venv\Scripts\python -m uvicorn app.main:app --reload  # serve on http://127.0.0.1:8000
```

`pipeline.seed` prints the thesis id; then the served call (Checkpoint A):

```powershell
curl "http://127.0.0.1:8000/theses/<thesis-id>/call?asof=2026-06-01"   # state: armed (starter_entry)
curl "http://127.0.0.1:8000/theses/<thesis-id>/call?asof=2026-05-28"   # state: warming
```

`app/openapi_export.py` dumps `openapi.json` — the frontend (M3b) generates its TS types from it.
