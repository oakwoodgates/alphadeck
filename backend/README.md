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
- `pipeline/` — `call_for_thesis` (re-derive → assemble → log); **`ingest_thesis`** (M2 per-thesis back-half ingest) + **`daily`** (the call-of-record cron); `seed` / `run` / `populate_master` / `users` (accounts + a workspace per user) / `ratify_*` CLIs.
- `replay/` — the DuckDB/Parquet point-in-time backtest harness.
- `app/` — FastAPI: `/theses` · `/theses/{id}/call?asof=` · `/workbench/*` (scored · securities · extract · facts · promote · draft-chain) + the `schemas_api` wire contract.

## Dev setup

`uv` is not installed on this machine, so we use a stdlib venv + pip (pyproject stays the source of truth).
The helper provisions it — per checkout (every git worktree gets its OWN `.venv`; never borrow one), idempotent
(a ready venv is a ~1 s no-op), installing exactly what CI installs:

```powershell
..\scripts\ensure-venv.ps1        # from backend\ — or from the repo root: .\scripts\ensure-venv.ps1 · bash scripts/ensure-venv.sh · make venv
```

By hand it is the same two steps: `python -m venv .venv` then `.venv\Scripts\python -m pip install -e ".[dev,replay]"` —
the `dev` extra carries pytest + pytest-xdist (`pytest -n 6`) + pytest-timeout (the hang guard) + the pinned ruff/black;
`replay` (duckdb + pyarrow) so the replay tests execute instead of being collect-skipped.

## Database

Use the **isolated dev stack** in [`DEV_PROD.md`](../docs/DEV_PROD.md). From the **main checkout root**
(not `backend/` or a worktree), with the documented `.env.dev` prerequisites in place:

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev up -d --build
```

This is project `alphadeck_dev`: app **8081**, API **8001**, Postgres **5545**, database `alphadeck_dev`,
volumes `alphadeck_dev_pgdata` and `alphadeck_dev_appdata`; cron is off. Backend startup applies migrations
and, with the dev overlay's `ALPHADECK_SEED_ON_BOOT=true`, writes demo cards into the founding workspace
and their DATA facts into the data tenant. Runtime files go to dev appdata; the backup directory is a shared
host bind, not part of DB isolation (see `DEV_PROD.md`). A migration is an idempotent write, not a read-only check.

**Host loop is explicit opt-in.** The unset `DATABASE_URL` default targets database `alphadeck` on
`localhost:5544`, which is **prod**, not isolated dev.
`infra/docker-compose.yml` shares prod's project and `pgdata` volume; use it only when intentionally working
against prod, never for throwaway experiments. For an isolated host loop, explicitly configure `DATABASE_URL`
with your configured credentials for `localhost:5545/alphadeck_dev`. From `backend/`, with that checkout's
venv active, migrations apply from `db/migrations/`:

```powershell
python -m db.migrate   # writes the explicitly selected database; dev Compose already runs this at startup
```

Under `pytest`, the URL is overridden to an auto-derived, per-worktree `alphadeck_test_<hash>`
(`db/testdb.py`). DB-backed tests **skip automatically** if Postgres isn't reachable; CI runs them against a Postgres service.

## Run (from the repo root)

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m pytest backend\tests      # tests (DB tests skip if no Postgres)
backend\.venv\Scripts\python -m ruff check backend         # lint
backend\.venv\Scripts\python -m black --check backend      # format check
```

## Seed + serve the API

The dev stack above already migrates, seeds and serves the API on **8001**. `pipeline.seed` is a no-op
unless `ALPHADECK_SEED_ON_BOOT=true`; the dev overlay enables it, prod does not by default. To re-run the
seed and print the demo thesis ids, from the **main checkout root** (the command runs in the dev backend's
`/app` working directory and writes the dev DB):

```powershell
docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev exec backend python -m pipeline.seed
```

For a host-served API instead, from `backend/` with the venv active and the explicit dev `DATABASE_URL`
above, `python -m uvicorn app.main:app --reload` serves on **8000**; that port must be free (prod also uses it).

**Authentication is required.** Follow [`AUTH.md`'s CLI/bootstrap procedure](../docs/AUTH.md#the-cli-python--m-pipelineusers)
against the **dev** backend, not its prod-container example. To read the seeded cards, the account must be
attached with `--founding`; a newly created private workspace otherwise has no demo cards. Use a session
or a personal bearer token for that account (Settings > Agents or the documented token CLI). With that token
already set as `ALPHADECK_MCP_TOKEN`, substitute the printed thesis id in these PowerShell examples:

```powershell
curl.exe -H "Authorization: Bearer $env:ALPHADECK_MCP_TOKEN" "http://127.0.0.1:8001/theses/<thesis-id>/call?asof=2026-06-01"
curl.exe -H "Authorization: Bearer $env:ALPHADECK_MCP_TOKEN" "http://127.0.0.1:8001/theses/<thesis-id>/call?asof=2026-05-28"
```

## Feed the platform (M2 — `docs/FEED_LOOP.md`)

These are **explicit writes/live pulls**, not setup checks. For the host commands below, run from the repo
root with `DATABASE_URL` explicitly targeting dev as above; an unset URL targets prod. Pull a thesis's back-half facts (insider Form 4 + EOD) so it can WARM/ARM, then run the daily
call-of-record cron. Both are **idempotent** (a re-run on unchanged data appends nothing):

```powershell
$env:PYTHONPATH = "backend"
backend\.venv\Scripts\python -m pipeline.ingest_thesis --thesis <thesis-id>   # per-thesis back-half ingest (incremental, fail-visible)
backend\.venv\Scripts\python -m pipeline.daily                                # per thesis: ingest -> assemble -> append the call-of-record
```

The **prod cron sidecar** runs it on the US-close schedule — **on by default in prod**, off in isolated dev.
The following commands target **prod**, not the dev quickstart; follow [`DEPLOY.md`](../docs/DEPLOY.md)'s
backup, nightly-window and lock safeguards before changing that stack:

```powershell
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build   # the cron sidecar starts with the stack
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --scale cron=0   # ...or skip it for one run
```

`app/openapi_export.py` dumps `openapi.json` — the frontend generates its TS types from it (`npm run gen:api`).
