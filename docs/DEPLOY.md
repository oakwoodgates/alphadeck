# Deploy — take code live on the running stack

How to ship a code change to the running **prod** stack (Flow A), and how to preview it on
**dev** first — from a branch's sha (Flow B) or straight from an uncommitted worktree
(Flow C). Written to be followed cold by an agent.

Pairs with: `docker-compose.yml` (prod) · `docker-compose.dev.yml` (dev override) ·
`docs/DEV_PROD.md` (the two stacks + the one-way refresh). Flow A runs through
`scripts/deploy.sh`, which builds the procedure's guards and checks in; its manual steps stay
below as the fallback. Flows B and C are manual: deploy IS the targeted `--build`.

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

**Use the wrapper.** `scripts/deploy.sh` runs this whole flow, guards included. Run it from the MAIN
checkout, on a clean `main` that equals `origin/main`:
```
git checkout main && git pull --ff-only origin main                     # the wrapper refuses anything else
bash scripts/deploy.sh --label pre-deploy-pr123 --dry-run backend cron    # the plan: changes nothing
bash scripts/deploy.sh --label pre-deploy-pr123 backend cron              # backup, build, restart rule, verify
bash scripts/deploy.sh --env-only cron                                    # a cron ENV change: recreate, no build
```
- **Services:** any of `frontend`, `backend`, `cron`, chosen by "The load-bearing fact" above and the
  cron caveat below. A build needs `--label`, which names the pre-deploy backup (a labeled dump is
  prune-exempt).
- **It refuses, changing nothing (exit 3),** on any of these:
  - a linked worktree, or no `.env` at the checkout root;
  - a dirty tree, or a branch other than `main`;
  - `HEAD != origin/main` after `git fetch` (deploy what is merged);
  - a clock inside **22:20–22:50 America/New_York**, around the 22:30 nightly pass. The script reads
    New York time whatever the host TZ. Git for Windows ships no zoneinfo, so there
    `TZ=America/New_York date` silently prints UTC; the script rejects that answer and uses the
    `EST5EDT` rule instead;
  - a pass in flight, or the call-of-record lock held. A pass in flight is `/admin/status`
    `cron.status == running`, or an `unfinished_run` still inside its timeout. The lock is held by a
    daily pass, a backfill or a fact repair (`pipeline/run_lock.py`; a repair holds it with the
    fact-writer gate, W1); the script only reads `pg_locks`, it never takes the lock.

    The check deliberately does NOT refuse on a held **per-company write lock** (W1,
    `db/write_lock.py` — namespace `0x414443`, `classid 4277315` in `pg_locks`). Those are held by a
    Workbench on-promote ingest, the Workbench price pull or a CLI ingest for one company at a time, and
    killing one by a rebuild is safe: every ingest leg is one transaction (an interrupted leg rolls back
    whole), the dead session releases its lock, and the unit is incremental, so the next run (the nightly
    at the latest) appends exactly what was missing. Refusing on them would make a deploy flaky during
    ordinary Workbench use for no protection.

    The same holds for the **staged onboarding job** (X1, the cron sidecar's second schedule — below):
    a `cron` rebuild kills a batch in flight, which is safe by the same argument plus its per-name
    checkpoint (`security_onboarding.legs_done`, written after each leg commits): the next run resumes each
    name at its next leg and appends nothing twice. Its own job lock (`(0x41444B, 3)`) is session-level and
    dies with the container. The wrapper does not wait for it.

  It lists every refusal in one run. A bad invocation, such as an unknown service or no `--label`,
  exits 2.
- **Then:** the backup (step 1 below), then the rebuild with `GIT_SHA` read from
  `git rev-parse HEAD` (never an argument; a `GIT_SHA` already in the shell is ignored). If the
  backend was rebuilt without the frontend, it runs `docker restart alphadeck-frontend-1`. Then it
  verifies.
- **Verification** exits 0 only if every check passes, else 5:
  - the backend is `healthy`;
  - `:8080/api/health` answers 200 (the proxy);
  - `ALPHADECK_IMAGE_SHA` in each REBUILT backend/cron container equals the sha built (after an
    env-only recreate: unchanged);
  - the cron container is running and, when recreated, printed its boot lines (the tail is shown);
  - `/admin/status` reads, with no NEW `cron.alarms` line and no quiet → loud `cron.status`. An alarm
    that was there before the deploy is shown, not failed.

  A failed step exits 4. On 4 or 5, use the `stack-recovery` skill.
- **`--dry-run`** runs the local checks for real: git (with `ls-remote` in place of `fetch`, so no
  ref moves) and the clock. It prints every stack read, command and check, and touches no Docker
  and no endpoint.

**Manual fallback** — the same procedure by hand, for when the wrapper cannot run (say the backend
is down, so a running pass cannot be ruled out: that is a recovery, see the `stack-recovery` skill).
Apply its guards yourself: never between 22:20 and 22:50 America/New_York, and never while
`/admin/status` reads `cron.status: running`.

1. **Back up prod first** (one-way Slice-4 dump to `./data/backups`, safe):
   ```
   docker compose -f docker-compose.yml -f docker-compose.prod.yml exec -T backend python -m pipeline.backup --label pre-deploy
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
   **A backend rebuilt WITHOUT the frontend needs `docker restart alphadeck-frontend-1`.** The
   recreated backend has a new Docker-network IP, and the frontend's nginx cached the old one, so every
   `/api` call 502s until nginx restarts. A rebuild that includes `frontend` recreates it, so no extra
   restart is needed.
4. **Verify the REAL artifact** (not just that it built):
   ```
   docker inspect --format '{{.State.Health.Status}}' alphadeck-backend-1   # -> healthy
   curl -s -o /dev/null -w '%{http_code}' http://localhost:8080/api/health   # -> 200: the /api PROXY, not just :8000
   docker exec alphadeck-backend-1 printenv ALPHADECK_IMAGE_SHA             # -> the sha you built (and alphadeck-cron-1)
   docker logs --tail 20 alphadeck-cron-1                                   # the boot lines (cron caveat below)
   curl http://localhost:8000/admin/status                                  # cron.status + cron.alarms: nothing new
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
  restart alphadeck-cron-1` does not. An env-ONLY change can keep the image:
  `docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --no-deps --no-build cron`,
  which is what `scripts/deploy.sh --env-only cron` runs. After changing any of the cron service's vars, confirm with
  `docker exec alphadeck-cron-1 printenv TZ ALPHADECK_MARKET_TZ RUN_AT ALPHADECK_CRON_AT RETRY_DELAY_S DB_WAIT_S RUN_TIMEOUT_S ONBOARD_EVERY_S ONBOARD_TIMEOUT_S PYTHONUNBUFFERED`
  and `docker exec alphadeck-backend-1 printenv TZ ALPHADECK_MARKET_TZ ALPHADECK_CRON_AT PYTHONUNBUFFERED`.
  The heartbeat URLs are credentials, so check them as set/unset, never by value:
  `docker exec alphadeck-cron-1 sh -c 'echo HEARTBEAT_URL=${HEARTBEAT_URL:+set} HEARTBEAT_BACKUP_URL=${HEARTBEAT_BACKUP_URL:+set}'`
  (an empty value prints nothing after the `=`). The sidecar's second boot line says the same thing:
  `docker logs alphadeck-cron-1 | grep "each pass bounded"` → `heartbeat on|off · backup heartbeat on|off`.
  The cron service's vars today: `TZ` (the shell's wall clock), **`ALPHADECK_MARKET_TZ`** (the trading-day
  clock — see the timezone note below), `RUN_AT` (the shell's schedule time),
  **`ALPHADECK_CRON_AT`** (the SAME wall time under the name `Settings.cron_run_at` reads, so the
  `--catch-up` guard's cutoff inside that container matches the time the shell fires on — both from the
  one host var `ALPHADECK_CRON_AT`), **`RETRY_DELAY_S`** (`ALPHADECK_CRON_RETRY_DELAY_S`, default
  1200 s — the wait before the one retry of a failed scheduled run or boot catch-up), **`DB_WAIT_S`**
  (`ALPHADECK_CRON_DB_WAIT_S`, default 300 s — the most the boot catch-up and each scheduled run wait for
  Postgres to accept a connection before firing anyway; see `FEED_LOOP.md` §catch-up on boot),
  **`RUN_TIMEOUT_S`** (`ALPHADECK_CRON_RUN_TIMEOUT_S`, default 5400 s — the most one `pipeline.daily` pass
  may run before the script's `timeout` stops it; a timed-out pass is retried, then paged), the two
  optional external-heartbeat URLs **`HEARTBEAT_URL`** / **`HEARTBEAT_BACKUP_URL`** (same names in `.env`,
  empty by default = a silent no-op; see `FEED_LOOP.md` §the scheduling sidecar), and the **second
  schedule** (X1): **`ONBOARD_EVERY_S`** (`ALPHADECK_ONBOARD_EVERY_S`, default 1800 s — how long after one
  run of the staged onboarding job ends the next fires; **0 turns it off**) and **`ONBOARD_TIMEOUT_S`**
  (`ALPHADECK_ONBOARD_TIMEOUT_S`, default 2700 s — the hard bound on one run; the job's own 1500 s budget is
  checked between legs, and one heavy filer's Form 4 leg alone MEASURED ~10 min, so the bound only stops a
  hung run). The job's other dials are `Settings` (`ALPHADECK_ONBOARD_*`), read where they act and NOT
  injected by compose (the code defaults apply): the cron container reads the job's
  `_WINDOW_BEFORE_MIN` / `_WINDOW_AFTER_MIN`, `_BATCH_MAX_NAMES`, `_BATCH_BUDGET_S`, `_DAILY_MAX_NAMES`,
  `_MAX_ATTEMPTS` and the nightly's `_NIGHTLY_WINDOW_DAYS`; the backend reads the promote's
  `_FAST_LANE_MAX` and the same window (a promote inside it queues). To change the window, set it on BOTH
  services' `environment:`. Confirm the loop started with
  `docker logs alphadeck-cron-1 | grep onboard-cron` → `onboard-cron: every 1800s …`. `SLACK_WEBHOOK_URL` now
  also carries the shell's failure page (`python -m notify.page`). `PYTHONUNBUFFERED=1` is baked into the
  image (not compose), so it needs a rebuild, not a recreate. Compose also caps the backend, cron and
  frontend logs at 50 MB × 5 (`logging:`), which, like an env change, applies when the container is recreated.
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
