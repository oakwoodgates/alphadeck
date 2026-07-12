# ROADMAP.md — the live plan

> Repo path: `docs/ROADMAP.md`. **This is the live plan** — where we are, what's next, what's locked, what's
> parked. History lives in the **shipped log** (one line per arc + a pointer); the detail lives in the canon
> docs, never here. Siblings: **`STAGE_MODEL.md` (the organizing frame — read first)**, `CALL_LOGIC.md` (the
> brain), `INVARIANTS.md` (the load-bearing rules), the stage docs (`DISCOVERY.md`, `WORKBENCH_EXTRACTION.md`,
> `WORKBENCH_ENRICHMENT.md`, `TRIAGE.md`, `CHAIN_DRAFTER.md`, `FEED_LOOP.md`), `RECALIBRATION.md` (the dial
> backlog), `PROJECT_OVERVIEW.md` (design rationale).

---

## Where we are

**Both halves are built; the whole pipeline runs on real data.**

- **The back half** (MONITOR): bitemporal store, two-key arming, the pure call-assembler, the catalyst
  subsystem, the M5 per-member ranked menu + theme arming — and it **feeds itself** (per-thesis ingest +
  the daily call-of-record cron, `FEED_LOOP.md`). Trust work shipped: the replay harness, the first
  recalibration pass, the production-tenant cut.
- **The front half** (DISCOVER → SURFACE → TRIAGE): narrative → EDGAR-first discovery (deterministic EFTS
  off an operator-seeded term set, recall 31/32 against the answer key, honest coverage/cap/sweep reporting)
  → drafted chain the operator ratifies → the **three-gate TRIAGE flow** (cheap cut → mark-for-data →
  finalize on data; `TRIAGE.md`) with per-section data pulls, honest extraction flags (one observed
  condition, one label), canonical-primary CIK resolution, and the archetype decided once on the rail.
- **Live-fire hardening happened on a real thesis** (the AI-memory gate walkthrough): the every-name
  dual-class mis-flag, the invisible ratify, the 2018 stale-marketable composition, and the ticker-less
  `master.get` crash were each caught live and fixed with regression tests.

**The MVP gate RAN (2026-07-10)** — the AI-memory thesis end-to-end on real data: 68 names ingested
(+41k insider txns, +8k bars), the call computed **WARMING / not-yet / `exit_by` signal-validity horizon**
with a real MU insider
conviction (Form 4-linked) against volume-backed watch names — the two-key discipline live; the daily
cron sidecar runs nightly and its same-day idempotency held in production (0 appended · 11 unchanged).
The North Star's *Armed* call now awaits a real two-key coincidence — correct behavior, which the cron
will catch.

**Since the gate:** **decision capture** shipped (#143/#144/#146 — the operator-decisions log:
take/pass/close/void, the derived position that makes **Managing** (in-position thesis monitoring, not risk
management) reachable, the gate's override
record, the Scoreboard's operator column accumulating from day one); **catalyst + kill-criteria
authoring** (#147 + the FE pair — the Key-1 conviction-fact form with a REQUIRED citation, the Cockpit
calendar/kill editors, and the third wipe-trap fixed structurally); **thesis archive** (board hygiene —
archive-never-delete; the cron walks only the live board); and the **notify PIPE** (transition
detection vs the prior call-of-record + a Notifier seam — delivery deliberately deferred; a channel is
one adapter away).

> **Keep the trust state honest (the load-bearing recalibration caveat).** "The platform feeds itself daily"
> is TRUE; **"the forward trust loop is closed" is NOT** — the **Scoreboard v1** now stands as its instrument,
> but evidence arrives only as matured forward outcomes accrue (nothing is validated at n=0, and no small
> threshold closes the loop). The `n ≥ 5` aggregate-metric gate is a UI safeguard against over-reading early
> summaries, **not** an evidence threshold. The first
> recalibration pass (#45) tuned **direction from the literature + the mechanism**; the replay only
> *illustrated*, it never *selected* a value. Its seed is **n=19 episodes, only UNH a long forward arc**, so its
> honest claim is "the dials are grounded in replayed history + the one obvious lever was inspected" — **NOT**
> "the system is validated." It shipped **zero dial changes** (the disciplined result at n=19; the one give-back
> — the insider-core signal-validity window ended ~35d past the realized peak — is a setup-strength *shape*
> problem [wire field `confidence`; age-decay, `RECALIBRATION.md` B.4], not
> a magnitude the binary liveness window can fix). The daily call-of-record is the forward RECORD the Scoreboard
> now tracks. Do not round "feeds itself" up to
> "validated forward."

## Organizing principle — two halves on one spine

- **Front half (hunt + research):** narrative → value chain → surfaced, scored names → a chosen basket.
- **Back half (park + time):** the basket becomes a parked thesis → incubate → trigger → graded call →
  monitor the entered thesis through the signal-validity window.
- **The spine:** the thesis as a first-class object connects the two (`PROJECT_OVERVIEW.md` §4).
- **Sequencing rule (locked): trust before front-half build** — honored; both are behind us.

---

## Shipped log — one line per arc (detail = the canon doc + the PRs)

| Arc | PRs | Canon |
|---|---|---|
| Back-half core: store, two-key arming, assembler, catalysts, through-line | ≤ #39 | `CALL_LOGIC.md`, `CATALYST_CONVICTION.md` |
| Confirmation honesty + the Workbench design pass (mockup + spec) | #40, #41 | `docs/mockups/mockup_workbench_nuclear.html` |
| M5: per-member ranked menu + theme arming | #42, #43 | `CALL_LOGIC.md`, `THEME_CONVICTION.md` |
| Phase 1 trust: replay harness · recalibration pass 1 (zero dial changes) · production tenant | #44, #45, #46 | `REPLAY.md`, `RECALIBRATION.md`, `PRODUCTION_TENANT.md` |
| Workbench MVP: persistence seam · ratify bridges + seed · scoring engine · UI · authoring | #47–#54 | `WORKBENCH_SCORING.md`, `TRIAGE.md` |
| Extract → ratify → score hybrid + the broadener (any-name universe) | #55–#58 | `WORKBENCH_EXTRACTION.md` |
| LLM seam 1: the FLAG-explanation drafter (structural no-write bound) | #59 | `WORKBENCH_EXTRACTION.md` |
| LLM seam 2: the narrative → chain drafter (response-only; exact membership decides) | #61–#65 | `CHAIN_DRAFTER.md` |
| M1 front door (create/edit thesis, the wipe-trap) + M2 feed loop (ingest, cron, price seam, sidecar) | #67–#73 | `CHAIN_DRAFTER.md`, `FEED_LOOP.md`, `DATA_SOURCES.md` |
| Config centralization (prompts/dials/URLs → files + typed Settings) + quick-win refactors | #75–#81 | — |
| EDGAR-first discovery: research two-step → EFTS pipeline → term set → seed-edit UI → async draft job | #83–#101 | `DISCOVERY.md` |
| The #10 recommendation family: tier rec · off-thesis flag · SURFACE estimates · derived archetype · enrichment | #102, #105–#118 | `INVARIANTS.md` #10, `WORKBENCH_ENRICHMENT.md` |
| TRIAGE crafting: include/find/weight + To-Review ruleset + row polish | #113–#122 | `TRIAGE.md` |
| Honest discovery: coverage + retry, capped terms, tail-sweep tri-state, status strip, worker guard | #123, #124 | `DISCOVERY.md`, `INVARIANTS.md` #9 |
| Draft persistence: the `/data` cache volume + the write-only draft run log + the run loader | #125, #131 | `DISCOVERY.md` |
| Canonical-primary CIK resolution (composite rank; the empirical gate killed SEC-file-order) | #126 | `DISCOVERY.md` §3, `INVARIANTS.md` #2 |
| The three-gate TRIAGE round: board partitions + acronym lens · archetype-null (rail = single home) · per-name get-data | #127–#129 | `TRIAGE.md` |
| Gate-3 honesty: shares period-of-report + three labels · scored-view visibility · per-section data + decoupled price leg · runway honest flags + one-date rule | #132–#136 | `WORKBENCH_EXTRACTION.md`, `FEED_LOOP.md` |
| Runway re-tier (flags mark exceptions) · finalize feel-of-control (on-file values, AUTO shows work, honest empty purity) | #140, #141 | `WORKBENCH_EXTRACTION.md` |
| **The MVP gate, run live (2026-07-10)**: 68-name ingest → WARMING call w/ MU insider conviction → cron nightly, idempotency held in prod | — | `FEED_LOOP.md`, `CALL_LOGIC.md` |
| Decision capture: the operator-decisions log (take/pass/close/void) · Managing thesis-monitoring state reachable · the gate's override record | #143, #144, #146 | `CALL_LOGIC.md` §2 |
| Catalyst + kill-criteria authoring (cited conviction facts + the calendar/kill editors; wipe-trap #3 fixed structurally) · thesis archive · the notify pipe | #147–#150 | `WORKBENCH_EXTRACTION.md`, `FEED_LOOP.md` |
| Excluded-name permanence (#7): the durable NO + optional reason, seeded greyed on re-draft (discovery never filters) | #151 | `TRIAGE.md` |
| Re-version restated bars (source-strategy A): a split self-heals in one cron tick; replay stays honest | #152 | `DATA_SOURCES.md` |

## Standing design decisions (made once, still steering)

- **Curation:** surface **and score every candidate**; the operator makes the final picks. The basket is a
  **quiet output** — ember loudness stays reserved for armed Board calls.
- **Drafted vs taxonomy — hybrid.** The MVP drafts fresh (novel narratives outrun any taxonomy); a maintained
  taxonomy **accrues from the operator's curation** over time — built out of normal use, never a separate
  maintenance project.
- **Per-thesis "specialist" = an accumulating per-thesis knowledge base** reasoned over by a **stateless LLM
  each time** — specialization lives in the data, with provenance; survives model swaps. The LLM proposes
  sources; the pipeline ingests the real number; the operator ratifies. **The LLM never supplies a number.**
- **The DD has two layers:** stored company-reference **facts** (sourced like any fact, never narrated) vs
  auto-drafted thesis-fit **reasoning** (clearly labeled drafted). Visibly distinct, always.
- **Reference-fact freshness = re-FETCH, not re-derivation** — a third freshness job (weekly, append-only),
  distinct from score re-derivation (no fetch) and the discovery cron (new entities). Filed below.

---

## What's next (by appetite, in rough order)

1. **The live Scoreboard — v1 BUILT** (the forward twin of replay; `docs/SCOREBOARD.md`): the episode ledger
   over the two record streams — the platform's calls scored on their own `exit_by` signal-validity yardstick
   + the operator's decisions joined to the episodes they answered (overrides with outcomes); the UI gates
   aggregate metrics below `n=5` and metrics include only matured, non-censored episodes. That gate prevents
   premature display; it is not an evidence threshold, and passing it does not establish calibration. The
   growing record still unlocks the **second, out-of-sample
   recalibration** — that arrives as the record accrues. Next here: **replay-history-alongside** (the
   immediate follow-up), then the v2 follow-blindly track + deltas (+ a metrics-led second view behind a
   toggle once n accrues).
2. **Notify DELIVERY** — the pipe + transition detection are built (`backend/notify`, the daily's material-
   transition compare); picking a channel (email / push / webhook) is one adapter behind `get_notifier()`.
   Deferred by operator call ("other things coming").
3. **Cron scaling refinement** — decouple "record ALL theses" (cheap, keep daily) from "ingest ALL theses
   daily" (live pulls): ingest active theses daily, dormant less often. Fine at today's scale; jotted.
4. **Re-draft drift detection** — flag an accepted name when its underlying data changed since acceptance
   ("re-confirm?"). Shares the MONITOR/Board drift machinery — **build it with the Board**, not in the
   Workbench.
5. **Workbench backlog, as-encountered** (never a batch): ambiguous send-back symmetry, tier-rec badge noise,
   send-back discoverability, accept-color decoupling, pre-revenue purity chip, explain transport-error,
   FE async-boundary tests, off-universe label copy, dilution polarity styling.

### Filed, post-MVP (additive)

- **Weekly discovery cron** — new names/links/ETFs *entering* a sector (discovery only; scores already
  re-derive on read).
- **Weekly reference-fact sweep** — re-fetch the stored company-reference facts not covered by the
  per-company EDGAR ingest (website, richer description). Append-only.
- **ETF-holdings-as-universe-seed** — folds into the Phase-3 ETF radar.
- **Rotation-awareness** — money rotating along the chain; *emerges* from two-key arming applied link by link,
  made legible on the Board. Reactive, never predictive.
- **Taxonomy accrual, the per-thesis knowledge base, liquidity, short interest.**

---

## Phase 3 — Breadth + depth (by appetite, not a strict order)

**Breadth:**
- **Laggard scanner** — a second confirmation path; `LAGGARD` is a wired confirmation kind, the detector unbuilt.
- **ETF radar** — universe seed; the N-1A/485 launch detector (emergence signal); holdings-derived flows; the
  `safe_sleeve` card field exists but is unpopulated.
- **More catalyst sources** (`CATALYST_CONVICTION.md` §3): the automated 8-K material-agreement detector; the
  NRC license-action feed; wire the DOE loans award-type group so the loan-guarantee → core rule fires.

**Depth:**
- **Umbrella / segment hierarchy + multi-segment board** — the `parent_id` seam exists; recursive-CTE reads +
  the umbrella board UI (the AI-buildout mockup).
- **Live LLM counter-case** behind the assembler's existing hook (the deterministic template stays the fallback).
- **Workbench post-MVP** (the filed items above) lands here by appetite.

---

## Locked — do not reopen

- **The through-line** (`INVARIANTS.md` #7): categorical call strength ← grade; signal-validity window ←
  horizon; provisional-call wording / setup-strength cap (`confidence` in code) ← the weaker key;
  catalyst-liveness ← the agreement horizon; sizing / instruments / execution / portfolio risk ← the operator
  and external systems. **Never re-couple; never add an `if kind ==` branch** where a property already carries
  the signal.
- **All of `INVARIANTS.md`.** In particular **no model-sourced numbers** — extended to the Workbench: the LLM
  drafts *structure* and *proposes sources*, never supplies a number. *Narrative is the operator's, structure
  is a draft, numbers are facts.*
- **The catalyst grade rule** — customer-vs-sponsor (contract ≥ $10M = core; loan / loan-guarantee = core;
  assistance / OTA / grant = flip); **grade is the nature/call-strength class of the commitment, not its
  obligation amount or a position-size instruction.**
- **The Workbench persistence seam** — the value-chain **structure** persists **operationally on the thesis
  spine** (`segment` + `authored_by` on `basket_member`, `segments` on `thesis` — editable config, **not**
  bitemporal); the **scores never persist** (re-derived on read). Chain-evolution *history* is a taxonomy-era
  addition — a future author must **not** assume it already exists.
- **The recalibration backlog stays consolidated in `RECALIBRATION.md`** — reference it, don't scatter it.
- **Trust before front-half build** *(honored — kept for the record)*.

## Parked — deliberate non-goals

- **OMS / execution / brokerage / sizing / portfolio risk — permanently external.** Advisory research and
  thesis monitoring are the product's identity; Alpha Deck hands off to the firm's existing systems and does
  not replace them.
- **Position sizing / portfolio construction, MNPI/compliance, quant/chart signals — out by design, not gaps**
  (`STAGE_MODEL.md` records the reasoning). TRIAGE captures a conviction *weight* but does no sizing math;
  the platform is EDGAR-only (no private-data surface → no MNPI vector); TA/price-pattern prediction is parked.
- **Deferred** (scope, not principle): intraday / streaming data; options-gamma; borrow + squeeze radar;
  crypto / commodities / prediction markets; runtime auth / multi-tenant serving; Dagster; Neo4j.
- **The live Scoreboard v1 is built** — the forward twin of the replay harness. It can earn trust only by
  *living with the calls over time*: the ledger is standing and forward outcomes accrue. The `n ≥ 5` UI gate
  merely suppresses tiny aggregates; it is not the point at which evidence becomes sufficient.
