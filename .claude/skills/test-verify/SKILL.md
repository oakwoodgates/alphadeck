---
name: test-verify
description: >-
  Run the backend suite so a "tests pass" claim is honest — the DB-backed tests
  SKIP when Postgres is unreachable, and a large skipped count is NOT a pass.
  Trigger on "run the tests", "do the tests pass", or before claiming any change
  is tested. Uses the per-worktree auto-derived test DB; never touches demo/prod
  data (fail-closed by construction).
---

# Test verify — an honest "tests pass"

Authoritative rules: `CLAUDE.md` §Conventions (the "tests pass" claim rule + the
idempotency count-the-table rule); the fail-closed test-DB hook is
`backend/db/testdb.py`.

## Guardrails (never violate)
- **Skipped ≠ passed.** The DB suite silently skips without Postgres. A claim of
  "tests pass" must come from a run whose summary shows the DB tests EXECUTED.
- **Never point `DATABASE_URL` at a real DB to make tests run.** The
  `pytest_configure` hook auto-derives `alphadeck_test_<hash>` per worktree and
  fail-closed refuses any non-`alphadeck_test*` name (the 2026-07-21 lesson) —
  work WITH it, never around it.
- **When WRITING idempotency tests: count the table, not the read.** The bitemporal
  as-of read dedups, so a duplicate append hides behind a correct read. Assert
  `count(*)` / `list_*` length before AND after a re-run.

## Steps
1. **Postgres reachable?** Anything serving host port 5544 works (the full stack, or
   `docker compose -f infra/docker-compose.yml up -d`). The test DB name is
   auto-derived — no env needed (`ALPHADECK_TEST_DB` pins one; CI does). Under `-n`,
   each xdist worker gets its own `alphadeck_test_<hash>_gwN` (the hook's
   `PYTEST_XDIST_WORKER` suffix), so workers never share a DB.
2. **Run the full suite** (from `backend\`, venv active) — parallel:
   ```
   pytest -n 6
   ```
   ~6 min here (measured 365 s / 1878 passed; serial was ~31 min before the pandas
   sentinel + xdist). **Never two pytest runs at once in one worktree** — they share
   the per-worktree DB and collide (spurious failures). Before any re-run, confirm a
   killed piped run didn't orphan a `python.exe` (`tasklist | findstr python`) — an
   orphan keeps running against the DB.
3. **Scoped iteration** (between full runs): run only the changed area's tests and
   skip the six `slow` replay sweeps (~90 s each):
   ```
   pytest tests/<area> -n 6 -m "not slow"        # e.g. pytest tests/ingest -n 6 -m "not slow"
   ```
   then the full suite ONCE before the PR. **EXCEPTION — the change touches the PIT /
   replay / signal path** (`backend/db/bitemporal`, `backend/signals`, `backend/calls`,
   `backend/replay`, `backend/pipeline/core`): the FULL suite including `slow` runs
   every iteration; `-m "not slow"` is never used there (the sweeps are the only
   end-to-end exercise of that path).
4. **Waiting.** The full suite (~6 min) must run in the BACKGROUND, output redirected to
   a file outside the repo (the session scratchpad), e.g.
   ```
   pytest -n 6 -q > <scratchpad>\pytest-full.log 2>&1     # run_in_background; read the file after
   ```
   then wait for the harness completion notification — do NOT poll in a loop.
   pytest-timeout (120 s default, `thread` method — the only one that can kill a hung
   native call; the slow sweeps override to 300 s) is the ONLY hang guard for a
   background run: a hung test fails LOUD instead of hanging the notification forever.
5. **Read the summary line, not the exit code alone.** Report the actual counts:
   passed / failed / skipped. A large skipped count means Postgres was unreachable —
   fix that and re-run; do NOT report a pass.
6. **Lint + format:**
   ```
   ruff check . ; black --check .
   ```
7. **Report honestly:** "N passed, M skipped (DB tests executed)" — or the failures
   verbatim. Occasionally clean stale per-worktree DBs: `python -m db.drop_test_dbs`.
