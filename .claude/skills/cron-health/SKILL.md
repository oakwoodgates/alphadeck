---
name: cron-health
description: >-
  Use when asked whether the nightly cron ran, whether the record or a feed is fresh, or why a thesis looks
  stale. Diagnosis is read-only; propose any re-run, repair or restore to the operator, never apply it.
---

# Cron health

## Guardrails
- **Read only.** A hand `pipeline.daily` writes the call of record; never run it as a probe. Propose the fix
  ("Run daily now" on Admin, a targeted re-ingest, `pipeline.backfill` for a missed night, a restore); the
  operator decides.
- **Never fix stale history by re-ingesting.** A re-ingest stamps today's `recorded_at`; it recovers going
  forward only (see `backup-restore`).
- **A zero-fetch line alone is not a freeze.** The verdict reads completed EDGAR fetches and the share of
  companies that got fresh SEC data. Corroborate with the run artifact and the record edge before declaring one.
- Never print the ops token.

## Steps
1. **The verdict.** `/admin/*` is steward-only, so send the prod ops token. From the main checkout, read it as
   one line, the way `scripts/deploy.sh` does:
   `TOKEN=$(sed -n 's/^[[:space:]]*ALPHADECK_OPS_TOKEN=//p' .env | tail -n 1 | tr -d "\r\"'")`, then
   `curl -fsS -H "Authorization: Bearer $TOKEN" http://localhost:8000/admin/status`. No token yet: the operator
   mints one once
   (`docker exec -it alphadeck-backend-1 python -m pipeline.users token create --email <admin> --name ops`).
   Read `cron.status`, `cron.alarms`, the record edge against the expected night, the last snapshot's age,
   `record.partial_nights`, `record.reconstructed_nights`, `unfinished_run`, `last_run.audit`,
   `last_run.edgar_fetches` and `edgar_fetches_completed`, `last_run.name_errors_new` and `split_defects`. What
   each verdict means: `docs/ADMIN.md`.
2. **The self-audit**, six checks (`DEFAULT_CHECKS` in `pipeline/self_audit.py`): `on_time_rows`,
   `code_sha_known`, `every_thesis_finished`, `split_defects_flagged`, `no_unsettled_bars` (a failure names
   `TICKER d @ recorded`: find the writer that bypassed `ingest/prices/settle.py`) and `splits_applied` (a
   recent unapplied split fails; an older one is named and passes; "not verified" is never a pass).
3. **Not healthy: read the run artifact**, the newest JSON in `/data/cron_runs/` in the cron container
   (`MSYS_NO_PATHCONV=1 docker exec alphadeck-cron-1 ls /data/cron_runs`). Look at `status` (`running` that
   never finished means a killed pass; its catch-up still runs), the per-thesis tallies and `name_errors`,
   `edgar_companies`, `edgar_companies_fresh` and `freeze_completed_floor`, `code_sha`, `run_kind` and the
   audit.
4. **No run at all: is the sidecar up?**
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml ps cron`, then `... logs --tail 50 cron`.
   Search the log for `PAGING` (a night or backup failed for good), `TIMED OUT`, `heartbeat`
   (`sent`, `held`, `FAILED`) and `backup:`. A box that was off shows only in the external heartbeat.
5. **Runs happen but data looks frozen:** suspect cache freshness. Compare a stale-looking name's newest
   insider fact date with EDGAR's filings (`docs/DATA_SOURCES.md`, "Cache freshness").

## Checks
- `healthy`, a current edge, no alarms, no partial nights and six passing audit checks: report and stop.
- Every claim in the report cites its readout: the status field, the artifact key or the log line.

## Stop and ask when
- Any remediation is needed: report the verdict, the evidence and the fix with its blast radius, then wait.
- The token is missing, or the readouts disagree (the verdict says healthy but the edge is behind).

## Model
`docs/FEED_MONITORS.md` (the checks, the artifact, the health page), `docs/CRON_SIDECAR.md` (schedule,
catch-up, heartbeat), `docs/ADMIN.md` (the verdicts).
