# Extractor

The extractor reads a company's SEC filings and returns one candidate per scoring fact: the share count, cash and
burn, and purity. Each candidate carries a tier, the flags it observed, a note on how it was read, and the filing
passages behind it. It is deterministic and decides nothing; the machine writer, the steward or the user decides what
is stored. The machine and the steward get every candidate; a plain user gets purity only. Read this before you
change `ingest/edgar/extract.py`, the annual modules beside it, what counts as cash, or a flag. What happens to a
share count or cash candidate: [PLATFORM_VALUES](PLATFORM_VALUES.md). When the machine runs it:
[PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md).

## Code

| Piece | Module |
|---|---|
| The candidate model: `Tier`, `ExtractedFact` (every flag listed in its comments), `LocatedPassage`, `ExtractionResult` | `domain/extraction.py` |
| Routing and the periodic (10-Q/10-K) path: `extract_with_annual_fallback`, `extract_for_security`, `extract_facts`; the user's entry `extract_purity_for_security` | `ingest/edgar/extract.py` |
| Annual cover shares (20-F, 40-F) | `ingest/edgar/annual_shares.py` |
| Annual statements cash and burn; the annual entry `annual_facts_for_security` | `ingest/edgar/annual_runway.py` |
| Where annual statements come from | `ingest/edgar/statement_sources.py` |
| The year-to-date pace | `domain/burn_pace.py` |
| Dials | `ExtractorConfig` in `domain/config.py` |
| Model seams (prompts in `llm/prompts/`) | `llm/flag_explanation.py`, `llm/purity_estimate.py` |
| Screens | user: `frontend/src/workbench/FactsPanel.tsx`; steward: `frontend/src/admin/CompanyFacts.tsx` |

## Rules on every path

- **A candidate or estimate is never a fact row.** The as-of read (`db/bitemporal.as_of`) has no ratifier filter and
  feeds the call's detectors, so a stored estimate would reach calls without an error
  ([invariant 3](INVARIANTS.md#3-the-model-augments-it-never-sources-a-number-or-a-firing)). Cache outside the
  `fact_*` tables, never inside.
- **It locates; it never invents.** A value is a filed number or a sum of filed numbers of one date. A gap is never
  filled by subtraction, and a check line is never an addend.
- **Missing is not zero.** A missing input is its own flag with no value; a missing component is said missing in the
  note, never "$0.0M".
- **One observed condition, one flag.** A flag marks an exception that needs judgment; composition (a derived
  quarter, the tags that make up cash) rides the note. A flag true of almost every filer carries no information.
- **Fail closed, on the conservative side.** No exact match means no value, never a looser fallback; between two
  plausible sources it withholds. Understated cash reads a shorter runway, never a longer one.

## Callers

| Caller | Who | Entry | Gets |
|---|---|---|---|
| Background extraction, Extract now (`platform_values.extraction.extract_one`) | machine; steward | `extract_with_annual_fallback(..., values_only=True)` | every candidate; purity from the values filing (unused) |
| `GET /workbench/securities/{id}/extract` | steward | `extract_with_annual_fallback` | every candidate, both paths |
| the same route, and the `get_extract` agent tool on a user's token | plain user | `extract_purity_for_security` | purity only |

A plain user's extract reads the submissions index, companyfacts and one document (the latest 10-K, else 10-Q), never
the values filing, an annual document or an exhibit. A company whose newest periodic filing is a 20-F or 40-F answers
`purity-not-covered` after the submissions read (`tests/ingest/test_extract_purity.py`,
`tests/app/test_user_fetches.py`).

## Routing: which path, which filing

- **The path** (`_takes_annual_path`): the latest 10-Q, 10-K, 20-F and 40-F are compared by when each became public.
  A 20-F or 40-F newest, or no 10-Q or 10-K at all, takes the annual path; otherwise, ties included, the periodic
  path. Forms match exactly: an amendment, 8-K or 6-K never decides. Why: a company that moved to 20-Fs once had its
  20-F numbers applied as AUTO under an old 10-Q's link.
- **The values filing** (`_newer`): the share count and cash and burn cite the newer of the latest 10-Q and 10-K by
  public time (trusted acceptance, else the end of the filing date; never the period of report), so link, period and
  passages belong to the filing whose numbers companyfacts serves. A tie keeps the 10-Q. Purity prefers the 10-K's
  segment note (`tests/ingest/test_extract_newest_filing.py`).
- Each document is read once, only when a role needs it; `values_only` never reads a second one.
- A candidate's `event_date` is the value's own valid time (the cover's as-of date, the burn period's end), never the
  filing date. When it became public is `db/public_clock.py`'s rule.
- **Empty results say which nothing**: `empty_reason` is `no-annual-filing` (nothing on EDGAR, the only true
  "nothing"), `cover-not-located` (a filing exists, unread) or `purity-not-covered`; `runway_empty_reason` is the
  annual runway leg's own state (below).

## The three tiers

| Tier | Meaning | Used for |
|---|---|---|
| AUTO | companyfacts reproduces one clean value | a current single-class cover count; a clean quarter of cash and burn |
| FLAG | the raw value (or none), the flags observed, the located passages | whatever cannot be established; every annual candidate |
| HUMAN | located only, never valued | purity, always |

A FLAG may carry no value (`no-companyfacts`, a dual-class cover with fewer than two class counts): a flag never
anchors a person to a wrong number.

## Shares on the periodic path

- **AUTO** needs one distinct `dei:EntityCommonStockSharesOutstanding` value on the latest cover date, dated on or
  after the period of report (`reportDate`). Why not the filing date: a 10-Q cover is dated between the period end
  and the filing date, so that comparison made AUTO unreachable.
- **`dual-class`**: more than one value on that date, or two or more per-class counts parsed from the cover text
  (`_cover_class_sum`; companyfacts drops the class dimension). The value is the sum: class B is economic common
  stock and the A/B split is about votes, so the market cap counts both. Fewer than two parsed classes: no value.
- **`stale-cover`**: one count older than the period; offered, dated by its own as-of date.
- **`no-companyfacts`**: nothing observed; located only.
- **`cover-after-filing`** (`screen_cover_rows`): a cover row dated after its own filing is refused first and named in
  the note, beside the condition flag on any tier. A count is never public before its filing; the bound is the filing
  date because companyfacts carries no acceptance time. The annual reader applies the same rule
  (`tests/ingest/test_cover_dates.py`). `pipeline.repair_shares_cover_dates` re-dates stored versions (the
  `data-repair` skill).

## Cash and burn on the periodic path

### The uniform cash rule

`cash_usd` is one "cash and short-term investments" number: cash and equivalents, plus short-term investments, plus
long-term marketable debt securities, from one balance sheet. `CallConfig.cash_runway_basis` states it; the scorer
trusts `cash_usd` as stored. Why one basis: liquid Treasuries count however the filer classifies them, and mixed
bases made runways incomparable. The investment part rides as `investments_usd` (None: none tagged, never 0). The
tag lists are constants in `ingest/edgar/extract.py`, pinned by
`tests/ingest/test_cash_composition.py::test_the_tag_lists_as_built`:

| Constant | Role |
|---|---|
| `_CASH` | cash and equivalents; the newest label wins, list order breaks a tie; the restricted-cash total is last and the note says when it is used |
| `_STI`, `_LT_MARKETABLE` | counted investments, read at the cash date; per role the largest, never the sum (the tags restate one another) |
| `_DEBT_TOTALS` | unsplit debt-securities totals: a check beside their own parts; named, not counted, beside other tags; alone, counted only when the `_COMBINED` line equals cash plus the total |
| `_NOT_CASH` | generic long-term investments, often stakes in other companies: never cash, named in the note |
| `_COMBINED` | the combined cash-and-short-term line: a check, never an addend |

- A balance sheet is one date: an investment tag that stopped before the cash date is left out and named, no flag.
- Never read: equity securities, restricted investments, `AvailableForSaleSecurities` and `MarketableSecurities` (they
  can hold equity stakes), `DepositsAssetsCurrent` (it also holds deposits paid to vendors).
- Why a lone total is not added: tags cannot tell it from cash equivalents or restricted holdings, and a steward's
  approve stores the offered number, so the offer is the low one and the note asks for the eligible part.

| Flag | Condition |
|---|---|
| `cash-tags-disagree` | two cash tags of the cash date contradict their nesting (`_CASH_NESTING`) |
| `investments-unreconciled` | cash plus short-term investments miss the combined line by more than `investments_reconcile_tolerance`; an unsplit total smaller than its parts; a lone total unconfirmed |
| `investments-off-date` | an investment tag sits only on a newer balance sheet than the cash |

### The burn

- **Operating cash flow** is read under its most current label (`_OCF` or `_OCF_CONTINUING`; on a tie the plain
  total). A derivation subtracts within one tag only.
- **One quarter of the cited filing.** A year-to-date column minus the same-start prior is a derived quarter: AUTO,
  stated in the note. With no prior, the raw year to date goes out flagged `ytd-raw`, never as a quarter (it would
  read a runway several times too short). The span must lie within `quarterly_span_min_days` to
  `quarterly_span_max_days` (`burn-span`) and end on the cited period (`burn-off-period`); the cash must be of that
  date (`stale-cash` older, `cash-after-period` newer). Each failure offers the value as found; nothing turns a flag
  into AUTO.
- **`possible-one-time`**: a non-routine reconciliation line of at least `one_time_line_fraction` of the operating
  cash use. Routine working-capital lines (`_ROUTINE_WC`) never trip it however large: category, not size. It never
  branches on a named item; backing it out is a person's call ([WORKBENCH_SCORING](WORKBENCH_SCORING.md) "The
  recurring-vs-one-time burn pattern").
- **Missing inputs** carry no value: `no-companyfacts`, `no-cashflow-column`, `no-cash-instant`. Screens show them as
  data gaps, not warnings (`MISSING_FLAGS` in `frontend/src/components/LocatedPassages.tsx`).
- `source` names the cited form (`10-q` or `10-k`); the note dates every input; `statement_currency` is `USD`.

### The pace caveat

For a burn inside the quarter window, `domain/burn_pace.py` takes the same tag's year to date ending on the burn's
end (at most `pace_ytd_max_days`, the earliest-filed row) as a quarterly pace. `pace_divergence` calls it `opposite`
(opposite signs) or `divergent` (outside `pace_ratio_low` to `pace_ratio_high` times the pace). It adds
`ytd_quarterly_burn_usd`, `ytd_burn_days`, `pace_caveat` and a note segment, never a flag or a tier change. A first
fiscal quarter has none. Storage: [PLATFORM_VALUES](PLATFORM_VALUES.md) "Storage".

## The annual-cover path

A foreign private issuer files a 20-F (or a 40-F) instead of a 10-K or 10-Q. `annual_shares.py` reads its share count
from the annual cover; `annual_facts_for_security` fetches the annual document once for both annual paths. The tests
are the oracle: `tests/ingest/test_annual_shares.py` and `tests/fixtures/sec_extractor/annual/`.

- **FLAG only, by construction.** The periodic AUTO gate assumes a cover dated after the period end; a 20-F cover is
  dated at the period end by form instruction, so the gate would pass on a count a year old. The module never names
  AUTO (`test_annual_path_never_returns_auto`, a source scan); every candidate carries `annual-cover`. Whether the
  machine applies a clean one is the machine writer's rule: [PLATFORM_VALUES](PLATFORM_VALUES.md) "The annual
  share-count rule".
- **The cover is located by the form instruction, strictly.** The primary accepts "issuer" or "registrant" (the 40-F
  wording), an optional "each of", and a possessive written as an apostrophe, a space or an entity
  (`clean_filing_text` unescapes it); a secondary catches the direct-count phrasing when the primary fails. No match:
  no value, never a looser pattern (`cover-not-located`). Why: an EPS note ("number of outstanding shares, basic and
  diluted") deep in a large filing is a confident wrong match, and both instructions need "the issuer's" or "the
  registrant's".
- **Never sum the numbers on an annual cover.** "N shares, including M ADSs" names a subset; more than one number
  flags `multi-value-cover`. This differs on purpose from the periodic `_cover_class_sum`; do not unify them.
- **The later as-of date wins** between the cover and companyfacts' `dei` row; a tie goes to the cover, which carries
  the passage. A disagreement flags `source-disagreement`. A count below `annual_implausible_floor_shares` flags
  `implausible-count`, never dropped; one older than `annual_stale_cover_days` flags `stale-cover`.
- **The ADS ratio: apply where read, withhold where not.** The cover states ordinary shares; the price feed carries
  the ADS price. The ratio is read from the document already fetched (prose, the securities-registered table row,
  number words). `known`: the market cap divides by it and the fact keeps the ordinary count. `unread` (ADR
  evidence, no defensible ratio: missing, fractional or conflicting; `ads-ratio-unread`): the market cap is
  withheld, never guessed at 1:1, because a wrong ratio is a silent multiplying error. No ADR evidence: 1:1, stored
  NULL/NULL, which must read as not applicable: every domestic and older row has it, so "withhold" would blank
  every market cap (`tests/workbench/test_scoring.py::test_domestic_10q_market_cap_unchanged`). An F-6 filing is a
  positive ADR signal only; its absence proves nothing.
- Purity is not covered for an annual filer.

## The annual-statements runway path

`annual_runway.extract_annual_runway` reads cash and a span-normalized operating cash burn from the annual statements.
The tests are the oracle: `tests/ingest/test_annual_runway.py`, `test_statement_sources.py`,
`test_annual_statement_checks.py`, `test_annual_deposits_guard.py`, `test_annual_reaudit3.py` and their fixtures.

- **FLAG only.** Every candidate carries `annual-statements` and both rows (balance-sheet cash, operating-cash-flow
  total) as passages; no passage, no fact (`test_runway_path_never_returns_auto`). Annual cash and burn always wait
  for the steward.
- **The sign is the state.** Positive operating cash flow is cash-generative: no runway, a negative burn, a note.
- **Span-normalize.** Burn per day is the operating cash flow over its span, so a 3-, 6- or 9-month column is never a
  year. The span comes from an exact companyfacts row ending at the period of report, else the statement's period
  phrase, else a bare fiscal-year column on an annual form.
- **Unit scale is read**: the nearest marker in the row's own table header, shared by one filing's statements; none
  anywhere flags `unit-scale-unread`, figures as printed.
- **The current column** matches the report year to the year tokens of the row's own table header (not a row label's
  years or another table's). A duplicated report-year header flags `column-ambiguous` and withholds the value; a sign
  shared by every column still gives the state.
- **Currency is established, never guessed**, in order: (1) a same-date companyfacts row (us-gaap or ifrs-full, any
  unit) equal to the figure at its printed precision; a header naming another is `currency-mismatch`; (2) explicit
  header markers (`_CURRENCY_MARKERS`), a home currency beside a US$ convenience column winning
  (`convenience-column` when unconfirmed); (3) the filing's own presentation-currency statement with an explicit
  marker. A bare `$` or "dollars" names nothing (Canadian filers print `$`). Otherwise `currency-unread`: no currency
  stored, screens show `?`. Nothing converts figures (`domain/cash_currency.py`).
- **The consolidated statement, not a breakdown.** The cash row picked is the one companyfacts reproduces, else the
  first whose heading shows no breakdown cue (`_BREAKDOWN_CUE_RE`: parent-only, consolidating, VIE, segment), else
  the first, flagged `table-unverified`. The operating cash flow comes from the same statement set; a notes line is
  never the total (`_OCF_NOTE_GUARD_RE`). A located, unreadable cash row is `cash-unread`.
- **Cash is a closing balance, never a flow.** A movement, exchange-rate or opening-balance row is never valued. The
  cash-flow statement's closing line is the fallback when no balance-sheet row was read. A balance-sheet statement row
  beats a note or MD&A table printing the same figure.
- **Deposits and short-term investments come only from the same statement.** A row counts only when the cash row is a
  balance-sheet statement row, the row is its own line (not a component, total or summary), it shares the cash row's
  column grid, and its report-year cell holds a value (a dash, or a blank beside a note reference, is empty). The part
  comes from the cash value's currency column, never a convenience translation. Otherwise it is quoted, not counted:
  `investments-unconfirmed`. Marketable securities and long-term investments are not read here.
- **The cross-check**: companyfacts on the statement's own date must match exactly at the printed precision; a
  same-date row that reproduces nothing is `figures-disagree`, with both numbers. A deposits figure agrees only in the
  statement's currency, within its concept family, never with a concept another counted row owns. No same-date row is
  "not cross-checked", never a pass. A lag is stated, not flagged (normal for annual filers). The later as-of wins per
  quantity.
- **Where statements come from** (`statement_sources.STATEMENT_SOURCES`, first hit wins): `main_doc_statements` (the
  document already fetched), then `exhibit_statements` (the 40-F wrapper shape: statements in an EX-99 exhibit).
  Exhibits are chosen by SGML header type, not file name, fetched cache-first (unknown size first, then largest), at
  most `exhibit_scan_max`, each skipped one logged. The winner must carry both statement rows (`has_statement_rows`, the
  extractor's own locators); more than one is `exhibit-ambiguous`. Passages cite the winning document; cover shares
  always come from the primary document.
- **Runway empty states** (`runway_empty_reason`): `cash-generative` (a state, not a gap), `financials-in-exhibit`
  (a burning company whose statements were in no document read), `exhibit-ambiguous`, `statements-not-located`.
- **Staleness is annual-calibrated**: `stale-runway` only past `annual_stale_runway_days`.

## Located passages

A `LocatedPassage` is deterministic retrieval over the cleaned filing text, an anchor and a fixed window (`_locate`),
never a model's reading; its `kind` is cover, balance-sheet, cash-flow, segment or business-description, with the
filing's link. Purity passages are wider and ranked by how many financial figures they hold (`_segment_passages`), so
a segment table beats boilerplate. The annual cover passage is purpose-built (`annual_cover_segment_chars`) and records
its offset. Screens show passages inline (`LocatedPassages.tsx`): reading the passage is the decision.

## Purity: what a plain user's extract returns

Purity is the user's own judgment. With annual revenue on file the candidate locates the segment note (`source`
`10-k-segment`); without, the Item 1 business description (`10-k-business-description`). The extractor never values
it. With `thesis_id`, `propose_purity` proposes an on-thesis percentage read only from the located passages, tagged
`estimate_source='llm_proposed'`; a proposal not grounded, outside 0 to 100 or naming no segment is discarded, and any
model failure leaves the candidate HUMAN. Fake-client tests prove the plumbing only; groundedness takes a live run.

The user confirms or overrides in `FactsPanel.tsx`: `POST /workbench/facts` writes `revenue_mix` in their workspace,
`ratified_by` their email, `vouched` `confirmed` or `overridden` (provenance, never a scoring input). The flow:
[WORKBENCH](WORKBENCH.md) "Purity and shared values". "Fundamentals loaded" on the draft screen
(`memberHasFundamentals` in `frontend/src/workbench/format.ts`) means a purity or runway pip, a stored runway fact, or
a market cap or share count on file; the row badge shows only once some basket name has one.

## The explain seam

The steward's aid for a FLAG candidate: `explain_flag` drafts at most two sentences on what the flagged figure is made
of, grounded in its located passages. It lives on the steward's Admin company page (the Explain button on a waiting
candidate in `CompanyFacts.tsx`), which posts the candidate to `POST /workbench/facts/explain`. FLAG only: AUTO needs
no untangling and purity is the user's.

- **It can never become a fact.** The route takes no database connection and writes nothing, and no ratify request has
  an explanation field; the missing write path is the guarantee, the prompt a courtesy
  (`tests/app/test_workbench_api.py::test_explaining_writes_no_fact`,
  `::test_explanation_has_no_path_into_a_ratified_fact`).
- **Components and direction only.** The prompt allows the passage's figures and an adjustment's direction, never the
  adjusted value. That rule rests on the prompt alone, so only a live run tests it; the lever if it slips is a
  stronger `llm_model`.
- **Fail-open.** No key, a timeout or an ungrounded draft returns 200 with `grounded: false`, never a 5xx. A draft
  shows a "drafted" badge.

## Contracts

- `ExtractedFact` inside `ExtractionResult`; wire shapes in `backend/openapi.json`. The machine writer
  (`platform_values/machine.py`) and the steward's approve store a candidate's `source`, `source_ref`, `event_date`,
  `note`, currency, investment part, pace and ADS pair.
- `quarterly_burn_usd` is positive when cash was consumed (`domain.extraction.QUARTERLY_BURN_SIGN`, copied into the
  agent tools' descriptions).
- Flag strings are read by the machine writer (`ANNUAL_ONLY_FLAGS` in `domain/platform_values.py`) and the screens
  (`MISSING_FLAGS`); a new flag joins the list in `domain/extraction.py`.
- Every threshold is an `ExtractorConfig` dial. The extract and explain routes write nothing.

## Who can do what

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| `GET /workbench/securities/{id}/extract` | plain user: a company on one of their cards, purity only, one extract allowance unit; steward: any company, uncounted | `require_own_security`, `allowance.charge("extract")`, the `is_super_admin` branch (`tests/app/test_user_fetches.py`) |
| `POST /workbench/facts/explain`, the Explain button | steward | `require_super_admin` (`tests/app/test_auth_gate.py`, `STEWARD_PATHS`) |
| `POST /workbench/facts`, purity | anyone signed in, own workspace | `require_user` |
| `POST /workbench/facts`, share count or cash | steward; 403 for a plain user | the route's `is_super_admin` check |
| `POST /workbench/facts`, `catalyst` | nobody: 409, the form is retired | `CATALYST_FORM_RETIRED` (`tests/app/test_thesis_lists_api.py`) |
| Background extraction; Extract now | machine; steward | [PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md) |

## Settings

| Knob | Owner | Passed by compose? |
|---|---|---|
| Extractor dials (spans, one-time fraction, reconcile tolerance, pace, annual ages and floors, `exhibit_scan_max`) | `ExtractorConfig` in `domain/config.py` | no |
| `ALPHADECK_LLM_MODEL`, `_MAX_TOKENS`, `_TIMEOUT_S` (explain); `ALPHADECK_LLM_PURITY_*` (purity estimate) | `domain/settings.py` | no |
| `ANTHROPIC_API_KEY` (absent: both seams return nothing) | `domain/settings.py` | yes |
| `ALPHADECK_USER_AGENT` (SEC requires it) | `domain/settings.py` | yes |

## Known limits

- An ADS ratio change is read at the next annual filing; a filing stating both ratios withholds the market cap.
- An annual filer gets no purity candidate.
- The explain prompt's no-final-figure rule has no structural check; a post-filter for numbers absent from the passage
  is not built.
- The periodic cover-date bound is the filing date, not the acceptance.

## Related

[PLATFORM_VALUES](PLATFORM_VALUES.md), [PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md), [WORKBENCH](WORKBENCH.md),
[WORKBENCH_SCORING](WORKBENCH_SCORING.md), [DATA_SOURCES](DATA_SOURCES.md) (EDGAR etiquette, the cache),
[ADMIN](ADMIN.md), [INVARIANTS](INVARIANTS.md).
