# INVARIANTS.md — the load-bearing rules, in one place

> Repo path: `docs/INVARIANTS.md`. `CLAUDE.md` states the **product** invariants (advisory-only, thesis-is-
> the-spine, opinionated-on-timing, inverse-loudness, …). This file states the **implementation** invariants
> that those depend on — the ones that were load-bearing in practice but lived only in our heads and in
> scattered code comments. A change that violates one of these is a bug, not a trade-off. Each names where
> it's enforced so it can't quietly rot.

---

## 1. No model-sourced numbers or firings  (CLAUDE.md invariant #3)

The LLM **augments, never sources.** It may draft the `counter_case` / explanatory prose citing existing
evidence IDs, a grounded plain-English explanation of an extracted FLAG candidate, or a value-chain DRAFT —
segments + names + thesis-fit prose — from a narrative (the two Workbench seams, below). It must **never** fire
a trigger, set a state / verdict / grade, or invent a number. Every trigger and grade comes from a
**deterministic parse** of data **or** a **one-time operator ratification**.

- *Enforced by:* the assembler signature (the LLM hook only injects `counter_case`); detectors are pure
  `f(point_in_time_data) -> SignalEvent`; the catalyst grade is `_derive_grade` (deterministic) or set at
  ratification — see `ingest/doe/feed.py`, `ingest/catalyst.py`, `calls/assembler.py`. The **theme
  conviction**'s grade + horizon are operator inputs on the ratified fact (`ingest/theme_conviction.py`),
  never the model's.
- *Also enforced by (the first LLM seam — the flag-explanation drafter, `backend/llm`):* the explanation
  rides a **separate rail** — `POST /workbench/facts/explain` has **no DB connection, writes nothing, and is
  never a field on `RatifyFactRequest`** — so it **structurally cannot become a fact**; the ratified number
  comes only from the operator's typed field. The prompt asks for components + direction, never the final
  value, but the **missing rail is the guarantee, the prompt only the courtesy**. Guarded by a
  no-ratify-field test + a **zero-`fact_*`-write** test on the explain endpoint. See `WORKBENCH_EXTRACTION.md`.
- *Also enforced by (the second LLM seam — the narrative→chain drafter, `backend/llm/chain_decomposition.py`):*
  it sources **no number** three ways — the tool schema + `ChainDraftOut` have **no value field** (structural);
  the prompt forbids any figure (Sonnet the adherence lever, the gate-2 manual no-number check its real test);
  and a drafted name is **UNSCORED** until the operator extract→ratifies it. The draft endpoint
  (`POST /workbench/theses/{id}/draft-chain`) is **RESPONSE-ONLY + test-enforced** — it holds a read-only conn
  (so NOT structural-by-absence like the flag seam) and writes nothing (`test_draft_endpoint_writes_nothing`:
  zero `fact_*` AND `basket_member`); the operator's promote is the only writer. See `CHAIN_DRAFTER.md`.
- *Also enforced by (the third grounded seam — the purity estimate, `backend/llm/purity_estimate.py`):* it PROPOSES
  a purity %, but **grounded ONLY in the fetched segment-footnote passage it carries** — a non-grounded proposal
  (or a % out of range) is discarded, never surfaced as a number; the estimate is **computed-on-read, never a
  `fact_*` row** (the leak-proof SURFACE constraint — `WORKBENCH_EXTRACTION.md`), and the operator's
  confirm/override is the only writer. Fail-open to HUMAN. The narrator's **`off_thesis` bool** rides the same
  discipline — a display-only opinion on `ResolvedPlacement`, never a number, never on the call path
  (`CHAIN_DRAFTER.md`).

## 2. Entity resolution is an exact-membership ALLOWLIST, never fuzzy, never a denylist

A name is resolved to a security only by **exact membership** in a hand-curated table (e.g. the DOE feed's
`recipient_id` → ticker). Fuzzy search may be a **discovery net**, but it never *decides* a mapping. An
unknown entity is **dropped** (unresolved), not guessed — an allowlist, so noise fails closed. (Why this is
non-negotiable: fuzzy "Oklo" matches a polluted homonym holding **$48B** of national-lab contracts;
"Centrus" matches an unrelated NAC International. See `docs/DATA_SOURCES.md`.)

- *Enforced by:* `ingest/doe/entities.resolve` (exact `recipient_id` lookup); rejection test
  `tests/ingest/test_doe_feed.py::test_resolver_is_exact_not_fuzzy`.

**The Workbench securities resolver is the same rule on the populated universe.** The broadener
(`pipeline.populate_master`) loaded the SEC `company_tickers` universe into the master, so `master.search` now
spans thousands of names — but it is still a **discovery NET**: it surfaces exact master rows for the operator
to PICK (the row shows ticker + CIK, so a homonym like Oklo is disambiguated by sight), and it **never decides
a mapping** — the operator commits the exact `security_id`. More rows strengthen the net; exact-identifier-
decides is unchanged. The broadener itself writes **only** exact `company_tickers` mappings — an incomplete
row (missing CIK or ticker) is **dropped**, never guessed.

- *Enforced by:* `securities/master.search` (read-only — never an ingest, never a write); the broadener's
  exact `(cik, ticker)` keying that drops incomplete rows (`securities/master.populate_universe`,
  `tests/securities/test_populate_master.py`).

**The chain drafter is where that net DECIDES, and promote is where it's enforced (S5).**
`resolve_placements` (`backend/workbench/chain_draft.py`) runs each drafter-proposed name through the master
and auto-places **only on a unique EXACT ticker or name match**; several / partial / token-only matches or a
ticker/name contradiction go to an **operator pick** (ticker + CIK shown), and a no-match name is **ABSENT**
(shown, never placed). Auto-place **never rests on a judgment call** — a lone substring match is the Oklo-trap
heuristic, not membership. The UI makes this VISIBLE: PLACED auto-loads (drafted, prunable), AMBIGUOUS enters
the basket **only by an explicit operator pick**, ABSENT is shown-not-placed. And because the drafter writes
nothing, **promote is the single write-side check** — every placed `security_id` must be an exact master
member, else `404`.

- *Enforced by:* `workbench/chain_draft.resolve_placements` (exact ticker/name → PLACED, else pick / absent;
  `tests/workbench/test_chain_draft.py`); the promote membership guard + honored-authorship
  (`app/routers/workbench.py`; `tests/app/test_workbench_api.py`). See `CHAIN_DRAFTER.md`.

**The M2 back-half ingest honors the same rule on the WRITE side.** `pipeline.ingest_thesis` targets each
basket member's already-resolved `security_id` via `master.get` (issuer ticker + CIK) — **never a fresh fuzzy
resolve**; an unresolved member is skipped and a foreign id reported, never guessed. (It adds no invariant; it
honors this one.) See `FEED_LOOP.md`.

- *Enforced by:* `pipeline.ingest_thesis` (resolves via `master.get`, skips null / foreign ids;
  `tests/pipeline/test_ingest_thesis.py`).

## 3. Provenance to a real, checkable source on every trigger

Every fired trigger carries provenance that resolves to a **real source** — a working EDGAR Form-4 URL, a
`usaspending.gov/award/…` URL, a `price:TKR:date` computation record — **plus the parsed terms** (show the
work). No black-box outputs; if you can't show the source, don't surface the result. (CLAUDE.md #6.)

- *Enforced by:* `Provenance` on every `SignalEvent`; the card resolves them to clickable URLs; tests assert
  the award/accession refs are present (`test_doe_feed`, `test_hims_armed_call`).

## 4. No lookahead — every read is as-of a timestamp  (CLAUDE.md invariant #1)

The store is bitemporal (`valid_from` = event time, `recorded_at` = when we learned it). Detectors read
**only** through the as-of accessor; a fact dated after the query `asof`, or a correction recorded after
`known_at`, is invisible. Facts are **append-only** — a correction is a new row, never an in-place `UPDATE`.
Firings are **re-derived from facts on every read** (no persisted firing layer), so corrections propagate and
replay stays honest.

- *Enforced by:* `db/bitemporal.as_of` (security-scoped) + `as_of_thesis` (thesis-scoped, e.g.
  `fact_theme_conviction`), both delegating to the shared `_as_of`; the append-only DB trigger; the
  correction-axis test (`tests/ingest/test_pit.py` / the bitemporal tests).
- *Also honored by (M2):* the per-thesis ingest leaves `recorded_at` to the DB default `now()` — **never
  backdated** — and the daily cron pins `asof=today` / `known_at=now` (a live read), so a fact ingested today
  is invisible to an as-of read pinned at an earlier transaction time (`tests/pipeline/test_ingest_thesis.py`,
  the no-lookahead test). See `FEED_LOOP.md`.

## 5. Tenant isolation; production is a fresh tenant, never a destructive wipe

Every row carries `tenant_id`. Dev/demo data is a tenant; **production is a new tenant**. Never build a
destructive reset path or assume a single global tenant — seeds are idempotent and additive.

**Isolation is discipline + the poison-row test, NOT RLS** — the `security_id` FK carries no tenant, so
nothing at the database layer *forces* a read to pass the right `tenant_id`. The standing obligation: **every
new read path MUST route through the tenant-filtered accessors** (`db.bitemporal.as_of` / `as_of_thesis`,
`securities/master.*`, the `PointInTimeData` methods) — never a raw fact query — **and the isolation test
MUST GROW with each new read surface**. A forgotten filter on a raw query would leak with no DB backstop to
catch it. (RLS is the auth-era defense-in-depth; see `docs/PRODUCTION_TENANT.md`.)

- *Enforced by:* `tenant_id` on every table; `DEFAULT_TENANT_ID` for the demo; seeds upsert/append; the
  poison-row proof `tests/db/test_tenant_isolation.py` (grown for each new accessor — insider/price/theme,
  the three scoring facts, the Workbench scored read).

## 6. The call-assembler is pure and deterministic

`assemble_call(thesis, events, asof, cfg)` is a pure function: same inputs → byte-identical CallCard. No
DB/network/clock inside it; `asof` is always a parameter (no implicit "now"). The `calls` table is an
**accountability record**, never the read path (the API recomputes live).

- *Enforced by:* the assembler takes `cfg` + `asof` as parameters; determinism golden tests; the read path
  recomputes via `pipeline/call_for_thesis`.
- *Also honored by (M2 — Option B intact):* the daily cron (`pipeline.daily`) appends the day's call-of-record
  via `calls_repo.record_if_changed` — idempotent (the `calls` log is immutable [`no_update`] + non-unique, so
  a conditional append, not an UPSERT) — but it builds **no read-serving signal/score cache**; the serve path
  still recomputes from facts, and the log is never read back to serve. (`_canonical` makes the compare
  order-independent so a pure reorder can't re-append; `tests/repositories/test_calls_repo.py`,
  `tests/pipeline/test_daily.py`.) See `FEED_LOOP.md`.

## 7. Factor behavior on the property that drives it — never on grade-as-a-bundle or signal kind

The through-line (CALL_LOGIC). Entry **size** ← grade; **hold-or-don't** ← horizon; **starter + confidence
cap** ← the weaker (entry) key; catalyst **liveness** ← the agreement's horizon. **Never** re-couple these,
and never add an `if kind == …` branch where a property already carries the signal. A new signal kind
inherits correct behavior from its own properties.

**Theme conviction (M5b) is the canonical case.** It's a Key-1 *fallback* emitted at **`flip`**, so it caps
the call through the weaker-key path exactly like any flip — there is **no** "theme is capped" branch. The
only reads of the theme kind are: set membership in `conviction_kinds`; the **`own_conviction_kinds`**
exclusion (`conviction_kinds − {THEME_CONVICTION}` — the seam that keeps a future conviction kind inheriting
"own" automatically); the `theme_armed` **display flag**; and the `is_own` **ranking tiebreak**. None is a
behavior branch.

**The Workbench scorer is the front-half case.** Each of the four meters is factored on its own driving
property — purity on revenue-mix %, runway on cash/burn, catalysts on live-count + grade, dilution on
overhang % — with **no `if kind ==` branch anywhere in the engine**. The 0-4 pip cutoffs all come from
`CallConfig` (no hardcoded thresholds), the same property-keyed discipline.

- *Enforced by:* CALL_LOGIC §3/§4/§7; the verdict keys on `conviction_hold_threshold_days`, not kind; the
  confidence cap keys on `is_starter` (entry grade), not kind; the theme conviction caps via its `flip`
  grade (`signals/theme_conviction.broadcast` emits flip), with the `own_conviction_kinds` property in
  `domain/config.py` — see `docs/THEME_CONVICTION.md` §5. For the scorer: a **behavioral** magic-number test
  (a changed cutoff changes a pip) + a **lexical** float-literal scan of `workbench/scoring.py`
  (`tests/workbench/test_scoring.py`).

## 8. The dilution overhang is computed ONCE — never backed out of the clamped severity

`dilution_clock.overhang_pct(facts, sid, asof)` is the **single source** of the raw convert-overhang % —
read by **both** the back-half risk-veto (`dilution_clock.score`) and the Workbench dilution meter. The meter
buckets on this raw %; it **must NEVER recover the overhang from the risk `severity`**, because severity
*saturates*: `severity = min(overhang / dilution_overhang_severe_pct, 1) × risk_block_severity`, so above the
severe bar the overhang is unrecoverable from it. A future reader will be tempted to derive the meter from
the already-computed severity — that is the trap. One raw computation, shared; "—" when there are no live
converts (no fake zero). *(The same one-source discipline as the back half re-deriving firings on read — #4.)*

- *Enforced by:* `signals/dilution_clock.overhang_pct` (the shared pure helper) used by both `score` and
  `workbench/scoring._dilution`; the dilution-meter golden test asserts the raw % bucket
  (`tests/workbench/test_scoring.py`); the existing `tests/signals/test_dilution_clock.py` covers `score`.

## 9. Recall is sacred — a silently dropped name is a system failure

Surfacing every real on-thesis name is the point of discovery. The asymmetry is load-bearing: a
**false positive** (junk surfaced) is **visible and deletable** — a nuisance; a **false negative**
(a real name dropped) is **invisible and gone** — the operator can't evaluate, delete, or even know
about a name they never see. So discovery **optimizes for recall and over-includes**; precision is
handled by the operator deleting visible junk in a lower-confidence tier, **never** by a filter that
might silently eat a real name. (Why this is non-negotiable: this rule has been violated five distinct
ways — the EFTS hit-cap, the pagination cap, the silent-recall fallback, the per-page skip, and a
seed-demotion-to-BROAD — each a plausible local fix that quietly cost real names. Every one passed its
own unit gate; none was caught by anything but a recall re-score.)

Rules this imposes on any change touching discovery / classify / filters / caps / term tiers:
1. **No change may REDUCE recall to gain precision, speed, or cost without PROVING recall is preserved.**
   Re-run the answer-key score; recall must hold (currently 31/32). "Probably fine" / "the dropped pages
   are noise anyway" is an assumption, not proof — and exactly the wording that preceded the misses above.
2. **No name may lose placement/surfacing authority SILENTLY.** If a name changes tier, gets filtered,
   or stops placing, that effect must be VISIBLE — logged, flagged, or surfaced. A demotion the operator
   can't see IS a drop.
3. **Degradation must be LOUD.** A capacity / error / empty condition that can't enumerate the full
   universe must raise or flag (the 503s, `DiscoveryDegraded`), never quietly return a smaller set as if
   complete. Silent fallback to a worse method is worse than an error.
4. **Caps and thresholds are backstops against pathology, never recall limiters.** Default them generous;
   a cap that could drop real names on a NORMAL run is too low — prove it doesn't.
5. **"Make the failure rarer" is not "make the failure safe."** Robustness reduces frequency; it does not
   satisfy this invariant. The failure MODE itself must be visible or impossible.

When this conflicts with tightness, determinism, cost, or convenience: **recall wins, and the trade-off is
surfaced to the operator, not silently resolved in code.**

- *Enforced by:* the answer-key recall re-score on every discovery-touching change (2026-07 live re-score:
  **31/32 holds** — ATAI, the historical miss, is now RECALLED, and the one non-placeable name is PRTG, which
  DELISTED out of SEC `company_tickers`: structurally unplaceable but SURFACED shown-not-placed → counted
  recalled by operator ruling, since #9 tests silent drops, not placeability; the full record is in the
  fixture's header) — the ground
  truth is the committed fixture `backend/tests/fixtures/recall_answer_key.py` (seeds + 32 acceptable-ticker groups
  + the collision-junk set), so the gate is re-runnable; the
  reliability raises (`DiscoveryDegraded` / `DiscoveryEmpty` / `DiscoveryNoTerms` → 503, never a recall
  fallback — `workbench/discovery.py`), with the degraded raise on **post-retry** counts that ride the
  operator-facing message; the per-CIK reconciler that set-difference-guarantees every
  discovered in-master CIK reaches the draft (`workbench/chain_draft.resolve_discovered_chain`); the VERIFY
  tier that surfaces low-confidence adjacents rather than dropping them (`ingest/edgar/fulltext.classify`). The
  full discovery system this invariant governs: `docs/DISCOVERY.md`.
- *Also enforced by (the honest-discovery slice — rules 2/3/4 made structural, not log-only):* the per-run
  **coverage report** (`DiscoveryRun.coverage` → `ChainDraftOut.report` → the Workbench **status strip**) — a
  sub-threshold EFTS gap used to pass looking complete (a log line the operator never reads); now pages
  ok/attempted + the failed TERMS ride every draft, after **one politeness-budgeted retry pass** over the
  failed subset (a recovered page-0 also fetches the deep pages it owed — the silent-partial trap, pinned by
  `test_discover_retry_recovered_page0_fetches_its_deep_pages`); the **hit-cap flag** (`capped_terms` + the
  `⚠ capped` chip — rule 4's "hitting the backstop goes on the record", `test_discover_reports_capped_term`);
  the **tail-sweep tri-state** (`TailSweep.status` — a LOST sweep reads `failed`, never conflated with
  "ran-and-found-nothing" or the deliberate no-key `skipped`); the **narration fill count** (M of N on the
  report); and the **single-worker startup guard** (`draft_jobs.assert_single_worker`, the app lifespan +
  the Dockerfile's explicit `--workers 1` — >1 worker silently broke the 409 guard and job polls).
- *Also honored by (SURFACE/TRIAGE):* the **off-thesis flag** RECOMMENDS removal but the name **STAYS PLACED** —
  a flagged name is never a silent drop, the operator prunes it (`CHAIN_DRAFTER.md`). In **TRIAGE**, every "hide"
  is a visible, reversible, still-promotable collapse (excluded rows, the To-Review "Low signal" / "No listed
  ticker" drawers) and the sort/filter VIEW never changes what Save persists — precision is the operator pruning
  visible names, never a filter that silently eats one (`TRIAGE.md`).

## 10. The LLM recommends; the operator decides — a recommendation is pending until confirmed, never auto-applied

The invariants above forbid the LLM **deciding** — sourcing a number (#1), deciding a mapping (#2),
firing a trigger, setting a tier that places names. They do **not** forbid the LLM **recommending**: a
**visible, pending** suggestion the operator confirms. The danger was never the recommendation — it was
a recommendation being **auto-applied**. A recommendation that changes nothing until the operator
confirms cannot cause the silent flood or silent drop the other invariants exist to prevent. So the LLM
may be maximally helpful — flag, suggest, recommend a tier — as long as the operator's confirmation is
what acts.

**The boundary:** a recommendation is the model's *loudest possible disagreement that still changes
nothing on its own.* The moment a suggestion can act without operator confirmation, it is a decision,
and the other invariants apply.

This is a **clarification of restraint, not a relaxation.** "Never decides" means never **autonomously
or silently** — not "never informs a decision the operator makes." The platform already runs on this
pattern: the chain drafter PROPOSES names, the operator RATIFIES; exact membership still DECIDES (#2).
A tier recommendation is the same shape one level up.

What this does NOT loosen (state it so the freedom isn't misread): no LLM-proposed term ever places
autonomously; no model-sourced number (#1); recall stays sacred (#9). On confirm, **authorship transfers
to the operator** (`system_drafted` recommendation → `operator_edited`) — so the record shows the
operator as the decider, and a confirmed recommendation is operator-authored and **stable across a
regenerate** (it does not return determinism to the model).

The illustrating case is the *valuable* one, not the defensive one: the model surfaces a discriminating
term the operator **didn't** seed, recommends it as SIGNAL, shows why — and it sits as an opportunity
until the operator adopts it. The project's core value (surface what the operator missed) and its core
restraint (the LLM never autonomously places) live in the same act; pending-until-confirmed is what lets
them coexist.

- *Enforced by:* the authorship model (`system_drafted` → `operator_edited` on confirm; the #100
  diff-stamp `stamp_edited_term_set`, `tests/workbench/test_term_set.py`); the chain drafter's
  propose-then-ratify seam (`workbench/chain_draft.resolve_placements`); the rule that no recommendation
  is persisted as a tier or auto-applied (a recommendation rides display-only, like the `matched_terms`
  provenance tags, never mutating `authored_by` until the operator acts).

**The #10 family — the shipped recommend→confirm seams** (the pattern the frame calls out, `STAGE_MODEL.md`): the
**tier recommendation** (SIGNAL/BROAD per term — `DISCOVERY.md`), the **off-thesis flag** (the narrator's opinion —
`CHAIN_DRAFTER.md`), the **grounded purity estimate** + the **market-cap estimate** (SURFACE values —
`WORKBENCH_EXTRACTION.md`), and the **derived archetype** (`WORKBENCH_ENRICHMENT.md`). Each is the same shape: a
visible, pending recommendation that changes nothing until the operator confirms, on which authorship transfers to
the operator (`system_drafted` → `operator_edited`). Every stage boundary is one of these handoffs.
