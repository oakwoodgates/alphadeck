# Alpha Deck: agent rules

Alpha Deck is decision support for a discretionary trader who reads narratives early and times entries badly: opinionated about timing, deferential about the thesis. It is advisory only; the firm's order, sizing and risk systems own the trade. Platform data is shared and curated by a steward; theses, baskets, decisions and calls are private to each user's workspace.
Read `README.md` first (what it is, the six stages, the docs table). This file is how to build it safely. `backend/CLAUDE.md` and `frontend/CLAUDE.md` load when you work under those folders; the skills list is automatic. Where this file and the owning doc disagree on a detail, the doc wins: say so.
Prod runs as the local Compose stack on the operator's machine. V2 is live on `main`. The editions nightly is on.

## Safety rules

Stop and ask if one of these blocks you.

- **No database connection outside pytest.** `backend/db/session.py` defaults `DATABASE_URL` to the PROD database (`localhost:5544/alphadeck`; dev is 5545). A CLI, script or REPL run with it unset reads and writes prod.
- **Tests run on their own database.** pytest picks a per-worktree `alphadeck_test_<hash>` (hook in `backend/tests/conftest.py`, derivation and guard in `backend/db/testdb.py`) and refuses any other name. Never point `DATABASE_URL` at a real database to make tests run. Pin `ALPHADECK_TEST_DB` only for a serial run, never with `-n`.
- **Skipped is not passed.** Database tests skip when Postgres is unreachable. A "tests pass" claim quotes the summary line and shows them executed (`test-verify` skill).
- **One venv per checkout.** Run `bash scripts/ensure-venv.sh` (or `make venv`) in every worktree. Never borrow a sibling's or the main checkout's venv.
- **Prod and dev stacks run only from the main checkout.** A worktree never runs one (a fork or sig stack runs from its own worktree, through `scripts/fork.sh`). Never a bare `docker compose up`; never `docker compose down -v` or `DROP DATABASE` against prod; `infra/docker-compose.yml` shares prod's database volume. A deploy changes data, because the boot runs the migrations. Procedures: `docs/OPERATIONS.md` and the `dev-stack`, `fork` and `stack-recovery` skills.
- **Only prod runs the nightly.** Every other stack has the cron off, and all stacks share one outbound address and SEC's per-host limit.
- **A deploy, a restore and any `--apply` of a repair or backfill CLI on prod are the operator's**, each after a backup (skills `deploy`, `backup-restore`, `data-repair`). `mcp__alphadeck-prod__*` is the operator's record: read-only. The `pipeline.v2_cutover` CLI is the operator's too; an agent never runs it outside the test suite.
- **Recover lost history by restoring a backup, never by re-ingesting.**
- **Nothing deletes from the `calls` log or the editions store.** No migration, repair or cutover path may.
- **Never renumber or rename a migration.** `db/migrate.py` records applied file names, so a renamed file runs again. A new one is numbered after the highest file in `backend/db/migrations/`.
- **A Setting reaches a container only through the `environment:` list of its service in the compose files.**

## Invariants

Full text, reasons and enforcing tests: `docs/INVARIANTS.md`. Code cites them as `invariant #N`. Never renumber or reuse a number; the former-number table in that file decodes an old cite. If a request seems to require violating one, stop and flag it.

1. **No lookahead.** History is judged on the public clock: a filing counts from its SEC acceptance, a bar from its settled session, a correction from its own filing. A night runs from a session's close to EDGAR's next open (06:00 ET on the next weekday). `recorded_at` is bookkeeping, never a replay axis.
2. **Every feature attaches to a thesis.** No orphan screeners or standalone tools.
3. **The model augments; it never sources a number or a firing.** Triggers fire from deterministic code and trace to a computation.
4. **Opinionated on timing, deferential on the thesis.** Never rank or veto a thesis on conviction. Expression text is advisory research context.
5. **Advisory only.** No orders, brokerage writes, execution, sizing or portfolio risk. The gate withholds the go-signal and logs an override; it never blocks the user.
6. **Explainability.** Every call, score and signal carries its evidence and provenance. If you cannot show the work, do not surface the result.
7. **Inverse loudness.** Quiet when early; loud only for Armed, in notifications as well as on screen.
8. **The dilution overhang is computed once** (`signals/dilution_clock.py`); the risk veto and the Workbench meter share it.
9. **Recall is sacred.** Discovery over-includes; a silently dropped name is a system failure. A change to discovery, classification, filters, caps or term tiers re-scores the answer key (`recall-rescore` skill).
10. **The model recommends; a person decides.** A recommendation is visible and pending, and changes nothing until a person confirms it.
11. **Entity resolution is an exact allowlist.** Fuzzy search may discover; it never decides a mapping. An unknown entity is dropped, never guessed.
12. **Two tenant scopes; every read names its scope** (`domain/scopes.py`: DATA is shared, WORKSPACE is one per user). Never build a destructive reset path or assume a single tenant.
13. **The call assembler is pure and deterministic.** No database, network or ambient clock inside it; "today" comes from `domain/market_time`.
14. **Behavior keys on the property that drives it** (grade, horizon, the weaker key), never on a signal kind.

## Interaction principles (WB#1 to WB#3)

Rules for the user-facing editor, separate from the invariants: invariants protect trust, these protect workflow. Code cites them as WB#N. Full text and the test for new editor work: `frontend/CLAUDE.md`.

- **WB#1 Reversibility.** Every user action has a visible inverse; none strands the user.
- **WB#2 Keep it visible.** Pruning hides and grays a name; it never deletes it or makes it vanish.
- **WB#3 Honest loudness.** Loudness marks the exception, not the rule; a badge true of every row is noise.

## How to work

- **Propose, don't assume.** Separate what the docs specify from what you propose. Put assumptions to the operator as open questions; never call your own choice a decision already made. A shorter plan that names what it still needs beats a polished one built on guesses.
- **The call logic is the product.** How signals compose into state, verdict, grade, expression and `exit_by` lives in `docs/CALL_LOGIC.md` (what each detector fires: `docs/DETECTORS.md`) and is golden-tested. Where a rule is not specified, propose it for the operator's sign-off before building: that is where the user's edge lives. A call-logic change appends an entry to `backend/domain/rule_log.py` in the same PR; the editions cut reads it.
- **Build the smallest thing that proves the loop.** No new datastore, orchestrator or streaming layer without the operator's sign-off. The invariants are non-negotiable; process ceremony is not.
- **Out of scope; flag, don't silently build:** trade execution and brokerage integration; position sizing and portfolio risk; intraday or streaming data; options-gamma and paid borrow data; crypto, commodities and prediction markets; teams or shared workspaces, user-login SSO, row-level security and impersonation (a private workspace per user is built, and so is OAuth sign-in for agent connectors: `docs/AUTH.md`, `docs/TENANT_SCOPES.md`, `docs/CONNECTORS.md`); a separate graph database.

## Conventions lint and tests do not catch

- **American English everywhere:** UI copy, comments, docstrings, docs (`favorable`, `color`, `gray`, `analyze`, `canceled`). Nothing lints it, so check every string a person reads. Never respell an identifier or a value that crosses a boundary (a wire or enum key, a DB column, a CSS class) or quoted external content (SEC fixtures, vendor field names). A repo-wide spelling sweep is its own change.
- **Write the vendor as Massive.com** in copy, comments, docs, errors and CLI output; the key is `MASSIVE_API_KEY`. Keep legacy identifiers (`polygon_*`, `ingest/polygon_splits.py`, `'polygon'`, `ALPHADECK_POLYGON_BASE`), cache paths, recorded fixtures and applied migrations. Repoint the `api.polygon.io` host only after a live check. A change to recorded CallCard wording in `domain/split_hold._sources` moves the editions fingerprint and belongs in the rule log.
- **LLM calls go through `backend/llm` only.** Prompts and structured-output schemas live with that module, responses carry citations, and a model never sources a number.
- **The OpenAPI contract is generated; regenerate it in the same PR** as anything FastAPI emits into it (a route, a model, a status code, a route docstring): `python -m app.openapi_export`, then `npm run gen:api`. CI diffs both files (`contract-sync` skill). Edit generated files at their source: never hand-edit `backend/openapi.json`, `frontend/src/api/types.gen.ts` or the recipe blocks of the `alphadeck-agent` skill (change `backend/agent_tools/prompts/`, then `python -m agent_tools.skill_sync --write`).
- **Bitemporal writes append.** Never `UPDATE` a fact that has temporal meaning; insert a new version (`valid_from`, `valid_to`, `recorded_at`). `recorded_at` is stamped now, never backdated.
- **Idempotency tests count the table, not the read.** The as-of read dedups, so a duplicate append hides behind a correct read: assert `count(*)` or the `list_*` length before and after a re-run.
- **Event-outcome reports are sliced by MARKET-CAP TIER; a pooled number never stands alone.**
  - Scope: any report on what happened to companies after an event (a split, an insider buy, a filing, a gap, an arm). Micro caps behave differently from majors.
  - **The bands (fixed):** **nano** < $50M · **micro** $50M–$300M · **small** $300M–$2B · **mid** $2B–$10B · **large** ≥ $10B.
  - Measured at the event: the as-traded close before it times the last share count reported before it, on the same basis (never a split-adjusted price with a pre-split count; divide by the ADS ratio for an ADR). Label it approximate; leave it blank when it cannot be pinned down, and count the blanks. Add a liquidity cut (20-day average dollar volume) where it helps.
  - Every number states, per tier, its horizon, its comparison (for example minus IWM, or the random-day null), n and how often it held. A tier with n < 10 is too small to read. Never claim a signal "works" without all four.
  - A tier is a slicing dimension for analysis and display only, never a call input (invariant 4). Define the bands once, in `backend/domain/market_cap.py`. Presentation: `docs/DASHBOARDS.md` rule 6.
- **Commit messages go through a quoted heredoc** (`git commit -F - <<'MSG' ... MSG`): backticks in `-m "..."` get evaluated.
- **Merge commits only.** Work of several PRs lands through an integration branch; an existing stack merges top first. After any merge, check that main holds the commits: the "Merged" badge proves nothing (`land-pr` skill).
- **Docs follow `docs/DOCS_STANDARD.md`;** `python scripts/check_docs.py` must pass.

## Naming guards

| Term | Means | Never |
|---|---|---|
| conviction | signal conviction, the deterministic keys in `calls/` | `BasketMember.conviction`, a user's stored 1 to 5 weight; wiring it into a call breaks invariant 4 |
| role | `SignalEvent.role` (`entry_trigger`, `risk_signal`) | `BasketMember.role` (prose), `context_role` (a company's part on one card), a user's role |
| flag | a value flag: a user's report on a platform value | the extractor's FLAG tier, a candidate's `outcome='flag'`, a data flag |
| gate | the call card's advisory gate | the sign-in gate, the n >= 5 gate, the fact-writer gate |
| watch | `CallCard.watch_members`, the confirmation-only members | a silent link (Context, ETFs, Bench) or a meta index card |
| workspace | a user's private tenant | a basket's `segment`, a value-chain link |
| recommendation, sign-off, authorship | a recommendation changes nothing until a person confirms it; sign-off marks a name endorsed; `authored_by` flips to `operator_edited` only when the user edits the words | one standing in for another; sign-off is never a gate or a call input |
| user, steward, operator | a plain user; the super admin who curates platform data; the person who runs the deployment | "operator" for a plain user |
| V2 | the SaaS release | a feature's version; say "not built" |

Full glossary: `docs/GLOSSARY.md`.

## Everyday commands

Stdlib venv and pip, no `uv`. From `backend/`, run every tool through this checkout's venv (`.venv/Scripts/python` on Windows).

```bash
bash scripts/ensure-venv.sh                      # once per checkout (PowerShell: .\scripts\ensure-venv.ps1)
python -m pytest tests/<area> -n 6 -m "not slow"  # scoped; the full suite and when to run it: backend/CLAUDE.md
python -m pytest -n 6                             # the full suite
python -m ruff check . ; python -m black --check .
python -m app.openapi_export                      # then, from frontend/: npm run gen:api
python -m agent_tools.skill_sync --check          # after touching the alphadeck-agent skill or the MCP prompts
npm test ; npm run build                          # from frontend/
python scripts/check_docs.py                      # from the repo root, after any Markdown edit
```

Every `python -m` CLI, who runs it and whether it writes: `docs/CLI.md`. Flags: `python -m <module> --help`. Repair and backfill tools: the `data-repair` skill.

## Where to look

Frame new work by stage (README, Core concepts: SEED, DISCOVER, SURFACE, TRIAGE, MONITOR, SCORE).

| If you are changing | Read |
|---|---|
| A detector, verdict, grade or `exit_by` | `docs/CALL_LOGIC.md`, `docs/DETECTORS.md`, `docs/DISPLAY_SIGNALS.md` |
| A filing family, fetch client, cache or vendor call | `docs/FILINGS.md`, `docs/DATA_SOURCES.md`, `docs/INGEST.md` |
| The nightly pass, the cron sidecar, monitors | `docs/FEED_LOOP.md`, `docs/FEED_MONITORS.md`, `docs/CRON_SIDECAR.md` |
| Discovery, the Workbench, member scoring, filing extraction | `docs/DISCOVERY.md`, `docs/WORKBENCH.md`, `docs/WORKBENCH_SCORING.md`, `docs/EXTRACTOR.md` |
| Share counts, cash and burn, value flags | `docs/PLATFORM_VALUES.md`, `docs/PLATFORM_EXTRACTION.md`, `docs/VALUE_FLAGS.md` |
| The Board, the Cockpit, a cross-card dashboard | `docs/BOARD.md`, `docs/DASHBOARDS.md`, `docs/dashboards/<slug>.md` |
| The Scoreboard, editions, backtests, replay | `docs/SCOREBOARD.md`, `docs/EDITIONS.md`, `docs/BACKTEST.md`, `docs/REPLAY.md` (skill `backtest`) |
| Sign-in, allowances, tenancy, output rules, OAuth | `docs/AUTH.md`, `docs/SECURITY.md`, `docs/TENANT_SCOPES.md`, `docs/CONNECTORS.md` |
| The MCP agent tools (`alphadeck-prod`, `alphadeck-dev` in `.mcp.json`) | `docs/AGENT_TOOLS.md`, skill `alphadeck-agent` |
| Stacks, deploys, forks, recovery, the Admin page | `docs/OPERATIONS.md`, `docs/ADMIN.md`; skills `deploy`, `dev-stack`, `fork`, `stack-recovery`, `backup-restore`, `cron-health` |
| A term, open work, how to write docs | `docs/GLOSSARY.md`, `docs/ROADMAP.md`, `docs/DOCS_STANDARD.md` |

Ports (app / API / Postgres): prod 8080 / 8000 / 5544 with the cron on; dev 8081 / 8001 / 5545; fork slot N 808N / 800N / 554(4+N); sig 8082 / 8002 / 5546; cron off outside prod. The Vite dev server (:5173) proxies `/api` to 127.0.0.1:8000.
