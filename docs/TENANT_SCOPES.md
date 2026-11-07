# TENANT_SCOPES.md — the two tenant scopes over the one `tenant_id` column

> Repo path: `docs/TENANT_SCOPES.md`. Replaces `PRODUCTION_TENANT.md` (the Phase-1 "production is a second tenant
> with its own master" model). Siblings: `INVARIANTS.md` §5 (the load-bearing rule and its standing obligations),
> `FEED_LOOP.md` (the writers), `REPLAY.md` (the mirror). The code: `backend/domain/scopes.py` (the map),
> migration `0076_tenant_kind.sql` (the `tenant.kind` marker).
>
> **The trust claim is isolation PROVEN, not a tenant row.** A workspace's ratified judgment must never appear in
> another workspace's read, and a stray row under a workspace must never enter a shared DATA read; that is
> certified structurally by `tests/db/test_scopes.py` and the poison-row test `tests/db/test_tenant_isolation.py`.

---

## The model — one column, two scopes, one map

Every table carries `tenant_id` (the seam has been in the schema since 0001). The column now means one of **two
scopes**, chosen PER TABLE by one map, `domain.scopes.SCOPE_OF`:

| Scope | What lives there | Who writes | Who reads |
|---|---|---|---|
| **DATA** (one shared tenant) | `security_master`, every ingested fact (`fact_insider_txn`, `fact_price_eod`, `fact_dilution`, `fact_fundamentals`, `fact_corporate_event`, `fact_activist_stake`, `fact_fund_shares`, `fact_spac_event`, `fact_filing_index`, `fact_fund_filing` / `_series` / `_symbol`), the split / ticker / CIK history, `security_onboarding`, the price-feed repairs, the Form 4/A tables and their decisions, `fundamentals_set_aside`, `split_jump_dismissal`, `data_repair` | the nightly, the on-promote ingest, the onboarding job, the weekly jobs, the repair CLIs, the Admin panels | every workspace |
| **WORKSPACE** (one per user) | `thesis` and its children (`basket_member`, `evidence`, `catalyst`, `kill_criterion`, `thesis_exclusion`, `basket_snapshot`), `calls`, `call_annotation`, `operator_decision`, the six `edition*` tables, `fact_theme_conviction`, `fact_term_mentions`, `fact_spac_match` | the owner (the app), and the nightly on the owner's behalf | the owner |
| **MIXED** | `fact_revenue_mix`, `fact_cash_burn`, `fact_shares_outstanding`, `fact_catalyst` | a FEED row (`ratified_by IS NULL` — the DOE feed, the 8-K catalysts) → DATA; a RATIFIED row (the Workbench ratify) → the caller's workspace | a read unions DATA + the caller's workspace |

The data tenant is `DATA_TENANT_ID = 00000000-…-0001` — the row 0001_init seeded, which migration 0076 marks
`kind='data'` (renamed `platform`; a partial unique index allows exactly one). Every other `tenant` row is
`kind='workspace'` (the default; `pipeline.provision_tenant` creates them). 0076 also adds `tenant.notify_webhook`
(NULL = log only), the per-workspace push destination PR 2 reads.

**Why a ratified fact is private (the MIXED rule).** A purity split, a cash-burn figure, a catalyst ratified from
a filing, a hand-confirmed share count: these are a user's JUDGMENTS about a company. Two users may ratify
different numbers; neither may feed the other's call (#4, #6), and a plain user must not poison the shared layer.
So a ratify writes the caller's workspace, a feed writes DATA, and a read unions the two. The as-of read's
latest-version pick (`DISTINCT ON (natural key) … ORDER BY recorded_at DESC, id DESC` — the natural key carries
no tenant) resolves the union: a workspace's later ratification overrides the feed row for that workspace only.

**Today's state (after PR 1).** The data tenant is ALSO the only workspace: every thesis, call and ratified row
still sits in `…0001`, so every read and write touches `…0001` only and every call is byte-identical to before the
split. PR 2 moves the founding workspace out (a fixed `…0002`) and makes each user's workspace real.

## The seam — how a read names its scope

- **`domain.scopes.tenants_for(table, *, data, workspace)`** — DATA → `(data,)`, WORKSPACE → `(workspace,)`,
  MIXED → `(data, workspace)` (one id when the two are equal). The ONE place a table's scope is decided.
- **The accessors take one tenant or several** (`db.bitemporal.as_of` / `as_of_many` / `as_of_thesis` /
  `fact_exists`, and the 4/A resolution in `db.insider_resolution`): `tenant_id = ANY(...)`. A single UUID works
  exactly as before.
- **`signals.base.PointInTimeData(..., data_tenant=DATA_TENANT_ID, workspace=…)`** reads each table for
  `tenants_for(table, …)`; the master, the benchmark lookup and the split list read the data tenant; the memo key
  carries the tenant tuple (a memo never crosses a scope). `tenant_id=` survives as a keyword-only TRANSITION ALIAS
  that sets both scopes (PR 2 removes it), and an unset workspace is the demo/test default.
- **The replay mirror** (`replay/export.py`, `replay/pit.py`) takes the same two ids: the export writes each table
  for `tenants_for(table, …)` (the aux 4/A tables, the split list and the identity aux from DATA), and the reader
  picks per table — so a mirror reads exactly what the live PIT reads. The manifest's `tenant_id` is the workspace;
  a `data_tenant` key appears only when the two differ.
- **The call path** (`pipeline/call_for_thesis.py`) builds `PointInTimeData(data_tenant=DATA_TENANT_ID,
  workspace=thesis.tenant_id)`; the call of record is written under `thesis.tenant_id` (a call is workspace data).
- **The feed writers name the data tenant**: every ingest leg and master read in `pipeline.daily`,
  `pipeline.ingest_thesis`, `pipeline.onboarding` (the X1 queue is keyed on the data tenant — one history load per
  company platform-wide), the Workbench price pull and ETF resolve, and the data-layer CLIs (`populate_master`,
  `spac_sweep`, `enrich_identity`, `audit_identity`, the fundamentals / fund-shares / price-symbol runs) use
  `DATA_TENANT_ID`, never `thesis.tenant_id`. A job that walks "every live basket" walks every workspace's baskets
  (a basket is a workspace's; the company it names is shared). `term_mentions` writes a WORKSPACE table: its rows
  keep the thesis's tenant.
- **The ratify path keeps the caller's tenant** (`POST /workbench/facts`, `/facts/auto-confirm`,
  `pipeline.ratify_catalyst`, `pipeline.ratify_shares`): only their master lookups name the data tenant.
- **The boot assert** — `scopes.assert_data_tenant(conn)` raises `DataTenantMissing` when the `DATA_TENANT_ID` row
  is missing or not `kind='data'`. The API lifespan and every nightly pass call it; nothing defaults silently.

`db.session.DEFAULT_TENANT_ID` stays as the demo/test default (equal to `DATA_TENANT_ID`, retired in PR 2).
The dashboards still read with it; their reads are DATA reads and value-identical, and PR 2 sweeps them.

## The growth rule

1. **A new tenant-bearing table is named in `SCOPE_OF` in the same change.** `tests/db/test_scopes.py` parses every
   migration and fails on a table with a `tenant_id` column the map does not name.
2. **A new read names its scope through the map** — `tenants_for` / the PIT / the accessors — never a hand-picked
   tenant on a raw query.
3. **The isolation tests grow with each new read surface**, and **no test uses the data tenant as a user's
   workspace** (with the two equal, a missed filter reads the right rows by accident).

## The isolation proof

- `tests/db/test_scopes.py` — the map's completeness; the migration and the boot assert; with workspaces W and V
  (neither is `…0001`): a MIXED read with `(DATA, W)` returns the data row AND W's row, latest-wins across them; a
  DATA read ignores a row stored under W; a WORKSPACE read ignores a row stored under DATA; V sees none of W's rows;
  `fact_exists` honors the same lists; the memo keys on the scope; the replay mirror applies the same map.
- `tests/db/test_tenant_isolation.py` — (a) read isolation on both axes; (b) an empty workspace re-derives to an
  honest Incubating from DATA facts alone, never another workspace's ratified rows; (c) a workspace thesis arms off
  DATA facts + its own ratified rows and cites only what it can see; (d) the call of record lands in the thesis's
  workspace; the smoke runs the whole cut on the identical path; and every DATA read surface (the master, the 8-K /
  13D tapes, the prefetch, the split list, the onboarding queue, the worklists) reads the data tenant and never a
  stray workspace row.

## Known limitations (after PR 1)

- **The data tenant is the only workspace.** Nothing distinguishes a user yet: there is one workspace, and it is
  `…0001`. PR 2's migration moves the thesis rows and the ratified MIXED rows into the founding workspace.
- **The Board lists every thesis** (`thesis_repo.list_all` is not workspace-scoped) and per-thesis routes load by
  id; the dashboards read with `DEFAULT_TENANT_ID`. Value-identical today; PR 2 scopes them.
- **Isolation is discipline + the tests, not RLS.** The `security_id` FK carries no tenant; RLS is the
  defense-in-depth for the multi-user era.
- **The frozen backtest archive and the editions store** keep their single-workspace shape: their runs pass the
  data tenant and one workspace.
