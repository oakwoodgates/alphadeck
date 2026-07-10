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
  *current* cover-share count.
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
- **YTD derivation — a clean derivation is AUTO; only the raw-YTD case flags.** companyfacts' cash-flow
  column is often year-to-date. When the latest span exceeds `quarterly_span_max_days` (100) and a same-start
  prior exists, the extractor DERIVES the quarter (YTD − prior YTD) — **reproducible arithmetic on two filed
  columns (the market-cap trust class), stated in the note (`derived (YTD − prior YTD)`), never a flag.**
  When there is **no prior to subtract**, the value goes out as the **raw YTD → flag `ytd-raw`** with a
  passage saying it is NOT a quarter (the runway audit: the old single label claimed a derivation that never
  happened — runway would have read ~3–4× too short, believed). The one-time detector runs on the derived
  quarter too — a clean basis never bypasses the anomaly check.
- **Cash includes marketable securities — same balance sheet only; the composition is a note, not an alarm.**
  `cash_usd = cash + equivalents + marketable securities` (the uniform cash rule, `WORKBENCH_SCORING.md`) —
  but **a balance sheet is ONE date**: a marketable instant dated differently from cash is a *different*
  balance sheet (usually a discontinued tag — MU's `AvailableForSaleSecurities*` last reported **2018**, and
  the old composer silently added those balances into current cash) and is **EXCLUDED from the sum**, named
  in the note ("verify where current investments live"). Included-or-excluded, the basis rides the note's
  as-ofs; **no flag** — same-dated inclusion is the textbook liquidity composition, and the off-date
  exclusion errs CONSERVATIVE (understated cash → runway reads *shorter*; a funding-risk gauge that
  over-warns, never under-warns). The alarm, when it matters, is the runway meter reading short.
- **Missing inputs are their own labels with None values — never a fake $0.** No cash instant *and* no
  OCF column → `no-companyfacts` (located-only; the old `or 0.0` coercions sent such filers out AUTO / $0 /
  $0 / "Clean quarter" — a confirmable fake zero). Cash without an OCF column → `no-cashflow-column` (burn
  stays None; a $0 burn used to ratify into a fake "cash-generative"). An OCF column without a cash
  instant → `no-cash-instant`. The ratify UI gates Confirm on blank fields (`Number("")` is 0) and renders
  the missing-data labels **grey (∅), not warm (⚠)** — a data gap is an authoring state, not an alarm; the
  warm glyph is reserved for the judgment exceptions (one-time / stale / raw-YTD / dual-class).
- **Every input's as-of date rides the note** ("cash as of … · burn over … → …"), and a cash balance sheet
  older than the filing's period end flags `stale-cash` (the shares `stale-cover` rule, applied here).
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

When no flag trips, `cash_burn` / `shares_outstanding` are AUTO; `revenue_mix` is always HUMAN.

## The located passage — deterministic retrieval, never a reading

A `LocatedPassage` is **deterministic keyword/section retrieval** over the cleaned filing text (`_locate`:
the first matching anchor + a fixed character window) — never a model's interpretation. It is the evidence put
in front of the operator (`kind` ∈ cash-flow / cover / balance-sheet / segment / business-description). The
ratify UI shows it inline (not a tooltip) with a clickable EDGAR link.

## Ratify — the operator's value is the only one stored

`POST /workbench/facts` (the facts panel) writes the operator's confirmed/edited value via the existing
`ingest_*` bridges (`fact_revenue_mix` / `fact_shares_outstanding` / `fact_cash_burn`):

- The stored value is **strictly the operator's submitted field** — never an extractor figure that slipped
  through. (AUTO is confirm-as-is, but it still travels as the operator's confirmed value.)
- `source` is the candidate's **basis**, preserved (`10-k-segment` vs `10-k-business-description`, `10-q`,
  `10-q-cover`) — never flattened to "ratified", so the provenance chip stays honest.
- `ratified_by` is stamped `"operator"` server-side. The fact is append-only (a re-ratify is a new row,
  latest-wins). The meter re-derives on the next scored read — closing the extract → ratify → score loop.
- **The ratify must be VISIBLE even when its meter can't move yet** (the gate-3 "no save?" finding: a
  shares confirm on a price-less fresh name changed nothing on screen — the fact was on file all along).
  Three surfaces carry it now: `_market_cap` keeps a half-input figure **value-None but provenance'd**
  (the ratified fact + a note naming the missing half — "shares on file · needs price" /
  "price on file · needs shares"); the funnel + fundamentals badge count a ratified shares fact as
  confirmed data **before** a price exists; and the FactsPanel tags a candidate whose fact type already
  has a ratified value **"✓ on file"** (the extract endpoint is deliberately DB-free, so its candidates
  can't know — the tag is derived from the meters' provenance). A re-confirm is an append, honestly
  labeled — never "the first save never happened."

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
