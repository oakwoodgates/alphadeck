# CLAUDE.md

Working agreements for AI agents (and humans) operating in this repo. Read this before writing code.
This file is about **how to build Alpha Deck correctly**; the *what* and *why* live in `README.md` and `docs/PROJECT_OVERVIEW.md`.

---

## Mission

Alpha Deck helps a discretionary trader turn early narrative convictions into well-timed trades. It is **decision-support, not execution**. Its job is to *preserve the operator's edge* (early narrative-spotting) and *patch the flaw* (timing, name selection). Be **opinionated about timing, deferential about thesis**.

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
4. **Opinionated on timing, deferential on thesis.** The system grades, times, and selects expression. It does **not** judge whether a trade idea is good. Don't add features that rank or veto theses on conviction.
5. **Advisory only (v1).** No order routing, no brokerage writes, no execution of any kind. The "gate" *withholds the platform's go-signal and logs overrides* — it never blocks the user and never places a trade. The user logs their own fills.
6. **Explainability is a feature.** Every call, score, and signal carries its evidence and provenance (source filing/data + the computation). No black-box outputs. If you can't show the work, don't surface the result.
7. **Inverse loudness.** Quietness scales with how early it is. Incubating must not nag (no alerts, low visual energy); loudness is reserved for Armed. This applies to notifications, not just CSS.

## Domain vocabulary

Use these terms precisely; they are the ubiquitous language of the codebase.

- **Thesis** — first-class object: `narrative`, `basket`, `evidence[]`, `catalysts[]`, `signals[]`, `kill_criteria[]`, `expression`/`position`.
- **Lifecycle states** (a loop, not a one-way ratchet): `Incubating → Warming → Armed → Managing`.
- **Signal taxonomy** (three orthogonal fields): **`role`** = `entry_trigger | risk_signal` (only entry triggers turn the keys; risk signals feed counter-case/confidence and can soft-veto *timing*); **`kind`** = what produced it (`insider | technical_breakout | laggard | squeeze | etf_launch | etf_flow | dilution_risk | …`); **`type`** = catalyst nature where one applies (`regulatory | promoter_attention | clinical_readout | personnel | …`, optional).
- **Trigger grade**: `flip` (fast, sentiment-driven; small size, short-dated, do not hold) vs `core` (structural; build the position). Applies to entry triggers.
- **Archetype** (basket member role): `leader | high_beta | lotto | shovel | adjacent | fund` (`adjacent` = off-thesis/impure exposure, surfaced and *flagged* by the Workbench; `fund` = the ETF safe-exposure sleeve).
- **Alpha half-life** — how long a signal's edge persists; sets the **exit-by** date.
- **Catalyst surface** — the binary events crossed between entry and exit-by.
- **Call card** — `{ verdict, grade, expression, exit_by, triggers_fired[] (with sources), missing[], counter_case, confidence, actions }`.
- **The gate** — advisory friction: withhold the go-signal + readiness scorecard + logged override.
- **ETF radar** — per-theme ETF intelligence: *availability* (which ETFs express the theme), *coming launches* (SEC N-1A/485 — a new thematic launch is an emergence-kind signal), and *holdings/flows* (free universe seed + positioning signal). An ETF is also a low-torque **expression** of a thesis (the safe-exposure sleeve), always surfaced with fund internals (holdings, weights, expense ratio, AUM, liquidity).
- **Scoring-fact tiers** (the Workbench extractor) — `AUTO` (companyfacts reproduces the value → pre-fill, confirm-and-go) · `FLAG` (raw value + a detected risk + a **located passage** → the operator ratifies the composition) · `HUMAN` (purity — located only, **never auto-valued**). The extractor **LOCATES; the operator RATIFIES** (invariant #3). See `docs/WORKBENCH_EXTRACTION.md`.
- **Located passage** — a deterministically-retrieved filing excerpt (keyword/section match, never a model's reading), shown inline as the evidence behind a FLAG/HUMAN candidate.
- **Authorship** — `operator_set` / `operator_edited` / `system_drafted`. The **draft → ratify transitions**: a drafted placement **loads** `system_drafted`, the operator **accepts** it → `operator_set` or **edits** any field → `operator_edited`; promote **honors** the authorship (never coerces it). Nothing drafted becomes a fact until the operator ratifies it.
- **The explain seam** — the Workbench's flag-explanation drafter (`backend/llm`, the FIRST LLM seam): a grounded plain-English **aid** to a FLAG ratify — an explanation, never a fact, on a rail that can't write one.
- **The chain drafter / the SECOND LLM seam** — the narrative→chain drafter (`backend/llm/chain_decomposition.py`): from a narrative, Sonnet drafts the value chain (segments + names + thesis-fit prose) into the authoring surface as `system_drafted`, to ratify. **Response-only** (writes nothing; promote is the only writer), **fail-open**, and it **never sources a number**. The front door to the loop: **draft → ratify → extract → score → promote**. See `docs/CHAIN_DRAFTER.md`.
- **Discovery net, made visible** — a drafter-proposed name is a *suggestion*; exact master membership DECIDES (`resolve_placements`): a unique exact ticker/name match auto-loads (drafted, prunable), an AMBIGUOUS one enters the basket **only by an explicit operator pick** (ticker + CIK shown), an ABSENT one is shown-not-placed. The allowlist rule of invariant #2, now on the UI.
- **`thesis_fit`** — the per-member drafted thesis-fit prose ("why this name sits in its segment"), on `basket_member`. Kept **distinct** from `detail` (the board/cockpit "met" cell) and a segment's `descriptor`; operational on the spine, never a fact/number.
- **The create → edit → draft front door** (M1) — the Workbench entry: "+ New thesis" (name + narrative) creates via the promote upsert (`id=null`), lands in the editor, then "Draft from narrative"; the same `ThesisFields` form edits the narrative later, **resending the existing basket + segments** (the **wipe-trap** — a full-replace promote must never empty the authored chain). See `docs/CHAIN_DRAFTER.md`.
- **The per-thesis back-half ingest** (`pipeline.ingest_thesis`, M2) — pulls the CALL-ENGINE facts (insider Form 4 + price EOD) for a thesis's **resolved** basket (`master.get`, exact membership #2), so a created thesis can WARM/ARM. **Incremental** (a re-run appends nothing), **fail-visible** per name, `recorded_at=now` (no-lookahead). See `docs/FEED_LOOP.md`.
- **The price-source seam** (`ingest/prices/source.py`) — the EOD source behind a `get_bars` **`PriceSource`** interface (Yahoo / Stooq **adapters**), so swapping the source is changing an adapter, not a rewrite. **force-refresh** — the recurring/daily path re-pulls + overwrites the cache (cache-first returns STALE on a hit otherwise); dev/`--no-live` stays cache-first.
- **The daily call-of-record cron / "the platform feeds itself"** (`pipeline.daily`, M2) — once a day per thesis: ingest → assemble (`record=False`) → **`record_if_changed`** (append the call-of-record only if it changed; the immutable `calls` log forces a conditional append, `_canonical` the order-independent compare). The forward RECORD the Scoreboard will track — **Scoreboard-ready, not coupled.** The `cron` sidecar (compose profile, **disabled by default**) is a swappable trigger; the CLI is the unit. **"Feeds itself" ≠ "validated forward"** (the Scoreboard is parked). See `docs/FEED_LOOP.md`.

## Architecture & stack

See `README.md` for the full table. Key shape:

- **Postgres** = operational, bitemporal system of record (theses, signals, evidence, current state, relationship graph). Graph via adjacency tables + recursive CTEs — **do not** add Neo4j without explicit sign-off.
- **DuckDB + Parquet** = point-in-time research/replay. Backtests scan Parquet; the live app reads Postgres.
- **Backend** = Python: FastAPI, Pydantic (core schemas are first-class and typed), Polars + Arrow for transforms.
- **Ingestion** = thin custom EDGAR client over SEC JSON APIs (respect rate limits + User-Agent rules), OpenFIGI for ID mapping, FINRA short interest.
- **Orchestration** = scheduled scripts now; Dagster only when the ingest→normalize→signal DAG earns it.
- **LLM** = Anthropic API behind a model-agnostic interface (`backend/llm`, fail-open + lazy-imported). **Two seams, both in the Workbench:** the FLAG-explanation drafter (Haiku — an aid to a FLAG ratify) and the narrative→chain drafter (Sonnet — drafts the value chain to ratify; `docs/CHAIN_DRAFTER.md`). Use structured/tool-use outputs, always with source citations, and **never to source a number**.
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
- **A recurring/daily fetch must FORCE-REFRESH.** `fetch_eod`/`fetch_csv` are cache-first; a cache *hit* returns STALE bars and never re-pulls (so a daily re-ingest froze — the #72 latent bug). The recurring path passes `force_refresh=True` (re-pull + overwrite the cache); dev/`--no-live` stays cache-first; a cache MISS always fetches.
- **Shell metacharacters in a `git commit -m` get EVALUATED.** Backticks in a double-quoted bash `-m "…"` run as command substitution — one actually ran `docker compose up` and hung the task. Use a **quoted heredoc** (`git commit -F - <<'MSG' … MSG`) or a message file for any non-trivial commit message. (The `gh pr create --body "$(cat <<'EOF' … EOF)"` pattern is already safe — the heredoc delimiter is quoted.)

## Out of scope for v1

Reject or defer (flag, don't silently build): trade execution / brokerage integration; intraday or streaming data; options-gamma and paid borrow-data signals; crypto / commodities / prediction-market asset classes; runtime authentication and multi-tenant serving (schema seams only); a separate graph database.

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

# frontend dev (from frontend\): npm install · npm run dev   # Vite on :5173, proxies /theses -> :8000
```

Keep this section current as the source of truth for build/run/test.
