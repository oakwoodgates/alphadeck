# WORKBENCH_EXTRACTION.md — the extract → ratify hybrid (and the first LLM seam)

> Repo path: `docs/WORKBENCH_EXTRACTION.md`. How the Workbench turns a name's SEC filings into the
> **ratified scoring facts** the meters read: the three-tier hybrid extractor, the operator ratify, and the
> one LLM aid in that loop (the flag-explanation drafter). Companion to `WORKBENCH_SCORING.md` (what the
> facts SCORE to — the consumption side), `INVARIANTS.md` (#1 no model-sourced numbers, #2 exact membership),
> and `ROADMAP.md` (Phase-2 sequencing). Engine: `backend/ingest/edgar/extract.py`; ratify:
> `backend/app/routers/workbench.py` + `backend/ingest/*`; the LLM seam: `backend/llm/`.
>
> **Status: BUILT** — hybrid-1 extractor (PR #55), hybrid-2a ratify endpoint (#56), hybrid-2b facts panel
> (#57), the flag-explanation drafter (#59). The narrative→chain drafter (S5, the SECOND LLM seam) is now BUILT — see `CHAIN_DRAFTER.md`.
> **The SURFACE shift (#109/#110/#111) then flipped the default** — facts went from blank-until-extracted to
> **recommended-until-confirmed** (the section below), adding computed-on-read estimates, the `vouched` provenance
> marker, and the **grounded purity-estimate seam** (the THIRD grounded LLM seam). This doc is the **SURFACE**
> stage's scoring-fact side; identity enrichment is its sibling, `WORKBENCH_ENRICHMENT.md` (see `STAGE_MODEL.md`).
>
> **Legend:** `[BUILT]` shipped · `[FILED]` deferred.

---

## Where the scored facts come from

`WORKBENCH_SCORING.md` covers how a name's facts become the four meters; this doc covers the step *before*
that — how the facts get into the store. The loop is **extract → ratify → score**, per name:

1. **Extract** `[BUILT #55]` — from the name's latest 10-Q/10-K + companyfacts, produce *candidate* facts
   (`domain.extraction.ExtractedFact`). Cache-first; an EXPLICIT operator action (never fired on a render).
   **Which filing each fact cites (V2 decision B1, 2026-11-14):** the share count and cash/burn cite the NEWER
   of the latest 10-Q and the latest 10-K, by when each became public (its trusted SEC acceptance, else its filing
   date — never its period of report), so the link, period and located passages are that filing's; purity keeps
   preferring the 10-K. Each document is read once, only when a role needs it (`values_only` skips the purity
   document). Before B1 the values always cited the 10-Q, so after a 10-K they showed the 10-K's companyfacts
   number under the older 10-Q's link.
   **Which path a company takes (V2 extractor correctness PR 2, 2026-11-15):** the 10-Q/10-K path when the
   company's NEWEST periodic filing — the latest 10-Q, 10-K, 20-F and 40-F compared by when each became public —
   is a 10-Q or 10-K; the annual path (below) when it is a 20-F or 40-F, or when there is no 10-Q/10-K at all
   (`_takes_annual_path`). A tie on public time keeps the 10-Q/10-K path. Before, any company that had EVER filed a
   10-Q took the quarterly path: the audit's five form switchers (EUDA, CURR, SCNI, CHR, BTCT) had the 20-F's
   companyfacts numbers applied AUTO under a 2020-2025 10-Q's link. A company that filed a 20-F years ago and files
   10-Qs now stays quarterly.
2. **Ratify** `[BUILT #56/#57]` — the operator confirms or edits each candidate; their value is written via
   the existing `ingest_*` bridges. The DD-rail facts panel is the surface.
3. **Score** — the meters re-derive on the next read (`WORKBENCH_SCORING.md`, Option B).

**The core principle: the extractor LOCATES, the operator RATIFIES.** The extractor auto-fills only the clean
cases; for everything else it puts the *right passage* in front of the operator and lets them ratify. This is
INVARIANT #3 (the LLM/automation never sources a number; `INVARIANTS.md` #1) at the data-entry boundary: a
stored scoring fact is always either a deterministic parse the operator confirmed or a value the operator
authored — never a guess the system made.

## The SURFACE shift — recommended-until-confirmed (#109/#110/#111)

The philosophy flip that defines the **SURFACE** stage: a scoring fact went from **blank-until-the-operator-
extracts** to **recommended-until-the-operator-confirms** — same invariants, opposite default. The operator's job
becomes JUDGMENT over a populated draft (confirm / override), not SEC data entry. **Three fact states** replace two:

- **system-estimated** — a machine-derived, UNVERIFIED value, visibly tagged with its source (`computed` /
  `llm_proposed`). **Never presented as a verified fact, never stored as one** (see the constraint below).
- **operator-confirmed** — the operator vouched for the estimate as-is (a ratified fact, stamped `vouched=confirmed`).
- **operator-overridden** — the operator changed the value (a ratified fact, stamped `vouched=overridden`).

`vouched` (the marker column, #109) is **PROVENANCE, never a trust tier** — a NULL (legacy manual ratify) /
`confirmed` / `overridden` fact all score **identically**; the scoring read never filters or branches on it. The
three states are **DERIVED per read** by comparing the computed estimate to the latest ratified fact — no ratified
fact → estimated; ratified == the shown estimate → confirmed; ratified differs → overridden. A
**`unconfirmed_estimates` count** on the scored member surfaces "rests on N unconfirmed" — honest confidence (#6).

> **⚠ ARCHITECTURAL CONSTRAINT — a SURFACE estimate is COMPUTED-ON-READ, NEVER a `fact_*` row.**
>
> **Not "should not" — MUST NOT.** The bitemporal as-of scoring read (`db/bitemporal.as_of`) has **no
> `ratified_by` filter**, and its `PointInTimeData` is the **shared** read for the Armed-call detectors. So **any**
> estimate written to a `fact_*` table would immediately, silently leak an **unverified number** into BOTH the
> Workbench score AND the back-half Armed call — a **#1/#3 violation that throws no error**; it just quietly
> corrupts the call. Therefore estimates live **only** in the extract endpoint's computed-on-read response; the
> **only** writer of a `fact_*` row is the operator's confirm/override (`POST /workbench/facts`,
> `ratified_by="operator"`). **The trap for a future author:** "cache the estimate for perf" or "persist it so the
> meter shows it pre-confirm" reintroduces exactly this leak — if you need it faster, cache OUTSIDE the `fact_*`
> tables / the as-of read, never inside. *(Same callout in `STAGE_MODEL.md`, on purpose — it must be impossible to
> miss.)*

## The three tiers

`extract_for_security` (the live cache-first wrapper) → `extract_facts` (pure, deterministic) returns exactly
three candidates — one per meter: `revenue_mix` (purity), `shares_outstanding` (market cap), `cash_burn`
(runway). Each lands in one of three tiers (`domain.extraction.Tier`):

- **AUTO — companyfacts reproduces a clean value → pre-fill, confirm-and-go.** The value is shown read-only;
  the operator confirms it as-is (no retype). Used only when the figure is unambiguous — e.g. a single-class,
  *current* cover-share count. **AUTO shares are now APPLIED on get-data, not confirmed** — see below.
- **FLAG — a raw value + a DETECTED risk + a LOCATED passage → the operator ratifies the composition.** The
  value is editable, pre-filled with the raw figure; the detected flag(s) and the filing passage backing them
  are shown inline. *Reading the passage IS the ratification decision.*
- **HUMAN — interpretation-bound (purity).** The extractor locates the evidence (the segment footnote or Item-1).
  Base behavior: LOCATED only, the operator authors the % from it. **SURFACE upgrade (#110):** when the extract is
  thesis-scoped, the **grounded purity-estimate seam** now PROPOSES an on-thesis % — *grounded in the fetched
  segment-footnote passage it carries* (never recalled from memory — a #1 violation would be inventing the number)
  — for the operator to **confirm or override**. It is a recommendation (#10), tagged `llm_proposed` + unverified,
  **fail-open to today's HUMAN** (no key / not-grounded → no value, the operator types it). Purity stays the
  operator's exposure-concentration edge; the model proposes a grounded starting point, the operator decides. The
  full seam is documented below.

## The detectors — what trips a FLAG (category, not size)

The detectors read `companyfacts`; every threshold is a dial in `ExtractorConfig` (`backend/domain/config.py`,
no magic numbers). **A FLAG marks an EXCEPTION needing judgment; COMPOSITION rides the note as provenance**
(the re-tier — honest loudness applied to the extractor itself: `ytd-derived` fired on ~3 of 4 filings [GAAP
10-Q cash-flow statements are YTD] and `verify-marketable-securities` on ~every real filer, so AUTO was
structurally empty and a wall of alarms drowned the rare flag that mattered). The judgment calls the operator
most needs to make:

- **The one-time detector is CATEGORY-not-size.** A burn quarter can hide a one-time outflow (a settlement /
  milestone payment) that makes runway look far worse than the recurring reality. The detector flags a
  **non-routine** operating-cash-flow reconciliation line whose magnitude is ≥ `one_time_line_fraction`
  (70 %) of |operating cash use|. "Non-routine" is decided **by category, not size**: routine working-capital
  swings (inventory, receivables, payables, deferred revenue, leases) are excluded *by category* even when
  large — LEU's inventory swing is **139 %** of its op-cash-use and correctly does NOT trip it — while an
  accrued / settlement / milestone line does (SMR's ENTRA1 settlement, ~84 %, trips it). The detector is
  **generic — it never branches on a named item** ("ENTRA1" appears only in a clarifying comment in
  `extract.py`, never in the logic); it locates the flagged AMOUNT (or a corroborating one-time keyword) in
  the cash-flow statement and lets the operator decide whether to back it out. → flag `possible-one-time`.
- **Operating cash flow is read under its most current label** (V2 extractor correctness PR 2):
  `NetCashProvidedByUsedInOperatingActivities` or `…ContinuingOperations`, the one whose newest column ends
  latest; when both reach the same period the plain tag (the statement's total, continuing plus discontinued)
  wins. The 2026-11-15 audit found 8 of 460 burns stale because the filer had moved to the continuing-operations
  tag (EHC's burn was from 2023, QXL's and T's from 2025). A derivation subtracts within one tag only: a total
  minus a continuing-operations part is not a quarter (it goes out `ytd-raw` instead).
- **YTD derivation — a clean derivation is AUTO; only the raw-YTD case flags.** companyfacts' cash-flow
  column is often year-to-date. When the latest span exceeds `quarterly_span_max_days` (100) and a same-start
  prior exists, the extractor DERIVES the quarter (YTD − prior YTD) — **reproducible arithmetic on two filed
  columns (the market-cap trust class), stated in the note (`derived (YTD − prior YTD)`), never a flag** — as
  long as the result covers about one quarter (the period rule below).
  When there is **no prior to subtract**, the value goes out as the **raw YTD → flag `ytd-raw`** with a
  passage saying it is NOT a quarter (the runway audit: the old single label claimed a derivation that never
  happened — runway would have read ~3–4× too short, believed). The one-time detector runs on the derived
  quarter too — a clean basis never bypasses the anomaly check.
- **Cash includes short-term investments and long-term marketable securities — same balance sheet only;
  the composition is a note, and a composition that cannot be established is a flag** (operator decisions D1
  and D2, 2026-11-15). `cash_usd` = cash + equivalents + short-term investments + long-term MARKETABLE
  securities: ONE "cash and short-term investments" number (the uniform cash rule, `WORKBENCH_SCORING.md`),
  with the investment part also carried on its own on the candidate (`investments_usd`; None = no investment
  tagged on the cash date, never a fake 0). It is not yet a stored column: the plan's migration for it is a
  follow-up (PR 1b). The tag lists (`ingest/edgar/extract.py`):
  - **cash** — `CashAndCashEquivalentsAtCarryingValue`, `…IncludingDiscontinuedOperations`, `Cash`, then the
    restricted-cash total (the newest label wins, the order breaks a tie; the note says when it includes
    restricted cash).
  - **short-term investments** — `ShortTermInvestments`, `MarketableSecuritiesCurrent`,
    `AvailableForSaleSecuritiesCurrent`, `AvailableForSaleSecuritiesDebtSecuritiesCurrent`,
    `DebtSecuritiesAvailableForSaleExcludingAccruedInterestCurrent`, `HeldToMaturitySecuritiesCurrent`,
    `OtherShortTermInvestments`, `CertificatesOfDepositAtCarryingValue`, `TimeDepositsAtCarryingValue`.
  - **long-term marketable securities** — `MarketableSecuritiesNoncurrent`,
    `AvailableForSaleSecuritiesNoncurrent`, `AvailableForSaleSecuritiesDebtSecuritiesNoncurrent`,
    `HeldToMaturitySecuritiesNoncurrent`.
  - **never cash** — `LongTermInvestments`, `EquityMethodInvestments`, `OtherLongTermInvestments`,
    `LongTermInvestmentsAndReceivablesNet` (the generic long-term investments line is often stakes in other
    companies: Disney stored 12.8bn against 5.2bn of cash, Kinder Morgan 8.8bn against 0.9bn). A same-dated
    one is NAMED in the note as not counted. Equity securities and restricted investments are never read.
    The cost: a Treasury holder that uses the generic line (SMR's 118.6M) reads lower, the conservative
    direction, and the note says why.

  Each investment tag is read **at the cash date**, and per role the **largest** same-dated value is taken,
  never the sum (the tags often restate one another); every tag seen is named with its amount. A missing
  component is said missing ("no short-term investments tagged on the cash date"), never "$0.0M".
  **A balance sheet is ONE date**: an investment tag that stopped before the cash date is a *different*
  balance sheet (usually a discontinued tag — MU's `AvailableForSaleSecurities*` last reported **2018**, and
  the old composer silently added those balances into current cash) and is **EXCLUDED from the sum**, named
  in the note with its date — no flag (it errs CONSERVATIVE: understated cash → runway reads *shorter*).
  **What the extractor cannot establish is a FLAG, never a silent AUTO:**
  - `cash-tags-disagree` — two balance-sheet cash tags of the cash date contradict their nesting (`Cash` ≤
    cash and equivalents ≤ the restricted-cash total): SOAR's cash and equivalents read 0 beside `Cash` 8.4M.
    Both are stated. `Cash` BELOW cash and equivalents is its cash part (OKLO), consistent, no flag.
  - `investments-unreconciled` — the filer also reports the combined `CashCashEquivalentsAndShortTermInvestments`
    on the cash date and cash + the short-term investments read miss it by more than
    `investments_reconcile_tolerance` (0.5%). The combined line is a check, never an addend, and the gap is
    never filled by subtraction (the extractor locates, it never invents). Cash read from the restricted-cash
    total overshoots the combined line, so it flags too: never a silent double count.
  - `investments-off-date` — an investment tag sits only on a NEWER balance sheet than the cash.
- **The cash candidate's `source` names the cited form:** `10-q`, or `10-k` when the values filing is a 10-K
  (since B1 the newer of the two by public time; before 2026-11-15 every value said `10-q`).
- **Missing inputs are their own labels with None values — never a fake $0.** No cash instant *and* no
  OCF column → `no-companyfacts` (located-only; the old `or 0.0` coercions sent such filers out AUTO / $0 /
  $0 / "Clean quarter" — a confirmable fake zero). Cash without an OCF column → `no-cashflow-column` (burn
  stays None; a $0 burn used to ratify into a fake "cash-generative"). An OCF column without a cash
  instant → `no-cash-instant`. The ratify UI gates Confirm on blank fields (`Number("")` is 0) and renders
  the missing-data labels **gray (∅), not warm (⚠)** — a data gap is an authoring state, not an alarm; the
  warm glyph is reserved for the judgment exceptions (one-time / stale / raw-YTD / dual-class).
- **The period and citation rule — AUTO only for one quarter of the cited filing** (V2 extractor correctness
  PR 2, 2026-11-15). The burn must span `quarterly_span_min_days`–`quarterly_span_max_days` (80–100, inclusive;
  native, or derived as the prior year to date's end → the period end), else `burn-span`: with the prior
  same-start year to date missing, the latest earlier one is subtracted (URI: nine months minus the first
  quarter, 183 days, stored AUTO at 2× before), and a native column under 80 days is short (AIAI, 54). The burn
  must end on the cited filing's period of report, else `burn-off-period`. The cash must be dated that period:
  older is `stale-cash`, newer is `cash-after-period` (RMIX's cash was 31 days after the period). Each failure
  offers the value as found, with its flag and a sentence in the note saying what was observed; nothing here
  ever turns a flag into AUTO. A raw year to date keeps its own `ytd-raw` (one condition, one label).
- **Every input's as-of date rides the note** ("cash as of … · burn over S → E (N days, tagged <tag>) · cited
  filing: the 10-Q for the period ending P (<link>)"), and a cash balance sheet older than the filing's period
  end flags `stale-cash` (the shares `stale-cover` rule, applied here).
  `event_date` = the burn period's own end (else the cash as-of) — the value's own valid-time, never the
  filing date.
- **Shares (market cap): three honest FLAG labels, one per OBSERVED condition.** A single-class, **current**
  cover concept → AUTO — where *current* is judged against the filing's **PERIOD OF REPORT** (submissions
  `reportDate`), never the filing date: a cover's "as of" date always falls *between* the period end and the
  filing date, so comparing against the filing date made AUTO unreachable live and mis-flagged every
  single-class name (the gate-3 finding; MU: cover 06-17 · filed 06-25 · period 05-28). Otherwise the flag
  names what was observed — a flag is evidence (#6), never a catch-all:
  - → flag `dual-class` — multiple classes OBSERVED: >1 distinct DEI values on the latest cover date, **or**
    ≥ 2 per-class counts parsed from the cover text (dual-class filers report DEI per class with dimension
    members companyfacts DROPS, so "no dei rows + a class-rich cover" is the common dual shape — LEU/SMR).
    Value = the cover A+B sum (total economic; the A/B split is voting, not economics), or **no value** if
    the cover regex yields < 2 classes — a FLAG never anchors the operator to a wrong number.
  - → flag `stale-cover` — a single-class count whose as-of date predates the period: a lagging
    companyfacts, not a class structure. The stale value IS offered, dated by its **own** as-of date
    (valid-time honesty); the operator confirms currency against the located cover.
  - → flag `no-companyfacts` — nothing observed anywhere: located-only, no value.
  - **Before any of these, a cover row dated after its own filing is refused** (`cover_after_filing`, 2026-10-29)
    and named by the flag `cover-after-filing` + a sentence in the note — beside the condition label, on any
    tier, so it rides into the stored fact's note on an auto-apply or a ratify. A count is never public before
    its filing: AerSale's 10-K/A tagged its 2024 cover count to an XBRL context dated 2034-03-05, companyfacts
    kept it, and the newest-`end` pick auto-applied it as ASLE's count. The bound is the FILING date (companyfacts
    carries no acceptance time; an acceptance is on or before its filing date). The annual-cover reader applies the
    same rule to its companyfacts side. MEASURED on dev's 838 cached companyfacts: 39 hold a refused row, the
    candidate changes for 2 (ASLE; GMER, a 10-Q cover dated 8 days after its filing, now `stale-cover`). The
    stored versions are corrected by `python -m pipeline.repair_shares_cover_dates`.

When no flag trips, `cash_burn` / `shares_outstanding` are AUTO; `revenue_mix` is always HUMAN.

## The AUTO-shares auto-confirm — removing a FAKE gate, not relaxing a real one `[BUILT]`

The AUTO shares confirm was **never a verification**. The operator has no independent knowledge of a share
count and is not going to look one up — clicking "Confirm" on the extractor's single-class cover figure
rubber-stamped it, while stamping `ratified_by="operator"`: **provenance asserting a check that never
happened**. So on **get-data** (both entry points — the per-name `⇣ get data` and the section runner) an
**AUTO shares count is now applied directly**, stamped **`ratified_by="auto"`**, which is what actually
occurs. This is a *net gain* in honesty (#6), not a loosened gate.

**The real human check is downstream and unchanged:** the operator reads the resulting **market cap** — a
figure they *do* have intuition for — and overrides the shares if it looks wrong.

**Why it is not a #1/#3 violation.** The written number is the extractor's deterministic reproduction of filed
companyfacts (the market-cap trust class) — never a model output, never a `computed`/`llm_proposed` estimate.
The architectural constraint above is untouched: estimates stay computed-on-read and never become `fact_*`
rows. AUTO is exactly the unambiguous case; anything ambiguous trips a FLAG and is excluded.

`POST /workbench/facts/auto-confirm { security_id, fact_type: "shares_outstanding" }` — the guarantees:

- **The request carries NO value** (the structural bound). The server re-extracts (cache-first) and writes its
  **own** parse, so no caller can inject a figure under the `auto` provenance.
- **AUTO-only, shares-only.** A FLAG (`dual-class` / `stale-cover` / `no-companyfacts`) declines with
  `applied=false, reason="not_auto"` and stays the operator's to ratify — the machine never resolves a class
  sum or judges cover currency. Purity is HUMAN (never auto-valued) and cash_burn stays a manual ratify;
  `fact_type` is a one-value Literal, so extending this is a decision, not a parameter.
- **Idempotent + never clobbers.** ANY existing shares fact (auto **or** an operator override) → no-op, so a
  re-run appends **zero rows** and a later get-data can't re-apply the machine value over the operator's
  decision. (Tested by COUNTING the table, not the read.)
- **Visible, never silent.** The section report says how many were auto-applied; the FactsPanel tags the fact
  **"✦ auto-applied — confirm or override"** and makes the field **editable** so the override is real (#1).
  Confirming as-is still means something: it appends an `operator` fact — a human vouching for it.
- **Legacy rows stay neutral.** ~108 historical rows carry `ratified_by="operator"` from the old ceremonial
  confirm, so the panel asserts "auto-applied" **only** for `ratified_by="auto"` and shows every other fact the
  neutral "✓ on file" — never "operator confirmed", which would claim a check that never happened. Those rows
  were **deliberately not backfilled**: `ratified_by` is a write-only column (nothing reads it; the as-of read
  never branches on it), correcting them would mean defeating the `no_update` append-only DB trigger or
  fabricating restatement events, and the note template
  (`Cover-page shares outstanding as of … (single class).`) identifies them provably if a consumer ever appears.

**The get-data control counts what's LEFT.** It used to hide once *any* fact was confirmed — already a bug
(ratifying one fact silenced a name with two outstanding), which auto-applying shares would have
industrialized on every clean name. It now tracks unratified candidates, using the same on-file predicate the
FactsPanel does.

## The annual-cover path — shares for a filer with NO 10-K/10-Q `[BUILT]`

(Also for a filer whose newest periodic filing is a 20-F or 40-F although it once filed 10-Qs: the routing rule
in "Where the scored facts come from".)

A **foreign private issuer** files a **20-F** (or **40-F** under the Canadian MJDS) instead of a 10-K/10-Q,
so `extract_for_security` had nothing to read and returned `[]` — no shares, therefore no market cap, for
roughly a fifth of a real basket. `annual_shares.py` reads the **annual cover** instead. Ground truth is
pinned in `backend/tests/ingest/test_annual_shares.py` + `tests/fixtures/sec_extractor/annual/` — one test
per hazard below; the tests are the oracle, not this page.

**AUTO is structurally unreachable here, and that is the point.** The `_shares` AUTO gate
(`latest_end >= period_end`) is justified on a 10-Q premise: *a cover is dated "as of the latest practicable
date" — after the period end.* **A 20-F cover is dated AT the period end, by SEC form instruction**, so that
gate passes **trivially, always**, while the value is a fiscal year old (measured: 204–569 days). Routing an
annual filer through it would pre-fill a year-old share count at the *lowest-friction* tier. The annual path
therefore lives in its own module and emits **FLAG only** — enforced by both a behavioral test and a
source-scan asserting the token `AUTO` never appears in it.

**The platform still applies a clean annual count (V2, the annual share-count rule).** The extractor's tier stays
FLAG; the decision to apply is the machine writer's, not the extractor's. A count whose only flags are
`annual-cover` / `stale-cover`, with its ADS ratio read or not applicable, no older than
`ExtractorConfig.annual_shares_auto_max_age_days` (400) on the market date, is applied as a platform value stamped
`auto` with its real as-of date, `source='annual-cover'` and its ADS ratio, and the note says it is an annual figure
(`docs/PLATFORM_VALUES.md`, "The annual share-count rule"). Every other flag, an older count and a pinned company
still wait for the steward. Annual cash and burn (the annual-statements path below) is never applied.

**Locating the cover = matching the SEC form instruction, not a keyword.** Three arms, each load-bearing:

- **`issuer` OR `registrant`** — the 40-F cover says *"of the Registrant's classes"* where the 20-F says
  *"of the issuer's classes"*. Matching only `issuer` silently drops every 40-F filer.
- **`each of` is optional** — some covers omit it.
- **possessive as `\W{0,3}s`** — the apostrophe survives as `'`, a space, or `&rsquo;` depending on filer.
  Only `clean_filing_text`'s `html.unescape` normalizes it; a hand-rolled entity strip misses those names.

A **secondary** strict instruction catches the direct-count phrasing some filers use instead of enumerating
classes — *"The number of the issuer's outstanding common shares … was 2,293,277"* — tried only when the
primary fails (so the primary names are untouched, and no filer matches both).

**Fail CLOSED.** No instruction match — *primary or secondary* — → **no value**, never a looser fallback. The
trap is a *confident wrong match*: `"number of outstanding shares - basic and diluted"` is an **EPS note**,
and in a 25 MB filing a loose pattern finds it hundreds of thousands of characters in. Both strict
instructions require *"the issuer's/registrant's"*, which the EPS note lacks, so it matches neither. Note
`_locate`'s ±110 window is too narrow for these covers (they run long), so the annual passage is
purpose-built rather than reusing it.

**Never sum the numbers on an annual cover.** A cover may read *"N shares of common stock, **including** M
ADSs"* — M is a **subset**, not a second class, and ADRs are common among foreign filers. Take the first
number; >1 number trips `multi-value-cover` for the operator to ratify against the passage. (This
deliberately differs from the 10-Q `_cover_class_sum`, which *does* sum genuine dual-class counts — a
different form with a different convention. Do not unify them.)

**Neither source dominates — the later as-of date wins.** `companyfacts` sometimes **lags** the filing (its
`dei` still on last year's annual report) and is sometimes **fresher** than the newest annual filing (a 40-F
landing after the last 20-F). So the rule is *later-as-of*, **not** *prefer-the-document*; ties go to the
cover, which carries the passage. Any value disagreement trips `source-disagreement` and states both. A
`companyfacts` value can also simply be **garbage** (a filer's own XBRL error) — hence an implausibility
floor that **flags rather than drops** (#9: never silently discard).

**Three empty states, kept distinct** (interaction #2 — "we couldn't read it" must never masquerade as
"there is nothing"): `no-annual-filing` (nothing on EDGAR — the only case where "nothing to extract" is
true) · `cover-not-located` (a filing exists but its cover is unread — still a live candidate) · covered
(shares emitted; **purity remains uncovered for these names** — cash/runway is now covered by the
annual-statements path below — and the UI says so rather than letting a `—` imply zero).

**The ADS ratio — apply where READ, SUPPRESS where not.** The annual cover states **ordinary** shares,
but the price feed carries the **ADR/ADS** price — they only multiply at 1:1, and real ratios run 1:1 to
three digits ("each ADS represents N ordinary/common shares"). A raw `shares × price` therefore overstates
the cap N-fold — and the mid-size errors (2×, 5×) are exactly the ones the operator's market-cap intuition
does **not** catch, on the one figure the extractor design leans on as the human check. The rules, each
pinned by a test:

- **Detection reads the filing already fetched** (no extra network): the ratio phrase in prose, the
  **securities-registered table row** (where the ADS noun sits in a *different table cell* — the form a
  prose-only parser misses), and word-numbers including **compounds** ("four hundred", "one hundred and
  twenty" — a single-token map reads "four hundred" as 4). A ratio-**change** narration ("changed *from*
  one ADS representing X … *to* …") contributes only its target — history is not a conflict.
- **`known`** (exactly one defensible ratio) → the market cap divides the ordinary count by it. **The fact
  keeps the true ordinary count** — the ratio modulates the *derivation*, never the value, or the fact
  divorces from its located passage. The division's evidence rides as its own located passage (#6).
- **`unread`** (ADR evidence but no defensible ratio: missing, **fractional** — "one-half of one share" is
  real — or **conflicting** statements in one document) → the cap is **WITHHELD** in the awaiting-price
  shape (value None, shares provenance visible, a note naming the missing half), never guessed at 1:1.
  Detection can never be proven complete, and a defaulted 1:1 is a *multiplicative, silent, permanent*
  error — the same fail-closed principle as the cover cue, one layer down. Better detection later moves a
  name **suppressed → correct**, never *wrong → right*.
- **not applicable** (no ADR evidence) → 1:1, the assumption recorded in the fact's note. **`NULL/NULL`
  MUST read as not-applicable** — every legacy row and every domestic 10-Q name has NULL, so a
  NULL-means-suppress encoding would blank every market cap in the app (the storage trap; a domestic-name
  byte-identity regression test guards it).
- **F-6 is a positive ADR signal only** — a real 5:1 name with no recent F-6 was measured. Never infer
  "no F-6 ⇒ not an ADR".
- Known limitation: a ratio **change** is picked up at the next annual filing (≤ ~a year of lag; the
  in-between filing that states both old and new reads as a conflict → suppressed, honestly). A ratio is a
  multiplicative input — it gets its own staleness thinking in a later pass.

## The annual-statements runway path — cash + burn for a filer with NO 10-K/10-Q `[BUILT]`

The sibling of the annual-cover shares path (Retrieval Slice A): a dark name's **runway** — cash and a
span-normalized operating-cash burn — read from the filer's annual financial statements
(`annual_facts_for_security` in `backend/ingest/edgar/annual_runway.py`). When the statements live in
the main 20-F/40-F document, the SAME fetch feeds cover-shares and runway (**one fetch feeds both**);
when they live in an `EX-99` exhibit (the 40-F/MJDS wrapper shape), the **statement-source seam**
below resolves the exhibit. Ground truth is pinned in `backend/tests/ingest/test_annual_runway.py` +
`test_statement_sources.py` and their fixtures (real trimmed statement text, every trim verified to
reproduce the full-document result) — the tests are the oracle, not this page.

**FLAG only, structurally** — the same bound as the shares path: the pre-fill tier's token appears
nowhere in the module (source-scan test), and every emitted fact carries **both** located statement
rows (the balance-sheet cash row + the cash-flow operating total) as passages. No passage → no fact.

**The sign IS the state.** Positive operating cash flow → **cash-generative**: no runway is computed
(a finite number would be bogus); the fact goes out with a negative quarterly burn and a
cash-generative note, and the meter reads it as the existing burn ≤ 0 top-pip. Negative → a finite
runway, span-normalized.

**Span-normalize is the core computation.** `burn_per_day = |OCF| / span_days` — a 3/6/9-month column
(a 6-K interim served by companyfacts, or a transition-period statement) read as a year puts runway
off 2–4×. The span comes from, in order: an exact companyfacts row match **ending at the period of
report** (filed dates — reproducible arithmetic, immune to a mislabeled period phrase; the end-date
pin defeats coincidentally-equal old rows); the statement's own period phrase; a bare fiscal-year
column on an annual form (annual by form semantics, stated in the note).

**Unit scale is read, never assumed.** Statement values sit under a `$'000` / `$000s` / `in thousands`
/ `in € thousand` / `k€` / `in millions` header; the nearest marker above the row (its own table
header) is applied, and one filing's statements share one declaration — a side missing its marker
inherits the companion statement's, noted. No marker anywhere → the figures go out **as printed** with
flag `unit-scale-unread` — stated, never guessed.

**The current column is picked, never assumed.** Statements print 2–3 fiscal years side by side —
ascending or descending, sometimes with a convenience second-currency column — and the report year is
matched against the column-year headers located above the row. An ambiguous mapping (a duplicated
report-year header) withholds the value with flag `column-ambiguous`; when every column shares one
sign, the generative/burning **state** still reads (a state is sign-only).

**Currency is established, never guessed** (the extractor correctness plan's PR 4, 2026-11-15). Foreign
filers report in their own currency (CNY, JPY, TWD, EUR, CAD, SGD, ...), often with a translated US$
convenience column beside it. Runway is a ratio — months are currency-independent **if** cash and burn
share a currency — so the currency is settled in this order: (1) **companyfacts reproduces the
figures**: a same-date row in us-gaap **or** ifrs-full, in **any** unit, equal to the statement's
figure at its printed precision; that unit **is** the currency, and a header naming another one is
flagged `currency-mismatch` (both stated). (2) **The header's explicit markers** above the row: US$,
NT$, HK$, S$, C$/CDN$/CAD, A$, R$, RMB/Renminbi/yuan, ¥/yen, ₩, €, ₹, CHF, NIS/₪, £, ZAR (one marker →
it; a home currency beside US$ on a row with more value columns than fiscal years → the home currency,
and when companyfacts did not confirm it, `convenience-column`). **A bare `$` (or "dollars") names no
currency** — a Canadian filer prints `$` too. (3) Otherwise **`currency-unread`**: the fact carries no
currency (never a blank-as-USD), and the front end renders `?`, never `$`. When the two statements'
headers name different currencies and companyfacts does not settle it, that is `currency-unread` too.
A fresher companyfacts reading is used only in the established currency (a mixed-currency ratio is
unrepresentable). The stored `*_usd` fields carry statement-currency values; the note names the
currency and how it was determined. (A cross-currency absolute display, and storing the currency on
approval, are later work: PR 6.)

**The consolidated statement, not a breakdown** (PR 4). A 20-F often prints a parent-only, condensed
consolidating (VIE / subsidiaries / eliminations) or segment balance sheet BEFORE the consolidated
statements (EH, ZEPP, TM in the 2026-11-15 audit). Every cash-row site is collected and picked in this
order: the one whose current-column figure companyfacts reproduces on the statement date; else the first
whose heading window (a little before the heading and its first ~400 characters) carries no breakdown
cue ("parent", "condensed", "consolidating", "Schedule I", "VIE", "subsidiaries" — not "XYZ Ltd. and
its subsidiaries" —, "eliminations", "segment", "financial services businesses"); else the first site,
flagged `table-unverified`. The operating cash flow takes the same order and comes from the **same
statement set**: the first qualifying cash-flow total after the chosen balance sheet, else the last. A
**notes line is never the total**: an operating-activities label whose own row carries "included in",
"paid for" or "operating leases" (QGEN's "Cash paid for operating leases included in cash flows from
operating activities $ 31,741") is rejected. A cash row that is located but unreadable is flagged
`cash-unread` (it used to add nothing, silently). The note names each table and what was passed over.

**Cash is cash plus short-term deposits and investments** (operator decision D1; PR 4). A "short-term
(bank) deposits", "short-term investments" or "time deposits" row on the SAME balance sheet as the cash
row (near it, no "Total current assets" between them, not restricted or long-term, not a combined
"cash ... and short-term ..." line) is added; each part is named in the note, rides as its own passage,
is carried on `investments_usd`, and is cross-checked against companyfacts' short-term deposit and
investment concepts. Marketable securities and the generic long-term investments line are not read here
(D2). When companyfacts carries same-date short-term investments that no row was located for, the note
says so and they are not counted.

**Later-as-of-wins per quantity** (the shares rule): companyfacts usually **lags** the filing by up to
a year (annual XBRL ingests slowly — the reason the filing is read at all) and is sometimes
**fresher** (a 6-K interim landing after the last annual — the interim then wins and is normalized by
its own exact span). **The cross-check** (PR 4) reads us-gaap and ifrs-full in every unit on the
statement's own date and requires an **exact** match at the printed precision (a 1% tolerance passed
EH's wrong column, 0.4% off): an agreement is named with its concept and unit; a same-date row that
reproduces nothing is `figures-disagree` with both numbers (this replaced `source-disagreement` on this
path); **no same-date row is a stated condition ("not cross-checked"), never a pass**. A cross-date lag
is stated in the note, not flagged — lag is the *rule* for annual filers and a flag true of every name
carries no information. Every annual cash and burn candidate stays a FLAG whatever the cross-check says.

**The statement-source seam — WHERE the statements come from is an ordered chain, not a nested if**
(Retrieval Slice A-2; `backend/ingest/edgar/statement_sources.py`). The runway extractor consumes
statement TEXT resolved by a **light ordered chain of source functions** — a list + a first-hit-wins
loop, deliberately NOT a registry or class framework (3–5 sources are ever expected; the
revenue-segment passages are the intended next user). Order = priority: **`main_doc_statements`**
(the statements in the already-fetched primary document — it re-reads the SAME text the cover-shares
leg fetched and **never re-fetches**; the fetch-once discipline is provable by handing the source no
client at all) then **`exhibit_statements`** (below). The seam changes *where* the text comes from,
never *what* is done to it: the resolved text flows into the unchanged Slice A extraction, and every
located passage cites the **winning document** — an exhibit-resolved runway passage cites the
exhibit, never the wrapper. **Cover shares stay on the primary document** (the 40-F cover carries
the count) — the seam governs statements only.

**The exhibit source — statements identified by CONTENT, strictly.** The 40-F/MJDS wrapper shape
puts the financial statements in an `EX-99.x` exhibit. `exhibit_statements` enumerates the filing's
documents (the accession `index.json`, cached under a **mutable** prefix — the key-classed 12h-TTL
freshness rule), identifies the EX-99 `.htm` candidates by their **SGML-header TYPE** (the
accession's `-index-headers.html`; exhibit *filenames* are NOT trustworthy — real filer agents name
exhibits with no "99" anywhere, so a filename pattern survives only as a recall fallback when the
header yields nothing), and fetches them **bounded**: candidates order unknown-size-first then
largest-first (a statements exhibit is a big document; the EX-99 tail is tiny certifications) and
cap at `exhibit_scan_max`, every capped-out candidate **logged, never silently dropped**. Each
document fetch is cache-first under the immutable `forms/*` prefix — paid once per accession, ever;
the XBRL/graphic/R-page army (90–110 documents per 40-F) is never touched. The **winner is the one
exhibit whose cleaned text carries BOTH statement rows** — the balance-sheet
cash-and-cash-equivalents row AND the operating-activities total, located by the extractor's own
row locators (signature and extraction can never disagree). A keyword *mention* fails it: the AIF
carries the statement headings with no rows, and the MD&A quotes the very numbers in prose — a
liquidity summary table can even hit the *cash* locator alone, which is why the signature demands
**both** rows. **Fail closed:** zero matches → the honest deferral below; **more than one** match →
`exhibit-ambiguous`, never a guess — a wrong statement source is the runway analog of a
confident-wrong cover match.

**Four runway empty states, kept distinct** (`ExtractionResult.runway_empty_reason` — separate from
`empty_reason` because the shares leg usually still emits): **`cash-generative`** (a state, not a gap
— no runway applies; shown even when the statements aren't in-doc, from the companyfacts sign) ·
**`financials-in-exhibit`** (a *burning* name whose statements were found in **neither** the main
document **nor** any exhibit the bounded scan read — deferred, never a companyfacts-only number) ·
**`exhibit-ambiguous`** (more than one exhibit carries the statement signature — deferred, never
guessed) · **`statements-not-located`** (sign unknowable — unread, not empty).

**Staleness is annual-calibrated** (honest loudness): an annual reading is inherently up to ~a year
old, so `stale-runway` fires only past `annual_stale_runway_days` (~1.5 annual cycles) — a delinquent
or stopped filer, never the ordinary annual rhythm.

## The located passage — deterministic retrieval, never a reading

A `LocatedPassage` is **deterministic keyword/section retrieval** over the cleaned filing text (`_locate`:
the first matching anchor + a fixed character window) — never a model's interpretation. It is the evidence put
in front of the operator (`kind` ∈ cash-flow / cover / balance-sheet / segment / business-description). The
ratify UI shows it inline (not a tooltip) with a clickable EDGAR link.

## Ratify — the operator's value is the only one stored

`POST /workbench/facts` (the facts panel) writes the operator's confirmed/edited value via the existing
`ingest_*` bridges (`fact_revenue_mix` / `fact_shares_outstanding` / `fact_cash_burn` /
`fact_catalyst`):

- The stored value is **strictly the operator's submitted field** — never an extractor figure that slipped
  through. (AUTO is confirm-as-is, but it still travels as the operator's confirmed value.)
- `source` is the candidate's **basis**, preserved (`10-k-segment` vs `10-k-business-description`, `10-q`,
  `10-q-cover`) — never flattened to "ratified", so the provenance chip stays honest.
- `ratified_by` is stamped `"operator"` server-side. The fact is append-only (a re-ratify is a new row,
  latest-wins). The meter re-derives on the next scored read — closing the extract → ratify → score loop.
- **The `catalyst` variant is hand-authored, not extractor-fed** (the authoring slice): no candidate
  exists, so the **citation is the provenance** — `source_ref` (the press release / 8-K / IR page) is
  REQUIRED, an empty one 422s (#6: an uncited catalyst is a bare operator claim, not evidence). It writes
  the Key-1 **conviction fact** (`ingest_catalyst`, `source='ratified'`) the catalyst-conviction detector
  consumes — distinct from the thesis-level catalyst *surface* (display events, `PUT
  /theses/{id}/catalysts`, whose sole writer is `thesis_repo.set_catalysts` — the `set_term_set`
  structural wipe-guard, so a promote can never blank an authored list; same for kill criteria).
- **The ratify must be VISIBLE even when its meter can't move yet** (the gate-3 "no save?" finding: a
  shares confirm on a price-less fresh name changed nothing on screen — the fact was on file all along).
  Three surfaces carry it now: `_market_cap` keeps a half-input figure **value-None but provenance'd**
  (the ratified fact + a note naming the missing half — "shares on file · needs price" /
  "price on file · needs shares"); the funnel + fundamentals badge count a ratified shares fact as
  confirmed data **before** a price exists; and the FactsPanel tags a candidate whose fact type already
  has a ratified value **"✓ on file"** (the extract endpoint is deliberately DB-free, so its candidates
  can't know — the tag is derived from the meters' provenance). A re-confirm is an append, honestly
  labeled — never "the first save never happened."
- **Re-entry shows the RATIFIED VALUES, not the stale candidate** (the tag alone wasn't enough: a
  re-opened name still *seeded its inputs* from the original candidate, so a saved purity visibly
  "reverted" to the original LLM rec even though the meter was right). The scored read threads each
  fact's own figures into its provenance **`detail`** (`mix_pct` + `segment_label` / `shares` /
  `cash_usd` + `quarterly_burn_usd` + the ratified note — display-only, #2, an open dict so **no wire
  schema change**); the panel seeds its inputs from those, suppresses the stale estimate tag, and
  remounts into the on-file state when a confirm lands. The candidate is only the seed for a fact
  **not yet on file**.
- **AUTO shows its work, on demand.** Every AUTO candidate carries its located passage too (the shares
  cover; the clean quarter's balance-sheet + cash-flow) — rendered **collapsed** behind a one-click
  "show the source" (AUTO doesn't demand reading; FLAG/HUMAN stay inline because reading the passage IS
  that decision). Approving a pre-filled value reads as the operator's decision, never a black box (#6).
- **An empty purity names WHY it's empty** (missing ≠ blank): pre-revenue ("no revenue data on file —
  author from the business description") vs a thesis-scoped extract whose grounded estimate declined
  ("couldn't ground a purity estimate in the passage") — different authoring starts, labeled honestly.

---

## The first LLM seam — the flag-explanation drafter `[BUILT #59]`

The one LLM call in an otherwise-deterministic system, introduced on the smallest, lowest-stakes surface
first. For a **FLAG** candidate a model drafts a ≤2-sentence plain-English explanation of what the flagged
figure is composed of, **grounded in the located passage**, shown ALONGSIDE the raw text behind an explicit
"Explain" button. It is an **aid to the ratify, never the ratify** — the SMR burn passage is a wall of
cash-flow text; the explanation says, in plain English, that it hides a one-time ~$264M ENTRA1 component.

**`backend/llm/` — the model-agnostic interface** (CLAUDE.md: "LLM calls go through the `backend/llm`
interface only"). This seam establishes it: `LLMClient` (Anthropic tool-use for structured output; an
`allow_live` offline gate mirroring `EdgarClient`; the SDK imported lazily, so the suite needs no key or
network) + `flag_explanation` (the prompt + the structured-output schema + `explain_flag`). The dials live in
`CallConfig` (`llm_model = claude-haiku-4-5-…`, `llm_max_tokens = 256`, `llm_timeout_s = 10`). S5 (the
narrative→chain drafter) extends this same interface — now BUILT (`backend/llm/chain_decomposition.py`); see
`CHAIN_DRAFTER.md`.

### THE BOUND — structural, not merely prompted (INVARIANT #3)

The explanation **cannot become a fact**, enforced by *structure*, not by trusting the prompt:

- `POST /workbench/facts/explain` takes **no DB connection, writes nothing, and is never a field on
  `RatifyFactRequest`**. The ratified number can only ever come from the operator's typed field on
  `/workbench/facts`. The explanation rides a **separate rail that dead-ends at the screen**.
- So even if the model emitted a number, there is no wire path to store it. **The missing rail is the
  guarantee; the prompt is the courtesy.** Guarded by two tests: no explanation/draft field on any ratify
  variant + **zero `fact_*` writes** when the explain endpoint is called.

### The rules

- **Components + direction only (decision #1).** The drafter may name the component figures that appear in the
  passage (the total, the one-time item) and the DIRECTION an adjustment implies ("the recurring figure is
  lower") — it **must not state the final adjusted value**. The operator does the arithmetic and types the
  number; the active judgment stays with the operator.
- **FLAG only.** AUTO is a clean confirm-and-go (nothing to untangle); HUMAN/purity is the operator's edge,
  deliberately not model-explained.
- **Fail-open by contract.** Any LLM trouble — no `ANTHROPIC_API_KEY`, timeout, SDK error, or the model
  declining to ground it — returns `200 {grounded: false}`, **never a 5xx**. With no key the facts panel works
  exactly as today (no explanation). The explanation is a layer, never a dependency.
- **Grounded or silent.** The drafter grounds in the located passage the operator can see; if it can't, it
  returns `grounded: false` and the UI says so ("read the passage") — never a fabricated explanation.
- **Marked model-drafted.** Shown with a quiet `drafted` badge (the `system_drafted` authorship vocabulary);
  nothing it says becomes a fact without the operator's ratify.

### The watch-item — decision #1 rests on the prompt

The bound (the explanation can't become a fact) is structural. **Decision #1 (no final value in the prose) is
the one part that rests on the PROMPT, not the rail** — the rail stops the explanation becoming a fact; it
does not stop the model *stating* a computed value the operator might rubber-stamp. So:

- The **gate-2 manual "no-final-figure" check is its only real test** — a fake-client unit test can't exercise
  a prompt's effect.
- If the model starts stating values, the **Sonnet-4.6 bump** (`llm_model`, a one-line `CallConfig` change) is
  the adherence lever — logged as a trigger, not a default.
- A deterministic post-filter (reject any number in the prose absent from the passage) *would* make it
  structural, but it's fuzzy on rounding ("$264M" vs "264,195") — **not built in v1**; noted as the path if
  prompt-adherence proves unreliable in use.

---

## The third LLM seam — the grounded purity estimate `[BUILT #110]`

On the same `backend/llm` plumbing, the SURFACE shift added a **grounded purity-estimate** seam — where the
flag-explanation drafter *explains* a FLAG, this one *proposes a value* for the HUMAN purity tier, so a discovered
name arrives with an on-thesis % to confirm instead of a blank. `backend/llm/purity_estimate.py:propose_purity`
(mirrors `flag_explanation`): given the thesis narrative + the fetched segment-footnote passage, it returns
`{segment, pct, reason, grounded}`.

- **Grounded ONLY in the located passage.** The operator's hard requirement: "the LLM read this footnote and
  proposes 20%" is fine; "the LLM knows the company is ~20% nuclear" is a **#1 violation**. So a non-grounded
  proposal (or a % out of `[0,100]`, or an empty segment) is **discarded, never surfaced as a number** — the
  estimate ALWAYS carries the passage it read, enforced by construction. To ground on real figures the purity path
  retrieves a **widened, financial-figure-ranked** segment window (`_segment_passages` in
  `ingest/edgar/extract.py`), not the ±110-char excerpt the other tiers use.
- **Thesis-scoped, optional.** Only the purity branch is thesis-aware (the on-thesis segment depends on the
  narrative); `thesis_id` is optional on extract — with no thesis context purity degrades to today's HUMAN.
- **Fail-open + response-only.** No key / not-grounded / parse fail → `None` → HUMAN (the operator types it). It is
  an estimate in the computed-on-read response, **never a fact row** (the architectural constraint above).
- **Confirm/override IS the decision (#10).** The operator confirms as-is (`vouched=confirmed`) or changes it
  (`vouched=overridden`); the shown estimate rides the ratify body so the server stamps `vouched` by comparison.
- **Gate-2 is a LIVE run.** A fake-client test proves the tag/rail plumbs; only a live extract on a real filing
  proves the proposal is *grounded* (reads the footnote, doesn't recall) — that is the seam's real test.

## The market-cap best-effort estimate `[BUILT #110]`

`_market_cap` (the confirmed figure = shares × price) blanks on messy / dual-class shares. The SURFACE layer
surfaces a **best-effort market-cap estimate** (from the FLAG-tier best-effort shares × latest price), tagged
unverified in the estimate layer — the confirmed meter keeps blanking, the estimate is a parallel display. Same
computed-on-read discipline: never a fact row. *(On a fresh thesis with no back-half price ingest, the estimate is
naturally absent — market cap is derived, not a directly-stored fact.)*
