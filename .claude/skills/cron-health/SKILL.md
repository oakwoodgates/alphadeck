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
- **A 0-fetch reading alone is not proof of a freeze.** Since O5 (2026-10-21) the
  verdict reads COMPLETED fetches (`edgar_fetches_completed`; `edgar_fetches` is the
  attempts). `FROZEN — 0 of N EDGAR fetches COMPLETED` = pulls went out and none came
  back (a dead feed: check `env.user_agent_set`, then SEC reachability);
  `FROZEN — 0 EDGAR fetches` = the cache never reached out (a real freeze, or a pass
  started within five minutes of another). Corroborate with the run log and the
  record edge before declaring one. The check fires only at EXACTLY zero — a pass
  that completed a handful of fetches across hundreds of names is not paged (open
  gap, `FEED_LOOP.md` "Known gaps"): compare `edgar_fetches_completed` with the
  names ingested yourself.

## Steps
1. **The verdict first:** `curl http://localhost:8000/admin/status`:
   - `cron.status`: `healthy` / `stale` / `unhealthy` / `gappy` / `running`;
   - `cron.alarms`, which covers code-sha unknown or a cron↔backend mismatch, notifier=log, UA unset, backup age, and a pass that did not finish or crashed;
   - the **record edge**: calls-log `MAX(asof)` vs the schedule-aware expected edge, so weekends don't cry wolf;
   - the last-snapshot age.

   Since 2026-10-16 also read:
   - `record.partial_nights`: a night is covered only when EVERY thesis that existed then has an on-time row, so "4 of 12" shows here;
   - `record.reconstructed_nights`: backfilled nights, deliberately QUIET;
   - `unfinished_run`;
   - `last_run.audit`: the V1 self-audit, one entry per check — `on_time_rows`,
     `code_sha_known`, `every_thesis_finished`, `split_defects_flagged`, and
     `no_unsettled_bars` (no EOD bar recorded before its 18:00 ET settle instant,
     benchmarks included, over the rows written since the last judging pass; a
     failure names `TICKER d @ recorded` — an in-progress value stored as a close, so
     find the writer that bypassed `ingest/prices/settle.py`);
   - `last_run.edgar_fetches` / `edgar_fetches_completed`: attempted vs completed
     EDGAR pulls (the freeze verdict reads the completed count; `null` before O5);
   - `last_run.name_errors_new`: newly failing names (known ones are a quiet count);
   - `split_defects`: the T4 split-basis monitor.

   `healthy` with a current edge, no alarms, no partial nights and all audit checks passing → report and stop.
2. **Not healthy → read the run-of-record:** the newest JSON in `/data/cron_runs/` inside the cron container (`MSYS_NO_PATHCONV=1 docker exec alphadeck-cron-1 ls /data/cron_runs`; append-only, one per pass). It holds:
   - per-thesis ingest tallies and `name_errors` with reasons;
   - appended / unchanged / errored / skipped counts;
   - `code_sha` / `config_hash` / `run_kind` / `env`;
   - the audit;
   - `status`: `running` / `finished` / `crashed`. A `running` file that never finished is a pass that was KILLED mid-loop. Its night's catch-up still runs (the guard counts finished passes only) and records only the theses missing an on-time row (M2).
   - **Out-of-band:** the healthchecks.io heartbeat (`HEARTBEAT_URL`) alerts when the whole box or Docker is down, which no in-box check can see.
3. **No run at all → is the sidecar up?** `docker compose -f docker-compose.yml -f docker-compose.prod.yml ps cron` +
   `docker compose -f docker-compose.yml -f docker-compose.prod.yml logs --tail 50 cron`.
   The shell logs its own failure signals (2026-10-16): grep that log for
   `PAGING` (a night or backup that failed for good — H3), `TIMED OUT` (a pass
   stopped at `RUN_TIMEOUT_S` — C2), `heartbeat` (`sent` / `held` / `FAILED` — H2)
   and `backup:` (`wrote` / `skipped` — the boot's age gate, M3).
4. **Runs happen but data looks frozen → suspect cache freshness** (the 2026-07
   failure shape): mutable EDGAR prefixes refresh on a key-classed 12h TTL — check
   a stale-looking name's newest insider fact date against EDGAR's actual filings
   (`docs/DATA_SOURCES.md` §cache-freshness).
5. **Report MEASURED findings** — verdict, evidence (edge dates, run-log excerpts),
   and the proposed remediation with its blast radius. Wait for the operator's go
   before touching anything.
