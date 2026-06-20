# CHAIN_DRAFTER.md — the narrative → value-chain drafter (the second LLM seam)

> Repo path: `docs/CHAIN_DRAFTER.md`. How the Workbench turns an operator's NARRATIVE into a drafted,
> ratifiable value chain — the front door to the loop: **draft → ratify → extract → score → promote**. The
> capstone of the front half (S5). Companion to `WORKBENCH_EXTRACTION.md` (the per-name extract → ratify side
> + the FIRST LLM seam), `WORKBENCH_SCORING.md` (what the ratified facts SCORE to), `INVARIANTS.md` (#2 exact
> membership, #3 no model-sourced numbers), `ROADMAP.md` (sequencing). Engines: the resolver
> `backend/workbench/chain_draft.py`; the decompose seam `backend/llm/chain_decomposition.py`; the endpoint +
> the promote guard `backend/app/routers/workbench.py`; the UI `frontend/src/workbench/` (`ChainEditor` +
> `useChainDraft` + `AddName`).
>
> **Status: BUILT** — the resolver + the promote write-guard (PR #61), the Sonnet decompose seam + the
> response-only draft endpoint (#62), the `thesis_fit` prose column (#64), the draft/ratify UI + the
> `ANTHROPIC_API_KEY` / `.env` wiring (#65). With it, the **front-half loop closes end to end.**
>
> **Legend:** `[BUILT]` shipped · `[FILED]` deferred.

---

## What it is

The operator types a narrative ("small modular nuclear is about to rip"). S5 drafts the **value chain** — the
**segments** (links: "Reactor developers", "Enrichment & fuel", "Utilities / offtake"), the **names** that sit
in each, and short thesis-fit **prose** (why a name sits there) — into the 4b authoring surface as
`system_drafted`, for the operator to ratify. It is **deferential about the narrative, opinionated about the
chain + the names** — the flaw-patch (name selection) the whole tool exists for.

**Three authorities stay separate** (the spine of the design):
- **S5 drafts STRUCTURE + NAMES + PROSE.** Never a number.
- **The hybrid extractor supplies FACTS** (`WORKBENCH_EXTRACTION.md`) — the operator ratifies each.
- **The scorer derives METERS** (`WORKBENCH_SCORING.md`) — re-derived on read.

A freshly drafted name is **UNSCORED** (its meters read "—") until the operator runs the extract → ratify loop
on it. *Narrative is the operator's, structure is a draft, numbers are facts.*

## The resolver — exact membership DECIDES (INVARIANT #2)

`resolve_placements(conn, segments, *, tenant_id)` (`backend/workbench/chain_draft.py`) runs every proposed
name through THIS tenant's security master and classifies it. **A model name is a discovery suggestion; exact
master membership decides** — the model proposing "Oklo" does NOT resolve Oklo; a placed `security_id` is only
ever the master row's, never the model's string.

- **PLACED** — a **unique EXACT ticker match OR a unique EXACT name match** → the master row's `security_id`
  (auto-place as a drafted member). The model emits a best-guess ticker per name, so exact-ticker carries the
  clean proposals.
- **AMBIGUOUS** — several / partial / token-only matches, OR a **ticker/name CONTRADICTION** (the exact ticker
  and the exact name resolve to DIFFERENT rows) → the operator **PICKS** from the candidates (each shown with
  ticker + CIK so a homonym is disambiguated by sight). **Auto-place never rests on a judgment call:** a lone
  substring/token match is the homonym-trap heuristic (the "$48B Oklo Technologies" trap), so it falls here,
  not to PLACED.
- **ABSENT** — no master row → "suggested, not in your universe": shown, never guessed onto a ticker.

Read-only — the resolver never ingests, never writes, sources no number. (`master.get`, added with the
resolver, fetches the conflicting ticker's row for the AMBIGUOUS pick list.)

## The decompose seam — Sonnet, structured, fail-open

`backend/llm/chain_decomposition.py` extends the `backend/llm` interface the first seam (#59) established:
- **`DECOMPOSE_TOOL`** — the structured-output contract: `segments[2..6] → {label, descriptor?, placements[] →
  {name, ticker?, prose}}`. There is **no value / score / number field anywhere in the schema** (structural).
- **`SYSTEM_PROMPT`** — bakes in: 2–6 segments; real US-listed companies; a best-guess ticker per name
  (verified against the master, never fabricated); ≤25-word grounded prose; and it **FORBIDS any number** —
  price / % / share count / runway / market cap / catalyst value. Drafted reasoning, not fact.
- **`decompose_narrative(client, narrative) -> dict | None`** — fail-open on EVERY path (no key / timeout /
  SDK error / no tool call / blank narrative → `None`).
- Dials live in `CallConfig`: `llm_decompose_model = "claude-sonnet-4-6"`, `llm_decompose_max_tokens ≈ 2000`,
  `llm_decompose_timeout_s ≈ 20` — **separate from the Haiku flag-drafter dials** so the first seam is
  undisturbed. Sonnet because decomposing a novel narrative is reasoning-heavy and **is** the product (a weak
  chain defeats the flaw-patch). Staged decomposition (segments → names → prose) is the `[FILED]` fallback if
  one call underperforms — a logged trigger, not a default.

## NEVER A NUMBER — schema + prompt + drafted-unscored (INVARIANT #3)

The bound holds three ways:
- **Structural** — the tool schema and `ChainDraftOut` carry no value field; there is nowhere for a number to
  ride into the system.
- **Prompt** — the system prompt forbids figures. This is the half that rests on the prompt; **Sonnet is the
  adherence lever**, and the **gate-2 manual no-number check is its real test** (a fake-client unit test can't
  exercise a prompt). A deterministic regex post-filter is the noted lever if adherence ever slips — **not
  built** (`[FILED]`).
- **Drafted-unscored** — a drafted name has no facts, so the scorer reads "—" until the operator extract →
  ratifies it. Drafting proposes structure + names + prose; the number always enters later, by the operator's
  hand.

## The draft endpoint — RESPONSE-ONLY, test-enforced

`POST /workbench/theses/{id}/draft-chain` (`backend/app/routers/workbench.py`): reads the thesis narrative →
`decompose_narrative` → `proposed_from_decomposition` (a defensive, fail-open parse) → `resolve_placements` →
returns `ChainDraftOut {thesis_id, segments, placements}`. The tenant comes from the thesis (mirrors the
scored endpoint).

- **Writes NOTHING.** It returns a draft and persists nothing — the operator's promote is the only writer.
- **The bound is RESPONSE-ONLY + TEST-ENFORCED, not structural-by-absence.** Unlike the flag-explanation
  endpoint (#59, which takes **no DB connection at all** — a write is literally impossible), the draft
  endpoint **holds a read-only conn** (it must, to read the narrative and run `master.search`). So "writes
  nothing" is guaranteed by **`test_draft_endpoint_writes_nothing`** (zero `fact_*` AND zero `basket_member`)
  + read-only discipline — treat that test as **load-bearing**, not a formality.
- **Fail-open by contract** — any LLM trouble → **200 with an empty draft, never a 5xx**; with no
  `ANTHROPIC_API_KEY` the endpoint is a no-op and hand-authoring is untouched.

## The promote guard — bound #2 at the single writer

Because the drafter returns a draft and writes nothing, **promote (`POST /workbench/theses`) is the single
place exact membership is enforced** — relocated there from the (never-built) S5 write path:
- **Every placed `security_id` must be an EXACT member of this tenant's master, else `404`** (reuses the #56
  ratify write-side tenant check). A buggy or hostile client cannot promote an unresolved / hallucinated id —
  the resolver is the discovery net; this is where membership *decides*, fail-closed.
- **`authored_by` is HONORED** (the validated `Authorship` enum), no longer coerced to `operator_set`. A
  drafted placement the operator keeps stays `system_drafted`; one they edit lands `operator_edited`; an
  out-of-enum value is a `422` at parse time. *(This replaced the old coerce-to-`operator_set` behavior and its
  test.)*

## `thesis_fit` — the drafted prose's home

The per-member "why this name sits in its segment" reasoning persists in **`basket_member.thesis_fit`**
(nullable text; migration `0011`). **Named for WHAT it holds** (the thesis-fit reasoning), not its origin: it
outlives the draft — the operator edits it (`operator_edited`) or hand-authors it (`operator_set`), and
`authored_by` records WHO. Kept **DISTINCT** from `detail` (the live board/cockpit "met" cell, e.g. "mkt
$1.2B", read in the output schemas) and from a segment's own `descriptor`. **Operational** on the thesis spine
(no bitemporal axes), like the rest of the chain structure. `ChainDraftOut` carries the prose as a response
field; the UI maps it to `thesis_fit` on promote. This is the auto-drafted **thesis-fit layer (DD layer b)** —
never the stored company-reference facts (layer a) the LLM does not narrate (`ROADMAP.md` Phase-2 decisions).

## The draft/ratify UI — the discovery net, made VISIBLE

`frontend/src/workbench/` — the operator drives the whole loop on screen:
- **"Draft from narrative"** (`ChainEditor`) calls the endpoint on an EXPLICIT click (never on render) and
  **MERGES** the draft into the local chain draft (`useChainDraft.loadDraft`) — never replaces: new segments
  append, PLACED names are added, deduped by `security_id`, so the operator's existing work is never clobbered.
- **PLACED** names auto-load as `system_drafted` (badged, prunable). **AMBIGUOUS** names are a **pick list**
  (ticker + CIK) — a non-PLACED name enters the basket **ONLY by an explicit operator pick** (which commits
  the exact `security_id`). **ABSENT** names are shown, never placeable.
- **The authorship transitions:** load → `system_drafted`; **accept** → `operator_set`; **edit** any field
  (segment / prose / archetype) → `operator_edited`. A drafted member shows its prose in an editable box with
  an accept / drop affordance; a placed-but-unratified name reads **unscored ("—")** until extract → ratify
  brings the facts in (the editor shows no meters; the scored view is fact-derived).
- **CIK is surfaced** in the resolver matches (`AddName`) + the pick list — the homonym tell, by sight.
- In-memory React state only (no browser storage); the full draft persists only on **promote** (the
  full-replace `POST /workbench/theses`, which honors authorship + stores `thesis_fit`).

## Enablement — `ANTHROPIC_API_KEY` via `.env`

Both LLM seams read `ANTHROPIC_API_KEY` from the environment. The stack reads it from a **gitignored `.env`**
(committed template `.env.example`); `docker-compose.yml`'s `backend` service injects `ANTHROPIC_API_KEY` +
`ALPHADECK_USER_AGENT` (`${VAR:-}`, fail-open) — before this, neither LLM seam worked in the deployed stack.
With no key, both seams degrade to no-output and the rest of the app is unaffected.
`scripts/run_5b_draft_check.ps1` is the repeatable live check: rebuild + restart, draft a chain from a
narrative, and scan every prose string for a number (the manual no-number gate).

## The create-thesis front door  `[BUILT — #67 / #68]`

The drafter operates on a thesis's narrative — and the front door to **create** that thesis from a NEW
narrative is now built (M1, the last front-half gap). The whole loop runs from the UI:
- **"+ New thesis" (M1a, #67)** — a small form (name + narrative) in the Workbench header, rendered even with
  zero theses. Submit calls the existing promote endpoint with a **null id**
  (`usePromoteThesis().mutateAsync({ id: null, basket: [], segments: [] })`) — the upsert's create branch, **no
  new write path** — then switches to the new (Incubating) thesis, ready for **"Draft from narrative."** So:
  **create → land in the editor → draft → ratify → promote.** Frontend-only (the backend create path already
  existed; a fact-less new thesis reads Incubating because state is computed on read).
- **Narrative editing after create (M1b, #68)** — the same `ThesisFields` form, pre-filled, opened from a
  quiet "✎ Edit" next to the narrative. The edit branch resends the SAME id **and the existing basket +
  segments** — the **WIPE-TRAP**: because promote is a full-replace upsert, an edit that sent empty arrays
  would wipe the authored chain, so it must resend them (a vitest asserts the chain survives an edit). A
  non-blocking "narrative changed — consider re-drafting" hint; the chain is never auto-wiped.

With these, the front-half loop is complete from a blank narrative: **create → (edit) → draft → ratify →
extract → score → promote.** After promote, the back half feeds the thesis its call-engine facts — see
`FEED_LOOP.md`.
