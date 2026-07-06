# DISCOVERY.md — the EDGAR-first discovery system

> Repo path: `docs/DISCOVERY.md`. How the Workbench finds the **names** for a thesis: from a narrative to a
> complete, deterministic, CIK-keyed universe of US-listed companies, surfaced PLACED / VERIFY with thesis-fit
> prose and a matched-term provenance tag. This is the name-finding engine behind the narrative→chain front
> door; `CHAIN_DRAFTER.md` is its companion (the authoring / ratify / promote surface that consumes this draft).
>
> House style follows `INVARIANTS.md`: a statement, the war story that earned it, then **Enforced by:** the
> code + tests. Companions: `INVARIANTS.md` (#2 exact membership, #3 no model-sourced numbers, **#9 recall is
> sacred**), `CHAIN_DRAFTER.md`, `ROADMAP.md` (sequencing + the open fork). Engines:
> `backend/ingest/edgar/fulltext.py` (the EFTS enumerator + `classify`), `backend/workbench/term_set.py` (the
> producer), `backend/workbench/discovery.py` (the orchestrator), `backend/workbench/chain_draft.py` (the
> per-CIK reconciler), `backend/llm/keyword_gen.py` + `backend/llm/chain_decomposition.py` (the demoted LLM
> roles), `backend/app/routers/workbench.py` (the `/terms` + `/draft-chain` endpoints).
>
> **Status: BUILT + operator-confirmed live.** Deterministic ~57 PLACED + a VERIFY tier, every name carrying
> thesis-fit prose + a matched-term tag, recall 31/32 on the answer key.

---

## The diagnosis — why single-pass LLM recall cannot do this  *(preserved; still load-bearing)*

The first drafter was a single **forced** Sonnet tool call (`tool_choice` pinned to `draft_value_chain` — it
literally cannot search), proposing names from training recall. That is **recall, not research, and it cannot
be tuned away**:

- **Stochastic.** Three runs of the same psychedelic-therapy thesis returned three different baskets (only two
  names survived all three); one run reached for **NVAX, a COVID-vaccine maker** — off-thesis drift.
- **False absents.** A real listed name lands as "not in your universe" because the model proposes a **dead
  identity** (MindMed → renamed Definium/DFTX, already in the master).
- **`high_beta` on everything** — a non-answer wearing a classification's clothes.

**Completeness is a METHOD problem, not a phrasing problem.** A discovery bake-off (psychedelic thesis, live) put
three methods head-to-head — **A** EDGAR full-text (EFTS), **B** LLM parallel-union, **C** LLM exclude-extend — and
made it concrete: the LLM methods sampled a **different tail each run** (~6 core stable, ~6–7 tail varying, ~$3.60 /
3 runs, plus free-text-parsing fragility + identity hallucination), while **EDGAR full-text returned the same 404
CIKs every time, deterministic, for $0** (ibogaine re-query: 31 = 31, identical). The LLM spend buys variance;
EDGAR buys determinism, and **CIK-keying dissolves the identity + variance problems at once.** Verdict: **method A
as the foundation; kill B** (parallel-union dominated on every axis); the LLM **demoted** off enumeration to the two
roles it is good at — proposing candidate keywords, and a directed tail-sweep (C-style) for names EDGAR
structurally can't see. This is `INVARIANT #9` in its origin: recall is the point, and a stochastic method silently
drops real names.

---

## The architecture — EDGAR-first, deterministic, operator-seeded

One narrative → a complete universe, in six steps. The deterministic layer (steps 1–4) owns COMPLETENESS; the
LLM (steps 5–6) only adds DISPLAY prose + the foreign tail. Nothing here sources a number (#3); discovery only
PROPOSES — exact CIK membership DECIDES (#2).

### 1. The term set — the operator owns the discriminating decision

The thesis OWNS a persisted, tiered **term set** (`thesis.term_set`, JSONB, operational-not-bitemporal, mirrors
`segments`). Each entry is **SIGNAL** or **BROAD**:

- **SIGNAL = operator SEEDS** — anchored canonical compounds (e.g. `psilocybin`, `ibogaine`, `5-MeO-DMT`). A
  SIGNAL hit **places a company alone**. `authored_by = operator_set`.
- **BROAD = keyword-gen proposals** — the LLM brainstorms candidates; a deterministic guard (`assign_tier`)
  tiers each. **No LLM proposal is ever SIGNAL** — it is BROAD (contributes only to the ≥-net, never places
alone) or DROPPED (generic/regulatory noise; a short collision abbreviation like `MDMA`/`DMT`/`LSD` that
  would ≥2-combine into junk). `authored_by = system_drafted`. An LLM term may be **recommended** for
  promotion to SIGNAL, but it becomes `operator_edited` SIGNAL only on the operator's explicit approval —
  the model never self-promotes (INVARIANTS.md #10).

Produced out of band by **`POST /workbench/theses/{id}/terms`** (`produce_terms` → `produce_term_set`): the
keyword-gen LLM proposes, the guard tiers, the operator's seeds are preserved across a regenerate (so a re-POST
re-rolls the LLM half while anchoring the seeds). The **autonomous** "is this term discriminating?"
decision is **off the model and off the draft path** — no LLM term self-tiers to SIGNAL, and discovery
just READS what the operator ratified. The model MAY **recommend** a tier (for an operator seed or its own
proposal); the operator confirms it, exact membership still decides placement, and the confirmed tier is
operator-authored (INVARIANTS.md #10).

**The tier RECOMMENDER (INVARIANT #10) — BUILT.** `POST /workbench/theses/{id}/recommend-tiers`
(`recommend_tiers`, Haiku, own `llm_tier_rec_*` dials) recommends `signal`/`broad` + a one-line reason per term,
judging each independently of its current tier. It is **DISPLAY-ONLY + RESPONSE-ONLY**: a separate wire type
(`TierRecommendation`), never on `ThesisDetail.term_set`, the endpoint calls **no writer** (test-enforced byte-
identical `term_set`), and it is **off `produce_term_set`'s determinism path** (`assign_tier` untouched). The FE
shows every term's recommendation (the "✦ Recommend tiers" button) — loud for a **disagreement** (DEFENSE: your
SIGNAL seed flagged BROAD — caught the nuclear flood before it placed junk; OFFENSE: an unseeded term flagged
SIGNAL — the value cell), quiet for an **agreement** (show-everything in v1 to judge the engine). The **confirm is
the existing tier toggle** — the operator's click stamps `operator_edited` (`stamp_edited_term_set`), never an
LLM-authored SIGNAL; an adopted OFFENSE keeps a "✦ adopted" trace in v1. A recommendation changes nothing until
confirmed → recall stays sacred (#9), no number (#3).

- *War story:* the live draft once placed ~370 junk names (utilities on "substance use disorder", Verisign on
  "MDMA") because keyword-gen put generic/collision terms in its SIGNAL tier and "≥1 signal → PLACED" faithfully
  placed them. The fix took the tiering decision off the LLM entirely.
- *Enforced by:* `workbench/term_set.py` (`produce_term_set`, `assign_tier` — never returns SIGNAL for an LLM
  term; `tests/workbench/test_term_set.py`); `domain/thesis.py` (`TermSetEntry`); the structural wipe-guard —
  `thesis_repo.set_term_set` is the SOLE writer and `upsert`'s SQL never names `term_set`, so a promote omitting
  it CANNOT blank it (`tests/repositories/test_thesis_repo.py::test_upsert_cannot_blank_a_persisted_term_set`).

### 2. EFTS enumeration — deterministic, free, CIK-keyed, parallel under a shared rate limit

`discover(edgar, [*signal, *broad])` queries `efts.sec.gov/LATEST/search-index?q="<term>"` for every US filer
whose filings mention a term, unioning the distinct **CIKs** (each tagged with which terms hit it). It is
**DETERMINISTIC** (an index query — re-running returns the same set), **CIK-keyed** (the stable identity — no
ticker-guessing), and **FREE**. Parallel but rate-bounded: per-term pages fan out over a thread pool, yet every
fetch funnels through the ONE shared `EdgarClient` → the ONE `RateLimiter` (the SEC budget is global), so
concurrency removes serialization without exceeding the limit; `ThreadPoolExecutor.map` yields in input order so
the merge is identical to the sequential walk.

**The run reports its own honesty** (`DiscoveryRun` — the honest-discovery slice, #9 rules 2/3/4 made
structural, not log-only):

- **Coverage + one retry pass.** A page that fails after `polite_get`'s retries is skipped and counted; when
  the fan-out completes, the failed subset gets **ONE retry pass** through the same client → the same rate
  limiter (the same global politeness budget — no new dial). A retry-recovered **page-0 also owes its deep
  pages** (they were never enumerated — recovering page 0 alone would be a silent partial), fetched inside the
  same pass and counted in `pages_attempted`. The **post-retry** failed fraction drives the existing
  `DiscoveryDegraded` raise (the failure MODE stays loud; only its frequency drops — #9 rule 5), and its
  message carries the counts (`"N/M EFTS pages failed"`). A **within-tolerance gap is no longer only a log
  line**: `DiscoveryCoverage` (pages ok/attempted, the failed TERMS, retried/recovered) rides the draft report
  to the operator.
- **The hit-cap is on the record.** `hit_cap` (default 1000, `ALPHADECK_DISCOVERY_HIT_CAP`) is a
  pathological-keyword backstop, not a recall limiter — and HITTING it now flags: the term lands in
  `capped_terms` (+ a WARNING log), rides the draft report, and the FE marks the term chip (`⚠ capped`). Pages
  beyond the cap are genuinely not searched, so a capped term says "deep hits for this term may be missing" —
  visible, never silent (#9 rule 4).

- *Enforced by:* `ingest/edgar/fulltext.py` (`discover` → `DiscoveryRun`/`DiscoveryCoverage`, `Filer`;
  determinism + parallel-==-sequential tests in `tests/ingest/test_fulltext.py`; the coverage/retry/capped
  gates there — `test_discover_retry_recovers_a_transient_page`,
  `test_discover_retry_recovered_page0_fetches_its_deep_pages` (the silent-partial nuance pinned),
  `test_discover_reports_capped_term`, `test_discover_clean_run_coverage`, and the post-retry count asserts on
  the degraded tests).

### 3. classify — PLACED / VERIFY (seeds-only-place)

`master.ids_for_ciks` resolves each discovered CIK to an EXACT in-master member (the cleanest INVARIANT #2),
then `classify` splits the in-master set by tier:

- **PLACED** — hits **≥1 SIGNAL** (a seed — an operator-specified compound). High-confidence; auto-loads.
- **VERIFY** — no signal, hits **≥1 BROAD** (any count). Lower-confidence, **surfaced not dropped**, and
  **promotable** (the operator adds the ones that fit). A not-in-master CIK is omitted here — the tail-sweep's
  job.

The OLD rule was "≥2 distinct keywords OR ≥1 signal → PLACED". The `≥2-broad → PLACED` clause was dropped — see
*SIGNAL = seeds only* below.

- *Enforced by:* `ingest/edgar/fulltext.py` (`classify`, `Discovery`; `tests/ingest/test_fulltext.py` —
  `test_classify_*`). `precision_filter` (the older `≥2-OR-signal` raw pre-filter) is retained for reference but
  is NOT the live path; `classify` is.

### 4. The per-CIK reconciler — completeness is the deterministic layer's, never the organizer's to lose

The organizer (the Sonnet decompose call, step 5) arranges names into value-chain segments — but it is an LLM
and unreliable at completeness. So **`resolve_discovered_chain`** matches each organizer placement to a
discovered CIK by exact ticker/name, then **set-difference-appends EVERY in-master discovered CIK the organizer
DIDN'T emit** to a synthetic "Discovered" segment, by its CIK. A name the organizer silently dropped — invisible
to an eyeball among a plausible-looking many — is caught structurally. The organizer's mistakes cost segment
arrangement, never a lost name. (#9 rule: a dropped name is a system failure.)

- *Enforced by:* `workbench/chain_draft.py` (`resolve_discovered_chain`; the dropped-CIK-surfaces test in
  `tests/app/test_workbench_api.py::test_draft_endpoint_dropped_discovered_name_surfaces`).

### 5. Fail-open batched narration + matched-term provenance

Each surfaced name carries two display layers, both value-free (#3):

- **Thesis-fit prose** — `narrate_placements` (a focused Sonnet step, reusing the decompose client) writes one
  ≤25-word reasoning sentence per PLACED + VERIFY name the organizer didn't narrate. **BATCHED** (chunks of 15
  — a 100+-name universe in one call truncates the tool JSON to nothing) with a **numbered-`ref` join** (the
  model replies by list number, never a re-typed name — the key can't drift), **fail-open per batch** (a failed
  batch keeps `prose=""` and is LOGGED with its reason — visible, never a silent empty), scoped to the
  promotable tiers.
- **Matched terms** — `ResolvedPlacement.matched_terms`, the discovery keyword(s) the name's CIK hit, surfaced
  as a quiet tag (`KAYS ← esketamine, psilocin`). It makes a colliding seed visible at a glance (#6/#9).
- **Discovery source** — `ResolvedPlacement.discovery_source` (`"edgar"` | `"off_universe"`), the ORIGIN of a
  placed name (#6): `"edgar"` = matched a CIK in the EDGAR-discovered universe; `"off_universe"` = resolved
  OUTSIDE it (via the sweep-augmented context — see §6). Set in exactly ONE place, the `_match_discovered_cik`
  fork (matched CIK → `"edgar"`, `None` → `"off_universe"` threaded through `_resolve_one`), so it is
  **orthogonal to status** — an off-universe name can be PLACED / AMBIGUOUS / ABSENT. The FE lights a quiet
  "off-universe" pill from it. It records "off the deterministic universe," **never** the hard claim "the
  tail-sweep sourced this" (`decompose` may surface an off-universe name from its own knowledge); display-only
  like `matched_terms`, never a number (#3), never promoted onto a `BasketMember` (#2).

- *War story:* this seam shipped green on 388 fake-client tests yet produced EMPTY prose for every name live —
  two faults a 1-name fake can't surface (token-ceiling truncation; a name-as-join-key the model formatted as
  "Name (TICKER)"). **A fake-client suite proves wiring, never that the live LLM call succeeds** — live
  confirmation against real Anthropic (token ceilings, join keys, parsing) is the gate, not a green fake suite.
- *Enforced by:* `llm/chain_decomposition.py` (`narrate_placements` — batching, ref-join, per-batch
  fail-open + `alphadeck.llm` logging; `tests/llm/test_chain_decomposition.py`); `workbench/chain_draft.py`
  (`ResolvedPlacement.matched_terms`, populated at both reconciler build sites); the draft endpoint fills + tags
  (`tests/app/test_workbench_api.py`).

### 6. The directed tail-sweep — the names EFTS structurally can't see

`research_tail_sweep` (the demoted Opus web-search role, behind the cost-safety in-flight guard + TTL cache +
`max_retries=0`) is given the already-found list and asked for what's MISSING — the foreign / ADR / brand-new-
listing names with no US filing yet. Framed as a directed sweep, never a bare exclusion (which makes the model
re-list the core and stop early). Fail-open to `None`; additive, never the universe.

Its contribution is made **visible (and so validatable)** by `discovery_source` (§5): a name the sweep-augmented
context surfaced resolves outside the EDGAR universe → `"off_universe"` → the quiet "off-universe" pill. The
signal to watch is *off_universe + PLACED* (a real US-tradeable name EDGAR's term-search missed — the sweep
earning its keep) vs *off_universe + ABSENT* (something with no US listing). The tag records the off-universe
ORIGIN, **not** a hard claim the web-search sourced the name — `decompose` may surface one from its own knowledge.

**LIVE VALIDATION (2026-07-06 — P0 finalize item 6, PASSED).** Run against the psychedelic thesis's REAL
found list (~123 placed+verify names; one bounded Opus pass, status `ran`). The sweep returned **exactly the
tail it was built for**: PharmAla Biotech (PMBHF), Universal Ibogaine (IBOGF), Bionomics (BNOX) — foreign /
OTC / ADR filers EFTS structurally can't see, each flagged US-tradeable. Discipline held on every contract:
**zero found-list names re-listed** (the directed framing works — it hunted what was MISSING); empty corners
declared honestly ("nothing new confirmed rather than padding", corners 2/3); non-tradeable names labeled
rather than dropped or oversold (Mindbloom *PRIVATE*, Awakn *ACQUIRED → Solvonis*, Solvonis *foreign-listed
only*); and **no financial figure in the prose** (numerals appeared only inside drug-candidate identifiers —
SVN-001/-002/-015 — a trial phase, and a founding year; the #3 no-number bound held live). Known limitation
unchanged: `max_retries=0` (the deliberate cost guard) means one transient fault loses the pass — now VISIBLE
as `tail_sweep: "failed"` on the draft report rather than silent (the honest-discovery slice).

- *Enforced by:* `llm/chain_decomposition.py` (`research_tail_sweep`); `workbench/research_runner.py` (the
  in-flight guard); `workbench/chain_draft.py` (`discovery_source`, set at the `_match_discovered_cik` fork);
  `tests/llm/test_chain_decomposition.py`; `tests/workbench/test_chain_draft.py` +
  `tests/app/test_workbench_api.py` (the provenance assertions).

**The end-to-end front door** (`POST /workbench/theses/{id}/draft-chain`): read the stored term set →
`run_discovery` (EFTS → classify) → `research_tail_sweep` → organize (decompose) → `resolve_discovered_chain`
(per-CIK reconcile) → fill prose + tags → `ChainDraftOut`. If the thesis has no term set, or EFTS can't
enumerate the universe, the draft **503s** (`DiscoveryNoTerms` / `DiscoveryDegraded` / `DiscoveryEmpty`) — never
a silent fall back to model recall (#9) — and the failed job's operator-facing message **carries the counts**
(`"discovery unavailable — discovery degraded: N/M EFTS pages failed (X%) after retries"`).

**Every draft carries its run report** (`ChainDraftOut.report`, the honest-discovery slice): EFTS coverage
(pages ok/attempted + the failed terms), the `capped_terms`, the **tail-sweep tri-state** (`ran | failed |
skipped` — `research_tail_sweep` returns a `TailSweep`, so a transiently-LOST sweep is no longer
indistinguishable from "no foreign names exist"; `skipped` = the operator's own no-key/live-disabled config,
rendered quiet), and the **narration fill** (M of N names that needed prose got it — a partial narration was a
log line only). The Workbench renders it as a **status strip**: one muted line at 100% healthy, a loud
flag-block on any gap (inverse loudness), which also disambiguates *done-but-empty* from *failed*. Display-only
RUN state — value-free (#3), never persisted (the draft stays response-only). *Enforced by:*
`app/schemas_api.py` (`DraftReportOut`), the report assembly in `execute_draft`
(`tests/app/test_workbench_api.py::test_draft_report_rides_the_response` / `…_carries_capped_term` / the
tail-sweep status asserts), `llm/chain_decomposition.py` (`TailSweep`;
`tests/llm/test_chain_decomposition.py::test_tail_sweep_failopen_names_the_outcome`), and the FE strip tests
(`frontend/src/workbench/__tests__/ChainEditor.test.tsx`).

---

## The key decisions, and why  *(do not relitigate)*

- **SIGNAL = operator seeds only** (the model may *recommend* a tier; an operator-confirmed recommendation
  is operator-authored and stable across runs, so it does not return determinism to the model — INVARIANTS.md
  #10). Moving the *autonomous* discriminating decision off the LLM made PLACED **deterministic**:
  the same byte-identical **57-name set across three runs** while keyword-gen re-rolled the broad terms each run
  (raw universe 3296 / 1020 / 610). The `≥2-broad → PLACED` clause was the *last* LLM-driven placement authority
  and the source of run-to-run variance (PLACED swung 96 → 184 on the same seeds); demoting it to VERIFY made
  PLACED stable while keeping every name surfaced. *Enforced by:* the determinism re-score (gitignored
  `docs/temp/termset_t2_live_gate.py`) + `classify`.
- **The esketamine case — #9 caught a change we proposed.** A live draft placed off-thesis junk; the suspected
  fix was demoting the worst-offending seed (`esketamine` — a *marketed* drug, Spravato, named in nearly every
  CNS 10-K) to BROAD. The #9 answer-key re-score REVERSED it: demoting esketamine moves **11 real answer-key
  names** to VERIFY (they cite it as comparator) to remove ~10 junk, and leaves other boilerplate junk. So we
  **demoted nothing** — the matched-term tags make junk deletable at a glance at zero recall cost (#9 working as
  designed: over-include, surface the reason, the operator prunes visible junk). Seed quality is an operator
  judgment for the (deferred) edit-UI, never a code filter.
- **CIK-keying dissolves the identity problem.** EDGAR returns CIKs, so the rebrand/DBA/ticker drift that sank
  the LLM methods doesn't matter: MindMed→Definium is one CIK; "Helus" is a Cybin DBA (a trade name, not a
  hallucination) that EDGAR full-text returns under Cybin's CIK regardless. `formerNames` + current-ticker are
  SECONDARY bridges, only for the LLM-proposed tail.

---

## Invariant ties

- **#2 — exact CIK membership decides; discovery only PROPOSES.** EFTS surfaces CIKs; `ids_for_ciks` +
  `classify` place only EXACT in-master members; a VERIFY/AMBIGUOUS name enters the basket only by an explicit
  operator action. *Enforced by:* `fulltext.classify`, `chain_draft.resolve_discovered_chain`, the promote
  membership guard (`app/routers/workbench.py`).
- **#3 — no model-sourced number.** Discovery returns keywords / CIKs / names; the prose is reasoning; nothing
  here is a figure. The narrate + decompose tool schemas have no value field. *Enforced by:* the tool schemas;
  the response shape (`ChainDraftOut` carries no number); the gate-2 manual no-number prose check.
- **#9 — recall is sacred.** The whole architecture is a #9 instrument: deterministic enumeration over stochastic
  recall (rule 1, prove don't assume); the VERIFY tier surfaces low-confidence adjacents rather than dropping
  them; the per-CIK reconciler guarantees no discovered name is lost to the organizer; the `503`s fail LOUD
  rather than silently degrade (rule 3); the matched-term tags make a tier-change visible (rule 2); the
  narration fail-open keeps a name even when its prose breaks. *Enforced by:* the answer-key recall re-score on
  every discovery-touching change (2026-07 live re-score: **31/32 holds** — ATAI, the historical miss, now
  RECALLED; the one non-placeable is PRTG, delisted out of `company_tickers`, surfaced shown-not-placed →
  counted recalled by operator ruling; details in the fixture header) — the
  ground truth is the **committed fixture `backend/tests/fixtures/recall_answer_key.py`** (the operator seeds +
  the 32 acceptable-ticker groups + the collision-junk set that must stay OUT of PLACED; previously it lived only
  in gitignored scripts, so the gate wasn't re-runnable); `workbench/discovery.py` (the `503`s); `fulltext.classify`
  (VERIFY); `chain_draft.resolve_discovered_chain` (per-CIK completeness). See `INVARIANTS.md` #9.

---

## Known deferred gaps  *(pointers — sequenced together in `ROADMAP.md`, not here)*

- **The seed-edit UI `[SHIPPED — #100]`** — the term-set editor (add/remove/promote/demote, regenerate-preserve)
  lives in the Workbench term-set drawer; no longer a gap.
- **Tail-sweep live validation `[VALIDATED — 2026-07-06, PASSED]`** — the dated record is in §6 above
  (PharmAla/Universal Ibogaine/Bionomics returned; zero re-listing; honest empty corners; no financial figure).
- **The identity bridge — DROPPED (operator decision, 2026-07-06, PR #123 gate-2).** The gap it targeted has
  closed from both ends: **clean renames are handled** — the deterministic path is **CIK-keyed**, so a renamed
  filer surfaces under its CURRENT identity regardless of what it used to be called (MNMD→Definium arrives as
  DFTX by its CIK through EFTS; and on the LLM-proposed tail, a kept ticker rescues a rename —
  atai→AtaiBeckley kept `ATAI` → PLACED). And the one hard case — the **ATAI dual-CIK redomicile** (…904 pre /
  …043 post) — **already SURFACES** on a live draft (both CIKs, one ticker-less, both off-universe), so recall
  is satisfied. What a bridge would still buy is collapsing the duplicate row — **identity-MERGE logic,
  subtle-bug-prone (the homonym trap), for cosmetic value** — so it is dropped, not deferred. `former_names`
  stays parsed-but-unused (cheap, tested; its planned consumer was this bridge). *(The analysis kept for the
  record: the LLM-proposed tail can carry a stale name/ticker from the model's training cutoff; a rename +
  ticker change defeats both exact-match signals — MindMed→Definium was that shape before EDGAR-first
  discovery made the deterministic CIK path the source of names. If a real false-ABSENT ever reappears, that
  is the shape to look for.)*
- **Enrichment `[SHIPPED — #105–108, #118]`** — identity (sector / exchange / listing-status gate / filer
  category) + the deterministic **derived archetype** killed the blanket `high_beta` default. Home:
  `WORKBENCH_ENRICHMENT.md`. *(No longer deferred — moved out of this list.)*
