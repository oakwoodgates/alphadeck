# Backend rules

Rules for work under `backend/`. The root `CLAUDE.md` safety rules and invariants apply here too.

## Working here

Run from `backend/` with this checkout's venv (`bash scripts/ensure-venv.sh` from the repo root, once per checkout). Type hints throughout; `ruff` and `black`. `unrecognized arguments: -n` means a lean venv: run the helper, never drop `-n`.

## Tests

- Scoped: `python -m pytest tests/<area> -n 6 -m "not slow"`. Full: `python -m pytest -n 6` (CI runs `-n 4`). Run the full suite, `slow` included, on every iteration for a change under `db/bitemporal.py`, `db/public_clock.py`, `signals/`, `calls/`, `replay/`, `backtest/`, `editions/` or `pipeline/core.py`: the `slow` sweeps are the only day-by-day, zero-forward-knowledge runs of the real pipeline. Run it once before the PR in every case.
- Never two pytest runs at once in one worktree: they derive the same databases and collide. After a killed or piped run, look for an orphaned `python.exe` before re-running.
- pytest-timeout (120 s, `thread` method) is the only hang guard; `slow` tests carry 300 s. A `+++ Timeout +++` dump is a hang: report it verbatim. A dispatched agent runs the suite in the foreground with an explicit 600000 ms timeout and splits the run by path if it could outlast it.
- Bash-subprocess tests can fail on Windows hosts; CI is the check. Report the summary line as it printed, failures included.
- Tests are point-in-time: a signal or backtest test fixes a timestamp and asserts no future leakage. Lookahead in a test is a failing test.
- Procedure: the `test-verify` skill. Stale test databases: the `prune-stale` skill.

## Package map

| Package | Holds |
|---|---|
| `app/` | The FastAPI app: `routers/`, wire models in `schemas_api.py`, `auth.py`, `deps.py` (`get_workspace`), allowances, workspace caps, `openapi_export.py` |
| `domain/` | The typed core: `Thesis`, `CallCard`, `SignalEvent`, `CallConfig` (every dial), `Settings`, `scopes.py`, `market_time.py`, `market_cap.py`, `rule_log.py` |
| `calls/` | The pure assembler (`assemble_call`), grading, the setup-strength cap, the counter-case |
| `signals/` | Detectors, pure functions of point-in-time data, and their registry; `display/` holds read-only indicators |
| `ingest/` | Fetch and parse: `edgar/` (client, Form 4, 8-K, 13D/G, full-text search, extractor), `prices/`, `funds/`, `doe/`, `sec_budget.py` |
| `securities/` | The security master, the company-list refresh, identity, `business_type/` |
| `platform_values/` | Share count and cash and burn, one value per company: the state read, the machine writer, the steward writer |
| `db/` | Migrations, bitemporal as-of reads, `public_clock.py`, `session.py`, `testdb.py`, write locks, change stamps |
| `repositories/` | Row to domain mapping and every table's writers |
| `pipeline/` | The nightly pass (`daily.py`), assembly from point-in-time facts (`core.py`), onboarding, `users.py`, repair and backfill CLIs |
| `scoreboard/`, `editions/` | The scored record; frozen re-runs of today's rules (engine, cut, nightly page) |
| `backtest/`, `replay/` | Immutable runs and sweeps; the engine under them (as-of harness, mirror export, episodes) |
| `dashboards/` | One compute module per dashboard, served from `app/routers/dashboards/` |
| `workbench/` | Discovery, the term set, the chain draft and member scoring |
| `radar/`, `notify/` | Fund and SPAC radars, outside the call path; Slack delivery, push policy, the escaper |
| `llm/`, `agent_tools/` | The model seam: `client.py` (`LLMClient`, behind `allow_live` like `EdgarClient`) and five modules; the read-only MCP server |

## Backend conventions

- **Core objects first.** `Thesis`, `SignalEvent` and `CallCard` are the contract with the frontend; they forbid unknown fields (`domain/base.py`).
- **Signals are pure:** `f(point_in_time_data) -> SignalEvent`, no side effects, no network, no implicit "now". Display signals stay off the call path (`docs/DISPLAY_SIGNALS.md`).
- **The security master is canonical.** Resolve every entity to it (CIK, ticker, CUSIP, FIGI) at ingest; handle corporate actions; avoid survivorship bias.
- **Every table is in the scope map.** A table with `tenant_id` goes in `SCOPE_OF`, one without in `UNSCOPED_TABLES` (`domain/scopes.py`); reads name their scope through `tenants_for`; the poison-row proof is `tests/db/test_tenant_isolation.py`. A request's workspace is `app.deps.get_workspace`; a thesis in another workspace is a 404. `DEFAULT_TENANT_ID` (tests and the seed) is the same id as `DATA_TENANT_ID`; demo cards go to `FOUNDING_WORKSPACE_ID` (`docs/TENANT_SCOPES.md`).
- **Platform values have one reader and two writers.** Read through `platform_values/state.py` (`read_states` for many companies, never a loop over `read_state`); write only through `platform_values/machine.py` or `steward.py`, which take the company write lock and re-read under it (fetch before locking). Guards: `tests/dashboards/test_one_pick.py`, `tests/ingest/test_platform_value_writers.py`, `tests/app/test_card_count_guard.py`; the allowed exceptions: `docs/PLATFORM_VALUES.md` "Who writes".
- **A counting thesis catalyst has one writer,** `repositories/thesis_catalyst_repo.py`, which takes no time from a caller. No module under `llm/` or `agent_tools/` reaches it (`tests/repositories/test_thesis_catalyst_writers.py`).
- **A writer that changes a company's stored data outside its write lock calls `db.change_stamp.mark_changed`** in the same transaction, or the dashboards serve that company stale (`docs/DASHBOARDS.md` rule 7).
- **A mirror export read has a total order** (`replay/export.py`, `_EXPORT_ORDER`), or the mirror hash depends on Postgres's plan (`docs/BACKTEST.md` "What the mirror hash promises").
- **Request limits live once, in `domain/text_types.py`, only on request models and query params.** A limit on a model built from stored rows makes one oversized value unloadable. Bound nested input with a request-side subclass (`BasketMemberIn`); `tests/app/test_request_bounds_guard.py` fails on an unbounded field.
- **Every route is gated** with `require_user` or `require_super_admin`; a steward route outside `/admin` and `/backtest` joins `STEWARD_PATHS` in `tests/app/test_auth_gate.py`. A response field that holds a URL is typed `HttpUrlOut` (`domain/links.py`; `tests/app/test_url_fields_guard.py`).
- **Slack text goes through `notify.post_text`,** which escapes `& < >`; flatten each interpolated value with `one_line` (`notify/escape.py`).
- **A recurring fetch stays fresh by key class, never by a per-call flag.** Prices pass `force_refresh=True`; a recurring EDGAR client is built with `RECURRING_CACHE_TTL_S`; companyfacts re-pulls through `companyfacts_gate` (`docs/DATA_SOURCES.md` "Cache freshness").
- **SEC requests share one global budget** (`ingest/sec_budget.py`, held in Postgres, fair-shared by lane; a 429 pauses every process). `ALPHADECK_INGEST_FETCH_CONCURRENCY=1` turns the concurrent fetch off. A declared User-Agent, a polite rate and an aggressive cache are correctness requirements (`docs/DATA_SOURCES.md` "EDGAR etiquette").
- **A new setting or default changes compose too:** a Setting reaches a container only through its service's `environment:` list, and a compose fallback equals the Settings default (`tests/domain/test_settings.py`).
- **Never edit an applied migration,** even a comment.

## LLM key

`ANTHROPIC_API_KEY` goes in the gitignored `.env`; compose injects it. It is needed only for live drafts: without it every `llm/` module fails open (no draft; the app works). The SDK is imported lazily, so the suite never needs a key.

## Operator CLIs

`python -m <module> --help` from `backend/`. A repair or backfill tool is a dry run by default, and `--apply` on prod is the operator's. The index: `docs/CLI.md`; the repair tools: the `data-repair` skill.
