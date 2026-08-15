---
name: dev-stack
description: >-
  Stand up or refresh the local DEV Alpha Deck stack beside prod — the isolated
  alphadeck_dev Compose project (ports 8081/8001/5545, cron OFF), seeded one-way
  from prod. Trigger when the operator says "set up dev", "spin up dev", "bring
  dev up", "refresh dev", or similar. Docker/compose ops; prod is never written.
---

# Set up / refresh the DEV stack

Authoritative runbook: `docs/DEV_PROD.md`. This skill is the executable checklist —
follow it top to bottom and STOP on any failed precondition (report it, don't guess).

## Guardrails (never violate)
- **Run from the MAIN checkout root**, NEVER a worktree. A stack run from a worktree
  produced a UA-less "TOTAL INGEST FAILURE". Check: the cwd's `.git` is a real
  directory (main checkout), not a `.git` file (worktree).
- **Prod DB is read-only.** The only prod→dev path is `pg_dump` READ (`refresh-dev.sh`).
  Never open a writable connection to prod; never `docker compose down -v` or
  `DROP DATABASE alphadeck` against prod.
- **Never a bare `docker compose up`** when you mean dev — that rebuilds PROD. Dev is
  ALWAYS the explicit `-p alphadeck_dev --env-file .env.dev` invocation in step 3.

## Steps
1. **Confirm main checkout.** If cwd is a worktree, stop and tell the operator to run
   from the main checkout (e.g. `C:\Users\funky\sites\oakwoodgates\alphadeck`).
2. **Preconditions.** Is Docker Desktop running? Is `.env.dev` present at the checkout
   root? If `.env.dev` is missing, stop and have the operator
   `cp .env.example .env.dev` and set `ANTHROPIC_API_KEY` + `ALPHADECK_USER_AGENT`
   (dev may reuse prod's values).
3. **Bring dev up:**
   ```
   docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev up -d --build
   ```
4. **Verify health:** `curl http://localhost:8001/health` → expect `{"status":"ok"}`
   (or `degraded:{"user_agent":"missing"}` if no UA — cosmetic in dev).
5. **Seed data from prod (one-way, read-only):**
   ```
   bash scripts/refresh-dev.sh
   ```
   Under pure PowerShell (no Git Bash) use the pipe form in `docs/DEV_PROD.md`
   (`Get-Content dump | docker exec -i … psql`), not `psql -f - < file`.
6. **Report:** app → http://localhost:8081 · API/docs → http://localhost:8001/docs.
   Remind the operator to bring dev down when idle (`… -p alphadeck_dev down`) — it's
   3 extra containers; the volumes persist so the next `up` is quick.

## Teardown (only when asked)
```
docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev down
```
Removes the dev containers/network but KEEPS `alphadeck_dev_pgdata` /
`alphadeck_dev_appdata`. Never `down -v` here (deletes dev data), and never against prod.
