# Alpha Deck

**A research cockpit for hunting, validating, timing, and monitoring equity theses.**
A "DD engine" that helps a discretionary trader turn early narrative convictions into *well-timed* trades — decision-support, not execution.

**Boundary:** Alpha Deck hands its research and timing output to the firm's existing OMS, execution,
position-sizing, and portfolio-risk systems. It does not route orders, size positions, set risk limits, or
manage the portfolio; after an operator-entered position is logged, it keeps monitoring the thesis.

> Working codename: **Alpha Deck**. US equities first; commodities / crypto / prediction markets later.
> **The organizing frame — the pipeline of stages (SEED → DISCOVER → SURFACE → TRIAGE → MONITOR → SCORE): [`docs/STAGE_MODEL.md`](docs/STAGE_MODEL.md) (read first).**
> The live plan: [`docs/ROADMAP.md`](docs/ROADMAP.md). Design rationale: [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md). Agent working-agreements: [`CLAUDE.md`](CLAUDE.md). The decision logic: [`docs/CALL_LOGIC.md`](docs/CALL_LOGIC.md). The catalyst conviction key: [`docs/CATALYST_CONVICTION.md`](docs/CATALYST_CONVICTION.md). Data sourcing: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md). Implementation invariants: [`docs/INVARIANTS.md`](docs/INVARIANTS.md). The post-MVP tuning agenda: [`docs/RECALIBRATION.md`](docs/RECALIBRATION.md). UI mockups in [`docs/mockups/`](docs/mockups/).

---

## The problem it solves

Alpha Deck is built around one operator pattern:

- **Edge:** spotting macro narratives early — reading the *narrative clock*. Strong at "what comes next."
- **Flaw:** timing ("terrible at dates") and translating a narrative into the *specific* names that best express it (defaults to broad ETFs).

So the whole system follows two rules:

1. **Preserve the edge, patch the flaw.** Capture early convictions so they're never lost or forgotten (the vault); supply name selection, DD, and timing discipline on top.
2. **Opinionated about timing, deferential about thesis.** The platform never votes on whether an idea is good — that's the operator's strength. It gets loud only on *when* and on the call's evidence-backed strength — the operator's weak spot.

## What it does

Maps the operator's six hunts onto capabilities:

| Goal | Capability |
|---|---|
| Picks-and-shovels | Value-chain decomposition; supply/manufacturing/distribution/downstream layers |
| Obvious next plays | Laggard scanner (beta-adjusted residual vs a theme leader) |
| Early to emerging / grey-area sectors | Emergence detector (regulatory + personnel + attention rate-of-change + first institutional footprint) |
| SEC filing / insider alpha | EDGAR firehose, insider-conviction scoring, dilution clock |
| Short squeezes | Squeeze radar (SI / DTC / borrow / gamma, gated on a catalyst) |
| Sector tracking | Sector dashboard with "section headline" lenses |
| Low-risk thematic exposure | **ETF radar** — availability + coming launches (SEC N-1A/485) + holdings/flows; the safe-exposure expression of a thesis |

## How it works — two halves on one spine

- **Front (hunt + research):** narrative → decompose into the value chain → surface & score specific names by exposure purity, risk, cash runway, catalyst density → auto-draft DD per name.
- **Back (park + time):** chosen names become a parked **thesis** → incubate → graded trigger → monitor the entered thesis through its live signal window.
- **Spine:** the **thesis is a first-class object** (narrative, basket, evidence, catalysts, signals, kill criteria, expression). Everything attaches to it; no orphan screeners.

**Thesis lifecycle** (a loop, not a ratchet): `Incubating → Warming → Armed → Managing`; Managing means an
operator-entered position is being monitored, not that Alpha Deck manages its risk.
**Trigger grading:** every fire is assigned a categorical call-strength class — *flip* for a fast,
sentiment/attention-driven setup, *core* for a structural, more durable setup — and a type (regulatory,
promoter/attention, technical breakout, clinical readout, squeeze, personnel). Grade never determines
position size, instrument, or expression.
**The call layer is opinionated and auditable:** the platform *makes the call and shows its work* — verdict +
advisory research context + the `exit_by` signal-validity horizon (not a sell-by date), the triggers that fired
(with source links), what's missing, the counter-case, experimental **setup strength** (wire field
`confidence`, not a probability), and logged `act / override / snooze` decisions. An override is a logged bet
against documented evidence, then scored.

See `docs/PROJECT_OVERVIEW.md` for the lifecycle, grading, sector dashboard lenses, and analytic components (momentum-health classifier, personality-backed attention proxy, dilution clock, laggard scanner, insider-conviction scoring, ETF radar) in full.

## Surfaces (UI)

Four surfaces; clickable mockups live in `docs/mockups/` (Board+Cockpit, the 2025 umbrella board, a segment cockpit, the Workbench, and the Cockpit per-name build).

- **Board** — pipeline of theses as cards flowing through the four lifecycle states. Home base.
- **Cockpit** — thesis detail: narrative + conviction notes (preserved), the basket grouped by each name's **own** call state (collapsible per-name buckets, Managing → Quiet) with a read-only **per-name panel** (its call, its own triggers, its slice of the operator record), evidence, catalyst calendar, and the live **call card** ([`docs/BOARD.md`](docs/BOARD.md)).
- **Workbench** — the front half: drop in a narrative, draft the value chain, ratify the names + facts, promote a thesis to the Board. **Built** — the create → draft → ratify → score → promote loop ([`docs/CHAIN_DRAFTER.md`](docs/CHAIN_DRAFTER.md)).
- **Scoreboard** — review/calibration: the episode ledger over the forward record — the platform's calls (scored on their own `exit_by` signal-validity yardstick) vs your logged decisions, overrides with their outcomes, gated aggregate metrics. Setup strength remains experimental until this forward record supports calibration; the `n ≥ 5` aggregate-metric gate is a UI safeguard against over-reading a tiny sample, not an evidence threshold. *(v1 built — `docs/SCOREBOARD.md`; the follow-blindly counterfactual + deltas are v2, replay-history-alongside is built.)*

**Signature design principle — inverse loudness:** visual (and notification) loudness runs *inversely* to how much the interface wants you to act. Incubating is dormant and quiet (the vault — nothing to react to); Warming glows faintly; Armed is loud and unmissable; Managing is a calm thesis-monitoring panel for a position the operator entered. The UI *is* the gate.

## Architecture & stack

Three load-bearing decisions drive the shape:

1. **EOD cadence → no streaming infra.** Scheduled batch + on-demand pulls. No Kafka, no event bus. A nightly pipeline and a job queue is the runtime.
2. **Point-in-time is the moat → Postgres + DuckDB/Parquet.** Postgres is the operational, bitemporal system of record; historical research/replay data lives as Parquet snapshots read through DuckDB. The replay harness makes "would this have flagged X without lookahead?" answerable.
3. **The LLM augments, never sources, signals.** Signals fire deterministically from code; the model reads filings, drafts DD, and explains calls — every trigger traces to a computation, never to the model's say-so.

| Layer | Choice |
|---|---|
| Operational DB | PostgreSQL — bitemporal system of record + relationship graph (adjacency tables + recursive CTEs; no Neo4j yet) |
| Research / replay | DuckDB + Parquet (point-in-time snapshots, embedded, file-based) |
| Backend | Python · FastAPI · Pydantic (core schemas) · Polars (+ Arrow) |
| Ingestion | Thin custom EDGAR client over SEC JSON APIs · OpenFIGI for CIK↔ticker↔FIGI · FINRA short interest |
| Orchestration | Scheduled scripts → Dagster when the DAG earns it |
| LLM | Anthropic API behind a model-agnostic interface; structured/tool-use outputs with source citations |
| Frontend | TypeScript · React (Vite SPA) · Tailwind · TanStack Query |
| Charts | lightweight-charts (price/volume/breakout) · visx/Recharts (analytics) |
| Ops | Docker Compose → single VPS / Fly / Railway. Monolith. `tenant_id` in schema from day one; auth deferred |

## Repo layout (proposed)

```
alpha-deck/
├── README.md
├── CLAUDE.md
├── docs/PROJECT_OVERVIEW.md
├── backend/
│   ├── app/          # FastAPI app + routes
│   ├── domain/       # thesis / signal / call schemas (Pydantic) — the spine
│   ├── securities/   # security master, entity resolution, corporate actions
│   ├── ingest/       # EDGAR, price/fundamentals, FINRA SI, FIGI
│   ├── signals/      # composable detectors (insider, laggard, squeeze, momentum-health…)
│   ├── llm/          # model-agnostic interface, DD drafting, call explanation
│   ├── pipeline/     # scheduled jobs / orchestration
│   ├── replay/       # point-in-time backtest harness (DuckDB/Parquet)
│   └── db/           # Postgres schema + migrations (bitemporal)
├── data/             # Parquet snapshots, DuckDB file (gitignored)
├── frontend/src/
│   ├── surfaces/     # board, cockpit, workbench, scoreboard
│   ├── api/          # TanStack Query hooks
│   ├── components/
│   └── lib/
└── infra/            # docker-compose, deploy
```

## v1 scope

**In:** the Board + Cockpit surfaces; the thesis object + lifecycle; the SEC/filing intelligence brick (insider-conviction, dilution clock, 8-K classification) feeding both halves; the laggard scanner; the opinionated call layer with override logging; the point-in-time data foundation.

**Built since (the MVP):** the **Workbench** (the full front half — scoring, authoring, the extract → ratify hybrid, the two LLM seams, and the create-thesis front door) + the **M2 feed loop** (per-thesis back-half ingest + the daily call-of-record cron) + the **Scoreboard v1** (the forward trust-loop instrument; its record is still accruing). See `docs/ROADMAP.md`.

**Out** (see CLAUDE.md): trade execution / brokerage writes, position sizing, and portfolio-risk management
(all handed off to the firm's OMS / execution / risk stack); intraday/streaming; options-gamma & paid borrow
data; crypto / commodities / prediction markets; runtime auth & multi-tenancy.

## Build order

1. **Foundation** — security master + point-in-time (bitemporal) data layer. Done first; expensive to retrofit.
2. **SEC / filing intelligence brick** — biggest operator gap, cheapest to build (EDGAR is free), feeds both halves.
3. **Thesis object + workspace** — the spine: evidence linking, catalyst calendar, kill criteria, the call card.
4. **Laggard scanner + theme taxonomy** — over EOD price.
5. **Squeeze radar / options-gamma** — phase in once paying for borrow + options data.

## Status

**The MVP is complete** — both halves on one spine are built and the platform feeds itself. A thesis flows
end to end on real data: **create from a new narrative → draft the value chain (the LLM seam) → ratify the
names + facts → promote → the back half ingests insider + price → it WARMS/ARMS → the daily cron logs the
call-of-record.** *(Freshness caveat: "feeds itself" was literally true only after #196 — the EDGAR cache
froze insider data for ~11 days until the key-classed 12h TTL; see `docs/POSTMORTEM_CRON_FREEZE_2026-07.md`.)*

Built: the domain spine + the bitemporal Postgres foundation; the SEC brick + the detectors + the
golden-tested call-assembler (`GET /theses/{id}/call?asof=` → computed, evidence-linked Armed calls); the
catalyst subsystem + the automated DOE feed; the M5 per-member menu + theme arming; Phase 1 (the replay
harness + recalibration + the production-tenant cut); the Board + Cockpit UI; and the **Workbench** — scoring
(the four meters), authoring, the extract → ratify hybrid, the SEC-universe broadener, the two LLM seams
(FLAG-explanation + the narrative → chain drafter), and the create-thesis front door. The **M2 feed loop** —
the per-thesis back-half ingest + the daily call-of-record cron + the price-source seam + the scheduling
sidecar — makes it feed itself. On top, the **front half is now framed as stages** (the Workbench = three of
them): **SURFACE** (recommended-until-confirmed facts + identity enrichment + the derived archetype +
the grounded purity estimate) and **TRIAGE** (basket crafting — include-controls, sort/filter, the conviction
weight) shipped, plus EDGAR-first **DISCOVER** and the off-thesis flag — see [`docs/STAGE_MODEL.md`](docs/STAGE_MODEL.md).
Sequencing: [`docs/ROADMAP.md`](docs/ROADMAP.md); how it feeds itself:
[`docs/FEED_LOOP.md`](docs/FEED_LOOP.md). Build/run/test commands: [`CLAUDE.md`](CLAUDE.md).

**Trust state (kept honest):** the calls are tuned **in-sample** (n=19; see `docs/ROADMAP.md`'s trust box).
"The platform feeds itself daily" is the **data loop — not forward validation.** The **Scoreboard v1**
(the forward trust loop's instrument: the platform's calls vs the operator's decisions, scored over the
call-of-record) is **built** — forward evidence now accrues with the record itself. The setup-strength value
remains experimental until those outcomes support calibration; the UI hides early aggregates below `n=5`
only to discourage over-reading, and crossing that gate does not establish evidence. The follow-blindly
counterfactual + deltas are v2.
