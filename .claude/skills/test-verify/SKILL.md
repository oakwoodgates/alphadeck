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
   auto-derived — no env needed (`ALPHADECK_TEST_DB` pins one; CI does).
2. **Run** (from `backend\`, venv active):
   ```
   pytest
   ```
3. **Read the summary line, not the exit code alone.** Report the actual counts:
   passed / failed / skipped. A large skipped count means Postgres was unreachable —
   fix that and re-run; do NOT report a pass.
4. **Lint + format:**
   ```
   ruff check . ; black --check .
   ```
5. **Report honestly:** "N passed, M skipped (DB tests executed)" — or the failures
   verbatim. Occasionally clean stale per-worktree DBs: `python -m db.drop_test_dbs`.
