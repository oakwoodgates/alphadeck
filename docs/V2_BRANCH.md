# The `v2` branch

`v2` is the long-lived branch for the V2 rebuild: platform data (share counts, cash/burn, company catalysts,
filings) becomes shared and steward-approved, theses stay private to a user's workspace, and one cutover (with a
reset) ends it. It is NOT `main`. Every V2 change lands on `v2` through a pull request into `v2`; `main` keeps
shipping V1 until the cutover merges `v2` into it.

This page is the routine for keeping `v2` current with `main`, and the rules a `main` change follows when it
touches what V2 changes.

## Why a pull request, every time

CI runs on every pull request, but on pushes to `main` only (`.github/workflows/ci.yml`). A merge of `main` pushed
straight to `v2` is never tested. So nothing is pushed straight to `v2`: not a feature, not a merge of `main`.

## The merge-main routine

From the `v2` worktree (the one whose branch is `v2`, or a fresh worktree off it):

1. `git fetch`
2. Branch off `v2`: `git switch -c merge-main-<YYYY-MM-DD> v2` (start from an up-to-date `v2`:
   `git merge --ff-only origin/v2` first if your local `v2` is behind).
3. `git merge --no-ff origin/main`
4. Resolve conflicts with these rules:
   - **`backend/domain/scopes.py`: keep V2's map.** Then classify every table `main` added: a table with a
     `tenant_id` goes in `SCOPE_OF` (with its V2 scope), a table without one goes in `UNSCOPED_TABLES`
     (`"platform"` or `"user-keyed"`). If you forget, the cutover dry run fails closed (exit 2, the table is
     listed under `UNMAPPED TABLES`) and `tests/db/test_scopes.py::test_every_table_has_one_owner` fails.
     If the new table needs something other than `keep` at cutover, add it to `CUTOVER_ACTION` in
     `backend/pipeline/v2_cutover.py`.
   - **Migrations:** a migration numbered below `0200` that comes from `main` is kept exactly as it is. Never
     renumber a migration, on either branch: `schema_migrations` records file names, so a renamed file runs twice.
   - **Generated contract files:** do not hand-merge `backend/openapi.json` or `frontend/src/api/types.gen.ts`.
     Take either side, then regenerate both from the merged code: from `backend/`,
     `python -m app.openapi_export`; from `frontend/`, `npm run gen:api`.
5. Test, from `backend/`:
   - `pytest tests/db/test_scopes.py tests/pipeline/test_v2_cutover_plan.py -n 6`
   - the scoped suites of every area the merge touched (`pytest tests/<area> -n 6 -m "not slow"`), and the
     frontend tests if `frontend/` changed.
   - `ruff check .` and `black --check .`
6. Push the branch and open a pull request into `v2` (not `main`) so CI runs on the merge. Merge it once CI is
   green.
7. After it merges, rebuild the V2 fork's image and re-seed it, so the fork runs the merged code on a fresh copy
   of prod: `bash scripts/fork.sh up`, then the re-seed (`scripts/refresh-fork.sh`; see `docs/FORKS.md`).

## A `main` change that touches what V2 changes

A pull request into `main` that touches any of these carries a short **"How this lands on v2"** note in its body:

- `backend/domain/scopes.py` (the scope map, or a new table);
- the ratify routes and CLIs (the Workbench ratify and auto-confirm, `pipeline.ratify_*`);
- the four scoring-fact tables: `fact_shares_outstanding`, `fact_cash_burn`, `fact_catalyst`, `fact_revenue_mix`;
- the thesis `catalyst` table.

The note says, in a line or two, what the next merge-main has to do on `v2` (for example: "new table `x` has a
`tenant_id`; on v2 it is DATA").

## The cutover dry run

`python -m pipeline.v2_cutover plan [--json PATH]` reads a database and prints, for every table, its owner (from
`domain/scopes.py`), what the cutover does with it (`keep` unless `CUTOVER_ACTION` says `move→data`,
`move→founding`, `convert (W6/W10)`, `reset` or `prune`), its row count, and its rows per tenant and per
`ratified_by`. Then it lists every surprise (a row where no plan expects it). It is read-only: a read-only
connection, rolled back, nothing written to the database (`--json` writes only the file you name).

Exit codes: `0` clean, `1` surprises, `2` a table the registry does not name or a named table the database lacks
(migrate first).

Run it only where you are allowed to read: the test database the suite derives, or a fork. Never point it at a
database you were not asked to inspect.
