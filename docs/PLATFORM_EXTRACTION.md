# Background extraction of platform values

Background extraction keeps every company's share count and cash/burn current without a click: it runs the
deterministic filing extractor on each company that is due and hands the result to the machine writer
([PLATFORM_VALUES](PLATFORM_VALUES.md)). The machine runs it in the nightly pass and in the onboarding job; the
operator runs the backfill by hand; the steward runs it for one company with Extract now. Read this before you
change when a company is extracted, the run order, the attempt record or the backfill. How the extractor reads a
filing is [EXTRACTOR](EXTRACTOR.md). Nothing here is a call input: no module of the call,
replay or editions engine names it
(`tests/platform_values/test_extraction_run.py::test_no_call_path_module_names_the_background_extraction`).

## Code

| Piece | Module |
|---|---|
| The gate (`inputs_key`, `due`), the work list, one company (`extract_one`), a run (`run_extraction`) | `platform_values/extraction.py` |
| The attempt store | `repositories/platform_extraction_repo.py` |
| The nightly step and its hand run | `pipeline/nightly_extraction.py` |
| The onboarding job's step | `pipeline/onboarding.py` (`_extraction_step`) |
| The backfill CLI | `pipeline/extract_values.py` |
| Run files in `data/extraction_runs/` | `pipeline/extraction_run_log.py` |

The code lives under `platform_values/`, not `domain/`, so it never moves the editions fingerprint.

## Who runs it

| Caller | `run_kind` | Who | Enforced by |
|---|---|---|---|
| The nightly step, last in the nightly pass | `nightly` | machine (cron sidecar) | the pass's lock |
| The onboarding job's step | `onboarding` | machine (the sidecar's `onboard_loop`) | the job's lock |
| `python -m pipeline.nightly_extraction --apply` | `nightly` | operator | refuses in the nightly window or during a pass (exit 75) |
| `python -m pipeline.extract_values --apply` | `backfill` | operator | the same refusals; advisory lock 7 |
| Extract now, `POST /admin/platform-values/{security_id}/extract` | `steward` | steward | `require_super_admin` (`tests/app/test_admin_platform_values_extract_api.py`) |

Both CLIs are dry runs without `--apply`. The Admin status line is steward-only
(`tests/app/test_admin_platform_extraction.py`). Migration 0207 also allows `get_data`, the run kind of a retired
Workbench route; nothing writes it.

## The attempt record

`platform_extraction_attempt` (migration 0207, data tenant, append-only) holds one row per attempt, whatever its
outcome, with `run_kind`, `inputs_key` and `live_fetches` (the SEC requests it made).

| `status` | Meaning |
|---|---|
| `done` | the machine writer ran; `shares_reason` and `cash_reason` hold its outcome per value type |
| `error` | the fetch or the write failed; nothing else written. `error` is one line of at most 200 characters built from the exception's class (`extraction.error_reason`), never its text, which can carry a URL or a secret |
| `busy` | another writer held the company for the whole lock wait; nothing written; not a failure |
| `skipped` | no CIK; nothing fetched |

A failure is never a candidate, so it never hides a value awaiting the steward.

**The inputs key** (`extraction.inputs_key`) names what the extractor reads: the CIK plus the accession of the
newest 10-Q, 10-K, 20-F and 40-F (`PERIODIC_FORMS`, exact form match: an amendment, an 8-K or a 6-K never changes
it). An attempt records the key from the submissions index it read; the gate computes the current key from the
stored filing index (`fact_filing_index`).

## When a company is due

`extraction.due` is pure; `work_list` calls it for every company on a live card in any workspace.

| Reason | Rule |
|---|---|
| `never` | no attempt exists |
| `new_filing` | the stored index has a periodic filing newer than the one of that form the last attempt read, public for at least `platform_extraction_hold_h` hours; or the CIK changed |
| `retry` | the last attempt was `busy`; or it failed and fewer than `platform_extraction_retry_max` attempts in a row failed on the same key |

Otherwise the company is `up to date`, `held` (a new filing not yet public for the hold) or `waiting` (it failed
`retry_max` times; it waits for a new filing or the steward).

- **Public** is the SEC acceptance, else the end of the filing date in market time; a filing not public now never
  makes a company due (invariant 1). The hold exists because companyfacts lags acceptance by hours, so a same-night
  extraction risks a stale flag.
- **A steward's failed Extract now never counts toward `retry_max`**, so failed clicks (on a stack with no SEC
  User-Agent, say) cannot leave a company waiting. A steward attempt counts for everything else: a `done` one on
  the current filings makes the company not due.
- **The backstop:** a company with no periodic filing has an empty key, so after one attempt it is not fetched
  again until one appears; a company that keeps failing stops after `retry_max` attempts on the same filings.

## Run order

`extraction.work_list` takes due companies in this order: `new_filing`, then `retry`, then `never`; within each,
pen-only companies last, then most cards first (`platform_values.cards.card_counts`), then ticker. ETFs are left
out (`extractable`: a fund's share count is `fact_fund_shares`). The list also counts what it leaves out
(`withheld_never`, `held`, `waiting`). It runs a fixed number of queries and makes no request.

**Never-extracted companies wait for a backfill.** With `include_never=None`, the nightly's and the onboarding
step's default, `work_list` holds back every never-extracted company until `backfill_applied` is true: a `backfill`
attempt with status `done` exists. A backfill whose every attempt failed, was busy or skipped unlocks nothing, nor
does a `done` attempt of another run kind. The backfill passes `include_never=True`. So the first extraction of the
whole list is the operator's backfill, dry run first, not a nightly.

## One company and a run

`extract_one(conn, company, *, client, wait_s, run_kind)` rolls back any open transaction, fetches with the company
free through the caller's client (the submissions index, then `extract_with_annual_fallback(..., values_only=True)`),
only then calls the machine writer (which takes the company lock and commits), and appends exactly one attempt
row. It never raises for a company; only a failure to write the attempt row propagates. Run twice on the same
filings it writes no second fact or candidate, and the company is then not due.

`run_extraction(connect, work, *, client, run_kind, ...)` walks a work list on one connection, fail-open per
company. Between companies it checks `should_stop()`, the count cap and the time budget; it never starts a company
when the slowest one so far would end past the budget. The rest is `deferred` and stays due. The lock wait defaults
to `ingest_lock_request_wait_s`, so one busy company cannot spend the budget. It returns an `ExtractionRun`
(`to_json()`, `admin_line()`, `attention`). Under the kill switch a `nightly` or `onboarding` run is `off`: nothing
fetched, nothing written (`KILL_SWITCHED`).

## The nightly step

`pipeline/nightly_extraction.run_nightly_extraction` is the last thing in the nightly pass
(`pipeline.daily._run_daily_pass_locked`): after every thesis's call of record is committed, the run artifact is
finished, the health page sent and the passenger legs run, so the call of record cannot move. It runs on live
passes only (a `--no-live` pass records `skipped: no-live`) and never raises into the pass: a fault is a
`status: error` block, never a failed pass or another exit status (`tests/pipeline/test_daily_platform_extraction.py`).

- **Budget** (`leg_budget`): the smaller of `platform_extraction_budget_s` and what remains of the pass's limit
  (`cron_run_timeout_s`) minus `MARGIN_S`. At zero it is `no time left`; the due companies wait a night.
- **Client:** a recurring `EdgarClient` (the recurring lane of the global SEC budget), built only when a company is
  due.
- **Output:** the pass artifact's `platform_extraction` block. The Admin status line
  (`AdminStatusOut.platform_extraction`) shows the newer of that block and the newest file in
  `data/extraction_runs/`, with no database read. It is loud (`attention`) when a company or the run failed, a
  company waits after repeated failures, or never-extracted companies are held for a backfill.
- **By hand:** `python -m pipeline.nightly_extraction` shows the work list and the budget; `--apply` runs it;
  `--no-live` makes the client cache-only. It refuses inside the nightly window or while a pass holds the
  call-of-record lock, stops between companies if either begins, and writes its summary to `data/extraction_runs/`.

## The onboarding job's step

New companies get their values from a step of the onboarding job (`pipeline/onboarding._extraction_step`), not a
leg. Why: a fast-laned promote and a company already on another card never pass through a leg of the job, and a
leg would fetch under the company lock. The step runs after the queue's batch, under the job's lock, once every
company lock is released. It takes only `never` companies from `work_list` (subject to the backfill gate), at most
`platform_extraction_onboard_max_names`, through the batch's recurring client, with what is left of the batch's
time budget. It stops between companies on the nightly window or a running pass and is off under the kill switch.
A company with a new filing or a retry is left to the nightly (`left_for_nightly`).

The step is fail-open: a fault is one failed line, and the batch's names, counts, stop reason, artifact and exit
status, the onboarding queue and every thesis stay as without it (`tests/pipeline/test_onboarding.py`). A run that
attempted a company writes `data/extraction_runs/`; `python -m pipeline.onboarding --status` shows the newest. The
sidecar's `onboard_loop` (`backend/scripts/daily_cron.sh`) fires the job outside the nightly window, so a promoted
company gets its values from the next onboarding run or the nightly.

## The backfill

`python -m pipeline.extract_values` extracts every due company by hand: `work_list(include_never=True)`, then
`run_extraction(..., run_kind="backfill")`. It is never scheduled. Flags: `--help`.

- **The dry run** (the default) reads the work list on a read-only session and the EDGAR cache on disk; it builds no
  client, makes no SEC request and writes nothing. Per company and in total it shows what a real run would fetch
  live and read from the cache by the client's own rules, with byte bounds and the request time at the global SEC
  rate, and prints what it cannot know without fetching (`CANNOT_KNOW`).
- **`--apply`** takes advisory lock 7 (`pipeline.run_lock.LOCK_ID_EXTRACTION`, one backfill at a time), refuses
  inside the nightly window (`onboarding.window_bounds`) and while a pass holds the call-of-record lock (a probe,
  never a take), and re-checks both between companies. One recurring `EdgarClient`. Resumable: a finished company
  is no longer due, and a run with nothing due writes nothing. `--max-names` and `--budget-s` bound it; `--ticker`
  scopes it, and a ticker not on a live card (or an ETF) refuses the run. It writes a run file and says whether the
  backfill gate is open before and after.
- **`--no-live`** is cache only: the client never reaches SEC and serves stale copies. A company whose documents are
  not all cached gets an `error` attempt, `not cached`, and stays due until `retry_max` such attempts. This is how a
  fork, which holds prod's EDGAR cache but no SEC User-Agent, rehearses.
- **The rescan** (`--rescan awaiting`) walks `extraction.rescan_list` instead: every company whose newest candidate
  of either value is an undecided flag the steward's queue lists (`state.awaiting`: awaiting, or pending beside a
  value), whatever `due` says. It leaves out, and counts, ETFs, companies on no live card, and a company whose other
  value's newest candidate a standing steward decision closes (approve, correct or dismiss), since a changed
  candidate would re-queue over it; the dry run names those for Extract now. The machine writer keeps every other
  decision standing: an identical candidate writes nothing, a steward's value of the same filing is
  `already_on_file`, and a pinned company gets a `pinned` candidate. Attempts carry run kind `backfill`. A
  re-extracted company still waits, so a rescan resumes with `--since`, which skips a company with a `done` attempt
  since that instant; a stopped or bounded rescan prints the instant.

| Exit | Meaning |
|---|---|
| 0 | ran (failed companies are listed, not fatal), nothing due, or the dry run |
| 1 | could not run: a live run with no `ALPHADECK_USER_AGENT`, or no database |
| 2 | a bad argument |
| 75 | refused or stopped by the window, another backfill or a running pass; the rest stays due |

Tests: `tests/pipeline/test_extract_values.py`, `tests/pipeline/test_extraction_run_log.py`.

## Settings and the kill switch

| Knob | Owner | Passed by compose? |
|---|---|---|
| `ALPHADECK_PLATFORM_EXTRACTION_MAX_NAMES` (companies per nightly run; 0 is the kill switch) | `domain/settings.py` | yes, backend and cron |
| `ALPHADECK_PLATFORM_EXTRACTION_BUDGET_S` (the nightly step's wall time) | `domain/settings.py` | yes, backend and cron |
| `ALPHADECK_PLATFORM_EXTRACTION_ONBOARD_MAX_NAMES` | `domain/settings.py` | yes, backend and cron |
| `ALPHADECK_PLATFORM_EXTRACTION_HOLD_H` | `domain/settings.py` | yes, backend and cron |
| `ALPHADECK_PLATFORM_EXTRACTION_RETRY_MAX` | `domain/settings.py` | yes, backend and cron |

Compose's fallbacks must equal the Settings defaults (`tests/domain/test_settings.py`). The kill switch stops the
nightly step and the onboarding step, never a backfill or Extract now. Both stopped steps run in the cron sidecar,
so a change reaches them through `scripts/deploy.sh --env-only cron`; the backend reads these settings only for
Extract now and the steward's due note. The nightly window, the onboarding batch budget and the pass timeout belong
to the nightly pass and the onboarding job ([FEED_LOOP](FEED_LOOP.md)).

## Known limits

- A company whose last attempt is `done` on the current filings is not retaken until a new periodic filing, even
  when the value shown is older (`older_than_on_file`); the steward presses Extract now.
- The gate reads the stored filing index, which can lag the submissions index by a night.
- The dry run's cost is an estimate.
- ETFs are not extracted (`extractable`).

## Related

[PLATFORM_VALUES](PLATFORM_VALUES.md), [EXTRACTOR](EXTRACTOR.md), [FEED_LOOP](FEED_LOOP.md),
[ADMIN](ADMIN.md), [DATA_SOURCES](DATA_SOURCES.md) (the SEC budget and cache rules).
