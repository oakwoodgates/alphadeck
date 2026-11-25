# Alpha Deck

Alpha Deck is a research and timing platform for discretionary equity traders. A user writes down a thesis, finds
the companies that express it, and gets a nightly, evidence-linked call on whether the setup is forming, ready or
over. The data is SEC filings, end-of-day prices and a few other public feeds; every call shows the filings and
computations behind it. It is advisory only: the firm's order, sizing and risk systems own the trade. Platform data
(filings, prices, share counts) is shared and curated by a steward; theses, baskets and decisions live in each
user's private workspace.
It is built for one pattern: a trader who reads narratives early and times entries badly. It keeps the early read and
adds name selection and timing discipline, so it is opinionated about when and deferential about whether the idea
is good.

## Core concepts

- **Thesis (a card).** A narrative, a basket of companies grouped into value-chain links, catalysts, kill criteria
  and the user's logged decisions. Three fixed silent links (Context, ETFs, Bench) hold names that are on the card
  but never count toward its call. Every feature attaches to a thesis.
- **The six stages.** Each hands the user a decision:

| Stage | What happens | Who leads | Read |
|---|---|---|---|
| SEED | A user banks a narrative in their own words | User | [WORKBENCH](docs/WORKBENCH.md) |
| DISCOVER | Theme to companies: EDGAR full-text search over the card's term set; a model organizes and narrates, never recalls names | Deterministic code | [DISCOVERY](docs/DISCOVERY.md) |
| SURFACE | Each name is filled in: identity, business type, share count, cash and burn from filings, a purity estimate to confirm | System recommends | [EXTRACTOR](docs/EXTRACTOR.md), [PLATFORM_VALUES](docs/PLATFORM_VALUES.md) |
| TRIAGE | The user cuts the draft to a basket: include or exclude, sign off, arrange links, save | User | [WORKBENCH](docs/WORKBENCH.md) |
| MONITOR | The saved card is graded every night | Machine times, user decides | [BOARD](docs/BOARD.md), [CALL_LOGIC](docs/CALL_LOGIC.md), [FEED_LOOP](docs/FEED_LOOP.md) |
| SCORE | The platform's calls and the user's decisions are scored over the nightly record | Machine judges | [SCOREBOARD](docs/SCOREBOARD.md), [EDITIONS](docs/EDITIONS.md) |

- **Lifecycle and the call card.** A card loops through Incubating (banked, quiet), Warming (triggers live, no name
  actionable yet), Armed (a conviction trigger and a confirmation live together on one name) and Managing (the
  user logged a position; the platform monitors the thesis and does not manage risk). The call card shows the verdict, the grade (`flip`: fast and
  attention-driven; `core`: structural and more durable), `exit_by` (where the signal's validity window ends, not
  a sell date), the triggers with source links, what is missing, a counter-case and setup strength (wire field
  `confidence`; experimental, not a probability). An override of the platform's go-signal is a logged bet against
  documented evidence.
- **Platform data and workspace data.** Share counts, cash and burn, filings and prices are one shared layer
  ([PLATFORM_VALUES](docs/PLATFORM_VALUES.md)). The steward, a super admin, approves what the machine cannot.
  Cards, purity, decisions and calls are private to a user's workspace ([TENANT_SCOPES](docs/TENANT_SCOPES.md)). The
  operator is whoever runs the deployment.
- **Ground rules** ([INVARIANTS](docs/INVARIANTS.md), fourteen in all): history is judged on the public clock, so
  no lookahead; a model augments and never sources a number or a firing; the platform is opinionated on timing,
  deferential on the thesis, and advisory only. Terms: [GLOSSARY](docs/GLOSSARY.md).

## Architecture at a glance

```
SEC EDGAR, Yahoo, Massive.com, USASpending, Nasdaq Trader, OpenFIGI
        | nightly pass after the close (cron sidecar)
        v
ingest --> Postgres: append-only, bitemporal facts --> call assembler (pure) --> calls log
                |                                              |
                v                                              v
     DuckDB/Parquet mirror                    FastAPI --> React app, MCP agent tools
     (replay, backtests, editions)
```

| Layer | What is used |
|---|---|
| Database | PostgreSQL 16 through psycopg 3; migrations in `backend/db/migrations/` |
| Replay and backtests | DuckDB over Parquet mirrors (the `replay` extra) |
| Backend | Python 3.11+, FastAPI, Pydantic, httpx; one API worker |
| LLM | Anthropic API through `backend/llm` (five modules); never sources a number |
| Agent tools | MCP server `backend/agent_tools`: local stdio, and `/mcp` in the API when `ALPHADECK_PUBLIC_ORIGIN` is set; 7 prompts for any user plus the steward's 5 Admin reviews; `read_dashboard` covers 8 dashboards |
| Frontend | TypeScript, React 18, Vite, Tailwind, TanStack Query, `lightweight-charts`; API types generated from `backend/openapi.json` |
| Ops | Docker Compose: nginx serves the app and proxies the API, a cron sidecar runs the nightly |

Prod is the local Compose stack on the operator's machine; the server move has not happened. Other stacks run beside
it ([OPERATIONS](docs/OPERATIONS.md)). Longer path: [DATA_FLOW](docs/DATA_FLOW.md).

| Stack | App / API / Postgres | Nightly cron |
|---|---|---|
| prod | 8080 / 8000 / 5544 | on |
| dev | 8081 / 8001 / 5545 | off |
| fork (one per experiment) | slot N: 808N / 800N / 554(4+N) | off |
| sig (replay lab) | 8082 / 8002 / 5546 | off |

## Repo map

- `backend/`: `app/` API and wire contract; `domain/` typed core and `CallConfig`; `calls/` the assembler;
  `signals/` detectors; `ingest/`, `securities/`, `platform_values/` data in; `db/` store and migrations;
  `pipeline/` nightly pass and repair CLIs; `scoreboard/`, `editions/`, `backtest/`, `replay/` scoring;
  `dashboards/`, `workbench/`, `radar/`, `notify/`, `llm/`, `agent_tools/`; `tests/`.
- `frontend/src/`: `board/`, `cockpit/`, `workbench/`, `scoreboard/`, `dashboards/`, `admin/`, `backtest/`, `radar/`,
  `settings/`, `auth/`, `oauth/`, `api/`.
- `docs/`: below, with `docs/dashboards/` (one doc per dashboard). `scripts/`: deploy, forks, venv, docs checker.
  `infra/`: prod's database-only compose file. `.claude/`: skills and the builder agent. Root: `docker-compose*.yml`,
  `Makefile`, `.env*.example`, [CLAUDE.md](CLAUDE.md) (agent rules).
- Local only, gitignored: `.env*`, `backend/.venv/`, `data/`.

## Run and test

- **Dev stack** (an isolated database beside prod; from the main checkout, never a worktree): `make dev-up`, or the
  compose form in [OPERATIONS](docs/OPERATIONS.md); steps in the `dev-stack` skill.
- **Backend:** `bash scripts/ensure-venv.sh` once per checkout. From `backend/`, with that venv:
  `python -m pytest tests/<area> -n 6 -m "not slow"`, `python -m ruff check .`, `python -m black --check .`. Run
  the full suite for work on the point-in-time path (`test-verify` skill). Under pytest, `db/testdb.py` selects a
  per-worktree `alphadeck_test_*` database; DB tests skip when Postgres is unreachable.
- **Frontend:** from `frontend/`: `npm ci`, `npm run dev` (proxies `/api` to 127.0.0.1:8000), `npm test`,
  `npm run build`, `npm run gen:api` after a contract change.
- **Warning:** `backend/db/session.py` defaults to the prod database (localhost:5544). A host command without an
  explicit `DATABASE_URL` reads and writes prod. Prod changes go through `scripts/deploy.sh`, never a bare
  `docker compose up`.

## Docs

| Doc | Owns |
|---|---|
| [ROADMAP](docs/ROADMAP.md), [INVARIANTS](docs/INVARIANTS.md) | Open work and the trust state; the fourteen rules |
| [GLOSSARY](docs/GLOSSARY.md), [CLI](docs/CLI.md), [DOCS_STANDARD](docs/DOCS_STANDARD.md) | Terms; every `python -m` entry point; how to write docs |
| [DISCOVERY](docs/DISCOVERY.md), [WORKBENCH](docs/WORKBENCH.md), [WORKBENCH_SCORING](docs/WORKBENCH_SCORING.md), [EXTRACTOR](docs/EXTRACTOR.md) | Finding names; the user's flow; scoring a member; reading filings |
| [BOARD](docs/BOARD.md), [DASHBOARDS](docs/DASHBOARDS.md) | Board, Cockpit, call card; dashboard rules (each dashboard: `docs/dashboards/`) |
| [CALL_LOGIC](docs/CALL_LOGIC.md), [DETECTORS](docs/DETECTORS.md), [DISPLAY_SIGNALS](docs/DISPLAY_SIGNALS.md) | Verdict, grade, `exit_by`; each detector; read-only indicators |
| [FILINGS](docs/FILINGS.md), [DATA_SOURCES](docs/DATA_SOURCES.md), [DATA_FLOW](docs/DATA_FLOW.md) | Stored filing families; vendors and caches; a fact's path |
| [FEED_LOOP](docs/FEED_LOOP.md), [INGEST](docs/INGEST.md), [FEED_MONITORS](docs/FEED_MONITORS.md), [CRON_SIDECAR](docs/CRON_SIDECAR.md) | The nightly pass; ingest legs; monitors; schedules and backups |
| [PLATFORM_VALUES](docs/PLATFORM_VALUES.md), [PLATFORM_EXTRACTION](docs/PLATFORM_EXTRACTION.md), [VALUE_FLAGS](docs/VALUE_FLAGS.md), [FUND_RADAR](docs/FUND_RADAR.md) | Shared values; background extraction; user flags; fund launches |
| [SCOREBOARD](docs/SCOREBOARD.md), [EDITIONS](docs/EDITIONS.md), [BACKTEST](docs/BACKTEST.md), [REPLAY](docs/REPLAY.md) | The record and its scoring; frozen re-runs; backtests; the replay engine |
| [OPERATIONS](docs/OPERATIONS.md), [ADMIN](docs/ADMIN.md) | Stacks, deploys, first run; the steward's Admin page |
| [AUTH](docs/AUTH.md), [SECURITY](docs/SECURITY.md), [TENANT_SCOPES](docs/TENANT_SCOPES.md), [CONNECTORS](docs/CONNECTORS.md), [AGENT_TOOLS](docs/AGENT_TOOLS.md) | Sign-in and allowances; output rules and limits; the two scopes; OAuth for agents; MCP tools |

`docs/archive/` holds records of one-time runs (the 2026-07 cache-freeze postmortem, the backtest pass verdicts);
they are never updated. After editing any Markdown, run `python scripts/check_docs.py` from the repo root.

## Status

The nightly record has accrued since 2026-07-10. No dial has been fitted to outcomes, and setup strength is
experimental. The full trust state, open work and known defects: [ROADMAP](docs/ROADMAP.md).
