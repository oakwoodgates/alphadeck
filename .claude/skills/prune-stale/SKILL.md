---
name: prune-stale
description: >-
  Use when asked to prune stale worktrees, branches or test databases, or to tidy the repo. Surveys read-only
  first, then removes merged and superseded ones; the real databases are never dropped.
---

# Prune stale worktrees, branches and test databases

Agent builds and old sessions leave worktrees, branches and per-worktree test databases behind. Survey first
(read-only), then remove. The one irreversible step is `DROP DATABASE`.

## Guardrails

- Drop only test databases: `alphadeck_test_` plus 8 hex characters and an optional `_gw<N>` (`db/testdb.py`).
  Never drop `alphadeck`, `alphadeck_dev`, `alphadeck_sig` or a fork's `alphadeck_<name>`. Why the exact pattern:
  `scripts/fork.sh` now refuses names starting with `test`, but a fork made before that guard (say `testbed`)
  owns a real database `alphadeck_testbed` that `LIKE 'alphadeck_test%'` also matches.
- Plain `DROP DATABASE`, never `WITH (FORCE)`; check `pg_stat_activity` first. A database in use belongs to a
  live run.
- Keep the live worktrees' databases: compute their names before dropping.
- Never remove the worktree you run in or the main checkout, never delete a checked-out branch, never `--force` a
  worktree removal.
- A `fork-<name>` or sig worktree with a live stack: tear the stack down first (the `fork` skill,
  `scripts/fork.sh destroy --yes`), or its containers and volumes stay behind.
- Merged means an ancestor of `origin/main`. A branch that is only inside an open integration branch is not
  merged yet.

## Steps

Survey, read-only:

1. `git fetch origin --prune`, then `git worktree list` and `git branch -vv`.
2. Per branch: `git merge-base --is-ancestor <branch> origin/main && echo MERGED`. An unmerged one may be
   superseded (a re-cut branch) or stale: read its commits before deleting.
3. Per running Postgres container (prod, dev, sig and every fork hold their own):
   ```
   for c in $(docker ps --filter name=postgres --format '{{.Names}}'); do echo "$c"; docker exec "$c" psql -U alphadeck -d postgres -tAc "SELECT datname FROM pg_database WHERE datname LIKE 'alphadeck_test%'"; done
   ```

Remove:

4. Test databases. A live name is `alphadeck_test_` plus the first 8 hex characters of the sha1 of that worktree's
   `git rev-parse --show-toplevel` output, with `_gw<N>` suffixes (or an `ALPHADECK_TEST_DB` pin):
   `printf %s "$(git -C <worktree> rev-parse --show-toplevel)" | sha1sum | cut -c1-8`. `db/testdb.py` hashes the
   `backend/` path instead when git cannot run; `resolve_test_db_name()` in that worktree's venv prints the exact
   name and opens no connection. Drop the other names that fit the pattern:
   `docker exec <container> psql -U alphadeck -d postgres -c 'DROP DATABASE "<name>"'`. A feature-named leftover
   from an old pin (`alphadeck_test_<word>`) is the operator's call. For the server on 5544 the operator can run
   `python -m db.drop_test_dbs --dry-run`, then without the flag: it drops every idle `alphadeck_test%` database,
   live worktrees' included (recreated on their next run), and skips any in use.
5. Worktrees: `git worktree remove <path>` (needs a clean tree), then `git worktree prune -v`. A permission prompt
   on it goes to the operator. On Windows a "filename too long" error can stop the file deletion while git still
   deregisters the worktree; the leftover files are harmless and can be deleted by hand.
6. Branches: `git branch -d` for merged ones, `git branch -D` for superseded ones once read. Merged leftovers on
   the remote: `git push origin --delete <branch>`.

## Checks

- `git worktree list` shows only live worktrees; `git branch` shows `main` and live feature branches.
- The database survey lists only live names, and every database dropped matched the pattern.

## Stop and ask when

- A branch is unmerged and not clearly superseded.
- A worktree has uncommitted changes or a running stack, or a prompt asks for `--force`.
- A database has active connections or its name does not fit the pattern.

## Model

[OPERATIONS], "Forks" and "The stacks".

[OPERATIONS]: ../../../docs/OPERATIONS.md
