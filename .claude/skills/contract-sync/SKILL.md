---
name: contract-sync
description: >-
  Use when a change touches anything FastAPI emits into the schema (a route, a model, a status code or a route
  docstring), when CI's contract diff-guard fails, or when asked to regenerate the API contract.
---

# Contract sync

`backend/openapi.json` and `frontend/src/api/types.gen.ts` are generated from the FastAPI app. CI regenerates both
and fails on any diff (`.github/workflows/ci.yml`: the backend job diffs `openapi.json`, the frontend job diffs
`types.gen.ts`). A route docstring becomes the operation description, so a docstring edit alone drifts the pair.

## Guardrails

- Never hand-edit either file: a hand edit is drift. Fix the source (the docstring, model or route) and regenerate.
- Same PR. The regenerated pair ships with the change that drifted it; when that change is already pushed, add a
  follow-up commit on the same branch, never a separate PR.
- Docstrings that reach the contract (routes and `app/schemas_api.py`) cite no doc paths: they ship to every
  client.
- Regenerate against the union. On a stack or an integration branch, regenerate once on a branch that holds every
  schema-touching commit; a pair generated per slice differs from the merged result and fails the diff-guard.
- After a rebase or a merge conflict in the pair, regenerate; never hand-merge.

## Steps

1. From `backend/`, with the venv from `scripts/ensure-venv.sh`:
   `.venv/Scripts/python.exe -m app.openapi_export`. It writes `openapi.json` with LF line endings.
2. From `frontend/`: `npm run gen:api`. Run `npm ci` first when `node_modules` is missing.
3. `git diff --stat -- backend/openapi.json frontend/src/api/types.gen.ts`. No diff is a valid outcome: the change
   did not drift the schema. Say so and stop.
4. Commit both files with the change that drove them.

## Checks

- Running steps 1 and 2 again leaves no new diff.
- `types.gen.ts` moved with `openapi.json` (`gen:api` reads that file alone); a lone move in either is suspect.
- `npm run build` (it typechecks) passes when `types.gen.ts` changed.

## Stop and ask when

- The export raises on import. Fix the import; never write the schema by hand.
- The diff holds routes or fields the change did not touch: another branch's drift or a stale venv. Find the
  source before committing.
- `types.gen.ts` drifts with no `openapi.json` change (a generator version change).

## Model

[DATA_FLOW], "Where files live".

[DATA_FLOW]: ../../../docs/DATA_FLOW.md
