---
name: contract-sync
description: >-
  Regenerate the OpenAPI contract pair (backend/openapi.json + frontend
  types.gen.ts) after ANY change to what FastAPI emits into the schema — a route
  docstring alone is enough to drift it (#61) and fail CI's diff-guard. Trigger on
  "regen the contract", "sync the api contract", a diff-guard CI failure, or as a
  step inside any FastAPI-touching change. Same-PR rule: regenerated files ship
  WITH the change that drifted them.
---

# Contract sync — regenerate the generated pair

Authoritative rule: `CLAUDE.md` §Conventions ("The OpenAPI contract is generated —
regenerate it in the SAME PR as anything FastAPI emits into the schema").

## Guardrails (never violate)
- **Never hand-edit** `backend/openapi.json` or `frontend/src/api/types.gen.ts` —
  they are generated artifacts; a hand edit is drift wearing a fix.
- **Same PR.** The regenerated pair lands in the SAME PR as the FastAPI change, or
  CI's diff-guard fails. A docstring rewrite alone was enough to trip it (#61).
- **Stacked PRs regenerate against the UNION.** On a stack, regenerate on a branch
  containing ALL schema-touching commits — regenerating per-slice against a partial
  schema re-introduces the ID-misbind trap.

## Steps
1. **Export the schema** (from `backend\`, venv active — or `$env:PYTHONPATH="backend"`
   from the repo root):
   ```
   python -m app.openapi_export
   ```
2. **Regenerate the TS types** (from `frontend\`):
   ```
   npm run gen:api
   ```
3. **Check what moved:** `git status` / `git diff --stat` on the two files. No diff is
   a valid outcome (the change didn't drift the schema) — say so and stop.
4. **Commit the pair with the driving change** (same PR). If the driving change is
   already pushed, this is a follow-up commit on the same branch, never a separate PR.
