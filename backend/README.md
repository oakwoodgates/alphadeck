# Alpha Deck — backend

Python project root. Packages land per the build plan:

- `domain/` — the spine: `Thesis`, `SignalEvent`, `CallCard`, `CallConfig` (Pydantic).
- `calls/` — the **call-assembler**: pure, deterministic, golden-tested `assemble_call(...)`.
- `db/` — bitemporal Postgres store: schema + migrations, `as_of(asof, known_at)` reads, append-only facts.
- `securities/` — canonical security master + entity resolution (OpenFIGI + SEC CIK, cache-first).
- (M2+) `ingest/`, `signals/`, `repositories/`, `pipeline/`, `app/`, `llm/`, `replay/`.

## Dev setup

`uv` is not installed on this machine, so we use a stdlib venv + pip (pyproject stays the source of truth).

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install "pydantic>=2.6" "psycopg[binary]>=3.1" pytest ruff black
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
