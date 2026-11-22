---
name: backup-restore
description: >-
  Use when asked to back up or snapshot the database, check snapshots, or restore one. Snapshots are safe;
  restore is destructive, CLI-only and needs the operator's explicit go. Recover lost history by restoring,
  never by re-ingesting.
---

# Backup and restore

## Guardrails
- **Restore is destructive and human-gated.** It replaces the whole database. Never run it without the
  operator's explicit go in this conversation; never automate it or build a button for it.
- **Recover history by restoring, never by re-ingesting.** A re-ingest stamps `recorded_at` with today, so
  the past stays invisible to reads pinned earlier.
- **Label a deliberate recovery point.** Retention keeps only the newest unlabeled snapshots, and a labeled one
  is pruned once it is both old and outside the newest labeled set (`docs/CRON_SIDECAR.md`, "Backups"). Copy a
  point you need longer off the box.
- Never restore while a nightly pass is running. Never print the ops token or the database password.
- A dump taken before the running images' newest migrations needs the matching older images: retag them and
  start them with `up -d --no-build` before any newer backend boots against the restored database, because the
  boot migrates it (`docs/OPERATIONS.md`, "Migrations and restore").
- A dump restored into dev or a fork carries every user's Slack webhook: clear them before anything there can
  notify (the `fork` skill).

## Steps

**Snapshot (safe; the dump only reads the database):**
1. Check what exists. `/admin/*` is steward-only, so send the prod ops token. From the main checkout, read it
   as one line, the way `scripts/deploy.sh` does, and never print it:
   `TOKEN=$(sed -n 's/^[[:space:]]*ALPHADECK_OPS_TOKEN=//p' .env | tail -n 1 | tr -d "\r\"'")`, then
   `curl -fsS -H "Authorization: Bearer $TOKEN" http://localhost:8000/admin/backups` for the list, or the
   same request to `/admin/status` for the last snapshot's age. Or `ls data/backups` at the main-checkout
   root. No token yet: the operator mints one once
   (`docker exec -it alphadeck-backend-1 python -m pipeline.users token create --email <admin> --name ops`).
   For dev or a fork, use that stack's API port and mint in its own backend.
2. Create one, labeled with the reason:
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml exec backend python -m pipeline.backup --label pre-<why>`
3. Before a change to retention, `python -m pipeline.backup --prune-dry-run` lists what the next prune would
   delete and deletes nothing. Show the operator the list.

**Restore (operator go required):**
1. Confirm with the operator which snapshot and which stack. Dev or a fork is the low-risk default; prod only
   on an explicit instruction.
2. State what is lost: everything recorded after the snapshot's timestamp.
3. The snapshot is a plain-SQL `pg_dump --no-owner` with no DROP statements, so it replays only into an empty
   database; a plain `psql < <file>` into a live database restores nothing. Run (the steps
   `scripts/refresh-dev.sh` runs for dev):
   ```
   docker compose -f docker-compose.yml -f docker-compose.prod.yml stop backend cron
   docker exec alphadeck-postgres-1 psql -U alphadeck -d postgres -v ON_ERROR_STOP=1 -c "DROP DATABASE IF EXISTS alphadeck WITH (FORCE)" -c "CREATE DATABASE alphadeck"
   docker exec -i alphadeck-postgres-1 psql -U alphadeck -d alphadeck -v ON_ERROR_STOP=1 -f - < ./data/backups/<file>
   docker compose -f docker-compose.yml -f docker-compose.prod.yml start backend cron
   ```
   For dev or a fork, substitute that stack's compose project, postgres container and database name. In
   PowerShell, pipe the file in with `Get-Content` instead of `<`.

## Checks
- After a snapshot: `ls data/backups` shows `alphadeck-<UTC>-pre-<why>.sql` at a plausible size.
- After a restore: `/health` answers ok (the backend applies any newer migration on start), the record edge on
  `/admin/status` matches the snapshot's date, and a spot-checked thesis renders.
- The snapshot holds the whole database, tokens included: a token minted after it was taken is gone, so a 401
  after a restore means mint a new one.

## Stop and ask when
- The operator has not said go for a restore, or the target stack is unclear.
- A nightly pass is running (`/admin/status` shows it), or the next `RUN_AT` is close.
- A dump fails, or `psql` stops on an error during the replay.

## Model
`docs/CRON_SIDECAR.md` ("Backups"): schedule, retention, format and password handling. The stacks and the
restore rule: `docs/OPERATIONS.md`.
