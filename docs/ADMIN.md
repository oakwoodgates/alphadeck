# ADMIN.md — the operator ops surface (freshness · run-now · backups)

> Repo path: `docs/ADMIN.md`. The **laptop-deploy ops surface**: is the record current, did last night's
> run actually work, run it NOW if not, and keep a DB-snapshot safety net. A READ surface over the cron's
> own instrumentation (`FEED_LOOP.md`) plus **two explicit operator triggers**. Companion to `FEED_LOOP.md`
> (the cron it watches + the backup mechanics) and `SCOREBOARD.md` (which surfaces the SAME staleness line).
> Code: `backend/app/routers/admin.py` · `frontend/src/admin/Admin.tsx`; the schedule math is
> `backend/pipeline/schedule.py`, the snapshot runner `backend/pipeline/backup.py`.
>
> **Status: BUILT** — Slice 1 (freshness/health + Run-daily-now, #208) + Slice 4 (the DB-snapshot button,
> #215). Reached via the FE **Admin** nav tab (Board · Workbench · Scoreboard · Admin).

---

## Why it exists

The deploy reality it answers: a **sleep-loop cron on a laptop** (`scripts/daily_cron.sh`) that can miss
nights, and containers that don't always restart. Three questions and one safety net:

1. **Is the record current?** — the record-freshness readout.
2. **Did last night's run work?** — the cron-health verdict.
3. **Run it NOW if not** — the one explicit trigger.
4. **…and never lose the DB again** — the snapshot button (Slice 4, born of the 2026-07-21 truncation below).

## The bounds (what the surface may and may not do)

- **Pure ops READ surface.** `status` / `runs` / `backups` own **no tables and write nothing** — test-proved
  by counting every public table before/after. They read the calls log's `MAX(asof)`, the run-of-record
  artifacts (`data/cron_runs/`), the backup directory, and the schedule math. **No LLM anywhere near this
  router.**
- **Operator-initiated triggers only.** *Run daily now* and *Create snapshot* fire **only** on an explicit
  click — never on a page load, mount, or poll (the **cost thread**: cost is the operator's to spend, never
  ambient). Reads may poll; the triggers never do.
- **Honest loudness (#7).** Staleness is measured against the last **expected** scheduled run (never raw
  `today − edge`), so a weekend never cries wolf; and a bad LAST run (freeze / errors / total ingest failure)
  is its own loud `unhealthy` verdict, **peer to** `stale` — the R1 freeze must never hide behind a green
  "healthy". Loud styling is reserved for `stale` / `unhealthy`; "current", "never begun", and "never ran"
  stay quiet.
- **Auth stays deferred** project-wide (these routes ride the same tenancy seam as the rest).

## Record freshness — "is the record current?"

`GET /admin/status` → the summary the page opens on. The **record edge** is the calls-log `MAX(asof)`
(`calls_repo.record_edge`), measured against the last **expected** Mon-Fri + `RUN_AT` run
(`schedule.py::last_expected_asof` / `expected_runs_behind`, on the container-local clock):

- A **Friday** edge read on a **Monday morning** is `0` behind — **current**, never a weekend false alarm;
  the same edge Monday **night** is `1` behind.
- `edge is None` → **"the record has never begun"** — the quiet fresh-install state (`days_behind` null,
  `stale` false), never an alarm.

This is the **same staleness the Scoreboard shows** (Slice 2, `SCOREBOARD.md`) — one contract
(`pipeline/schedule.py`), two surfaces. *(Earmark: `schedule.py` is now the second home of the Mon-Fri +
`RUN_AT` contract, alongside the shell's sleep-loop; a durable `market_today()` would unify them — see
`ROADMAP.md` "what's next".)*

## Cron health — "did last night's run work?"

The one-word `cron.status` verdict, plus the last run's counts and any problems:

| Verdict | Meaning |
|---|---|
| `never_ran` | no run-of-record artifact yet — run one below, or bring the `cron` sidecar up |
| `unhealthy` | the last run **froze / errored / totally failed** — as loud as `stale`, so a bad run can't hide behind green |
| `stale` | the record missed an expected scheduled run (freshness above) |
| `healthy` | the last run is clean and the record is current |

The verdict re-derives via `assess_health` over the newest **readable** run-of-record artifact
(`data/cron_runs/`, skip-unreadable fail-open). The **benign** `--no-live` cache-only note is excluded from
the alarm set, so a hand-run dev pass never paints the cron `unhealthy` (honest loudness). `GET /admin/runs`
returns the run history — the last N artifacts parsed, newest first.

## Run daily now — the one trigger

`POST /admin/run-daily` **kicks a background job** and returns immediately (**202** + `job_id`); poll
`GET /admin/run-daily/jobs/{job_id}`. It runs the cron's **exact** unit of work (`run_daily_pass` — live
ingest → call-of-record → the run-log artifact → the health page), so a manual run **lands in the run
history like the nightly one**. A **409** single-slot guard means a double-click can never stack a second
pass. It does a **LIVE EDGAR pull** (~2 min warm, up to ~65 min on a cold cache) and is safe to re-click once
finished — the pass is idempotent (`record_if_changed` appends nothing on unchanged facts). The job opens its
own DB connection (it outlives the request); a lost job (server restart / expiry) shows "lost from view", not
an infinite spinner — the run history + record edge are the durable authority.

## Backups — the DB-snapshot safety net (Slice 4)

The operator-facing view of the backup net; the **mechanics live in `FEED_LOOP.md` §Backups & restore** (the
runner, retention, the host bind, the client version) — this section is the surface, not the source.

- **Create + list.** `POST /admin/backup` kicks a background job (**202** + `job_id`, poll
  `GET /admin/backup/jobs/{job_id}`, **409** single-slot guard) that shells `pg_dump` — **READ-ONLY**, opens
  no app connection and mutates no row — to `./data/backups/alphadeck-<UTC>[-<label>].sql` (a host bind, so a
  dump is copyable off-box). `GET /admin/backups` lists them newest-first, and `/admin/status` carries the
  **last-snapshot age**. Same operator-initiated discipline as *Run daily now* (fires only on the click; the
  list + age are reads).
- **Retention = keep-last-N, labeled EXEMPT.** After a successful dump the newest `ALPHADECK_BACKUP_KEEP`
  (default **7**) **unlabeled** snapshots are kept and older unlabeled ones pruned; a **labeled** dump (e.g.
  `pre-migration`) is never auto-deleted. A failed dump never prunes (it must not shrink the safety net).
- **Nightly, too.** `scripts/daily_cron.sh` runs `python -m pipeline.backup` right after the scheduled
  `pipeline.daily` (weekday branch), fail-open — deliberately **not** folded into `run_daily_pass`, so a
  manual "Run daily now" does not also dump.
- **RESTORE is CLI-only — never a button.** A restore is destructive (drop-schema + reload) and belongs in
  human hands. The documented sequence (the one actually used on 2026-07-21):
  `docker exec -i alphadeck-postgres-1 psql -U alphadeck -d alphadeck < ./data/backups/<file>`.

**Why the net exists (the honest note).** On **2026-07-21** a shared-Postgres pytest hazard **truncated the
whole demo DB**. Recovery worked **only** because an ad-hoc `pg_dump` happened to exist — it was a **real
restore** from a 2026-07-17 snapshot (all six theses and the real call-of-record, migrations `0021→0024`),
**not** a synthetic rebuild; a forward `daily` run then re-armed Rainbow Rush. This slice turns that lucky
ad-hoc into a one-click **and** nightly net (and #217 removed the root cause — a fail-closed guard that
refuses to truncate any non-`alphadeck_test` DB). Full account of the truncation hazard: the operator memory;
the freeze it is adjacent to: `POSTMORTEM_CRON_FREEZE_2026-07.md`.

## The endpoints

| Endpoint | Shape | Notes |
|---|---|---|
| `GET /admin/status` | freshness + cron verdict + last-snapshot age | read-only; the page opens on it |
| `GET /admin/runs?limit=` | the run-of-record history, newest first | read-only file parse; skip-unreadable |
| `POST /admin/run-daily` | **202** + `job_id` | the cron's exact unit; 409 single-slot |
| `GET /admin/run-daily/jobs/{job_id}` | job status → run-history row | 404 → "lost from view", never a spinner |
| `POST /admin/backup` | **202** + `job_id` | read-only `pg_dump`; 409 single-slot |
| `GET /admin/backup/jobs/{job_id}` | job status → `BackupOut` | 404 → "lost from view" |
| `GET /admin/backups` | the snapshot list, newest first | read-only directory scan |

## What it deliberately is NOT

- **Not the scheduler.** The `cron` sidecar fires the nightly run (`FEED_LOOP.md`); this surface only
  **triggers on demand** and **observes**. It does not change the schedule.
- **Not a restore surface.** Restore stays a documented CLI act (above) — destructive, human-only.
- **Not the dead-man's switch.** A run that never happens produces no artifact → no page (a known gap:
  `restart: unless-stopped` covers crash/reboot, not a deliberate `stop`). A *persistent* absence needs an
  **external** heartbeat — the sidecar can't page about its own absence. See `FEED_LOOP.md` "Known gaps".
- **Not the replay-regenerate button.** Regenerating the Scoreboard's replay artifact stays out of scope,
  deferred to the replay-panel work (`ROADMAP.md`).
