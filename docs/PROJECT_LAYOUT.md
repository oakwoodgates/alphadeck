# PROJECT_LAYOUT.md — the repo, file by file

> Repo path: `docs/PROJECT_LAYOUT.md`. The anti-black-box **file map**: what each module is and where the
> behavior lives. Companion to `DATA_FLOW.md` (where data lives at runtime), `PROJECT_OVERVIEW.md` (the why),
> `ROADMAP.md` (sequencing), and `CLAUDE.md` (how to build). For the *why* of any subsystem, follow the doc
> links; this is the *where*.
>
> **As of the post-MVP breadth arc (`main` through #238 — MVP at #73; the refactor cycle #75–#81; the
> Scoreboard #158–#164; cron-freeze remediation #196–#203; test-DB isolation + Slices 1–4 #207–#217; the
> "dark"-name Retrieval slices #222–#226; the Scoreboard drill-down #227–#231; the dev/prod split #232; the
> ETF sleeve + net-flow #233–#237; the origin chip #238):** both halves are built, the **sixth stage (SCORE)
> shipped**, and the platform **feeds itself** (literally true only after #196 — the EDGAR cache froze insider
> data ~11 days until the key-classed 12h TTL; `POSTMORTEM_CRON_FREEZE_2026-07.md`). Back half — the bitemporal
> store, two-key arming, the pure call-assembler, the catalyst subsystem + the DOE feed, the M5 per-member menu
> + theme arming, the replay harness + recalibration + the production-tenant cut. Front half (the Workbench) —
> scoring, authoring, the extract → ratify hybrid, the SEC-universe broadener, **the two LLM seams**
> (FLAG-explanation + narrative→chain, S5), and the **create-thesis front door** (M1). The **M2 feed loop** —
> the per-thesis back-half ingest + the daily call-of-record cron + the price-source seam + the scheduling
> sidecar — makes it **feed itself** (`FEED_LOOP.md`). The **post-MVP breadth** adds the SCORE Scoreboard + its
> episode drill-down, the read-only display-signal tape, the **ETF sleeve** (a `fund` safe-exposure member with
> N-PORT holdings + a net-flow chip), the per-name **origin chip** (SURFACE identity), and the local **dev/prod
> split**. The front-half loop closes end to end (**narrative → draft → ratify → promote → extract → score**)
> and the back half feeds the promoted thesis its call-engine facts. Suite: **backend pytest** (DB-backed tests
> auto-derive a per-worktree DB — `db/testdb.py` — so they never touch the demo; they SKIP only when Postgres
> is unreachable) + **frontend vitest**; `ruff` + `black` + `tsc` + `vite build` clean; CI runs them + the
> openapi↔types drift guard on every PR.

## Tracked hierarchy

```
alphadeck/
├── CLAUDE.md                       # agent working agreements + invariants + the live vocabulary/commands
├── README.md                       # what it is, the stack table, v1 scope
├── docker-compose.yml              # full stack: Postgres + backend + SPA/nginx; + the `cron` sidecar (M2; ON by default, `--scale cron=0` to skip)
├── docker-compose.dev.yml          # the DEV override: base + `-p alphadeck_dev` (namespaced project · cron OFF · ports +1) — DEV_PROD.md
├── .env.example                    # env template → copy to .env (gitignored): ANTHROPIC_API_KEY, UA, POLYGON_API_KEY, ...
├── .github/workflows/ci.yml        # CI: backend ruff/black/pytest + openapi-diff · frontend tsc/build/vitest + types-diff
├── infra/docker-compose.yml        # DB-only slice for the local backend dev loop (shares the pgdata volume)
├── scripts/refresh-dev.sh          # ops: the ONE-WAY prod→dev data refresh (pg_dump READ; never writes prod) — DEV_PROD.md
├── docs/                           # THE CANON — read STAGE_MODEL.md first (the frame), then by stage
│   ├── STAGE_MODEL.md · PROJECT_OVERVIEW.md · ROADMAP.md · INVARIANTS.md · DATA_FLOW.md · DATA_SOURCES.md
│   ├── DISCOVERY.md · CHAIN_DRAFTER.md · WORKBENCH_EXTRACTION.md · WORKBENCH_ENRICHMENT.md · WORKBENCH_SCORING.md · TRIAGE.md   # the front half, in stage order
│   ├── BOARD.md (the MONITOR surface) · CALL_LOGIC.md (the brain) · FEED_LOOP.md (the rhythm) · ADMIN.md (the ops surface)   # the back half
│   ├── SCOREBOARD.md · DISPLAY_SIGNALS.md   # SCORE (the forward record) · the read-only tape indicators
│   ├── CATALYST_CONVICTION.md · THEME_CONVICTION.md · PRODUCTION_TENANT.md · REPLAY.md · DEV_PROD.md   # + the local dev/prod split
│   ├── RECALIBRATION.md · POSTMORTEM_CRON_FREEZE_2026-07.md   # the tuning agenda · the ~11-day cache-freeze postmortem
│   └── mockups/ · PROJECT_LAYOUT.md (this file)   # the visual targets · the file map
├── frontend/                       # React + Vite + Tailwind + TanStack Query (SPA)
│   └── src/
│       ├── App.tsx · main.tsx · index.css        # the routing shell (a path per view; route wrappers translate URL ↔ page props) + the design tokens (inverse loudness)
│       ├── nav.ts                                # the URL scheme, pure: / · /scoreboard · /workbench · /admin · /thesis/:id, ?asof= + ?name= builders/guards
│       ├── api/{client,hooks,types.gen}.ts       # openapi-fetch client (baseUrl /api — proxy-stripped, the contract never carries it); the hooks; GENERATED wire types
│       ├── board/{Board,ThesisCard}.tsx          # the Board (lifecycle columns + the Decision Queue + the collapsed Archived section)
│       ├── cockpit/                              # MONITOR — the per-name Cockpit
│       │   ├── Cockpit.tsx · buckets.ts          #   the grouped basket (collapsible per-name buckets) · the pure bucket derivation
│       │   ├── NamePanel.tsx · SpineListEditors.tsx   #   the read-only per-name panel (call + own triggers + operator record) · the spine-list editors
│       │   └── DisplaySignalsSection.tsx         #   the read-only display-signal chips (posture headline · dated flips · basis fine-print)
│       ├── scoreboard/                           # SCORE — the episode ledger + the drill-down drawer (SCOREBOARD.md)
│       │   ├── Scoreboard.tsx                    #   the page: live ledger + Summary/Timing toggle + operator-span rows + metrics strip + replay panel + the scorecard drawer
│       │   ├── EpisodeRow.tsx · LedgerHead.tsx · MetricsStrip.tsx · ReplayPanel.tsx   #   the ledger row/head (Summary|Timing) · the n-gated metric cards · the collapsible replayed-history panel
│       │   ├── EpisodeScorecard.tsx · EventLedger.tsx · PriceSparkline.tsx   #   the drawer: four timing lenses · the numbered event ledger · the lightweight-charts CLOSE+SMA sparkline w/ event chips
│       │   └── {ledger,overlay,rows,scorecard}.ts   #   the pure formatters (ledger rows/identity · chart-overlay numbering · row badges/tone · the four-lens phrasing)
│       ├── admin/Admin.tsx                       # the ADMIN ops surface (ADMIN.md): cron health · record freshness · run history · backups + the run-daily / snapshot triggers
│       ├── components/{CallCard,MemberMenu,DecisionActions,ErrorToast,Drawer}.tsx   # the call card · the M5 per-member menu · decision capture (take/pass/close/void) · the shared error toast · the reusable slide-out drawer
│       ├── util/{format,exportNames,useDebouncedCallback}.ts   # shared FE helpers: label/date formatters · the kept/segmented-names JSON export · the debounce hook
│       ├── workbench/                            # the front half
│       │   ├── Workbench.tsx                     #   the page (NARRATIVE › DECOMPOSE › SCORE › PROMOTE) + the create/edit form (M1)
│       │   ├── ThesisFields.tsx · AutoTextarea.tsx   #   M1: the name + narrative form (shared by create + narrative-edit) · the auto-growing textarea
│       │   ├── ChainEditor.tsx · useChainDraft.ts · DraftStatusStrip.tsx · RunPicker.tsx   #   AUTHOR + the S5 DRAFT/RATIFY surface + the draft state machine · the draft-honesty report · the saved-run loader
│       │   ├── AddName.tsx                       #   the resolver typeahead (exact-membership pick; CIK shown)
│       │   ├── SurfaceEtf.tsx · SleeveRail.tsx   #   the ETF sleeve: resolve+add a `fund` member · the fund dossier (flow chip · AUM · N-PORT holdings vs basket · include/remove)
│       │   ├── ScoredRow.tsx · Meter.tsx         #   the four-meter scored row
│       │   ├── FactsPanel.tsx · DDRail.tsx       #   extract → ratify (hybrid) + the "behind the scores" rail
│       │   ├── CatalystFactForm.tsx              #   "+ log a catalyst" — the cited Key-1 conviction fact (the ratify union's catalyst variant)
│       │   ├── junkTells.ts · triageSession.ts   #   TRIAGE: the low-quality-match predicates · the prune-session working-state blob (autosave)
│       │   └── format.ts                         #   archetype labels, error text
│       └── {test/setup.ts, **/__tests__/*}       # vitest (vi.mock the api/hooks boundary; real component logic)
└── backend/                        # Python: FastAPI + Pydantic + psycopg
    ├── pyproject.toml              # deps (incl. anthropic) + ruff/black/pytest cfg
    ├── Dockerfile                  # the FastAPI image (python:3.11-slim + tzdata, for the cron sidecar's explicit TZ)
    ├── scripts/daily_cron.sh       # M2: the cron sidecar's sleep-loop trigger (sleeps to US-close, fires pipeline.daily)
    ├── domain/                     # THE SPINE — Pydantic schemas (the backend↔frontend contract)
    │   ├── base.py                 #   DomainModel (extra="forbid")
    │   ├── enums.py                #   State/Verdict/Grade/Role/Kind · Archetype · Authorship (drafted/operator) · TermTier (signal/broad) · InstrumentKind (the ETF-sleeve instrument type)
    │   ├── thesis.py               #   Thesis (+ term_set) · Segment · BasketMember (segment / authored_by / thesis_fit) · TermSetEntry
    │   ├── call.py · signal.py · security.py   #   (Security carries the 0028 origin ingredients — raw locators, derived-on-read)
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
    │   ├── tier_recommendation.py  #   the tier-recommendation seam (invariant #10): proposes signal/broad for a term, display-only + advisory
    │   ├── purity_estimate.py      #   proposes an on-thesis revenue-purity % grounded ONLY in a located passage (a ratify aid, never a fact)
    │   ├── prompt_loader.py        #   loads the externalized system prompts (config refactor S3; fail-loud on a missing file)
    │   └── prompts/*.md            #   the seam prompts as files (flag_explain · chain_decompose · chain_narrate · keyword_gen · tail_sweep · purity_estimate · tier_recommend)
    ├── workbench/                  # the Workbench engines (pure)
    │   ├── scoring.py              #   score_member/score_thesis → the four pip meters (re-derived on read)
    │   ├── term_set.py             #   the discovery term-set producer: keyword-gen PROPOSES, the deterministic guard TIERS (seeds=SIGNAL)
    │   ├── discovery.py            #   run_discovery: read the stored term set → EFTS enumerate → classify → DiscoveredUniverse (DiscoveryNoTerms/Empty/Degraded → 503)
    │   ├── enrichment.py           #   lazily enrich master rows (sector/exchange/status) for discovered CIKs from EDGAR submissions (S1–S4)
    │   ├── research_runner.py      #   the tail-sweep cost-safety wrapper (in-flight 409 guard + TTL cache)
    │   ├── chain_draft.py          #   resolve_discovered_chain: the per-CIK RECONCILER (PLACED/VERIFY by CIK + matched_terms; _resolve_one for off-universe names)
    │   ├── etf_overlap.py          #   classify an ETF's N-PORT holdings vs the basket → held / available / unresolved (the sleeve DD)
    │   ├── draft_jobs.py           #   the async draft-job registry (kick-off → poll; 409 in-flight guard; reaper; single-worker guard)
    │   ├── draft_run_log.py        #   the DISCOVER run-of-record: one WRITE-ONLY JSON per completed draft job (data/draft_runs/; fail-open, never a read path)
    │   ├── run_loader.py           #   the read-only reader behind the RunPicker (list / read a saved draft run)
    │   └── triage_store.py         #   the TRIAGE prune-session store: one mutable JSON blob per thesis (resumable operator working state)
    ├── notify/                     # the notify seam: TransitionEvent + Notifier protocol + LogNotifier (delivery = one adapter, deferred)
    ├── calls/                      # THE CALL-ASSEMBLER (the product) — pure + golden-tested
    │   └── assembler.py · grading.py · confidence.py · counter_case.py
    ├── signals/                    # detectors — pure f(point_in_time_data) -> SignalEvent | None
    │   ├── insider_conviction.py · volume_breakout.py · catalyst_conviction.py · theme_conviction.py
    │   ├── dilution_clock.py · base.py (PointInTimeData) · common.py (fired_signal / provenance / entry-liveness) · registry.py (the #176 detector registry)
    │   └── display/                # READ-ONLY display signals — off the call path (own registry + protocol); DISPLAY_SIGNALS.md
    │       └── base.py · registry.py · sma.py · range52w.py · volume_regime.py · insider_flow.py · etf_flow.py
    ├── ingest/                     # data-ingestion bricks (cache-first; live behind allow_live; CacheMiss canonical in __init__.py)
    │   ├── http.py                                               # polite_get (429/5xx retry + Retry-After) + RateLimiter (the shared token-bucket; Tier-1)
    │   ├── edgar/{client,submissions,form4,converts,extract,fulltext}.py   # SEC client + Form 4 + converts + extractor + fulltext (the EFTS discovery enumerator: discover · classify · parallel under the shared RateLimiter)
    │   ├── edgar/{annual_shares,annual_runway,statement_sources}.py         # the "dark" FPI retrieval (FLAG-only): 20-F/40-F cover shares (+ADS ratio) · statement cash/runway · the primary-doc-vs-EX-99 seam
    │   ├── edgar/nport.py                                        # locate / fetch / parse one fund series' N-PORT holdings (the ETF sleeve)
    │   ├── funds/{source,polygon,snapshot_loader,ingest_security}.py        # the fund shares-out sampler: the source seam (Polygon primary when keyed → Global X / stockanalysis fallback) · the one incremental, re-versioning, no-lookahead leg
    │   ├── doe/{client,entities,feed}.py                          # the USASpending/DOE automated catalyst feed
    │   ├── prices/{eod_loader,source,ingest_security}.py          # EOD bars (+ latest_bar_date, stored_bars, force_refresh) · the PriceSource seam (Yahoo/Stooq) · the ONE price leg (incremental tail + the RE-VERSION pass)
    │   └── {cash_burn,revenue_mix,shares,catalyst,theme_conviction}.py   # the ratify bridges (write fact_*)
    ├── securities/                 # entity resolution → the security master
    │   ├── master.py               #   search (discovery net) · resolve · ids_for_tickers / ids_for_ciks (exact membership) · populate_universe (broadener) · enrich (identity + the 0028 origin ingredients) · exists · get
    │   ├── origin.py               #   the pure origin ladder: derive-on-read WHERE a name is from (biz country → city → incorporation → None) over the raw 0028 locators — SURFACE identity, TAGS never filters, off the call path
    │   ├── coherence.py            #   classify_members: does a row's SHOWN ticker agree with its BOUND security_id (the #172–#175 misbind audit)
    │   └── figi.py · sec_tickers.py · fund_tickers.py   #   OpenFIGI · the SEC ticker map · the ETF/fund ticker → trust CIK + series/class resolver
    ├── db/                         # bitemporal Postgres store
    │   ├── session.py · bitemporal.py (as_of / as_of_thesis / append_fact) · migrate.py
    │   ├── testdb.py · drop_test_dbs.py   #   the per-worktree test-DB derivation (fail-closed alphadeck_test* guard) + the stale-DB cleanup CLI (#217)
    │   └── migrations/0001…0028    #   …0012 thesis_term_set · 0019 operator_decision · 0021 thesis_exclusion · 0024 insider_issuer_identity · 0025 shares_ads_ratio · 0026 master_instrument_kind · 0027 fact_fund_shares · 0028 master_origin
    ├── repositories/               # the row↔domain seam (raw rows never escape)
    │   └── mappers.py · thesis_repo.py (get/list_all/upsert + the sole writers: set_term_set/set_catalysts/set_kill_criteria/set_exclusions/set_archived — the structural wipe-guards) · calls_repo.py (append · latest_for_thesis · record_if_changed/_canonical) · decisions_repo.py (the operator-decisions log + the derived position)
    ├── pipeline/                   # thin orchestration / CLIs
    │   ├── call_for_thesis.py · run.py · seed.py · core.py
    │   ├── ingest_thesis.py        #   M2: per-thesis back-half ingest (Form 4 + EOD; incremental, fail-visible)
    │   ├── daily.py · daily_job.py · cron_run_log.py · schedule.py   #   the daily call-of-record cron (ingest → assemble → TRANSITION → recording-GATE → record_if_changed) · the admin "run daily" job · the append-only run-of-record · the Mon–Fri/RUN_AT schedule math [#196–#200]
    │   ├── backup.py · backup_job.py   #   the rolling pg_dump snapshots (keep-N) + the admin "create snapshot" job (#215)
    │   ├── backfill_fund_shares.py     #   CLI: backfill historical ETF fund-shares from Polygon over a range (#237)
    │   ├── populate_master.py      #   the SEC-universe broadener CLI
    │   ├── audit_identity.py · repoint_canonical.py   #   the shown-vs-bound identity audit CLI + the one-time canonical re-point (#172–#175)
    │   ├── provision_tenant.py     #   cut a fresh tenant (production)
    │   └── ratify_*.py (+ ratify_common.py)   #   operator-ratify CLIs (catalyst / cash_burn / revenue_mix / shares) + the shared ticker→security_id resolver
    ├── app/                        # FastAPI
    │   ├── main.py · deps.py        #   deps: get_conn · get_current_tenant · get_thesis_or_404 · get_llm_client · get_decompose_client · get_keyword_client · get_research_client · get_edgar_client
    │   ├── health.py                #   the /health payload + the SEC-User-Agent boot-visibility guard (dev/prod split, #232)
    │   ├── openapi_export.py        #   dumps backend/openapi.json (the frontend's type source)
    │   ├── routers/theses.py        #   GET /theses · /theses/{id} · /theses/{id}/call?asof=
    │   ├── routers/workbench.py     #   /workbench: scored · securities · extract · facts(+/explain) · theses(promote) · theses/{id}/terms · theses/{id}/draft-chain (EDGAR-first) · the ETF sleeve (resolve-etf · etf-holdings)
    │   ├── routers/scoreboard.py    #   GET /scoreboard?asof= (the forward record + the staleness line) · /scoreboard/replay (the historical panel artifact) · /scoreboard/price-window (the drill-down chart's on-demand OHLCV + SMA + insider-buy overlay)
    │   ├── routers/admin.py         #   the ops surface (ADMIN.md): /admin/status · /runs · /run-daily(+jobs) · /backup(+jobs) · /backups — reads own no tables
    │   └── schemas_api.py           #   the WIRE contracts (ThesisDetail · WorkbenchScored · ChainDraftOut · ScoreboardResponse · EtfHoldingsOut · …)
    ├── scoreboard/                 # THE SCORE STAGE (SCOREBOARD.md) — record-not-recompute, censoring/maturity-gated
    │   ├── record.py · schema.py   #   derive each thesis's scored record (episodes + outcomes) from the calls-of-record · the analytical models
    │   ├── assemble.py · run.py    #   assemble the full Scoreboard (record walk + n-gated aggregate metrics) · the SB1 terminal-render CLI
    │   ├── decisions.py            #   join the operator decision log → priced take/pass spans (the operator track)
    │   ├── prices.py · overlays.py #   the asof/known_at-capped realized-EOD reader (no-lookahead) · the drawer-chart overlays (rolling SMA + windowed insider buys)
    │   ├── provenance.py           #   the per-episode ingest-honesty flags (freeze-era, thaw lag)
    │   └── artifact.py · replay_snapshot.py   #   write / read the replay-panel JSON snapshot + the CLI that builds it
    ├── replay/                     # the backtest harness — DuckDB + Parquet, point-in-time (REPLAY.md)
    │   └── harness.py · episodes.py · pit.py · export.py · compare.py · metrics.py · scoring.py · schema.py · run.py
    ├── seed_data/                  # committed REAL inputs (HIMS demo, DOE fixtures) — read by seed + tests
    └── tests/                      # 1,100+ tests; DB-backed ones skip if Postgres is unreachable
        ├── conftest.py             #   db / security_id fixtures (db TRUNCATEs the spine + facts + master)
        ├── app/conftest.py         #   the shared `client` fixture (get_conn → db; clears overrides on teardown) — Tier-4
        ├── workbench/              #   test_scoring · test_extract_golden · test_chain_draft (the resolver/Oklo-trap)
        ├── llm/                    #   test_flag_explanation · test_chain_decomposition (fake client; no network)
        ├── app/test_workbench_api.py   # promote guard · ratify · explain · draft-chain (writes-nothing/fail-open)
        ├── db/test_tenant_isolation.py # the poison-row proof — grows with each new read surface
        ├── pipeline/test_ingest_thesis.py · test_daily.py  # M2: count-the-table idempotency · fail-visible · no-lookahead · tenant
        ├── ingest/test_price_source.py · test_http.py       # M2: the fresh-data force-refresh + the seam · polite_get (429/5xx)
        └── scoreboard/ · calls/ · signals/ · ingest/ · securities/ · repositories/ · pipeline/ · replay/ · app/
```

## Local-only (gitignored — present in the working dir, not in git)

- `.env` — the real secrets (`ANTHROPIC_API_KEY`, `ALPHADECK_USER_AGENT`, `POLYGON_API_KEY`, …). Copy from
  `.env.example`. Docker Compose injects it into the backend container; the local dev loop reads the same names
  from the shell. `.env.dev` is the dev-stack twin (also gitignored) — see `DEV_PROD.md`.
- `backend/.venv/` — the project venv (stdlib venv + pip; the image pins Python 3.11, `requires-python >=3.11`).
- `data/` — on-disk caches of live pulls (`edgar_cache/`, `price_cache/`, `figi_cache/`, `sec_cache/`,
  `doe_cache/`) + the write-only DISCOVER run logs (`draft_runs/`) + the rolling DB snapshots (`backups/`, #215).
- Local **Postgres** via Docker Compose (`localhost:5544`, the shared `pgdata` volume). The demo DB
  (`alphadeck`) holds the seed + the populated master; **tests auto-derive a per-worktree
  `alphadeck_test_<hash>`** (a `pytest_configure` hook in `backend/db/testdb.py`) — the `db` fixture
  truncates, and the demo is unreachable from the suite **by construction** (a fail-closed guard refuses any
  non-`alphadeck_test` name). Just run `pytest`; `python -m db.drop_test_dbs` cleans up stale ones.
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
- **Post-MVP — SCORE + the honesty / ops / breadth arc (#158–#238):** the **Scoreboard v1** (the forward-record
  episode ledger + the operator track + n-gated aggregate metrics + replay-history-alongside — #158–#164) and
  its **episode drill-down** (the drawer, four timing lenses, the Summary|Timing toggle, the price sparkline +
  the on-demand `price-window` endpoint — #227–#231, `SCOREBOARD.md`); the read-only **display-signal tape**
  (SMA / 52-week / volume / insider-flow / ETF-flow context — #192–#206 + #237, `DISPLAY_SIGNALS.md`); the
  **cron-freeze remediation** (#196–#203, `POSTMORTEM_CRON_FREEZE_2026-07.md`); the **Slices 1–4 + Board fixes +
  test-DB isolation** batch (#207–#217) — the admin **ops surface** (`ADMIN.md`), clock honesty, the insider
  open-market + issuer-self screens, Scoreboard record-provenance + maturity, the **DB-snapshot button + nightly
  backup**, and the per-worktree test-DB fix; the **"dark"-name Retrieval slices** (honest current shares +
  IFRS cash/runway from the 20-F/40-F annual filings, FLAG-only, + the statement-source seam + the ADS ratio —
  #222–#226); the local **dev/prod Docker split** + the `/health` boot-visibility guard (#232, `DEV_PROD.md`);
  the **ETF sleeve** (the instrument-kind foundation + a `fund` safe-exposure member with N-PORT holdings /
  basket overlap + fund internals — #233–#236) and its **net-flow** display signal (the shares-outstanding
  sampler → `etf_flow`, Polygon-primary + backfill — #237); and the per-name **origin chip** (derive-on-read
  SURFACE identity over the raw 0028 locators — #238).
- **Not built yet:** the record's **forward validation** (the Scoreboard now tracks the record, which began
  2026-07-10 and is still accruing — the aggregate metrics stay honestly empty until clean-data arms mature,
  #214) → the second, out-of-sample recalibration; the **restatement re-version** + the **source-strategy A/B
  decision** (keep Yahoo + re-version vs raw+splits + own-the-adjustment — `DATA_SOURCES.md` / `FEED_LOOP.md`);
  **cron-scaling** (active theses daily, dormant less) + **cron-ops hardening** (a durable `market_today()`,
  the R4 0-fetch false-positive, a dead-man's-switch); **2f "the real WHY"** + the deferred
  **replay-regenerate button**; **insider Class B** + the `insider_flow` sell-side ceiling; Phase-3 breadth
  (laggard scanner, ETF radar *coming-launches* N-1A/485, more catalyst sources, umbrella hierarchy, live LLM
  counter-case) — by appetite. See `ROADMAP.md`.

## Flags for the reviewer (current)

1. **Dials are STARTING calibration, not precision** — everything in `domain/config.py` (`CallConfig` /
   `ExtractorConfig`); pass 001 was in-sample (n=19), not forward-validated. `RECALIBRATION.md`.
2. **Trust is in-sample.** The replay harness validated the edge over history; the **live Scoreboard is BUILT
   and now tracks the forward record** (#158–#164), but that record **began 2026-07-10 and is still accruing**
   (freeze-touched → the aggregate metrics are honestly empty until the first clean-data arm matures, #214).
   Forward VALIDATION is the open item — don't overclaim the calls until the record lives with them.
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
