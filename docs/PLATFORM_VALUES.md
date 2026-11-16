# Platform values: the share count and cash/burn (V2)

Not a rule doc. This page says how a company's two **platform values** are stored, picked and labeled on the
`v2` branch. The rules of the call live in `docs/CALL_LOGIC.md`; neither value is a call input.

A platform value is one value per company for everyone. It lives in the data tenant (`docs/TENANT_SCOPES.md`,
`domain/scopes.py`):

| Value type (`fact_type`) | Fact table | Read by |
|---|---|---|
| `shares_outstanding` | `fact_shares_outstanding` | the market cap (Workbench, Hot Board, Insider Tracker, Peer Table, Theme Leaderboard, Ticker Lookup, Market Map, Track Record, the data flags) |
| `cash_burn` | `fact_cash_burn` | the runway meter (Workbench), the Peer Table |

`cash_usd` is one "cash and short-term investments" number (cash + equivalents + short-term investments +
long-term marketable securities; never the generic long-term investments line: `docs/WORKBENCH_EXTRACTION.md`).
The extractor also carries the investment part on its own (`ExtractedFact.investments_usd`) and names every
component in the note.

**The stored currency and investment part** (migration 0208; operator decisions D4 and D1 (d), 2026-11-15).
`fact_cash_burn.currency` is the statement's ISO code (three capital letters; the `*_usd` columns then hold that
currency's figures, never converted) and `fact_cash_burn.investments_usd` the investment part already inside
`cash_usd`; `extraction_candidate.investments_usd` is the same on the candidate (whose currency is its
`statement_currency`). Both are nullable with no default and no backfill: NULL means **not recorded** (a V1 row, a
periodic value applied before the PR 6 follow-up, an annual value whose currency the extractor could not establish, a
correction that stated none), never an invented `USD` or 0. They are set on the INSERT of a new version only, on
every write path: the machine's apply and its candidate, the steward's approve (the candidate's own) and correct
(the steward states them: an ISO code, a part never negative and never above the cash, else 422), and an undo
falls back to the earlier version with its own. Every screen that prints a cash or burn figure labels it in
the stored currency through ONE rule (`?` for an annual-statements value stored without one, `$` for a quarterly
value or a V1 row without one; never a conversion): `backend/domain/cash_currency.py` `currency_symbol` for every
backend printer (the Filing Stream row, the pace caveat on the scored meter, the Peer Table cell and the steward
page) and its twin `frontend/src/components/currency.ts` (`labelCurrency` / `storedCurrency` / `cashSymbol`) for
every screen (the steward pages, the Workbench facts panel and rail, the scored row, the Cockpit name panel, the
Peer Table). Where there is room (a hover, a detail line) they add "of which short-term investments X" when a part
is stored. The runway in months is a ratio and unchanged. Display only: nothing under `calls/` or `signals/` reads them.
The periodic 10-Q/10-K path reads companyfacts' USD unit only, so a value it read records `USD` explicitly
(`statement_currency='USD'` on the extraction, `currency='USD'` once applied); a result that read nothing records
none. Rows applied before that stay NULL (no backfill) and still read `$`.

**The stored year-to-date pace** (migration 0209; operator decision D3's sub-decision, a column). `fact_cash_burn`
and `extraction_candidate` carry `ytd_quarterly_burn_usd` (the year to date ending on the burn's end, scaled to one
quarter, burn sign, in the value's own currency) and `ytd_burn_days` (its span): both or neither, nullable, no
default, no backfill (NULL = not recorded). They ride the same write paths as 0208's columns, on the INSERT of a new
version only: the machine's apply and its candidate, the steward's approve (the candidate's own), correct and the
Workbench confirm (stated, both or neither, else 422), and an undo falls back to the earlier version's. A
correction that does not state the pace stores none, and the machine-written pace segment and caveat are dropped
from the note it keeps (`domain.burn_pace.drop_pace`), so an old pace is never re-applied to a corrected burn. One
reader, `domain.burn_pace.stored_pace`, reads a row's pace: the columns, else (a row written before 0209) the
note's segment. The caveat sentence is never stored: the pure rule computes it on read from the stored numbers,
and it never changes a tier. Display only.

## The one pick

The as-of read returns one row per filing (`source_ref`; the newest version of each, set-aside versions skipped).
The value shown is `domain.platform_values.pick_current` over those rows (operator decision D1 (a), 2026-11-14):

1. the latest `valid_from` wins;
2. on a date tie, the newest write wins: `recorded_at`, then `id` (on a history read, `ingested_at` is the write);
3. a row dated after today is never picked.

Every reader that shows "the value" calls it. `tests/dashboards/test_one_pick.py` fails on a reader that picks by
its own rule. Before V2 each reader kept the first row on a date tie, so the alphabetically first filing URL won.

## Who set it: the `ratified_by` vocabulary

Migration 0206 fixes the vocabulary on every new row (added NOT VALID: stored rows are not judged until W10
validates the constraint):

| `ratified_by` | Who | `ratifier_kind` |
|---|---|---|
| `auto` or NULL | the machine applied a clean filing value | `machine` |
| `operator` | a V1 row carried over | `carried` |
| an email (`x@y`) | the steward | `steward` |

## The four states

`platform_values.state.read_state(conn, security_id, fact_type)` reads, in the data tenant only:

- `P` = the picked value (record clock, as of today's market date, known now): exactly what the meters show;
- `C` = the newest candidate for the company and type (`extraction_candidate`, migration 0200);
- whether a standing decision (an approve, correct or dismiss no undo names) closes `C`;
- the pin: the newest `steward_pin` event is `pin`.

Then `domain.platform_values.derive_state` gives the state:

| Condition | State | Extra |
|---|---|---|
| `P`, set by `auto` / NULL | `from_filing` | ratifier `machine` |
| `P`, set by `operator` | `verified` | ratifier `carried` |
| `P`, set by an email | `verified` | ratifier `steward` |
| `P`, and `C` an undecided flag dated after `P` | the state of `P` | `pending` = `C` |
| no `P`, no `C` | `no_value` | reason `not-extracted` |
| no `P`, `C` a "nothing" | `no_value` | reason = `C`'s own (`no-annual-filing`, `cash-generative`, ...) |
| no `P`, `C` an undecided flag | `awaiting_steward` | `awaiting` = `C` |
| no `P`, `C` a dismissed flag | `no_value` | reason `dismissed` |

**Awaiting the steward never reaches a meter.** A candidate is not a fact: it is in no as-of read and no mirror
(`tests/db/test_v2_schema.py`), and `DerivedState.meter_value` returns only `P`.

`read_states(conn, security_ids, fact_type)` is the same read for a set of companies, in a fixed number of queries
whatever the set's size (one point-in-time view prefetched over the set, the newest candidate per company, their
standing decisions, the newest pin per company), then `pick_current` and `derive_state` per company. `read_state`
is `read_states` for one company, so there is one way to read a state; `tests/platform_values/test_state_batched.py`
pins that the batch equals the per-company reads in every state above and in the edge cases (a workspace row, a
value dated after today, an undone approve or dismiss, a reopened older candidate, a pin then an unpin).

`read_company` adds every stored version (with `set_aside`), the newest 20 candidates, the newest 50 decisions and
the newest pin event; `awaiting` lists the open flags the state says still wait, newest first (one `read_states`
batch per value type); `awaiting_companies` groups them one row per company.

**A reopened older candidate waits nowhere.** The state reads only the newest candidate per company and type. If
the steward undoes the dismiss of an older candidate while a newer one stands, the older one is open again but is
not `awaiting`, not `pending`, and not in either queue; it shows only in the company read's `candidates` (its
`decision` is None).

## The machine writer (W2b)

`platform_values/machine.py`, `record_extraction(conn, security_id, result, *, wait_s, cache_dir=None, cik=None,
today=None) -> {fact_type: MachineOutcome}`, is the only code that turns the deterministic extractor's parse into a
platform value. It takes an `ExtractionResult` already fetched (no network inside) and, for each type:

| Extractor result | What is written |
|---|---|
| AUTO with its number(s) | `machine_action` decides: a fact stamped `auto` (applied), a `flag` candidate with flag `pinned` (the company is pinned), or nothing: `already_on_file` (any stored version of the filing, set-aside ones too, so an undone approve is never re-applied), `older_than_on_file` (dated on or before the value shown), `after_today` (never shown, so never written) |
| an annual share count (`source='annual-cover'`, a 20-F / 40-F cover) whose only flags are `annual-cover` / `stale-cover`, ADS ratio `known` or not applicable, at most `annual_shares_auto_max_age_days` (400) old | treated as AUTO (the annual share-count rule, below): the same `machine_action` |
| FLAG, or AUTO with no number | a `flag` candidate: raw values, flags, filing, date, located passages, ADS fields / statement currency and investment part |
| no value of that type | a `nothing` candidate with the extractor's reason: cash/burn takes the runway reason first (`cash-generative`, `financials-in-exhibit`, `exhibit-ambiguous`, `statements-not-located`), then the result's (`no-annual-filing`, `cover-not-located`), else `no-candidate` |
| an exception (SEC down, companyfacts 404) | nothing: the caller never gets a result to record |

A strictly newer filing is applied over a verified value unless the company is pinned (D3 (a)). A candidate that
repeats the newest candidate for that company and type (same outcome, reason, filing, date, flags and values) is
not written again. Only the extractor's own parse is read: a fact carrying an `estimate_source` (a model's
proposal) is ignored, and the module imports nothing from `llm`.

**The annual share-count rule** (the extractor correctness plan's PR 5, operator-approved 2026-11-15). Every share
count from an annual foreign filer is a FLAG by design (`annual-cover`, plus `stale-cover`, which on an annual filer
only means older than 183 days), and an audit found 30 of 30 such counts right with every ADS ratio right. So the
machine applies one when `domain.platform_values.annual_shares_auto_eligible(fact, today, max_age_days)` holds:

- a share count with a finite number and `source = 'annual-cover'`;
- its flags are a subset of `ANNUAL_ONLY_FLAGS = {annual-cover, stale-cover}`: any other flag
  (`multi-value-cover`, `dual-class`, `ads-ratio-unread`, `source-disagreement`, `implausible-count`,
  `cover-after-filing`) or a flag not named yet still queues;
- `ads_ratio_status` is `known` (with a ratio of 1 or more) or None (no ADR evidence);
- its own as-of date (`event_date`, the period of report or a fresher companyfacts date) is 0 to
  `ExtractorConfig.annual_shares_auto_max_age_days` (400, a domain dial) days before today's market date. A count
  dated after today, or older than the cap, queues with its age in the note.

`plan()` maps an eligible FLAG to `auto`; `machine_action` then applies the pin (a pinned company gets a candidate
with flag `pinned`), `already_on_file`, `older_than_on_file` and `after_today` rules unchanged. The applied fact is
`ratified_by='auto'`, `source='annual-cover'`, `valid_from` = the count's real as-of date, the ADS fields, and the
filing's accession and acceptance; its state is `from_filing`. Facts carry no flag column, so the stored note keeps
the extractor's note (the form, the as-of date, the age, the ADS ratio) and adds one sentence: "Applied automatically
as an annual figure (the annual share-count rule): a 20-F/40-F cover count as of <date>, <N> days old on <today>,
flags annual-cover, stale-cover; ...". The steward's company page says "annual figure from the 20-F or 40-F cover,
as of <date>" in the value's status line, quietly. **Share counts only:** annual cash and burn always waits for
the steward. A candidate recorded for the same filing before the rule stops waiting (the value shown has its date),
and stays in History. Tests: `tests/domain/test_platform_values.py`, `tests/platform_values/test_machine.py`.

**Lock and stamp.** The caller fetches with the company free. The writer then takes the company write lock
(`acquire_company_lock`, waiting at most `wait_s`; `CompanyLockBusy` with nothing written), re-reads the state, the
pin and every stored filing under it, writes both types in one transaction with the company's change stamp,
commits, and releases. Two simultaneous get-data clicks write one value.

**Callers.** `POST /workbench/facts/auto-confirm` (get-data; D2 (a)): same gates as before (own cards, the daily
`auto_confirm` allowance; a steward neither), extraction by `extract_with_annual_fallback` (annual filers too), the
lock wait `ingest_lock_request_wait_s` (busy is 409), an extraction failure 502. The answer's top level is the share
count's outcome, `cash_burn` the cash/burn one; the reasons are the writer's, with `flagged` spelled `not_auto`.
Background extraction (below) calls it with the request wait `ingest_lock_request_wait_s`, not the batch wait, so
one busy company cannot spend a run's budget (the W3 plan, Q5).

## Background extraction (W3a)

`platform_values/extraction.py` keeps the two values current without a click. Its callers: the nightly step,
the onboarding job's step for new companies, the backfill CLI (`python -m pipeline.extract_values`) and the
steward's Extract now, each described below and each calling the pieces below. Nothing here is a call input; no
module of the call, replay or editions engine names it
(`tests/platform_values/test_extraction_run.py`).

**The attempt record.** `platform_extraction_attempt` (migration 0207, data tenant, append-only) holds one row per
attempt, whatever its outcome (approved B3 (a)):

| `status` | Meaning | Also stored |
|---|---|---|
| `done` | the machine writer ran | `shares_reason`, `cash_reason`: its `MachineOutcome.reason` per value type |
| `error` | the fetch or the write failed; nothing else was written | `error`: one line, at most 200 characters, built from the exception's class, never its text (no URL, no secret) |
| `busy` | another writer held the company for the whole wait; nothing was written; not a failure | `detail` |
| `skipped` | nothing could be read (no CIK); nothing was fetched | `detail` |

Every row carries `run_kind` (`nightly`, `onboarding`, `backfill`, `steward`, `get_data`), `inputs_key` and
`live_fetches` (the SEC requests the attempt made). A failure is never a candidate, so it never hides a value
awaiting the steward.

**The inputs key** names exactly what the extractor reads: the company's CIK plus the accession of its newest 10-Q,
10-K, 20-F and 40-F (exact form match: an amendment, an 8-K or a 6-K never changes it), for example
`1234567|10-Q:0001-26-000002|10-K:0001-26-000001|20-F:|40-F:`. The attempt's key comes from the submissions index
the extraction read; the gate's from the stored filing index (`fact_filing_index`).

**When a company is due** (`due`, pure; `work_list` reads it for every company on a live card):

| Reason | Rule |
|---|---|
| `never` | no attempt exists |
| `new_filing` | the stored index has a 10-Q, 10-K, 20-F or 40-F newer than the one of that form the last attempt read, public for at least `platform_extraction_hold_h` hours (companyfacts lags acceptance by about 5-6 hours); or the CIK changed |
| `retry` | the last attempt was `busy`; or it failed and fewer than `platform_extraction_retry_max` attempts in a row failed on the same key (a steward's failed Extract now click is not counted) |

Otherwise it is not due: `up to date`, `held` (a new filing not yet public for the hold) or `waiting` (failed
`retry_max` times; it waits for a new filing or the steward). "Public" is the SEC acceptance, else the end of the
filing date in ET; a filing not public at `now` never makes a company due. An attempt that read a filing the stored
index does not have yet read a newer one, never an older one. **The backstop:** a company with nothing to find (no
periodic filing) has an empty key, so after one attempt it is not fetched again until a periodic filing appears in
its filing index; a company that keeps failing stops after `retry_max` attempts on the same filings.
**Steward clicks and the cap** (operator decision 2026-11-15): a failed `steward` attempt (Extract now) never counts
toward `retry_max`, so failed clicks (on a stack with no SEC User-Agent, say) cannot leave a company waiting for a
new filing. `due` skips them when it counts the failures in a row (as it skips `busy`); `work_list` hands it each
attempt's `run_kind`. A steward attempt counts for everything else: a `done` one on the current filings makes the
company not due, and the company page lists every attempt.

**The order** (`work_list`): `new_filing`, then `retry`, then `never`; within each, pen-only companies last, then
most cards first (the card count from `platform_values/cards.py`, reused, never rebuilt), then ticker. ETFs are left
out (`extractable`, one line to flip; a decision that can wait).

**Before a backfill extracts a company** (approved B4 (a), tightened 2026-11-15): `work_list` with
`include_never=None` (the nightly's and onboarding's default) holds back every never-extracted company until a
`backfill` attempt with status `done` exists (`backfill_applied`: a backfill extracted at least one company, whether
applied, a candidate recorded or nothing found), and counts them in `withheld_never`. A backfill whose every attempt
failed (`error`, `not cached` included), was `busy` or was `skipped` unlocks nothing; neither does a `done` attempt
of another run kind (steward, nightly, onboarding). The backfill passes `include_never=True`.

**One company** (`extract_one(conn, company, *, client, wait_s, run_kind)`): rolls back any open transaction,
fetches with the company free through the caller's client (the submissions index, then
`extract_with_annual_fallback(..., values_only=True)`), only then calls `record_extraction` (which takes the company
lock and commits), then appends exactly one attempt row and commits. It never raises for a company; only a failure
to write the attempt row itself (the database gone) propagates. Running it twice on the same filings writes no
second fact and no second candidate, and the company is then not due.

**A run** (`run_extraction(connect, work, *, client, run_kind, should_stop, max_names, budget_s, wait_s, clock)`):
walks the list on one connection, fail-open per company, checking between companies: `should_stop()`, the count
cap, and the time budget (it never starts a company when the slowest one so far would end past the budget). It
returns an `ExtractionRun` (counts per outcome, failures named, `deferred`, `live_fetches`, `to_json()`,
`admin_line()`, `attention` when anything failed). `pipeline/extraction_run_log.py` writes and reads the run files
in `data/extraction_runs/`.

**The onboarding job's step (W3e, approved B5 (a)).** New companies get their values from a step of the onboarding
job (`pipeline/onboarding.py`, `_extraction_step`), not from a leg: a promote of five or fewer new names is
fast-laned and a company already on another card is never queued, so a leg would miss both (the W3 plan,
correction 5), and a leg would fetch under the company lock. The step runs AFTER the queue's batch, on every batch
that got past its refusals (also when nothing is queued or the daily cap is reached), under the job's lock, when the
batch's names are finished and every company lock is released. It takes only `never` companies from `work_list`
(B4: until a backfill extracts a company they are counted as `withheld_never`, never fetched), at most
`platform_extraction_onboard_max_names` (25), through the batch's one recurring client, with what is left of the
batch's `onboard_batch_budget_s`; it stops between companies on the nightly window or a running pass, and is `off`
under the kill switch. A company already extracted (a new filing, a retry) is the nightly's (`left_for_nightly`).
Fail-open: a fault is one `FAILED` line; the batch's names, counts, stop reason, artifact and exit status, the
onboarding queue and every thesis stay exactly as without it (`tests/pipeline/test_onboarding.py`, section 9). A
run that attempted a company writes `data/extraction_runs/` (run kind `onboarding`); the job prints its one line
(`Shared facts, new companies · …`) and `python -m pipeline.onboarding --status` shows the newest one. So a new
company gets its values within about 30 minutes of a promote outside the nightly window, or from the nightly.

**Settings** (`domain/settings.py`; each passed to the backend and cron containers in `docker-compose.yml`):

| `ALPHADECK_…` | Default | Meaning |
|---|---|---|
| `PLATFORM_EXTRACTION_MAX_NAMES` | 300 | companies per nightly run. **0 = the kill switch:** a `nightly` or `onboarding` run is `off` (nothing fetched, nothing written); a backfill or steward run still runs by hand |
| `PLATFORM_EXTRACTION_BUDGET_S` | 900 | a nightly run's wall-time budget, seconds |
| `PLATFORM_EXTRACTION_ONBOARD_MAX_NAMES` | 25 | companies per onboarding run |
| `PLATFORM_EXTRACTION_HOLD_H` | 24 | hours a new filing must be public before it makes its company due |
| `PLATFORM_EXTRACTION_RETRY_MAX` | 3 | failures in a row on the same filings before a company waits (a steward's failed click is not counted) |

To turn background extraction off on prod: set `ALPHADECK_PLATFORM_EXTRACTION_MAX_NAMES=0` in `.env`, then
`scripts/deploy.sh --env-only cron`. That one recreate covers both steps the switch stops: the nightly step and the
onboarding job both run in the cron sidecar (`backend/scripts/daily_cron.sh`: the nightly pass, and `onboard_loop`
firing `python -m pipeline.onboarding`). The backend container needs no deploy for it: there the settings serve only
the steward's Extract now (a `steward` run, which the kill switch never stops; it reads the hold and the retry cap
for its due note), and the Admin line reads the run files.

**The nightly step** (`pipeline/nightly_extraction.py`, V2 W3c; approved B2 (a)): the LAST thing in the nightly
pass, after every thesis's call of record is written and committed and the run artifact is finished. Live passes
only (a `--no-live` pass records `skipped: no-live`). Its time budget is the smaller of
`PLATFORM_EXTRACTION_BUDGET_S` and the pass's limit (`ALPHADECK_CRON_RUN_TIMEOUT_S`, 5,400 s) minus the time the
pass has spent minus 600 s; at zero it is `no time left` (the due companies counted, taken the next night). It uses
the recurring SEC client (the recurring lane of the global SEC budget) and `work_list`'s B4 rule. It never raises:
a fault is a `status: error` block. Its summary (`ExtractionRun.to_json()` plus `budget`, `withheld_never`, `held`,
`waiting`, `live_completed`) is added to the finished pass artifact as the `platform_extraction` block.

| `status` | Admin line |
|---|---|
| `ran` | `Shared facts · Nov 14: 42 extracted: 31 applied, 6 to the steward, 5 nothing found · 2 failed (ACME: SEC unreachable) · 118 still due · 84 SEC requests · 3m 12s` |
| `off` | `Shared facts · Nov 14: off (the kill switch, ALPHADECK_PLATFORM_EXTRACTION_MAX_NAMES=0)` |
| `no time left` | `Shared facts · Nov 14: no time left in the pass · 12 due, taken next night` |
| `skipped: no-live` | `Shared facts · Nov 14: skipped (a --no-live pass fetches nothing)` |
| `error` | `Shared facts · Nov 14: did not run (the step failed (RuntimeError))` |

Any line gains `· N waiting after repeated failures` and `· N never extracted, held until a backfill extracts a
company` when they are not 0. **Loud** (`attention`) when a company failed, the run itself failed, a company is
waiting after repeated failures, or never-extracted companies are held back (the backfill is owed); quiet otherwise.

**On Admin:** `AdminStatusOut.platform_extraction` (`line`, `ran_at`, `run_kind`, `attention`) is the newer of the
newest finished pass's block and the newest file in `data/extraction_runs/` (the backfill, the onboarding step, a
hand run): two directory reads, no database, fail-open.

**By hand** (`python -m pipeline.nightly_extraction`): the same step outside a pass. A dry run by default (the
work list, the budget; reads only); `--apply` runs it (attempts are `nightly`), `--no-live` makes the client
cache-only (no SEC request, no User-Agent needed: a missing document is an `error` attempt, `not cached`, and stays
due). It refuses (exit 75) inside the nightly window or while a pass holds the call-of-record lock, stops between
companies if either begins, and writes its summary to `data/extraction_runs/`.

## The backfill (W3d)

`python -m pipeline.extract_values` extracts every due company by hand, through the pieces above
(`work_list(include_never=True)`, then `run_extraction(..., run_kind="backfill")`). It is never scheduled. Nothing
it writes is a call input.

```bash
python -m pipeline.extract_values                    # DRY RUN (the default): the SEC cost plan; no request, no write
python -m pipeline.extract_values --no-live          # DRY RUN of a cache-only run: who would extract, who would fail
python -m pipeline.extract_values --apply            # extract every due company (live, the recurring SEC lane)
python -m pipeline.extract_values --apply --no-live  # cache only: never reaches SEC (the fork rehearsal)
python -m pipeline.extract_values --apply --max-names 200 --budget-s 1800 --ticker OKLO --ticker LEU
```

**The dry run** reads the work list (a read-only database session) and the EDGAR cache on disk. It builds no client,
makes no SEC request and writes no row and no file. Per company and in total it prints what a real run would fetch
live and what it would read from the cache, by the client's own rules:

| Read | Counted as live when |
|---|---|
| the submissions index | missing, or older than the recurring 5 minutes (so most companies cost one request) |
| companyfacts | missing, or due by the filing gate (judged on the cached index); a cached 404 costs nothing (the extraction then fails on it) |
| the one values document | missing (`forms/` is cached forever): the newer of the latest 10-Q and 10-K (B1, values only), else the newest 20-F / 40-F, which is also read when it is newer by public time than the 10-Q and 10-K (the router's `_takes_annual_path`) |
| an annual filer's exhibit search | a bound of up to 8 more (the index headers, the filing index, up to `exhibit_scan_max` EX-99 documents) |
| a company whose index is not cached | one known request, the rest a range (up to 10 more) |

Bytes: a missing document is bounded by the index's `size` column (the whole filing's size, so an upper bound); an
index or companyfacts refresh is estimated by its cached copy's size; anything else is counted "of unknown size".
The time shown is a floor: the requests at `ALPHADECK_SEC_GLOBAL_RATE_PER_SEC` (8.5 a second). **What the dry run
cannot know without fetching** (printed with every plan): whether the refreshed index lists a newer filing (another
document, and companyfacts may become due); exact bytes; whether an annual filer's statements sit in an exhibit;
which documents a company with no cached index needs; SEC throttling and the Workbench's share of the one global
budget; the CPU time per company.

**`--apply`** takes advisory lock 7 (`LOCK_ID_EXTRACTION`, one backfill at a time); refuses inside the nightly window
(the onboarding job's window, `ALPHADECK_ONBOARD_WINDOW_BEFORE_MIN` / `_AFTER_MIN`) and while a pass holds the
call-of-record lock (a probe, never a take); and re-checks both between companies, stopping cleanly when either
begins. It uses one recurring-dial `EdgarClient`: the global SEC budget's recurring lane. It is **resumable**: a
finished company is no longer due, so a second run does only what is left, and a run with nothing due writes
nothing (not even an artifact). `--max-names` (alias `--limit`) and `--budget-s` bound it, checked between companies;
it takes companies in the work list's order (a new filing, a retry, then never extracted; pen-only last; most cards
first). `--ticker` scopes it; a ticker that is not a company on a live card (or is an ETF) refuses the run.

It writes `data/extraction_runs/<started>Z-backfill-<token>.json` (`pipeline/extraction_run_log.py`: the start to
the microsecond, the run kind and a short random token, so two runs never share a name and the name sort is the time
sort; `ExtractionRun.to_json()` plus `mode`, `tickers`, `errors_by_reason`, `stop_detail`,
`backfill_applied_before` / `_after`) and prints a summary: applied, candidates (flagged to the steward, nothing
found), unchanged, skipped, busy, failed with their reasons, deferred and why, and the live SEC fetches.

| Exit | Meaning |
|---|---|
| 0 | ran (failed companies are listed, not fatal), nothing due, or the dry run |
| 1 | could not run: a live run with no `ALPHADECK_USER_AGENT`, or no database |
| 2 | a bad argument: an unknown `--ticker`, `--max-names` below 1, a non-positive `--budget-s` |
| 75 | refused (the nightly window, another backfill, a running pass) or stopped between companies by the window or a pass; nothing more written, the rest stays due |

**`--no-live`** (cache only): the client never reaches SEC; a stale copy is served. A company whose documents are
not all cached gets an `error` attempt with the reason `not cached` and is counted; nothing is fetched. It stays due
(`retry`) until `platform_extraction_retry_max` such attempts on the same filings, then waits for a new filing.

**Decision B4** (tightened 2026-11-15). The nightly leg and the onboarding step take no never-extracted company
until this CLI has EXTRACTED at least one company: a `backfill` attempt with status `done` (`backfill_applied`).
Failed attempts (a `not cached` error included), `busy` and `skipped` (no CIK) attempts unlock nothing, so a first
backfill in which every company fails leaves them held. The CLI says where B4 stands before (dry run) and after
(`--apply`); after a run that extracted nothing it says `B4: nothing was extracted (...), so the nightly is still
holding never-extracted companies, and so is the onboarding job, ...`.

### Rehearsing on the fork (cache only)

A fork has prod's EDGAR cache (copied by `scripts/refresh-fork.sh`) but no SEC User-Agent, so a live run there fails
every company. Rehearse cache-only, inside the fork's backend container, from the fork worktree root (`<name>` = the
fork's `FORK_NAME`):

```bash
F="-f docker-compose.yml -f docker-compose.fork.yml -p alphadeck_<name> --env-file .env.fork"
docker compose $F exec backend python -m pipeline.extract_values --no-live            # who would extract, who fails
docker compose $F exec backend python -m pipeline.extract_values                      # prod's live cost, from the copied cache
docker compose $F exec backend python -m pipeline.extract_values --apply --no-live --max-names 50   # a first slice
docker compose $F exec backend python -m pipeline.extract_values --apply --no-live    # the rest (resumable)
```

Afterwards, look at: the summary and `data/extraction_runs/` (the per-company CPU time is the run's `seconds` over
its `attempted`, which sets the nightly cap and budget); `platform_extraction_attempt` grouped by `status` and
`error` (how many are `not cached`); the steward's queue (the flagged companies) and the values now "from the
filing". The live dry run on the fork measures prod's request count from the copied cache, except for anything
filed after the copy.

**What the rehearsal changes on the fork.** It records `backfill` attempts; once one company extracts from the
cache (a `done` attempt), it flips the B4 marker on the fork:
from then on the fork's nightly and onboarding steps would take never-extracted companies. That is acceptable there:
the fork's cron service rides the `donotstart` profile (`docker-compose.fork.yml`, `docs/FORKS.md`), and that
sidecar is what fires both the nightly and the onboarding job, so nothing on the fork acts on the marker. A re-seed resets it: `scripts/fork.sh
refresh --reset` drops the fork database and restores prod's dump, which has no attempt rows (prod is not on V2 yet),
then the migrations create an empty `platform_extraction_attempt`. On prod, run the dry run first, then `--apply`
before the V2 cron starts (cutover order, W10).

## The steward's decisions and undo

`platform_value_decision` (migration 0205, data tenant, append-only) holds one row per steward action:

| `action` | Names | Effect |
|---|---|---|
| `approve` | a candidate and the fact version it wrote | the candidate's value is applied |
| `correct` | the fact version it wrote (and optionally a candidate it closes) | the steward's value is applied |
| `dismiss` | a candidate | the candidate is closed, no fact |
| `undo` | the decision it undoes (`revokes`) | below |

An **undo** sets aside the fact version an approve or correct wrote: `db/set_aside.py` registers "the fact written
by an undone approve or correct" for both tables, so every read (both clocks, the basket prefetch, the replay
mirror) skips it and falls back to the version it replaced. Nothing is deleted. An undone approve or dismiss
reopens its candidate. An undo cannot be undone (make a new decision). A decision or undo that wrote no fact has a
NULL `fact_id`; the registered SQL filters NULLs out, or `NOT IN` would blank every read.

The steward's actions (W2c, below) write them; the machine writer (W2b) writes candidates.

## The steward's write (W2c)

`platform_values/steward.py` holds the six steward actions. Each one takes the company write lock
(`db/write_lock.py`, the request wait `Settings.ingest_lock_request_wait_s`), re-reads the state under it, checks its
rule, appends (never updates) the fact and then the decision or pin event, stamps the company changed in the same
transaction, commits, and releases. The fact's `ratified_by` and the decision's `decided_by` are the signed-in
steward's email. The action owns its transaction: it refuses a connection with one already open, so its write is
the company's newest (the tie-break of D1 (a)).

| Action | Writes | Refused |
|---|---|---|
| `approve(candidate)` | the candidate's own value (its filing, date, ADS fields; a cash/burn candidate's `statement_currency` as `currency` and its `investments_usd`), `vouched='confirmed'`; an `approve` decision | a "nothing" candidate or a flag without a complete value (422); a currency that is not an ISO code or an investment part below zero or above the cash (422, `bad_values`); already decided (409); would not be shown (below) |
| `correct(company, type, values, source_ref, event_date)` | the steward's value (cash/burn may state `currency` and `investments_usd`), `source='ratified'` (the Workbench confirm keeps the candidate's basis); a `correct` decision, closing `candidate_id` when given | would not be shown (below); the candidate decided or not this company's and type's (409); an ADS ratio without `known`, or `known` without a ratio (422); a currency that is not an ISO code, or an investment part below zero or above the cash (422) |
| `dismiss(candidate, reason)` | a `dismiss` decision | a blank reason (422); already decided (409) |
| `undo(decision)` | an `undo` decision naming it | an undo (409: make a new decision); already undone (409) |
| `pin(company, type)` / `unpin` | a `steward_pin` event | already pinned / not pinned (409) |

**The correction rule** (D1 (a)), for approve and correct: a value is written only when it will be shown. Its
date must be on or after the value shown from any OTHER filing and not after today; correcting the shown filing
itself replaces its value. A date after today is refused with 422 (`after_today`); a date before the value shown is
refused with 409 (`older_than_shown`), and the steward's detail names the blocking value's date and filing. After
the write, still inside the transaction, the meter's own pick is re-read: if it is not the new row the write is
rolled back (`not_shown`, 409). Nothing is ever stored that no meter would show.

Errors: `LookupError` (404), `PlatformValueConflict` (409), `PlatformValueInvalid` (422), `CompanyLockBusy` (409).
Each kind has one fixed sentence in `app/errors.py` (`PLATFORM_VALUE_SENTENCES`).

### The routes (steward only, `/admin/platform-values`)

| Route | Body | Answer |
|---|---|---|
| `GET /admin/platform-values/awaiting?limit=` (1-500, default 200) | | `PlatformAwaitingOut { items, total }`, newest first |
| `GET /admin/platform-values/queue?limit=&offset=&pen=` (limit 1-200, default 50; offset 0-100,000; pen `after` / `only` / `exclude`, default `after`) | | `PlatformQueueOut { items, total, pen_only_total }`, in queue order (below) |
| `GET /admin/platform-values/{security_id}` | | `CompanyValuesOut` (each value's `pin`: the newest pin event, below) |
| `POST /admin/platform-values/candidates/{candidate_id}/approve` | `PlatformApproveIn { note? }` | `CompanyValuesOut` |
| `POST /admin/platform-values/{security_id}/correct` | `PlatformCorrectSharesIn` or `PlatformCorrectCashBurnIn` (by `fact_type`), with `source_ref`, `event_date`, `note?`, `candidate_id?` | `CompanyValuesOut` |
| `POST /admin/platform-values/candidates/{candidate_id}/dismiss` | `PlatformDismissIn { reason }` | `CompanyValuesOut` |
| `POST /admin/platform-values/decisions/{decision_id}/undo` | `PlatformUndoIn { reason? }` | `CompanyValuesOut` |
| `POST /admin/platform-values/{security_id}/pin` and `/unpin` | `PlatformPinIn { fact_type, note? }` | `CompanyValuesOut` |
| `POST /admin/platform-values/{security_id}/extract` | none | `PlatformExtractOut { attempt, company }` (below) |

Every write answers the company's new state. The model names carry a `Platform` prefix where a bare name
(`DecisionOut`, ...) already exists in the contract. A plain user gets 403 on every route
(`tests/app/test_admin_platform_values_api.py`, with real sign-ins). `POST /workbench/facts` keeps the steward's
share-count and cash/burn confirm, through `correct` with no candidate (approved decision D4 (a)); a plain user still
gets 403. The two ratify CLIs (`pipeline.ratify_shares`, `pipeline.ratify_cash_burn`) are deleted.

**The pin event on the company read (W4a).** Each value in `CompanyValuesOut` carries `pin`
(`PlatformPinEventOut { action, decided_by, note, recorded_at }`): the newest `steward_pin` event, `pin` or `unpin`,
who, when and why; None when the value was never pinned. `pinned` (bool) stays; it is `pin.action == "pin"`.

## The steward's queue (W4a)

`GET /admin/platform-values/queue` lists every company with a share count or cash/burn waiting for the steward, one
row per company (approved B2 (a): these two values only; B3 (a)). It reads the same open flags as `/awaiting`
(`state.awaiting_companies`, so a flag older than the value shown is not listed) and adds each company's card count
(below).

`PlatformQueueItemOut { security_id, ticker, name, cards, pen_only, waiting_since, shares, cash_burn }`: `shares` and
`cash_burn` are `PlatformQueueValueOut { state, candidate, value }` or None (that value waits for nothing); `value`
is the value shown when the candidate is pending beside it. `waiting_since` is the older of the waiting candidates'
`recorded_at`.

**The order** (approved B3 (a), B5 (a)), total:

1. pen-only companies last (every live row it sits in is the Discovered link);
2. then most cards first;
3. then the longest wait first (`waiting_since` ascending);
4. then the company id.

A company on no live card (removed from every card after its candidate was written) shows `cards = 0`,
`pen_only = false`, so it sorts just before the pen-only block. `pen=after` (default) keeps that order, `pen=only`
lists only the pen-only companies, `pen=exclude` leaves them out; `offset` / `limit` cut the page. `total` counts
every company waiting and `pen_only_total` the pen-only ones, whatever the page and `pen`.

**Its cost is fixed.** The awaiting list (one query), one `read_states` batch per value type and one card count:
the number of statements does not grow with the queue (`tests/app/test_admin_platform_values_queue_api.py`).

## The card count

`platform_values/cards.py`, `card_counts(conn, security_ids) -> dict[UUID, CardCount]` with
`CardCount(cards: int, pen_only: bool)`. One statement for any number of ids:

- `cards`: how many live (non-archived) cards hold the company, in ANY workspace, in any basket row: every link,
  the Discovered pen and the silent links (Context, ETFs, Bench) included (approved D5 (a)). One card holding the
  company in two rows counts once.
- `pen_only`: every one of those rows is the `Discovered` link (approved B5 (a)). A row with no link counts as not
  Discovered.
- A company on no live card is absent from the answer; the caller decides what that means.

It is the only W4 read that crosses workspaces, so it returns two numbers per company and nothing else: no thesis
id or name, no workspace, no user, no link label; it logs nothing. It is steward-side only:
`tests/app/test_card_count_guard.py` fails when a product file other than the steward router names it. W3's
backfill may reuse it for its order: add the caller to that guard's `ALLOWED` in the same change.

### The steward's company page (W4b)

`/admin/facts/<security_id>` (steward-only in the UI like Admin; a plain user sees one line and no request is made;
reached by the company search box on the page, and from the queue card once W4c lands). One block per value
(`frontend/src/admin/CompanyFacts.tsx`, words in `factWords.ts`): the state line (who set it, when, the filing link
and date), a waiting candidate with its located passages, flags and Explain (the Workbench's explain route; a draft,
never a value), then Approve, Correct, Dismiss, Undo, Pin and Unpin, each answered by the company's new values. An
undo cannot be undone, so the page offers the decision again (Approve on the reopened candidate, "Correct again"
prefilled from the set-aside version). A refusal shows the server's sentence inside the block that made the call.
Nothing disappears: dismissed candidates, undone decisions and set-aside versions stay in the collapsed History, gray.
Who pinned a value, when and why shows beside the Pinned chip, from the company read's newest pin event (`pin`).

### Extract now (W4d)

`POST /admin/platform-values/{security_id}/extract` (steward only, no body) runs background extraction's own
per-company step, `extract_one(..., run_kind="steward")`, on the RECURRING SEC client (`app.deps.get_platform_sec_client`,
the five-minute dial: companyfacts is re-read from the cache unless the company filed since; its requests ride the
recurring lane of the global SEC budget). The step fetches with the company free, then the machine writer takes the
lock and writes, then exactly one attempt row is appended. The route never holds the lock itself and holds no
transaction across the SEC call. There is no daily allowance to charge: a steward is never counted.

| Answer | When | Written |
|---|---|---|
| 200, `attempt.status` `done` | the writer ran | its usual rows, plus the attempt |
| 200, `attempt.status` `error` | SEC unreachable, a document missing, no SEC User-Agent set, ... | the attempt only (`error`, one line); a value awaiting the steward still waits |
| 404 (`PLATFORM_VALUE_NOT_FOUND`) | no such company in the data tenant | nothing; nothing fetched |
| 404 (`PLATFORM_VALUE_NO_CIK`) | the company has no CIK | nothing; nothing fetched |
| 409 (`COMPANY_BUSY`) | another writer held the company for the whole wait (`ingest_lock_request_wait_s`) | the `busy` attempt only |
| 422 (`PLATFORM_VALUE_FUND`) | an ETF (`extraction.extractable` is false) | nothing; nothing fetched |

`PlatformExtractOut { attempt: PlatformExtractAttemptOut, company: CompanyValuesOut }`;
`PlatformExtractAttemptOut { attempt_id, status, error, detail, live_fetches, shares, cash_burn }`, each value a
`PlatformExtractValueOut { outcome, reason, written, fact_id, candidate_id }` with `outcome` the writer's reason
(`applied`, `flagged`, `pinned`, `nothing_found`, `already_on_file`, `older_than_on_file`, `after_today`) or
`failed` / `skipped` (`reason` then the attempt's error line or detail); `reason` on `nothing_found` is the
extractor's own (`cash-generative`, ...); `written` False on `flagged` means the same flag was already waiting.

**The recent attempts on the company read.** `CompanyValuesOut.attempts` (additive): the company's newest 10
`platform_extraction_attempt` rows, newest first, whoever ran them:
`PlatformExtractionAttemptOut { id, recorded_at, run_kind, status, shares_reason, cash_reason, error, detail }`.
The company page lists them in each value's History.

## The user's read (W5a)

A plain user reads both values of a company, or of every company on one of their cards, with the state in words,
who set the value as a kind, and their own flag on it. They never write a value. This is the one read every user
screen and agent tool uses (the Workbench's Shared values block and the Cockpit's lines, W5e; `get_triage`, W5d).
It is CURRENT: there is no `asof`, so a dated view must not use it to explain an old meter.

| Route | Answer |
|---|---|
| `GET /platform-values/theses/{thesis_id}` | `UserThesisValuesOut { thesis_id, members }`: the card's resolved members in basket order, each company once; another workspace's thesis is 404 (`get_thesis_or_404`, like every thesis route) |
| `GET /platform-values/{security_id}` | `UserCompanyValuesOut { security_id, ticker, name, shares, cash_burn }`: any company in the master (shared data, no card check); an unknown id is 404 ("There is no such company.") |

The static `theses` path is declared before `{security_id}`; nothing else lives under the prefix (a user's flag
routes live under `/value-flags`, W5b). Both routes are on the plain user gate; the steward may read them too.

`UserValueOut { fact_type, state, reason, label, set_by, value, annual_line, pending, awaiting, my_flag }`:

| Field | Meaning |
|---|---|
| `state`, `reason` | the state from `read_states` (the one reader): `no_value` (with the reason), `from_filing`, `awaiting_steward`, `verified` |
| `label` | the state in words (`platform_values/words.py`, below) |
| `set_by` | who set the value shown: `machine` / `steward` / `carried`; None with no value. Never the steward's email |
| `value` | the value the meters show (`UserAppliedValueOut`): `source`, `source_url`, `valid_from`, `filing_accepted`, `set_at`; a share count's `shares` and ADS fields; cash and burn's `cash_usd`, `investments_usd`, `quarterly_burn_usd`, the stored `currency` (None = not recorded), `currency_symbol` (the one rule, `domain.cash_currency`) and `pace` (`domain.burn_pace.stored_pace`) |
| `annual_line` | an annual cover count (`source='annual-cover'`): "annual figure from the 20-F or 40-F cover, as of Mar 31, 2026" |
| `pending` | a newer flagged candidate beside the value shown: `event_date`, `filing_accepted`, `source_url`, `line` ("A newer filing (dated ...) is awaiting the steward"). No figure. The value shown is kept |
| `awaiting` | nothing shown and a flagged candidate waits: `event_date`, `filing_accepted`, `source_url`, `line`, `located_passages` (`kind`, `anchor`, `excerpt`, `url`) and `passages_unreadable`. **No figure** |
| `my_flag` | the caller's own flag on the value (`id`, `note`, `opened_at`, `closed_at`, `outcome`, `outcome_note`): their open flag, else their newest closed one; None if they never flagged it |

**No parked number reaches a user** (approved B1 (b), 2026-11-16): for a share count or cash awaiting the steward
the answer carries the state, the filing's link and date and its located passages, and no candidate figure, flag
name or note; the pending line likewise names only the newer filing. The passages are the filing's own words, so
an excerpt can print the number the filing prints. (An API or agent caller can still reach the live extract route's
raw parse until W7 narrows it.)

**`my_flag`** reads `value_flag` (migration 0202) with ONLY its original columns, in one statement whose WHERE
names the caller's id (`user_id = %s`), never a read of every user's flags filtered afterward:

```sql
SELECT DISTINCT ON (security_id, value_type)
       id, security_id, value_type, note, opened_at, closed_at, outcome, outcome_note
FROM value_flag
WHERE user_id = %s AND security_id = ANY(%s) AND value_type = ANY(%s) AND value_ref = ''
ORDER BY security_id, value_type, (closed_at IS NULL) DESC, closed_at DESC NULLS FIRST, opened_at DESC, id DESC
```

**Its cost is fixed:** the master (one statement), one `read_states` batch per value type and the flag query, for
one company or thirty (`tests/platform_values/test_user_view.py` counts the statements on its own connection).
Links: every URL field is `HttpUrlOut`, built by `dashboards.filings.source_url` (a stored URL that passes the one
rule, an accession through the EDGAR index, anything else None).

**The words** live in ONE module, `platform_values/words.py` (not under `domain/`, so the editions fingerprint does
not move): `state_label`, the no-value reasons (`NO_VALUE_WORDS`), `set_by` / `set_by_words`, `annual_line`,
`pending_line`, `awaiting_line`, `day_words`. The user read uses it now; the server-written lines (the Filing Stream,
the Evidence Ledger, the scored read's notes) move to it in W5d. Tests: `tests/platform_values/test_words.py`,
`test_user_view.py`, `tests/app/test_platform_values_user_api.py` (real sign-ins: two plain users in two workspaces
and the steward).
