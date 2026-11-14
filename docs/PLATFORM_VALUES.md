# Platform values: the share count and cash/burn (V2)

Not a rule doc. This page says how a company's two **platform values** are stored, picked and labeled on the
`v2` branch. The rules of the call live in `docs/CALL_LOGIC.md`; neither value is a call input.

A platform value is one value per company for everyone. It lives in the data tenant (`docs/TENANT_SCOPES.md`,
`domain/scopes.py`):

| Value type (`fact_type`) | Fact table | Read by |
|---|---|---|
| `shares_outstanding` | `fact_shares_outstanding` | the market cap (Workbench, Hot Board, Insider Tracker, Peer Table, Theme Leaderboard, Ticker Lookup, Market Map, Track Record, the data flags) |
| `cash_burn` | `fact_cash_burn` | the runway meter (Workbench), the Peer Table |

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

`read_company` adds every stored version (with `set_aside`), the newest 20 candidates and the newest 50 decisions;
`awaiting` lists the open flags the state says still wait, newest first.

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
| `approve(candidate)` | the candidate's own value (its filing, date, ADS fields), `vouched='confirmed'`; an `approve` decision | a "nothing" candidate or a flag without a complete value (422); already decided (409); would not be shown (below) |
| `correct(company, type, values, source_ref, event_date)` | the steward's value, `source='ratified'` (the Workbench confirm keeps the candidate's basis); a `correct` decision, closing `candidate_id` when given | would not be shown (below); the candidate decided or not this company's and type's (409); an ADS ratio without `known`, or `known` without a ratio (422) |
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
| `GET /admin/platform-values/{security_id}` | | `CompanyValuesOut` |
| `POST /admin/platform-values/candidates/{candidate_id}/approve` | `PlatformApproveIn { note? }` | `CompanyValuesOut` |
| `POST /admin/platform-values/{security_id}/correct` | `PlatformCorrectSharesIn` or `PlatformCorrectCashBurnIn` (by `fact_type`), with `source_ref`, `event_date`, `note?`, `candidate_id?` | `CompanyValuesOut` |
| `POST /admin/platform-values/candidates/{candidate_id}/dismiss` | `PlatformDismissIn { reason }` | `CompanyValuesOut` |
| `POST /admin/platform-values/decisions/{decision_id}/undo` | `PlatformUndoIn { reason? }` | `CompanyValuesOut` |
| `POST /admin/platform-values/{security_id}/pin` and `/unpin` | `PlatformPinIn { fact_type, note? }` | `CompanyValuesOut` |

Every write answers the company's new state. The model names carry a `Platform` prefix where a bare name
(`DecisionOut`, ...) already exists in the contract. A plain user gets 403 on every route
(`tests/app/test_admin_platform_values_api.py`, with real sign-ins). `POST /workbench/facts` keeps the steward's
share-count and cash/burn confirm, through `correct` with no candidate (approved decision D4 (a)); a plain user still
gets 403. The two ratify CLIs (`pipeline.ratify_shares`, `pipeline.ratify_cash_burn`) are deleted.
