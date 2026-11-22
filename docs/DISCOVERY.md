# Discovery

How the Workbench finds the companies for a thesis: the card's term set drives an EDGAR full-text search keyed
by CIK, the hits are classified PLACED or VERIFY, a model organizes and narrates them, and the result comes back
as a draft. A user runs it from the Workbench on their own card; a steward's draft also stores each company's
identity on the shared master; the nightly never runs discovery or identity enrichment. Read this before you
change the term set, the enumerator, the caps, the draft job, a model step in the draft, the run log or identity
enrichment. What a user does with the draft is [WORKBENCH](WORKBENCH.md); the recall rules are
[invariant 9](INVARIANTS.md#9-recall-is-sacred).

## Code

| Piece | Module |
|---|---|
| Term set: producer, tier guard, edit stamping | `backend/workbench/term_set.py` |
| Keyword proposals; tier recommender | `backend/llm/keyword_gen.py`; `backend/llm/tier_recommendation.py` |
| EFTS enumerator and classifier | `backend/ingest/edgar/fulltext.py` (`discover`, `classify`) |
| Orchestrator and its failure types | `backend/workbench/discovery.py` (`run_discovery`) |
| Organizer, narrator, tail-sweep | `backend/llm/chain_decomposition.py`; cache and guard in `backend/workbench/research_runner.py` |
| Reconciler, resolver, listing-status gate | `backend/workbench/chain_draft.py` |
| The pipeline and its routes | `backend/app/routers/workbench.py` (`execute_draft`, `start_draft_chain`) |
| Draft jobs; run log; run loader | `backend/workbench/draft_jobs.py`, `draft_run_log.py`, `run_loader.py` |
| Identity | `backend/ingest/edgar/submissions.py` (`parse_identity`), `backend/workbench/enrichment.py`, `securities/master.enrich` |
| Canonical primary | `backend/securities/sec_tickers.py`, `master.ids_for_ciks`, `master.canonicalize_ids` |
| Editor side | `frontend/src/workbench/ChainEditor.tsx`, `DraftStatusStrip.tsx`, `RunPicker.tsx` |

## How it works

### Why EDGAR, not model recall

A model asked to name companies from memory returned a different basket on each run, drifted off thesis, and named
renamed companies under dead identities. EDGAR full-text search returns the same CIKs every run, for free, and a CIK
survives renames and trade names. So the deterministic layer owns completeness, and a model only proposes keywords,
sweeps for the foreign tail, arranges names into segments and writes one sentence per name. There is no fallback to
model recall: a discovery that cannot enumerate the universe fails.

### The pipeline

`execute_draft` runs, in order:

1. Read the card's stored term set; a seeds-only draft keeps the SIGNAL entries only.
2. Enumerate EFTS for every term under per-tier caps (`run_discovery`, `discover`).
3. Resolve each CIK to an exact master row and classify PLACED or VERIFY (`master.ids_for_ciks`, `classify`).
4. Identity: a steward's draft stores it on the master; a user's draft overlays it for this response only.
5. Tail-sweep for names EFTS cannot see (`research_tail_sweep`); skipped on a seeds-only draft.
6. Organize the names into segments (`decompose_narrative`, parsed by `proposed_from_decomposition`).
7. Reconcile the layout against the discovered universe, CIK by CIK (`resolve_discovered_chain`).
8. Narrate every PLACED and VERIFY name (`narrate_placements`) and attach the run report.

### The term set

| Tier | Source | What a hit does |
|---|---|---|
| SIGNAL | the user's seeds (`operator_set`) or a term the user promoted (`operator_edited`) | places the company alone (PLACED) |
| BROAD | keyword proposals the guard kept (`system_drafted`) | corroborates only (VERIFY) |

- `assign_tier` never returns SIGNAL for a model proposal: it keeps it as BROAD or drops it as generic or a short
  collision token. Why: a live draft once placed hundreds of junk names because the model put collision terms in
  SIGNAL; taking the tier off the model made PLACED the same set on every run.
- `POST /workbench/theses/{id}/terms` produces the set. Every user-authored entry is kept verbatim, new seeds join as
  SIGNAL, and only the `system_drafted` terms re-roll.
- `PUT /workbench/theses/{id}/terms/edit` saves the user's edit with no model call; the server stamps authorship by
  diffing the stored set (`stamp_edited_term_set`). A blank or duplicate term is a 422; an empty list clears the set.
- `POST /workbench/theses/{id}/recommend-tiers` returns a model's tier and reason per term on its own wire type and
  calls no writer; the user adopts one through the edit toggle
  ([invariant 10](INVARIANTS.md#10-the-model-recommends-a-person-decides)).
- `thesis_repo.set_term_set` is the only writer; `upsert` never names `term_set`, so a save cannot blank it
  (`tests/repositories/test_thesis_repo.py::test_upsert_cannot_blank_a_persisted_term_set`).
- Seed quality is a person's judgment, never a code filter. Demoting a widely cited seed (esketamine) to cut junk
  would have moved eleven real answer-key companies to VERIFY; the matched-term tags make the junk easy to delete.

### Enumeration (EFTS)

- `discover` queries EFTS per term and unions the distinct CIKs, each tagged with the terms that hit it. Pages fan
  out over a thread pool, but every fetch goes through the one shared `EdgarClient` and its rate limiter
  ([DATA_SOURCES](DATA_SOURCES.md) "EDGAR etiquette"), and the merge equals the sequential walk.
- Caps are per tier: SIGNAL terms enumerate deep (`discovery_hit_cap`), BROAD terms shallow
  (`discovery_broad_hit_cap`), and a term in both tiers enumerates deep. Why: a BROAD hit only corroborates and its
  deep pages are mostly unrelated filers, while a SIGNAL hit places a name, so a real company deep under a seed must
  not be cut. A term that reaches its cap lands in `capped_terms` and its chip is marked. The caps are backstops; a
  normal run should not reach them.
- A failed page gets one more pass after the fan-out, through the same client; a recovered page 0 also fetches its
  deep pages. If the failed fraction still exceeds `discovery_degraded_ratio`, `discover` raises `DiscoveryDegraded`
  with the counts; below it, the gap rides the report's coverage.
- An error body is a failed page, never an empty one. EFTS sometimes answers success with an error body; every page
  is validated and a bad one raises `EftsErrorBody`, takes the retry, and lands in `failed_terms` if still bad.
  `empty_terms` means a valid page 0 with zero hits, and the client never caches a bad body
  (`tests/ingest/test_fulltext.py::test_an_error_body_on_page0_is_RETRIED_and_recovered_never_reported_empty`).

### Classification and the canonical primary

- `classify` splits the in-master CIKs: PLACED has at least one SIGNAL hit; VERIFY has BROAD hits only and is
  surfaced and promotable by hand. A CIK with no master row is left to the tail-sweep. The old rule that two BROAD
  hits make PLACED is gone: it was the last model-driven placement and made PLACED swing between runs.
- A CIK with several master rows (share classes, an ADR beside a foreign ordinary, warrants) resolves to its
  `is_primary` row, ranked by instrument class, exchange, foreign-ordinary demotion, then SEC file order. Why not
  SEC's first row: checked against every multi-row CIK, it picked OTC lines and warrants over the listed share. The
  save re-asserts the same pick, so draft and save cannot disagree (`tests/securities/test_canonical_primary.py`).

### The tail-sweep

`research_tail_sweep`, a web-search model pass, gets the found list and is asked for what is missing: foreign, ADR
and newly listed names with no US filing. It is framed as a directed sweep, never a bare exclusion (which makes the
model re-list the core). It fails open and only adds context; its outcome is a tri-state on the report (`ran`,
`failed`, `skipped`), so a lost sweep never reads as "no foreign names exist". The client runs with `max_retries=0`;
`run_research` caches the result by thesis and narrative for `llm_research_cache_ttl_s` (zero: always fresh). A
name it surfaces carries `discovery_source="off_universe"`: its origin, not a claim the sweep found it.

### The organizer and the reconciler

- The organizer arranges the found names into segments: structure and assignment only. Why no per-name prose: in one
  organize call it grew with the universe and truncated large drafts to zero segments. A stray `prose` field is
  stripped at `proposed_from_decomposition` and counted in a warning.
- `resolve_discovered_chain` matches each placement to a discovered CIK by exact ticker, then exact name. An unmatched
  name goes to `_resolve_one`: a unique exact match is PLACED; a contradiction, several rows or a partial match is
  AMBIGUOUS with candidate rows (ticker and CIK) for a person to pick; no row is ABSENT, shown and never placed
  ([invariant 11](INVARIANTS.md#11-entity-resolution-is-an-exact-allowlist)).
- Every in-master discovered CIK the organizer did not emit is appended to a "Discovered" segment, so the organizer's
  mistakes cost layout, never a company (`test_draft_endpoint_dropped_discovered_name_surfaces`). A failed organize
  leaves every name there.
- The draft never writes Context, ETFs or Bench. A segment named after one is not emitted and its names go to
  Discovered, with a warning carrying the count; only a person places a name in a silent link
  ([CALL_LOGIC](CALL_LOGIC.md) section 1).
- The listing-status gate: a PLACED name whose master row reads `inactive` becomes AMBIGUOUS with its own row as the
  single pick and a hedged flag, so a false inactive costs one click, never a dropped name.
- A resolved placement shows the master row's SEC name and ticker, never the model's echo.

### Narration and display provenance

- `narrate_placements` writes one thesis-fit sentence per PLACED and VERIFY name, in batches joined back by list
  number, failing open per batch with a log line. It also sets an `off_thesis` flag per name: a recommendation, and
  the name stays placed.
- `matched_terms`, `discovery_source`, `off_thesis` and the identity chips are display only, never promoted onto a
  member. The exception is `surfaced_terms` (below).
- No number: the tool schemas and `ChainDraftOut` have no value field, and the prompts forbid figures
  ([invariant 3](INVARIANTS.md#3-the-model-augments-it-never-sources-a-number-or-a-firing)). A fake-client suite
  proves wiring, never that the live call works: narration once passed hundreds of fake-client tests and returned
  empty prose live. After a prompt or model change, run a live draft and read the prose for numbers.

### The draft job

- `POST /workbench/theses/{id}/draft-chain` starts a background job and answers 202 with a job id; the optional body
  sets `scope` (`full` by default, or `seeds_only`). It answers 409 while the card already has a draft running, and
  429 when `draft_jobs_max_running` drafts run across all cards (checked before the allowance is charged) or when a
  user's daily `chain_draft` allowance is spent.
- `GET .../draft-chain/jobs/{job_id}` polls `running`, `done` with the draft, or `failed` with a message. A job lost
  to expiry or a restart is a 404, shown as a lost draft. The editor polls every 2.5 s and stops at
  `DRAFT_POLL_TIMEOUT_MS` (`ChainEditor.tsx`), below the reaper's running TTL.
- A discovery that cannot run ends the job `failed`, with counts where there are any: no term set
  (`DiscoveryNoTerms`), nothing placeable (`DiscoveryEmpty`), too many failed pages (`DiscoveryDegraded`), or a
  seeds-only draft with no SIGNAL seeds (`DiscoveryNoSeeds`, before any EFTS fetch). The model steps fail open.
  Without `ANTHROPIC_API_KEY` they produce nothing, and seeds, EFTS, classification and hand-authoring still work.
- A seeds-only draft enumerates SIGNAL seeds only and skips the tail-sweep. The scope rides the report, the run log
  and a badge on the status strip: a chosen state, never a silent substitute for a full draft.
- The job opens its own connection and writes no `basket_member` or `fact_*` row
  (`tests/app/test_workbench_api.py::test_draft_endpoint_writes_nothing`). Its only writes are a steward's identity
  enrichment and the run log file.
- The registry is in-process memory. `draft_jobs.assert_single_worker` refuses to boot when `WEB_CONCURRENCY` or
  `UVICORN_WORKERS` asks for more than one worker, and the Dockerfile pins `--workers 1`. More workers would need a
  shared job store first.
- The reaper fails a job running past `draft_job_running_ttl_s` and drops finished jobs after
  `draft_job_finished_ttl_s`. The real cost bound is one tail-sweep pass per job (`max_retries=0` and
  `llm_research_timeout_s`).

### The run report

Every draft carries `ChainDraftOut.report`: EFTS coverage and failed terms, capped and empty terms, the tail-sweep
state, the narration fill (M of N) and the scope. `DraftStatusStrip.tsx` shows one quiet line when the run is whole
and a loud block on any gap, which also tells a done-but-empty draft from a failed one. It is run state, never
stored on the card or in the term set.

### The run log and the run loader

- Each completed job writes `data/draft_runs/<thesis_id>/<timestamp>-<job_id>.json` (`write_draft_run_log`): the
  thesis and narrative, the term set as used, the dials (both caps, the models, the scope) and the full draft. A
  failed write is logged and swallowed; a failed job writes nothing. The files live on the `appdata:/data` volume.
  Why: once a draft is pruned and saved, what it surfaced and under which dials is otherwise gone. A file with no
  `discovery_broad_hit_cap` predates per-tier caps.
- The run loader reads those files back when `run_loader_enabled` is on; it is off by default.
  `GET /workbench/theses/{id}/runs` lists a card's runs and `GET .../runs/{run_id}` returns one as a `ChainDraftOut`
  for the editor to load instead of paying for a fresh draft (`RunPicker.tsx`). Both are 404 when the setting is off
  (the picker hides); a file that no longer validates is a 422. Loading seeds editor state only
  (`test_run_loader_loads_artifacts_with_and_without_scope`).

### Re-scope and `surfaced_terms`

- `basket_member.surfaced_terms` freezes the matched terms that surfaced a company when it enters the basket. The
  save keeps the stored value for a member already on the card, the payload for a new one, and empty when omitted
  (`test_promote_freezes_existing_members_surfaced_terms`). It decides nothing and never reaches the call; invariant 11
  names it as the one provenance field allowed on a member. `repositories/mappers.py` must read it back, or the next
  resave wipes it.
- `pipeline.backfill_surfaced_terms` fills members older than the column by re-running discovery: empty values only
  unless `--overwrite`; a card whose discovery fails is refused and the run continues. The "original" is the match
  against the current term set, so run it before a card's terms are refined; afterwards it cannot be recovered.
- Re-scope (`startRescope` in `Workbench.tsx`) clears only the candidate pile, keeps the saved basket as it is, and
  re-runs discovery on the current term set. A row shows its frozen terms and, apart, the terms that match it now.

### Identity enrichment

- `parse_identity` reads a CIK's submissions JSON: sector, exchange, a listing status, the filer category and the
  origin ingredients. Identity is never a fact, never a number, never promoted onto a member, never a call input.
- `master.enrich` fills the exchange only when it is null: submissions give a company-level exchange, the company list
  writes each instrument's own venue, and overwriting it once labeled a foreign ordinary line "Nasdaq".
- Status is a listing heuristic (a current ticker and exchange read `active`, else `inactive`), never a delisting
  verdict; the user sees a hedged guess.
- Only a genuine submissions document, one that echoes a top-level `cik`, enriches, so a bad fetch cannot harden into
  a false `inactive`. A fault skips that one name, never the draft (`tests/workbench/test_enrichment.py`).
- Writers: a steward's draft (`enrich_for_ciks`), `python -m pipeline.enrich_identity`, and the steward's
  successor-CIK adoption on Admin. A user's draft reads the same submissions through `identities_for_ciks` and overlays
  them for that response only (`overlay_identity`), because the master is shared data only a steward writes
  (`tests/app/test_user_fetches.py::test_a_plain_users_draft_writes_no_master_row_and_still_carries_identity`).
- `pipeline.enrich_identity` re-enriches a whole scope every run (`--baskets` by default, `--thesis`, `--universe`;
  `--live` to fetch), with no `--missing-only`: a per-field "missing" test goes stale each time a field is added. To
  add a field, extend `parse_identity` and the `master.enrich` UPDATE, then re-run it.
- The nightly never enriches identity: the data barely changes, a failure there would sit on the call path, and a
  command with a receipt is safer than a side effect.
- The scored view reads identity and a derived origin through `master.identity_for`; the editor prefers it over chips
  captured at draft time. Business type is derived from the sector by the maps in `backend/securities/business_type/`
  (its [README](../backend/securities/business_type/README.md)). `former_names` is parsed and unused.
- CIK keying absorbs renames and trade names, so there is no identity bridge: a redomicile that leaves two CIKs
  surfaces both, and merging them is bug-prone work for a cosmetic gain.

## Contracts

- The draft, its poll and the run loader write no `basket_member` or `fact_*` row; the save is the only writer of
  the basket, and `thesis_repo.set_term_set` the only writer of the term set.
- `ChainDraftOut` and `DraftReportOut` are the wire shapes (`backend/openapi.json`); the run loader serves the same
  `ChainDraftOut`.
- A file under `data/draft_runs/` is never a fact and never read on the call path.
- Every change to discovery, classification, caps, tiers or filters re-scores the recall answer key
  (`tests/fixtures/recall_answer_key.py`) with the `recall-rescore` skill before it merges.

## Who can do what

Every route sits on the workbench router behind `require_user`; `get_thesis_or_404` limits a card route to the
caller's own workspace (404 otherwise).

| Route, control or CLI | Who | Enforced by |
|---|---|---|
| Produce terms, recommend tiers | any signed-in user, own card; `term_run` allowance; term cap | `allowance.charge("term_run")`, `workspace_caps.check`; `tests/app/test_user_fetches.py::test_the_draft_and_term_allowances`, `tests/app/test_workspace_caps.py` |
| Edit terms | any signed-in user, own card; term cap | `workspace_caps.check`; `test_edit_terms_past_the_cap_is_409` |
| Start a draft | any signed-in user, own card; `chain_draft` allowance (a steward is not counted); global draft cap | `require_draft_room`, `allowance.charge("chain_draft")`; `tests/app/test_job_caps_api.py` |
| Store identity during a draft | steward; a user's draft only overlays it | `execute_draft(steward=...)`; `test_a_stewards_draft_still_stores_the_identity` |
| Poll a draft; list or load saved runs | any signed-in user, own card; runs only when `run_loader_enabled` is on | `get_thesis_or_404`; the setting |
| `pipeline.enrich_identity`, `pipeline.backfill_surfaced_terms`, `pipeline.repoint_canonical` | the operator, in the backend container | no route |

## Settings

A setting reaches the stack only when compose passes it; otherwise the default in `domain/settings.py` applies.

| Knob | Owner | Passed by compose? |
|---|---|---|
| `discovery_hit_cap`, `discovery_broad_hit_cap` | `domain/settings.py` | yes |
| `discovery_max_workers`, `discovery_degraded_ratio` | `domain/settings.py` | no |
| `llm_keyword_*`, `llm_tier_rec_*`, `llm_decompose_*`, `llm_research_*` | `domain/settings.py` | no |
| `draft_job_running_ttl_s`, `draft_job_finished_ttl_s` | `domain/settings.py` | no |
| `draft_jobs_max_running` | `domain/settings.py` | yes |
| `allowance_chain_draft_per_day`, `allowance_term_run_per_day` | `domain/settings.py` ([AUTH](AUTH.md) "User-triggered fetches") | yes |
| `run_loader_enabled` | `domain/settings.py` | yes |
| `ANTHROPIC_API_KEY`, `ALPHADECK_USER_AGENT` | environment | yes |

## Known limits

- One transient fault loses the tail-sweep for that draft (`max_retries=0`); the report shows `failed`.
- The single-worker guard cannot see a `--workers` flag typed on the command line.
- The no-number bound on drafted prose rests on the prompt wherever the schema cannot block a figure; there is no
  post-filter.
- AMBIGUOUS and ABSENT rows carry no thesis-fit sentence.
- A restart loses running and finished drafts; the run log keeps completed ones.

## Related

- [WORKBENCH](WORKBENCH.md): the user's flow on the draft.
- [INVARIANTS](INVARIANTS.md) 3, 9, 10 and 11; [CALL_LOGIC](CALL_LOGIC.md) section 1 (the silent links).
- [DATA_SOURCES](DATA_SOURCES.md) (EDGAR etiquette); [AUTH](AUTH.md) (allowances and workspace caps).
- Skills: `recall-rescore` (the recall gate), `seed-thesis` (seed a card on dev).
