# Operations: stacks, deploys and recovery

How Alpha Deck runs: the Docker Compose stacks on one machine and what keeps them apart, what a deploy changes,
how settings reach a container, the connection limits, the clock windows and restart behavior. Prod runs as the
local Compose stack on the operator's machine; there is no server deployment. The operator runs prod; an agent
prepares a deploy, a dev stack or a fork on the operator's request. Read this doc before you deploy, touch a
compose file or a stack script, or add a setting. The procedures live in skills: `deploy`, `dev-stack`, `fork`,
`stack-recovery` and `backup-restore`.

## Code

| Piece | Module |
|---|---|
| The prod deploy wrapper (its header is its usage, guards and exit codes) | `scripts/deploy.sh` |
| Compose: the base file, the prod, dev, fork and sig overlays, the DB-only file | `docker-compose.yml`, `docker-compose.prod.yml`, `docker-compose.dev.yml`, `docker-compose.fork.yml`, `docker-compose.sig.yml`, `infra/docker-compose.yml` |
| Images and the boot command | `backend/Dockerfile`, `frontend/Dockerfile`, `frontend/nginx.conf` |
| Boot steps | `db/wait.py`, `db/migrate.py`, `pipeline/seed.py` |
| Stack scripts | `scripts/refresh-dev.sh`, `scripts/fork.sh`, `scripts/refresh-fork.sh`, `scripts/refresh-sig.sh`; `Makefile` wraps the dev and prod forms |
| Env templates | `.env.example`, `.env.fork.example`, `.env.sig.example` |
| Settings | `domain/settings.py` |
| The `/backtest` archive | `scripts/archive_backtest_prod.py` |
| The health probe | `app/health.py` |

## How it works

### The stacks

| Stack | Compose project and DB | Ports (app / api / postgres) | Cron | Env file | Runs from |
|---|---|---|---|---|---|
| prod | `alphadeck`: the base file plus `docker-compose.prod.yml` | 8080 / 8000 / 5544 | on | `.env`, auto-loaded | the main checkout |
| dev | `alphadeck_dev` | 8081 / 8001 / 5545 | off | `.env.dev` | the main checkout |
| fork `<name>` | `alphadeck_<name>` | slot N: 808N / 800N / 554(4+N), N from 3 to 9 | off | `.env.fork` | its own worktree |
| sig | `alphadeck_sig` | 8082 / 8002 / 5546 | off | `.env.sig` | its own worktree |
| tests | no stack: a per-worktree database named `alphadeck_test...` (`db/testdb.py`) | none | none | none | any checkout |

What keeps them apart:

- **The project name.** Compose namespaces containers, network and named volumes by project, so a dev, fork or
  sig Postgres volume cannot reach prod's `alphadeck_pgdata`. Each overlay also names its own database and
  replaces the base ports with `!override` (Compose would otherwise append them and bind prod's ports too).
- **A bare `docker compose up` is prod.** The base file is project `alphadeck` with `.env`. Never run it when you
  mean another stack; prod's own form adds `-f docker-compose.prod.yml`.
- **`infra/docker-compose.yml` is prod's database.** It uses project `alphadeck` and the `pgdata` volume, for a host
  uvicorn loop that deliberately reads prod data; it is never an isolated dev database.
- **Worktrees never run prod or dev.** Only the main checkout holds `.env` and `.env.dev`, and a stack started
  without them has no secrets (no SEC User-Agent, so every live pull is skipped). Forks and sig run from their own
  worktree with their own env file; the `deploy` skill's worktree preview builds one dev image with the main
  checkout's `.env.dev` passed explicitly.
- **Built versus mounted.** Prod's backend and cron are built images. Dev, fork and sig mount `./backend:/app` and
  serve with `--reload`, so their backend runs whatever that checkout has checked out; their frontend is built.
- **One scheduled fetcher.** Every overlay puts `cron` in the `donotstart` profile, so only prod runs the nightly.
  All stacks share one outbound address and SEC's per-host limit ([DATA_SOURCES](DATA_SOURCES.md), "EDGAR
  etiquette"), while each stack's SEC budget counts only its own requests.
- **The host default is prod.** A command run on the host with no `DATABASE_URL` connects to `db/session.py`'s
  default: prod's database on 5544. Run prod commands inside its containers (`docker exec alphadeck-backend-1 ...`).
- **Shared on purpose:** the host directory `./data/backups` (dumps, not the live database), which is how the
  other stacks read prod's dumps.

A new checkout: copy `.env.example` to `.env` and `.env.dev` at the main checkout root, bring dev up (the
`dev-stack` skill), then create a user in the dev backend ([AUTH](AUTH.md), "The CLI (`python -m pipeline.users`)").
The dev overlay sets `ALPHADECK_SEED_ON_BOOT`, so the demo cards sit in the founding workspace; attach the user with
`--founding` to see them.

The host loop is opt-in: set `DATABASE_URL` to `localhost:5545/alphadeck_dev`, run `scripts/ensure-venv.sh`, then from `backend/` `python -m db.migrate` and
`python -m uvicorn app.main:app --reload` (port 8000, shared with prod). `pipeline.ingest_thesis --thesis <id>`
and `pipeline.daily` pull live data and write: dev only.

### One-way refresh

`refresh-dev.sh`, `refresh-fork.sh` and `refresh-sig.sh` copy prod into another stack and never write prod. Each
restores a dump from the main checkout's `data/backups` (the newest, or with `--fresh` one it first takes in prod's
backend, a read-only `pg_dump`) into a recreated database on the target's own Postgres, then runs `db.migrate`. The
fork and sig scripts also copy prod's cache volume, mounted read-only, unless `--no-cache`; dev only with
`--with-cache`.

A restored database is all of prod's, including every user's Slack webhook (`user_meta.slack_webhook_url`). A hand
`pipeline.daily`, Run daily now or the editions job on that stack pushes to those webhooks ([FEED_LOOP](FEED_LOOP.md),
"Notifications"). Clear them before anything there can notify; the `fork` skill has the statement.

### Forks

A fork is a full stack for an experiment: its own worktree and branch, database, ports and cards, seeded from
prod. `scripts/fork.sh init <name> --slot N` runs from the main checkout and creates
`.claude/worktrees/fork-<name>` on branch `fork/<name>` (or `--branch`, `--from`), scaffolds `.env.fork`, starts
the stack and seeds it; `up`, `down`, `refresh` and `destroy` run from the fork's worktree. Steps: the `fork` skill.

- **Identity is `.env.fork`.** `FORK_NAME` sets the project, database and volumes; the slot sets three ports.
  `fork.sh` and `refresh-fork.sh` each refuse an empty name, `prod`, `dev`, `sig`, any `test*` name, anything
  outside `[a-z0-9_]`, and anything resolving to prod's project, before any Docker call.
- **Refresh is reset only.** `refresh --reset` drops the fork's database and restores prod's newest dump, wiping the
  fork's own cards, decisions and calls. The flag is required so the bare command can never silently change meaning.
- **No live SEC by default.** `.env.fork` scaffolds an empty `ALPHADECK_USER_AGENT`, so `/health` reads degraded and
  every live pull is skipped; the fork reads prod's copied cache. Setting one is a deliberate opt-in.
- **No call of record.** The cron is off, and the recording gate withholds a `--no-live` pass ([FEED_LOOP](FEED_LOOP.md),
  "Recording the call"). Calls compute on read; the Scoreboard shows prod's record frozen at the seed.
- **Only prod's names.** Prod fetches per card. To work on a new name, add it to a prod card (a quiet watchlist card
  serves), let the nightly fetch it, then refresh the fork.
- **`down` keeps the volumes; `destroy --yes` is `down -v`** and prints the worktree and branch removal commands.

The sig stack is an older form of the same idea for replay work: its own worktree and `.env.sig`, an image with the
replay extra, `./data/backtest` bound read-only, seeded by `refresh-sig.sh`, torn down with `down -v`. Replays run in
its backend container (`python -m replay.run`, [REPLAY](REPLAY.md); `python -m backtest.run`, [BACKTEST](BACKTEST.md)).

### What a deploy changes

- **Images are built.** Prod has no bind mount, so a change is dormant until its service is rebuilt: `frontend` for
  a frontend change, `backend` for any backend half (a route, a field, a migration).
- **The cron is its own image,** built from the same context with the replay extra and carrying
  `backend/scripts/daily_cron.sh`. A change on the nightly path, in the cron's jobs or in that script needs `cron`
  too; `deploy.sh` notes when the backend and cron shas differ.
- **The boot migrates.** The backend's command (`backend/Dockerfile`) runs `db.wait`, `db.migrate`, then
  `pipeline.seed`, then uvicorn. Every backend start applies pending migrations to prod, which writes schema and
  data, so a deploy is not read-only and the backup comes first. `pipeline.seed` writes only when
  `ALPHADECK_SEED_ON_BOOT` is true: compose defaults it to false and only the dev overlay sets it.
- **Provenance.** The `GIT_SHA` build arg becomes `ALPHADECK_IMAGE_SHA`, stamped as `calls.code_sha` on every row a
  container records. `deploy.sh` reads it from `git rev-parse HEAD` and ignores a value in the shell. A forgotten
  sha stamps NULL, which is honest; a wrong one is a false provenance claim. On a mounted backend it is the image's
  sha and can lag the code.
- **The frontend restart.** A recreated backend gets a new network address and nginx keeps the one it resolved at
  start, so `/api` answers 502 until the frontend restarts. `deploy.sh` restarts it after a backend-only rebuild.
- **Env changes need a recreate** (`up -d --no-deps --no-build <service>`, which `deploy.sh --env-only cron` runs),
  never `docker restart`. `PYTHONUNBUFFERED` is set in the image, so it needs a rebuild.

### The deploy script

`scripts/deploy.sh` is the only way code reaches prod: backup, build of the named services from the merged `main`,
the frontend restart rule, then verification. Run it from Git Bash in the main checkout; `bash` typed in PowerShell
is WSL.

- **It refuses, changing nothing,** on a linked worktree, no `.env`, a dirty tree, a branch other than `main`, `HEAD`
  not equal to `origin/main`, a New York clock inside 22:20 to 22:50 (it falls back to the `EST5EDT` rule when Git
  for Windows has no zoneinfo), a pass running (`cron.status` running, or an unfinished run inside its timeout) or
  the call-of-record lock held. It reads the lock from `pg_locks` and never takes it.
- **It does not refuse on** the per-company write lock (an interrupted ingest leg rolls back and the next run
  appends what is missing), the onboarding job (it resumes from its per-name checkpoint, and its lock dies with the
  container), or **the editions lock**: a `cron` deploy kills a running hand `editions.run cut`
  ([EDITIONS](EDITIONS.md), "Cutting by hand"). Do not deploy `cron` during a hand cut.
- **Status reads carry a steward's token,** `ALPHADECK_OPS_TOKEN` from `.env`, never printed. Each read tries
  `[::1]`, then `127.0.0.1`, never `localhost`: Docker Desktop's IPv4 loopback forward can reset while IPv6
  answers.
- **It verifies** health, the `/api` proxy, each rebuilt container's `ALPHADECK_IMAGE_SHA`, the cron's boot lines,
  and that `/admin/status` gained no `cron.alarms` line and no loud verdict.
- **`--hold-cron`** builds frontend and backend with the cron kept stopped, for a data change that must land before
  any nightly work runs on the new code. It refuses unless the cron is already stopped, and verification expects it
  still stopped. Before holding it, the old code must have recorded the last expected night; otherwise the next
  cron boot's catch-up records that night with the new code.
- **`--env-only cron`** recreates the cron without a build and backs up only with `--label`; **`--dry-run`** runs
  the local checks for real and touches no Docker and no endpoint.

### Migrations and restore

- `db/migrate.py` applies the files in `backend/db/migrations/` in name order and records each applied file name in
  `schema_migrations`. Never renumber or rename a migration (it would run again) and never edit an applied one. A new
  migration is numbered after the highest file (0213, so 0214 next); 0085 to 0199 are unused, and a file there
  would sort before the 02xx migrations on a fresh database. The table-side rule is
  [TENANT_SCOPES](TENANT_SCOPES.md), "Adding a table".
- **Restore older images before a pre-migration dump.** A dump taken before the running images' newest migrations
  needs the matching older images started before any newer backend boots against the restored database, because
  the boot migrates it. Steps: the `backup-restore` skill; its `DROP DATABASE alphadeck` is the only sanctioned one.
- Recover lost history by restoring a dump, never by re-ingesting: a re-ingest stamps today's `recorded_at`.

**The V2 rollback window.** While it lasts (until the operator removes the `v1-pre-v2` image tags), the full V2
cutover runbook, rollback included, is `git show 8094406a:docs/V2_CUTOVER.md`, and the pre-V2 backend, cron and
frontend images carry that tag. A rollback to them follows the rule above.

### The `/backtest` archive

Prod's `/backtest` page (steward only) serves a frozen copy of the promoted keeper passes in `./data/backtest_prod`,
bound read-only by `docker-compose.prod.yml` alone; dev and sig bind their own `./data/backtest`. The archive is
not in the database dumps: `scripts/archive_backtest_prod.py` rebuilds it from the dev store and fails on a
keeper-count or mirror-hash mismatch, so nothing partial is served. A prod `up` without the overlay omits the bind
and the page reads unavailable. Promoting a pass edits the keeper constants in that script. Steps: the `deploy`
skill.

### Connections

Every API request opens one database connection for its run; there is no pool. Postgres runs with its default
`max_connections` (compose sets no override). What the API may hold:

- **In-flight requests.** uvicorn runs `--workers 1 --limit-concurrency` from `ALPHADECK_UVICORN_LIMIT_CONCURRENCY`
  (the Dockerfile's build-arg default) and answers 503 at once past it. Hosted `/mcp` streams count. One worker is
  deliberate: the job registries live in process (`workbench.draft_jobs.assert_single_worker`). The dev, fork and
  sig overlays' `--reload` commands read the same variable (`tests/ops/test_boot_command.py`).
- **Background jobs.** At most `draft_jobs_max_running` chain drafts and `ingest_jobs_max_running` on-promote ingests
  run at once across every card, each on its own connection (0 is no cap). A draft over the cap answers 429 before
  the allowance is charged; a promote's new names go to the onboarding queue. A job counts until its thread ends
  (`tests/workbench/test_job_caps.py`).
- **The SEC budget** holds one connection per process (`ingest/sec_budget.py`).
- **The arithmetic.** The in-flight cap, both job caps, the budget connection, a few brief ones (boot, run-daily
  probe, backup) and the cron's passes must stay below `max_connections` less Postgres's reserved slots, with room
  for `psql` and migrations. Raise `max_connections` before any cap.
- **Request connections** (`db.session.connect_request`, used by `app.db_conn.get_conn` and the OAuth provider) set
  connect, statement, lock and idle-in-transaction timeouts as session options (the `db_*_timeout_s` settings; 0
  is off); a connect, statement or lock timeout answers 503. No request route holds a transaction open across a
  call to SEC, a vendor or a model (`tests/app/test_read_then_call.py`). **Job connections** (`connect_job`) take
  `job_statement_timeout_s` and `job_lock_timeout_s` and no idle timeout. The nightly and the CLIs use `connect`
  with `cron_statement_timeout_s`. Tests: `tests/db/test_connect_request.py`.

### Clock and windows

- **One timezone variable.** `ALPHADECK_MARKET_TZ` feeds both services' `TZ` and `Settings.market_tz`;
  `ALPHADECK_TZ` and `ALPHADECK_CRON_TZ` are read nowhere. A bad zone fails unevenly: `market_tz()` raises on the
  next status read or pass, while the sidecar's shell `date` falls back to UTC and fires at the wrong hour. Check
  `printenv TZ ALPHADECK_MARKET_TZ` in both containers after a change.
- **One schedule time.** `ALPHADECK_CRON_AT` feeds the sidecar's `RUN_AT` and `Settings.cron_run_at`, which Admin's
  covered-night rule and the catch-up cutoff read ([CRON_SIDECAR](CRON_SIDECAR.md)).
- **Windows.** `deploy.sh` refuses 22:20 to 22:50 New York time. The onboarding job, the weekly company-list and
  term-mentions jobs and the extraction backfill refuse (exit 75) inside the nightly window around `RUN_AT`,
  defined once by the onboarding job (`pipeline.onboarding.window_bounds`, the `onboard_window_*` settings). The
  repair CLIs refuse while a pass or another repair holds a lock ([FEED_LOOP](FEED_LOOP.md), "Concurrency and
  locks").

### Restart and recovery

- Every service is `restart: unless-stopped`: the stack comes back after a Docker or machine restart, and an explicit
  `stop` holds. After a reboot all containers start at once; the backend's `db.wait` polls Postgres for up to
  `ALPHADECK_DB_WAIT_S` before migrating, and the restart policy is the backstop. The cron waits for the database on
  its own (`DB_WAIT_S`).
- A machine that is off writes no run artifact and sends no page: the external heartbeat is the dead-man's switch
  ([CRON_SIDECAR](CRON_SIDECAR.md), "Failure handling").
- A stack that will not load (a 502, a blank app, no answer): the `stack-recovery` skill.
- `/health` is open. With no SEC User-Agent it still answers 200 but reports degraded (`app/health.py`,
  `tests/app/test_health_guard.py`).

### Safety rules

- Never `docker compose down -v` or `DROP DATABASE alphadeck` against prod (the restore is the one exception), and
  never a bare `docker compose up` when you mean another stack.
- A deploy, a restore and any `--apply` of a repair CLI on prod are the operator's, each after a backup (the
  `data-repair` skill).

## Contracts

- `deploy.sh`: exit 0 deployed and verified (or a clean dry run), 2 usage, 3 refused with nothing changed, 4 a step
  failed, 5 deployed but a check failed; greppable `ok:`, `REFUSE:`, `OK:` and `FAIL:` lines; a failed step or
  check names the `stack-recovery` skill (`tests/ops/test_deploy_script.py` asserts it).
- The image's command waits for the database before migrating, and every command that runs uvicorn (the image's
  and the overlays' `--reload` ones) carries the concurrency bound (`tests/ops/test_boot_command.py`).
- A compose fallback equals its Settings default, because compose always passes the value
  (`tests/domain/test_settings.py`).

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `scripts/deploy.sh`, compose commands on prod, a restore | operator | shell and Docker access on the operator's machine; the script's refusals |
| The `/admin/status` reads `deploy.sh` makes | a steward's token | `require_super_admin` on the admin router (`app/main.py`) |
| Dev, fork and sig lifecycle | operator, or an agent on the operator's request | the scripts' name guards |
| `GET /health` | anyone, no sign-in | open route (`app/main.py`) |
| `/docs`, `/redoc`, `/openapi.json` on any stack | anyone signed in | `require_user` (`app/main.py`) |
| `/backtest/*` | steward | `require_super_admin`; `tests/app/test_auth_gate.py` |

## Settings

**A setting reaches a container only if its service's `environment:` list in `docker-compose.yml` names it.**
Compose passes an explicit list and no `env_file`, and `Settings` reads no `.env` file, so a value in `.env` for an
unlisted name changes nothing in a container; it affects only a process run on the host. To make one tunable, add it
to compose with a fallback equal to the Settings default and extend `tests/domain/test_settings.py`.

Not passed by compose, so the code default holds on every Docker stack: `ALPHADECK_REQUIRE_UA`; `ALPHADECK_DB_WAIT_S`; `ALPHADECK_BACKUP_KEEP`, `ALPHADECK_BACKUP_LABELED_KEEP`,
`ALPHADECK_BACKUP_LABELED_MAX_AGE_DAYS`, `ALPHADECK_BACKUP_STALE_HOURS`; `ALPHADECK_TAPE_STALE_DAYS`,
`ALPHADECK_FUND_SHARES_STALE_DAYS`, `ALPHADECK_TAPE_CLOSED_BEFORE_DAYS`, `ALPHADECK_TAPE_CLOSED_AFTER_DAYS`,
`ALPHADECK_FREEZE_COMPLETED_FLOOR`, `ALPHADECK_INSIDER_AMENDMENT_REVIEW_HORIZON_DAYS`; the onboarding job's
`ALPHADECK_ONBOARD_*` dials (only `ALPHADECK_ONBOARD_EVERY_S` and `_TIMEOUT_S` reach the cron);
`ALPHADECK_CRON_STATEMENT_TIMEOUT_S`; `ALPHADECK_SCOREBOARD_DEFAULT_EDITION`; and from `.env.example` the
`ALPHADECK_LLM_*` model dials, the source base URLs, `ALPHADECK_EDGAR_RATE_PER_SEC`, the USASpending rate and the
HTTP timeouts, and `OPENFIGI_API_KEY`.

| Knob | Owner | Passed by compose? |
|---|---|---|
| `GIT_SHA` (becomes `ALPHADECK_IMAGE_SHA`) | `backend/Dockerfile`; set by `deploy.sh` | build arg, backend and cron |
| `ALPHADECK_SEED_ON_BOOT` | `pipeline/seed.py` | yes, backend (the dev overlay sets it true) |
| `ALPHADECK_MARKET_TZ`, `ALPHADECK_CRON_AT` | `domain/settings.py`, `backend/scripts/daily_cron.sh` | yes, both services |
| `ALPHADECK_UVICORN_LIMIT_CONCURRENCY` | `backend/Dockerfile` and the overlays' commands | yes, backend |
| `ALPHADECK_DRAFT_JOBS_MAX_RUNNING`, `ALPHADECK_INGEST_JOBS_MAX_RUNNING`, `ALPHADECK_DB_*_TIMEOUT_S`, `ALPHADECK_JOB_*_TIMEOUT_S` | `domain/settings.py` | yes, backend |
| `ALPHADECK_PUBLIC_ORIGIN`: the hosted `/mcp` endpoint is available when it is set; set it only behind TLS ([SECURITY](SECURITY.md), "TLS") | `domain/settings.py` | yes, backend |
| `ALPHADECK_OPS_TOKEN` | read from `.env` by `deploy.sh` | no container gets it |
| `FORK_NAME`, `FORK_SLOT`, the three port variables, `FORK_PIP_EXTRAS` | `.env.fork` | compose interpolation |

The cron's schedule settings are in [CRON_SIDECAR](CRON_SIDECAR.md); the sign-in settings in [AUTH](AUTH.md).

## Known limits

- `deploy.sh` does not check the editions lock, so a `cron` deploy can kill a hand cut.
- `refresh-fork.sh` still runs the platform-facts move after migrating (a no-op on a dump taken after the
  cutover) and keeps `--v1-state`.
- The sig stack duplicates what a fork does and has no branch of its own.
- Nothing terminates TLS, and the backend's port 8000 is published on the host, past nginx's headers
  ([SECURITY](SECURITY.md)).

## Related

[CRON_SIDECAR](CRON_SIDECAR.md), [FEED_LOOP](FEED_LOOP.md), [EDITIONS](EDITIONS.md), [ADMIN](ADMIN.md),
[SECURITY](SECURITY.md), [AUTH](AUTH.md), [TENANT_SCOPES](TENANT_SCOPES.md), [DATA_SOURCES](DATA_SOURCES.md),
[BACKTEST](BACKTEST.md), [REPLAY](REPLAY.md). Skills: `deploy`, `dev-stack`, `fork`, `stack-recovery`,
`backup-restore`, `data-repair`.
