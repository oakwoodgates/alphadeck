---
name: deploy
description: >-
  Ship a merged code change to the running PROD stack (or preview an unmerged branch
  on DEV) by rebuilding the built Docker images — the frontend/backend are baked
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
  dormant on prod until the BACKEND image rebuilds.
- **Build context = the MAIN checkout**, never a worktree.
- **Back up prod first:**
  ```
  docker compose exec backend python -m pipeline.backup --label pre-deploy
  ```

## Ship to PROD (merged -> live)
1. Fast-forward the main checkout (it can be behind by more than your PR):
   ```
   git checkout main && git pull --ff-only origin main
   ```
2. Rebuild only the changed service(s) — `--no-deps` spares postgres + cron:
   ```
   docker compose up -d --build --no-deps frontend            # FE change
   docker compose up -d --build --no-deps frontend backend    # + backend half
   ```
   (A backend rebuild re-runs the idempotent migrate + seed — ~20-40s, brief API blip.)
   **After a backend rebuild, restart the frontend too** — a recreated backend gets a NEW
   Docker-network IP, and the frontend nginx cached the old one, so every `/api` call 502s
   until nginx re-resolves. (A rebuild that already includes `frontend` recreates it, so no
   extra restart is needed; a backend-ONLY rebuild needs this.)
   ```
   docker restart alphadeck-frontend-1
   ```
3. **Verify the REAL artifact**, not just the build:
   ```
   docker inspect --format '{{.State.Health.Status}}' alphadeck-backend-1   # healthy
   curl http://localhost:8000/<endpoint>                                    # new field
   curl -o /dev/null -w '%{http_code}' http://localhost:8080/api/health     # the /api PROXY — MUST be 200
   ```
   **Verify the `/api` PROXY (`:8080/api/health`), not just the backend directly (`:8000`)** — a
   backend rebuild can leave the frontend's nginx pointing at the old backend IP (502) even while
   `:8000/health` is 200. For a frontend change, open the live control in the Browser pane. Prod app
   :8080 / api :8000. (If the proxy 502s / the SPA 000s, see the `stack-recovery` skill.)

## Preview on DEV (unmerged branch)
Detach the main checkout to the sha, rebuild the dev service, restore main — full form in
`docs/DEPLOY.md` Flow B. Dev app :8081 / api :8001.

## Guardrails (never violate)
- **Prod DB is read-only** — deploy rebuilds IMAGES, never data. Never
  `docker compose down -v` against prod; never `DROP DATABASE alphadeck`.
- **`--no-deps`** — never rebuild postgres/cron as a side effect of shipping FE/BE.
- Prefer the targeted `--no-deps <service>` over a bare `docker compose up --build`.
