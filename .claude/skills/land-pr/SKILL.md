---
name: land-pr
description: >-
  Merge a PR (or a stack) into main and VERIFY main actually received the commits —
  merge-commit, never squash; the "Merged" badge on a stacked PR can lie. Trigger on
  "merge it", "land the PR", "land the stack", "merge and verify". Ends by offering
  the deploy skill — a merged change is dormant on prod until images rebuild.
---

# Land a PR — merge, then prove main has it

Authoritative rules: `CLAUDE.md` §Conventions (the stacked-PR trap + the contract rule).
STOP and report on any failed precondition (red CI, conflicts); do NOT guess or force.

## Guardrails (never violate)
- **Merge-commit, never squash** (`gh pr merge <n> --merge`) — the operator's standing
  preference. Never `--squash`/`--rebase` unless explicitly asked this time.
- **Don't trust the "Merged" badge on a stack.** A stacked PR merged into an
  already-merged base shows "Merged" while main never received its commits (the
  #135 trap, re-landed as #136). The verify step below is mandatory, not optional.
- **The main CHECKOUT stays clean.** Landing happens via `gh` against the remote;
  never merge locally in the main checkout.

## Steps
1. **Preconditions per PR:** CI green + mergeable (`gh pr checks <n>`,
   `gh pr view <n> --json mergeable,baseRefName,headRefOid`). Red CI → stop, report.
   CI takes ~6 min (the backend pytest job dominates): wait with
   `gh pr checks <n> --watch` run in the BACKGROUND (or re-check on the completion
   notification) — never in a foreground call, which times out under the Bash cap.
2. **Contract check (FastAPI-touching PRs):** if the diff touches anything FastAPI
   emits into the schema (a route docstring counts — #61), the PR must already carry
   the regenerated `backend/openapi.json` + `frontend/src/api/types.gen.ts`. Missing →
   run the `contract-sync` skill on the branch first.
3. **Order (stacks only):** merge **top-of-stack first**, or delete each base branch
   on merge so GitHub retargets the children. Never merge a child into a base branch
   that is already merged.
4. **Merge:**
   ```
   gh pr merge <n> --merge --delete-branch
   ```
5. **Verify main received the commits** (per PR, using the head sha from step 1):
   ```
   git fetch origin main
   git merge-base --is-ancestor <head-sha> origin/main && echo IN-MAIN || echo MISSING-FROM-MAIN
   ```
   `MISSING-FROM-MAIN` on a "Merged" PR = the #135 trap fired — stop and report;
   the fix is a new PR re-landing the commits (the #136 shape), not force-pushes.
6. **Report + hand off:** state what landed (PR #, merge sha). Remind the operator the
   change is **dormant on prod until the image rebuilds** — offer the `deploy` skill.
