---
name: deploy
description: >-
  Use when taking merged code live on prod ("deploy", "ship it", "rebuild prod") or previewing a branch or
  worktree change on dev. Runs `scripts/deploy.sh`, its by-hand fallback, the dev previews and the
  `/backtest` archive refresh; prod needs the operator's go.
---

# Deploy

`scripts/deploy.sh` takes merged code live on prod; its header is the usage, refusals and exit codes.

## Guardrails

- Prod is the operator's. State the sha and the services and get the go: `frontend` for a frontend change,
  `backend` for any backend half (a route, a field, a migration), and `cron` too for a change on the nightly path,
  in a cron job or in `backend/scripts/daily_cron.sh`.
- Run from Git Bash in the main checkout (`bash` typed in PowerShell is WSL). The build context is the main
  checkout, never a worktree.
- The backend's boot migrates prod, so the backup comes first; the script takes it. Never `docker compose down -v`
  and never `DROP DATABASE alphadeck` on prod.
- Never deploy `cron` while a hand editions cut runs (`editions.run cut`): the script does not check the editions
  lock, and recreating the cron kills the cut ([EDITIONS], "Cutting by hand").
- Report a refusal (exit 3) as it stands; never work around it.
- `GIT_SHA` is only the sha just read from the checkout being built: a wrong one is a false provenance claim.

## Steps

1. `git checkout main && git pull --ff-only origin main`.
2. Show the plan: `bash scripts/deploy.sh --label pre-deploy-<topic> --dry-run <services>`; it touches no Docker
   and no endpoint.
3. Ship: the same without `--dry-run`. A cron env-only change: `bash scripts/deploy.sh --env-only cron`. A data
   change that must land before the nightly runs new code: stop the cron, then `--hold-cron frontend backend`,
   once the old code has recorded the last expected night.
4. A frontend change: open the changed control on `http://localhost:8080`.

**By hand,** only when the wrapper cannot run. Apply its guards yourself: not 22:20 to 22:50 New York time, not
while `/admin/status` reads `cron.status: running`. With `P="docker compose -f docker-compose.yml -f
docker-compose.prod.yml"`:

1. `$P exec -T backend python -m pipeline.backup --label pre-deploy-<topic>`.
2. `GIT_SHA=$(git rev-parse HEAD) $P up -d --build --no-deps <services>` (`--no-build cron` for an env-only change;
   `docker restart` does not apply env).
3. After a backend rebuild without `frontend`: `docker restart alphadeck-frontend-1` (nginx keeps the old backend
   address).

**Preview on dev.** Dev is app 8081, API 8001; a curl there needs a token minted in the dev backend.

- A sha: `git checkout <sha>`, then `docker compose -f docker-compose.yml -f docker-compose.dev.yml -p
  alphadeck_dev --env-file .env.dev up -d --build --no-deps frontend`, then `git checkout main`. The dev backend
  is mounted, so it serves the sha's code only while the checkout stays detached.
- Uncommitted worktree work: `docker compose -f <wt>/docker-compose.yml -f <wt>/docker-compose.dev.yml
  --project-directory <wt> --env-file <main>/.env.dev -p alphadeck_dev up -d --build --no-deps frontend backend`.
  The main checkout's env file means no stack runs from the worktree. Rebuild dev from the main checkout
  afterwards, or dev serves code that is in no commit.

**The `/backtest` archive.** From the main checkout with prod up:

1. `PYTHONPATH=backend backend/.venv/Scripts/python scripts/archive_backtest_prod.py` (read-only on the source;
   fails on a keeper-count or mirror-hash mismatch).
2. `$P up -d --no-deps backend` picks up the bind; rebuild `frontend` only if the page code changed.
3. To promote a pass, add it to the keeper constants in the script, update its run-count guard, and repeat.

## Checks

The script exits 0 only when all of these pass; by hand, run each.

- `docker inspect --format '{{.State.Health.Status}}' alphadeck-backend-1` prints `healthy`.
- `curl -o /dev/null -w '%{http_code}' http://localhost:8080/api/health` prints 200: the proxy, not only `:8000`.
- `docker exec alphadeck-backend-1 printenv ALPHADECK_IMAGE_SHA` (and `alphadeck-cron-1`) prints the built sha.
- `docker logs --tail 20 alphadeck-cron-1` shows the boot lines.
- `curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/admin/status`: no new `cron.alarms` line and no
  louder `cron.status`. `$TOKEN` is `ALPHADECK_OPS_TOKEN`, read without printing it:
  `TOKEN=$(sed -n 's/^[[:space:]]*ALPHADECK_OPS_TOKEN=//p' .env | tail -n 1 | tr -d "\r\"'")`. None yet: the
  operator mints one with `pipeline.users token create` in the backend and adds it.
- After a cron env change, `printenv TZ ALPHADECK_MARKET_TZ ALPHADECK_CRON_AT` prints the same values in
  `alphadeck-cron-1` and `alphadeck-backend-1`.

## Stop and ask when

- The script refuses, or exits 4 or 5: report it, then the `stack-recovery` skill.
- A hand editions cut, a repair CLI or a restore is running.
- Undoing the deploy needs a dump taken before its migrations: start the older images first (the
  `backup-restore` skill).

## Model

The model is [OPERATIONS]: the stacks, what a deploy changes, the refusals and settings.

[OPERATIONS]: ../../../docs/OPERATIONS.md
[EDITIONS]: ../../../docs/EDITIONS.md
