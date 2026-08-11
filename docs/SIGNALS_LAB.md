# Signals Lab — the off-prod historical-replay tier

A **third, throwaway-safe** Alpha Deck stack (`alphadeck_sig`) beside prod + dev, for **honest historical
replay/backtest** of new signal detectors: run the branch's detector code **as-of past timestamps** and see
what it *would have fired*, on a **read-only copy of prod's data (DB + caches)**, with **only the code
differing**. It never drives decisions and never touches prod or dev.

Pairs with: `docker-compose.yml` (prod, unchanged) · `docker-compose.sig.yml` (the sig override) ·
`.env.sig.example` (the env template) · `scripts/refresh-sig.sh` (the one-way prod→sig refresh) ·
`docs/REPLAY.md` (the replay harness) · `docs/DEV_PROD.md` (the dev tier this mirrors).

---

## The model — a third isolated tier

| Tier | Compose project | DB name | Postgres volume | Host ports (app/api/pg) | Cron | Env | Runs from |
|------|-----------------|---------|-----------------|--------------------------|------|-----|-----------|
| **live / prod** | `alphadeck` | `alphadeck` | `alphadeck_pgdata` | 8080 / 8000 / **5544** | ON | `.env` | main checkout |
| **dev** | `alphadeck_dev` | `alphadeck_dev` | `alphadeck_dev_pgdata` | 8081 / 8001 / **5545** | OFF | `.env.dev` | main checkout |
| **sig (Signals Lab)** | `alphadeck_sig` | `alphadeck_sig` | `alphadeck_sig_pgdata` | **8082 / 8002 / 5546** | OFF | `.env.sig` | **the worktree** |

## Isolation guarantees (why the lab is throwaway-safe)

- **Structural, not a convention.** A distinct Compose **project name** (`-p alphadeck_sig`) auto-namespaces
  the containers, the network, **and the named volumes** — so the sig Postgres data lives in
  `alphadeck_sig_pgdata` and *physically cannot reach* prod's `alphadeck_pgdata`. Distinct host ports let
  sig, dev, and prod run **at the same time**.
- **Runs FROM THE WORKTREE** (not the main checkout, unlike dev). Maximal isolation: the main checkout is
  never involved in running the lab, so a lab failure can't cost prod/main. `./backend` + `./frontend` in
  the compose files are the worktree's own, and `.env.sig` lives at the worktree root.
- **Cron is OFF (R16).** A frozen prod snapshot is what makes the backtest a *controlled* comparison — sig
  recomputes on the **same facts** as prod, so any call-card/backtest diff is provably the new code. New
  detectors' historical facts are added by **manual backfill**, never a live nightly pull.
- **The refresh is ONE-WAY: prod → sig, read-only.** `pg_dump` READS prod; the dump is restored into the
  **sig** DB. Prod is never written (the anti-truncation guarantee — see `scripts/refresh-sig.sh`).

## Prerequisites (once)

1. Create `.env.sig` at the **worktree root** (copy `.env.sig.example`, fill in values — reusing prod's
   values is fine). It is gitignored.
2. The backend image bakes the `.[replay]` extra (duckdb + pyarrow) that the backtest harness needs — the
   sig overlay passes `--build-arg PIP_EXTRAS=replay` to `backend/Dockerfile` (prod/dev stay lean).

## Commands (run from the WORKTREE root)

### Up
```
docker compose -f docker-compose.yml -f docker-compose.sig.yml -p alphadeck_sig --env-file .env.sig up -d --build
```
App at http://localhost:8082, API/docs at http://localhost:8002/docs. Cron is OFF; the backend serves with
`--reload` over a bind-mount of the worktree's `./backend`, so branch edits are live.

### Refresh from prod (one-way, prod→sig)
```
bash scripts/refresh-sig.sh                 # newest prod dump + prod cache (cache ON by default)
bash scripts/refresh-sig.sh --fresh         # take a brand-new read-only prod dump first, then load it
bash scripts/refresh-sig.sh --no-cache      # DB only, skip the appdata cache copy
bash scripts/refresh-sig.sh --dry-run       # print the plan, touch nothing (safe anywhere)
```
Reads prod's newest `*.sql` from the **main checkout's** `data/backups` (prod's read-only dump path,
resolved via git) and restores it into `alphadeck_sig`; copies prod's `appdata` cache volume into sig by
default (the backtest wants the caches). Requires the **sig** stack up (and the **prod** stack up for
`--fresh`). Ends with `OK: sig refreshed from <dump> (<N> theses, <M> calls)`.

PowerShell (no Git Bash) restore redirection differs — a pipe, not `< file`:
```powershell
$sig  = "-f","docker-compose.yml","-f","docker-compose.sig.yml","-p","alphadeck_sig"
$sigpg = docker compose @sig ps -q postgres
$dump = (Get-ChildItem <main-checkout>\data\backups\*.sql | Sort-Object LastWriteTime -Descending | Select-Object -First 1).FullName
docker exec $sigpg psql -U alphadeck -d postgres -c "DROP DATABASE IF EXISTS alphadeck_sig WITH (FORCE)"
docker exec $sigpg psql -U alphadeck -d postgres -c "CREATE DATABASE alphadeck_sig"
Get-Content $dump | docker exec -i $sigpg psql -U alphadeck -d alphadeck_sig     # <-- pipe, not `< file`
docker exec $(docker compose @sig ps -q backend) python -m db.migrate
```

### Backtest (the primary use — in-container, on the branch code)
```
# 1. refresh sig from prod   2. (the branch's migrations apply on `up`/refresh)   3. backfill new historical
#    facts with correct knowability   4. replay over the target window on the branch code:
docker exec $(docker compose -f docker-compose.yml -f docker-compose.sig.yml -p alphadeck_sig ps -q backend) \
  python -m replay.run --start 2025-04-01 --end 2026-06-30 --pin 2027-01-01 --out /data/replay-out
```
Writes `episodes.parquet` / `outcomes.parquet` / `metrics.json` showing what the new detectors *would have
fired*. For the baseline, run the same window/PIN on prod code and diff. Full harness + the metric set:
`docs/REPLAY.md`. **Known limitation:** thesis definitions / `security_master` are read from the *current*
SoR (only the facts are as-of), so backtest over windows in which the basket was stable and treat
evolving-basket results as indicative only (`docs/REPLAY.md` §KNOWN LIMITATION).

### Teardown (throwaway — nothing to preserve)
```
docker compose -f docker-compose.yml -f docker-compose.sig.yml -p alphadeck_sig down -v   # drops the sig volumes too
git worktree remove <this-worktree>                                                       # remove the worktree
```
`down -v` deletes `alphadeck_sig_pgdata` + `alphadeck_sig_appdata` (the lab is throwaway; prod/dev are
untouched). Unlike dev, `down -v` here is expected — there is nothing in the lab worth keeping.

## Safety recap

- **One-way prod → sig** (`pg_dump`, read-only) — prod is never written by lab work.
- **Never** a bare `docker compose up` while intending sig (that rebuilds **prod**). Sig is always the
  explicit `-f docker-compose.yml -f docker-compose.sig.yml -p alphadeck_sig` invocation.
- Four tiers — `alphadeck_test_*` (tests) / `alphadeck_dev` (dev) / `alphadeck_sig` (lab) / `alphadeck`
  (live) — cannot reach one another's live DB. The lab adds no code to prod's run path.
