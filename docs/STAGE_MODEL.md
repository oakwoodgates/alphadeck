# STAGE_MODEL.md — the pipeline of stages (read this first)

> Repo path: `docs/STAGE_MODEL.md`. **The organizing frame** — the lens the other docs hang off. Alpha Deck is a
> **pipeline of stages, each a trust handoff.** This doc names the stages, states the gradient they form, and maps
> each to the docs that detail it. It is the *frame*; `PROJECT_OVERVIEW.md` is the *why*, `README.md` the sketch,
> `CLAUDE.md` the build agreements, `ROADMAP.md` the sequencing. This doc references the others, never duplicates.

---

## The pipeline

**SEED → DISCOVER → SURFACE → TRIAGE → MONITOR → SCORE.**

Six stages. Each takes the prior stage's output and hands the operator a decision. The point of naming them: the
work only became tractable once we stopped saying "the Workbench" and started saying *which stage*. Future work is
framed by **stage**, not by surface.

| Stage | What happens | Who leads | Detail doc(s) |
|---|---|---|---|
| **SEED** | The operator banks a narrative / early conviction in their own words (the vault). The edge is theirs, preserved. | Operator | `PROJECT_OVERVIEW.md` §2/§5 |
| **DISCOVER** | Theme → names. EDGAR-first deterministic enumeration off an operator-seeded term set — not model recall. | Deterministic (+ LLM organizes/narrates) | `DISCOVERY.md`, `CHAIN_DRAFTER.md` |
| **SURFACE** | The system POPULATES each name — estimated scoring facts + machine-parsed identity — for the operator to confirm. | System recommends | `WORKBENCH_EXTRACTION.md`, `WORKBENCH_ENRICHMENT.md` |
| **TRIAGE** | Craft the discovered draft into a chosen, weighted basket through **three gates**: cheap cut (zero API) → mark for data (bounded spend) → finalize on data. | Operator crafts | `TRIAGE.md` |
| **MONITOR** | The chosen basket becomes a parked thesis → incubate → warm → arm → monitor after an operator-entered position. The two-key call loop; `Managing` is thesis monitoring, not portfolio risk management. | System times and watches the thesis; operator gates | `BOARD.md` (the surface), `CALL_LOGIC.md` (the brain), `FEED_LOOP.md` (the rhythm) |
| **SCORE** | Forward attribution — the platform's calls vs the operator's decisions (follow-blindly = v2). *(v1 built — the Scoreboard.)* | System judges | `REPLAY.md` (the historical twin) → `SCOREBOARD.md` (the forward one) |

## The trust gradient

The stages form a **gradient of who is trusted with what**:

**operator-authored (SEED) → deterministic (DISCOVER) → system-estimated (SURFACE) → operator-confirmed (TRIAGE) →
watched (MONITOR) → judged (SCORE).**

Trust in the *machine* rises through DISCOVER/SURFACE (it enumerates, it estimates) and then hands back to the
*operator* at TRIAGE (they confirm, prune, weight) before the back half times it. Nothing the machine estimates
ever acts on its own.

## Every boundary is a "recommend → confirm" handoff (the #10 family)

Each stage boundary is the same shape: the system makes a **visible, pending recommendation**; the operator's
**confirm is what acts** (`INVARIANTS.md` #10). The danger was never the recommendation — it was a recommendation
being *auto-applied*. This pattern now recurs across the whole arc — the **#10 family**:

- **DISCOVER** — the **tier recommendation** (SIGNAL vs BROAD per term) and the **off-thesis flag** (the narrator's
  "doesn't fit" opinion) — recommend; the operator confirms/prunes.
- **SURFACE** — the **grounded purity estimate**, the **market-cap estimate**, and the **derived archetype** —
  recommend a value/class; the operator confirms or overrides.
- On confirm, **authorship transfers to the operator** (`system_drafted` → `operator_edited`) — the record shows
  the operator as the decider, and the confirmation is stable across a re-roll.

The invariant already anticipated this ("any later flag/archetype recommendation"); the family is now shipped.
See `INVARIANTS.md` #10.

## The third thread — cost is the operator's to spend, never spent ambiently

Real usage falsified the hidden assumption "you'll want data on everything placed" — a 370-name draft made
extract-everything unaffordable *and unnecessary*. The resolution is a **third protective thread**, peer to the
other two: the **trust invariants** protect *decisions* (the system must never lie, drop, or auto-decide), the
**interaction principles** (`CLAUDE.md`) protect the *workflow* (reversible, visible, honest) — this one protects
**affordability**. It is the same recommend → confirm seam extended from the *trust* axis to the *cost* axis:
the system **surfaces everything** (recall stays sacred — the draft stays huge, #9), and the **operator decides
where money is spent**. Concretely: extraction and price pulls run only on an explicit per-name or per-section
click — never on draft, save, promote, or render, and never as a batch over the basket.

Two vocabulary words carry it (`TRIAGE.md` has the mechanics):

- **Shortlist** — the survivors of the cheap cut; the ONLY set expensive operations touch. Cost scales with the
  operator's shortlist, not draft size — which is what lets discovery over-include freely.
- **Mark for data** — the per-name, opt-in act of spending on a shortlisted name (shipped as *the control is
  the trigger*: one click = one name's extraction + price pull, cost visible per click).

This is TRIAGE's **three-gate** shape — cheap cut (judge on free row data) → mark for data (spend on the
maybes) → finalize on data (ratify per fact) — and it maps 1:1 onto the buy-side screen → shortlist → diligence
funnel.

## ⚠ ARCHITECTURAL CONSTRAINT — SURFACE estimates are COMPUTED-ON-READ, NEVER persisted as fact rows

> **A SURFACE estimate must NEVER be written to a `fact_*` table. Not "should not" — MUST NOT.**
>
> The bitemporal as-of scoring read (`db/bitemporal.as_of`) has **no `ratified_by` filter**, and its
> `PointInTimeData` is the **shared** read for the Armed-call detectors. So **any** estimate written as a `fact_*`
> row would immediately, silently leak an **unverified number** into BOTH the Workbench score AND the back-half
> Armed call — a #1/#3 violation (`INVARIANTS.md`) that **throws no error**; it just quietly corrupts the call.
>
> Therefore estimates live **only** in the extract endpoint's computed-on-read response; the **only** writer of a
> `fact_*` row is the operator's confirm/override (`POST /workbench/facts`, `ratified_by="operator"`). The three
> fact states (estimated / confirmed / overridden) are DERIVED per read by comparing the computed estimate to the
> latest ratified fact — never stored as an estimate row.
>
> **The trap for a future author:** "let's cache the estimate for perf" or "let's persist it so the meter shows it
> pre-confirm" reintroduces exactly this leak. If you need the estimate faster, cache it OUTSIDE the `fact_*`
> tables / outside the as-of read — never inside. The full detail lives in `WORKBENCH_EXTRACTION.md` (same
> callout, repeated on purpose).

## Where this maps

**The professional buy-side funnel** — idea → screen → diligence → basket curation → monitor → attribution — maps 1:1:
SEED=idea, DISCOVER=screen, SURFACE=diligence, TRIAGE=basket curation (not position sizing), MONITOR=monitor,
SCORE=attribution. **The
wedge:** **DISCOVER** (theme→names) and **SCORE** (per-thesis forward attribution) are the two stages the existing
tools serve worst — that's where Alpha Deck is differentiated, not in re-deriving screeners that already exist.

**"The Workbench" is THREE stages in one UI** — the tail of DISCOVER (resolve the discovered names) + SURFACE
(populate + confirm) + TRIAGE (craft the basket). The Board/Cockpit is MONITOR; the built, forward-accruing
Scoreboard is SCORE.
Naming the three stages separately is what kept each buildable.

**The "two halves on one spine"** (`PROJECT_OVERVIEW.md` §4) is the same spine at coarser grain: **front half** =
DISCOVER + SURFACE + TRIAGE (hunt + research); **back half** = MONITOR (park + time); **SEED** is the vault the
front half draws from; **SCORE** is the forward twin of the back half's replay. The stage model refines that view;
it does not replace it. The **thesis object** is the spine every stage reads and writes.

## Conscious out-of-scope — decisions, not gaps

These are deliberately **not** stages, and their absence is a design choice to record so a future author doesn't
read it as an omission:

- **OMS / execution / position sizing / portfolio construction and risk** — the firm's existing external
  systems. Alpha Deck hands off research and timing context, records the operator's decisions/fills, and
  monitors the entered thesis; it does **not** route orders, choose an instrument or allocation, set risk
  limits, or manage a position. TRIAGE captures a per-name **conviction weight** (1–5, `TRIAGE.md`) as forward
  metadata, but does no optimizer, risk-parity, or sizing math. Only *evaluation* math enters — at SCORE
  (attribution), never as a construction step.
- **MNPI / compliance surface** — out **by design.** The platform reads only public EDGAR + free data; there is no
  private-data ingestion surface, so there is no MNPI vector to police. Advisory-only, EDGAR-only.
- **Quant / chart / TA signals** — parked. The edge is narrative-early + filing-derived timing, not price-pattern
  prediction.

---

**Read next:** `PROJECT_OVERVIEW.md` (why), then the stage doc for whatever you're building. If you're touching the
front half, `DISCOVERY.md` → `WORKBENCH_EXTRACTION.md` / `WORKBENCH_ENRICHMENT.md` → `TRIAGE.md`, in stage order.
