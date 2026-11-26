# CLI index

Every `python -m` entry point under `backend/`: what it does, who runs it, whether it writes, and which doc or skill
owns it. Look here to find the tool for a job. Flags live in each module's `--help` and argparse block, never in a
doc. Run from `backend/` with the checkout's own venv (`bash scripts/ensure-venv.sh`).

## Rules for every CLI

- No CLI is a route. The gate is shell access to the stack (or the repo); the lock, window and refusal rules a
  tool enforces itself are in its owner doc. The API server (`uvicorn app.main:app`) is not a backend module and is
  not listed.
- `db/session.py` defaults to the **production** database, so a run with `DATABASE_URL` unset reaches prod. "DB"
  below means the database `DATABASE_URL` names. An agent opens no database connection outside pytest and runs a
  database CLI only on the operator's go, against a named non-prod URL. A write on prod is the operator's.
- "On apply" means a dry run by default: nothing is written until the write option is passed.
- "Machine" is the cron sidecar or the container's boot command. "Operator" is whoever has shell access to the
  stack. "Developer" is anyone working in the repo; a developer tool that connects to a database follows the rule
  above.

## Boot and schema

| Module | Does | Run by | Writes | Owner |
|---|---|---|---|---|
| `db.wait` | blocks until Postgres accepts a connection | machine (boot) | no | [OPERATIONS](OPERATIONS.md#restart-and-recovery) |
| `db.migrate` | applies pending migrations in name order | machine (boot); operator | DB schema | [OPERATIONS](OPERATIONS.md#migrations-and-restore) |
| `pipeline.seed` | seeds the demo cards; a no-op unless `ALPHADECK_SEED_ON_BOOT` is true | machine (every boot); operator | DB, only when on | [OPERATIONS](OPERATIONS.md#what-a-deploy-changes) |

## The nightly and the cron sidecar

| Module | Does | Run by | Writes | Owner |
|---|---|---|---|---|
| `pipeline.daily` | the nightly pass: refreshes shared facts, records each thesis's call | machine; operator by hand | DB | [FEED_LOOP](FEED_LOOP.md#cli-and-exit-codes) |
| `pipeline.schedule` | `is-session`: is a date a market session (pure) | machine (shell) | no | [CRON_SIDECAR](CRON_SIDECAR.md) |
| `notify.page` | sends the failure page when a night or a backup failed for good | machine (shell) | no (Slack) | [CRON_SIDECAR](CRON_SIDECAR.md#failure-handling) |
| `pipeline.backup` | takes, lists and prunes `pg_dump` snapshots | machine; operator | dump files, never the DB | [CRON_SIDECAR](CRON_SIDECAR.md#backups) |
| `editions.run` | `trial`, `publish`, `nightly`, `cut` over the editions engine | `nightly`: machine; `cut`, `publish`: operator; `trial`: operator or developer | `trial`: a run directory; the others: DB on apply | [EDITIONS](EDITIONS.md#trial-and-publish) |
| `pipeline.warm_dashboards` | reads each heavy dashboard's default view once per workspace | machine | a run artifact, no DB | [DASHBOARDS](DASHBOARDS.md#7-cache-warm-up-and-shared-readers) |
| `db.change_stamp` | prunes the per-company change log | machine | DB (deletes) | [CRON_SIDECAR](CRON_SIDECAR.md) |
| `db.housekeeping` | prunes expired sign-in rows | machine | DB (deletes) | [AUTH](AUTH.md#housekeeping-the-nightly-prune) |
| `pipeline.onboarding` | loads a cold company's filing history outside the nightly window | machine; operator | DB | [INGEST](INGEST.md#cold-names-onboarding) |
| `pipeline.populate_master` | refreshes the company list from SEC's ticker file | machine (weekly); operator | DB on apply | [DATA_SOURCES](DATA_SOURCES.md#company-list-and-identity) |
| `pipeline.term_mentions` | counts EDGAR filings mentioning each card's SIGNAL terms | machine (weekly); operator | DB on apply | [CRON_SIDECAR](CRON_SIDECAR.md#schedules) |

## Ingest and data

| Module | Does | Run by | Writes | Owner |
|---|---|---|---|---|
| `pipeline.ingest_thesis` | loads Form 4, 8-K, 13D/G, prices and fund shares for a card's basket | operator | DB | [INGEST](INGEST.md) |
| `pipeline.ingest_fundamentals` | loads quarterly revenue and share series for basket members | operator | DB | [INGEST](INGEST.md) |
| `pipeline.ingest_benchmarks` | loads the SPY and IWM tape | operator | DB | [INGEST](INGEST.md) |
| `pipeline.nightly_extraction` | the nightly pass's platform-extraction leg, standalone | operator | DB on apply | [PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md) |
| `pipeline.extract_values` | the platform-value backfill: extracts every due company, or re-extracts the steward's queue (the rescan) | operator | DB on apply | [PLATFORM_EXTRACTION](PLATFORM_EXTRACTION.md) |
| `pipeline.enrich_identity` | fills sector, status and category on master rows | operator | DB | [DISCOVERY](DISCOVERY.md#identity-enrichment) |
| `pipeline.fund_radar` | scans EDGAR for new fund filings; also prints a read-only report | operator (the nightly runs the same scan) | DB; the report no | [FUND_RADAR](FUND_RADAR.md#errors-cost-and-the-cli) |
| `pipeline.spac_radar` | scans EDGAR for SPAC events and matches them to term sets | operator (a nightly passenger leg) | DB | [FEED_LOOP](FEED_LOOP.md#the-pass-in-order) |
| `pipeline.spac_sweep` | enriches SPAC-structured companies' SIC so blank checks flag | operator (a nightly passenger leg) | DB | [FEED_LOOP](FEED_LOOP.md#the-pass-in-order) |
| `pipeline.audit_identity` | audits master health and basket identity coherence | operator | no | [INVARIANTS](INVARIANTS.md#11-entity-resolution-is-an-exact-allowlist) |

## People and purity

| Module | Does | Run by | Writes | Owner |
|---|---|---|---|---|
| `pipeline.users` | creates users, roles, tokens, workspace caps and OAuth clients | operator | DB | [AUTH](AUTH.md#the-cli-python--m-pipelineusers) |
| `pipeline.ratify_revenue_mix` | ratifies one purity fact in a named workspace | operator | DB (that workspace) | [TENANT_SCOPES](TENANT_SCOPES.md) |

## Research and records

| Module | Does | Run by | Writes | Owner |
|---|---|---|---|---|
| `pipeline.run` | assembles one card as of a date and appends a `calls` row, outside the run lock | operator or developer | DB (`calls`) | [FEED_LOOP](FEED_LOOP.md#known-gaps) |
| `scoreboard.run` | prints the scored record across every workspace | operator or developer | no | [SCOREBOARD](SCOREBOARD.md) |
| `editions.entry` | prints each card's join dates | operator or developer | no | [EDITIONS](EDITIONS.md#join-dates) |
| `replay.run` | exports a record-clock mirror and replays a window | operator or developer, replay extra | files in the output directory | [REPLAY](REPLAY.md) |
| `backtest.run` | one immutable backtest run | operator or developer, replay extra | the backtest store | [BACKTEST](BACKTEST.md) |
| `backtest.sweep` | one dial or a grid across a range, as a curve | operator or developer, replay extra | the backtest store | [BACKTEST](BACKTEST.md) |
| `backtest.rebench` | re-reads a finished run's excess both ways | operator or developer | an optional JSON file | [BACKTEST](BACKTEST.md) |
| `backtest.pair` | pairs two passes' episodes: timing versus selection | developer | no | [BACKTEST](BACKTEST.md) |
| `backtest.slice_nulls` | per-family null draws on a finished run | developer | an optional JSON file | [BACKTEST](BACKTEST.md) |

The backtest skill runs the five `backtest.*` tools ([backtest](../.claude/skills/backtest/SKILL.md)).

## Repair and backfill

Operator only; an agent runs none without the operator's go. They share one contract (dry run by default, a repair
lock, a `data_repair` journal, back up first) and the skill holds it with each tool's kind, journal and checks:
[data-repair](../.claude/skills/data-repair/SKILL.md). "On apply" and "on execute" name the write option; the rest
differ as the skill says.

| Module | Repairs | Writes |
|---|---|---|
| `pipeline.backfill` | a missed night's call | by default |
| `pipeline.backfill_accepted` | Form 4 acceptance times | on execute |
| `pipeline.backfill_aff10b5_1` | Form 4 10b5-1 checkbox | on execute |
| `pipeline.backfill_fund_shares` | ETF share-count history | always |
| `pipeline.backfill_insider_holds` | Form 4 rows held for an impossible date | on apply |
| `pipeline.backfill_splits` | the split list | on apply |
| `pipeline.backfill_surfaced_terms` | members' discovery terms | always |
| `pipeline.dedup_identical_versions` | byte-identical re-versions (deletes) | on apply |
| `pipeline.detect_splits` | new splits | on apply |
| `pipeline.rederive_amendment_matches` | legacy Form 4/A decisions | on apply |
| `pipeline.reingest_fundamentals` | revenue under the current label rule | on apply |
| `pipeline.relink_amendments` | Form 4/A links | on apply |
| `pipeline.repair_activist_misattribution` | self-filed 13D/G rows (deletes) | on apply |
| `pipeline.repair_adr_insider_misattribution` | ADR titles on Form 4 rows | on apply |
| `pipeline.repair_benchmark_bars` | named benchmark bars | on apply |
| `pipeline.repair_catalyst_dates` | DOE award catalyst dates | on apply |
| `pipeline.repair_filing_dates` | filing dates by SEC acceptance | on apply |
| `pipeline.repair_impossible_txn_dates` | impossible-date Form 4 rows (deletes) | on apply |
| `pipeline.repair_insider_filer_text` | Form 4 filer text | on apply |
| `pipeline.repair_insider_ownership` | Form 4 ownership evidence | on apply |
| `pipeline.repair_shares_cover_dates` | typo cover dates on share counts | on apply |
| `pipeline.repair_split_basis` | price bars onto the split scale | on apply; a revoke at once |
| `pipeline.repoint_canonical` | members on a non-primary sibling | by default |
| `pipeline.resolve_price_symbols` | OTC vendor price symbols | by default |
| `pipeline.set_aside_fundamentals` | screened-out revenue rows | on apply |
| `pipeline.split_history` | a new joiner's split history | on apply |

## Developer tools and the V2 cutover

| Module | Does | Run by | Writes | Owner |
|---|---|---|---|---|
| `app.openapi_export` | writes `backend/openapi.json` from the app | developer | that file | [contract-sync](../.claude/skills/contract-sync/SKILL.md) |
| `agent_tools` | the read-only MCP server over stdio (the package's `__main__`) | the agent's MCP client | no | [AGENT_TOOLS](AGENT_TOOLS.md) |
| `agent_tools.server` | the same server, started by module | the agent's MCP client | no | [AGENT_TOOLS](AGENT_TOOLS.md) |
| `agent_tools.skill_sync` | checks or rewrites the `alphadeck-agent` skill's recipe blocks | developer | the skill file, on write | [AGENT_TOOLS](AGENT_TOOLS.md) |
| `db.drop_test_dbs` | drops stale per-worktree `alphadeck_test*` databases; never `alphadeck` | developer, opt-in | drops test databases | [test-verify](../.claude/skills/test-verify/SKILL.md) |
| `pipeline.v2_cutover` | the V2 cutover: `plan` and `verify` read only; `move-platform-facts` and `run` write on apply | operator | DB on apply | [OPERATIONS](OPERATIONS.md#migrations-and-restore) |

## Known limits

- `pipeline.run` appends a `calls` row outside the call-of-record lock, and whether it should write at all is open
  ([FEED_LOOP](FEED_LOOP.md#known-gaps)).
- The V2 cutover has run, and `refresh-fork.sh` still calls `move-platform-facts`
  ([OPERATIONS](OPERATIONS.md#known-limits)).

## Related

[OPERATIONS](OPERATIONS.md), [CRON_SIDECAR](CRON_SIDECAR.md), [FEED_LOOP](FEED_LOOP.md),
[data-repair](../.claude/skills/data-repair/SKILL.md), [DOCS_STANDARD](DOCS_STANDARD.md) (rule 14: a doc lists a CLI
once, in one line).
