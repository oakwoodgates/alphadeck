# ADMIN.md — the operator ops surface (freshness · run-now · backups)

> Repo path: `docs/ADMIN.md`. The **laptop-deploy ops surface**: is the record current, did last night's
> run actually work, run it NOW if not, and keep a DB-snapshot safety net. A READ surface over the cron's
> own instrumentation (`FEED_LOOP.md`) plus **two explicit operator triggers**. Companion to `FEED_LOOP.md`
> (the cron it watches + the backup mechanics) and `SCOREBOARD.md` (which surfaces the SAME staleness line).
> Code: `backend/app/routers/admin.py` · `frontend/src/admin/Admin.tsx`; the schedule math is
> `backend/pipeline/schedule.py`, the snapshot runner `backend/pipeline/backup.py`.
>
> **Status: BUILT** — Slice 1 (freshness/health + Run-daily-now, #208) + Slice 4 (the DB-snapshot button,
> #215) + the hole-aware freshness read and the `gappy` verdict (#330). Reached via the FE **Admin** nav tab (Board · Workbench · Scoreboard · Admin).

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
  "healthy". A night with **no call-of-record inside the recent window** is its own loud `gappy` verdict too
  (the wrong-day fire the edge check cannot see). Loud styling is reserved for `stale` / `unhealthy` /
  `gappy`; "current", "never begun", and "never ran" stay quiet, and the missed-nights list renders only when
  there is one to show.
- **Auth stays deferred** project-wide (these routes ride the same tenancy seam as the rest).

## Record freshness — "is the record current?"

`GET /admin/status` → the summary the page opens on. The **record edge** is the calls-log `MAX(asof)`
(`calls_repo.record_edge`), measured against the last **expected** Mon-Fri + `RUN_AT` run
(`schedule.py::last_expected_asof` / `expected_runs_behind`, on the market clock — `market_now()`):

- A **Friday** edge read on a **Monday morning** is `0` behind — **current**, never a weekend false alarm;
  the same edge Monday **night** is `1` behind.
- `edge is None` → **"the record has never begun"** — the quiet fresh-install state (`days_behind` null,
  `stale` false), never an alarm.

**The hole check (hole-aware freshness, 2026-09-09).** The edge check above sees only `MAX(asof)` — and a
run that fires on the **wrong day** (the laptop's sleep drift: a 00:24 or 09:09 wake recorded the *next*
day's as-of) advances the edge right over the night it skipped. MEASURED on prod, 6 of 13 weekdays had no
`calls` row while the page read "current". So `/admin/status` also scans the last
**`ALPHADECK_ADMIN_MISSED_WINDOW`** (default **10**; `0` disables) scheduled weekdays ending at the last
expected run (`schedule.py::scheduled_window` / `missed_asofs`; `calls_repo.recorded_asofs` +
`record_first`) for nights with **no call-of-record at all**, bounded to the record's own span (a night before
the record began is pre-history, never a "miss"; a fresh install has no holes). `record.missed` counts them,
`record.missed_asofs` lists them ascending, `record.window_days` says how many were scanned. `stale` /
`days_behind` keep their exact edge-check meaning; the expected day itself, when missing, appears in both.
The page lists the missed dates **only when there are any** (a control that doesn't discriminate doesn't
render). The cause — the sidecar firing for the day it *woke* rather than the night it was scheduled for — is
fixed in `FEED_LOOP.md` §the scheduling sidecar (the target as-of is now fixed at schedule time).

**A reconstructed night reads as recorded here — on purpose.** `record_edge` / `record_first` /
`recorded_asofs` count EVERY `calls` row, including the rows `pipeline.backfill` wrote (`calls.reconstructed`,
migration 0042): after a backfill the night is no longer a hole, `gappy` clears, and the edge can advance.
Freshness asks whether the log advanced, not whether a row is scoreable — the Scoreboard's record path is the
one reader that filters reconstructed rows out (`SCOREBOARD.md`), so "healthy" here and "N nights
reconstructed · not scored" there can both be true of the same night.

### Stale price tapes — "is every name still being priced?"  `[BUILT, G5a]`

`status.tape` is the price-tape panel, and it closes the "monitor health: is it watched?" gap for prices.
**The failure it makes visible:** the price leg appends bars after the latest stored one, so a name whose
vendor series simply **STOPS** — the SEC ticker stays canonical but the vendor prices the name under a new
symbol after a rename, or the name delisted — returns a series that ends at the stop: **zero bars appended,
no error, indistinguishable from a market holiday.** Nothing read the tape's edge, so it was invisible, and
for that name every price-driven signal goes dark from the stop date (no breakout, no SMA flip, no RVOL — and
no price-based de-arm either) while the CIK-keyed filing feeds keep flowing, so it can still WARM on a filing
and never confirm on price.

- **What counts as stale:** the latest stored EOD bar is `ALPHADECK_TAPE_STALE_DAYS` (default **5**) or more
  **calendar** days before the run's as-of, or the name has no bars at all. Calendar days because the trading
  clock deliberately has no holiday calendar. A live tape gets that session's bar appended nightly, so its
  edge sits at 0–1 days and the threshold never comes near it — the number only matters once bars **stop**
  arriving: 5 means a tape may lag up to **four** calendar days before it reads stale, and a dead tape
  surfaces within a week. Counted out: a Friday close is fresh through Tuesday's pass (4 days) and reads
  stale on **Wednesday's** (5), so a weekend — or a weekend plus a Monday or Friday holiday — is inside the
  window. The accepted edge: a rare **two-session** closure beside a weekend (a Thursday+Friday shutdown
  leaves a Wednesday edge and a Monday pass = 5 days) flags for one night and clears on the next session.
  `0` disables the monitor; raise it if that night ever costs more than catching a dead tape a day sooner.
- **Where it comes from:** the nightly pass records each name's tape edge and its stale set into the
  run-of-record artifact, and this panel reads the newest artifact that actually **evaluated** recency. So it
  is "as of last night" (the right granularity for a nightly feed), it costs no query, and this surface still
  owns no tables. `tape` is **null** until a pass has looked — a `--no-live` pass never counts.
- **One row per security**, even when two theses hold the name, with its last-bar date and the thesis it was
  seen under; a name with no ticker renders by id rather than vanishing (#9). The list renders **only when
  something is stale** — "no stale tapes" is the normal night.
- **It pages, but it never changes `cron.status`.** A stopped tape is a **feed** gap to repair, not a cron
  fault: the run did its job. So the night's run row carries a problem line naming the tape and the notifier
  pushes it, while the one-word verdict stays about the cron (an `unhealthy` chip that really meant "a vendor
  renamed a ticker" would teach you to ignore the chip). Only **newly** stale tapes page — the diff is against
  the previous evaluated pass, keyed on the security (not the ticker), so a handful of known-dead tapes do not
  re-page every night. The **first** evaluated pass after this shipped pages the whole current inventory once,
  on purpose.
- **The repair is yours, and it is data, not code:** check each listed name for a ticker rename or a
  delisting, then set the vendor symbol override on the security master (`security_master.price_symbol`, the
  OTC fix's seam). The next nightly pass re-pulls the full year under the new symbol, appends the missing tail
  and hole-fills the overlap. Teaching the symbol resolver to follow renames **automatically** is deliberately
  not built: a wrong auto-resolve would file another company's tape under your member, which is worse than a
  visible gap (`INVARIANTS.md` #4/#6). See `DATA_SOURCES.md` §free EOD prices.

This is the **same staleness the Scoreboard shows** (Slice 2, `SCOREBOARD.md`) — one contract
(`pipeline/schedule.py`), two surfaces — both now feeding it `domain/market_time.market_now()` (an explicit
`ZoneInfo`) rather than an ambient `datetime.now()`. *(Earmark, still open: `schedule.py` remains the second
home of the Mon-Fri + `RUN_AT` contract alongside the shell's sleep-loop. `market_today()` did **not** unify
them — it answers "what day is it in market time" and does no trading-calendar logic; the remaining work is
shrinking the shell to a dumb trigger. See `ROADMAP.md` "what's next".)*

## Cron health — "did last night's run work?"

The one-word `cron.status` verdict, plus the last run's counts and any problems:

| Verdict | Meaning |
|---|---|
| `never_ran` | no run-of-record artifact yet — run one below, or bring the `cron` sidecar up |
| `unhealthy` | the last run **froze / errored / totally failed / could not refresh the benchmark tape** — as loud as `stale`, so a bad run can't hide behind green |
| `stale` | the record missed an expected scheduled run (freshness above) |
| `gappy` | the edge is current and the last run clean, but a night inside the last `ALPHADECK_ADMIN_MISSED_WINDOW` scheduled runs has **no call-of-record** — a run fired on the wrong day (the hole check above); the detail names the dates |
| `healthy` | the last run is clean, the record is current, and the window has no holes |

Priority: `never_ran` > `unhealthy` > `stale` > `gappy` > `healthy` (a stale edge with a hole reads `stale`
— the louder, more actionable verdict — while `record.missed_asofs` still lists both).

The verdict re-derives via `assess_health` over the newest **readable** run-of-record artifact
(`data/cron_runs/`, skip-unreadable fail-open). The **benign** `--no-live` cache-only note is excluded from
the alarm set, so a hand-run dev pass never paints the cron `unhealthy` (honest loudness). A **`--catch-up`**
pass (the sidecar's boot / late-wake catch-up) is re-read with the freeze check skipped, exactly as the run
itself was assessed — it runs inside the EDGAR 12h TTL and legitimately fetches ~0 — so the history never
shows a catch-up as unhealthy; its row carries a small **catch-up** tag (`AdminRunOut.catch_up`).
`GET /admin/runs` returns the run history — the last N artifacts parsed, newest first.

A **newly stale price tape** (G5a) also appears in `problems`, but carries the same benign marker as the
`--no-live` note, so it never makes the verdict `unhealthy` — it is a feed gap, not a cron fault (see
"Stale price tapes" above).

A **failed benchmark refresh** is one of the alarms (G4). The SPY/IWM tape is a *shared* call-logic input
(`benchmark_rs`), refreshed by a fail-open passenger leg before the per-thesis loop; its faults used to reach
stdout only, so a night could produce calls against a **stale** tape and still read green. Two problem lines,
reported distinctly because they are different news: the leg **failing outright** (nothing refreshed) and
**N individual benchmark pulls** failing (a partly stale tape). The counts are written into the artifact, so a
night that paged re-reads as `unhealthy` in the history forever; an artifact written before this shipped reads
clean, never broken.

## Run daily now — the one trigger

`POST /admin/run-daily` **kicks a background job** and returns immediately (**202** + `job_id`); poll
`GET /admin/run-daily/jobs/{job_id}`. It runs the cron's **exact** unit of work (`run_daily_pass` — live
ingest → call-of-record → the run-log artifact → the health page), so a manual run **lands in the run
history like the nightly one**. A **409** single-slot guard means a double-click can never stack a second
pass. It does a **LIVE EDGAR pull** (~2 min warm, up to ~65 min on a cold cache) and is safe to re-click once
finished — the pass is idempotent (`record_if_changed` appends nothing on unchanged facts). **Safe at any hour
(G1):** it used to be a morning-only button, because it warmed every company's filing index for up to 12h and
the night's scheduled pass then served that warm index — blind to the afternoon's filings. The recurring
client now carries the **recurring TTL (five minutes)**, so this button *refreshes* the cache rather than
warming it and the night re-fetches regardless — anything read more than five minutes ago is re-pulled, and
nothing can be filed in the five minutes before the 22:30 pass (`FEED_LOOP.md` §Fresh data). The one thing
that still reads a warm cache is a pass fired **within five minutes** of another, which is why a back-to-back
re-click can legitimately report ~0 EDGAR fetches. The job opens its
own DB connection (it outlives the request); a lost job (server restart / expiry) shows "lost from view", not
an infinite spinner — the run history + record edge are the durable authority. A manual pass that starts
**before** that night's `RUN_AT` (a pre-open click, on the prior session's bars) does **not** satisfy the
sidecar's `--catch-up` guard — the night's post-close pass still runs (2026-09-10; on Sep 9 two 09:09 / 09:15
ET passes had masked a missed post-close pass, and the record stayed a pre-open call).

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
**not** a synthetic rebuild; a forward `daily` run then re-armed one of the restored theses. This slice turns that lucky
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
