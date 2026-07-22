# Alpha Deck — backend

Python project root. Packages (the file-by-file map is `docs/PROJECT_LAYOUT.md`):

- `domain/` — the spine: `Thesis`, `Segment`, `BasketMember`, `SignalEvent`, `CallCard`, `CallConfig` (Pydantic).
- `calls/` — the **call-assembler**: pure, deterministic, golden-tested `assemble_call(...)`.
- `signals/` — pure detectors: `insider_conviction` (Key 1, warms), `volume_breakout` (Key 2, arms), `catalyst_conviction`, `theme_conviction`, `dilution_clock`, `scan`.
- `ingest/` — cached/polite ingest bricks: the EDGAR client + Form 4 (+ `http.polite_get`'s 429/5xx backoff); the **PriceSource seam** (`prices/source.py` — `get_bars`; Yahoo/Stooq adapters; the recurring path **force-refreshes**); the DOE catalyst feed; the ratify bridges (`revenue_mix` / `shares` / `cash_burn`).
- `db/` — bitemporal Postgres store: migrations, `as_of(asof, known_at)` reads, append-only facts.
- `securities/` — canonical security master + entity resolution (`search` discovery net · `populate_universe` broadener · OpenFIGI + SEC CIK, cache-first).
- `workbench/` — the scorer (the four pip meters, re-derived on read) + `chain_draft` (the exact-membership resolver).
- `llm/` — the two LLM seams (`flag_explanation` + `chain_decomposition`), model-agnostic, fail-open, SDK lazy-imported.
- `repositories/` — row↔domain mappers + `thesis_repo` / `calls_repo` (`append` · `record_if_changed`); raw rows never escape.
- `pipeline/` — `call_for_thesis` (re-derive → assemble → log); **`ingest_thesis`** (M2 per-thesis back-half ingest) + **`daily`** (the call-of-record cron); `seed` / `run` / `populate_master` / `provision_tenant` / `ratify_*` CLIs.
- `replay/` — the DuckDB/Parquet point-in-time backtest harness.
- `app/` — FastAPI: `/theses` · `/theses/{id}/call?asof=` · `/workbench/*` (scored · securities · extract · facts · promote · draft-chain) + the `schemas_api` wire contract.

## Dev setup

`uv` is not installed on this machine, so we use a stdlib venv + pip (pyproject stays the source of truth).

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install "pydantic>=2.6" "psycopg[binary]>=3.1" "httpx>=0.27" "fastapi>=0.110" "uvicorn>=0.29" "anthropic>=0.40" pytest ruff black
```

## Database

Postgres runs via Docker Compose (host port 5544 to avoid clashing with a local Postgres):

```powershell
docker compose -f ..\infra\docker-compose.yml up -d
```

`DATABASE_URL` defaults to `postgresql://alphadeck:alphadeck@localhost:5544/alphadeck`. (Under `pytest` this
is overridden to an auto-derived, per-worktree `alphadeck_test_<hash>` — see `db/testdb.py`; the default above
is the app/dev URL.) Migrations are idempotent and apply from `db/migrations/`:

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

## Feed the platform (M2 — `docs/FEED_LOOP.md`)

Pull a thesis's back-half facts (insider Form 4 + EOD) so it can WARM/ARM, then run the daily
call-of-record cron. Both are **idempotent** (a re-run on unchanged data appends nothing):

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m pipeline.ingest_thesis --thesis <thesis-id>   # per-thesis back-half ingest (incremental, fail-visible)
backend\.venv\Scripts\python -m pipeline.daily                                # per thesis: ingest -> assemble -> append the call-of-record
```

Or let the **cron sidecar** run it on the US-close schedule — it's **on by default** in the full stack:

```powershell
docker compose up -d --build                                                  # the cron sidecar starts with the stack
docker compose up -d --scale cron=0                                           # ...or skip it for one run
```

`app/openapi_export.py` dumps `openapi.json` — the frontend generates its TS types from it (`npm run gen:api`).
