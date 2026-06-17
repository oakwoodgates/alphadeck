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
- **HUMAN — interpretation-bound (purity) → LOCATED only, NEVER auto-valued.** The extractor locates the
  evidence (the segment footnote or Item-1) but proposes no number; the operator authors the % from it.
  Purity is the operator's exposure-concentration edge — the model never nudges it.

## The detectors — what trips a FLAG (category, not size)

The detectors read `companyfacts`; every threshold is a dial in `ExtractorConfig` (`backend/domain/config.py`,
no magic numbers). The judgment calls the operator most needs to make:

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
- **YTD derivation.** companyfacts' cash-flow column is often year-to-date, not a quarter. When the latest
  span exceeds `quarterly_span_max_days` (100), the extractor DERIVES the quarter (YTD − the prior YTD of the
  same fiscal year) and flags it so the operator confirms the period basis. → flag `ytd-derived`.
- **Cash includes marketable securities — flagged for verification.** `cash_usd = cash + equivalents + ALL
  marketable securities (current AND noncurrent)` — the uniform cash rule (`WORKBENCH_SCORING.md`). The
  marketable-securities tags are filer-specific, so when any are present the extractor includes them, flags
  it, and locates the balance-sheet line — the operator confirms the composition rather than trusting a tag
  guess. → flag `verify-marketable-securities`.
- **Dual-class shares.** A single-class, current cover concept → AUTO. Otherwise (dual-class / absent / stale)
  → FLAG: a best-effort cover-page regex sums the per-class counts (total economic = A+B; the A/B split is
  voting, not economics) and locates the cover. If the cover doesn't yield ≥ 2 classes it returns **no value**
  (not a guess), so a FLAG never anchors the operator to a wrong number. → flag `dual-class`.

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
