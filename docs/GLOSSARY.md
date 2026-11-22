# Glossary

Each domain term: what it means, its owning module, and the doc with the rules. Colliding terms are in
[Naming guards](#naming-guards).

## The call

- **Thesis**: a user's narrative with its basket, catalysts and kill criteria; a card lives in one workspace.
  `domain/thesis.py`; [INVARIANTS](INVARIANTS.md#2-every-feature-attaches-to-a-thesis).
- **Basket, link**: the companies a thesis follows (`basket_member` rows); a link is a value-chain stage
  (`segment`). A company in N links is N rows. [WORKBENCH](WORKBENCH.md#arranging-the-value-chain).
- **Lifecycle state**: Incubating, Warming, Armed, Managing, a loop. Managing means the user logged a position; it is
  monitoring, not risk management. `domain/enums.py`;
  [CALL_LOGIC](CALL_LOGIC.md#2-state-transition-rules).
- **Signal event, detector, keys**: a detector is a pure function that emits signal events (a `role`, a `kind`, a
  grade). Only an entry trigger turns Key 1 (conviction) or Key 2 (confirmation); Armed needs both on one company.
  `domain/signal.py`; [CALL_LOGIC](CALL_LOGIC.md#1-inputs), [DETECTORS](DETECTORS.md).
- **Grade**: `flip` (fast, attention-driven) or `core` (structural); call strength, never a size. `calls/grading.py`;
  [CALL_LOGIC](CALL_LOGIC.md#3-grade-decision).
- **Verdict**: the call's readiness word (`watching` to `core_entry`); never a position size.
  `calls/assembler.py`; [CALL_LOGIC](CALL_LOGIC.md#4-verdict-mapping).
- **Call card**: one thesis's call on one night: state, verdict, grades, triggers, `exit_by` and a deterministic
  counter-case no model writes. `domain/call.py`, `calls/counter_case.py`; [BOARD](BOARD.md#the-call-card).
- **Setup strength**: the product name of wire field `confidence`; a relative read, not a probability.
  `calls/confidence.py`; [CALL_LOGIC](CALL_LOGIC.md#7-setup-strength).
- **`exit_by`, catalyst surface**: `exit_by` is the signal-validity horizon, never a sell date; `arm_until` is the
  entry window; the catalyst surface is the dated catalysts on or before `exit_by`.
  [CALL_LOGIC](CALL_LOGIC.md#6-signal-validity-horizon-exit_by-and-clocks).
- **Counting catalyst**: a catalyst-list entry is a calendar item until the user turns on "counts toward the call";
  it counts from the night saved. `signals/thesis_catalyst.py`;
  [CALL_LOGIC](CALL_LOGIC.md#3c-thesis-catalysts-a-users-catalyst-on-the-card).
- **Silent links, meta index card**: Context, ETFs and Bench are three fixed last links whose names are visible and
  never vote; a `thesis.meta_index` card has no voting names. `domain/thesis.py`;
  [CALL_LOGIC](CALL_LOGIC.md#1-inputs).
- **Display signal**: a read-only indicator beside the call; never an input, never recorded. `signals/display/`;
  [DISPLAY_SIGNALS](DISPLAY_SIGNALS.md).
- **Decision**: the user's logged take, pass, close or void; append-only, nothing is routed.
  `repositories/decisions_repo.py`; [BOARD](BOARD.md#decision-capture).
- **Call of record**: the immutable `calls` log, a row per changed card per night; never the read path.
  `repositories/calls_repo.py`; [FEED_LOOP](FEED_LOOP.md#recording-the-call).
- **Dial, rule log**: a dial is a `CallConfig` value; a change that can move a recorded card appends a `RuleEntry`.
  `domain/rule_log.py`; [CALL_LOGIC](CALL_LOGIC.md#changing-a-rule-or-a-dial).

## Time and data

- **Public clock**: a fact counts only from when it became public (SEC acceptance, a settled bar). A night runs from
  a session's close to EDGAR's next open. Facts append versions; `recorded_at` is bookkeeping, not the history gate.
  `db/public_clock.py`; [INVARIANTS](INVARIANTS.md#1-no-lookahead-history-is-judged-on-the-public-clock).
- **Held insider row**: a Form 4 row with an impossible date; `resolve_holds` counts it from a filer's 4/A, a steward's
  decision or, if filed on time, its filing night. `domain/insider_holds.py`;
  [FILINGS](FILINGS.md#held-rows-a-date-that-cannot-be-true).
- **Market-cap tier**: one of five fixed bands at the event; a slicing dimension, never a rank. `domain/market_cap.py`;
  [BACKTEST](BACKTEST.md#sliced-by-market-cap).
- **Business type**: a display identity from SIC through editable maps; the steward can re-tag it; never a call
  input. `securities/business_type/`; [README](../backend/securities/business_type/README.md).

## People and tenancy

- **User, steward, operator**: a user is a plain user with their own workspace; the steward is the `super_admin` who
  curates platform data; the operator is only the person who runs the deployment. [AUTH](AUTH.md).
- **Workspace, data tenant**: a workspace is one user's private tenant (cards, calls, decisions, purity); the data
  tenant is the one shared tenant (company master, ingested facts, platform values). `domain/scopes.py`;
  [TENANT_SCOPES](TENANT_SCOPES.md#the-tenants).
- **Workspace caps**: three limits (live cards, companies, terms per card); refused only when a request adds past
  one. `app/workspace_caps.py`; [AUTH](AUTH.md#workspace-caps).

## Platform values and extraction

- **Platform value**: a company's share count or cash/burn, one shared value; the machine writes clean ones, the
  steward decides the rest, a user only reads. Never a call input. `platform_values/`;
  [PLATFORM_VALUES](PLATFORM_VALUES.md).
- **Value flag**: a user's report that a platform value is wrong or missing; it tells the steward and never changes
  the value. `repositories/value_flag_repo.py`; [VALUE_FLAGS](VALUE_FLAGS.md).
- **Extractor tiers, located passage, purity**: AUTO (a clean value), FLAG (risks observed, a value or none) and HUMAN
  (located only; purity, the user's own judgment). A passage is an excerpt found by fixed retrieval. The extractor
  locates; a person ratifies. `ingest/edgar/extract.py`; [EXTRACTOR](EXTRACTOR.md#the-three-tiers).
- **Runway not applicable**: runway reads n/a for banks, insurers, funds and similar firms; cash and burn are still
  stored.
  `securities.business_type.runway_applies`; [PLATFORM_VALUES](PLATFORM_VALUES.md#runway-does-not-apply).
- **Explain seam**: the steward's drafter that explains a FLAG candidate; never a fact. `llm/flag_explanation.py`;
  [EXTRACTOR](EXTRACTOR.md#the-explain-seam).

## Workbench and discovery

- **Workbench, cheap cut, save**: the Workbench is where a user drafts, cuts and signs off a basket; the cheap cut is
  the free first cut; save (promote) fully replaces the basket. [WORKBENCH](WORKBENCH.md#the-save-contract).
- **Term set, EDGAR-first discovery**: names are found by EDGAR full-text search over a card's terms, never model
  recall; a SIGNAL term (the user's seed) places a company, a BROAD term only corroborates.
  `ingest/edgar/fulltext.py`; [DISCOVERY](DISCOVERY.md#the-term-set).
- **Chain drafter**: a model arranges discovered names into links and writes each name's `thesis_fit`, why it sits in
  its link; it sources no number. `llm/chain_decomposition.py`;
  [DISCOVERY](DISCOVERY.md#the-organizer-and-the-reconciler).
- **Recommendation, sign-off, authorship**: a recommendation is a visible, pending model suggestion that changes
  nothing until a person confirms; sign-off (`signed_off`) endorses a company; authorship (`authored_by`) records who
  wrote its description. [INVARIANTS](INVARIANTS.md#10-the-model-recommends-a-person-decides),
  [WORKBENCH](WORKBENCH.md#sign-off-and-authorship).
- **Context role**: a per-company, per-card label (payer, benchmark); display only. `domain/enums.py`;
  [WORKBENCH](WORKBENCH.md#context-roles).
- **Recall**: discovery over-includes; no real name may drop silently. Skill
  [recall-rescore](../.claude/skills/recall-rescore/SKILL.md); [INVARIANTS](INVARIANTS.md#9-recall-is-sacred).
- **Cold name**: a company never loaded; its history loads in the onboarding job, outside the nightly.
  `pipeline/onboarding.py`; [INGEST](INGEST.md#cold-names-onboarding).

## The nightly, the record and research

- **The nightly**: `pipeline.daily` after each session's close refreshes shared facts and records each call; the cron
  sidecar fires it. [FEED_LOOP](FEED_LOOP.md), [CRON_SIDECAR](CRON_SIDECAR.md).
- **Fund radar, SPAC radar**: scans of EDGAR for new fund filings and SPAC events; neither feeds a call. `radar/`;
  [FUND_RADAR](FUND_RADAR.md).
- **Scoreboard**: scores the platform's calls against realized prices, beside the user's decisions. `scoreboard/`;
  [SCOREBOARD](SCOREBOARD.md).
- **Arm episode, edition**: an arm episode is a run in which one member is armed, the scoring unit; an edition is a
  frozen re-run of today's rules over a workspace's history; Edition 0 is the record. `replay/episodes.py`,
  `editions/`; [REPLAY](REPLAY.md#the-arm-episode), [EDITIONS](EDITIONS.md#what-an-edition-is).
- **Reconstructed, late row**: a rebuilt missed night is reported, never scored; a late row defines no episode by
  default. [SCOREBOARD](SCOREBOARD.md#reconstructed-rows).
- **n >= 5 gate**: a metric under five samples is withheld; a display safeguard, not evidence.
  [SCOREBOARD](SCOREBOARD.md#setup-strength-and-the-n--5-gate).
- **Replay, mirror, backtest**: replay re-runs the call pipeline over a Parquet mirror of the facts; a backtest scores
  such a run's episodes and ranks no thesis. `replay/`, `backtest/`; [REPLAY](REPLAY.md), [BACKTEST](BACKTEST.md).
- **Dashboard, Board, Cockpit**: a dashboard is a display-only page over many cards; the Board is the home dashboard,
  the Cockpit one card. [DASHBOARDS](DASHBOARDS.md), [BOARD](BOARD.md).
- **Stage**: SEED, DISCOVER, SURFACE, TRIAGE, MONITOR, SCORE, each a recommend-then-confirm handoff.
  [README](../README.md).

## Naming guards

| Term | Means | Never |
|---|---|---|
| workspace | a user's tenant | a basket's `segment` (a link) |
| V2 | the SaaS release | a feature's version; say "not built" |
| operator | the person who runs the deployment | a plain user; `operator_edited`-style identifiers name the user's acts |
| conviction | signal conviction, Key 1 in `calls/` | `BasketMember.conviction`: a stored weight the call never reads |
| role | `SignalEvent.role` | `BasketMember.role` (prose), `context_role`, the user's role |
| flag | a value flag | the extractor's FLAG tier, a candidate's `outcome='flag'`, a data flag |
| gate | the call card's advisory gate | the sign-in gate, the n >= 5 gate, the fact-writer gate |
| watch | `CallCard.watch_members`, the confirmation-only members | a silent link or a meta index card |
| `DEFAULT_TENANT_ID` | the tests' and seed's id, equal to `DATA_TENANT_ID` | a workspace; demo cards go to `FOUNDING_WORKSPACE_ID` |
| Massive.com | the vendor's name | a reason to rename `polygon_*` identifiers |
