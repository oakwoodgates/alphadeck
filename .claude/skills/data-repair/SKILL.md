---
name: data-repair
description: >-
  Use when stored data needs a repair or backfill CLI (`pipeline.repair_*`, `backfill_*`,
  `relink_amendments` and the rest). Holds the shared contract, one line per tool and the checks
  before any write; a write on prod is the operator's.
---

# Data repair

The `backend/pipeline/` CLIs that rewrite stored data after a fix, or fill history the nightly misses.
Flags: each module's `--help` (read its argparse block).

## Guardrails

- Every run, a dry run too, connects to a database. An agent runs none without the operator's go, and
  never with `DATABASE_URL` unset: unset means prod (`db/session.py`). A prod write is the operator's.
- Back up first ([backup-restore](../backup-restore/SKILL.md)). A deleting tool's only undo is a restore.
- The usual contract: dry run by default; `--apply` writes under the repair lock (`pipeline/run_lock.py`:
  the call-of-record lock, then the fact-writer gate); a held lock means nothing written, exit 75; most
  writes check their counts before committing, and each appends a `data_repair` journal row.
- Where tools differ:
  - `--execute`, not `--apply`: `backfill_accepted`, `backfill_aff10b5_1`.
  - Write by default, `--dry-run` previews: `backfill`, `resolve_price_symbols`, `repoint_canonical`. No
    preview: `backfill_fund_shares`, `backfill_surfaced_terms`. `repair_split_basis --revoke` writes at
    once.
  - Call-of-record lock only (exit 75): `backfill`, `backfill_splits`, `detect_splits`. `split_history`
    takes the onboarding job's lock and refuses in the nightly window or beside a pass.
    `backfill_fund_shares` takes company locks. No lock: `backfill_surfaced_terms`,
    `resolve_price_symbols`, `repoint_canonical`.
  - Refuse an unset `DATABASE_URL`: `dedup_identical_versions`, `repair_catalyst_dates`,
    `repair_impossible_txn_dates`.
- The journal (`repositories/data_repair_repo.py`): one row per write or revoke, even one that changed
  nothing (`split_history` only when it appended, as `backfill_splits`). It holds tool, action,
  `applied_at`, summary, counts, `rows_changed`, a deletes flag and a provenance path. A row that changed
  rows makes the next editions cut run a repair trial. No tool reads it to refuse a second run.

## Steps

1. Find the tool below; read its docstring and argparse block.
2. Has it run? No route or CLI lists the journal; the operator reads it (an agent only with the
   operator's explicit go) and checks `data/repairs/`:
   ```bash
   docker exec -e PGOPTIONS='-c default_transaction_read_only=on' alphadeck-postgres-1 psql -U alphadeck -d alphadeck -c "SELECT tool, action, applied_at, rows_changed, summary FROM data_repair ORDER BY applied_at"
   ```
   No row proves nothing: six tools never journal, and older runs predate the journal. Each tool's own
   guard is idempotency: once applied, a re-run plans nothing.
3. The operator takes a labeled backup, runs the dry run, and reviews counts, refusals and listed rows.
4. The operator runs the write. Exit 75 wrote nothing: retry after the holder finishes.

### The tools

One-off: fixes rows stored before a fixed defect; the ingest now writes them right. Reusable: re-applies a
current rule or fills a recurring gap.

| Module | Repairs | Kind | Journal | Owner |
|---|---|---|---|---|
| `backfill` | a missed night's calls | reusable | no | [FEED_LOOP] |
| `backfill_accepted` | Form 4 acceptance times | one-off; `--drift` reusable | yes | [DATA_SOURCES] |
| `backfill_aff10b5_1` | Form 4 10b5-1 flag | one-off | yes | [DATA_SOURCES] |
| `backfill_fund_shares` | ETF share history | reusable | no | [DATA_SOURCES] |
| `backfill_insider_holds` | impossible-date Form 4 holds | reusable | yes | [DATA_SOURCES] |
| `backfill_splits` | the split list | reusable | yes | [DATA_SOURCES] |
| `backfill_surfaced_terms` | members' discovery terms | one-off | no | [DISCOVERY] |
| `dedup_identical_versions` | deletes identical re-versions | one-off | yes | [INVARIANTS] |
| `detect_splits` | the nightly split check | reusable | no | [DATA_SOURCES] |
| `rederive_amendment_matches` | legacy Form 4/A decisions | one-off | yes | [DATA_SOURCES] |
| `reingest_fundamentals` | revenue, current label rule | reusable | yes | [CALL_LOGIC] |
| `relink_amendments` | Form 4/A links | reusable | yes | [DATA_SOURCES] |
| `repair_activist_misattribution` | deletes self-filed 13D/G | one-off | yes | [DATA_SOURCES] |
| `repair_adr_insider_misattribution` | Form 4 ADR titles | one-off | yes | [DATA_SOURCES] |
| `repair_benchmark_bars` | named price bars | reusable | yes | [FEED_LOOP] |
| `repair_catalyst_dates` | DOE award dates (deletes) | one-off | yes | [DATA_SOURCES] |
| `repair_filing_dates` | dates by SEC acceptance | one-off | yes | [DATA_SOURCES] |
| `repair_impossible_txn_dates` | deletes impossible-date Form 4 | one-off | yes | [DATA_SOURCES] |
| `repair_insider_filer_text` | Form 4 filer text | one-off | yes | [DATA_SOURCES] |
| `repair_insider_ownership` | Form 4 ownership evidence | one-off | yes | [DATA_SOURCES] |
| `repair_shares_cover_dates` | typo cover dates | one-off | yes | [PLATFORM_VALUES] |
| `repair_split_basis` | bars onto the split scale | reusable | yes | [FEED_LOOP] |
| `repoint_canonical` | members on a non-primary sibling | one-off | no | [DISCOVERY] |
| `resolve_price_symbols` | OTC price symbols | reusable | no | [DATA_SOURCES] |
| `set_aside_fundamentals` | screened-out revenue rows | reusable | yes | [CALL_LOGIC] |
| `split_history` | a joiner's split history | reusable | yes | [DATA_SOURCES] |

## Checks

- The write printed `journal: data_repair <id>` (journaling tools) and counts that match the plan.
- A second dry run plans nothing.

## Stop and ask when

- The target is prod, or `DATABASE_URL` is unset or not the database you mean.
- A one-off tool already has an `apply` row.
- The dry run exceeds the docstring's scope or lists refusals or mismatches.
- The tool is `dedup_identical_versions`: by default it deletes in every tenant, and the platform-value
  writer guard test marks it not V2-aware.

## Model

Why the journal matters, the editions cut's repair trial: [SCOREBOARD](../../../docs/SCOREBOARD.md).

[FEED_LOOP]: ../../../docs/FEED_LOOP.md
[DATA_SOURCES]: ../../../docs/DATA_SOURCES.md
[DISCOVERY]: ../../../docs/DISCOVERY.md
[INVARIANTS]: ../../../docs/INVARIANTS.md
[CALL_LOGIC]: ../../../docs/CALL_LOGIC.md
[PLATFORM_VALUES]: ../../../docs/PLATFORM_VALUES.md
