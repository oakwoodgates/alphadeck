# CHAIN_DRAFTER.md — the narrative → value-chain authoring surface (the second LLM seam)

> Repo path: `docs/CHAIN_DRAFTER.md`. How the Workbench turns a drafted value chain into a **ratified,
> promotable** thesis — the **author / ratify / promote** half of the front door: `draft → ratify → extract →
> score → promote`. The **how-the-names-are-found** half (EDGAR full-text enumeration → the operator-seeded
> term set → deterministic PLACED/VERIFY classify → the per-CIK reconciler → batched narration + matched-term
> tags → the tail-sweep) lives in its own home, **`DISCOVERY.md`** — read it first; this doc picks up where the
> draft lands. Companions: `DISCOVERY.md` (discovery), `WORKBENCH_EXTRACTION.md` (the per-name extract → ratify
> side + the FIRST LLM seam), `WORKBENCH_SCORING.md` (what the ratified facts SCORE to), `INVARIANTS.md` (#2
> exact membership, #3 no model-sourced numbers, #9 recall is sacred), `ROADMAP.md` (sequencing). Engines: the
> reconciler/resolver `backend/workbench/chain_draft.py`; the organize+narrate seams
> `backend/llm/chain_decomposition.py`; the endpoint + the promote guard `backend/app/routers/workbench.py`;
> the UI `frontend/src/workbench/` (`ChainEditor` + `useChainDraft` + `AddName`).
>
> **Status: BUILT, then re-pointed onto EDGAR-first discovery.** The authoring spine shipped as S5 (the
> resolver + promote write-guard #61, the Sonnet decompose seam + the response-only draft endpoint #62, the
> `thesis_fit` prose column #64, the draft/ratify UI + the `.env` wiring #65). The drafter then stopped
> enumerating from model recall: discovery now finds the names deterministically (`DISCOVERY.md`), and this seam
> ORGANIZES + narrates that universe. The **front-half loop closes end to end.**
>
> **Legend:** `[BUILT]` shipped · `[FILED]` deferred.

---

## What it is

The operator types a narrative ("small modular nuclear is about to rip"). The draft surfaces the **value
chain** — the **segments** (links: "Reactor developers", "Enrichment & fuel", "Utilities / offtake"), the
**names** that sit in each, and short thesis-fit **prose** (why a name sits there) — into the 4b authoring
surface as `system_drafted`, for the operator to ratify. It is **deferential about the narrative, opinionated
about the chain + the names** — the flaw-patch (name selection) the whole tool exists for.

**The names come from EDGAR-first DISCOVERY, not model recall** (`DISCOVERY.md`): the deterministic EFTS
enumerator finds the US-listed universe by CIK from the thesis's operator-seeded term set; Sonnet only
ORGANIZES that stable set into segments + prose (it never enumerates), and the per-CIK reconciler guarantees no
discovered name is lost to the organizer's layout. This doc is the **authoring / ratify / promote** surface
that consumes the draft — the resolver-decides-at-promote, the no-number bound, the `thesis_fit` home, the
draft/ratify UI, and the create-thesis front door.

**Three authorities stay separate** (the spine of the design):
- **S5 drafts STRUCTURE + NAMES + PROSE.** Never a number.
- **The hybrid extractor supplies FACTS** (`WORKBENCH_EXTRACTION.md`) — the operator ratifies each.
- **The scorer derives METERS** (`WORKBENCH_SCORING.md`) — re-derived on read.

A freshly drafted name is **UNSCORED** (its meters read "—") until the operator runs the extract → ratify loop
on it. *Narrative is the operator's, structure is a draft, numbers are facts.*

## Membership decides (INVARIANT #2) — the reconciler + the master-resolver fallback

**A model name is a discovery suggestion; exact membership decides** — the model proposing "Oklo" does NOT
resolve Oklo; a placed `security_id` is only ever the master row's, never the model's string. With EDGAR-first
discovery, that decision happens TWICE, both in `backend/workbench/chain_draft.py`:

- **The per-CIK reconciler — `resolve_discovered_chain`** (the live path) places by EXACT CIK membership against
  the discovered universe (PLACED ≥1 signal · VERIFY broad-only · the dropped-but-discovered appended to
  "Discovered") and owns COMPLETENESS. Full treatment in **`DISCOVERY.md`**.
- **The master-resolver fallback — `_resolve_one`** (the old `resolve_placements` logic) handles an organizer
  name that matches NO discovered CIK (a tail-sweep / off-universe name): it runs the name through THIS tenant's
  master and classifies it —
  - **PLACED** — a **unique EXACT ticker OR name match** → the master row's `security_id` (auto-place).
  - **AMBIGUOUS** — several / partial / token-only matches, OR a ticker/name CONTRADICTION → the operator
    **PICKS** (each candidate shown with ticker + CIK). **Auto-place never rests on a judgment call:** a lone
    substring match is the homonym-trap heuristic (the "$48B Oklo Technologies" trap), so it falls here.
  - **ABSENT** — no master row → "suggested, not in your universe": shown, never guessed onto a ticker.

Read-only — neither path ingests, writes, or sources a number. (`master.get` fetches the conflicting ticker's
row for the AMBIGUOUS pick list.)

## The organize + narrate seams — Sonnet, structured, fail-open

`backend/llm/chain_decomposition.py` extends the `backend/llm` interface the first seam (#59) established. With
discovery owning enumeration, the decompose call is now an **ORGANIZER** (it arranges the discovered set), and a
second focused call NARRATES:
- **`decompose_narrative(client, narrative, research_context=…)`** + **`DECOMPOSE_TOOL`** — the structured
  contract `segments[2..6] → {label, descriptor?, placements[] → {name, ticker?, prose}}`, with the discovery
  universe threaded in as `research_context` so the model ORGANIZES a stable set into segments + prose (it never
  enumerates). **No value / score / number field anywhere in the schema** (structural). Fail-open on every path
  → `None`.
- **`narrate_placements(client, narrative, items)`** + **`NARRATE_TOOL`** — fills thesis-fit prose for the
  PLACED + VERIFY names the organizer didn't narrate (BATCHED, numbered-`ref` join, per-batch fail-open + logged
  — the mechanism + its live war story are in `DISCOVERY.md`). `{ref, prose}` only — no number.
- The system prompts **FORBID any number** (price / % / share count / runway / market cap). Drafted reasoning,
  not fact — Sonnet is the adherence lever, the gate-2 manual no-number check its real test.
- Dials in `CallConfig`: `llm_decompose_model = "claude-sonnet-4-6"`, `llm_decompose_max_tokens = 2000`,
  `llm_decompose_timeout_s = 60` — separate from the Haiku flag-drafter dials so the first seam is undisturbed.
  Sonnet because organizing a novel narrative is reasoning-heavy and **is** the product (a weak chain defeats
  the flaw-patch).

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

`POST /workbench/theses/{id}/draft-chain` (`backend/app/routers/workbench.py`) runs the EDGAR-first pipeline
(full treatment in `DISCOVERY.md`): read the stored term set → `run_discovery` (EFTS → classify) →
`research_tail_sweep` → `decompose_narrative` (organize) → `resolve_discovered_chain` (per-CIK reconcile) →
fill prose + matched-term tags → `ChainDraftOut {thesis_id, segments, placements}`. The tenant comes from the
thesis (mirrors the scored endpoint).

- **Writes NOTHING.** It returns a draft and persists nothing — the operator's promote is the only writer.
- **The bound is RESPONSE-ONLY + TEST-ENFORCED, not structural-by-absence.** Unlike the flag-explanation
  endpoint (#59, which takes **no DB connection at all** — a write is literally impossible), the draft
  endpoint **holds a read-only conn** (it must, to read the narrative + term set and resolve CIKs). So "writes
  nothing" is guaranteed by **`test_draft_endpoint_writes_nothing`** (zero `fact_*` AND zero `basket_member`)
  + read-only discipline — treat that test as **load-bearing**, not a formality.
- **Discovery is completeness-or-fail (#9), not silently fail-open.** A thesis with no produced term set, or a
  universe EFTS can't enumerate, returns **503** (`DiscoveryNoTerms` / `DiscoveryDegraded` / `DiscoveryEmpty`),
  VISIBLE — never a quiet fall back to model recall. The LLM seams (tail-sweep / organize / narrate) still
  fail-open: their trouble degrades prose, never drops a name, and a failed organize returns 200 with an empty
  draft. With no `ANTHROPIC_API_KEY` the prose/organize degrade and hand-authoring is untouched.

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
- **"Produce term set"** (the writer for `DISCOVERY.md`'s term set) produces + DISPLAYS the SIGNAL/BROAD split
  read-only, so the operator inspects what discovery will read before drafting (a draft with no term set 503s).
- **PLACED** names auto-load as `system_drafted` (badged, prunable). **VERIFY** names (in-universe by CIK,
  broad-only, lower-confidence) sit in a quiet "Verify" section — shown not auto-placed, one-click **add**
  commits the known `security_id` (the same #2 discipline as AMBIGUOUS); they are **promotable**, so they carry
  thesis-fit prose too. **AMBIGUOUS** names are a **pick list** (ticker + CIK) — a non-PLACED name enters the
  basket **ONLY by an explicit operator pick**. **ABSENT** names are shown, never placeable.
- Each PLACED/VERIFY name shows its **matched-term tag** (`← psilocybin` — the discovery keyword(s) that
  surfaced it), display-only provenance, never promoted as a fact.
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
