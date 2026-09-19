# Deploy — take code live on the running stack

How to ship a code change to the running **prod** stack (Flow A), and how to preview it on
**dev** first — from a branch's sha (Flow B) or straight from an uncommitted worktree
(Flow C). Written to be followed cold by an agent.

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
- A change on the **nightly `pipeline.daily` path** — or to the sidecar script itself,
  `backend/scripts/daily_cron.sh`, which ships INSIDE that image — needs a `cron` rebuild too:
  the cron is a SEPARATE image built from the same context (`build: ./backend`), so a `backend`
  rebuild alone leaves the 22:30 run on old code (Flow A's cron caveat has the details).
- Dev is the same for the frontend (built image). The dev **backend** is the only
  bind-mounted, live-reload service (`./backend:/app` + `--reload`); the dev frontend
  still needs a rebuild to preview.

**Build context = the MAIN checkout** (`C:\Users\funky\sites\oakwoodgates\alphadeck`),
never a worktree — Compose build contexts (`./frontend`, `./backend`) resolve relative
to the compose file there, and worktrees never run the stack. **Flow C is the one
exception**, and only because it hands Compose the main checkout's env file explicitly —
read it before reaching for it.

---

## Flow A — merged branch -> PROD (take it live)

1. **Back up prod first** (one-way Slice-4 dump to `./data/backups`, safe):
   ```
   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend python -m pipeline.backup --label pre-deploy
   ```
2. **Fast-forward the main checkout** (it can be behind by more than your PR — a prior
   dev-detour leaves local `main` behind origin):
   ```
   git checkout main && git pull --ff-only origin main
   ```
3. **Rebuild only the changed service(s)** — `--no-deps` keeps postgres + cron untouched
   (but a daily-pipeline change must rebuild cron too — the **cron caveat** below); the
   explicit prod form `docker compose -f docker-compose.yml -f docker-compose.prod.yml`
   auto-loads `.env`, resolves to project `alphadeck`, and adds the read-only `/backtest`
   archive bind.
   **Prefix every backend/cron rebuild with `GIT_SHA=$(git rev-parse HEAD)`** — see the
   `GIT_SHA` note below for what it is and what happens if you forget:
   ```
   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps frontend   # FE change (no GIT_SHA needed)
   GIT_SHA=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps frontend backend
   GIT_SHA=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps cron
   GIT_SHA=$(git rev-parse HEAD) docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps backend cron   # both halves
   ```

   **`GIT_SHA` — stamp the code onto the record (F1, migration `0044`).** The backend and cron
   services take a `GIT_SHA` build arg, which the Dockerfile turns into `ENV ALPHADECK_IMAGE_SHA`
   and `pipeline.daily` stamps as `calls.code_sha` on every row it records — so the record can say
   which code produced a call, not just which facts. It is **optional by design**: unset simply
   means UNKNOWN and the column gets `NULL`, never a wrong or empty value, so a forgotten prefix
   costs legibility on that night's rows and nothing else. Do not invent one after the fact. Run it
   from the checkout you are deploying (step 2 has already put you on the merged `main`). It matters
   most on **cron**, whose image writes the rows that ARE the record.
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
   docker logs alphadeck-cron-1                  # the boot catch-up line, then "next run <ts> — asof <date>"
   docker top alphadeck-cron-1                   # a `sleep 60` (the sliced wait) — never a tens-of-thousands-second sleep
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

The built image PERSISTS after you restore `main` — for the FRONTEND. **The dev backend does not
preview this way:** the dev override bind-mounts `./backend:/app` from the compose project
directory, so the running container serves whatever the MAIN checkout has checked out — the sha's
code only while you are detached, `main`'s again the moment you restore it, image or no image. A
backend half (a CLI, a migration, a route) previews on dev through Flow C (whose
`--project-directory` re-points that bind mount at the worktree), or by leaving the checkout
detached while you look. Dev: app http://localhost:8081 · API + docs http://localhost:8001/docs.

---

## Flow C — UNCOMMITTED worktree -> DEV (preview while iterating)

Flow B needs a sha. When the loop is **build -> look -> then decide whether to commit**,
there isn't one yet, and committing just to see something is the thing to avoid. Build
dev straight from the worktree instead:

```
docker compose -f <worktree>/docker-compose.yml -f <worktree>/docker-compose.dev.yml \
  --project-directory <worktree> \
  --env-file <MAIN-checkout>/.env.dev \
  -p alphadeck_dev up -d --build --no-deps frontend
```

**Why this is not the thing the worktree rule forbids.** It does not RUN a stack from a
worktree; it builds ONE service's image, using the worktree as the build context, into
the already-running `alphadeck_dev` project. `--project-directory` is what makes the
compose file's relative contexts (`./frontend`) resolve inside the worktree. The rule
exists to stop a stack whose env/config comes from a worktree — the worktree-`.env` gap,
since `.env` / `.env.dev` live only at the main-checkout root — and the absolute
`--env-file` closes exactly that gap. Nothing is written to the main checkout: no detach,
no WIP commit; it stays clean and on `main` throughout.

For a backend half, add `backend` to the service list: with `--project-directory <worktree>`
the dev override's `./backend:/app` bind mount resolves to the WORKTREE's backend, so the
re-created container live-reloads the worktree's code (this is how a new `pipeline.*` CLI is
exercised on dev before it is merged — Flow B cannot do it, above). Use Flow B once a sha
exists (it leaves a reproducible provenance trail); use Flow C while iterating. **Rebuild dev
from the main checkout when you are done** — otherwise dev keeps serving an image (and, for the
backend, a bind mount) built from code that exists nowhere in git.

---

## The `/backtest` archive (prod only)

Prod's `/backtest` page serves a FROZEN, read-only archive — the promoted keeper passes
copied into `data/backtest_prod` and bound read-only by `docker-compose.prod.yml`
(`./data/backtest_prod -> /data/backtest:ro`). It is decoupled from dev backtest runs (dev
writes `./data/backtest`) and is NOT in the DB backups, so it is rebuilt from the source,
never restored. `PROD` below = `docker compose -f docker-compose.yml -f docker-compose.prod.yml`
(run from the main-checkout root).

- **A bare `docker compose up` (no prod overlay) omits the bind** — `/backtest` then reports
  `available:false` (an empty page; nothing else is affected). Re-run with the prod form
  (`PROD up …`, or `make prod-up`) to restore it. No data is lost — only the bind is missing.

**Build / refresh the archive** (main checkout, backend venv, prod up):
1. Curate + verify:
   ```
   PYTHONPATH=backend backend/.venv/Scripts/python scripts/archive_backtest_prod.py
   ```
   copies the three keeper passes (324 runs today) + the one shared mirror + the keeper
   sweeps into `data/backtest_prod`, asserts the copied mirror's content hash with the
   store's own `mirror_hash`, and writes a filtered `index.json`. Read-only on the source;
   a keeper-count mismatch or a bad hash fails loudly, so nothing partial is served.
2. Recreate the backend to pick up the read-only bind (no `--build`): `PROD up -d --no-deps backend`.
3. ONLY if the `/backtest` PAGE code changed, rebuild the frontend too:
   `PROD up -d --build --no-deps frontend`.
4. Verify: `curl http://localhost:8000/backtest/runs` → `available:true`, with the keeper
   passes present.

**When a future pass earns promotion:** add its `pass_id` to `KEEPER_PASSES` in
`scripts/archive_backtest_prod.py` AND bump the `!= 324` count guard to the new run total,
then re-run step 1 + step 2.

---

## Safety

- **Prod DB is read-only to deploy** — a deploy rebuilds IMAGES, never touches data.
  Never `docker compose down -v` against prod; never `DROP DATABASE alphadeck`.
- **`--no-deps`** — rebuild only the named service; leaves postgres + the cron sidecar
  running untouched.
- **A forgotten `GIT_SHA` is safe, a wrong one is not.** Omitting it stamps `NULL` (honest: the
  image did not declare its commit). Never pass a sha you did not just read out of the checkout
  being built — `calls.code_sha` is a provenance claim, and a wrong claim is worse than none.
  Verify after a rebuild with
  `docker exec alphadeck-cron-1 printenv ALPHADECK_IMAGE_SHA`.
- **The cron sidecar is separately imaged.** It builds from the same context as the
  backend (`build: ./backend`) but is its own image (`alphadeck-cron`) / container
  (`alphadeck-cron-1`), so a backend rebuild does NOT refresh it. A change on the nightly
  `pipeline.daily` path must ALSO `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build --no-deps cron`, verified
  with `docker ps` (fresh CreatedAt) + `docker logs alphadeck-cron-1` (its next scheduled
  run) — else the 22:30 run silently stays on old code.
- **The sidecar's SHELL ships in that image too.** `backend/Dockerfile` does `COPY . .`, so
  `backend/scripts/daily_cron.sh` is baked in and the container runs `/app/scripts/daily_cron.sh`.
  A change to the loop itself (the schedule math, the failed-run retry) therefore needs the same
  `--no-deps --build cron` — there is no bind-mount to pick it up.
- **A cron ENV change needs the container RECREATED, not just rebuilt.** Compose applies a changed
  `environment:` block on recreate, which `up -d --build --no-deps cron` does — but a bare `docker
  restart alphadeck-cron-1` does not. After changing any of the cron service's vars, confirm with
  `docker exec alphadeck-cron-1 printenv TZ ALPHADECK_MARKET_TZ RUN_AT ALPHADECK_CRON_AT RETRY_DELAY_S`
  and `docker exec alphadeck-backend-1 printenv TZ ALPHADECK_MARKET_TZ ALPHADECK_CRON_AT`.
  The cron service's vars today: `TZ` (the shell's wall clock), **`ALPHADECK_MARKET_TZ`** (the trading-day
  clock — see the timezone note below), `RUN_AT` (the shell's schedule time),
  **`ALPHADECK_CRON_AT`** (the SAME wall time under the name `Settings.cron_run_at` reads, so the
  `--catch-up` guard's cutoff inside that container matches the time the shell fires on — both from the
  one host var `ALPHADECK_CRON_AT`), and **`RETRY_DELAY_S`** (`ALPHADECK_CRON_RETRY_DELAY_S`, default
  1200 s — the wait before the one retry of a failed scheduled run).
- **All four timezone readings must print the SAME zone, and they come from ONE host variable.**
  `ALPHADECK_MARKET_TZ` (default `America/New_York`) now feeds both services' container `TZ` *and* the
  `Settings.market_tz` the domain clock reads; `ALPHADECK_TZ` and `ALPHADECK_CRON_TZ` are **retired and
  read nowhere** — delete them from `.env` if they are still there. The `printenv` lines above are the
  test, because a bad zone fails **asymmetrically**: `market_tz()` raises a loud `RuntimeError` on the
  next Admin status read or daily run (it is called lazily, so boot is unaffected), while the sidecar's
  shell `date` would silently fall back to UTC and fire `RUN_AT` at the wrong hour.
- Prefer the targeted `--no-deps <service>` over a full `docker compose -f docker-compose.yml -f docker-compose.prod.yml up --build`
  (which rebuilds + restarts the whole stack — still safe, just slower).
- The prod stack is `restart: unless-stopped` — it self-recovers after a daemon/laptop
  reboot; a deploy just swaps in the new image.
