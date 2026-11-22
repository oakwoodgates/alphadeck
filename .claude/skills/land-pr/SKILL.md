---
name: land-pr
description: >-
  Use when asked to merge or land a PR or a set of PRs into main and prove main has the commits. A multi-PR effort
  lands through an integration branch; a single PR merges directly; merge commits only.
---

# Land a PR

A multi-PR effort lands through an integration branch: sub-PRs target it, they land into it in one sitting, and
one PR carries it into main with one CI run. A single PR merges straight into main. Both end with a containment
check, because GitHub's Merged badge is not proof.

## Guardrails

- Merge commits only: `gh pr merge --merge`, `git merge --no-ff`. Never `--squash` or `--rebase` unless the
  operator asks this time.
- The Merged badge can lie. A PR merged into an already merged base shows Merged while main never received its
  commits. The containment check in Verify is mandatory.
- Never merge in the main checkout. Landing runs through `gh` against the remote, or in a dedicated worktree on
  the integration branch.
- Red CI, a conflict you cannot resolve mechanically, or any failed precondition: stop and report. No force
  push, no admin override. A missing commit is re-landed by a new PR, never by rewriting pushed history.
- A merge commit takes git's default message (`--no-edit`). Any other commit message goes through a quoted
  heredoc (`git commit -F - <<'MSG' ... MSG`).

## Steps

**One PR**

1. `gh pr checks <n>` and `gh pr view <n> --json mergeable,baseRefName,headRefOid`; note the head sha. Wait for
   CI with `gh pr checks <n> --watch` in the background: a foreground call outlasts the Bash timeout.
2. Contract: a diff that touches anything FastAPI emits into the schema (a route docstring counts) must carry
   the regenerated pair; if it does not, run the `contract-sync` skill on the branch first.
3. `gh pr merge <n> --merge --delete-branch`, then Verify.

**An effort of several PRs (the default for more than one)**

1. Cut `<effort>` from `origin/main` (`git fetch origin main`, then
   `git push origin origin/main:refs/heads/<effort>`). Open one draft PR `<effort>` to main as soon as the branch
   differs from main (GitHub refuses an empty one). CI runs on pull requests and on pushes to main only
   (`.github/workflows/ci.yml`), so the open PR is what gives the branch CI.
2. Builders branch from `origin/<effort>` and open their PRs with `--base <effort>`.
3. Land them in one sitting from a worktree on `<effort>`
   (`git worktree add .claude/worktrees/land-<effort> -b <effort> origin/<effort>`): `git fetch origin`, note each
   sub-PR's head sha (`gh pr view <n> --json headRefOid`), then `git merge --no-ff --no-edit origin/<branch>` for
   each sub-branch. Resolve conflicts. After the last merge,
   regenerate generated files once: the contract pair (`contract-sync`). For a docs effort, run
   `python scripts/check_docs.py` too: it has no baseline, so every fail finding must be cleared. Run the local
   checks once, then push once.
4. Wait for the one CI run on the integration PR (`gh pr checks <n> --watch`, in the background).
5. `gh pr ready <n>` if it is still a draft, then `gh pr merge <n> --merge`. Delete the integration branch only
   when the effort is done; a phased effort keeps it and merges `origin/main` into it before the next phase.

Why: a serial merge into main moves main per PR and costs a CI wait each; one integration PR costs one CI run.

**Existing stacks.** Merge top of stack first, or delete each base branch on merge so GitHub retargets the
children. Never merge a child into an already merged base.

**Verify, on every route.** For each recorded head sha, and for the integration branch head:

```
git fetch origin main
git merge-base --is-ancestor <head-sha> origin/main && echo IN-MAIN || echo MISSING-FROM-MAIN
```

Sub-PRs landed into the integration branch are checked against `origin/<effort>` after the push, then against
`origin/main` after the final merge. `MISSING-FROM-MAIN` on a Merged PR: stop and report; the fix is a new PR
that re-lands the commits.

## Checks

- Every recorded sha prints `IN-MAIN`; `gh pr view <n> --json state,baseRefName` shows each PR merged.
- The main checkout is untouched and `git status` is clean in the landing worktree.
- The report names what landed (PR numbers, merge sha), says the change is dormant on prod until its images are
  rebuilt, and offers the `deploy` skill.

## Stop and ask when

- CI is red, a PR is not mergeable, or a conflict is in anything but a generated file.
- Landing would need `--squash`, `--rebase`, `--admin` or a force push.
- Containment prints `MISSING-FROM-MAIN`.

## Model

[OPERATIONS], "What a deploy changes".

[OPERATIONS]: ../../../docs/OPERATIONS.md
