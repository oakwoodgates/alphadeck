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
ORGANIZES that stable set into segments — **structure + assignment only, no per-name prose** (it never
enumerates); the batched narration step authors every placed/verify name's thesis-fit sentence (the prose
reroute — per-name prose in the organize call scaled its output with the universe and truncated large drafts
to zero segments); and the per-CIK reconciler guarantees no discovered name is lost to the organizer's layout.
This doc is the **authoring / ratify / promote** surface that consumes the draft — the
resolver-decides-at-promote, the no-number bound, the `thesis_fit` home, the draft/ratify UI, and the
create-thesis front door.

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
  contract `segments[2..6] → {label, descriptor?, placements[] → {name, ticker?}}`, with the discovery
  universe threaded in as `research_context` so the model ORGANIZES a stable set into segments (it never
  enumerates). **Structure + assignment only — no per-name prose field** (the prose reroute: per-name sentences
  made the ONE organize call's output scale with the universe and truncate past `llm_decompose_max_tokens` — a
  387-name draft collapsed to 0 segments live; the batched narrate step is the prose author). Segment
  `descriptor`s are retained (the organizer still reasons at the link level). **No value / score / number field
  anywhere in the schema** (structural). Fail-open on every path → `None`. Defense-in-depth: any stray `prose`
  the model emits despite the schema is **stripped at the single LLM-output door**
  (`proposed_from_decomposition`) and WARN-logged with a count — wasted output tokens are a prompt bug, never a
  silent cost leak.
- **`narrate_placements(client, narrative, items)`** + **`NARRATE_TOOL`** — **the sole prose author**: fills
  thesis-fit prose for every empty-prose PLACED + VERIFY name — organizer-placed and reconciler-appended alike,
  each narrated WITH its real segment label threaded into the numbered line (BATCHED, numbered-`ref` join,
  per-batch fail-open + logged — the mechanism + its live war story are in `DISCOVERY.md`).
  `{ref, prose, off_thesis}` — no number. AMBIGUOUS / ABSENT rows are not narrated (accepted: the "Couldn't
  resolve" drawer is identity triage, not thesis-fit — those rows render without a fit sentence).
- **The off-thesis flag (#117).** `narrate_placements` also emits a per-name **`off_thesis` bool** — surfacing the
  "doesn't fit the thesis" judgment the narrator already makes in its prose (a boilerplate term-collision) as a
  structured bit. It is set at the narration MERGE (`execute_draft`) onto `ResolvedPlacement.off_thesis`, **display-
  only** like `matched_terms` / `discovery_source` (never a number #3, **never promoted onto a `BasketMember` #2**,
  never on the call path). It **RECOMMENDS (#10), the name STAYS PLACED (#9)** — membership is deterministic
  exact-CIK, so a flagged name is never a silent drop; the operator prunes it (the TRIAGE include-uncheck).
  **Coverage = every placed/verify name** (since the prose reroute the narrator judges organizer-placed and
  reconciler-appended names alike — the old organizer-placements-exempt scope is gone). Conservative: "when
  unsure, leave false." Fail-open: absent `off_thesis` → False (never flag on missing narration). The prompt
  makes the prose STATE the reason so it supports the flag. How the buckets consume it (inverse loudness —
  highlight keepers, quiet the noise): `TRIAGE.md`.
- The system prompts **FORBID any number** (price / % / share count / runway / market cap). Drafted reasoning,
  not fact — Sonnet is the adherence lever, the gate-2 manual no-number check its real test.
- Dials in `CallConfig`: `llm_decompose_model = "claude-sonnet-4-6"`, `llm_decompose_max_tokens = 8000`,
  `llm_decompose_timeout_s = 180` — separate from the Haiku flag-drafter dials so the first seam is undisturbed.
  Sonnet because organizing a novel narrative is reasoning-heavy and **is** the product (a weak chain defeats
  the flaw-patch). One client serves both the organize call and every narrate batch.

## NEVER A NUMBER — schema + prompt + drafted-unscored (INVARIANT #3)

The bound holds three ways:
- **Structural** — the tool schema and `ChainDraftOut` carry no value field; there is nowhere for a number to
  ride into the system.
- **Prompt** — the system prompt forbids figures. This is the half that rests on the prompt; **Sonnet is the
  adherence lever**, and the **gate-2 manual no-number check is its real test** (a fake-client unit test can't
  exercise a prompt). A deterministic regex post-filter is the noted lever if adherence ever slips — **not
  built** (`[FILED]`).
- **Drafted-unscored** — a drafted name has no facts, so the scorer reads "—" until the operator extract →
  ratifies it. Drafting proposes structure + names + prose (the organizer assigns, narration authors the
  prose); the number always enters later, by the operator's hand.

## The draft endpoint — a KICK-OFF → POLL job, RESPONSE-ONLY, test-enforced

The draft takes minutes (EDGAR discovery + the Opus tail-sweep + decompose + narrate). Held open as one
request it blew past nginx's 300s `proxy_read_timeout` — the browser 504'd while the backend kept billing. So
the draft is a **kicked-off JOB**, not a held-open request (`backend/workbench/draft_jobs.py`, an in-memory
registry mirroring `research_runner` — module-level dict + `threading.Lock`, single-worker; a daemon thread runs
the pipeline):

- `POST /workbench/theses/{id}/draft-chain` → **202** `{job_id, status:"running"}` (the kick-off). **409** if a
  draft for the thesis is already running (the in-flight guard, now at the JOB layer); **404** unknown thesis.
- `GET /workbench/theses/{id}/draft-chain/jobs/{job_id}` → the poll: `{status: running|done|failed, result, error}`.
  **404** if the job is unknown / expired / wiped by a restart — the FE shows a visible "draft was lost", never an
  infinite spinner. The FE polls every ~2.5s, caps at ~360s, and stops on a terminal status.

The pipeline itself is unchanged — `execute_draft(conn, …)` (full treatment in `DISCOVERY.md`): read the stored
term set → `run_discovery` (EFTS → classify) → `research_tail_sweep` → `decompose_narrative` (organize) →
`resolve_discovered_chain` (per-CIK reconcile) → fill prose + matched-term tags → `ChainDraftOut`.

- **Writes NOTHING.** The job returns a draft (in memory) and persists nothing — the operator's promote is the
  only writer. The job thread opens its OWN read-only conn (it outlives the request); "writes nothing" is
  guaranteed by **`test_draft_endpoint_writes_nothing`** (zero `fact_*` AND zero `basket_member`) — load-bearing.
- **…except the run-of-record artifact, which is deliberately NOT a write in that sense (a file is not a
  fact).** A COMPLETED job dumps one WRITE-ONLY JSON per run —
  `data/draft_runs/<thesis_id>/<utc-timestamp>-<job_id>.json` (`workbench/draft_run_log.py`, fired by the job
  layer's `on_success` hook AFTER the result is published) — carrying the thesis + narrative, the **term set
  as used** (term/tier/authorship), the dials in effect (hit cap + the two draft models), and the full draft
  (segments, placements with provenance, the honesty report). It is the DISCOVER stage's `calls`-log analogue:
  an accountability record ("what did the 2026-07-06 draft see, and under which dials?"), **never a read
  path** — nothing in the app loads it, promote stays the only spine writer, and `…writes_nothing` stays green
  untouched. A failed artifact write is logged + swallowed (fail-open — it can never cost the operator the
  draft); a failed JOB records nothing. Persists across rebuilds via the compose `appdata:/data` volume.
- **Discovery is completeness-or-fail (#9), surfaced as a VISIBLE failed job, not silently fail-open.** A thesis
  with no produced term set, or a universe EFTS can't enumerate, ends the job **`failed`** with the cause
  (`DiscoveryNoTerms` → "term set is empty…"; `DiscoveryDegraded`/`DiscoveryEmpty` → "discovery unavailable — …",
  **carrying the post-retry counts**, e.g. "12/180 EFTS pages failed (7%) after retries"),
  shown on the poll — never a quiet fall back to model recall. (This moved from a synchronous 503 to a failed
  job in the async-draft slice.) The LLM seams (tail-sweep / organize / narrate) still fail-open: their trouble
  degrades prose, never drops a name, and a failed organize is a **done** job with an empty draft. With no
  `ANTHROPIC_API_KEY` the prose/organize degrade and hand-authoring is untouched.
- **Every draft carries its run report** (`ChainDraftOut.report` — the honest-discovery slice; full semantics in
  `DISCOVERY.md`): EFTS coverage (pages ok/attempted + failed terms, after one politeness-budgeted retry pass),
  the hit-`capped_terms`, the tail-sweep tri-state (`ran | failed | skipped` — a lost sweep is no longer
  indistinguishable from "no foreign names exist"; `skipped` = the operator's own no-key config), and the
  narration fill (M of N). The FE renders it as a **status strip** under the draft controls — one muted line at
  100% healthy, a loud `⚑` block on any gap (inverse loudness) — which also disambiguates *done-but-empty*
  (strip: `0 placed · coverage N/N`) from *failed* (error toast, no strip). Display-only RUN state, value-free
  (#3), never persisted; the `⚠ capped` marker on a term chip is the same run state, never written to
  `term_set`.
- **Cost stays bounded.** One job per thesis (the 409 guard) + the Opus client's `max_retries=0` + 300s SDK
  timeout → an abandoned job (the FE stopped polling) bills at most one bounded pass; a registry reaper
  (`draft_job_running_ttl_s` / `draft_job_finished_ttl_s`) flips a stuck job to failed and bounds the registry.
  **Single-worker is load-bearing AND now guarded** (mirrors `research_runner`'s caveat):
  `draft_jobs.assert_single_worker` (the app lifespan) refuses to boot on env-driven
  `WEB_CONCURRENCY`/`UVICORN_WORKERS` > 1, and the Dockerfile CMD pins an explicit `--workers 1` (a hand-typed
  CLI `--workers 2` with no env var is the guard's stated blind spot — the pinned CMD is the production
  mitigation). `--workers>1` would need a shared (DB-backed) job store first.

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
never the stored company-reference facts (layer a) the LLM does not narrate (`ROADMAP.md`, standing design
decisions).

## The draft/ratify UI — the discovery net, made VISIBLE

`frontend/src/workbench/` — the operator drives the whole loop on screen:
- **"Draft from narrative"** (`ChainEditor`) calls the endpoint on an EXPLICIT click (never on render) and
  **MERGES** the draft into the local chain draft (`useChainDraft.loadDraft`) — never replaces: new segments
  append, PLACED names are added, deduped by `security_id`, so the operator's existing work is never clobbered.
- **The term set** (a collapsible drawer, open by default) is the writer for `DISCOVERY.md`'s term set:
  **Produce / Regenerate** (keyword-gen), **✦ Recommend tiers** (the #10 recommender), and per-term **edit**
  (seed / remove / promote / demote) — the operator curates the SIGNAL/BROAD split discovery reads before
  drafting (a draft with no term set 503s).
- The draft result is organized into **three buckets** (the post-draft IA — `mockup_workbench_results.html`):
  - **PLACED** — a flat list (the operator owns segment; not pre-grouped). Each name carries an **archetype**
    dropdown (wired) + a **segment** dropdown (now **WIRED** to re-segment via `placeMember` — selecting a link
    moves the name into it, flipping `authored_by → operator_edited` so the choice survives a re-roll; no
    "— remove —", pruning is the TRIAGE include-uncheck) + a quiet **authorship** badge + the company **name** +
    the SURFACE identity chips (sector / exchange / filer-category — `WORKBENCH_ENRICHMENT.md`). The archetype
    color shows only once the name is operator-owned / enrichment-derived (an unconfirmed `system_drafted` default
    reads neutral, not a wall of red). The **off-thesis FLAG is now LIVE (#117)** — the narrator emits a structured
    `off_thesis` bool (below); a flagged row shows the ⚑ + reason and **stays placed** (the operator unchecks to
    exclude). TRIAGE/basket crafting over these buckets is `TRIAGE.md`.
  - **TO REVIEW** — **VERIFY** (in-universe by CIK, broad-only) + tail-sweep names merged, one action: a
    **"check to add" checkbox** that commits the known `security_id` (the same #2 discipline as AMBIGUOUS) and
    **moves the row up to Placed** (no "skip" — a candidate is only added or left in the queue). They're
    **promotable**, so they carry thesis-fit prose; the recommended segment rides the provenance line. See
    `TRIAGE.md` for the keeper / off-thesis / ticker-less partition.
  - **COULDN'T RESOLVE** — a quiet drawer ("identity, not thesis-fit"): **AMBIGUOUS** names are a **pick list**
    (ticker + CIK, behind "pick CIK…") — a non-PLACED name enters the basket **ONLY by an explicit operator
    pick**; **ABSENT** names are shown, never placeable. (Retired the old "discovered / unplaced" dropdown.)
- Each PLACED/VERIFY name shows its **matched-term tag** (`← psilocybin` — the discovery keyword(s) that
  surfaced it), display-only provenance, never promoted as a fact.
- **The authorship transitions:** load → `system_drafted`; **accept** → `operator_set`; **edit** a field
  (prose / archetype) → `operator_edited`. A drafted member shows an **accept** affordance + its prose in an
  editable box; a placed-but-unratified name reads **unscored ("—")** until extract → ratify brings the facts
  in (the editor shows no meters; the scored view is fact-derived).
- **CIK is surfaced** in the resolver matches (`AddName`) + the pick list — the homonym tell, by sight.
- In-memory React state only (no browser storage); the full draft persists only on **promote** (the
  full-replace `POST /workbench/theses`, which honors authorship + stores `thesis_fit`).
- **⚡ Quick draft (seeds only)** — the fast lane (draft-scope #303/#304): the SAME kick-off + poll flow with
  `{scope: "seeds_only"}` (the full button still posts NO body — the pre-scope wire holds). Discovery
  enumerates only the operator's SIGNAL seeds, the tail-sweep is skipped, everything downstream unchanged —
  minutes cheaper, and the operator explicitly picked the narrower spend (the cost thread). At zero SIGNAL
  seeds the button **disables with the why** in its tooltip (visible, not vanished); a completed seeds-only
  run stamps a **persistent badge on the status strip** ("Seeds-only draft — BROAD terms + tail-sweep not
  run") — a *chosen* state on the mid-loudness cool tint, never the ⚑ gap block, and it survives the session
  round-trip. Full wire detail: `DISCOVERY.md` §the draft scope.
- **CHERRY-PICK / pick-mode** (PR-3) — the starter-basket inversion of the load. A quiet **tri-state
  load-mode select** beside the draft buttons: untouched ⇒ the LANE decides (⚡ quick ⇒ pick, ✦ full ⇒
  auto-load — the operator-decided pairing); an explicit choice wins for both lanes, and the active mode is
  visible before kick-off (the running draft freezes its mode at kick-off). In pick-mode, **genuinely-NEW
  placed names divert to a Recommended pile** above To-Review (seed-hit — higher confidence than To-Review's
  broad-only) with the same check-to-add gesture: a **pick enters exactly like a To-Review add**
  (`system_drafted`, `signed_off: false` — pick = INCLUDED, endorsement stays a separate act —
  `surfaced_terms` captured, the draft's recommended segment(s); a multi-link name is ONE pile row picking
  into N membership rows), and the placed row carries the visible inverse (**↩ to recommended** restores the
  pile row exactly as it was, WB#1). **THE WIPE-TRAP-COUSIN GUARANTEE:** the mode redirects ONLY
  `loadDraft`'s append-new branch — the editor partitions the result BEFORE calling `loadDraft`, removing
  only placed rows of sids not already in the basket, so every existing member (established, re-rolled,
  parked) takes `loadDraft`'s path byte-identically; "start empty" is about NEW names, never a wipe or
  shrink of the basket. A re-draft merges the pile (dedup by `security_id`; picked names never re-enter;
  un-re-placed pending rows stay visible, WB#2), and the pile + origin map + mode ride the triage session as
  ADDITIVE blob fields (old blobs restore with an empty pile; no SCHEMA_VERSION bump).
- **✓ Sign off all picked** — the bulk endorse for the ORIGIN-TRACKED picked set only (the pile's picks +
  To-Review's adds; hand-adds enter signed off already): picking is deliberate, so bulk-endorsing the picked
  set is honest — auto-endorsing on pick was rejected, the acts stay separate. Renders ONLY when it
  discriminates (≥1 picked, included, un-endorsed name — WB#3), co-mutates a name's multi-membership rows
  with the target computed once, never touches established / excluded / non-picked members, and stays
  reversible per name via each row's sign-off toggle (WB#1).
- *Enforced by:* `frontend/src/workbench/__tests__/ChainEditor.cherrypick.test.tsx` (routing, lane defaults +
  override, pick shape, send-back, re-draft merge, bulk sign-off scope) +
  `triageSession.cherrypick.test.ts` (the additive session fields; SCHEMA_VERSION pinned at 1), and the
  quick-draft/badge suites in `__tests__/ChainEditor.test.tsx`.

## Enablement — `ANTHROPIC_API_KEY` via `.env`

Both LLM seams read `ANTHROPIC_API_KEY` from the environment. The stack reads it from a **gitignored `.env`**
(committed template `.env.example`); `docker-compose.yml`'s `backend` service injects `ANTHROPIC_API_KEY` +
`ALPHADECK_USER_AGENT` (`${VAR:-}`, fail-open) — before this, neither LLM seam worked in the deployed stack.
With no key, both seams degrade to no-output and the rest of the app is unaffected.
The repeatable live check is the Workbench "✦ Draft from narrative" flow: draft a chain from a narrative and
confirm no prose string states a number (the manual no-number gate).

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
