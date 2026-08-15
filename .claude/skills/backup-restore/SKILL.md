---
name: backup-restore
description: >-
  Snapshot the prod DB (labeled dumps are prune-exempt recovery points) or restore
  one — restore is destructive, CLI-only, and needs the operator's explicit go.
  Trigger on "back up prod", "snapshot the DB", "restore the demo", "restore from
  backup". The bitemporal trap: recover lost history by RESTORING a snapshot,
  never by re-ingesting — re-ingest stamps recorded_at = today.
---

# Backup / restore — the DB safety net

Authoritative runbook: `docs/FEED_LOOP.md` §Backups & restore (+ the operator view
in `docs/ADMIN.md`). The runner is `pipeline/backup.py` — a read-only `pg_dump`
subprocess, no app connection.

## Guardrails (never violate)
- **Restore is destructive (drop-schema + reload) and HUMAN-GATED.** Never run it
  without the operator's explicit go in this conversation; never automate it or
  build a button for it.
- **The bitemporal trap:** to recover lost history, RESTORE a snapshot. Never
  re-ingest — re-ingest stamps `recorded_at` = today, so the past becomes invisible
  to as-of reads pinned earlier (the demo-rebuild lesson).
- **Label deliberate recovery points.** Retention keeps only the newest 7 UNLABELED
  dumps; a labeled dump (`--label pre-<thing>`) is never auto-pruned.

## Snapshot (safe, read-only)
1. Check what already exists first — the cron takes a nightly weekday snapshot:
   `GET /admin/status` carries the last-snapshot age; `GET /admin/backups` lists all
   (or `ls data/backups` at the main-checkout root).
2. Create, labeled with the reason:
   ```
   docker compose exec backend python -m pipeline.backup --label pre-<why>
   ```
3. Verify the file landed: `ls data/backups` → `alphadeck-<UTC>-pre-<why>.sql`,
   non-trivial size.

## Restore (destructive — operator go required)
1. **Confirm with the operator:** which snapshot, into which stack (restoring into
   DEV or a fork is the low-risk default; prod only on explicit instruction).
2. State what will be lost: everything recorded after the snapshot's timestamp.
3. Run the exact documented sequence (this is the 2026-07-21 recovery form):
   ```
   docker exec -i alphadeck-postgres-1 psql -U alphadeck -d alphadeck < ./data/backups/<file>
   ```
   (For dev/fork, substitute that stack's postgres container + DB name.)
4. Verify: `/health` ok, the record edge (`GET /admin/status`) matches the
   snapshot's date, and a spot-checked thesis renders.
