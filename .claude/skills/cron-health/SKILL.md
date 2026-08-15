---
name: cron-health
description: >-
  Diagnose whether the daily call-of-record cron ran and the record is fresh —
  the readouts that would have caught the 11-day 2026-07 freeze. Trigger on "is
  the cron healthy", "did the cron run", "check the feed", "is the data fresh",
  or any suspicion a thesis looks stale. Diagnosis is READ-ONLY; any remediation
  (re-runs, restores) is proposed to the operator, never applied.
---

# Cron health — did last night's run happen, and did it do anything?

Authoritative sources: `docs/FEED_LOOP.md` + `docs/ADMIN.md` (the freshness
surfaces) + `docs/POSTMORTEM_CRON_FREEZE_2026-07.md` (what a freeze looks like).

## Guardrails (never violate)
- **Diagnosis is read-only.** A manual `pipeline.daily` WRITES the call-of-record —
  never run it as a probe. Propose remediation (the Admin "Run daily now", a
  targeted re-ingest, a restore); the operator decides.
- **Never "fix" stale data by re-ingesting history** — the bitemporal trap
  (see `backup-restore`). Re-ingest recovers going-forward only.
- **A 0-fetch reading alone is not proof of a freeze.** The R4 zero-fetch check has
  a known false-positive mode (open backlog) — corroborate with the run log and the
  record edge before declaring one.

## Steps
1. **The verdict first:** `curl http://localhost:8000/admin/status` → `cron.status`
   (`healthy` / `stale` / `unhealthy`), the **record edge** (calls-log `MAX(asof)`
   vs the schedule-aware expected edge — weekends don't cry wolf), and the
   last-snapshot age. `healthy` + current edge → report and stop.
2. **Not healthy → read the run-of-record:** newest JSON in `data/cron_runs/`
   (append-only, one per pass) — per-thesis ingest tallies, appended/unchanged/
   errored counts, and whether the run happened at all.
3. **No run at all → is the sidecar up?** `docker compose ps cron` +
   `docker compose logs --tail 50 cron`.
4. **Runs happen but data looks frozen → suspect cache freshness** (the 2026-07
   failure shape): mutable EDGAR prefixes refresh on a key-classed 12h TTL — check
   a stale-looking name's newest insider fact date against EDGAR's actual filings
   (`docs/DATA_SOURCES.md` §cache-freshness).
5. **Report MEASURED findings** — verdict, evidence (edge dates, run-log excerpts),
   and the proposed remediation with its blast radius. Wait for the operator's go
   before touching anything.
