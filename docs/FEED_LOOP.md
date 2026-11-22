# Feed loop: the nightly pass

The nightly pass refreshes the shared platform facts for every company on a live card and appends each
thesis's call of record to the immutable `calls` log. The machine runs it: the cron sidecar fires
`python -m pipeline.daily` after the close on every market session ([CRON_SIDECAR](CRON_SIDECAR.md)), and a
steward can start the same pass from the Admin page. Read this doc before you change `pipeline/daily.py`,
`calls_repo.record_if_changed`, the run locks or the push policy. The per-company ingest legs are in
[INGEST](INGEST.md); the monitors, pages and the run artifact are in [FEED_MONITORS](FEED_MONITORS.md). No
model runs on this path (invariant 3): every fact is a filing or a price bar.

## Code

| Piece | Module |
|---|---|
| CLI, exit codes, the past-as-of refusal, the catch-up guard | `pipeline/daily.py` (`main`) |
| The pass under the lock: pre-legs, loop, monitors, artifact, page, passengers | `pipeline/daily.py` (`run_daily_pass`) |
| The per-thesis loop | `pipeline/daily.py` (`run_daily`) |
| The Admin "Run daily now" job and its bounded child process | `pipeline/daily_job.py`, `pipeline/bounded_pass.py` |
| Recording | `repositories/calls_repo.py` (`record_if_changed`, `_canonical`) |
| Locks | `pipeline/run_lock.py`, `db/write_lock.py` |
| Push policy, digest and destinations | `notify/policy.py`, `pipeline/push_policy.py`, `notify/__init__.py` |
| Run artifact and the catch-up guard | `pipeline/cron_run_log.py` (`already_ran_live`) |
| On-time window and lateness | `pipeline/night_coverage.py` |
| Missed-night reconstruction | `pipeline/backfill.py` |

## How it works

### The pass, in order

`run_daily_pass` takes the call-of-record lock, writes a `running` start artifact, then runs these legs:

| Order | Leg | Code | On a fault |
|---|---|---|---|
| a | Benchmark tape (SPY, IWM) | `pipeline/ingest_benchmarks.py` | Fail-open; counted on the artifact and paged |
| b | Baselines read from earlier artifacts | `cron_run_log.previous_*` | Fail-open |
| c | Form 4/A sweep: a system decision for every resolvable amendment that has none | `insider_amendment_scan.run_amendment_sweep` | Fail-open, printed |
| d | Split detection start (one Massive.com request) | `pipeline/detect_splits.py` | Fail-open, a status on the artifact |
| e | The per-thesis loop (next section), then one digest per workspace | `run_daily` | Per thesis |
| f | The monitors | [FEED_MONITORS](FEED_MONITORS.md) | Each fail-open |
| g | The self-audit | `pipeline/self_audit.py` | A failed check pages; the pass goes on |
| h | Artifact finalized as `finished` | `cron_run_log.write_cron_run_log` | Fail-open |
| i | The health page | `assess_health` | Fail-open |
| j | Passenger legs: shell sweep, SPAC radar, fund radar | `radar/shell_sweep.py`, `radar/spac.py`, `radar/funds.py` | Fail-open |
| k | Platform-value extraction, last | `pipeline/nightly_extraction.py` | Fail-open; its block is added to the finished artifact |

Legs a, j and k run on a live pass only. Each fail-open leg uses its own connection, so its fault cannot
reach the per-thesis counters or the recording gate. Leg k budgets against what is left of the pass's time
limit ([PLATFORM_VALUES](PLATFORM_VALUES.md), "Background extraction"); the fund radar is
[FUND_RADAR](FUND_RADAR.md)'s.

#### The per-thesis loop

For each thesis in `thesis_repo.list_all` (every workspace; archived theses are skipped):

| Step | What | Rule |
|---|---|---|
| 1 | Refresh facts: `ingest_thesis` with a recurring EDGAR client and `force_refresh` | [INGEST](INGEST.md). A thesis-level raise is captured on the thesis |
| 1b | Refresh its members' fundamentals (filing-gated `companyfacts`) | Live passes only. A fault lands on `fundamentals_error`, never `error`, so it never withholds the call |
| 2 | Assemble the call without writing: `call_for_thesis(record=False)` at the as-of, `known_at` now | The same assembler every read uses (invariant 13) |
| 3 | Detect a transition and notify | The state or verdict differs from the latest call of record at or before this as-of. The event goes to the thesis's workspace destinations under the push policy ([Notifications](#notifications)) |
| 4 | Record with `record_if_changed` | Gated ([Recording the call](#recording-the-call)); committed per thesis |

One thesis's failure is captured on its `ThesisRunResult` and never stops the loop. Step 3's baseline is the
row `record_if_changed` compares against: `asof <=` this as-of, never `<`. A second pass on the same day
therefore never re-announces a move, "unchanged card, no row" and "unchanged card, no page" stay one rule,
and a catch-up for a past night compares against that night's own row, never a later one.

### Tenancy

The ingest legs and master reads use the DATA tenant (`domain.scopes.DATA_TENANT_ID`). The call reads through
`PointInTimeData(data_tenant=..., workspace=thesis.tenant_id)` and the call of record appends under the
thesis's workspace. The pass refuses to start without a valid data tenant (`scopes.assert_data_tenant`). Rules:
[TENANT_SCOPES](TENANT_SCOPES.md), invariant 12.

### Recording the call

`calls` is immutable (a `no_update` trigger) and its `(thesis_id, asof)` index is not unique, so an upsert is
impossible. `record_if_changed` reads, compares, then appends:

- It compares the card with the latest row for `(thesis, asof)`. No row, or a different card, appends exactly
  one row; an identical card appends nothing.
- `_canonical` sorts dict keys and list elements and rounds floats, so a reorder or a jsonb round trip never
  reads as a change.
- `ingest_fresh`, `ingest_errors`, `reconstructed`, `config_hash`, `code_sha` and `run_kind` ride the write
  outside the compare. A deploy or a dial edit alone never re-records an unchanged card; the stamps belong to
  the run that first recorded that card.
- The recording gate (`withheld_reason`): a `--no-live` pass, an ingest that raised, or a thesis whose every
  name errored skips assemble, notify and record. A partial failure records with `ingest_fresh=false`, and so
  does a thesis with a name another writer held past the lock wait or a name still onboarding.
- `run_kind` is `cron` only when the sidecar passes `--run-kind cron`; the CLI default is `manual` and
  `pipeline.backfill` writes `backfill`. It is a flag, never an env var: the sidecar and the operator type the
  same command, so only the flag tells them apart. `code_sha` is the image's `ALPHADECK_IMAGE_SHA`, `NULL` when
  unknown, never invented.

**Adding a CallCard field.** The prior row is parsed through the current model before the compare, so an old
row reads the new field as its default and only cards where the field is not its default re-record. A new
as-of always appends, so the churn that matters is a same-as-of re-run across the deploy: ship card changes
after a clean nightly. Rollback hazard: `DomainModel` is `extra="forbid"` (`domain/base.py`), so once a row
carrying the new field is recorded, older code cannot parse it and the log cannot be rewritten. Roll back past
such a change by keeping the field in the model, never by reverting it wholesale.

### Notifications

Step 3's destination is per user of the thesis's workspace (`notify.notifier_for_workspace`, fed once per pass
by `user_meta_repo.webhooks_by_workspace`): every enabled user's own webhook from the Settings page,
deduplicated; else the env `SLACK_WEBHOOK_URL` only when the workspace holds an enabled super admin; else the
log. A plain user's workspace never reaches the env channel. Health and crash pages always use the env notifier
and carry counts and labels, never a card name.

The push policy (`notify/policy.py`, pure; inverse loudness, invariant 7):

| Move | Push |
|---|---|
| Into Armed from any other state | Loud |
| Out of Armed, or any move short of it | Recorded, never pushed |
| Armed to Armed, grade or armed-name count rose and nothing fell | Loud upgrade |
| Armed to Armed, something fell and nothing rose | Quiet downgrade, naming the names no longer armed and why |
| Armed to Armed, moves in opposite directions or neither moved | Quiet, both stated |
| Names newly armed inside a thesis that stayed Armed | One quiet digest per workspace per pass, first-time arms before re-arms (`push_policy._EVER_ARMED_SQL`) |

Every push lists names (the ticker, else the company name, else the security id), never a count. A push for a
past or late night says so in its header (`TransitionEvent.timing`). The SPAC radar sends one push per
announced company per workspace. `pipeline.backfill` imports no notifier.

### Missed and late nights

- **Never re-run a past night with `pipeline.daily --asof <past>`.** It would assemble that night with today's
  knowledge and append a scored row, so the CLI refuses it (exit 2, `past_asof_refusal`). The tool for a hole
  is `python -m pipeline.backfill --asof <night>`, with `--dry-run` first. It reconstructs the night on the
  public clock (invariant 1), marks every row `reconstructed`, never ingests and never pushes. It skips a thesis
  created after the night and reads the roster from `basket_snapshot` where one exists. The Scoreboard reports
  reconstructed rows and never scores them ([SCOREBOARD](SCOREBOARD.md)).
- `--catch-up` is the sidecar's guarded run. It is a no-op when a finished, live pass for that as-of started at
  or after the night's `RUN_AT` and recorded for at least one thesis (`already_ran_live`). A `running` or
  `crashed` artifact never counts, nor does a pass started before `RUN_AT`. `main` re-checks the guard under
  the lock.
- A catch-up skips every thesis that already holds an on-time, non-reconstructed row for the night (no
  ingest, no assemble, no write) and records only the missing ones. The on-time window runs from the night's
  `RUN_AT` to EDGAR's next open, 06:00 market time on the next weekday (`night_coverage.on_time_window`). The
  scheduled run and "Run daily now" never skip: they re-version on purpose.
- A row recorded after that window is late (`night_coverage.is_late`, derived from `recorded_at`, never
  stamped). The pass writes it like any row, and the Scoreboard sets it aside by default. A Friday night's
  window runs to Monday 06:00, so a weekend catch-up is on time.
- A deploy never backfills older holes: the sidecar's catch-ups cover the last expected night at boot and walk
  forward from the target after a wake ([CRON_SIDECAR](CRON_SIDECAR.md)).

## Contracts

### CLI and exit codes

`python -m pipeline.daily` takes `--asof`, `--no-live`, `--catch-up`, `--late-ok` and `--run-kind`; see
`--help`.

| Exit | Meaning |
|---|---|
| 0 | The night has a call of record for at least one thesis: a quiet unchanged night, a partial night, an empty roster, a `--no-live` pass, or a `--catch-up` no-op |
| 1 | The night has no record: the pass crashed, or every thesis was withheld or errored for a real reason. The sidecar retries on it |
| 2 | Refused before anything ran: a past `--asof` without `--catch-up`, `--late-ok` or `--no-live`, or a malformed setting |
| 75 | Did not run: another process holds the call-of-record lock. Nothing written, nothing paged |

Per-thesis errors print and page but never change the exit code (`_failed_to_record`).

### Concurrency and locks

Every lock is a Postgres session-level advisory lock, so a killed holder's lock dies with its connection. The
run-lock namespace is `0x41444B`:

| Id | Lock | Taken by |
|---|---|---|
| 1 | Call of record | The pass, start to finish; a real `pipeline.backfill` run; every fact repair, before lock 2 |
| 2 | Fact-writer gate | Shared by every company writer; exclusive for a repair (`run_lock.acquire_repair_lock`) |
| 3 | Onboarding job | `pipeline.onboarding` |
| 4 | Editions nightly | `editions.run` |
| 5 | Company-list refresh | `pipeline.populate_master --apply` |
| 6 | Term mentions | `pipeline.term_mentions --apply` |
| 7 | Platform-value backfill | `pipeline.extract_values --apply` |

The company lock is `(0x414443, the first 32 bits of security_id)`, exclusive, taken with lock 2 shared
(`db/write_lock.py`). Lock order, the deadlock rule: call of record, then the gate, then one company; never the
reverse, never two companies at once. Every acquisition is a try or a bounded poll of tries, so no session
blocks inside Postgres. Pinned by `tests/db/test_write_lock.py` and `tests/pipeline/test_ingest_write_lock.py`.

Locks 3 to 7 only probe lock 1 (`run_lock.describe_holder`) and never take it, so they can never make a pass
exit 75. The Workbench and CLI ingests write facts, never `calls`: they take the gate and a company lock only.
A batch writer waits `ingest_lock_wait_s` for a held company, the Workbench price pull waits
`ingest_lock_request_wait_s` then answers 409, and a repair waits `repair_lock_wait_s` for writers to drain then
exits 75. Every pass connection carries `cron_statement_timeout_s`.

Writers left unserialized: the boot demo seed (one transaction, first boot only), the steward's stored-fact
and Form 4/A decisions, the pass's Form 4/A sweep (decisions only, under lock 1) and the SPAC radar's
`fact_spac_*` tables (one writer). The deploy wrapper checks lock 1 only.

### Idempotency

Every ingest leg is incremental and every write reads, compares and appends, so a re-run appends nothing.
Tests count the table, never the read, because the bitemporal read hides a duplicate
(`test_daily_idempotent_end_to_end_count_the_table`).

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `python -m pipeline.daily` | Machine (the sidecar); the operator by hand | Shell access to the stack; the refusals and locks below |
| "Run daily now": `POST /admin/run-daily`, `GET /admin/run-daily/jobs/{job_id}` | Steward | `require_super_admin` on the admin router (`app/main.py`); `tests/app/test_auth_gate.py` |
| `GET /admin/status`, `GET /admin/runs` | Steward | Same |
| `python -m pipeline.backfill --asof <night>` | The operator | Shell access; refuses tonight and later |
| `PUT /account/notifications/slack-webhook` (the caller's own webhook) | Any signed-in user | `require_user` on the account router; `tests/app/test_auth_gate.py` |

"Run daily now" runs the same `run_daily_pass` in a child process bounded by `Settings.cron_run_timeout_s`. It
answers 409 while another process holds the call-of-record lock or another "Run daily now" job is running.

## Settings

| Knob | Owner | Passed by compose to cron? |
|---|---|---|
| `ALPHADECK_CRON_AT` (`cron_run_at`) | `domain/settings.py` | Yes |
| `ALPHADECK_MARKET_TZ` (`market_tz`) | `domain/settings.py` | Yes |
| `ALPHADECK_CRON_RUN_TIMEOUT_S` | `domain/settings.py` | Yes |
| `ALPHADECK_CRON_STATEMENT_TIMEOUT_S` | `domain/settings.py` | No |
| `ALPHADECK_INGEST_LOCK_WAIT_S`, `ALPHADECK_INGEST_LOCK_REQUEST_WAIT_S`, `ALPHADECK_REPAIR_LOCK_WAIT_S` | `domain/settings.py` | No |
| `SLACK_WEBHOOK_URL` | `domain/settings.py` | Yes |

## Known gaps

- A night with no record stays a hole until someone acts. The sidecar retries a failed run once and catches up
  only the last expected night at boot, so a multi-night outage leaves older holes; Admin lists them
  (`gappy`), and the tool is `pipeline.backfill`.
- Some members' closes, mostly OTC, are not at the vendor yet at `RUN_AT`. That night's call rests on a
  slightly incomplete tape and the next pass appends the late bars; accepted rather than moving the pass to the
  morning.
- `python -m pipeline.run` appends a `calls` row unconditionally (`call_for_thesis(record=True)`), outside the
  call-of-record lock, with `run_kind` `manual` and no `reconstructed` mark. Whether it should write at all is
  open.
- `latest_for_thesis` includes reconstructed rows, so a catch-up of a night that already holds a reconstructed
  row compares its transition against that row.

## Related

[INGEST](INGEST.md), [FEED_MONITORS](FEED_MONITORS.md), [CRON_SIDECAR](CRON_SIDECAR.md),
[DATA_FLOW](DATA_FLOW.md), [CALL_LOGIC](CALL_LOGIC.md), [SCOREBOARD](SCOREBOARD.md), [ADMIN](ADMIN.md),
[INVARIANTS](INVARIANTS.md). The repair and backfill CLIs: the `data-repair` skill.
