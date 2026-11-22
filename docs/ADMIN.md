# Admin page

`/admin` is the steward's operations page. It answers three questions from what the nightly machinery already
wrote (is the record current, did last night's pass work, which platform-data decisions wait for the steward) and
carries two triggers: Run daily now and a backup. The steward's company page, `/admin/facts/<security_id>`, holds the
share-count and cash/burn decisions. Only a steward reaches either: the header shows the Admin tab to a steward only,
the app renders a "stewards only" page to anyone else (`App.tsx`), and every `/admin/*` route is mounted behind
`require_super_admin` (`app/main.py`; `tests/app/test_auth_gate.py` treats every `/admin` path as a steward path).
Read this doc before you add a panel, a status field or an admin route. What each nightly monitor flags, and why, is
in [FEED_MONITORS](FEED_MONITORS.md); this doc covers what the page shows and what each control does.

## Code

| Piece | Module |
|---|---|
| The page: record freshness, cron health, Run daily now, backups, run history | `frontend/src/admin/Admin.tsx` |
| Panels | `SharedFacts.tsx`, `PriceFeedRepairs.tsx`, `InsiderAmendments.tsx`, `InsiderHolds.tsx`, `SplitList.tsx`, `DataFlagList.tsx`, `Onboarding.tsx` (all in `frontend/src/admin/`) |
| The company page | `CompanyFacts.tsx`, `CorrectForm.tsx`, `FlagsFromUsers.tsx`, `FactSearch.tsx` |
| Routers | `app/routers/admin.py`, `admin_price_feed.py`, `admin_insider_amendments.py`, `admin_insider_holds.py`, `admin_splits.py`, `admin_onboarding.py`, `admin_platform_values.py` |
| Schedule and coverage | `pipeline/schedule.py` (the session calendar from `domain/trading_days.py`), `pipeline/night_coverage.py`, `scoreboard/late.py` |
| Run daily now | `pipeline/daily_job.py`, `pipeline/bounded_pass.py` |
| Backups | `pipeline/backup.py`, `pipeline/backup_job.py` |

## How it works

### Rules of the page

- **Reads write nothing** (`test_admin_reads_write_NOTHING` in `tests/app/test_admin_api.py`). They read the run
  artifacts under `data/`, the backup directory and a few tables. No model is called anywhere on the page.
- **Triggers fire on a click only,** never on a load or a poll.
- **Every write is a steward's decision on platform data:** an append-only row naming its author, with an exact
  undo that refuses (409) when the value it would restore has moved since. These routes act on the data tenant,
  never on the steward's workspace ([TENANT_SCOPES](TENANT_SCOPES.md)).
- **Loud only for what the steward must act on** (invariant 7). A list renders only when it has rows. A stopped feed
  pages once but never changes the cron verdict: the run did its job, a feed needs a repair.
- **Another workspace's card shows as ids,** `workspace <8 hex> · thesis <8 hex>` (`_NameGuard` in `admin.py`);
  tickers and security ids stay.
- **Not a restore surface, not the scheduler, not the dead-man's switch:** restore is CLI-only (the
  `backup-restore` skill), the cron sidecar schedules, and only the external heartbeat notices a machine that is off
  ([CRON_SIDECAR](CRON_SIDECAR.md)).

### Record freshness

The first card, from `GET /admin/status`.

- **The edge** is the newest as-of in `calls` (`calls_repo.record_edge`), measured against the last expected run:
  the last market session at `RUN_AT` on the market clock. A Friday edge read on Monday morning is current; `stale`
  means an expected run is missing. No edge at all is the quiet "never begun" state.
- **Holes.** `record.missed_asofs` lists the sessions in the last `admin_missed_window` scheduled runs, within the
  record's own span, that have no covering call of record. A night is covered by a row recorded at or after that
  night's `RUN_AT` (`Settings.cron_run_at`, the variable the sidecar fires on). A night whose only row came from a
  daytime run is listed and marked so: a pre-open run reads the prior session's bars.
- **Partial nights** (`record.partial_nights`): some card on the night's roster (created before `RUN_AT`, not archived
  before it) has no covering row. The definition is the self-audit's (`pipeline/night_coverage.py`).
- **Two deliberate asymmetries with the Scoreboard.** A night `pipeline.backfill` reconstructed counts as recorded
  here, so the hole clears (listed quietly in `reconstructed_nights`), though the Scoreboard never scores it. A late
  row covers its night here while the Scoreboard sets it aside; a card-night recorded only late is listed in
  `late_only_nights`, judged by `scoreboard/late.py` so the two pages agree ([SCOREBOARD](SCOREBOARD.md),
  "Late-recorded rows").
- **One-line fields,** each shown only when it has something: splits needing review (loud); revenue rows set aside
  by the data-quality screen (quiet); `data_tenant_theses`, cards no user can open (loud above 0);
  `platform_rows_in_workspaces`, share counts or cash/burn under a workspace, which no read sees (loud above 0); the
  editions job's line and, for a week after an automatic cut, the cut's ([EDITIONS](EDITIONS.md)); the weekly
  company-list refresh ([DATA_SOURCES](DATA_SOURCES.md), "Company list and identity"); the newest
  background-extraction run (`ExtractionRun.admin_line`; [PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md)).
- **Stopped feeds** (`tape`): price tapes and ETF fund-shares samples past their staleness threshold, from the newest
  finished live pass that covered each card, with the thresholds that pass used. Loud rows, one per security; closed
  tapes in a quiet block. A plain gap is repaired by setting `security_master.price_symbol` ([DATA_SOURCES](DATA_SOURCES.md),
  "Prices (Yahoo)"); renamed, never-priced and closed names move to Price feed repairs.
- **Missing closes:** one quiet line each night; loud only for a close still missing after the next session's pass;
  never a page and never the verdict.
- **Split-basis defects:** loud rows per name and kind, marking those that paged; flaps no reader sees are one
  quiet settled line. They never change the verdict.

### Shared facts

The steward's way into the shared share counts and cash/burn ([PLATFORM_VALUES](PLATFORM_VALUES.md)). It reads
`GET /admin/platform-values/queue` and writes nothing.

- How many companies await the steward (loud above 0), and a company search that opens the company page.
- The queue in the server's order: each company, how many cards hold it (a count, never whose), what waits (a
  flagged value, or a newer filing beside the value shown) and for how long, paged. Companies held only in a
  Discovered pen are not counted and open on request.
- A quiet line counting the other work below (splits to review, held insider rows owed, Form 4/As owed), each count
  a link to its panel.

**The company page** shows each value's state, its versions and decisions, and its candidates with the located
filing passage. A flag candidate offers a plain-English explanation, a grounded aid that never supplies a value
(`POST /workbench/facts/explain`, steward only). The six decisions (approve, correct, dismiss, undo, pin, unpin)
follow [PLATFORM_VALUES](PLATFORM_VALUES.md), "The steward": an undo sets the decision aside and offers it again
(a correction comes back prefilled), and a pin shows who pinned and why. Users' flags on the value are closed or
reopened here ([VALUE_FLAGS](VALUE_FLAGS.md), "The steward's side"). Extract now runs background extraction's
per-company step on a click, recorded as a steward attempt ([PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md)); it is not
a decision and has no undo. Each value's history lists the recent extraction attempts.

### Price feed repairs

One card for every price-feed decision, shown only when it has something. Its inventory is the stopped-feed
inventory; its decisions are read live from the repair log.

- **Renamed** (loud): a stopped tape whose company EDGAR lists under a symbol not priced, with the nightly splice
  probe's verdict on each listed symbol and the EDGAR evidence. Adopt sets `price_symbol`; a symbol EDGAR does not
  list for the name is refused (409; use Set).
- **Never priced** (loud): Set price symbol (validated by one vendor fetch into a throwaway cache; no bars is 422,
  nothing written), Acknowledge (kept unpriced, with a reason, revocable) or a link to the basket editor.
- **Acknowledged, closed and in force** (quiet): acknowledged names, names that stopped trading, and every standing
  Adopt or Set whose tape has healed, each with its inverse.
- **Known feed limitations** (quiet): names whose vendor serves no history ([FEED_MONITORS](FEED_MONITORS.md), "Known
  feed limitations"). A mark is made through the API with a required reason, after one vendor fetch shows the
  vendor serves bars but no history (409 otherwise); it quiets nothing else and has an undo.
- **Successor CIK** (loud): a member whose CIK lists no symbol while SEC's ticker map gives its ticker to another
  registrant. Adopt CIK re-points `security_master.cik` in place (only the recommended successor, read that night;
  409 for anything else or a CIK another row holds); its undo restores every column it touched.

### Insider amendments

Every Form 4/A the nightly stored, with its evidence (remarks, footnotes, the EDGAR index, each original's rows). The
system applies what the filer's own words settle ([FILINGS](FILINGS.md), "Form 4/A: stored apart, resolved at read"
and "The filer's words"); the steward decides only the rest.

- **Owed your decision** (loud; items inside today's signal windows first): an ambiguous amendment (link it to a
  candidate), an unresolved row match, or a zero-row 4/A whose words retract a trade. The verdicts: use the
  correction, keep the original, add to the original. Each owed item pages once when new.
- **Resolved automatically** (quiet): the system's resolutions, each with its exact undo, including its sole-match
  links.
- **Quiet lists:** decided by the steward; historical (outside the review line, never paged); awaiting the nightly
  sweep; nothing to apply.

Each card first says whether any answer on it can change a row a live call or an edition page reads. One standing
link and one standing steward verdict per amendment at most (a second is 409).

### Held insider rows

Form 4 rows the ingest held because their transaction date cannot be true ([FILINGS](FILINGS.md), "Held rows: a
date that cannot be true"). Each row sits in one bucket judged by the same ladder the insider read runs
(`domain.insider_holds.hold_state`), so the panel never disagrees with a call:

- **Owed your decision** (loud, the only loud bucket), with why the placement rule did not place it.
- Quiet: placed by rule, corrected by the filer, decided, dismissed, and held in a Form 4/A.

Each row shows the filing's own words and a suggested date (arithmetic on the filing, never a model). Approve takes a
date, by default only a suggestion that agrees with the period of report; the date must pass the year rule and fall
on or before the filing's market date (422 otherwise). Dismiss needs a reason; undo returns the row to the state
before. A decision counts from when it was made, on the public clock. `GET /admin/status` carries
`insider_holds_owed`.

### Splits

The split list that `pipeline.backfill_splits` filled from Massive.com and each company's SEC filing
(`admin_splits.py`; [FILINGS](FILINGS.md), "Split filings"). Shown only when the list has entries.

- **Needs review** (loud, the only loud list): a split one source cannot settle, with the SEC passage and
  Massive.com's row side by side: confirm one source's values (or the only source's), or reject.
- **Auto-confirmed, announced and set aside** (quiet). Revoke any confirmed split; Restore undoes a reject or a
  revoke. Rejected and revoked splits stay listed, grayed.
- **Split-shaped jumps with no split listed** (quiet): jumps the split-night hold released that no source lists.
  Dismissing one as a real move, and its undo, are display only; the hold never reads them.

Every action appends one version naming the steward and the version it acts on, so a stale card is refused (409).
A confirm lifts the split-night hold the next night; the nightly price leg and the repair tool keep stored tapes on
the list's scale. Nothing here sends a push.

### Data flags

The current "is this real?" flags across the steward's own workspace's active cards, computed on read from
`GET /data-flags` (any signed-in user's route, for their own workspace; [BOARD](BOARD.md), "Data flags"). Names whose
flags reach a Cockpit number come first. Read-only: each flag names what clears it.

### Onboarding

The staged cold-ingest queue ([INGEST](INGEST.md), "Cold names (onboarding)"), from `GET /admin/onboarding` and the
job's newest artifact. Shown once the queue has held a name.

- A quiet summary: counts by state, the nightly window the job yields to, and the latest run and why it stopped.
- **Failed** (loud, with Retry): a name whose history legs failed every allowed attempt. Retry requeues it with its
  checkpoint kept (409 not failed, 404 not queued).
- **Stalled** (loud): names waiting and no run finished for `onboard_stalled_after_h`, so the sidecar's onboarding
  loop is not running. A day that hit its cap reads as quietly paused instead.
- Waiting, loading and recently loaded names (quiet), each with its checkpoint; a name no live card holds is parked.

`python -m pipeline.onboarding --status` prints the same from the CLI.

### Cron health

The one-word verdict `cron.status`, its detail and alarms, and the last finished run's counts:

| Verdict | Meaning |
|---|---|
| `never_ran` | No finished pass and none running |
| `running` | A pass is in flight inside the run timeout and nothing else is wrong, including the very first pass; quiet |
| `unhealthy` | The last finished pass raised an alarm ([FEED_MONITORS](FEED_MONITORS.md), "The health page"), or a status alarm is up |
| `stale` | The edge missed an expected run |
| `gappy` | The edge is current and the last run clean, but a night in the window is a hole or partial |
| `healthy` | None of the above |

With a finished pass the order is `unhealthy`, `stale`, `gappy`, `running`, `healthy`; without one, `running` if the
first pass is in flight, else `never_ran`. The status alarms (`cron.alarms`): the cron's last pass ran with an unknown
`code_sha`; the cron's sha differs from the backend's (compared only for a cron pass that started after this backend
did); the last live pass paged through the log notifier or had no SEC User-Agent; the newest unlabeled dump is older
than `backup_stale_hours` in schedule time; the newest pass did not finish within the run timeout or crashed; a
recorded night has no run artifact. The backup and missing-artifact alarms apply only where the run history shows
the cron. Each finished pass is re-judged by the rule it was assessed under, so an old row never changes color.

The run history (`GET /admin/runs`) lists finished passes newest first; a line ends with where the pass spent its
time when the artifact recorded it. Procedure for reading all this: the `cron-health` skill.

### Run daily now

`POST /admin/run-daily` starts the cron's exact unit of work (`run_daily_pass`: live ingest, the call of record, the
artifact, the health page) as a background job and answers 202 with a job id; poll
`GET /admin/run-daily/jobs/{job_id}`. It answers 409 while any process runs a pass (the cross-process run lock is
probed first) or the job slot is busy, and 503 when the database cannot be reached. The pass runs in a child process
bounded by `cron_run_timeout_s`, the sidecar's limit: past it the child is stopped, its artifact reads crashed and one
crash page goes out. A lost job never spins; the run history is the authority. A run that starts before that night's
`RUN_AT` does not satisfy the sidecar's catch-up, so the night's own pass still runs ([FEED_LOOP](FEED_LOOP.md),
"Missed and late nights").

### Backups

Back up now (`POST /admin/backup`) starts a read-only `pg_dump` as a background job (202; 409 while one runs); the
list (`GET /admin/backups`) and the last snapshot's age are reads. Schedule, retention and format:
[CRON_SIDECAR](CRON_SIDECAR.md), "Backups". Restore is CLI-only: the `backup-restore` skill.

## Contracts

Wire shapes: `backend/openapi.json`. Every route below is steward-only; seven sit under `/admin/splits` and twelve
under `/admin/platform-values`.

| Route | Panel | What it does |
|---|---|---|
| `GET /admin/status` | Record freshness, Cron health | the summary; writes nothing |
| `GET /admin/runs` | Run history | finished passes, newest first |
| `POST /admin/run-daily`, `GET /admin/run-daily/jobs/{job_id}` | Run daily now | start and poll |
| `POST /admin/backup`, `GET /admin/backup/jobs/{job_id}`, `GET /admin/backups` | Backups | start, poll, list |
| `POST /admin/price-feed/{security_id}/adopt` | Price feed repairs | adopt a listed symbol |
| `POST /admin/price-feed/{security_id}/set-symbol` | Price feed repairs | set a validated symbol |
| `POST /admin/price-feed/changes/{change_id}/undo` | Price feed repairs | undo a symbol change |
| `POST /admin/price-feed/{security_id}/acknowledge`, `POST /admin/price-feed/acks/{ack_id}/revoke` | Price feed repairs | keep unpriced; revoke |
| `POST /admin/price-feed/{security_id}/limited-history`, `POST /admin/price-feed/limitations/{mark_id}/revoke` | Price feed repairs | mark a known limitation; undo |
| `POST /admin/price-feed/{security_id}/adopt-cik`, `POST /admin/price-feed/cik-changes/{change_id}/undo` | Price feed repairs | adopt a successor CIK; undo |
| `GET /admin/insider-amendments` | Insider amendments | the buckets |
| `POST /admin/insider-amendments/{security_id}/{accession}/apply`, `/keep`, `/add`, `/link`, `/undo-pick` | Insider amendments | verdicts, link, undo the system's pick |
| `POST /admin/insider-amendments/decisions/{decision_id}/revoke` | Insider amendments | undo a decision |
| `GET /admin/insider-holds` | Held insider rows | the buckets |
| `POST /admin/insider-holds/{hold_id}/approve`, `/dismiss`; `POST /admin/insider-holds/decisions/{decision_id}/revoke` | Held insider rows | date, dismiss, undo |
| `GET /admin/splits` | Splits | the list and the unlisted jumps |
| `POST /admin/splits/{version_id}/confirm`, `/reject`, `/revoke`, `/restore` | Splits | decide a split; undo |
| `POST /admin/splits/jumps/{security_id}/{step_date}/dismiss` | Splits | dismiss a jump |
| `POST /admin/splits/jumps/dismissals/{dismissal_id}/undo` | Splits | undo a dismissal |
| `GET /admin/onboarding`, `POST /admin/onboarding/{security_id}/retry` | Onboarding | the queue; retry a failed name |
| `GET /admin/platform-values/queue` | Shared facts | the queue |
| `GET /admin/platform-values/awaiting` | none (API only) | flagged candidates waiting, newest first |
| `GET /admin/platform-values/{security_id}` | Company page | a company's values |
| `POST /admin/platform-values/candidates/{candidate_id}/approve`, `/dismiss` | Company page | decide a candidate |
| `POST /admin/platform-values/{security_id}/correct` | Company page | a steward value |
| `POST /admin/platform-values/decisions/{decision_id}/undo` | Company page | undo a decision |
| `POST /admin/platform-values/{security_id}/pin`, `/unpin` | Company page | pin or unpin the shown value |
| `POST /admin/platform-values/flags/{flag_id}/close`, `/reopen` | Company page | close or reopen a user's flag |
| `POST /admin/platform-values/{security_id}/extract` | Company page | Extract now |

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| The Admin page, the company page and every `/admin/*` route | steward | `require_super_admin` on each admin router (`app/main.py`); `tests/app/test_auth_gate.py` |
| The explanation on the company page (`POST /workbench/facts/explain`) | steward | `require_super_admin` on the route; `STEWARD_PATHS` in `tests/app/test_auth_gate.py` |
| The Data flags panel's read (`GET /data-flags`) | anyone signed in, their own workspace | `require_user`, `get_workspace` |
| The same status and splits reads through agent tools (`get_admin_status`, `get_splits`) | a steward's token | the tool scope ([AGENT_TOOLS](AGENT_TOOLS.md)) |
| The artifacts the page reads | machine (the nightly and its jobs) | the cron sidecar |
| Restore | operator, by hand | no route exists |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `admin_missed_window` (`ALPHADECK_ADMIN_MISSED_WINDOW`; 0 disables the hole scan) | `domain/settings.py` | yes, backend |
| `cron_run_at` (`ALPHADECK_CRON_AT`), `cron_run_timeout_s` (`ALPHADECK_CRON_RUN_TIMEOUT_S`) | `domain/settings.py` | yes, both services |
| `backup_stale_hours` | `domain/settings.py` | no |
| `onboard_stalled_after_h`, `onboard_daily_max_names`, `onboard_max_attempts` | `domain/settings.py` | no |
| `insider_amendment_review_horizon_days` (pins the review line) | `domain/settings.py` | no |
| The job time-to-live settings (`daily_job_*_ttl_s`, `backup_job_*_ttl_s`) | `domain/settings.py` | no |

The feed thresholds are in [FEED_MONITORS](FEED_MONITORS.md); why "no" means the code default holds is in
[OPERATIONS](OPERATIONS.md), "Settings".

## Known limits

- A card archived and later restored reads as expected on the nights it sat archived (`archived_at` is one stamp).
- The loud `platform_rows_in_workspaces` line names `pipeline.v2_cutover move-platform-facts`, which refuses the
  production database; on prod a non-zero count needs a repair decision instead.
- The `--adopt` CLI and the company-list finalize write `price_symbol` without a change row, so an undo on the price
  card refuses rather than overwrite them.

## Related

[FEED_MONITORS](FEED_MONITORS.md), [FEED_LOOP](FEED_LOOP.md), [CRON_SIDECAR](CRON_SIDECAR.md),
[PLATFORM_VALUES](PLATFORM_VALUES.md), [FILINGS](FILINGS.md), [EDITIONS](EDITIONS.md), [SCOREBOARD](SCOREBOARD.md),
[OPERATIONS](OPERATIONS.md). Skills: `cron-health`, `backup-restore`.
