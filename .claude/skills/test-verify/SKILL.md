---
name: test-verify
description: >-
  Use when asked to run the backend tests, to say whether tests pass, or before claiming a change is tested.
  Provisions this checkout's venv, runs the suite on the per-worktree test database and reports real counts.
---

# Test verify

A "tests pass" claim is honest only when the database-backed tests ran. They skip without Postgres, and a large
skipped count is not a pass. This skill is the one home for the test procedure; the `builder` agent adds only
its own wait rules.

## Guardrails

- Skipped is not passed. `tests/conftest.py` (`_migrated`) skips every database-backed test when Postgres is
  unreachable; the summary line must show them executed.
- Never point `DATABASE_URL` at a real database to make tests run, and never run a CLI, script or REPL with it
  unset: `db/session.py` defaults to prod (`localhost:5544/alphadeck`). Only pytest is safe. The
  `pytest_configure` hook in `tests/conftest.py` swaps in `alphadeck_test_<hash>` per worktree, and
  `db/testdb.py` raises on any name that does not start with `alphadeck_test`. Work with it, never around it.
- `ALPHADECK_TEST_DB` pins one name for a serial run only, never with `-n`: a pin skips the per-worker suffix, so
  every worker shares one database and wipes the others' rows. CI leaves it unset.
- Each checkout has its own venv. Never borrow a sibling's or the main checkout's: its editable install points at
  the other tree, and the sibling can be pruned mid-run.
- Never two pytest runs at once in one worktree: they derive the same databases and collide.
- An idempotency test counts the table, not the read. The as-of read dedups, so a duplicate append hides behind a
  correct read; assert `count(*)` or the `list_*` length before and after a re-run.

## Steps

1. From the checkout root run `bash scripts/ensure-venv.sh` (PowerShell: `.\scripts\ensure-venv.ps1`; or
   `make venv`). It is idempotent, creates a missing venv and installs `-e ".[dev,replay,mcp]"`, what CI installs:
   pytest-xdist, pytest-timeout, the pinned linters, and duckdb and pyarrow so the replay tests run. It ends
   `OK:` or `FAIL:`. `unrecognized arguments: -n` means a lean venv: run the helper, never drop `-n`.
2. Postgres must answer on host port 5544; the test databases sit beside the real one. If nothing answers, the
   operator starts a stack or `docker compose -f infra/docker-compose.yml up -d` (prod's database: same project
   and volume).
3. Full suite, from `backend/`: `.venv/Scripts/python.exe -m pytest -n 6`. CI runs `-n 4`.
4. Between full runs, scope it: `pytest tests/<area> -n 6 -m "not slow"`. `slow` marks the replay, backtest and
   editions sweeps. When the change touches the point-in-time, replay or signal path (`db/bitemporal.py`,
   `db/public_clock.py`, `signals/`, `calls/`, `replay/`, `backtest/`, `editions/`, `pipeline/core.py`), run the
   full suite including `slow` on every iteration: the sweeps are the only day-by-day, zero-forward-knowledge
   runs of the real pipeline (the fast parity and lookahead guards in `tests/replay` run either way). Run the
   full suite once before the PR in every case.
5. Waiting. The orchestrator runs the full suite in the background with output redirected to a file outside the
   repo, then waits for the completion notice; it never polls in a loop. A dispatched `builder` runs it in the
   foreground with an explicit 600000 ms timeout instead, because nothing can wake it.
6. pytest-timeout is the only hang guard (`timeout` and `timeout_method = "thread"` in `backend/pyproject.toml`;
   the `slow` tests override it). A `+++ Timeout +++` stack dump is a hang: report it verbatim.
7. Before a re-run after a killed or piped run, check `tasklist | findstr python`. An orphaned `python.exe` keeps
   writing to the database.
8. Read the summary line, not the exit code alone: passed, failed, skipped.
9. Lint, from `backend/`: `ruff check .` then `black --check .`.

## Checks

- The report quotes the summary line verbatim ("N passed, M skipped") with the wall time, or the failures verbatim.
- The database-backed and replay test files executed rather than skipped.
- `ruff check .` and `black --check .` exit 0.

## Stop and ask when

- Postgres is down, or the database tests skip and you cannot make them run.
- The name guard raises `refusing to run the DB suite against ...`.
- Stale test databases pile up: dropping them is the operator's (the `prune-stale` skill;
  `python -m db.drop_test_dbs --dry-run` lists them).

## Model

[OPERATIONS], "The stacks".

[OPERATIONS]: ../../../docs/OPERATIONS.md
