# Filings

What Alpha Deck stores from each SEC filing family, and how amendments and impossible dates resolve before
any reader sees the rows: Form 4 and 4/A, 8-K items, SC 13D/G, split filings and the filing index. The machine
writes every table here (the nightly ingest legs and the onboarding job); the steward rules on the few
amendments and held rows the system cannot resolve; users see the results through calls and dashboards. Read
this before you change an EDGAR ingest leg, the insider read seam or a filing repair. How a filing is fetched
and when it became public is in [DATA_SOURCES](DATA_SOURCES.md); what fires from these tables is in
[DETECTORS](DETECTORS.md); how fires become a call is in [CALL_LOGIC](CALL_LOGIC.md) section 3.

## Code

| Piece | Module |
|---|---|
| Enumeration from a company's submissions JSON | `ingest/edgar/submissions.py` |
| Form 4 parse, held rows, other-issuer screen | `ingest/edgar/form4.py` |
| Form 4/A ingest, link and system decisions | `ingest/edgar/form4a.py` |
| 4/A rules: dispositions, row match, voids, words, composition | `domain/insider_amendments.py` |
| Held rows: when a row with an impossible date counts | `domain/insider_holds.py` |
| Joint reports, internal transfers, private purchases | `domain/insider_joint_reports.py`, `domain/insider_internal_transfers.py`, `domain/insider_private_purchases.py` |
| The insider read seam, both engines | `domain/insider_amendments.ResolutionInputs.resolve`; `db/insider_resolution.py` |
| 8-K items; succession forms | `ingest/edgar/form8k.py`; `domain/edgar_forms.py` |
| SC 13D/G | `ingest/edgar/schedule13.py` |
| Split filings | `ingest/edgar/split_filing.py`, `pipeline/backfill_splits.py`, `pipeline/detect_splits.py`, `pipeline/split_history.py` |
| The filing index | `pipeline/ingest_thesis._filing_index_leg`, `submissions.index_filings` |
| Nightly 4/A sweep and owed-item scan; held-row scan | `pipeline/insider_amendment_scan.py`; `pipeline/insider_hold_scan.py` |

## How it works

**What every filing leg shares.** Each leg enumerates from the company's own submissions JSON, one cached read
the legs share, so the 8-K, 13D/G and index legs cost no extra enumeration request; a filing document is fetched
once into the immutable `forms/` cache. Only the `filings.recent` window is read ([DATA_SOURCES known
limits](DATA_SOURCES.md#known-limits)). Rows are append-only (`no_update` triggers); `recorded_at` is the
database's `now()`; `valid_from` is the filing's public date. Everything is stored and policy is applied on
read, so a 13G, an 8-K item outside the detector cut or a succession report stays on the tape for a later reader
without a re-ingest (invariant 9). Every fact table here is platform data in the data tenant.

### Form 4

`fact_insider_txn` holds one row per non-derivative transaction (`parse_form4`): owner name, role and CIK, code,
shares, price, date, issuer CIK, the security title and the filing's foreign trading symbol, and the filing's
`accepted` time. A joint filing's co-owners are not stored: each transaction is one row under the first
reporting owner, so one filing never double-counts. Later captures are nullable and progressive (a row stored
before a capture stays NULL until a history repair appends a version):

- **The Rule 10b5-1 checkbox** (`aff_10b5_1`): filing-level and tri-state: true is a planned trade, false
  discretionary, null unknown, and null never reads as false; that would assert a fact the filing never stated.
  SEC added the checkbox in its December 2022 amendments, so most older filings have none. The
  sell detector screens an explicit true; the buy side weighs it by `insider_10b5_1_buy_weight`, a dormant dial
  (`domain/config.py`); a 4/A that moves it is a field change. `pipeline.backfill_aff10b5_1` fills rows stored
  before the capture from cached filings.
- **Ownership evidence** (migration 0054): `direct_indirect` and `shares_owned_after`, the joint-report rule's
  only inputs. A malformed holding costs the row its evidence, never the filing its ingest.
- **The filer's own words about whose shares** (migration 0055): `ownership_nature`, `txn_footnotes` (every
  footnote the transaction cites) and the filing-level `filing_natures`. They link internal transfers and
  locate private purchases.

**The other-issuer screen.** A company's submissions feed also lists the ownership filings it made as the
reporting owner of another company's stock. A Form 4 or 4/A whose issuer CIK is not the member's is not stored
(a counted `form4_other_issuer` skip). Rows stored before the screen are kept and screened at read; the
member's CIK set includes every steward CIK re-point, so an Adopt never hides a predecessor's own history.

### Held rows: a date that cannot be true

A row is held at ingest, in `insider_txn_hold` (migration 0082) and never stored as a fact, when its date is
before 1934, after its accession's filing year, or (with the acceptance known) after the acceptance's market
date and after the filing's last signature date (`form4.implausible_txn_date_code`; the signature test keeps a
filer in a time zone ahead of New York). No date is invented or clamped (invariant 3). The read,
`domain.insider_holds.resolve_holds`, decides when a held row counts (rule `2026-11-insider-rows-dated-after-filing`):

1. A stored row that also has a hold is never read at its written date.
2. A held row of a withdrawn or duplicate filing, or one a 4/A replaced, is not returned
   (`insider_amendments.suppression_of`, the same suppression the stored rows get).
3. The filer's own applied 4/A carrying the same person, code, shares and price wins: the trade counts once.
4. Otherwise the newest standing steward decision (`insider_txn_hold_decision`, migration 0083: approve at a
   date, or dismiss).
5. Otherwise, for an on-time filing (accepted within `insider_hold_on_time_days` of its period of report), the
   row counts from the filing night (`date_basis = 'filing_night'`). The detectors' below-the-day's-low screens
   skip such a row but keep it counted.
6. Otherwise it waits for the steward.

A hold reads its filing's acceptance through `db.public_clock.hold_accepted_sql` (rule
`2026-11-held-row-reads-its-filings-acceptance`): its own, else the newest one stored on the same filing's rows
of the same security, never another security's or a filing date; a 4/A hold keeps its own.
`pipeline/insider_hold_scan.py` tells the steward once per new held row.

### Form 4/A: stored apart, resolved at read

The Form 4 leg reads every 4/A in the same submissions document after the night's originals
(`ingest/edgar/form4a.py`), so an original and its amendment arriving together link. A 4/A is stored as filed in
`insider_amendment`, with an append-only `insider_amendment_link` naming its original (migration 0050), and never
in `fact_insider_txn`: its natural key carries the accession, so a clerical amendment there would double-count
and a zero-row void would remove nothing. A company still onboarding reads no 4/A until its full walk has stored
the originals.

**The link.** Step 1: a stored Form 4 of this security filed on the amendment's `dateOfOriginalSubmission`,
belonging to one of its reporting owners. Step 2: an earlier Form 4 of the same owner sharing transaction dates.
Else unlinked. Several identical candidates are filing-agent duplicates; several that differ are ambiguous. An
original is never one made public after its amendment (`made_public_after`, both instants from the submissions
index, never the stored `accepted` column). An ambiguous set links to the one candidate holding a row the
amendment accounts for by exact shape (`row_tie_break`); failing that, when every amendment row matches one
candidate by shares and price, the system picks it (`sole_match`, basis `system:sole-match`). The linker sees an
original's held rows as rows of that filing, with no date (`with_held_rows`, rule
`2026-11-amendment-linker-sees-held-rows`), pairing a held row first and never by date.

**Dispositions** (`classify`): correction (1:1), void (zero rows against an original with rows), duplicates,
partial, consolidation, addition, restatement, field change (Table I identical but a signal row's owner role,
name or 10b5-1 flag moved; migration 0051), ambiguous; inert when clerical, rowless or unlinked.

**The system applies the filer's own correction.** A Form 4/A is the insider's legal correction of their own
filing, filed under penalty: deterministic data, not a model's recommendation (invariant 10). So every amendment
linked to a definite original gets a system decision, append-only, in the link's transaction at ingest, or from
the nightly sweep (`run_amendment_sweep`) for anything never decided. An `apply` carries basis
`system:filer-correction`, or `system:no-signal-effect` when no row of a signal code changes; a void its words do
not retract gets a `keep` (`system:no-retraction`, or `system:held` when owed). The signal codes are derived from
the detectors' declarations (`signals.horizons.insider_signal_codes`), never copied.

**The row match** (`match_rows`). A partial, consolidation, addition, restatement or field change replaces only
the original rows it accounts for, by exact shape, same owner first: identical; a price fix; a recode; a date
fix; a same-day sum (a consolidation, or one sale split into lots); a share fix. A bare same-date, same-code pair
never matches. An unaccounted original stands; an unmatched amendment row is an addition. The match is stored on
the decision (`replaced_rows`, migration 0053); NULL means the whole original. Leftovers of one owner and code
are settled by the filer's words, below.

**Voids** (`void_reading`). A zero-row 4/A retracts its original only when its text carries retraction words
and the original holds exactly one transaction, a buy or a sale. Without retraction words the original stands;
retraction words against several trades hold it for the steward.

### The filer's words

Deterministic phrase lists over the 4/A's remarks and footnotes (`RETRACTION_PHRASES`, `FIX_PHRASES`,
`ADD_PHRASES`, `SUPERSEDE_PHRASES` in `domain/insider_amendments.py`), never a model. On leftovers: FIX words
replace them; ADD words keep every original and add; neither leaves the item unresolved, the original standing;
both read as FIX when the amendment re-reports at least as many rows in every leftover group, else unresolved.
Words are never mapped to codes. A phrase names a transaction, never a row, which is why a void acts only on a
single-trade original. `tests/domain/test_insider_amendments.py` and
`tests/db/test_insider_amendments.py::test_the_real_row_check_table` pin the lists on real filings.

**Composition** (`compose_statements`). Every applied amendment of one original counts, in SEC-acceptance order
(`statement_order`): a whole replacement supersedes the original and every earlier amendment; a row-level one
replaces only the rows of the same trade it accounts for in an earlier amendment; one whose words supersede an
earlier amendment replaces that amendment's rows.

**What the steward rules on.** Only what the system cannot resolve: an ambiguous link, an unresolved row match or
a held void, and only inside the review line (`review_line`: a buy or sale a live call or an edition page can
read, from the detectors' own horizons and the earliest edition night; `insider_amendment_review_horizon_days`
pins a flat window instead). New items page once through the nightly scan. Older ones are listed as historical,
originals standing, still decidable. The verdicts are Use the correction, Keep the original and Add to the
original, each with an exact Undo; the steward can also Undo any system decision. Meanwhile a pending void or
duplicates set suppresses its original's rows; other pending kinds change nothing.

**Time.** On history views each input counts from its public instant: the 4/A, its link and every system
decision from the amendment's acceptance, a steward decision from when it was made
([invariant 1](INVARIANTS.md#1-no-lookahead-history-is-judged-on-the-public-clock)). Repairs append at `now()`:
`pipeline.relink_amendments` (a link the fixed cascade moves), `pipeline.rederive_amendment_matches` (an old
whole-original decision given its row match); both revoke and re-decide, never rewrite.

### Joint reports and internal transfers

`ResolutionInputs.resolve` is the one seam every as-of read of `fact_insider_txn` passes through, on Postgres
and the replay mirror alike, in this order: 4/A resolution, held rows, joint reports, internal transfers.

- **Joint reports** (`collapse_joint_reports`). Rows identical on date, code, shares, price and post-trade
  holding, from two or more reporting owners in separate filings, are one trade reported by each party with an
  interest (a fund and its partner, a spouse, a family vehicle). Two direct holders never merge; rows of one
  filing never merge; a post-trade holding of zero or equal to the shares merges only between filings a
  non-trivial holding already linked; a row without the 0054 evidence never merges, so it can only stay
  double-counted. The survivor is the direct holder's row when there is one; it keeps its name for the
  distinct-insider count and carries every co-reporter's role and filing.
- **Internal transfers** (`screen_internal_transfers`). A buy and a sale on one security with the same date,
  shares and price are one transfer when the legs' own filings link their owners: the same filer, a shared
  holding, or a holding whose nature names the other leg's owner. Both legs stay on the read, tagged; the
  detectors leave them out and name them. Evidence is pair-local, so every reader screens alike. A private sale
  between unrelated people is real conviction and stays counted.
- **Private purchases** (`insider_private_purchases.py`): a counted buy whose own cited footnote says it was
  bought privately carries a quiet Event Ledger note. Display only; never a call input.

`pipeline.repair_insider_ownership` and `pipeline.repair_insider_filer_text` append the 0054 and 0055 evidence
to stored rows from cached filings; a row whose filing is not cached stays bare and never merges or screens.

## 8-K items

`fact_corporate_event` (migration 0038) holds one row per member 8-K: form, the SEC item codes (`items`),
accession, filing date, `accepted` and the index link, all from the submissions JSON the Form 4 leg already
reads. `items` is NULL until EDGAR classifies the filing, and a new version is appended when it does. The forms
are `domain/edgar_forms.CORPORATE_EVENT_FORMS`, the one list: `8-K`, `8-K/A` and a successor issuer's first
report (`8-K12B`, `8-K12G3`, `8-K15D5` and amendments). A succession report is stored but never read by a
detector, cut by form in `signals/corporate_events.event_facts`, because its items describe the reorganization
itself. The item cut lives in `CallConfig.corporate_event_items` and is applied on read, so a retune needs no
re-ingest; `corporate_risk` and `corporate_catalyst` read this tape (switches in `domain/config.py`). The natural
key carries `security_id` (migration 0038).

## SC 13D/G

`fact_activist_stake` (migration 0039) holds every 13D/G-family schedule filed about a member, enumerated from
the member's own submissions JSON (EDGAR lists a schedule under both filer and subject). The form type is the
classification: 13D is a stake with intent, 13G passive.

- **Eight form strings.** EDGAR renamed the forms at its structured-XML cutover (`SC 13D` became
  `SCHEDULE 13D`); `submissions.SCHEDULE13_FORMS` matches both eras, or every newer filing would drop silently.
- **The true subject.** A 13D-family filing whose cover (`issuerCIK`) or header (`SUBJECT COMPANY`) names
  another company is the member's own outbound stake and is skipped, counted; an unresolved subject keeps the row.
- **Filer identity** costs one immutable fetch per filing: the structured cover (`reportingPersonName`, CIK,
  `percentOfClass`) or the classic header's `FILED BY` block. A classic-era 13G is not fetched and keeps a NULL
  filer by design. A failed identity read stores the row with a NULL filer and a loud counter; a later run
  appends a version when it resolves.
- **The date** is the public date, never the stake crossing inside the filing (`dateOfEvent` is never read for
  time).

The `activist_stake` detector fires from this tape (`signals/activist_stake.py`; switch `activist_stake_enabled`
in `domain/config.py`).

## Split filings

The split list (`security_split`) confirms a split only when two independent sources agree exactly on ratio and
first session (`domain/splits.decide`); the company's own filing is one source, Massive.com's split reference
the other. The filings read: 8-Ks under items 3.03 and 5.03 (a charter amendment) and 7.01 and 8.01 (without
one), 6-Ks and Reg A forms (`backfill_splits.SPLIT_8K_ITEMS`, `SEC_FORMS`). Each is read once, as its full
submission, by the deterministic extractor `ingest/edgar/split_filing.py`, keeping the located passage verbatim.

- **Screens.** A date more than `HISTORICAL_GAP_DAYS` before the filing is a past split's, never this
  filing's. A 6-K or charter 8-K goes through the restatement screen (`extract_split_screened`), which only
  removes readings: a report restating an old split never lists it as new. Each filing is read knowing the
  company's confirmed splits (`backfill_splits.known_for`): a ratio equal to one, stated as done, is that past
  split's whatever its age.
- **The wide-form reader.** A Massive.com split with no agreeing filing in its window gets one more look at the
  company's periodic reports, ADR registrations, prospectuses and information statements around its first
  session (`WIDE_FORMS`; a periodic report's primary document only): a confirmation search, never nightly for
  every company.
- **Distributions.** A forward split under x1.5 that only Massive.com lists, with no filing in its window
  stating it or mentioning a split, is set aside as a distribution (`distribution_reason`; settings
  `split_distribution_auto_set_aside`, `split_distribution_max_factor`). A wide form can still confirm it. So
  is one Massive.com labels a stock dividend whose filing mentions a split without a ratio
  (`split_distribution_label_overrides_abstain`); a filing that states it still sends it to review.
- **Who runs it.** `pipeline/detect_splits.py` checks each company nightly inside its ingest, before its price
  leg; the stored price tape can confirm a split one documentary source states, never alone. Filings it cannot
  place go on the run artifact with link, passage and ratio; `detect_splits --record` keeps a long-window run's.
  `pipeline/split_history.py` gives a company joining a basket its history at onboarding.
  `pipeline.backfill_splits` is the operator's CLI. Nothing a steward decided is overwritten; the steward reviews
  the list on Admin ("Splits").

## The filing index

`fact_filing_index` (migration 0070) stores every row of a company's `filings.recent` (any form), one per
accession, from the cached submissions document: zero extra requests. It is insert-only: a stored accession is
never re-versioned, because SEC's listed fields drift. `valid_from` is the filing date; `accepted` is set only
when the listed time is trusted. It is display only: research dashboards read it, no detector does, it is kept
out of the replay mirror (`db.bitemporal.DISPLAY_ONLY_FACT_TABLES`), and its failure never withholds a call.

## Contracts

- `fact_insider_txn`, `insider_amendment*`, `insider_txn_hold*`, `fact_corporate_event`, `fact_activist_stake` and
  `fact_filing_index` are append-only; a correction is a new version or an append-only decision stamped `now()`.
  Nothing here deletes a row.
- One read seam (`ResolutionInputs.resolve`) for Postgres and the replay mirror; the parity tests under
  `tests/replay/` (`test_pit_insider_amendments.py`, `test_pit_joint_reports.py`,
  `test_pit_internal_transfers.py`) hold them row for row.
- A detector that reads `fact_insider_txn` declares its codes or `reads_insider_roles`, or the detector registry
  raises (`signals/horizons.py`).

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| Filing legs, the 4/A sweep and scans | Machine (nightly pass, onboarding job) | `pipeline/daily.py`, `backend/scripts/daily_cron.sh` |
| 4/A panel: apply, keep, add, link, undo-pick, revoke (`/admin/insider-amendments/...`) | Steward | `require_super_admin` on the router; `tests/app/test_auth_gate.py`, `tests/app/test_admin_insider_amendments_api.py` |
| Held rows: approve, dismiss, revoke (`/admin/insider-holds/...`) | Steward | `require_super_admin`; `tests/app/test_admin_insider_holds_api.py` |
| Split list review (`/admin/splits/...`) | Steward | `require_super_admin`; `tests/app/test_auth_gate.py` |
| `relink_amendments`, `rederive_amendment_matches`, `repair_insider_ownership`, `repair_insider_filer_text`, `backfill_aff10b5_1`, `backfill_splits` (all `pipeline.`) | Operator | Dry run by default; the call-of-record or repair lock; the `data-repair` skill |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| `insider_hold_on_time_days`, `insider_10b5_1_buy_weight`, `corporate_event_items`, `activist_stake_enabled` | `domain/config.py` (`CallConfig`) | No (config, not environment) |
| `ALPHADECK_INSIDER_AMENDMENT_REVIEW_HORIZON_DAYS` | `domain/settings.py` | No |
| `MASSIVE_API_KEY` (the split reference) | `domain/settings.py` | Yes |
| `ALPHADECK_POLYGON_SPLITS_PATH` (`/stocks/v1/splits`; `/v3/reference/splits` = the deprecated fallback) | `domain/settings.py` | Yes |

## Known limits

- Only `filings.recent` is enumerated ([DATA_SOURCES](DATA_SOURCES.md#known-limits)).
- A row stored before a capture and whose filing is not cached stays bare: counted, never merged or screened by
  name.
- The share-fix shape can pair a 0-share, zero-price line with a same-price lot of the same owner and date (one
  known non-signal row).
- A spouse's indirect row that matches another owner's direct trade with a different post-trade holding is not
  collapsed outside an internal transfer.
- The `supersedes` column on `fact_insider_txn` is vestigial for amendments.

## Related

[DATA_SOURCES](DATA_SOURCES.md), [DETECTORS](DETECTORS.md), [CALL_LOGIC](CALL_LOGIC.md) section 3,
[ADMIN](ADMIN.md) (the 4/A, held-row and Splits panels), [INVARIANTS](INVARIANTS.md) (1, 3, 9, 10).
