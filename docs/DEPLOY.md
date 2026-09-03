# Deploy — take code live on the running stack

How to ship a code change to the running **prod** stack (and how to preview an unmerged
branch on **dev** first). Written to be followed cold by an agent.

Pairs with: `docker-compose.yml` (prod) · `docker-compose.dev.yml` (dev override) ·
`docs/DEV_PROD.md` (the two stacks + the one-way refresh). There is no deploy script —
deploy IS the targeted `--build` below.

---

## The load-bearing fact

Both the **frontend and backend are BUILT images** in prod (`build: ./frontend`,
`build: ./backend` — no bind-mount). A code change stays **DORMANT** on the running
stack until you **rebuild its image**:

- A **frontend** change needs a `frontend` rebuild.
- A change with a **backend** half (a new field, endpoint, migration) needs a `backend`
  rebuild too — a "frontend-only" PR needs only the FE rebuild.
- A change on the **nightly `pipeline.daily` path** needs a `cron` rebuild too — the cron
  is a SEPARATE image built from the same context (`build: ./backend`), so a `backend`
  rebuild alone leaves the 22:30 run on old code (Flow A's cron caveat has the details).
- Dev is the same for the frontend (built image). The dev **backend** is the only
  bind-mounted, live-reload service (`./backend:/app` + `--reload`); the dev frontend
  still needs a rebuild to preview.

**Build context = the MAIN checkout** (`C:\Users\funky\sites\oakwoodgates\alphadeck`),
never a worktree — Compose build contexts (`./frontend`, `./backend`) resolve relative
to the compose file there, and worktrees never run the stack.

---

## Flow A — merged branch -> PROD (take it live)

1. **Back up prod first** (one-way Slice-4 dump to `./data/backups`, safe):
   ```
   docker compose exec backend python -m pipeline.backup --label pre-deploy
   ```
2. **Fast-forward the main checkout** (it can be behind by more than your PR — a prior
   dev-detour leaves local `main` behind origin):
   ```
   git checkout main && git pull --ff-only origin main
   ```
3. **Rebuild only the changed service(s)** — `--no-deps` keeps postgres + cron untouched
   (but a daily-pipeline change must rebuild cron too — the **cron caveat** below); plain
   `docker compose` auto-loads `.env` and resolves to project `alphadeck`:
   ```
   docker compose up -d --build --no-deps frontend           # FE change
   docker compose up -d --build --no-deps frontend backend   # + backend half
   docker compose up -d --build --no-deps cron               # + a daily-cron-path change
   ```
   A backend rebuild re-runs the idempotent migrate + seed (~20-40s, brief API blip —
   safe, it is what every restart does). "Idempotent" now covers the FACT tables too: until
   PR-1c every boot re-appended the demo fixtures as a fresh bitemporal version (~1,900 rows a
   restart; the seed names reached ~140-155 stored versions per price bar and 175 per Form 4
   fact), so a rebuild is no longer a write.

   **Cron caveat — the daily-pipeline path.** The `cron` service shares the backend build
   context (`build: ./backend`) but is a SEPARATE image (`alphadeck-cron`) and container
   (`alphadeck-cron-1`): `--no-deps backend` rebuilds `alphadeck-backend` ONLY and leaves
   the running cron on OLD code. A change that alters what the nightly run executes — the
   `pipeline.daily` path (`backend/pipeline/daily.py` and anything it calls) — must ALSO
   rebuild `cron` (the third line above), or the 22:30 call-of-record stays stale. This
   nearly shipped a stale cron on the #272 cron-wire deploy (caught by inspecting the cron
   container's image + creation time). Verify the swap took:
   ```
   docker ps --filter name=alphadeck-cron-1     # fresh CreatedAt -> the new image is live
   docker logs alphadeck-cron-1                  # echoes "next run <ts>" — its live schedule
   ```
4. **Verify the REAL artifact** (not just that it built):
   ```
   docker inspect --format '{{.State.Health.Status}}' alphadeck-backend-1   # -> healthy
   curl http://localhost:8000/<endpoint>                                    # the new field
   ```
   For a frontend change, open the live control in the Browser pane and exercise it.
   Prod: app http://localhost:8080 · API + docs http://localhost:8000/docs.

---

## Flow B — unmerged branch -> DEV (preview before merge)

Preview a not-yet-merged branch on dev without merging. The main checkout can't check
out the branch itself (a worktree holds it), so DETACH to its sha, rebuild, restore:

```
git checkout <sha>
docker compose -f docker-compose.yml -f docker-compose.dev.yml -p alphadeck_dev --env-file .env.dev up -d --build --no-deps frontend
git checkout main
```

The built image PERSISTS after you restore `main`. Add `backend` to the rebuild for a
backend half. Dev: app http://localhost:8081 · API + docs http://localhost:8001/docs.

---

## Safety

- **Prod DB is read-only to deploy** — a deploy rebuilds IMAGES, never touches data.
  Never `docker compose down -v` against prod; never `DROP DATABASE alphadeck`.
- **`--no-deps`** — rebuild only the named service; leaves postgres + the cron sidecar
  running untouched.
- **The cron sidecar is separately imaged.** It builds from the same context as the
  backend (`build: ./backend`) but is its own image (`alphadeck-cron`) / container
  (`alphadeck-cron-1`), so a backend rebuild does NOT refresh it. A change on the nightly
  `pipeline.daily` path must ALSO `docker compose up -d --build --no-deps cron`, verified
  with `docker ps` (fresh CreatedAt) + `docker logs alphadeck-cron-1` (its next scheduled
  run) — else the 22:30 run silently stays on old code.
- Prefer the targeted `--no-deps <service>` over a bare `docker compose up --build`
  (which rebuilds + restarts the whole stack — still safe, just slower).
- The prod stack is `restart: unless-stopped` — it self-recovers after a daemon/laptop
  reboot; a deploy just swaps in the new image.
