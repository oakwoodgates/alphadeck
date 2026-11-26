# Ingest: the per-company legs

`pipeline/ingest_thesis.py` loads the facts the detectors read for each resolved member of a thesis's basket:
Form 4 and 4/A, 8-K items, 13D/G, the filing index, prices and ETF fund shares. Fundamentals load in a
separate step. Every fact goes to the shared DATA tenant. The machine runs it: step 1 of the nightly pass
([FEED_LOOP](FEED_LOOP.md)), the Workbench promote's background load, the onboarding job for cold names, and
the operator's CLI. Read this doc before you change a leg, the price write path or onboarding. What each SEC
filing family stores and how amendments resolve: [FILINGS](FILINGS.md). Vendors, the SEC budget and cache
freshness: [DATA_SOURCES](DATA_SOURCES.md).

## Code

| Piece | Module |
|---|---|
| The per-thesis walk, the legs, `NameResult` | `pipeline/ingest_thesis.py` |
| Concurrent fetch ahead of the serial writer | `pipeline/fetch_ahead.py` |
| Form 4, 4/A, 8-K, 13D/G, filing index | `ingest/edgar/` (`form4.py`, `form4a.py`, `form8k.py`, `schedule13.py`, `submissions.py`) |
| Price leg | `ingest/prices/ingest_security.py`, `source.py`, `settle.py`, `split_normalize.py`, `restatement.py` |
| Benchmark tape | `pipeline/ingest_benchmarks.py` |
| Fund shares | `ingest/funds/` |
| Fundamentals | `ingest/fundamentals.py`, `ingest/edgar/companyfacts_gate.py`, `pipeline/ingest_fundamentals.py` |
| Company write lock | `db/write_lock.py` |
| Skipped-filing baseline | `ingest/skips.py`, `pipeline/ingest_skips.py` |
| Cold-name onboarding | `pipeline/onboarding.py`, `domain/onboarding.py`, `pipeline/split_history.py` |

## How it works

### The legs

Each member's legs run in this order, under its company lock:

| Leg | Writes | Incremental by | A failure |
|---|---|---|---|
| Form 4, then its 4/A leg | `fact_insider_txn`; `insider_amendment`, `insider_amendment_link`; held rows in `insider_txn_hold` | Accessions already stored or held (`form4.existing_accessions`) | One unfetchable or unparseable filing is skipped and counted (`form4_skipped`), never stored, so it is retried; anything else fails the leg |
| 8-K items | `fact_corporate_event` | Stored accessions | Fails the leg |
| 13D/G | `fact_activist_stake` | Stored accessions | Fails the leg; a filing for another subject or an unresolved filer is skipped and counted |
| Filing index (display only) | `fact_filing_index` | Insert-only by accession | Recorded on `filing_index_error`, never a name error |
| Split detection (nightly only) | `security_split` | One check per company per pass | The detector's fault, never a name error |
| Price | `fact_price_eod` | Bars after the latest stored, plus restated bars ([The price write path](#the-price-write-path)) | Fails the leg |
| Fund shares (ETF sleeves only) | `fact_fund_shares` | An unchanged `(date, count)` sample appends nothing | Fails the leg |

The 8-K, 13D/G and filing-index legs read the same cached submissions document the Form 4 leg just read, so
they add no enumeration request. The filing index is display only: no detector reads it and the replay mirror
leaves it out (`db.bitemporal.DISPLAY_ONLY_FACT_TABLES`), so its failure can never withhold a call.
Fundamentals run per thesis as the nightly's step 1b, filing-gated, under each company's lock; a fault is
recorded on the thesis's `fundamentals_error` and never costs the record.

### Write rules

- **Exact membership** (invariant 11): each member resolves by its stored `security_id` through `master.get`,
  never a fresh fuzzy match. An unresolved member is skipped; a placed id missing from the master is reported.
- **One tenant for facts** (invariant 12): every leg writes `DATA_TENANT_ID`, never the thesis's workspace, so a
  company held in two workspaces is ingested once.
- **Honest stamps** (invariant 1): `recorded_at` is the database's `now()`, never backdated. `valid_from` is the
  date the fact became public; the 8-K, 13D/G and revenue legs date a row by its trusted SEC acceptance (rule
  `2026-11-filings-dated-by-acceptance` in `domain/rule_log.py`; [FILINGS](FILINGS.md)).
- **Per-leg isolation:** each leg runs in its own try, commits on success and rolls back on failure. A failure
  is captured on the name's `NameResult` (`error`, and `leg_errors` by leg) and the walk goes on; one bad name
  never stops the others.
- **The company lock:** the walk tries every company first, defers a held one to the end, then waits
  `ingest_lock_wait_s`. A company still held is not ingested by this writer (`lock_busy`, naming the holder).
  It is not an error, so it never withholds a night; the nightly stamps that thesis `ingest_fresh=false`, the
  on-promote job reports a failed job and the CLI exits 1. Lock order and ids: [FEED_LOOP](FEED_LOOP.md).
- **Known skips print once:** a filing that skipped the same way in its thesis's last covering pass is
  recorded without its line; a new one prints its accession. Only the printing changes.
- **A held insider row:** a Form 4 row with an impossible transaction date is written to `insider_txn_hold`
  in the leg's transaction instead of `fact_insider_txn`, and its line prints once. How the read counts it:
  [FILINGS](FILINGS.md).
- **Idempotent:** a re-run of a current name writes zero rows; tests count the table.

### Fetch-ahead

A live walk is latency-bound, so `pipeline/fetch_ahead.py` runs a pool of `ingest_fetch_concurrency` workers
that make each upcoming company's vendor reads ahead of the writer: its submissions index, its new Form 4 and
4/A documents, and its price pull, at most twice the pool size in companies ahead. The writer still walks the
basket in order on its one connection, under each company's lock; no worker touches the database.

The contract: a fetched read is the writer's first read of that key, made early. Its outcome, document or
exception, is handed to the legs in place of their own first read (`ReplayingEdgar`, `ReplayingPrices`), so the
legs see identical inputs, write the same facts and raise where the serial read would have. The walk stays
serial wherever a handoff could be ambiguous: a company's second basket row, a CIK or price symbol already
fetched, a company the lock deferred, a `--no-live` walk, and `ingest_fetch_concurrency = 1`, the kill switch
(an env-only cron deploy). The settle guard judges a handed-over pull at the instant the worker started it.
Cache files are written atomically (`ingest.write_atomic`). `tests/pipeline/test_fetch_ahead.py` proves serial
and concurrent walks write identical rows in every table, identical results and the same set of requests.

Every SEC request draws from the one global SEC budget and every Yahoo request from the process-wide gate;
`ingest/http.py`'s `polite_get` adds jittered backoff on 429 and transient 5xx ([DATA_SOURCES](DATA_SOURCES.md),
"EDGAR etiquette").

### Freshness

A recurring pass must never serve itself a stale cache. The nightly builds its EDGAR client with the recurring
TTL (`ingest.edgar.client.RECURRING_CACHE_TTL_S`) and passes `force_refresh` to the price leg; on that client
`companyfacts` is filing-gated, and immutable `forms/` documents cache forever. A standalone
`pipeline.ingest_thesis` and the Workbench keep the interactive TTL. The key classes, the interactive versus
recurring rule and the filing gate: [DATA_SOURCES](DATA_SOURCES.md), section "Cache freshness".

### The price write path

One implementation, `ingest.prices.ingest_security.ingest_bars_for_security`, serves the nightly, the CLI and
the steward's Workbench pull. The interactive pull stays cache-first; the nightly force-refreshes. The vendor
sits behind `ingest/prices/source.py`'s `PriceSource` ([DATA_SOURCES](DATA_SOURCES.md)). In order:

1. **Settle guard** (`settle.withhold_unsettled`): a bar for day d is stored only once the market clock passes d
   at `price_settle_time`. Why: a pull during the session returns the session so far, not a close. A withheld
   bar is dropped from both the new tail and the restatement compare, and the next settled pass appends it, so
   nothing is lost. The self-audit checks the table every night ([FEED_MONITORS](FEED_MONITORS.md)).
2. **No-session days** (`settle.withhold_closed_days`): a bar dated a weekend or NYSE closure
   (`domain.trading_days.is_scheduled_day`) is never stored. Bars stored before this rule stay, and
   `signals.base.window_prices` skips closures on read.
3. **Vendor gaps** (`gap_fill.py`; the nightly and `pipeline.fill_price_gaps` only, never the Workbench pull): a
   session the vendor served no close for joins the pull as Massive.com's raw bar when Massive.com traded it, it
   is on or after the newest split in force's first session (the raw bar is already on the list's scale) and
   before no pending split's, and it falls within `price_gap_fill_split_sessions` of that first session or the
   vendor has served nothing since it. Why: Yahoo nulls sessions around some splits (SNDK, SCNI). A vendor or
   stored bar is never replaced. Each fill's provenance is a `price_bar_fill` row (migration 0217), never split
   evidence; a later vendor close re-versions it in step 5. Tonight's session reads the nightly's grouped day;
   an older one is one range read, at most `price_gap_fill_max_names` a night, the rest deferred.
4. **Split normalization** (`split_normalize.py`): each vendor bar dated before a split in force is put on the
   split list's scale before storing. A run of bars is rescaled only when the store's own boundary proves the
   step is the vendor's, never on a read alone, because a genuine vendor re-base of a raw store reads the same
   as a doubled split. A company's first night after a split puts its history on the new scale; that night the
   vendor's own post-split bars stand in for stored ones after the step, so history the vendor serves raw again
   beside a late first post-split bar is still put back on the new scale. Counts ride
   `BarsResult.split_normalized`; a divide-back is named whenever it changes. Stored bars off the list's scale
   are repaired with `pipeline.repair_split_basis` (the `data-repair` skill).
5. **Restatements** (`restatement.py`): bars in the vendor pull's newest three sessions use the exact compare, so late
   volume lands. An older bar re-versions only when its close or volume moved past the module's tolerances. An
   older set whose every bar returns to a value the store already replaced is a revert and is held whole until
   a pull on a later market date agrees; a value the store never held applies at once, so a genuine re-base is
   never held back. Why: vendor flips back to an unadjusted basis mostly persist, and the hold keeps a one-night
   flip out of the tape. The pending hold is an operational row in `price_restatement_hold` (migration 0047);
   `fact_price_eod` stays insert-only. A hold is never silent: `price_bars_held` on the artifact and one quiet
   line.
6. **Append:** new and restated bars go in as new versions stamped now.

The benchmark leg (SPY, IWM) stamps `recorded_at` from the database clock and compares exactly over the newest
`benchmark_restate_window` settled sessions; older benchmark bars are never re-versioned by a pass. It feeds
only the display relative-strength column.

Fundamentals are stamped at the database clock once per company batch and dated by their filing; the table's
natural key is one version per filing plus re-parses (migrations 0057, 0060), and
`db/bitemporal.first_knowable_versions` picks the latest filing. Rules: [DATA_SOURCES](DATA_SOURCES.md).

### Cold names (onboarding)

A cold name is a company never loaded: no `security_onboarding` row (migration 0059) and no other live basket
holds it. Its first ingest walks its whole filing history, which is far too slow for the nightly window, so the
history loads outside it. The queue is one row per company on the DATA tenant, `queued`, `in_progress`, `done`
or `failed`, with a per-leg checkpoint (`legs_done`); it is operational state, updated in place, while the facts
it leads to stay append-only.

- **The promote's lane** (`onboarding.plan_promote`). A promote enqueues its cold names. With at most
  `onboard_fast_lane_max` of them, outside the nightly window, it takes the fast lane: the background on-promote
  load (`ingest_thesis`, fetch-ahead included) loads them now and marks each row from its results. Otherwise
  the names are queued and the response carries `onboarding` instead of the ingest job. A plain user's fast
  lane spends their daily allowance of new names ([AUTH](AUTH.md), "User-triggered fetches"); without enough
  left, the names queue. The SPAC and fund radar attaches use the same helper (`start_member_load`). A queue
  fault falls back to loading every new name at once. Why the limit: its worst case must finish inside the
  on-promote job's reaper TTL and before the window opens (the setting's comment).
- **The job** (`python -m pipeline.onboarding`, fired by the sidecar every `ONBOARD_EVERY_S`). It walks queued
  and in-progress rows that a live basket holds, a resumed row first, then the oldest; a row no live basket holds
  is parked. Per name, under its company lock (tried once; a held company waits for the next run), it runs the
  legs not yet checkpointed, the same leg functions the nightly runs, and commits each leg before its
  checkpoint, so a crash resumes at the next leg. The history legs (Form 4 and 4/A, 8-K, 13D/G) decide `done`;
  the tail legs (filing index, price, fund shares, fundamentals) never block it. A history-leg failure
  re-queues the name; `onboard_max_attempts` failures mark it `failed`, loud on Admin, where the steward's
  Retry re-queues it with its checkpoint.
- **The window and the yield.** The job refuses to start inside the nightly window (`onboard_window_before_min`
  and `onboard_window_after_min` around `RUN_AT`) and stops between names and legs when it enters it, when a
  pass holds the call-of-record lock (probed, never taken), or when its batch budget (`onboard_batch_max_names`,
  `onboard_batch_budget_s`) or the daily cap (`onboard_daily_max_names`) is spent. A stopped name keeps its
  checkpoint. It takes its own lock (id 3) and, per name, the gate and company lock like any writer.
- **The SEC lane.** The job's one EDGAR client is built on the recurring TTL, which puts it in the SEC
  budget's recurring lane, so a Workbench discovery run keeps its share. It runs its legs serially; only the
  fast lane gets the fetch-ahead.
- **Two more steps per batch.** At the start, `pipeline/split_history.py` fills the split history of every live
  member that has none (`security_split_history`, migration 0067; needs `MASSIVE_API_KEY`). After the queue,
  the job extracts platform values for never-extracted companies ([PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md), "The
  onboarding job's step").
- **The nightly's treatment.** The pass reads the pending rows once (a failed read walks every member in full).
  A member still queued, in progress or failed gets its per-filing legs windowed to filings within
  `onboard_nightly_window_days` of the as-of (`domain.onboarding.OnboardingWindow`) and its 4/A leg waits for
  the full load; its other legs run as usual. Why that window: it equals the insider cluster window, so the
  recent filings that can arm are read. The fetch-ahead plans the same filings
  (`ingest.edgar.form4a.form4_leg_filings`; `tests/pipeline/test_onboarding_fetch_ahead.py`). The thesis records
  `ingest_fresh=false`, and the artifact and report name the names. At most `onboard_nightly_max_names`
  distinct names get the windowed walk per pass; the rest are deferred: listed, but no lock, fetch or write, and
  left out of the stale-feed judgment.
- **History is unaffected.** Facts loaded later keep their own public times, so history reads see the name's
  whole history (invariant 1); only the call rows those nights wrote differ, which is why they say
  `ingest_fresh=false`. The editions nightly holds a thesis with a member still onboarding.
- **The run record:** `data/onboard_runs/<started>.json` (`pass_kind: onboard`), a separate folder from the
  nightly's, so no nightly reader mistakes it for a pass. A refusal or an idle run writes nothing.

## Contracts

- `NameResult` (`pipeline/ingest_thesis.py`) is what the pass, the monitors and the onboarding marks read: each
  leg's error, the skip counts, the feed edges (`tape_edge`, `fund_shares_edge`), the EDGAR facts the identity
  monitors need (`edgar_tickers`, `edgar_cik`, `edgar_completed`), `price_not_found` and `lock_busy`. A monitor
  judges these facts; the ingest never judges them itself ([FEED_MONITORS](FEED_MONITORS.md)).
- Every write goes to the DATA tenant under the fact-writer gate and one company lock; ids and lock order:
  [FEED_LOOP](FEED_LOOP.md), "Concurrency and locks".
- A display-only fact table (`db.bitemporal.DISPLAY_ONLY_FACT_TABLES`) is never read by a detector and stays out
  of the replay mirror.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| Promote with new names (fast lane or queue) | Any signed-in user, for their workspace's thesis | `require_user` on the Workbench router; the allowance (`app/allowance.py`) |
| `GET /workbench/theses/{thesis_id}/onboarding` | Any signed-in user | `require_user` on the Workbench router |
| `POST /workbench/securities/{security_id}/ingest-prices` | Steward | `require_super_admin` on the route; `tests/app/test_user_fetches.py` (`test_the_price_pull_is_steward_only`) |
| Onboarding panel and Retry: `GET /admin/onboarding`, `POST /admin/onboarding/{security_id}/retry` | Steward | `require_super_admin` on the router; `tests/app/test_auth_gate.py` |
| `python -m pipeline.onboarding` (`--status`, `--enqueue-thesis`, `--retry-failed`) | Machine (the sidecar); the operator by hand | Shell access; refuses inside the nightly window |
| `python -m pipeline.ingest_thesis`, `pipeline.ingest_fundamentals`, `pipeline.ingest_benchmarks` | The operator | Shell access |
| `python -m pipeline.fill_price_gaps` (`--ticker`, `--since`, `--apply`) | The operator | Dry run by default; `--apply` takes the call-of-record lock and refuses inside the nightly window |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `ALPHADECK_INGEST_FETCH_CONCURRENCY` | `domain/settings.py` | Yes (backend, cron) |
| `ALPHADECK_SEC_GLOBAL_RATE_PER_SEC`, `ALPHADECK_YAHOO_RATE_PER_SEC` | `domain/settings.py` | Yes (backend, cron) |
| `ALPHADECK_USER_AGENT`, `MASSIVE_API_KEY` | `domain/settings.py` | Yes (backend, cron) |
| `ALPHADECK_PRICE_SETTLE_TIME`, `ALPHADECK_BENCHMARK_RESTATE_WINDOW` | `domain/settings.py` | No |
| `ALPHADECK_INGEST_LOCK_WAIT_S` | `domain/settings.py` | No |
| `ALPHADECK_ONBOARD_*` (fast lane, window, batch, daily cap, attempts, nightly window and cap) | `domain/settings.py` | No |
| `ALPHADECK_PRICE_GAP_FILL` (`split`, `all`, `off`), `_SPLIT_SESSIONS` (20), `_MAX_NAMES` (10) | `domain/settings.py` | Yes (backend, cron) |

## Known limits

- Form 4s filed before EDGAR's XML era are text documents the leg cannot parse; they are skipped and counted
  every pass, never stored.
- A name added without the promote path (the demo seed, a test, hand SQL) gets no onboarding row and a cold
  walk inside the pass; `pipeline.onboarding --enqueue-thesis` queues it.
- The cold test treats membership of another live basket as proof the history is loaded. A name that entered
  that basket by a non-promote path moments before is the exception.
- A vendor that serves one bar per pull gives the restatement compare no overlap, so a provisional close is
  never corrected and a missed night is a permanent hole ([FEED_MONITORS](FEED_MONITORS.md), "Known feed
  limitations").
- Benchmark bars stored by the old nightly leg, and fundamentals rows written before migration 0057, keep
  their backdated `recorded_at`. History reads use the public clock, so neither moves a call.

## Related

[FEED_LOOP](FEED_LOOP.md), [FEED_MONITORS](FEED_MONITORS.md), [DATA_SOURCES](DATA_SOURCES.md),
[FILINGS](FILINGS.md), [TENANT_SCOPES](TENANT_SCOPES.md), [ADMIN](ADMIN.md), [INVARIANTS](INVARIANTS.md).
