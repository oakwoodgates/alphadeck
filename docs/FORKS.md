# Forks — isolated experiment stacks, seeded one-way from prod

A **fork** is a full Alpha Deck stack (frontend + backend + its own Postgres) for testing a "different
version of reality": its own theses, baskets, calls, and config, and its own signal/detector code (a git
branch running from its own worktree). Forks generalize the Signals-Lab pattern — one command stands one
up, refreshes it from prod, or tears it down, and **prod is never written by any fork operation**.

Pairs with: `docker-compose.fork.yml` (the ONE generic override) · `.env.fork.example` (the identity
template) · `scripts/fork.sh` (lifecycle) · `scripts/refresh-fork.sh` (the one-way seeder) ·
`docs/DEV_PROD.md` (the dev tier) · `docs/SIGNALS_LAB.md` (the sig tier this generalizes — sig itself
stays as-is for now, on its own branch).

---

## The model — forks beside the existing tiers

| Tier | Compose project | DB name | Host ports (app/api/pg) | Cron | Env | Runs from |
|------|-----------------|---------|--------------------------|------|-----|-----------|
| **live / prod** | `alphadeck` | `alphadeck` | 8080 / 8000 / **5544** | **ON** | `.env` | main checkout |
| **dev** | `alphadeck_dev` | `alphadeck_dev` | 8081 / 8001 / **5545** | OFF | `.env.dev` | main checkout |
| **sig (Signals Lab)** | `alphadeck_sig` | `alphadeck_sig` | 8082 / 8002 / **5546** | OFF | `.env.sig` | its worktree |
| **fork `<name>`** | `alphadeck_<name>` | `alphadeck_<name>` | slot ladder (below) | OFF | `.env.fork` | its worktree |

- **Slot ladder:** slot N → app `808N` / api `800N` / pg `554(4+N)`. Slots 0–2 are prod/dev/sig; forks
  start at **3** (first fork: 8083/8003/5547). The slot lives in the fork's `.env.fork`.
- **Identity is one file.** `.env.fork` at the fork worktree root carries `FORK_NAME` (→ project
  `alphadeck_<name>`, DB `alphadeck_<name>`, volumes `alphadeck_<name>_*`) and the three ports. One
  generic compose override serves every fork; there are no per-fork compose files.
- **Isolation is structural, not a convention.** The distinct Compose project name auto-namespaces the
  containers, the network, and the named volumes — a fork's Postgres physically cannot reach prod's
  `pgdata`. Distinct ports let every tier run simultaneously. The tooling is **fail-closed** on top:
  `FORK_NAME` ∈ {empty, `prod`, `dev`, `sig`, `test`} is refused, names are `[a-z0-9_]+` only, and
  anything resolving to prod's project/DB dies before a single Docker call.
- **Exactly one live scheduled fetcher: prod.** A fork's cron service rides the `donotstart` profile —
  it is never part of the fork's resolved config. SEC etiquette is a correctness requirement here.

## Lifecycle (the raw forms are authoritative; `fork.sh` wraps them)

```bash
# CREATE — from the MAIN checkout root. Makes worktree .claude/worktrees/fork-<name> (new branch
# fork/<name> off main, or --branch <existing> / --from <ref>), scaffolds .env.fork, builds + starts the
# stack, seeds it from prod. Re-runnable.
scripts/fork.sh init <name> --slot 3

# START / STOP — from the fork worktree root. `down` KEEPS the fork's volumes.
scripts/fork.sh up
scripts/fork.sh down

# RE-SEED from prod — from the fork worktree root. RESET-ONLY this pass (see below): --reset is REQUIRED.
scripts/fork.sh refresh --reset               # newest prod dump + prod's caches (cache ON by default)
scripts/fork.sh refresh --reset --fresh       # take a brand-new READ-ONLY prod dump first
scripts/fork.sh refresh --reset --no-cache    # DB only, skip the appdata cache copy
scripts/fork.sh refresh --reset --dry-run     # print the plan, touch nothing

# DESTROY — from the fork worktree root. down -v: removes the fork's containers AND volumes; prints the
# worktree/branch removal commands to run from the main checkout afterwards. Prod/dev/sig untouched.
scripts/fork.sh destroy --yes
```

The raw compose invocation behind `up` (identical semantics):

```bash
docker compose -f docker-compose.yml -f docker-compose.fork.yml -p alphadeck_<name> --env-file .env.fork up -d --build
```

Never a bare `docker compose up` while intending a fork — that rebuilds **prod**. A fork is always the
explicit `-f … -f docker-compose.fork.yml -p alphadeck_<name> --env-file .env.fork` invocation (which is
why `fork.sh` exists).

## Refresh is RESET-ONLY this pass — know what `--reset` means

`refresh --reset` **drops the fork's database and re-clones prod's newest dump** (then migrates forward
and, by default, copies prod's appdata caches). The fork's own theses/baskets/calls are **wiped**. That
is the right semantics for: the first seed, a throwaway code-side experiment (the sig workflow), or
"start my experiment over."

The app-preserving **data mode** — merge prod's new facts into the fork while keeping the fork's own
app plane — is **designed but deliberately not built yet** (deferred). The `--reset` flag is required *today* so
that if data mode ever lands as the default, the bare command's meaning cannot silently flip from
"wipe + re-clone" to "merge". Until then: if your fork holds theses you care about, **don't refresh** —
the data you seeded with keeps working (it just ages).

The refresh is **one-way by construction**: prod is only ever read via `pg_dump` (the same
anti-truncation guarantee as `refresh-dev` / `refresh-sig`); the dump is read from the **main
checkout's** `data/backups/` (resolved via git), and the restore targets only the fork's own Postgres.

## The deferred data-mode design (for when it's built)

Data mode is the **second, app-preserving** refresh semantics: additively merge prod's newer *data
plane* into the fork while never touching its *app plane*. It is ~100 lines of SQL generation in
`refresh-fork.sh` (still just a script — no services, no schema, no backend code), **deferred, not
built**. The design is recorded here so it isn't re-derived (and re-validated at build time — the guard
below is what catches schema drift):

- **Two planes, by explicit allowlist (never a `fact_*` pattern):**
  - *Data plane (merged):* `security_master` plus the global market-fact tables — `fact_price_eod`,
    `fact_insider_txn`, `fact_dilution`, `fact_catalyst`, `fact_revenue_mix`, `fact_shares_outstanding`,
    `fact_cash_burn`, `fact_fund_shares`, `fact_spac_event`.
  - *App plane (never written):* `tenant`, `thesis`, `basket_member`, `evidence`, `catalyst`,
    `kill_criterion`, `calls`, `operator_decision`, `thesis_exclusion` — **plus two thesis-FK'd fact
    tables**, `fact_theme_conviction` (operator-ratified, `thesis_id` FK `ON DELETE CASCADE`) and
    `fact_spac_match` (`thesis_id` FK). Both are thesis-scoped, not global market facts, so merging
    prod's rows would FK-violate the instant prod holds a thesis the fork doesn't. State the consequence
    so it never reads as a bug: a fork's theme convictions are its own and its SPAC-match tape freezes at
    seed, exactly like `calls`; `--reset` re-clones all three.
  - *Ignored:* `schema_migrations`.
- **Fail-closed classification guard (recall-is-sacred, applied to refresh):** after staging prod's
  dump, list its public tables and **fail loudly** on any table not in (data ∪ app ∪ ignored) — so a
  future migration that adds a table is forced into an explicit classification instead of producing a
  silently never-refreshed fork (a forgotten fact table would be a silent name-drop).
- **Merge mechanism:** restore prod's dump into a scratch `alphadeck_<name>_staging` on the *fork's*
  Postgres → run `db.migrate` on staging up to the fork branch's schema (so column lists align) → per
  data-plane table in FK order (`security_master` first), stream `\copy … TO STDOUT` into a fork-DB
  transaction that loads a `TEMP TABLE (LIKE T)` and runs
  `INSERT INTO T SELECT * FROM tmp ON CONFLICT (id) DO NOTHING` → drop staging. Stock `postgres:16`, no
  extensions (`postgres_fdw` is the fallback if the pipe proves ugly).
- **Why the union is safe:** all PKs are UUIDs (no sequence/`setval` collisions); fact tables are
  append-only bitemporal, so insert-missing-by-id receives *all* of prod's new versions and the as-of
  read dedups by natural key + `recorded_at DESC` — a fact ratified differently in prod vs the fork
  resolves by the later `recorded_at` (standard bitemporal correction, visible in provenance).
  `security_master` is the one UPDATE-in-place table, so insert-missing gives it **new names only**:
  fork-side business-type re-tags survive, at the cost of not receiving prod-side identity
  re-enrichment for names the fork already holds (`--reset` is the fix when that matters).
- **Self-assertion + idempotency:** snapshot app-plane `count(*)` before, assert identical after (fail
  if any app table changed); a second back-to-back run adds `+0` rows (count the table, not the read).
- **The two load-bearing traps:** an app-plane write destroys a fork's authored reality (the wipe-trap
  analog — the allowlists, the guard, and the self-assertion defend it); a **schema-divergent** fork
  that reshapes a data-plane table can't union prod's rows and must use `--reset` only (a reshaped table
  surfaces as a loud column-mismatch failure in the merge transaction).

## What a fork does NOT do (by design — zero code enforces it)

- **No live SEC fetching.** `.env.fork` scaffolds `ALPHADECK_USER_AGENT=` **empty on purpose**: config
  is fail-open, so the fork skips every live EDGAR pull — a fork is *structurally* unable to spam the
  SEC. Expect `/health` to report `degraded: {"user_agent": "missing"}` in a fork; that is the feature,
  not a fault. Setting a UA in `.env.fork` is a deliberate opt-in to ad-hoc pulls — know why you're
  doing it; bulk data wants go through prod (below).
- **No call-of-record.** A fork's cron is off, and the daily recording gate (R2a, `pipeline/daily.py`)
  withholds the log of record on any no-live run anyway — a snapshot-fed stack has nothing honest to
  record daily. Forks compute calls **on read**; honest history for an experimental detector is the
  replay harness (`docs/REPLAY.md`), not a cron. Consequences you'll see: the fork's Scoreboard shows
  prod's record **frozen at the seed**, and the admin staleness readout grumbles about the missing cron
  — cosmetic, exactly as in dev/sig.
- **No independent universe.** A fork works within the names prod tracks (that constraint is what keeps
  this whole design a simple one-way copy).

## Getting NEW data into a fork: add-to-prod → refresh

Prod fetches facts **per thesis** — a name gets data only by sitting in some prod thesis's basket. So to
work on a name the fork doesn't have data for, put it in prod first, let prod's nightly cron fetch it,
then `fork.sh refresh --reset`. Two zero-code conventions (pick per name):

1. **Thesis parity** — create the experiment's thesis in prod too (quiet/Incubating — inverse loudness
   means it won't nag) with the names you need. Cleanest provenance, and prod's own call on that thesis
   is a free baseline for the fork's variant. Cost: prod's cron records calls for a thesis you may not
   be acting on (Scoreboard entries).
2. **A "Fork watchlist" thesis** — one junk-drawer basket in prod whose only job is making the cron
   fetch names. One move per name. Cost: one permanently meaningless thesis on prod's Board + call log.

## PowerShell (no Git Bash) — the restore redirection differs

`refresh-fork.sh` runs under Git Bash (`psql -f - < dump.sql`). Under pure PowerShell the restore is a
pipe instead (the `docs/DEV_PROD.md` / `docs/SIGNALS_LAB.md` idiom):

```powershell
# From the FORK worktree root, with the fork stack up. <name> = your FORK_NAME.
$fork  = "-f","docker-compose.yml","-f","docker-compose.fork.yml","-p","alphadeck_<name>","--env-file",".env.fork"
$forkpg = docker compose @fork ps -q postgres
$dump  = (Get-ChildItem <main-checkout>\data\backups\*.sql | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
docker exec $forkpg psql -U alphadeck -d postgres -c "DROP DATABASE IF EXISTS alphadeck_<name> WITH (FORCE)"
docker exec $forkpg psql -U alphadeck -d postgres -c "CREATE DATABASE alphadeck_<name>"
Get-Content $dump | docker exec -i $forkpg psql -U alphadeck -d alphadeck_<name>     # <-- pipe, not `< file`
docker exec $(docker compose @fork ps -q backend) python -m db.migrate
```

## Safety recap

- **One-way prod → fork** (`pg_dump`, read-only) — prod is never written by fork work; there is no
  fork→prod path in any script.
- **Fail-closed identity** — a misconfigured `.env.fork` dies loudly before Docker is touched; it can
  never resolve to prod's project or DB.
- **`down` vs `destroy`** — `down` keeps the fork's volumes (like dev); `destroy --yes` is the explicit
  `down -v`. Neither can reach `alphadeck_pgdata`.
- **Load** — each fork is 3 more containers beside prod's 4 (and dev/sig when up). Bring idle forks
  down; the volumes persist, so the next `up` is quick.
- Five tiers — `alphadeck_test_*` (tests) / `alphadeck_dev` / `alphadeck_sig` / `alphadeck_<name>`
  (forks) / `alphadeck` (live) — cannot reach one another's live DB.
