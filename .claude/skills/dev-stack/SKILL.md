---
name: dev-stack
description: >-
  Use when the operator asks to set up, bring up, refresh or tear down the dev stack (project
  `alphadeck_dev`, ports 8081/8001/5545, cron off). Builds it from the main checkout and seeds it one way
  from prod's dumps.
---

# Dev stack

Dev is a second Compose project beside prod with its own database and volumes. Its backend mounts `./backend`
and reloads; its frontend is built.

## Guardrails

- Run from the main checkout root, never a worktree: only the main checkout holds `.env.dev`, and a stack without
  it has no SEC User-Agent. In a worktree `.git` is a file, not a directory.
- Dev is always `-f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev`. A bare
  `docker compose up` is prod.
- Prod is read only through its dumps (`refresh-dev.sh`). Never `down -v` or `DROP DATABASE alphadeck` on prod.
- A refreshed dev holds every prod user's Slack webhook. Before Run daily now or any `pipeline.daily` on dev, clear
  them (the statement is in the `fork` skill; use dev's database `alphadeck_dev`).

## Steps

With `D="docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev"`:

1. Docker Desktop is running and `.env.dev` exists. If not, the operator copies `.env.example` to `.env.dev` and
   fills in the keys and `ALPHADECK_USER_AGENT`.
2. `$D up -d --build`.
3. `bash scripts/refresh-dev.sh` restores prod's newest dump into dev and migrates it. Flags: `--fresh` takes a new
   dump first, `--with-cache` also copies prod's cache, `--dry-run` prints the plan. Under PowerShell without Git
   Bash, run its steps by hand with `$pg` the dev Postgres container (`$D ps -q postgres`), each through
   `docker exec $pg`:
   `psql -U alphadeck -d postgres -c "DROP DATABASE IF EXISTS alphadeck_dev WITH (FORCE)"`, then
   `CREATE DATABASE alphadeck_dev`, then `Get-Content <dump> | docker exec -i $pg psql -U alphadeck -d
   alphadeck_dev` (a pipe; `-f - < file` is bash only), then `python -m db.migrate` in the dev backend.
4. A first user on a new checkout: `$D exec backend python -m pipeline.users create --email <you> --display-name
   <name> --super-admin --founding`; the founding workspace holds the demo cards.
5. Report: app `http://localhost:8081`; API `http://localhost:8001`.

Teardown, only when asked: `$D down`. It keeps `alphadeck_dev_pgdata` and `alphadeck_dev_appdata`; `down -v`
deletes dev's data.

## Checks

- `curl http://localhost:8001/health` answers `{"status":"ok"}`, or `degraded` with no User-Agent.
- `/docs` on 8001 needs a sign-in: open it in a browser signed in to the dev app, or send a dev token.
- `docker ps --filter name=alphadeck_dev` lists backend, frontend and postgres, and no cron.

## Stop and ask when

- The cwd is a worktree, `.env.dev` is missing, or Docker Desktop is down.
- `refresh-dev.sh` finds no dump in `data/backups`, or fails mid-restore.
- Port 8081, 8001 or 5545 is taken.

## Model

[OPERATIONS], "The stacks" and "One-way refresh".

[OPERATIONS]: ../../../docs/OPERATIONS.md
