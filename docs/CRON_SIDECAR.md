# Cron sidecar: schedules, catch-up and backups

`backend/scripts/daily_cron.sh` is the scheduler: a POSIX shell loop in the compose `cron` service that fires
the nightly pass ([FEED_LOOP](FEED_LOOP.md)) after the close on every market session, runs the jobs that follow
it, and runs three background schedules. It is the machine's; the operator changes it through compose settings
and a cron deploy. The full-stack `docker compose up` starts it; the DB-only dev loop
(`infra/docker-compose.yml`), the dev override and the test suite never do. Read this doc before you change the
schedule, the wake order, the catch-up rules or backup retention. The script's own comments carry the detailed
reasons.

## Code

| Piece | Module |
|---|---|
| The loop, the boot block, the schedules | `backend/scripts/daily_cron.sh` |
| The session calendar the shell asks | `pipeline/schedule.py` (`is-session`), `domain/trading_days.py` |
| The crash page, sent with no database | `notify/page.py` |
| Snapshots and retention | `pipeline/backup.py`; the Admin button's job: `pipeline/backup_job.py` |
| The service, its env and its bind mounts | `docker-compose.yml` (`cron`) |

## How it works

### Schedules

| Schedule | Fires | Command | Off switch |
|---|---|---|---|
| Nightly | `RUN_AT` in the market TZ on each session | `pipeline.daily --run-kind cron --late-ok --asof <target>`, then the wake order below | None; scale the service to zero |
| Onboarding | `ONBOARD_EVERY_S` after the previous run ends | `pipeline.onboarding --run-kind cron` ([INGEST](INGEST.md), cold names) | `ONBOARD_EVERY_S=0` |
| Company-list refresh | Weekly, `MASTER_REFRESH_DOW` at `MASTER_REFRESH_AT` | `pipeline.populate_master --live --apply --run-kind cron` ([DATA_SOURCES](DATA_SOURCES.md), the company list) | `MASTER_REFRESH=0` |
| Term mentions | Weekly, `TERM_MENTIONS_DOW` at `TERM_MENTIONS_AT`, after the company-list refresh | `pipeline.term_mentions --weekly --apply --run-kind cron` (the module docstring; [narrative-adoption](dashboards/narrative-adoption.md)) | `TERM_MENTIONS=0` |

The three background schedules start after the boot block, so a boot catch-up never shares the box with them,
and the nightly loop never waits for them. Each job refuses inside the nightly window and yields to a running
pass on its own; the loop only fires it, bounds it with `timeout` and logs its exit. A weekly run refused with
exit 75 is retried hourly a few times, then waits for the next week. The term-mentions count is a no-op until
someone runs its first backfill by hand.

### The nightly wake order

On a session, or a wake whose catch-up covered a session:

1. Wait for the database, then the scheduled pass (`run_pass`, bounded by `RUN_TIMEOUT_S`), with the heartbeat
   start ping before it. It always fires, even when someone ran that night by hand, and re-versions.
2. On a non-zero exit, one retry `RETRY_DELAY_S` later as `--catch-up`; if it fails too, the shell pages.
3. Catch-ups for every session strictly after the target up to the last expected night when the run finished.
4. One database snapshot (`pipeline.backup`), unconditional.
5. The editions nightly (`editions.run nightly --apply`, bounded by `EDITIONS_TIMEOUT_S`; owner:
   [EDITIONS](EDITIONS.md)).
6. The sign-in housekeeping prune (`db.housekeeping --prune`; [AUTH](AUTH.md), "Housekeeping").
7. The dashboards' change-log prune (`db.change_stamp --prune`).
8. The dashboard cache warm-up (`pipeline.warm_dashboards --night <last expected night>`; owner:
   [DASHBOARDS](DASHBOARDS.md) rule 7).

Steps 4 to 8 are fail-open: a failure is logged (a failed backup also pages) and the loop goes on. They sit in
this scheduling layer and never inside `run_daily_pass`, so "Run daily now" from Admin never dumps, cuts pages
or warms caches, and nothing about them can cost the call of record. The warm-up is last so the cache it fills
is the one the morning visit reads.

### Getting the right night

- **Sessions come from Python.** `is_session` asks `python -m pipeline.schedule is-session D`, the one calendar
  the call path and Admin use. If that check fails for any other reason it logs one loud line and falls back to
  Monday to Friday, so a broken check can never stop the nightly; at worst it fires on a holiday.
- **The target is fixed at schedule time.** The as-of is computed before the wait and passed as `--asof`; it is
  never re-read after waking, so a late wake still records the intended night. `--late-ok` lets the pass accept
  a target that a long suspend made late.
- **Waits are sliced.** `wait_until` sleeps at most `SLICE_S` at a time and re-reads the wall clock. Why: on
  Docker Desktop with WSL2 the VM's monotonic clock stops while the host sleeps, so one long `sleep` overshoots by
  the whole suspend.
- **Three catch-ups, each a `--catch-up` pass** (a no-op when the night already has a live pass;
  [FEED_LOOP](FEED_LOOP.md), "Missed and late nights"):
  - at boot, one pass for the last expected night, so a host that was off at `RUN_AT` records it when it comes
    back;
  - after the boot block, the nights that became expected while it ran;
  - after each scheduled run, the sessions a long sleep skipped, walking forward from the target.

  None of them reaches an older night: a deploy never silently backfills history. Older holes are the
  operator's, with `pipeline.backfill`.
- The shell's `date` runs in the container TZ, which compose sets from `ALPHADECK_MARKET_TZ`, the same zone
  `market_today()` reads. Keep the two in step.

### Failure handling

| Failure | What the shell does |
|---|---|
| Database not up at boot or at `RUN_AT` | Polls a cheap connect for up to `DB_WAIT_S`, then fires anyway |
| Pass exits non-zero (the night has no record) | One retry after `RETRY_DELAY_S`, as `--catch-up` |
| Pass hangs | `timeout` stops it at `RUN_TIMEOUT_S` (exit 124; 137 for a kill), which flows into the same retry |
| Retry, boot catch-up retry, or a catch-up night fails; a backup fails | `python -m notify.page`: no database, never raises, sends through the env notifier, names a date and an exit code, never a thesis |
| The whole box is down | Only the external heartbeat can tell |
| A background job or post-pass step fails | One log line; the loop goes on |

The heartbeat is the shell's, never the app's. After a pass exits 0 for the night that is the last expected
night at that moment, the shell sends one GET to `HEARTBEAT_URL`; a pass that landed an older night holds the
ping until the newest night lands. It pings `HEARTBEAT_URL/start` before the scheduled run, and
`HEARTBEAT_BACKUP_URL` after each successful backup. Both are optional; unset is silent. A ping URL is a
credential: the shell never logs it and its boot line says only on or off. The external service's schedule
lives outside the repo.

The image sets `PYTHONUNBUFFERED=1` and compose caps the service's log, so output lands as it happens.

### Backups

- **Nightly:** one unconditional `pipeline.backup` per wake that ran a night, after the pass and its catch-ups.
  Unconditional because its job is to capture the run that just finished, which no earlier dump holds.
- **At boot:** `pipeline.backup --if-older-than-h BACKUP_MAX_AGE_H`, after the boot catch-up. It dumps only when
  the newest unlabeled snapshot is that old, so a crash-looping container dumps about once a day.
- **Format:** a plain-SQL `pg_dump --no-owner` with no DROP statements, written atomically to `data/backups`
  (`/data/backups` in the container; the same host directory is bound on `backend` and `cron`, so it can be
  copied off the box). It replays only into an empty database. The password reaches `pg_dump` through its
  environment only, and every backup error message is redacted.
- **Retention** (after a successful dump only; a failed dump never prunes): the newest `backup_keep` unlabeled
  snapshots stay. A labeled snapshot is deleted only when it is both older than `backup_labeled_max_age_days`
  and outside the newest `backup_labeled_keep` labeled ones. `python -m pipeline.backup --prune-dry-run` lists
  what the next prune would delete and removes nothing.
- **Restore** is CLI-only, destructive and human-gated: the `backup-restore` skill.

## Contracts

- Every invocation passes `--run-kind cron`, and only this script may: the CLI defaults to `manual`, and a
  flag is the one thing that tells the nightly record from a hand run of the same command. Never turn it into
  an env var, or a `docker exec` into this container would stamp `cron` falsely.
- The shell reads only the exit code of `pipeline.daily`: 0 means the night has a record, anything else is a
  miss worth one retry ([FEED_LOOP](FEED_LOOP.md), "CLI and exit codes"). `timeout` adds 124 for a stopped
  pass and 137 for a killed one.
- A heartbeat ping means "the last expected night is recorded", never "a pass ran".
- The background jobs own their rules (the window, the yield, their locks); the loop only fires them.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| The sidecar's schedules | Machine | The compose `cron` service |
| Changing a schedule or a setting | The operator | Compose env and a cron deploy (`scripts/deploy.sh`) |
| "Back up now": `POST /admin/backup`, `GET /admin/backup/jobs/{job_id}`; `GET /admin/backups` | Steward | `require_super_admin` on the admin router (`app/main.py`); `tests/app/test_auth_gate.py` |
| `python -m pipeline.backup` (`--label`, `--prune-dry-run`, `--if-older-than-h`) | The operator | Shell access |
| Restore | The operator, by hand | No route exists |

## Settings

Script defaults are in `daily_cron.sh`; the compose names are in `docker-compose.yml` under `cron`.

| Script variable | Compose variable | Passed by compose? |
|---|---|---|
| `RUN_AT`, `TZ` | `ALPHADECK_CRON_AT`, `ALPHADECK_MARKET_TZ` | Yes |
| `RETRY_DELAY_S`, `DB_WAIT_S`, `RUN_TIMEOUT_S` | `ALPHADECK_CRON_RETRY_DELAY_S`, `ALPHADECK_CRON_DB_WAIT_S`, `ALPHADECK_CRON_RUN_TIMEOUT_S` | Yes |
| `HEARTBEAT_URL`, `HEARTBEAT_BACKUP_URL` | Same names | Yes |
| `ONBOARD_EVERY_S`, `ONBOARD_TIMEOUT_S` | `ALPHADECK_ONBOARD_EVERY_S`, `ALPHADECK_ONBOARD_TIMEOUT_S` | Yes |
| `EDITIONS_NIGHTLY`, `EDITIONS_TIMEOUT_S` | `ALPHADECK_EDITIONS_NIGHTLY`, `ALPHADECK_EDITIONS_TIMEOUT_S` | Yes |
| `ALPHADECK_WARM_DASHBOARDS`, `WARM_DASHBOARDS_TIMEOUT_S` | `ALPHADECK_WARM_DASHBOARDS`, `ALPHADECK_WARM_DASHBOARDS_TIMEOUT_S` | Yes |
| `MASTER_REFRESH`, `_DOW`, `_AT`, `_TIMEOUT_S`; the same four for `TERM_MENTIONS` | The `ALPHADECK_`-prefixed names | Yes |
| `SLICE_S`, `DB_POLL_S`, `BACKUP_MAX_AGE_H`, the two weekly jobs' `_RETRY_S` and `_RETRIES` | None | No (script defaults) |
| `backup_keep`, `backup_labeled_keep`, `backup_labeled_max_age_days` | `ALPHADECK_BACKUP_KEEP`, `ALPHADECK_BACKUP_LABELED_*` | No (`domain/settings.py`) |

The editions nightly is on in prod.

## Known limits

- One retry per night. A night whose retry also fails pages and stays a hole until someone acts.
- A multi-night outage catches up only the last expected night at boot; older holes need `pipeline.backfill`.
- An exit 75 (another pass held the lock) on the scheduled run logs "run FAILED" and takes the retry, which then
  no-ops or runs as a catch-up.
- The external heartbeat expects a ping on weekdays; on a market holiday no pass runs, so it alerts unless its
  schedule knows the holiday.
- A suspend that spans `RUN_AT` still fires late, within one slice of waking.

## Related

[FEED_LOOP](FEED_LOOP.md), [FEED_MONITORS](FEED_MONITORS.md), [INGEST](INGEST.md), [ADMIN](ADMIN.md),
[OPERATIONS](OPERATIONS.md). Restore: the `backup-restore` skill. Health checks: the `cron-health` skill.
