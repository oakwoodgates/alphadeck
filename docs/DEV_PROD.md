# Dev / Prod — two local stacks, one-way refresh

How to run a **dev** Alpha Deck stack beside the **prod** demo on the same machine, fully isolated, and
copy prod's data into dev cheaply and safely. Written to be followed cold by an agent.

Pairs with: `docker-compose.yml` (prod, unchanged) · `docker-compose.dev.yml` (the dev override) ·
`Makefile` (wrappers) · `scripts/refresh-dev.sh` (the one-way refresh) · `.env.example` (env template).

---

## The model — three isolated tiers

| Tier | Compose project | DB name | Postgres volume | Host ports (app/api/pg) | Cron | Env |
|------|-----------------|---------|-----------------|--------------------------|------|-----|
| **live / prod** | `alphadeck` | `alphadeck` | `alphadeck_pgdata` | 8080 / 8000 / **5544** | ON | `.env` |
| **dev** | `alphadeck_dev` | `alphadeck_dev` | `alphadeck_dev_pgdata` | 8081 / 8001 / **5545** | OFF | `.env.dev` |
| **fork `<name>`** | `alphadeck_<name>` | `alphadeck_<name>` | `alphadeck_<name>_pgdata` | slot ladder (808N/800N/554(4+N), N≥3) | OFF | `.env.fork` |
| **tests** | (pytest, no stack) | `alphadeck_test_<hash>` | (per-worktree, auto) | n/a | n/a | none |

Forks are self-serve experiment stacks seeded ONE-WAY from prod (`scripts/fork.sh`), run from their own
worktrees — full model: `docs/FORKS.md`. (The Signals Lab predates them and stays as-is on its branch.)

- **Isolation is structural, not a convention.** A distinct Compose **project name** (`-p alphadeck_dev`)
  auto-namespaces the containers, the network, **and the named volumes** — so the dev stack's Postgres
  data lives in `alphadeck_dev_pgdata` and *physically cannot reach* prod's `alphadeck_pgdata`. Distinct
  host ports let dev and prod run **at the same time**. (Tests are a third tier, isolated by
  `db/testdb.py`'s per-worktree `alphadeck_test_*` DB + hard guard — see CLAUDE.md.)
- **The refresh is ONE-WAY: prod → dev, read-only.** `pg_dump` READS prod; the dump is restored into the
  **dev** DB. The refresh never opens a writable connection to prod. This is the anti-truncation guarantee
  (the 2026-07-21 lesson: a stray write/truncate against the live DB).

---

## Prerequisites (once)

1. **Run from the MAIN checkout root** — e.g. `C:\Users\funky\sites\oakwoodgates\alphadeck`.
   **Worktrees never run the stack.** Worktrees are for code + tests (tests use the auto per-worktree DB
   and need no `.env`). Running a stack from a worktree is what produced the UA-less-container "TOTAL
   INGEST FAILURE"; keeping the stack in the main checkout closes that gap by construction.
2. **Two env files at that root** (both gitignored; copy `.env.example`):
   - `.env` — prod. Compose auto-loads it for the plain `docker compose up`.
   - `.env.dev` — dev. The dev wrapper passes `--env-file .env.dev` (which **replaces** the auto-loaded
     `.env` for `${VAR}` interpolation). Set the same keys (`ANTHROPIC_API_KEY`, `ALPHADECK_USER_AGENT`,
     …); it's fine for dev to reuse prod's values.

---

## Commands

`make` is frequently absent on Windows — each target is just a wrapper, so the raw `docker compose` form
is authoritative. Bash (Git Bash) and PowerShell forms are identical except where noted.

### Prod up (unchanged)
```
docker compose up -d --build          # or: make prod-up
```
Project `alphadeck`, ports 8080/8000/5544, cron ON, auto-loads `.env`.

### Dev up (beside prod)
```
docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev up -d --build
# or: make dev-up
```
Project `alphadeck_dev`, ports 8081/8001/5545, cron OFF, `alphadeck_dev` DB, source-mounted `--reload`
backend. App at http://localhost:8081, API/docs at http://localhost:8001/docs.

### Dev down (keeps dev data)
```
docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev down    # or: make dev-down
```
Stops + removes the dev containers/network but **keeps** `alphadeck_dev_pgdata` / `alphadeck_dev_appdata`.
(Never `down -v` — that deletes the dev volumes. And never `down -v` against prod.)

### Refresh dev from prod (one-way)
```
bash scripts/refresh-dev.sh            # or: make refresh-dev
bash scripts/refresh-dev.sh --fresh    # take a brand-new read-only prod dump first, then load it
bash scripts/refresh-dev.sh --dry-run  # print the plan, touch nothing (safe anywhere)
```
Flags: `--from-latest` (default, newest `./data/backups/*.sql`) · `--fresh` (dump prod first) ·
`--with-cache` (also copy prod's `appdata` cache) · `--dry-run` · `--help`. Requires the **dev** stack up
(and the **prod** stack up for `--fresh`). Ends with `OK: dev refreshed from <dump> (<N> theses, <M> calls)`.

---

## Windows gotcha — the restore redirection differs

`scripts/refresh-dev.sh` runs under **Git Bash** (stdin redirection `psql -f - < dump.sql`). Under **pure
PowerShell** (no Git Bash) the redirection is a pipe instead. The manual PowerShell refresh:

```powershell
# From the main checkout root, with the dev stack up.
$dev   = "-f","docker-compose.yml","-f","docker-compose.dev.yml","-p","alphadeck_dev"
$devpg = docker compose @dev ps -q postgres
$dump  = (Get-ChildItem .\data\backups\*.sql | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName

# (optional --fresh: a read-only prod dump first)
# docker exec $(docker compose -p alphadeck ps -q backend) python -m pipeline.backup --label pre-refresh

docker exec $devpg psql -U alphadeck -d postgres -c "DROP DATABASE IF EXISTS alphadeck_dev WITH (FORCE)"
docker exec $devpg psql -U alphadeck -d postgres -c "CREATE DATABASE alphadeck_dev"
Get-Content $dump | docker exec -i $devpg psql -U alphadeck -d alphadeck_dev     # <-- pipe, not `< file`
docker exec $(docker compose @dev ps -q backend) python -m db.migrate
```

Key difference: **bash** `docker exec -i <dev-pg> psql -U alphadeck -d alphadeck_dev -f - < file.sql`
vs **PowerShell** `Get-Content file.sql | docker exec -i <dev-pg> psql -U alphadeck -d alphadeck_dev`.

---

## Other gotchas

- **Two full stacks = double the container load.** Prod + dev is 7 containers (prod also runs cron). Bring
  dev **down when idle** (`make dev-down`) — the dev volumes persist, so the next `dev-up` + `refresh-dev`
  is quick.
- **Cron is prod-only.** The dev override assigns `cron` to a profile the dev `up` never enables, so dev
  never fires the nightly live EDGAR/Yahoo pulls or writes snapshots. `docker compose … config` on the dev
  stack omits the service entirely. Prod's cron (base file, no override) stays ON by default.
- **`infra/docker-compose.yml` shares prod's DB — a footgun.** It uses `name: alphadeck` + the `pgdata`
  volume, i.e. the SAME project + volume as the root stack, so `infra up` touches the **prod** data. That's
  intentional for a uvicorn-on-host dev loop against real data, but it is **not** an isolated dev DB. For
  isolation use `docker-compose.dev.yml` (this doc). Don't point tests or throwaway experiments at `infra`.
- **Shared host-bind dirs (by design, not a leak).** Both stacks bind the same host `./data/backups` and
  `./data/scoreboard_replay` (regenerable artifacts + DB dumps, **not** the live DB). That's *why*
  `refresh-dev` can read prod's dumps from dev. The live DB (the `pgdata` volume) is what's isolated.
- **Source mount + `--reload` (dev only).** The dev backend mounts `./backend:/app`; edit-and-reload works
  because the editable install resolves packages from `/app`. If Windows file-watching misbehaves, comment
  out the `- ./backend:/app` line in `docker-compose.dev.yml` to run the pristine built image.

---

## Boot-visibility guard — a missing User-Agent is now LOUD

SEC pulls need `ALPHADECK_USER_AGENT`. Config is deliberately **fail-open** (a missing UA skips the live
pull; the rest of the app works), so a blank UA used to surface only deep at the first EDGAR call. Now:

- On boot, a missing/blank `ALPHADECK_USER_AGENT` logs an **ERROR**, and **`GET /health`** reports
  `{"status": "degraded", "degraded": {"user_agent": "missing"}}` (still HTTP 200 — the healthcheck passes;
  the flag is for an operator/agent to SEE). With the UA set, `/health` is `{"status": "ok", "degraded": {}}`.
- Opt into **fail-closed**: set `ALPHADECK_REQUIRE_UA=true` and the backend **refuses to boot** without a
  UA (uvicorn exits non-zero). Never set it to an empty string — use `true`/`false`, or omit it.

Check it: `curl http://localhost:8001/health` (dev) or `:8000` (prod). Logic lives in `backend/app/health.py`
(pure, unit-tested in `backend/tests/app/test_health_guard.py` — runs without a DB).

---

## Safety recap

- **One-way prod → dev** (`pg_dump`, read-only) — prod is never written by dev work.
- **Never** `docker compose down -v` or `DROP DATABASE alphadeck` against prod; never a bare `docker
  compose up` while intending dev (that rebuilds **prod**). Dev is always the explicit `-f … -f … -p
  alphadeck_dev` invocation.
- Three tiers — `alphadeck_test_*` (tests) / `alphadeck_dev` (dev) / `alphadeck` (live) — cannot reach one
  another. Nothing here touches the call logic, data model, migrations, or seed.
