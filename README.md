# Alpha Deck

**A research cockpit for hunting, validating, timing, and managing equity theses.**
A "DD engine" that helps a discretionary trader turn early narrative convictions into *well-timed* trades — decision-support, not execution.

> Working codename: **Alpha Deck**. US equities first; commodities / crypto / prediction markets later.
> The live plan: [`docs/ROADMAP.md`](docs/ROADMAP.md). Design rationale: [`docs/PROJECT_OVERVIEW.md`](docs/PROJECT_OVERVIEW.md). Agent working-agreements: [`CLAUDE.md`](CLAUDE.md). The decision logic: [`docs/CALL_LOGIC.md`](docs/CALL_LOGIC.md). The catalyst conviction key: [`docs/CATALYST_CONVICTION.md`](docs/CATALYST_CONVICTION.md). Data sourcing: [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md). Implementation invariants: [`docs/INVARIANTS.md`](docs/INVARIANTS.md). The post-MVP tuning agenda: [`docs/RECALIBRATION.md`](docs/RECALIBRATION.md). UI mockups in [`docs/mockups/`](docs/mockups/).

---

## The problem it solves

Alpha Deck is built around one operator pattern:

- **Edge:** spotting macro narratives early — reading the *narrative clock*. Strong at "what comes next."
- **Flaw:** timing ("terrible at dates") and translating a narrative into the *specific* names that best express it (defaults to broad ETFs).

So the whole system follows two rules:

1. **Preserve the edge, patch the flaw.** Capture early convictions so they're never lost or forgotten (the vault); supply name selection, DD, and timing discipline on top.
2. **Opinionated about timing, deferential about thesis.** The platform never votes on whether an idea is good — that's the operator's strength. It gets loud only on *when / what grade / what expression* — the operator's weak spot.

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
- **Back (park + time):** chosen names become a parked **thesis** → incubate → graded trigger → manage to exit.
- **Spine:** the **thesis is a first-class object** (narrative, basket, evidence, catalysts, signals, kill criteria, expression). Everything attaches to it; no orphan screeners.

**Thesis lifecycle** (a loop, not a ratchet): `Incubating → Warming → Armed → Managing`.
**Trigger grading:** every fire is graded *flip* vs *core/structural* and typed (regulatory, promoter/attention, technical breakout, clinical readout, squeeze, personnel). Expression follows grade.
**The call layer is opinionated and auditable:** the platform *makes the call and shows its work* — verdict + expression + exit-by, the triggers that fired (with source links), what's missing, the counter-case, a calibrated confidence, and `act / override / snooze`. An override is a logged bet against documented evidence, then scored.

See `docs/PROJECT_OVERVIEW.md` for the lifecycle, grading, sector dashboard lenses, and analytic components (momentum-health classifier, personality-backed attention proxy, dilution clock, laggard scanner, insider-conviction scoring, ETF radar) in full.

## Surfaces (UI)

Four surfaces; three clickable mockups exist in `docs/mockups/` (Board+Cockpit, the 2025 umbrella board, and a segment cockpit).

- **Board** — pipeline of theses as cards flowing through the four lifecycle states. Home base.
- **Cockpit** — thesis detail: narrative + conviction notes (preserved), basket with archetype tags, evidence, catalyst calendar, and the live **call card**.
- **Workbench** — the front half: drop in a narrative, get the decomposed "section headline" lists, pull names into a basket that spawns a thesis. *(v1: stubbed)*
- **Scoreboard** — review/calibration: platform calls vs your decisions vs the counterfactual; override log; outcome attribution. *(v1: stubbed)*

**Signature design principle — inverse loudness:** visual (and notification) loudness runs *inversely* to how much the interface wants you to act. Incubating is dormant and quiet (the vault — nothing to react to); Warming glows faintly; Armed is loud and unmissable; Managing is a calm instrument panel. The UI *is* the gate.

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

**Stubbed:** Workbench and Scoreboard surfaces.

**Out** (see CLAUDE.md): trade execution / brokerage writes; intraday/streaming; options-gamma & paid borrow data; crypto / commodities / prediction markets; runtime auth & multi-tenancy.

## Build order

1. **Foundation** — security master + point-in-time (bitemporal) data layer. Done first; expensive to retrofit.
2. **SEC / filing intelligence brick** — biggest operator gap, cheapest to build (EDGAR is free), feeds both halves.
3. **Thesis object + workspace** — the spine: evidence linking, catalyst calendar, kill criteria, the call card.
4. **Laggard scanner + theme taxonomy** — over EOD price.
5. **Squeeze radar / options-gamma** — phase in once paying for borrow + options data.

## Status

**M3a complete — Checkpoint A is reached and served.** A single real thesis (HIMS) flows end to end on
real data: SEC Form 4 + EOD → the bitemporal store → pure detectors (insider conviction, volume
breakout) → the golden-tested call-assembler → `GET /theses/{id}/call?asof=`, producing a computed,
evidence-linked **Armed** call (currently a `starter_entry`: core conviction, momentum-only
confirmation). Built so far: the domain spine, the bitemporal Postgres foundation, the SEC brick + the
two detectors, thesis persistence + repositories + the assembly pipeline, and the FastAPI layer.
**Next (M3b):** the Board + Cockpit UI rendering the call over generated TS types. Build/run/test
commands live in `CLAUDE.md`.
