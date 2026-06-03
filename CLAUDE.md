# CLAUDE.md

Working agreements for AI agents (and humans) operating in this repo. Read this before writing code.
This file is about **how to build Alpha Deck correctly**; the *what* and *why* live in `README.md` and `docs/PROJECT_OVERVIEW.md`.

---

## Mission

Alpha Deck helps a discretionary trader turn early narrative convictions into well-timed trades. It is **decision-support, not execution**. Its job is to *preserve the operator's edge* (early narrative-spotting) and *patch the flaw* (timing, name selection). Be **opinionated about timing, deferential about thesis**.

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
- **Trigger grade**: `flip` (fast, sentiment-driven; small size, short-dated, do not hold) vs `core` (structural; build the position).
- **Trigger type**: `regulatory | promoter_attention | technical_breakout | clinical_readout | squeeze | personnel`.
- **Archetype** (basket member role): `leader | high_beta | lotto | shovel`.
- **Alpha half-life** — how long a signal's edge persists; sets the **exit-by** date.
- **Catalyst surface** — the binary events crossed between entry and exit-by.
- **Call card** — `{ verdict, grade, expression, exit_by, triggers_fired[] (with sources), missing[], counter_case, confidence, actions }`.
- **The gate** — advisory friction: withhold the go-signal + readiness scorecard + logged override.

## Architecture & stack

See `README.md` for the full table. Key shape:

- **Postgres** = operational, bitemporal system of record (theses, signals, evidence, current state, relationship graph). Graph via adjacency tables + recursive CTEs — **do not** add Neo4j without explicit sign-off.
- **DuckDB + Parquet** = point-in-time research/replay. Backtests scan Parquet; the live app reads Postgres.
- **Backend** = Python: FastAPI, Pydantic (core schemas are first-class and typed), Polars + Arrow for transforms.
- **Ingestion** = thin custom EDGAR client over SEC JSON APIs (respect rate limits + User-Agent rules), OpenFIGI for ID mapping, FINRA short interest.
- **Orchestration** = scheduled scripts now; Dagster only when the ingest→normalize→signal DAG earns it.
- **LLM** = Anthropic API behind a model-agnostic interface; use structured/tool-use outputs to fill call-card fields, always with source citations.
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

## Out of scope for v1

Reject or defer (flag, don't silently build): trade execution / brokerage integration; intraday or streaming data; options-gamma and paid borrow-data signals; crypto / commodities / prediction-market asset classes; runtime authentication and multi-tenant serving (schema seams only); a separate graph database.

## Data sources

- **Free (v1 baseline):** SEC EDGAR (filings, full-text search, XBRL), FINRA short interest, OpenFIGI, free EOD price/fundamentals, on-chain/crypto data for crypto-adjacent themes only.
- **Paid, case-by-case (later):** borrow fee/utilization, options/gamma, premium fundamentals.
- **EDGAR etiquette:** declared User-Agent, respect the documented rate limit, cache aggressively. Ingestion politeness is a correctness requirement, not a nicety.

## Commands

_Greenfield — fill in as the project scaffolds._

```
# backend:   (TBD) uv / poetry install · uvicorn app.main:app --reload · pytest
# frontend:  (TBD) pnpm install · pnpm dev · pnpm test
# data:      (TBD) python -m pipeline.run --asof <date>
# infra:     (TBD) docker compose up
```

Keep this section current as the source of truth for build/run/test once they exist.
