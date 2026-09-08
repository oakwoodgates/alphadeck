---
name: prune-stale
description: >-
  Prune stale git worktrees + branches + per-worktree test DBs that accrue from
  agent builds and past sessions. Trigger on "prune stale worktrees", "clean up
  branches", "drop stale test DBs", "tidy the repo". Read-only survey first;
  dropping alphadeck_test_* never touches the demo/prod DB (separate databases).
---

# Prune stale worktrees, branches & test DBs

Survey first (read-only), then remove. The one irreversible action is a `DROP DATABASE`
— and it's safe ONLY because test DBs are SEPARATE databases from the live data.

## Guardrails (never violate)
- **`DROP DATABASE alphadeck_test_*` is safe; `alphadeck` / `alphadeck_dev` is the real
  data — NEVER drop those.** Test DBs are distinct databases on the SAME Postgres
  container; dropping one never touches the demo/prod data.
- **Never remove the worktree you're RUNNING IN** (breaks the session), and **never
  delete a CHECKED-OUT branch** (git refuses anyway).
- Keep the LIVE worktrees' test DBs — compute the live hashes before dropping.

## Survey (read-only)
- `git worktree list` — the worktrees + their branches.
- Merged? per branch: `git merge-base --is-ancestor <branch> origin/main && echo MERGED`
  (after `git fetch origin --prune`). Merged = safe; unmerged may be superseded (a
  re-cut branch, e.g. a stale-base recovery) or genuinely stale — check before -D.
- Test DBs live on BOTH Postgres containers — check each:
  `docker exec alphadeck-postgres-1 psql -U alphadeck -d postgres -tAc "SELECT datname FROM pg_database WHERE datname LIKE 'alphadeck_test%'"`
  and the same on `alphadeck_dev-postgres-1`.

## Remove
1. **Test DBs** — the live per-worktree names to KEEP are `alphadeck_test_` +
   `sha1(<worktree-root>)[:8]` (per `backend/db/testdb.py`; or an `ALPHADECK_TEST_DB`
   pin), plus any xdist `_gw*` suffixes. Drop the ORPHANS (plain `DROP DATABASE`, no
   FORCE; confirm no active connections first via `pg_stat_activity`). Feature-named
   test DBs from old runs (e.g. `alphadeck_test_p4calib`) are orphans.
2. **Worktrees** — `git worktree remove <path>` for the stale ones (needs a clean tree;
   NOT the one you're in). Then `git worktree prune -v`. **Traps:** the auto-mode
   classifier may BLOCK `git worktree remove` → surface to the operator for approval (or
   they run it); on Windows a "filename too long" can stop the file delete but git still
   deregisters the worktree (leftover files on disk are harmless, deletable manually).
3. **Branches** — delete merged + superseded/stale with `git branch -D <list>` (force,
   since superseded branches read "unmerged"), EXCEPT `main` and any checked-out branch.
   Leftover remote branches (rare — most `--delete-branch` on merge):
   `git push origin --delete <branch>`.

## After
`git worktree list` shows only the live worktrees; `git branch` only `main` + live
feature branches; the test-DB lists show only the live per-worktree names.
