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

## The machine writer (W2b)

`platform_values/machine.py`, `record_extraction(conn, security_id, result, *, wait_s, cache_dir=None, cik=None,
today=None) -> {fact_type: MachineOutcome}`, is the only code that turns the deterministic extractor's parse into a
platform value. It takes an `ExtractionResult` already fetched (no network inside) and, for each type:

| Extractor result | What is written |
|---|---|
| AUTO with its number(s) | `machine_action` decides: a fact stamped `auto` (applied), a `flag` candidate with flag `pinned` (the company is pinned), or nothing: `already_on_file` (any stored version of the filing, set-aside ones too, so an undone approve is never re-applied), `older_than_on_file` (dated on or before the value shown), `after_today` (never shown, so never written) |
| FLAG, or AUTO with no number | a `flag` candidate: raw values, flags, filing, date, located passages, ADS fields / statement currency |
| no value of that type | a `nothing` candidate with the extractor's reason: cash/burn takes the runway reason first (`cash-generative`, `financials-in-exhibit`, `exhibit-ambiguous`, `statements-not-located`), then the result's (`no-annual-filing`, `cover-not-located`), else `no-candidate` |
| an exception (SEC down, companyfacts 404) | nothing: the caller never gets a result to record |

A strictly newer filing is applied over a verified value unless the company is pinned (D3 (a)). A candidate that
repeats the newest candidate for that company and type (same outcome, reason, filing, date, flags and values) is
not written again. Only the extractor's own parse is read: a fact carrying an `estimate_source` (a model's
proposal) is ignored, and the module imports nothing from `llm`.

**Lock and stamp.** The caller fetches with the company free. The writer then takes the company write lock
(`acquire_company_lock`, waiting at most `wait_s`; `CompanyLockBusy` with nothing written), re-reads the state, the
pin and every stored filing under it, writes both types in one transaction with the company's change stamp,
commits, and releases. Two simultaneous get-data clicks write one value.

**Callers.** `POST /workbench/facts/auto-confirm` (get-data; D2 (a)): same gates as before (own cards, the daily
`auto_confirm` allowance; a steward neither), extraction by `extract_with_annual_fallback` (annual filers too), the
lock wait `ingest_lock_request_wait_s` (busy is 409), an extraction failure 502. The answer's top level is the share
count's outcome, `cash_burn` the cash/burn one; the reasons are the writer's, with `flagged` spelled `not_auto`.
W3's nightly leg will call the writer with `ingest_lock_wait_s`.

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

Nothing writes these rows yet: the machine writer (W2b) and the steward's actions and routes (W2c) do.
