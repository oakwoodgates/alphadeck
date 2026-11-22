---
name: fork
description: >-
  Use when asked to create, start, stop, refresh or destroy a fork: an isolated experiment
  stack on its own worktree, ports and database, seeded one way from prod. Runs `scripts/fork.sh`,
  clears copied webhooks and rehearses extraction from the cache.
---

# Fork

A fork is a full stack (frontend, backend, Postgres; no cron) in `.claude/worktrees/fork-<name>` on branch
`fork/<name>`, identified by its `.env.fork`. Slot N gives app 808N, API 800N and Postgres 554(4+N); prod, dev and
sig hold slots 0 to 2, so forks use 3 to 9.

## Guardrails

- `init` runs from the main checkout; `up`, `down`, `refresh` and `destroy` run from the fork's worktree.
- The scripts refuse an empty name, `prod`, `dev`, `sig`, `test` and anything outside `[a-z0-9_]` before any Docker
  call. Do not edit `.env.fork` to get around that.
- The fork has no SEC User-Agent by design: `/health` reads degraded and live pulls are skipped. Setting one is the
  operator's decision.
- A restored fork holds every prod user's Slack webhook. Clear them right after `init` and after every
  `refresh --reset` (step 2), and keep `SLACK_WEBHOOK_URL` out of `.env.fork`.
- `refresh --reset` drops the fork's database: its own cards, decisions and calls are gone.

## Steps

With `F="-f docker-compose.yml -f docker-compose.fork.yml -p alphadeck_<name> --env-file .env.fork"`:

1. Create, from the main checkout: `scripts/fork.sh init <name> --slot <N>` (pick a free slot; `--branch` or
   `--from` to start elsewhere). It makes the worktree, scaffolds `.env.fork`, starts the stack and seeds it.
2. Clear the copied webhooks, from the fork worktree:
   `docker compose $F exec postgres psql -U alphadeck -d alphadeck_<name> -c "update user_meta set
   slack_webhook_url = null where slack_webhook_url is not null"`.
3. Start and stop: `scripts/fork.sh up`, `scripts/fork.sh down` (keeps the volumes).
4. Re-seed from prod: `scripts/fork.sh refresh --reset`, then step 2 again.
5. Destroy: `scripts/fork.sh destroy --yes` (`down -v`); it prints the worktree and branch removal to run from the
   main checkout.

A new name: the fork only has what prod fetched. Add the name to a prod card (a quiet watchlist card serves), let
the nightly fetch it, then `refresh --reset`.

**Rehearsing background extraction from the cache.** A live run fails every company here (no User-Agent), so run
it cache-only in the fork's backend, from the fork worktree:

1. `docker compose $F exec backend python -m pipeline.extract_values --no-live`: who would extract, who fails.
2. `docker compose $F exec backend python -m pipeline.extract_values`: prod's live request count from the copied
   cache (anything filed after the copy is missing).
3. `docker compose $F exec backend python -m pipeline.extract_values --apply --no-live --max-names 50`: a first
   slice.
4. `docker compose $F exec backend python -m pipeline.extract_values --apply --no-live`: the rest; it resumes.

The rehearsal records attempts; one `done` attempt opens the backfill gate on the fork. That is harmless because
the fork runs no cron, and the next `refresh --reset` restores prod's own attempt rows.

**The sig stack** works the same way from its own worktree, with `G="-f docker-compose.yml -f
docker-compose.sig.yml -p alphadeck_sig --env-file .env.sig"` and `.env.sig` copied from `.env.sig.example`:
`docker compose $G up -d --build`, then `bash scripts/refresh-sig.sh` (copies the cache unless `--no-cache`), then
clear the webhooks in `alphadeck_sig` as in step 2. Teardown is `docker compose $G down -v`.

## Checks

- `curl http://localhost:800<N>/health` answers, `degraded` for the missing User-Agent.
- `docker compose $F ps` lists backend, frontend and postgres, and no cron.
- After step 2, `select count(*) from user_meta where slack_webhook_url is not null` is 0.
- After a rehearsal: the run summary and `data/extraction_runs/` (per-company time is `seconds / attempted`, which
  sizes the nightly cap and budget); `platform_extraction_attempt` grouped by `status` and `error` (how many are
  not cached); the steward's queue on the fork's Admin page.

## Stop and ask when

- The fork holds work the operator may want, and the request is a refresh or a destroy.
- No slot is free, or a slot's ports are taken.
- Anything would set a User-Agent, a webhook or `SLACK_WEBHOOK_URL` on the fork.

## Model

[OPERATIONS], "Forks" and "One-way refresh"; extraction: [PLATFORM_EXTRACTION].

[OPERATIONS]: ../../../docs/OPERATIONS.md
[PLATFORM_EXTRACTION]: ../../../docs/PLATFORM_EXTRACTION.md
