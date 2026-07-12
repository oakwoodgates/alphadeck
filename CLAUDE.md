# CLAUDE.md

Working agreements for AI agents (and humans) operating in this repo. Read this before writing code.
This file is about **how to build Alpha Deck correctly**; the *what* and *why* live in `README.md` and `docs/PROJECT_OVERVIEW.md`.
**The organizing frame** — the platform as a pipeline of stages (SEED → DISCOVER → SURFACE → TRIAGE → MONITOR → SCORE), each a recommend→confirm trust handoff — is `docs/STAGE_MODEL.md`; frame new work by **stage**. SURFACE = `docs/WORKBENCH_EXTRACTION.md` (facts) + `docs/WORKBENCH_ENRICHMENT.md` (identity); TRIAGE = `docs/TRIAGE.md`; MONITOR = `docs/BOARD.md` (the surface) + `docs/CALL_LOGIC.md` (the brain) + `docs/FEED_LOOP.md` (the rhythm).

---

## Mission

Alpha Deck helps a discretionary trader turn early narrative convictions into well-timed trades. It is **decision-support, not execution**. Its job is to *preserve the operator's edge* (early narrative-spotting) and *patch the flaw* (timing, name selection). Be **opinionated about timing, deferential about thesis**.

**Product boundary:** Alpha Deck ends at the research-and-monitoring handoff. The firm's existing OMS,
execution, position-sizing, and portfolio-risk systems own orders, allocation, and risk controls. Alpha Deck
does not replace or write to them; it records operator decisions and monitors the thesis after an entry.

## Project stage

Solo, greenfield, validating whether the core loop is useful for one trader. Right-size everything to that:
- Prefer the **simplest thing that proves the loop** over the most complete or scalable thing.
- The invariants below are non-negotiable; **process ceremony is not.** Defer parallel worktrees, heavy CI, full bitemporal-correction semantics, orchestration, and similar unless they earn their keep *this pass*.
- Fewer milestones with a faster path to a runnable core loop beats a comprehensive milestone tree.

## North star — what "done" means

Success for a build pass is a **working product loop, not completed milestones.** A pass is done when a *single real thesis* flows end-to-end and produces a real, opinionated, evidence-linked **Armed call** the operator would act on: computed signals → state/verdict/grade/exit-by → the Armed CallCard with provenance links, visible in Board + Cockpit, on **real (not static) data**.

Plumbing that never reaches a computed Armed call is not the goal. Build the foundation *in service of reaching that loop*, not as a destination. A plan that defers the computed call to "later" has the priorities backwards.

## The call logic is the product

How `SignalEvent`s compose into lifecycle state, verdict, grade, expression, and exit-by is the platform's brain — the "make the call and show its work" promise. It is **not** an emergent recompute. Make it a named, first-class, golden-tested component, and keep its rules in `docs/CALL_LOGIC.md`. Where those rules aren't specified yet, **propose them for the operator's sign-off before building** — this is where the operator's edge lives, not yours.

## Propose, don't assume

When planning or designing, separate **what these docs specify** from **what you are proposing**. Surface assumptions and unresolved choices as open questions for the operator to confirm — do not label your own choices as decisions already made or "locked with the user." A plan built on unconfirmed guesses, however polished, is worse than a shorter one that names what it still needs to know.

## Non-negotiable invariants

Do not violate these. If a request seems to require violating one, stop and flag it.

1. **No lookahead, ever.** Every historical read is *as-of* a timestamp. Signals and backtests may only use data that was knowable at the simulated point in time. The data layer is bitemporal (valid-time + transaction-time); honor it. A backtest that touches future data is a bug, not a feature.
2. **The thesis is the spine.** Features attach to the thesis object (narrative, basket, evidence, catalysts, signals, kill criteria, expression). Do not build orphan screeners or standalone tools that float free of a thesis.
3. **The LLM augments; it never sources signals.** Triggers fire from deterministic code against data. The model reads filings, drafts DD, and writes call *explanations* — it never invents a trigger, fires a call, or is the authority for a number. Every trigger on a call card must trace to a computation.
4. **Opinionated on timing, deferential on thesis.** The system grades the call's strength and times the signal window. Any expression text is advisory research context, never a position-size, instrument-selection, or order instruction. It does **not** judge whether a trade idea is good. Don't add features that rank or veto theses on conviction.
5. **Advisory only (v1).** No order routing, no brokerage writes, no execution, sizing, or portfolio-risk management of any kind. The "gate" *withholds the platform's go-signal and logs overrides* — it never blocks the user and never places or manages a trade. The user logs their own fills; the firm's OMS / execution / risk stack owns the trade.
6. **Explainability is a feature.** Every call, score, and signal carries its evidence and provenance (source filing/data + the computation). No black-box outputs. If you can't show the work, don't surface the result.
7. **Inverse loudness.** Quietness scales with how early it is. Incubating must not nag (no alerts, low visual energy); loudness is reserved for Armed. This applies to notifications, not just CSS.

## Workbench interaction principles

These are UX/interaction rules for the operator-facing editor. They are DISTINCT from the
non-negotiable invariants above: invariants protect *trust* (the system must never lie, drop, or
auto-decide — violating one corrupts a decision). These protect *workflow* (the editor must be
reversible, honest, and non-vanishing — violating one is friction, not corruption). Backend code
checks against the invariants; frontend work checks against these.

**1. Reversibility — every operator action has a visible inverse.**
Every state change the operator makes in the Workbench (accept, add, place/segment, include, exclude)
must have a visible way back. No action strands the operator in a state they can't leave without a
re-roll (which costs API credits) or a full restart. Reversibility is achieved by returning to the
*prior* state, never by destroying data. Concretely: accept ⇄ un-accept, add (To-Review → Placed) ⇄
send-back (Placed → To-Review), include ⇄ exclude. If a new action can't be undone, it isn't done.

**2. Keep it visible — pruning hides, it never vanishes.**
Excluding, skipping, or de-selecting a name greys it and keeps it on screen, reversible in one click —
it does not delete the row. The operator is judging a universe; they need to *see* what they set aside,
not have it disappear. (This is the same instinct as invariant #9 "recall is sacred," applied to the
UI: #9 keeps the system from dropping names; this keeps the *interface* from dropping them.) A true
hide-from-view is only ever an explicit, reversible filter — never the default, and never a delete.

**3. Honest loudness — loudness marks the exception, not the rule.**
A badge or flag true of *every* row carries no information and is noise; loudness is reserved for the
minority that needs action. Surface the rare keeper, quietly collapse the common noise (and vice-versa
per bucket). A control that doesn't yet discriminate shouldn't render. (This is the UI reading of
invariant #7, inverse-loudness.)

**The test for new editor work:** does this action have an inverse (1), does it hide rather than
destroy (2), and does its loudness mark the exception rather than the rule (3)? If a change fails one
of these, it's friction — fix it before shipping, the same way a backend change that fails an invariant
is a bug before it ships.

## Domain vocabulary

Use these terms precisely; they are the ubiquitous language of the codebase.

- **Thesis** — first-class object: `narrative`, `basket`, `evidence[]`, `catalysts[]`, `signals[]`, `kill_criteria[]`, `expression`/`position`.
- **Lifecycle states** (a loop, not a one-way ratchet): `Incubating → Warming → Armed → Managing`. **Managing** means the operator has entered a position and Alpha Deck is monitoring the thesis; it is not portfolio or position risk management.
- **Signal taxonomy** (three orthogonal fields): **`role`** = `entry_trigger | risk_signal` (only entry triggers turn the keys; risk signals feed the counter-case / setup strength — wire field `confidence` — and can soft-veto *timing*); **`kind`** = what produced it (`insider | technical_breakout | laggard | squeeze | etf_launch | etf_flow | dilution_risk | …`); **`type`** = catalyst nature where one applies (`regulatory | promoter_attention | clinical_readout | personnel | …`, optional).
- **Trigger grade**: the categorical **call-strength class** on an entry trigger: `flip` (fast, sentiment/attention-driven setup) vs `core` (structural, more durable setup). Grade never determines position size, instrument, or expression.
- **Archetype** (basket member role): `leader | high_beta | lotto | shovel | adjacent | fund` (`adjacent` = off-thesis/impure exposure, surfaced and *flagged* by the Workbench; `fund` = the ETF safe-exposure sleeve).
- **Conviction — two unrelated meanings, they must NEVER cross.** (1) **Operator conviction** = `BasketMember.conviction`, the operator's per-name 1–5 **size weight** (stored metadata, TRIAGE-authored; NULL = unset, never 0) — it never touches the call. (2) **Signal conviction** = the deterministic call machinery in `calls/` (`conviction_kinds` / `conviction_grade` / `key_conviction` — triggers that warm/arm). Wiring operator conviction into the call is a **#4 violation** (opinionated on timing, deferential on thesis).
- **Alpha half-life** — how long a signal's edge is expected to remain relevant; sets the `exit_by` **signal-validity horizon**, not a mandatory trade exit.
- **Catalyst surface** — the binary events that fall within the thesis's `exit_by` signal-validity window.
- **Call card** — `{ verdict, grade, expression, exit_by, triggers_fired[] (with sources), missing[], counter_case, confidence, actions }`; the product label for wire field `confidence` is **setup strength**, an experimental relative indicator, not a probability.
- **The gate** — advisory friction: withhold the go-signal + readiness scorecard + logged override.
- **ETF radar** — per-theme ETF intelligence: *availability* (which ETFs express the theme), *coming launches* (SEC N-1A/485 — a new thematic launch is an emergence-kind signal), and *holdings/flows* (free universe seed + positioning signal). An ETF is also a low-torque **expression** of a thesis (the safe-exposure sleeve), always surfaced with fund internals (holdings, weights, expense ratio, AUM, liquidity).
- **Scoring-fact tiers** (the Workbench extractor) — `AUTO` (companyfacts reproduces the value → pre-fill, confirm-and-go) · `FLAG` (raw value + a detected risk + a **located passage** → the operator ratifies the composition) · `HUMAN` (purity — located only, **never auto-valued**). The extractor **LOCATES; the operator RATIFIES** (invariant #3). See `docs/WORKBENCH_EXTRACTION.md`.
- **Located passage** — a deterministically-retrieved filing excerpt (keyword/section match, never a model's reading), shown inline as the evidence behind a FLAG/HUMAN candidate.
- **Authorship** — `operator_set` / `operator_edited` / `system_drafted`. The **draft → ratify transitions**: a drafted placement **loads** `system_drafted`, the operator **accepts** it → `operator_set` or **edits** any field → `operator_edited`; promote **honors** the authorship (never coerces it). Nothing drafted becomes a fact until the operator ratifies it.
- **Recommendation** — a visible, **pending** LLM suggestion (a tier, a flag, an archetype) that **changes
  nothing until the operator confirms**; on confirm it becomes `operator_edited` (operator authority). The
  LLM recommends; the operator decides. A recommendation rides display-only (like the `matched_terms` tags),
  never auto-applied. See INVARIANTS.md #10.
- **The explain seam** — the Workbench's flag-explanation drafter (`backend/llm`, the FIRST LLM seam): a grounded plain-English **aid** to a FLAG ratify — an explanation, never a fact, on a rail that can't write one.
- **The chain drafter / the SECOND LLM seam** (`backend/llm/chain_decomposition.py`) — the narrative→chain front door: Sonnet ORGANIZES the EDGAR-discovered universe (never enumerates from recall) into segments + `system_drafted` thesis-fit prose, to ratify. **Response-only, fail-open, never sources a number.** See `docs/CHAIN_DRAFTER.md` + `docs/DISCOVERY.md`.
- **EDGAR-first discovery + the term set** — how names are FOUND: the thesis owns a persisted tiered term set (**SIGNAL = operator seeds** — a hit PLACES alone; **BROAD = keyword-gen** — corroboration-only, never auto-promoted) → deterministic EFTS enumeration by CIK → PLACED/VERIFY classify → the per-CIK reconciler (completeness is the deterministic layer's, never the organizer's to lose) → narration + matched-term tags. AMBIGUOUS enters only by an explicit operator pick; ABSENT is shown-not-placed. Recall is sacred (#9); the LLM is demoted to keyword-gen + the tail-sweep. Full detail: `docs/DISCOVERY.md`.
- **`thesis_fit`** — the per-member drafted thesis-fit prose ("why this name sits in its segment"), on `basket_member`. Kept **distinct** from `detail` (the board/cockpit "met" cell) and a segment's `descriptor`; operational on the spine, never a fact/number.
- **The create → edit → draft front door** (M1) — "+ New thesis" → the promote upsert (`id=null`) → the editor → Draft-from-narrative; a narrative edit resends the existing basket + segments (the **wipe-trap** — a full-replace promote must never empty the authored chain). See `docs/CHAIN_DRAFTER.md`.
- **The back-half feed loop** (M2) — `pipeline.ingest_thesis` (per-thesis Form 4 + EOD for the resolved basket; incremental, fail-visible, no-lookahead) + `pipeline.daily` (the call-of-record cron — `record_if_changed` appends only on change; **"feeds itself" ≠ "validated forward"**; the Scoreboard is built and its forward record is still accruing) + the **`PriceSource` seam** (swappable EOD adapters; the recurring path force-refreshes a stale cache, and the Workbench's per-name/per-section pull shares the SAME price leg, decoupled). See `docs/FEED_LOOP.md`.
- **Three-gate TRIAGE + the cost thread** — TRIAGE runs **cheap cut** (zero API, on visible row data) → **mark for data** (the per-name / per-section opt-in; the control IS the trigger) → **finalize on data** (ratify per fact; archetype decided ONCE, on the rail). The **shortlist** (cheap-cut survivors) is the only set expensive operations touch: **cost is the operator's to spend, never ambient** — the third protective thread, peer to the trust invariants (decisions) and the interaction principles (workflow). See `docs/STAGE_MODEL.md` + `docs/TRIAGE.md`.

## Architecture & stack

See `README.md` for the full table. Key shape:

- **Postgres** = operational, bitemporal system of record (theses, signals, evidence, current state, relationship graph). Graph via adjacency tables + recursive CTEs — **do not** add Neo4j without explicit sign-off.
- **DuckDB + Parquet** = point-in-time research/replay. Backtests scan Parquet; the live app reads Postgres.
- **Backend** = Python: FastAPI, Pydantic (core schemas are first-class and typed), Polars + Arrow for transforms.
- **Ingestion** = thin custom EDGAR client over SEC JSON APIs (respect rate limits + User-Agent rules), OpenFIGI for ID mapping, FINRA short interest.
- **Orchestration** = scheduled scripts now; Dagster only when the ingest→normalize→signal DAG earns it.
- **LLM** = Anthropic API behind a model-agnostic interface (`backend/llm`, fail-open + lazy-imported). **Two seams, both in the Workbench:** the FLAG-explanation drafter (Haiku — an aid to a FLAG ratify) and the narrative→chain seam (Sonnet — it ORGANIZES + narrates the EDGAR-discovered universe, never enumerates; `docs/CHAIN_DRAFTER.md` + `docs/DISCOVERY.md`). Discovery itself is deterministic EFTS, not the LLM (the LLM is demoted to keyword-gen + a tail-sweep). Use structured/tool-use outputs, always with source citations, and **never to source a number**.
- **Frontend** = TypeScript, React (Vite SPA), Tailwind, TanStack Query for server state; lightweight-charts for price views.
- **Ops** = Docker Compose → single VPS/Fly/Railway. Monolith. `tenant_id` in every table from day one; auth deferred.

## Conventions

- **Core objects first.** Define the `Thesis`, `Signal`, and `Call` Pydantic schemas before building features around them; they are the contract between backend and frontend.
- **Signals are pure, composable, backtestable.** A signal is `f(point_in_time_data) -> SignalEvent`. No side effects, no network calls inside a detector, no implicit "now" — time is always a parameter.
- **Bitemporal columns** are explicit and consistently named (`valid_from`, `valid_to`, `recorded_at`). Never `UPDATE`-in-place a fact that has temporal meaning; insert a new version.
- **Security master is canonical.** Resolve every entity to the master (CIK ↔ ticker ↔ CUSIP ↔ FIGI) at ingest. Handle corporate actions and avoid survivorship bias in the historical universe.
- **Tests are point-in-time.** Any signal/backtest test fixes a timestamp and asserts no future leakage. Treat lookahead in a test as a failing test.
- **Python**: type hints throughout; `ruff` + `black`. **TS**: strict mode; server state via TanStack Query (not ad-hoc fetch); no browser storage for app state.
- **LLM calls** go through the `backend/llm` interface only — no scattered API calls. Prompts and structured-output schemas live with that module; responses must carry citations.
- **The OpenAPI contract is generated — regenerate it in the SAME PR as anything FastAPI emits into the schema.** A route docstring (it becomes the operation `description`), a response model, a new endpoint, a status code — all drift `backend/openapi.json` *and* `frontend/src/api/types.gen.ts`. Regenerate both (`python -m app.openapi_export` + `npm run gen:api`) in the same PR, or CI's diff-guard fails (it bit #61 — a docstring rewrite alone was enough).
- **A "tests pass" claim must come from a run that EXECUTED the DB tests.** The DB-backed suite SKIPS when Postgres is unreachable (or `DATABASE_URL` is unset) — a large "skipped" count is **not** a pass. Run against the test DB (`DATABASE_URL` → `alphadeck_test`, **never** the demo DB the `db` fixture truncates).
- **Idempotency tests COUNT THE TABLE, not the read.** The bitemporal as-of read dedups (`DISTINCT ON (natural-key) … recorded_at DESC`), so a duplicate append **hides behind a correct read** while the table silently grows. Assert `count(*)` / `list_*` length before *and* after a re-run — never just that the read looks right. The load-bearing pattern across the M2 ingest (`fact_*`) and the daily cron (`calls`); see `docs/FEED_LOOP.md`.
- **Recall is sacred — a silently dropped name is a system failure.** Any change touching discovery / classify / filters / caps / term tiers optimizes for recall and over-includes; precision is the operator deleting visible junk, never a filter that silently drops a real name. Prove recall holds (the answer-key re-score) and make any tier-demotion VISIBLE. The full rule + the five tests it imposes: `docs/INVARIANTS.md` #9.
- **A recurring/daily fetch must FORCE-REFRESH.** `fetch_eod`/`fetch_csv` are cache-first; a cache *hit* returns STALE bars and never re-pulls (so a daily re-ingest froze — the #72 latent bug). The recurring path passes `force_refresh=True` (re-pull + overwrite the cache); dev/`--no-live` stays cache-first; a cache MISS always fetches.
- **Shell metacharacters in a `git commit -m` get EVALUATED.** Backticks in a double-quoted bash `-m "…"` run as command substitution — one actually ran `docker compose up` and hung the task. Use a **quoted heredoc** (`git commit -F - <<'MSG' … MSG`) or a message file for any non-trivial commit message. (The `gh pr create --body "$(cat <<'EOF' … EOF)"` pattern is already safe — the heredoc delimiter is quoted.)
- **Stacked PRs merge TOP-of-stack first** (or delete the base branch on merge). A stacked PR merged into an already-merged base shows "Merged" while main never received its commits — the #135 trap, re-landed as #136. After any stacked merge, verify main actually contains the commits; don't trust the badge.

## Out of scope for v1

Reject or defer (flag, don't silently build): trade execution / brokerage integration; position sizing and portfolio-risk management (handoff to the firm's OMS / execution / risk systems); intraday or streaming data; options-gamma and paid borrow-data signals; crypto / commodities / prediction-market asset classes; runtime authentication and multi-tenant serving (schema seams only); a separate graph database.

## Data sources

Full detail in `docs/DATA_SOURCES.md`. Summary:
- **Free (v1 baseline):** SEC EDGAR (filings, full-text search, XBRL, and N-1A/485 for new ETF registrations), FINRA short interest, OpenFIGI, free EOD price/fundamentals, public ETF holdings, on-chain/crypto data for crypto-adjacent themes only.
- **Paid, case-by-case (later):** borrow fee/utilization, options/gamma, premium fundamentals, ETF flow data (or derive from shares-outstanding × NAV).
- **EDGAR etiquette:** declared User-Agent, respect the documented rate limit, cache aggressively. Ingestion politeness is a correctness requirement, not a nicety.

## Commands

The whole app runs from one command via Docker (below). For backend development, use a stdlib venv +
pip (no `uv`); run from `backend/` with the venv active (or set `$env:PYTHONPATH="backend"` and call
`backend\.venv\Scripts\python` from the repo root). Postgres is Docker Compose on host port 5544.

```powershell
# full stack — one command: Postgres + API (migrates + seeds HIMS on start) + the SPA behind nginx
docker compose up --build                                # app: localhost:8080 · API/docs: localhost:8000/docs

# + the daily call-of-record cron ("feeds itself", M2d) — DISABLED by default; opt in via the `cron` profile
docker compose --profile cron up -d --build             # adds the cron sidecar (daily @ US close, America/New_York)

# infra only — Postgres for the local backend dev loop
docker compose -f infra/docker-compose.yml up -d        # Postgres 16 (localhost:5544)

# backend setup (once)
python -m venv backend\.venv
backend\.venv\Scripts\python -m pip install "pydantic>=2.6" "psycopg[binary]>=3.1" "httpx>=0.27" "fastapi>=0.110" "uvicorn>=0.29" "anthropic>=0.40" pytest ruff black
# the LLM seams (the FLAG-explanation + narrative->chain drafters) need ANTHROPIC_API_KEY for LIVE drafts; with
# no key they fail open (no draft, the app works as today). The suite never needs the key (the SDK is lazy).
# Put it in a gitignored .env (copy .env.example) — docker compose injects it into the backend container.

# backend dev loop (from backend\, venv active)
python -m db.migrate                                    # apply migrations (idempotent)
python -m pipeline.seed                                 # seed the HIMS demo thesis
python -m pipeline.populate_master --live               # load the SEC universe into the master (UA req'd)
python -m uvicorn app.main:app --reload                 # serve the API (127.0.0.1:8000)
python -m pipeline.run --thesis <id> --asof 2026-06-01  # assemble a call from the CLI
python -m pipeline.ingest_thesis --thesis <id>          # ingest a thesis's back-half facts (Form 4 + EOD)
python -m pipeline.daily                                 # the cron's unit: refresh facts + log each thesis's call-of-record
pytest                                                   # tests (DB tests skip if no Postgres)
ruff check . ; black --check .                          # lint + format

# Checkpoint A, served:
curl "http://127.0.0.1:8000/theses/<id>/call?asof=2026-06-01"

# the narrative->chain draft (S5; needs ANTHROPIC_API_KEY — else an empty fail-open draft):
curl -X POST "http://127.0.0.1:8000/workbench/theses/<id>/draft-chain"

# frontend dev (from frontend\): npm install · npm run dev   # Vite on :5173, proxies /api -> :8000
```

Keep this section current as the source of truth for build/run/test.
