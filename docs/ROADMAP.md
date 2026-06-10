# ROADMAP.md — the live plan

> Repo path: `docs/ROADMAP.md`. **This is the live plan.** It **supersedes the earlier M0–M5 build plan**
> (which predates the catalyst subsystem and the through-line refactor, describes the insider-only model, and
> whose "M5" is not the M5 we now mean — treat it as a historical artifact, not the live plan).
>
> Siblings: `CALL_LOGIC.md` (the brain), `CATALYST_CONVICTION.md` (the catalyst key), `INVARIANTS.md` (the
> load-bearing rules), `RECALIBRATION.md` (the post-MVP tuning agenda — the dials live there, not here),
> `PROJECT_OVERVIEW.md` (design rationale). This doc is **sequencing**; it references the others, never
> duplicates them.

---

## Where we are

The **back half is built and verified end-to-end**: the bitemporal store, two-key arming, the pure
call-assembler, the catalyst subsystem (operator-ratified bridge + the automated DOE/USASpending feed), and
the through-line refactor. **3 theses / 4 armed names render on real data** (HIMS starter, UNH core, nuclear →
LEU core_entry headline with OKLO starter beneath). Docs are reconciled except the old build plan (now
historical).

The **front half — the Workbench — is unbuilt.** It is the only one of the four surfaces (Board, Cockpit,
**Workbench**, Scoreboard) with **no mockup yet**, which is why it slipped; its design pass is now done (Phase
2 below) and its mockup + spec land operator-side in `docs/mockups/`.

## Organizing principle — two halves on one spine

The product is **two halves on one spine** (`PROJECT_OVERVIEW.md` §4):
- **Front half (hunt + research):** narrative → decompose the value chain → surface & score names by exposure
  purity / runway / catalyst density → auto-draft DD. *(The Workbench.)*
- **Back half (park + time):** chosen names become a parked thesis → incubate → trigger detection → graded
  call → manage to exit. *(Built.)*
- **The spine:** the thesis as a first-class object connects the two.

We built the **back half**. The roadmap **finishes the back half → makes it trustworthy → builds the front
half (the Workbench) → enhances.**

> **Sequencing rule (locked): trust before front-half build.** There is no point auto-generating names for an
> engine you don't yet trust to *time* them. The replay harness + recalibration (Phase 1) come **before** the
> Workbench build (Phase 2).

---

## NOW — cleanup + design (no build-pipeline contention)

- **Confirmation-box honesty fix.** The Armed card's Confirmation key currently reads "a volume-backed
  breakout" even on a momentum-only/flip card (HIMS) — the loudest element, overstating a starter, and it
  *contradicts* the card's own caveat. Make it grade-aware: volume-backed → say so; momentum-only →
  "momentum-only, not yet volume-confirmed" (reusing the caveat wording so they agree); amber, not green, for
  the weak case. *(Requires exposing `confirmation_grade` — it is not derivable from the existing fields.)*
- **Retire the old build plan.** This doc supersedes it; the historical copy gets a courtesy header pointing
  here.
- **Workbench design pass — operator-led, COMPLETE this round.** The locked decisions are in **Phase 2**
  below; the mockup + spec are being finalized operator-side, in the existing Board/Cockpit language, to land
  in `docs/mockups/`. **Not built now** — the build is Phase 2, after trust.

---

## M5 — finish the back-half loop

### Part A — the per-member ranked menu  *(build now; independently shippable)*
A theme currently collapses to a single headline. Instead, show **every armed member ranked**, each with its
**own call** (verdict / grade / confidence / clocks / name-specific evidence). Fold in the **runway + freshness
ranking** (`RECALIBRATION.md` B.2): rank on grade **and** runway, so a core arm about to lapse (LEU → 06-30)
doesn't headline over a starter with years of runway (OKLO → 2029). Surface **confirmation-only members** too
("moving, no conviction yet — watch"). The **Decision Queue shows a theme by its top-ranked *actionable*
name**, not every member.

### Part B — theme/group arming  *(design pass, THEN build)*
New arming logic, so a short design pass first (like the catalyst pass) covering:
- the theme conviction is **operator-ratified + graded + horizon'd** (the same shape as every conviction);
- it arms on **volume-backed** confirmation, **not** momentum-only;
- per-member **co-location still holds**;
- a **cap (or the ranking) gates** how many theme-armed names surface, so the queue doesn't flood.

---

## Phase 1 — Trust + production  *(strictly sequential)*

1. **Replay / backtest harness** — DuckDB + Parquet; the `known_at` (transaction-time) axis already makes it
   honest. Validates the edge over history **and** produces the scored outcomes recalibration needs.
2. **Recalibration pass** — tune the dials + filed refinements in `RECALIBRATION.md` against replay outcomes,
   **never to fit one name**.
3. **Cut production as a fresh tenant** — `tenant_id` per row means production is a *new tenant*, never a
   destructive wipe; dev/demo data is kept. The tenant is threaded from the thesis (auth deferred) and
   isolation is **proven** by a poison-row test, not just asserted — design, threading map, and known
   limitations (the Board is not yet tenant-scoped; no RLS) live in `PRODUCTION_TENANT.md`.

---

## Phase 2 — Build the front half (the Workbench)  *(after trust)*

The deepest cut at name-selection: it **generates the names from a narrative** instead of the operator
hand-seeding them. The design below is the output of the operator-led design pass — ready and waiting; the
mockup + spec land operator-side in `docs/mockups/`.

### What it is
Turn a **narrative** (the operator's edge, untouched) into a **scored, structured basket of names** (the
flaw), then hand it to the Board as a thesis. **Deferential about the narrative, opinionated about the chain
and the names.** The **hero is the value-chain decomposition** — it makes the whole chain visible (**off-thesis
/ impure names surfaced and _flagged_, not hidden**) so the operator picks from a *map*, not from the two names
that came to mind first. **Runs both directions:** Workbench → Board to promote; Board → Workbench to
re-research a theme.

**Visual companion (committed):** `docs/mockups/mockup_workbench_nuclear.html` — the front half in the existing
Board/Cockpit aesthetic, interactive (click a chain link to switch segments, a name to load its DD): narrative
up top (preserved + attributed), the chain links as the hero, scored names per link (the meters — purity /
runway / catalysts / **dilution** — plus market cap as a *figure* and the archetype pill), a DD rail, the
basket, and a promote-to-Board action. *(The mockup shows **three** meters; the build adds **dilution** as a
fourth, its-own-meter — a deliberate divergence, pressure-polarity; see `docs/WORKBENCH_SCORING.md`.)* **Keep
this spec and that mockup in sync.** Its scores / market caps / company facts are **illustrative placeholders
— they never enter the codebase** (all data-derived in the build).

### Build status — the MVP slices
The Workbench MVP ships in slices. **The data-and-engine half + the display half are done; AUTHORING and the
drafter remain — and the "working Workbench MVP" is NOT done until authoring lands.** The MVP's whole pitch is
narrative → *decompose into a value chain* → scored basket → promote; until authoring exists we have a
Workbench that **displays, scores, and promotes a pre-built (seeded) basket — not one that turns a narrative
into a basket.** So **Slice 4b is the second half of the MVP, not deferred polish.**
- **Slice 1 — the persistence seam `[MERGED #47]`** — the value-chain **structure** persists operationally on
  the thesis spine (`segment` + `authored_by` on `basket_member`, the `segments` list on `thesis`; migration
  0008). Survives-reload proven on a fresh connection.
- **Slice 2 — the three ratify bridges `[MERGED #48]`** — `fact_revenue_mix` (purity),
  `fact_shares_outstanding` (market cap), `fact_cash_burn` (runway): provenance-carrying bitemporal facts
  mirroring the catalyst bridge. *(+ the nuclear seed on real operator-ratified figures, #49.)*
- **Slice 3 — the scoring engine `[MERGED #50]`** — the **four** data-derived pip meters + the market-cap
  figure, re-derived on read (Option B); the Workbench API (`GET …/scored`, `POST /workbench/theses`) + the
  current-tenant resolver. See `docs/WORKBENCH_SCORING.md`.
- **Slice 4 — the Workbench UI · DISPLAY · SCORE · PROMOTE `[MERGED #52]`** — the React screen against
  the mockup, wired to the live scored endpoint: the four-meter rows + the market-cap figure + the fit label,
  the **DD rail** ("behind the scores" provenance with the burn-composition / cash-basis notes + clickable
  EDGAR), the quiet **promote round-trip** (→ an Incubating Board thesis). The **dilution meter is the ember
  RISK axis** (pressure polarity, set apart — never a goodness meter); a no-data figure renders **"—"**
  (structurally distinct from a measured zero). Because the seed is a **flat basket** (no value-chain
  decomposition — that is drafted in S5 + authored in 4b), the chain **hero is honestly empty** and the names
  render as one flat scored list; the gap is made honest in-product (a disabled "add/edit names" affordance + an
  "authoring is 4b" marker) so the polish never masquerades as a finished product. **Frontend only — the
  Slice-3 wire is untouched.**
- **Slice 4b — AUTHORING · the second half of the MVP, NOT optional** — build & edit the value chain:
  add / place / move names (authorship tracked), **decompose the flat basket into links**, re-score on edit.
  Two sub-slices — the wire add gated first (the S1→S4 rhythm):
  - **4b-1 — the resolver + the `authored_by` write rule `[BUILT — in review]`** — `GET /workbench/securities`,
    a **read-only discovery net** over the per-tenant master (exact-membership, INVARIANT #2 — never an ingest,
    never a guess; the operator picks the exact `security_id`); the promote write **stamps**
    `authored_by = operator_set` server-side (coercing the body — `system_drafted` is reserved for S5's own
    write path, `operator_edited` defers to S5's draft-diff). The wire grows; openapi + types regenerated.
  - **4b-2 — the authoring UI + the first frontend test harness `[NEXT]`** — segment CRUD, place/move, the
    add-a-name typeahead, save via the existing full-replace `POST /workbench/theses`, re-score; plus a
    vitest/RTL harness landing the grouped-render coverage the flat seed left unexercised in S4.
  - **⚠️ Authoring is the MACHINERY, not a populated universe.** The resolver searches the CURRENT tenant's
    master — **which is still just the seed**. So after 4b you can decompose / re-arrange the seeded basket and
    add a name *already ingested*, but **"turn a fresh narrative into a basket" stays gated on INGESTION** (the
    filed XBRL auto-extract that populates the master — NOT authoring). "Authoring done" must never read as
    "the flaw is patched" — same discipline as the flat-basket gap.
  The **working Workbench MVP milestone sits AFTER 4b *and* a populated universe.**
- **Slice 5 — the bounded LLM drafter `[FILED]`** — `backend/llm`: drafts the decomposition + the thesis-fit
  prose; the operator ratifies every placement; cites sources; caches; **never sources a number.**

### Decisions locked (design pass)
- **Curation:** surface **and score every candidate**, pre-tag a *suggested* basket; the operator makes the
  final picks.
- **Scores — all data-derived, no model-sourced numbers:** exposure **purity** (revenue mix / ETF weights);
  cash **runway** (cash + burn — the *same dilution clock* the back half uses); **catalyst density** (the live
  feeds); **archetype / risk**. Plus **market cap as a figure, not a meter** (the room-to-grow read).
  *(Liquidity and short interest are filed for later.)*
- **The decomposition:** the chain skeleton — the links, and which name sits in which link — is **LLM-drafted
  reasoning the operator can edit**; the **names and their scores are data, never invented.** *Narrative is the
  operator's, structure is a draft, numbers are facts.*
- **Drafted vs taxonomy — hybrid.** The MVP **drafts fresh** (handles novel, pre-consensus narratives a fixed
  taxonomy would lag — the operator's edge is *early*). Over time a maintained **taxonomy accrues from the
  operator's curation**: saved chains seed related future narratives; the LLM fills gaps. The taxonomy builds
  itself out of normal use — not a separate maintenance project.
- **Per-thesis "specialist" = an accumulating per-thesis knowledge base** (events, news, history, the curated
  chain, the data sources that matter for that sector), reasoned over by a **stateless LLM each time — not a
  model that learns.** Specialization lives in the **data, with provenance** → auditable, survives model swaps.
  When the LLM finds data for a score it **proposes a source**; the pipeline ingests the real number from the
  real source; the operator ratifies. **The LLM never supplies a number.**
- **The DD has two distinct layers** (the mockup labels them): (a) a **stored company-reference block** —
  overview / what the company does / founded / HQ / website: per-security **reference *facts*, stored and
  sourced like any other fact**, reused by any thesis that holds the name; the **LLM never narrates these**.
  (b) Below it, the **auto-drafted thesis-fit prose** — why the name sits in *this* chain: **reasoning, clearly
  labeled as drafted**. Facts and reasoning kept visibly distinct ("stored facts" vs "auto-drafted").
- **Reference-fact freshness = re-FETCH, not re-derivation** — a *third* freshness job. Those stored facts
  change occasionally (pivot, HQ move, rebrand), so they're **re-fetched** (re-deriving a stale HQ just returns
  the stale HQ). Most ride the **per-company EDGAR ingest already run** (name, business/SIC, HQ); the few that
  don't (website, richer description) ride a **low-priority weekly sweep** (filed below). **Appended, never
  overwritten** (bitemporal → history + replay preserved). Distinct from score **re-derivation** (no fetch —
  the seam) and from the **discovery cron** (new entities).
- **Basket & loudness:** the basket is a **quiet output** at the bottom of the research column, *not* a loud
  rail card — **ember loudness stays reserved for armed Board calls** (inverse loudness). The Workbench is a
  calm research surface, not a second Decision Queue.
- **Entry point:** narrative-only for the MVP.

### The one HARD MVP build requirement — the persistence seam *(also Locked)*
When the Workbench MVP is built, the value-chain decomposition **MUST persist as structured data attached to
the thesis**, even though the MVP drafts fresh and doesn't yet read from saved chains.
- **Persists (structure):** the segment list (the links), which security sits in which link, and the operator's
  curation + authorship (drafted-by-LLM vs operator-added / edited / dropped).
- **Does NOT persist (scores):** purity, runway, catalyst density, market cap — they **re-derive on read** from
  the underlying facts (Option B), so a chain reopened months later shows current numbers, never a stale snap.
- **Shape:** an **enrichment of the existing thesis→security membership graph** — a **segment label on each
  `basket_member` edge**, plus the list of segments. **Not a new subsystem.** Persists **OPERATIONAL on the
  thesis spine** (editable config, like the rest of `0003` — the structure is the thesis *definition*, not a
  bitemporal *fact*): no `valid_from`/`recorded_at`, no append-only trigger. *(Built in Phase-2 Slice 1 —
  migration `0008_workbench_chain.sql`.)* **Chain-evolution history is a
  taxonomy-era addition** — versioning is added **when that consumer lands** (taxonomy accrual + the rotation
  record draw on it *then*, not now); a future author must **not** assume the history already exists.
- **Why it's a hard requirement, not a feature:** it is the seam that keeps the drafted→hybrid upgrade
  *additive*. If the MVP throws the drafted chain away, the taxonomy, the knowledge base, and the cron all
  become a retrofit. Persisting the structure costs almost nothing now (the thesis already persists) and keeps
  the upgrade path open.

### Filed, post-MVP (additive — do NOT build for the Workbench MVP)
- **Weekly discovery cron** — finds new names, links, and ETFs *entering* the sector. **Discovery only** —
  scores already stay current via re-derivation on read, so the cron does **not** refresh scores. Weekly, not
  daily.
- **Weekly reference-fact sweep** — re-fetches the stored company-reference facts *not* already covered by the
  per-company EDGAR ingest (website, richer description). The **third** freshness job — distinct from the
  discovery cron (new entities) and from score re-derivation (no fetch). Low-priority, weekly, append-only.
- **ETF-holdings-as-universe-seed** — folds into the Phase-3 ETF radar.
- **Rotation-awareness** — money rotating along the chain over the theme's life. **Not a new predictor;** it
  *emerges* from the existing two-key arming applied link by link (links light up on the Board in sequence),
  made legible by the living-Workbench layer. Reactive and disciplined, never predictive.
- **Taxonomy accrual, the per-thesis knowledge base, liquidity, short interest** also sit here.

---

## Phase 3 — Breadth + depth  *(by appetite, not a strict order)*

**Breadth:**
- **Laggard scanner** — a second confirmation path; `LAGGARD` is already a wired confirmation kind, the
  detector is unbuilt.
- **ETF radar** — universe-seed (where the Workbench's ETF-holdings-as-universe-seed folds in); the N-1A/485
  launch detector (emergence signal); holdings-derived flows; the `safe_sleeve` card field exists but is
  unpopulated.
- **More catalyst sources** (`CATALYST_CONVICTION.md` §3): the automated 8-K material-agreement detector; NRC
  license-action feed; **wire the DOE loans award-type group** so the coded-but-latent loan-guarantee → core
  rule actually fires.

**Depth:**
- **Umbrella / segment hierarchy + multi-segment board** — the `parent_id` seam already exists; this is the
  recursive-CTE reads + the umbrella board UI (the AI-buildout mockup).
- **Live LLM counter-case** behind the assembler's existing hook (the deterministic template stays the
  guaranteed fallback).
- **Workbench post-MVP** (the filed items above) lands here by appetite.

---

## Locked — do not reopen
- **The through-line** (`INVARIANTS.md` #7): size ← grade, hold ← horizon, starter / confidence-cap ← the
  weaker key, catalyst-liveness ← the agreement horizon. **Never re-couple; never add an `if kind ==` branch**
  where a property already carries the signal.
- **All of `INVARIANTS.md`.** In particular **no model-sourced numbers** — extended to the Workbench: the LLM
  drafts the *chain structure* (reasoning the operator edits) and *proposes* data sources, but **never supplies
  a number**; the pipeline ingests the real number from the real source and the operator ratifies. *Narrative
  is the operator's, structure is a draft, numbers are facts.*
- **The catalyst grade rule** — customer-vs-sponsor (contract ≥ $10M = core; loan / loan-guarantee = core;
  assistance / OTA / grant = flip); **grade is the nature of the commitment, not its size.**
- **Trust before front-half build.**
- **The recalibration backlog stays consolidated in `RECALIBRATION.md`** — reference it, don't scatter it back
  into the plan.
- **The Workbench persistence seam** (above): the value-chain **structure** persists **operationally on the
  thesis spine** (a `segment` label + `authored_by` on `basket_member`, a `segments` list on `thesis` —
  editable config, **not** bitemporal); the **scores never persist** (they re-derive on read). A build
  requirement, not a feature. *(Chain-evolution history is deferred to the taxonomy era — see the seam section
  above.)*

## Parked — deliberate non-goals
- **Execution / brokerage — permanently out.** Advisory-only is the product's identity.
- **Deferred** (scope, not principle): intraday / streaming data; options-gamma; borrow + squeeze radar;
  crypto / commodities / prediction markets; runtime auth / multi-tenant serving; Dagster; Neo4j.
- **The live Scoreboard is the one parked item clearly coming** — the **forward twin of the replay harness**.
  Where replay asks "would this have flagged X historically, without lookahead?", the Scoreboard scores the
  platform's live calls vs the operator's decisions vs the follow-blindly counterfactual. It earns trust by
  *living with the calls over weeks*; it arrives with live use.
