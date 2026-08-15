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
3. **Verify the REAL artifact**, not just the build:
   ```
   docker inspect --format '{{.State.Health.Status}}' alphadeck-backend-1   # healthy
   curl http://localhost:8000/<endpoint>                                    # new field
   ```
   For a frontend change, open the live control in the Browser pane. Prod app :8080 / api :8000.

## Preview on DEV (unmerged branch)
Detach the main checkout to the sha, rebuild the dev service, restore main — full form in
`docs/DEPLOY.md` Flow B. Dev app :8081 / api :8001.

## Guardrails (never violate)
- **Prod DB is read-only** — deploy rebuilds IMAGES, never data. Never
  `docker compose down -v` against prod; never `DROP DATABASE alphadeck`.
- **`--no-deps`** — never rebuild postgres/cron as a side effect of shipping FE/BE.
- Prefer the targeted `--no-deps <service>` over a bare `docker compose up --build`.
