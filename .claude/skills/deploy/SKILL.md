---
name: deploy
description: >-
  Ship a merged code change to the running PROD stack (or preview an unmerged branch,
  or uncommitted worktree work, on DEV) by rebuilding the built Docker images — the frontend/backend are baked
  images, so a change is dormant until rebuilt. Trigger on "deploy", "ship it", "take
  it live", "push to prod", "rebuild prod", "preview on dev". Touches PROD — back up
  first; the prod DB stays read-only.
---

# Deploy — take code live on the running stack

Authoritative runbook: `docs/DEPLOY.md`. This touches **PROD** — move carefully: back
up first, rebuild only the changed service(s), verify the real artifact. STOP and report
on any failed precondition; do NOT guess.

## Before touching prod
- **State the plan and get the operator's go** — which branch/sha, and which service(s):
  frontend only, or a backend half too? A backend half (new field/endpoint/migration) is
  dormant on prod until the BACKEND image rebuilds. A change on the nightly `pipeline.daily`
  path (or `backend/scripts/daily_cron.sh`) needs `cron` too.
- **Build context = the MAIN checkout**, never a worktree.

## Ship to PROD with the wrapper (use this)
`scripts/deploy.sh` runs the whole flow with its guards built in. Run it from the MAIN checkout:
```
git checkout main && git pull --ff-only origin main
bash scripts/deploy.sh --label pre-deploy-pr123 --dry-run backend cron   # show the plan; changes nothing
bash scripts/deploy.sh --label pre-deploy-pr123 backend cron             # backup -> build -> restart rule -> verify
bash scripts/deploy.sh --env-only cron                                   # cron ENV change: recreate, no build, no backup
```
- **Refuses (exit 3, nothing changed)** on a worktree or a missing `.env`, a dirty tree, a branch
  other than `main`, or `HEAD != origin/main` after a fetch. It also refuses inside **22:20–22:50
  America/New_York** (the 22:30 pass; New York time whatever the host TZ), and while a pass is in flight
  (`/admin/status` `cron.status == running`, or an `unfinished_run` inside its timeout) or the
  call-of-record lock is held. A bad invocation (unknown service, no `--label` on a build) exits 2.
  **Report a refusal to the operator; never work around it.**
- **Then:** a labeled backup (`--label`, required for a build), then
  `GIT_SHA=$(git rev-parse HEAD) … up -d --build --no-deps <services>`. If the backend was rebuilt
  without the frontend, it runs `docker restart alphadeck-frontend-1`.
- **Exit 0 only if every check passes:** the backend is `healthy`; `:8080/api/health` returns 200;
  `ALPHADECK_IMAGE_SHA` in each rebuilt backend/cron container equals the built sha; the cron is
  running and printed its boot lines; `/admin/status` shows no new `cron.alarms` line and no quiet → loud
  `cron.status`. A verification failure exits 5 and a failed step exits 4; both point to the
  `stack-recovery` skill.
- A frontend change still needs a live look: open the control in the Browser pane.

## Ship to PROD by hand (the fallback)
For when the wrapper cannot run. Apply its guards yourself: never between 22:20 and 22:50
America/New_York, and never while `/admin/status` reads `cron.status: running`.
1. **Back up prod first:**
   ```
   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m pipeline.backup --label pre-deploy
   ```
2. Fast-forward the main checkout (it can be behind by more than your PR):
   ```
   git checkout main && git pull --ff-only origin main
   ```
3. Rebuild only the changed service(s) — `--no-deps` spares postgres + cron. Prefix any
   backend/cron rebuild with `GIT_SHA=$(git rev-parse HEAD)`:
   ```
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps frontend   # FE change
   GIT_SHA=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps frontend backend
   GIT_SHA=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps cron   # cron-path change
   ```
   `GIT_SHA` is stamped into the image and recorded as `calls.code_sha` on every call-of-record row
   (F1 / migration 0044), so the record names the code that produced a call. OPTIONAL: unset means
   UNKNOWN and the column gets `NULL` — never a wrong value — so a forgotten prefix costs legibility
   and nothing else. **Never pass a sha you did not just read from the checkout you are building.**
   (A backend rebuild re-runs the idempotent migrate + seed — ~20-40s, brief API blip.)
   **After a backend rebuild, restart the frontend too** — a recreated backend gets a NEW
   Docker-network IP, and the frontend nginx cached the old one, so every `/api` call 502s
   until nginx re-resolves. (A rebuild that already includes `frontend` recreates it, so no
   extra restart is needed; a backend-ONLY rebuild needs this.)
   ```
   docker restart alphadeck-frontend-1
   ```
   A cron ENV-only change keeps the image: `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps --no-build cron`.
4. **Verify the REAL artifact**, not just the build:
   ```
   docker inspect --format '{{.State.Health.Status}}' alphadeck-backend-1   # healthy
   curl http://localhost:8000/<endpoint>                                    # new field
   curl -o /dev/null -w '%{http_code}' http://localhost:8080/api/health     # the /api PROXY — MUST be 200
   docker exec alphadeck-backend-1 printenv ALPHADECK_IMAGE_SHA             # the sha you built (and alphadeck-cron-1)
   docker logs --tail 20 alphadeck-cron-1                                   # the cron's boot lines
   curl http://localhost:8000/admin/status                                  # cron.status + cron.alarms: nothing new
   ```
   **Verify the `/api` PROXY (`:8080/api/health`), not just the backend directly (`:8000`)** — a
   backend rebuild can leave the frontend's nginx pointing at the old backend IP (502) even while
   `:8000/health` is 200. For a frontend change, open the live control in the Browser pane. Prod app
   :8080 / api :8000. (If the proxy 502s / the SPA 000s, see the `stack-recovery` skill.)

## Preview on DEV (unmerged branch)
Detach the main checkout to the sha, rebuild the dev service, restore main — full form in
`docs/DEPLOY.md` Flow B. Dev app :8081 / api :8001.

## Preview on DEV (UNCOMMITTED work in a worktree)
No sha yet — the loop is build -> look -> then decide whether to commit. Build dev from the
worktree with `--project-directory <worktree>` + `--env-file <MAIN>/.env.dev` (absolute), which
is what keeps it inside the worktree rule rather than around it — full form in `docs/DEPLOY.md`
Flow C. **Rebuild dev from the main checkout afterwards**, or dev serves code that is in no
commit.

## Guardrails (never violate)
- **Prod DB is read-only** — deploy rebuilds IMAGES, never data. Never
  `docker compose down -v` against prod; never `DROP DATABASE alphadeck`.
- **`--no-deps`** — never rebuild postgres/cron as a side effect of shipping FE/BE.
- Prefer the targeted `--no-deps <service>` over a full `docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build`.
