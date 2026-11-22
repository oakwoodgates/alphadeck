# Data flow: where data lives and how a fact becomes a call

This doc traces a fact from its source to a recorded call: what is a file, what is a database row, and what
is recomputed on each read. Read it before you delete anything under `data/`, add a fact table, or change
which reads recompute and which read stored output. How the nightly pass moves data: [FEED_LOOP](FEED_LOOP.md);
how a call is made: [CALL_LOGIC](CALL_LOGIC.md).

## The path

1. **Source.** SEC EDGAR (filings, the company file, full-text search), Yahoo (end-of-day prices), Massive.com
   (splits, fund share counts), OpenFIGI (identity), USASpending and the Nasdaq Trader lists. Vendors and their
   rules: [DATA_SOURCES](DATA_SOURCES.md).
2. **Cache.** A live pull goes through a raw-response cache under `data/` (`edgar_cache/`, `price_cache/`,
   `figi_cache/` and others). It is a polite, reproducible mirror, never the source of truth, and how long each
   key stays fresh is DATA_SOURCES "Cache freshness".
3. **Fact table.** Ingest writes `fact_*` rows: append-only and bitemporal (a `no_update` trigger), stamped
   with the database clock and dated by when the fact became public (invariant 1). The per-company legs:
   [INGEST](INGEST.md).
4. **Read.** A read for a past as-of judges facts on the public clock (`db/public_clock.py`, `serve_view`): only
   what was public by the end of that night.
5. **Call.** The detectors re-derive the dated signals from those facts and the pure assembler builds the
   `CallCard` (invariant 13). No model touches a number or a signal (invariant 3).
6. **Record.** The nightly pass appends the call of record to `calls` when the card changed
   (`record_if_changed`).
7. **Replay.** Editions and backtests re-run calls on the public clock over a frozen mirror and store their
   output in their own tables ([REPLAY](REPLAY.md), [SCOREBOARD](SCOREBOARD.md)).

## Tenancy

One `tenant_id` column, two scopes, mapped once in `domain/scopes.py` ([TENANT_SCOPES](TENANT_SCOPES.md),
invariant 12):

- **DATA**, one shared tenant: the company master and its ticker, split and CIK history, every ingested fact,
  the platform's share counts, cash and catalysts, the onboarding queue, the feed repairs and decisions, and the
  repair journal. The feed writes it; every workspace reads it.
- **WORKSPACE**, one per user: the thesis and its basket, the calls of record, the editions store, annotations,
  and the workspace's own judgments such as purity. Read and written in that workspace only.

## Read modes

| Read | Route | What it does |
|---|---|---|
| Recompute | `GET /theses/{id}/call?asof=` | Re-derives signals and assembles the card. Writes no call or fact row |
| Record | `GET /theses?with_call=true&asof=` | Returns the call of record in effect at that date (greatest `(asof, seq)` on or before it); `null` means no record. Never reassembles |
| Edition | The Scoreboard | Reads stored re-runs; the newest published edition by default ([EDITIONS](EDITIONS.md)) |
| History | Any past as-of | The public clock, as above |

There is no separate signal-firing store: signals are recomputed on every read, and a recorded card keeps what
the system said with its provenance. Signing in may update session bookkeeping; the no-write rule is about the
call computation. Both routes need any signed-in user and read that user's workspace (`require_user` on the
theses router, `app/main.py`; `tests/app/test_auth_gate.py`).

## Write rules

| Data | Writer | Rule |
|---|---|---|
| `fact_*` | The ingest legs, the seed, steward decisions | Append-only and bitemporal; a correction is a new version, never an overwrite |
| `security_master` | Resolve, the company-list refresh, steward repairs | Mutable identity. A rename updates the row in place and appends to `security_ticker_history` (migration 0066), so every fact stays attached |
| Thesis, basket, term set, evidence | The Workbench promote and edits, the seed | Operational; the promote writes the basket and rejects any placed id that is not an exact master member (invariant 11) |
| `calls` | The nightly pass (`record_if_changed`), `pipeline.backfill` (rows marked `reconstructed`), `pipeline.run` (unconditional) | Immutable; never written by a GET |

The model's five uses (`backend/llm/`: flag explanation, chain decomposition, keyword generation, purity
estimate, tier recommendation) draft prose, structure or a recommendation for a person to accept. None writes a
fact or a call, and none feeds a detector (invariant 3).

## Where files live

| Class | Location | Recovery boundary |
|---|---|---|
| Committed inputs | `backend/seed_data/` (the demo seed, also read by real-data tests); `backend/tests/fixtures/` (test-only) | In git; never written at runtime. Shipped code never reads `tests/` |
| Generated contracts | `backend/openapi.json`, `frontend/src/api/types.gen.ts` | In git; regenerate both together (`app.openapi_export`, then `npm run gen:api`); CI fails on drift |
| Raw caches | `data/edgar_cache/`, `data/price_cache/`, `data/figi_cache/` | Refetchable only while the upstream still serves it, and a mutable response may differ. Deleting a cache is not a backup policy |
| Triage working state | `data/triage_sessions/` (`workbench/triage_store.py`) | One current session, overwritten; deleting it loses unpromoted work |
| Draft provenance | `data/draft_runs/` (`workbench/draft_run_log.py`) | Best-effort record per completed draft; a new draft is a new run |
| Run artifacts | `data/cron_runs/`, `data/onboard_runs/`, `data/backfills/` and similar | The record of each job; monitors diff against them |
| Database snapshots | `data/backups/` (`pipeline/backup.py`) | Plain-SQL dumps with retention ([CRON_SIDECAR](CRON_SIDECAR.md)). They do not hold triage sessions, draft runs or other files under `data/` |
| Research artifacts | `data/backtest/` (`backtest/store.py`) | Immutable run directories; a re-run is a new run, not a restore |

Container appdata survives an image rebuild, but deleting its volume loses the files on it. A database dump is
not a filesystem backup: check the compose mounts before any cleanup.

## Related

[README](../README.md), [FEED_LOOP](FEED_LOOP.md), [INGEST](INGEST.md), [DATA_SOURCES](DATA_SOURCES.md),
[TENANT_SCOPES](TENANT_SCOPES.md), [INVARIANTS](INVARIANTS.md).
