# Platform values

A platform value is a company's share count or its cash and burn: one value per company for everyone, stored in
the data tenant (invariant 12), never in a workspace. The machine applies clean values from filings, the steward
decides the rest, and a plain user reads each value with its state and may flag it. The values feed the market
cap, the runway meter and the Peer Table; neither is a call input. Read this before you change
`backend/platform_values/`, `domain/platform_values.py`, or any reader or writer of `fact_shares_outstanding` or
`fact_cash_burn`. When the machine runs: [PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md). A user's flag:
[VALUE_FLAGS](VALUE_FLAGS.md). How a number is parsed from a filing: [WORKBENCH_EXTRACTION](WORKBENCH_EXTRACTION.md).

## Code

| Piece | Module |
|---|---|
| Pure rules: pick, state, correction rule, machine's rule, annual share-count rule | `domain/platform_values.py` |
| The one reader | `platform_values/state.py` |
| The machine writer; the steward writer | `platform_values/machine.py`; `platform_values/steward.py` |
| The user's read and its words | `platform_values/user_view.py`, `platform_values/words.py` |
| The cross-workspace card count | `platform_values/cards.py` |
| Store; routes | `repositories/platform_value_repo.py`; `app/routers/admin_platform_values.py` (steward), `app/routers/platform_values.py` (user) |
| Currency label; year-to-date pace | `domain/cash_currency.py` (frontend twin `components/currency.ts`); `domain/burn_pace.py` |
| Screens | steward: `frontend/src/admin/SharedFacts.tsx`, `CompanyFacts.tsx` ([ADMIN](ADMIN.md)); user: `frontend/src/workbench/SharedValues.tsx` |

## Storage

| Table | Migration | Holds |
|---|---|---|
| `fact_shares_outstanding`, `fact_cash_burn` | fact tables | the values: append-only versions |
| `extraction_candidate` | 0200 | a flagged or "nothing" extraction result; never a fact |
| `steward_pin` | 0201 | pin and unpin events |
| `platform_value_decision` | 0205 | the steward's approve, correct, dismiss and undo |
| `platform_extraction_attempt` | 0207 | one row per extraction attempt |
| `value_flag` | 0202, 0210, 0211 | users' flags |

All but `value_flag` are in the DATA scope (`domain/scopes.py`); `value_flag` is user-keyed (`UNSCOPED_TABLES`). The
Admin status counts share or cash rows left under a workspace (`platform_rows_in_workspaces`): nothing reads them,
so it should be 0.

**Who set it.** Migration 0206 limits `ratified_by` on new rows: `auto` or NULL is the machine, `operator` a V1 row
carried over, an email the steward. The check was added `NOT VALID`; the V2 cutover (`pipeline/v2_reset.py`)
validates both constraints. `domain.platform_values.ratifier_kind` maps a stored value to `machine`, `carried` or
`steward`.

**Cash columns.** What `cash_usd` includes: [WORKBENCH_EXTRACTION](WORKBENCH_EXTRACTION.md). Migration 0208 adds
`currency` (the statement's ISO code; the `*_usd` columns hold that currency's figures, never converted) and
`investments_usd` (the investment part inside `cash_usd`); 0209 adds the stored year-to-date pace
(`ytd_quarterly_burn_usd`, `ytd_burn_days`, both or neither). NULL means not recorded, never an invented `USD` or 0;
there is no backfill. Every write path sets them on a new version's insert only. One label rule
(`domain.cash_currency.currency_symbol` and its frontend twin): `?` for an annual-statements value with no currency,
`$` for a quarterly or V1 value with none. One pace reader (`domain.burn_pace.stored_pace`: the columns, else the
note's segment on an older row). A correction that states no pace stores none and drops the machine's pace from
the note (`drop_pace`). The pace caveat is computed on read and never changes a tier.

## The one pick

The as-of read returns one row per filing (`source_ref`; the newest version, set-aside versions skipped).
`domain.platform_values.pick_current` chooses the value shown:

1. the latest `valid_from` wins;
2. on a date tie the newest write wins: `recorded_at`, then `id`;
3. a row dated after today is never picked.

Every reader that shows "the value" calls it; `tests/dashboards/test_one_pick.py` fails on a reader with its own
key. Why: readers once kept the first row of a date tie, so the alphabetically first filing URL won.

## The four states

`platform_values.state.read_states(conn, security_ids, fact_type)` reads, in the data tenant and exactly as the
meters do (record clock, today's market date), the picked value, the newest candidate per company, its standing
decision and the newest pin event. `domain.platform_values.derive_state` gives the state:

| Picked value | Newest candidate | State |
|---|---|---|
| set by the machine | any | `from_filing` |
| set by the steward or carried | any | `verified` |
| a value | an undecided flag dated after it | the value's state, plus `pending` |
| none | none | `no_value`, reason `not-extracted` |
| none | a "nothing" candidate | `no_value`, the candidate's reason |
| none | an undecided flag | `awaiting_steward` |
| none | a dismissed flag | `no_value`, reason `dismissed` |

A candidate is never a fact: it is in no as-of read and no replay mirror
(`tests/db/test_v2_schema.py::test_a_candidate_is_not_a_fact`), and `DerivedState.meter_value` returns only the
picked value, so a value awaiting the steward never reaches a meter.

`read_states` runs a fixed number of queries for any set; `read_state` is `read_states` for one company
(`tests/platform_values/test_state_batched.py`). Never loop over `read_state`. `read_company` adds stored versions
(with `set_aside`), recent candidates, decisions and the pin event. Only the newest candidate per company and type
is read, so an older candidate reopened by an undo waits in no queue and shows only in the company read.

## Who writes

A value is written by the machine writer (`ratified_by='auto'`) or the steward writer (the steward's email), and
otherwise only by the exceptions below. Both writers take the company write lock
(`db.write_lock.acquire_company_lock`), re-read the state under it, append, stamp the company changed
(`db.change_stamp.mark_changed`) in the same transaction, and commit. Any SEC fetch happens before the lock.

`tests/ingest/test_platform_value_writers.py` reads the source (no database) with three rules: only named files call
`ingest_shares_outstanding` or `ingest_cash_burn`; only named files write either table directly; only
`pipeline/seed.py` stamps `operator`. A stale entry fails too. The allowed exceptions:

| File | Why it may write |
|---|---|
| `pipeline/seed.py` | the demo seed's fixtures |
| `db/bitemporal.py`, `ingest/shares.py`, `ingest/cash_burn.py` | the generic `append_fact` and the two ingest functions (`require_write_tenant`, append, stamp) |
| `pipeline/repair_shares_cover_dates.py`, `pipeline/repair_filing_dates.py` | repairs bound by the rules below |
| `pipeline/v2_move.py` | the one-time move of workspace rows into the data tenant |
| `pipeline/dedup_identical_versions.py` | listed so a new writer still fails (Known limits) |

**The repair rules** (`pipeline/platform_repair_guard.py`):

1. Data tenant only: a row under another tenant is listed `outside_data_tenant`, never judged or written.
2. The steward's decisions stand: a fact any version of which a decision names (standing or undone), or which has a
   set-aside version, is listed `steward_decision` and never written.
3. Each company an `--apply` appends to is stamped inside the repair's own transaction, because a repair holds the
   repair lock, not the company lock whose release stamps.

Also guarding: `tests/ingest/test_platform_write_tenant.py` (the ingest functions refuse a workspace tenant) and
the card-count guard (below).

## The machine writer

`platform_values/machine.record_extraction(conn, security_id, result, *, wait_s, ...)` is the only code that turns
the extractor's parse into a value. It takes an already-fetched `ExtractionResult` and, per value type:

| Extractor result | Written |
|---|---|
| AUTO | `machine_action` decides: apply a fact; a `flag` candidate flagged `pinned` for a pinned company; or nothing (`already_on_file`, `older_than_on_file`, `after_today`) |
| an annual share count the annual rule accepts | treated as AUTO |
| FLAG, or AUTO with no number | a `flag` candidate with raw values, flags, filing, date and located passages |
| no value | a `nothing` candidate with the extractor's reason |
| an exception | nothing; the caller records a failed attempt |

`already_on_file` counts every stored version of the filing, set-aside ones too, so an undone approve is never
re-applied. A strictly newer filing replaces a verified value unless the company is pinned. A candidate identical
to the newest one is not written again. Only the extractor's own parse is read: a fact with an `estimate_source` is
ignored and the module imports nothing from `llm` (invariant 3;
`tests/platform_values/test_machine.py::test_the_writer_imports_no_model_code`). Callers: background extraction
and the steward's Extract now, both through `platform_values.extraction.extract_one`, with the request lock wait
(`ingest_lock_request_wait_s`) so one busy company cannot spend a run's budget.

### The annual share-count rule

An annual foreign filer's cover count (20-F or 40-F) always carries the FLAG tier. The machine applies it as AUTO
when `domain.platform_values.annual_shares_auto_eligible` holds: source `annual-cover`, a finite count, flags only
from `ANNUAL_ONLY_FLAGS`, an ADS ratio `known` (at least 1) or absent, and an as-of date 0 to
`ExtractorConfig.annual_shares_auto_max_age_days` days old. Anything else, and a pinned company, still queues. The
applied fact keeps its real as-of date and acceptance, and its note says it was applied as an annual figure. Share
counts only: annual cash and burn always wait for the steward. Tests: `tests/domain/test_platform_values.py`,
`tests/platform_values/test_machine.py`.

**The same-day annual tie** (`annual_cover_replaces`): when the value shown is the machine's own quarterly-cover
count (`10-q-cover`) dated the same day as an accepted annual cover count, the machine appends the annual count,
and the pick shows it as the newest write. Never over a steward's or carried value. The new row keeps its own date
and acceptance, so nothing becomes public earlier (invariant 1).

## The steward

The steward is the super admin. `platform_values/steward.py` holds six actions: approve a candidate (its own value),
correct a value (the steward's value citing a filing, `source='ratified'`, optionally closing a candidate), dismiss
a candidate (with a reason), undo a decision, pin and unpin (a pinned company's new filing values all wait). Each
takes the company lock, re-reads the state, checks its rule, appends the fact then the decision or pin event,
stamps and commits. `ratified_by` and `decided_by` are the signed-in steward's email, never a request body's. An
action refuses a connection with an open transaction, so its write is the company's newest.

**The correction rule** (`correction_wins`): approve and correct write only a value that will be shown. Its date
must be on or after the value shown from any other filing and not after today; correcting the shown filing itself
replaces it. After the write, inside the transaction, the meter's own pick is re-read and the write rolls back if
the new row is not it (`not_shown`).

**Undo** sets aside the fact version an approve or correct wrote (`db/set_aside.py`), so every read (both clocks,
the basket prefetch, the replay mirror) falls back to the version it replaced. Nothing is deleted. An undone
approve or dismiss reopens its candidate; an undo cannot be undone. A decision that wrote no fact has a NULL
`fact_id`, which the registered SQL filters out: a `NOT IN` over a NULL would blank every read
(`tests/db/test_platform_value_set_aside.py`).

Refusals: `LookupError` 404, `PlatformValueConflict` 409, `PlatformValueInvalid` 422, `CompanyLockBusy` 409, each
kind with one sentence in `app.errors.PLATFORM_VALUE_SENTENCES`; the steward also sees the blocking date and filing.

**Routes** (`/admin/platform-values`, mounted with `require_super_admin`; bodies in `backend/openapi.json`): the
awaiting list, the queue, the company read, the six actions, Extract now, and a flag's close and reopen. Every
write answers the company's new state. A plain user gets 403 on each
(`tests/app/test_admin_platform_values_api.py`, real sign-ins). `POST /workbench/facts` also takes the steward's
share-count and cash confirm, through `correct`.

**The queue** lists one row per company with a candidate waiting (`state.awaiting_companies`) or an open user flag
(a count only). Order: pen-only companies last, most cards first, longest wait, then the id. Its query count is fixed
(`tests/app/test_admin_platform_values_queue_api.py`).

**Extract now** (`POST /admin/platform-values/{security_id}/extract`) runs background extraction's per-company step
with run kind `steward` on the recurring SEC client (`app.deps.get_platform_sec_client`), holding no lock or
transaction across the SEC call, and answers the attempt and the company's new state. 404 for an unknown company
or no CIK, 422 for an ETF, 409 when busy (`tests/app/test_admin_platform_values_extract_api.py`).

### The card count

`platform_values/cards.card_counts(conn, security_ids)` gives, in one statement, how many live cards in any
workspace hold each company (every link included; a card counts once) and whether every row is the Discovered pen
(`pen_only`). It is the one platform-value read that crosses workspaces, so it returns those two numbers only and
logs nothing. `tests/app/test_card_count_guard.py` allows two callers: the steward router (the queue order) and
`platform_values/extraction.py` (the extraction work order, never served). A new caller joins that guard's
`ALLOWED` with the reason it is steward-side.

## What a user sees

### The user's read

| Route | Who | Answers |
|---|---|---|
| `GET /platform-values/{security_id}` | anyone signed in | both values of any company in the master; 404 for an unknown id |
| `GET /platform-values/theses/{thesis_id}` | anyone signed in, own workspace | every company on the card; another workspace's thesis is 404 |

Each value (`UserValueOut`) carries the state and its words, who set it as a kind, the applied value with its
filing, an annual line, a pending line when a newer filing waits, the passages of a value awaiting the steward, and
`my_flag`, the caller's own flag. `platform_values/user_view.values_for` builds it from `read_states` in a fixed
number of statements (`tests/platform_values/test_user_view.py`). Every user screen and agent tool reads it. It is
current, with no as-of: a dated view must not use it to explain an old meter.

- **No parked number reaches a user.** A value awaiting the steward, or a pending newer filing, carries the filing's
  link, date and passages, never a candidate figure, flag name or note.
- **No email reaches a user.** No plain user's response and no agent tool names who set a value by email; the scored
  read, the Filing Stream, Ticker Lookup and the Evidence Ledger say a kind (`machine`, `steward`, `carried`, or
  `you` for the user's own purity; `tests/app/test_no_steward_email_for_users.py`). The steward's routes still
  show emails.
- **The words** live in `platform_values/words.py`, outside `domain/`, so a wording change does not move the
  editions fingerprint (`tests/platform_values/test_words.py`).

A user never writes a share count or cash: the Workbench rail's Shared values block is read-only, and the purity
panel (`FactsPanel.tsx`) is the user's own.

### Flags

A user may flag a value in any state as wrong or missing. A flag tells the steward and never changes the value;
only the user and the steward see it. Everything else: [VALUE_FLAGS](VALUE_FLAGS.md).

## Runway does not apply

A runway (cash divided by quarterly burn) means nothing for a bank, insurer, broker, exchange, asset manager,
lender or fund trust: its cash moves with customer money, loans or fund flows, not with spending.

- **The rule:** `securities.business_type.runway_applies(*, sector, ticker, instrument_kind)`, pure. False for every
  ETF and for a ticker or SIC description listed in `backend/securities/business_type/runway_not_meaningful.csv`;
  true otherwise, including no SIC. It keys on the SIC string, never the business-type leaf: the finance leaf also
  holds crypto miners and treasury companies, whose runway is real. The file's contract and its tests:
  [the business-type README](../backend/securities/business_type/README.md).
- **Who asks it:** the Workbench scoring (`workbench/scoring.py`: the scored read, the Cockpit name panel,
  `get_triage`), the Peer Table (`dashboards/peer_table.py`) and the user's read.
- **What changes:** no runway pips, months or pace; the scored runway carries `runway:not-applicable` and the fit no
  funding risk; the Peer Table gap is `not_applicable`; the user's read adds `runway_not_applicable`. Screens show a
  quiet "n/a" with `platform_values.words.RUNWAY_NOT_APPLICABLE`, never a warning (invariant 7).
- **What does not change:** the cash and burn are still extracted, stored, shown and flaggable.

Display only: nothing on the call path imports the rule.

## Narrowed fetches

- **A user's extract is purity only.** For a plain user, `GET /workbench/securities/{id}/extract` (own card, the
  extract allowance) runs `ingest.edgar.extract.extract_purity_for_security`: the submissions index, companyfacts and
  one 10-K or 10-Q, never the values filing, an annual document or an exhibit. A 20-F or 40-F filer answers
  `purity-not-covered`. The steward gets the full extract (`tests/ingest/test_extract_purity.py`,
  `tests/app/test_user_fetches.py`).
- **Steward-only:** `POST /workbench/facts/explain`, `POST /workbench/securities/{id}/ingest-prices`, the ETF resolve
  and the business-type re-tag (`require_super_admin`; `tests/app/test_auth_gate.py`, `STEWARD_PATHS`).
  `POST /workbench/facts` refuses a share count or cash from a plain user (403) and the retired catalyst form (409).
- **A fund or SPAC attach loads like a promote:** `POST /radar/funds/attach` and `POST /radar/spac/attach` call
  `app.routers.workbench.start_member_load` when they added a company.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `GET /platform-values/...` | anyone signed in | `require_user` (`_USER`, `app/main.py`) |
| `/admin/platform-values/*` (12 routes) | steward | `require_super_admin` (`_STEWARD`); `test_admin_platform_values_api.py` |
| `POST /workbench/facts`, share count or cash | steward | the route's `is_super_admin` check |
| Nightly and onboarding extraction | machine | [PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md) |
| `pipeline.extract_values`, the two repair CLIs | operator | by hand; dry run by default |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `ALPHADECK_INGEST_LOCK_REQUEST_WAIT_S` (every action's lock wait) | `domain/settings.py` | no |
| `annual_shares_auto_max_age_days` | `ExtractorConfig` in `domain/config.py` | no (a domain dial) |
| Extraction settings, the kill switch | [PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md) | yes |
| The flag allowance, the steward's Slack webhook | [VALUE_FLAGS](VALUE_FLAGS.md) | yes |

## Known limits

- `pipeline/dedup_identical_versions.py` deletes duplicate versions in every tenant and follows none of the repair
  rules; the writer guard lists it so a new writer still fails and calls its V2 rules a follow-up.
- An older candidate reopened by an undo waits in no queue.
- A company fixed by a same-day annual tie is not retaken by the nightly until a new periodic filing (its attempt
  is `done`); the steward presses Extract now.
- The user's read is current only.

## Related

[PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md), [VALUE_FLAGS](VALUE_FLAGS.md),
[WORKBENCH_EXTRACTION](WORKBENCH_EXTRACTION.md), [WORKBENCH_SCORING](WORKBENCH_SCORING.md), [ADMIN](ADMIN.md),
[AUTH](AUTH.md) (allowances), [TENANT_SCOPES](TENANT_SCOPES.md), [INVARIANTS](INVARIANTS.md).
