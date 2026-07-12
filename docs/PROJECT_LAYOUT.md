# PROJECT_LAYOUT.md — the repo, file by file

> Repo path: `docs/PROJECT_LAYOUT.md`. The anti-black-box **file map**: what each module is and where the
> behavior lives. Companion to `DATA_FLOW.md` (where data lives at runtime), `PROJECT_OVERVIEW.md` (the why),
> `ROADMAP.md` (sequencing), and `CLAUDE.md` (how to build). For the *why* of any subsystem, follow the doc
> links; this is the *where*.
>
> **As of the MVP + the config/quick-win refactor cycle (`main` through PR #81):** both halves are built **and the platform feeds itself.** Back
> half — the bitemporal store, two-key arming, the pure call-assembler, the catalyst subsystem + the DOE feed,
> the M5 per-member menu + theme arming, the replay harness + recalibration + the production-tenant cut. Front
> half (the Workbench) — scoring, authoring, the extract → ratify hybrid, the SEC-universe broadener, **the two
> LLM seams** (FLAG-explanation + narrative→chain, S5), and the **create-thesis front door** (M1). The **M2
> feed loop** — the per-thesis back-half ingest + the daily call-of-record cron + the price-source seam + the
> scheduling sidecar — makes it **feed itself** (`FEED_LOOP.md`). The front-half loop closes end to end
> (**narrative → draft → ratify → promote → extract → score**) and the back half feeds the promoted thesis its
> call-engine facts. Suite: **283 backend** (pytest; DB-backed tests skip without Postgres) + **39 frontend**
> (vitest); `ruff` + `black` + `tsc` + `vite build` clean; CI runs them + the openapi↔types drift guard on
> every PR.

## Tracked hierarchy

```
alphadeck/
├── CLAUDE.md                       # agent working agreements + invariants + the live vocabulary/commands
├── README.md                       # what it is, the stack table, v1 scope
├── docker-compose.yml              # full stack: Postgres + backend + SPA/nginx; + a DISABLED-by-default `cron` sidecar (M2; --profile cron)
├── .env.example                    # env template → copy to .env (gitignored): ANTHROPIC_API_KEY, UA, ...
├── .github/workflows/ci.yml        # CI: backend ruff/black/pytest + openapi-diff · frontend tsc/build/vitest + types-diff
├── infra/docker-compose.yml        # DB-only slice for the local backend dev loop (shares the pgdata volume)
├── scripts/run_5b_draft_check.ps1  # live gate-2 check: draft a chain from a narrative + scan the prose for numbers
├── docs/                           # THE CANON — read STAGE_MODEL.md first (the frame), then by stage
│   ├── STAGE_MODEL.md · PROJECT_OVERVIEW.md · ROADMAP.md · INVARIANTS.md · DATA_FLOW.md · DATA_SOURCES.md
│   ├── DISCOVERY.md · CHAIN_DRAFTER.md · WORKBENCH_EXTRACTION.md · WORKBENCH_ENRICHMENT.md · WORKBENCH_SCORING.md · TRIAGE.md   # the front half, in stage order
│   ├── BOARD.md (the MONITOR surface) · CALL_LOGIC.md (the brain) · FEED_LOOP.md (the rhythm)   # the back half
│   ├── CATALYST_CONVICTION.md · THEME_CONVICTION.md · PRODUCTION_TENANT.md · REPLAY.md
│   ├── RECALIBRATION.md   # the post-MVP tuning agenda (pass-001 record retired into ROADMAP's trust box)
│   └── mockups/ · PROJECT_LAYOUT.md (this file)   # the visual targets · the file map
├── frontend/                       # React + Vite + Tailwind + TanStack Query (SPA)
│   └── src/
│       ├── App.tsx · main.tsx · index.css        # the routing shell (a path per view; route wrappers translate URL ↔ page props) + the design tokens (inverse loudness)
│       ├── nav.ts                                # the URL scheme, pure: / · /scoreboard · /workbench · /thesis/:id, ?asof= + ?name= builders/guards
│       ├── api/{client,hooks,types.gen}.ts       # openapi-fetch client (baseUrl /api — proxy-stripped, the contract never carries it); the hooks; GENERATED wire types
│       ├── board/{Board,ThesisCard}.tsx          # the Board (lifecycle columns + the Decision Queue + the collapsed Archived section)
│       ├── cockpit/{Cockpit,NamePanel,SpineListEditors}.tsx · cockpit/buckets.ts   # the Cockpit (the grouped basket: collapsible per-name buckets) · the read-only per-name panel (call + own triggers + operator record) · the spine-list editors · the pure bucket derivation
│       ├── components/{CallCard,MemberMenu,DecisionActions,ErrorToast}.tsx   # the call card · the M5 per-member menu · decision capture (take/pass/close/void) · the shared error toast
│       ├── workbench/                             # the front half
│       │   ├── Workbench.tsx                      #   the page (NARRATIVE › DECOMPOSE › SCORE › PROMOTE) + the create/edit form (M1)
│       │   ├── ThesisFields.tsx                    #   M1: the name + narrative form (shared by create + narrative-edit)
│       │   ├── ChainEditor.tsx · useChainDraft.ts #   AUTHOR + the S5 DRAFT/RATIFY surface + the draft state machine
│       │   ├── AddName.tsx                        #   the resolver typeahead (exact-membership pick; CIK shown)
│       │   ├── ScoredRow.tsx · Meter.tsx          #   the four-meter scored row
│       │   ├── FactsPanel.tsx · DDRail.tsx        #   extract → ratify (hybrid) + the "behind the scores" rail
│       │   ├── CatalystFactForm.tsx               #   "+ log a catalyst" — the cited Key-1 conviction fact (the ratify union's catalyst variant)
│       │   └── format.ts                          #   archetype labels, error text
│       └── {test/setup.ts, **/__tests__/*}        # vitest (vi.mock the api/hooks boundary; real component logic)
└── backend/                        # Python: FastAPI + Pydantic + psycopg
    ├── pyproject.toml              # deps (incl. anthropic) + ruff/black/pytest cfg
    ├── Dockerfile                  # the FastAPI image (python:3.11-slim + tzdata, for the cron sidecar's explicit TZ)
    ├── scripts/daily_cron.sh       # M2: the cron sidecar's sleep-loop trigger (sleeps to US-close, fires pipeline.daily)
    ├── domain/                     # THE SPINE — Pydantic schemas (the backend↔frontend contract)
    │   ├── base.py                 #   DomainModel (extra="forbid")
    │   ├── enums.py                #   State/Verdict/Grade/Role/Kind · Archetype · Authorship (drafted/operator) · TermTier (signal/broad)
    │   ├── thesis.py               #   Thesis (+ term_set) · Segment · BasketMember (segment / authored_by / thesis_fit) · TermSetEntry
    │   ├── call.py · signal.py · security.py
    │   ├── extraction.py           #   ExtractedFact · Tier (AUTO/FLAG/HUMAN) · LocatedPassage
    │   ├── workbench.py            #   ScoredMember · ScoredFigure (the meter results)
    │   ├── config.py               #   CallConfig (the trust-validated call-engine dials) · ExtractorConfig
    │   ├── settings.py             #   typed Settings: env-overridable LLM dials + base URLs + throttle (ALPHADECK_*; config refactor)
    │   └── coerce.py               #   to_float — the shared scalar coercer (Tier-1 dedup)
    ├── llm/                        # THE LLM SEAMS (model-agnostic; fail-open; SDK lazy-imported)
    │   ├── client.py               #   LLMClient.draft_structured (forced tool-use) + research (web_search) + the allow_live gate
    │   ├── flag_explanation.py     #   seam 1 (Haiku): the FLAG-explanation drafter (an aid to a ratify)
    │   ├── chain_decomposition.py  #   seam 2 (Sonnet): ORGANIZE the discovered universe → segments + prose · narrate_placements (batched prose) · research_tail_sweep (Opus)
    │   ├── keyword_gen.py          #   discovery: narrative → candidate keywords (Haiku) — proposes, the term-set guard tiers
    │   ├── prompt_loader.py        #   loads the externalized system prompts (config refactor S3; fail-loud on a missing file)
    │   └── prompts/*.md            #   the seam prompts as files (flag_explain · chain_decompose · chain_narrate · keyword_gen · tail_sweep)
    ├── workbench/                  # the Workbench engines (pure)
    │   ├── scoring.py              #   score_member/score_thesis → the four pip meters (re-derived on read)
    │   ├── term_set.py             #   the discovery term-set producer: keyword-gen PROPOSES, the deterministic guard TIERS (seeds=SIGNAL)
    │   ├── discovery.py            #   run_discovery: read the stored term set → EFTS enumerate → classify → DiscoveredUniverse (DiscoveryNoTerms/Empty/Degraded → 503)
    │   ├── research_runner.py      #   the tail-sweep cost-safety wrapper (in-flight 409 guard + TTL cache)
    │   ├── chain_draft.py          #   resolve_discovered_chain: the per-CIK RECONCILER (PLACED/VERIFY by CIK + matched_terms; _resolve_one for off-universe names)
    │   ├── draft_jobs.py           #   the async draft-job registry (kick-off → poll; 409 in-flight guard; reaper; single-worker guard)
    │   └── draft_run_log.py        #   the DISCOVER run-of-record: one WRITE-ONLY JSON per completed draft job (data/draft_runs/; fail-open, never a read path)
    ├── notify/                     # the notify seam: TransitionEvent + Notifier protocol + LogNotifier (delivery = one adapter, deferred)
    ├── calls/                      # THE CALL-ASSEMBLER (the product) — pure + golden-tested
    │   └── assembler.py · grading.py · confidence.py · counter_case.py
    ├── signals/                    # detectors — pure f(point_in_time_data) -> SignalEvent | None
    │   ├── insider_conviction.py · volume_breakout.py · catalyst_conviction.py · theme_conviction.py
    │   └── dilution_clock.py · scan.py · base.py (PointInTimeData)
    ├── ingest/                     # data-ingestion bricks (cache-first; live behind allow_live; CacheMiss canonical in __init__.py)
    │   ├── http.py                                               # polite_get (429/5xx retry + Retry-After) + RateLimiter (the shared token-bucket; Tier-1)
    │   ├── edgar/{client,submissions,form4,converts,extract,fulltext}.py   # SEC client + Form 4 + converts + extractor + fulltext (the EFTS discovery enumerator: discover · classify · parallel under the shared RateLimiter)
    │   ├── doe/{client,entities,feed}.py                          # the USASpending/DOE automated catalyst feed
    │   ├── prices/{eod_loader,source,ingest_security}.py          # EOD bars (+ latest_bar_date, stored_bars, force_refresh) · the PriceSource seam (Yahoo/Stooq) · the ONE price leg (incremental tail + the RE-VERSION pass)
    │   └── {cash_burn,revenue_mix,shares,catalyst,theme_conviction}.py   # the ratify bridges (write fact_*)
    ├── securities/                 # entity resolution → the security master
    │   ├── master.py               #   search (discovery net) · resolve · ids_for_tickers / ids_for_ciks (exact membership) · populate_universe (broadener) · exists · get
    │   └── figi.py · sec_tickers.py
    ├── db/                         # bitemporal Postgres store
    │   ├── session.py · bitemporal.py (as_of / as_of_thesis / append_fact) · migrate.py
    │   └── migrations/0001…0021    #   …0009 scoring_facts · 0012 thesis_term_set · 0018 archetype_nullable · 0019 operator_decision · 0020 thesis_archived · 0021 thesis_exclusion
    ├── repositories/               # the row↔domain seam (raw rows never escape)
    │   └── mappers.py · thesis_repo.py (get/list_all/upsert + the sole writers: set_term_set/set_catalysts/set_kill_criteria/set_exclusions/set_archived — the structural wipe-guards) · calls_repo.py (append · latest_for_thesis · record_if_changed/_canonical) · decisions_repo.py (the operator-decisions log + the derived position)
    ├── pipeline/                   # thin orchestration / CLIs
    │   ├── call_for_thesis.py · run.py · seed.py · core.py
    │   ├── ingest_thesis.py        #   M2: per-thesis back-half ingest (Form 4 + EOD; incremental, fail-visible)
    │   ├── daily.py                #   the daily call-of-record cron (ingest → assemble → TRANSITION detection → record_if_changed; archived skipped)
    │   ├── populate_master.py      #   the SEC-universe broadener CLI
    │   ├── provision_tenant.py     #   cut a fresh tenant (production)
    │   └── ratify_*.py             #   operator-ratify CLIs (catalyst / cash_burn / revenue_mix / shares)
    ├── app/                        # FastAPI
    │   ├── main.py · deps.py        #   deps: get_conn · get_current_tenant · get_thesis_or_404 · get_llm_client · get_decompose_client · get_keyword_client · get_research_client · get_edgar_client
    │   ├── openapi_export.py        #   dumps backend/openapi.json (the frontend's type source)
    │   ├── routers/theses.py        #   GET /theses · /theses/{id} · /theses/{id}/call?asof=
    │   ├── routers/workbench.py     #   /workbench: scored · securities · extract · facts(+/explain) · theses(promote) · theses/{id}/terms (produce the term set) · theses/{id}/draft-chain (EDGAR-first)
    │   └── schemas_api.py           #   the WIRE contracts (ThesisDetail · WorkbenchScored · ChainDraftOut · …)
    ├── replay/                     # the backtest harness — DuckDB + Parquet, point-in-time (REPLAY.md)
    │   └── harness.py · episodes.py · pit.py · export.py · compare.py · metrics.py · scoring.py · run.py
    ├── seed_data/                  # committed REAL inputs (HIMS demo, DOE fixtures) — read by seed + tests
    └── tests/                      # 283 tests; DB-backed ones skip if Postgres is unreachable
        ├── conftest.py             #   db / security_id fixtures (db TRUNCATEs the spine + facts + master)
        ├── app/conftest.py         #   the shared `client` fixture (get_conn → db; clears overrides on teardown) — Tier-4
        ├── workbench/              #   test_scoring · test_extract_golden · test_chain_draft (the resolver/Oklo-trap)
        ├── llm/                    #   test_flag_explanation · test_chain_decomposition (fake client; no network)
        ├── app/test_workbench_api.py   # promote guard · ratify · explain · draft-chain (writes-nothing/fail-open)
        ├── db/test_tenant_isolation.py # the poison-row proof — grows with each new read surface
        ├── pipeline/test_ingest_thesis.py · test_daily.py  # M2: count-the-table idempotency · fail-visible · no-lookahead · tenant
        ├── ingest/test_price_source.py · test_http.py       # M2: the fresh-data force-refresh + the seam · polite_get (429/5xx)
        └── calls/ · signals/ · ingest/ · securities/ · repositories/ · pipeline/ · replay/ · app/
```

## Local-only (gitignored — present in the working dir, not in git)

- `.env` — the real secrets (`ANTHROPIC_API_KEY`, `ALPHADECK_USER_AGENT`, …). Copy from `.env.example`. Docker
  Compose injects it into the backend container; the local dev loop reads the same names from the shell.
- `backend/.venv/` — the project venv (stdlib venv + pip; the image pins Python 3.11, `requires-python >=3.11`).
- `data/` — on-disk caches of live pulls (`edgar_cache/`, `price_cache/`, `figi_cache/`, `sec_cache/`, `doe_cache/`).
- Local **Postgres** via Docker Compose (`localhost:5544`, the shared `pgdata` volume). The demo DB
  (`alphadeck`) holds the seed + the populated master; **tests run against `alphadeck_test`** (the `db`
  fixture truncates, so it must never point at the demo DB).
- `docs/temp/` — scratch (this file was promoted out of it).

## State — built vs. not

- **Built & merged (the whole loop):** the spine + call-assembler; bitemporal Postgres + the security master;
  EDGAR/Form-4 + the detectors + scan; Checkpoint A (computed Armed HIMS call); the catalyst subsystem + the
  DOE feed; M5 (per-member menu + theme arming); Phase 1 (replay harness + recalibration pass 001 + the
  production-tenant cut, isolation poison-row-proven); the Workbench — scoring (the four meters), authoring,
  the extract → ratify hybrid, the SEC-universe broadener, the two LLM seams (FLAG-explanation + the
  narrative→chain drafter, S5), the **create-thesis front door** (M1 — #67/#68), and the **M2 feed loop**
  (the per-thesis back-half ingest + the daily call-of-record cron + the price-source seam + the scheduling
  sidecar — #70/#71/#72/#73). **The front-half loop closes end to end AND the back half feeds itself**
  (`FEED_LOOP.md`) — the MVP.
- **Behavior-preserving refactor cycle (#75–#81):** config centralization (a typed `domain/settings.py` —
  env-overridable LLM dials + base URLs + throttle — and the LLM prompts externalized to `llm/prompts/*.md`),
  then the quick-win dedups (Tier 1 `coerce.to_float` / `RateLimiter` / `CacheMiss`; Tier 2 the
  `get_thesis_or_404` dependency + `_provenance_out`; Tier 3 the FE shared bits + as-of-defaults-to-today;
  Tier 4 the shared `client` test fixture). No behavior change; gated by the suite + the openapi↔types guard.
- **Not built yet:** the **live Scoreboard** (the forward trust loop — parked, arrives with live use → the
  second, out-of-sample recalibration); the **deferred restatement re-version** + the **source-strategy A/B
  decision** (keep Yahoo + re-version vs raw+splits + own-the-adjustment — `DATA_SOURCES.md` / `FEED_LOOP.md`);
  the **cron-scaling refinement** (ingest active theses daily, dormant less); Phase-3 breadth (laggard scanner,
  ETF radar, more catalyst sources, umbrella hierarchy, live LLM counter-case) — by appetite. See `ROADMAP.md`.

## Flags for the reviewer (current)

1. **Dials are STARTING calibration, not precision** — everything in `domain/config.py` (`CallConfig` /
   `ExtractorConfig`); pass 001 was in-sample (n=19), not forward-validated. `RECALIBRATION.md`.
2. **Trust is in-sample.** The replay harness validated the edge over history; the **live Scoreboard** (forward
   validation) is parked. Don't overclaim the calls until it lives with them.
3. **The Board is not tenant-scoped** (`thesis_repo.list_all` is all-tenants) — a display limitation, not a
   fact leak (per-call reads are isolated); deferred to the auth era. No RLS — isolation is discipline + the
   poison-row test (`PRODUCTION_TENANT.md`).
4. **The LLM bound rests partly on the prompt.** The chain drafter's "never a number" is structural (no value
   field) + prompt (Sonnet the lever); the **manual no-number check is its real test** — a fake-client unit
   test can't exercise a prompt. The regex post-filter is the deferred lever (`CHAIN_DRAFTER.md`).
5. **`GET /theses/{id}` returns the wire `ThesisDetail`** (no `tenant_id` on the wire); benign for
   single-tenant / deferred-auth.
6. **The openapi↔types contract is generated** — any FastAPI schema change (incl. a route docstring) must
   regenerate `backend/openapi.json` + `frontend/src/api/types.gen.ts` in the same PR (CI diff-guards both).
