---
name: fork
description: >-
  Create, refresh, or tear down a FORK — an isolated experiment stack (own
  frontend/backend/Postgres, own theses/signals) seeded one-way from prod, cron
  OFF, on the slot ladder (808N/800N/554(4+N), N>=3). Trigger on "spin up a fork",
  "make a fork", "new experiment stack", "refresh the fork", "tear down the fork".
  Prod is never written.
---

# Forks — isolated experiment stacks

Authoritative runbook: `docs/FORKS.md`. This skill is the executable checklist —
`scripts/fork.sh` wraps the raw compose forms (the doc's raw forms are authoritative).
STOP on any failed precondition and report it; do NOT guess.

## Guardrails (never violate)
- **Prod is never written.** Refresh is a one-way `pg_dump` READ from prod into the
  fork's own DB. There is no fork->prod path in any script.
- **`init` runs from the MAIN checkout root**; `up` / `down` / `refresh` / `destroy`
  run from the FORK's own worktree root. Worktrees never run prod's stack.
- **Fail-closed identity.** `FORK_NAME` in {empty, prod, dev, sig, test} is refused;
  names are `[a-z0-9_]+`. A misconfigured `.env.fork` dies before Docker is touched.
- **Exactly one live SEC fetcher: prod.** A fork's cron rides the `donotstart` profile;
  `.env.fork` scaffolds `ALPHADECK_USER_AGENT=` EMPTY on purpose (a fork structurally
  can't live-fetch SEC — `/health` reporting `degraded` UA is expected, not a fault).
- **`refresh --reset` WIPES the fork's app plane** (its theses/baskets/calls) and
  re-clones prod's newest dump. Reset-only this pass — if the fork holds work you care
  about, don't refresh (the seeded data keeps working, it just ages).

## Lifecycle (pick per the request)
1. **Create** (from the MAIN checkout) — makes the worktree + branch, scaffolds
   `.env.fork`, builds/starts the stack, seeds from prod. Pick a free slot (N>=3):
   ```
   scripts/fork.sh init <name> --slot 3
   ```
2. **Start / stop** (from the fork worktree) — `down` KEEPS the fork's volumes:
   ```
   scripts/fork.sh up
   scripts/fork.sh down
   ```
3. **Re-seed from prod** (from the fork worktree; RESET-ONLY — wipes the fork's app plane):
   ```
   scripts/fork.sh refresh --reset
   ```
4. **Destroy** (from the fork worktree) — `down -v`; prints the worktree/branch removal
   to run from the main checkout afterward. Prod/dev/sig untouched:
   ```
   scripts/fork.sh destroy --yes
   ```

## New data into a fork
A fork works within the names PROD already tracks. To work on a new name: add it to a
prod thesis (or a "Fork watchlist" thesis), let prod's cron fetch it, then
`fork.sh refresh --reset`. See `docs/FORKS.md` -> "Getting NEW data into a fork".

## Slots
Slot N -> app 808N / api 800N / pg 554(4+N). Prod/dev/sig hold 0/1/2; forks start at 3
(first fork 8083/8003/5547). The slot lives in the fork's `.env.fork`. Bring idle forks
down — each is 3 extra containers; the volumes persist, so the next `up` is quick.
