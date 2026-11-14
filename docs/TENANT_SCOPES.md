# TENANT_SCOPES.md — the two tenant scopes over the one `tenant_id` column

> Repo path: `docs/TENANT_SCOPES.md`. Replaces `PRODUCTION_TENANT.md` (the Phase-1 "production is a second tenant
> with its own master" model). Siblings: `INVARIANTS.md` §5 (the load-bearing rule and its standing obligations),
> `FEED_LOOP.md` (the writers), `REPLAY.md` (the mirror), `AUTH.md` (users and sessions). The code:
> `backend/domain/scopes.py` (the map), migration `0076_tenant_kind.sql` (the `tenant.kind` marker), migration
> `0078_founding_workspace.sql` (the founding workspace and the move out of the data tenant).
>
> **V2 (the `v2` branch, W1a):** two scopes, no MIXED. Share counts, cash/burn and company catalysts are platform
> data (DATA): one value per company, read by every workspace, set by hand by a steward only. Purity is the
> workspace's own (WORKSPACE). Every read names ONE tenant per table.
>
> **The trust claim is isolation PROVEN, not a tenant row.** A workspace's own judgment must never appear in
> another workspace's read, and a stray row under a workspace must never enter a shared DATA read; that is
> certified structurally by `tests/db/test_scopes.py` and the poison-row test `tests/db/test_tenant_isolation.py`.

---

## The model — one column, two scopes, one map

Every table carries `tenant_id` (the seam has been in the schema since 0001). The column now means one of **two
scopes**, chosen PER TABLE by one map, `domain.scopes.SCOPE_OF`:

| Scope | What lives there | Who writes | Who reads |
|---|---|---|---|
| **DATA** (one shared tenant) | `security_master`, the platform values (`fact_shares_outstanding`, `fact_cash_burn`, `fact_catalyst`; V2), every ingested fact (`fact_insider_txn`, `fact_price_eod`, `fact_dilution`, `fact_fundamentals`, `fact_corporate_event`, `fact_activist_stake`, `fact_fund_shares`, `fact_spac_event`, `fact_filing_index`, `fact_fund_filing` / `_series` / `_symbol`), the split / ticker / CIK history, `security_onboarding`, the price-feed repairs, the Form 4/A tables and their decisions, `insider_txn_hold` (the insider rows held for an impossible date) and `insider_txn_hold_decision` (a steward's decision about one), `fundamentals_set_aside`, `split_jump_dismissal`, `data_repair`, `security_change` (the dashboards' per-company change stamp) | the nightly, the on-promote ingest, the onboarding job, the weekly jobs, the repair CLIs, the Admin panels, the steward's Workbench writes to the master (the business-type re-tag, Surface an ETF) and the steward's share-count and cash/burn confirm (V2), the machine auto-confirm (the server's own parse of a filing); never a plain user's typed value (`AUTH.md`) | every workspace |
| **WORKSPACE** (one per user) | `thesis` and its children (`basket_member`, `evidence`, `catalyst`, `kill_criterion`, `thesis_exclusion`, `basket_snapshot`), `calls`, `call_annotation`, `operator_decision`, the six `edition*` tables, `fact_theme_conviction`, `fact_term_mentions`, `fact_spac_match`, `fact_revenue_mix` (purity; V2) | the owner (the app), and the nightly on the owner's behalf | the owner |

The data tenant is `DATA_TENANT_ID = 00000000-…-0001` — the row 0001_init seeded, which migration 0076 marks
`kind='data'` (renamed `platform`; a partial unique index allows exactly one). Every other `tenant` row is
`kind='workspace'` (the default; `pipeline.users create` makes one with each user, `tests/workspaces.new_workspace`
makes one for a test). 0076 also added `tenant.notify_webhook`, a per-workspace push destination; migration 0079
dropped it: the destination is now per USER, `user_meta.slack_webhook_url` (keyed on the user, no `tenant_id`; set
on the Settings page; the nightly's notify reads every enabled user's of a workspace, `FEED_LOOP.md`).

**Which value is whose (V2).** A share count, a cash/burn figure and a company catalyst are facts about a company
that every workspace should read the same: they are platform data (DATA), read from the data tenant only, with no
fallback to any workspace and no union. Only a steward sets a share count or a cash/burn value by hand
(`POST /workbench/facts` answers 403 to a plain user); the machine auto-confirm (the server applying its own parse
of a filing) and the ratify CLIs write the data tenant too. The Workbench company-catalyst form is retired on the
branch (409); company catalysts move to the thesis's catalyst list (W6). A purity split is a user's JUDGMENT: two
users may ratify different numbers, and neither may feed the other's view (#4, #6), so purity is WORKSPACE: written
and read in the caller's workspace only. (Before V2 the four tables were MIXED: a ratify wrote the caller's
workspace and a read unioned it with the data tenant.)

**Today's state (after PR 2's move).** Every user has a private workspace. Migration 0078 created the **founding
workspace** (`FOUNDING_WORKSPACE_ID = 00000000-…-0002`, `kind='workspace'`, a fixed id so the migration runs
unattended on boot) and moved into it every row of the 19 WORKSPACE tables and every RATIFIED row of the 4 MIXED
tables that sat under `…0001`, plus every `app_user` on `…0001`. The data tenant now holds no thesis, no
workspace row and no ratified row; its feed rows (`ratified_by IS NULL`) stay. `tenant_id` is a scope label, not a
temporal value, so the move is an UPDATE of bookkeeping (the append-only `no_update` trigger is disabled and
re-enabled by name around it, inside the one migration transaction). The migration checks its own
post-conditions and raises when one fails; a second run moves nothing. Its header carries the read-only per-table
count query the operator runs on prod before and after the deploy, and the inverse statements (the move back).

A trigger on `app_user` (`workspace_kind_check`) refuses a `workspace_id` that is not a `kind='workspace'` tenant:
no user can sit on the data tenant again. `pipeline.users create` makes a NEW workspace per user by default
(`--workspace <uuid>` attaches to an existing one, `--founding` to the founding workspace; `docs/AUTH.md`). The
boot seed (`pipeline.seed`, on `ALPHADECK_SEED_ON_BOOT=true` only, the dev overlay) writes its demo
cards into the founding workspace and their DATA facts into the data tenant.

## The seam — how a read names its scope

- **`domain.scopes.tenants_for(table, *, data, workspace)`** — DATA → `(data,)`, WORKSPACE → `(workspace,)`:
  always one id. The ONE place a table's scope is decided.
- **`domain.scopes.require_write_tenant(table, tenant_id)`** — a writer of a DATA table refuses (`ValueError`) any
  tenant but the data tenant, before it writes (`ingest_shares_outstanding`, `ingest_cash_burn`, `ingest_catalyst`
  call it first): a row no read would ever see is refused, not written.
- **The accessors take one tenant or several** (`db.bitemporal.as_of` / `as_of_many` / `as_of_thesis` /
  `fact_exists`, and the 4/A resolution in `db.insider_resolution`): `tenant_id = ANY(...)`. A single UUID works
  exactly as before.
- **`signals.base.PointInTimeData(..., data_tenant=DATA_TENANT_ID, workspace=…)`** reads each table for
  `tenants_for(table, …)`; the master, the benchmark lookup and the split list read the data tenant; the memo key
  carries the tenant tuple (a memo never crosses a scope). `workspace=` is REQUIRED (PR 2 removed the PR 1
  `tenant_id=` transition alias); `data_tenant=` defaults to `DATA_TENANT_ID`.
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
  (a basket is a workspace's; the company it names is shared). `term_mentions` writes a WORKSPACE table: it runs once
  per workspace and writes each card's rows under that card's own workspace (`--workspace` scopes a hand run).
- **The ratify path (V2):** `POST /workbench/facts` writes purity under the caller's workspace and a share count or
  cash/burn value (steward only) under the data tenant; `/facts/auto-confirm` writes the data tenant.
  `pipeline.ratify_shares` and `pipeline.ratify_cash_burn` take no tenant and write the data tenant;
  `pipeline.ratify_revenue_mix` takes a REQUIRED `--workspace <uuid>` (there is no deployment-wide default tenant:
  `ALPHADECK_TENANT_ID` and `db.session.current_tenant_id` are retired). `pipeline.ratify_catalyst` is deleted. The
  data-layer CLIs (`populate_master`, `spac_sweep`, `enrich_identity`) default to the data tenant.
- **The boot assert** — `scopes.assert_data_tenant(conn)` raises `DataTenantMissing` when the `DATA_TENANT_ID` row
  is missing or not `kind='data'`. The API lifespan and every nightly pass call it; nothing defaults silently.

## The workspace rules (PR 2)

- **Every card-selecting read names its workspace.** `thesis_repo.get` / `get_asof` / `list_all` / `lifespans` /
  `created_at_for` take a REQUIRED keyword `workspace`: the caller's workspace id (a thesis in another workspace
  reads as `None`, the API's 404), or the sentinel `domain.scopes.WORKSPACE_ANY` (no filter) for SYSTEM code only:
  the nightly's walk, the editions job, the repair / backfill CLIs, the replay harness. `None` raises; a forgotten
  workspace fails, it never reads every workspace. Functions keyed by an already-owned thesis id (`set_*`,
  `entries_for`, `basket_size_asof`, the snapshot reads) are unchanged.
- **The request's workspace** is `app.deps.get_workspace`: the signed-in user's `workspace_id`. The SYSTEM principal
  (the cron's dashboard warm-up) has none of its own; it names one per request in the `X-Alphadeck-Workspace:
  <uuid>` header (400 naming the header when missing). The header is honored ONLY for the system principal: a
  user's header is ignored. (`SYSTEM_USER.workspace_id` is the nil UUID, a placeholder that names no tenant and is
  never read.) The old `get_current_tenant` alias is gone: a workspace route depends on `get_workspace`.
- **Ownership** is one choke point, `app.deps.get_thesis_or_404` (loads from the caller's workspace, else 404 "thesis
  not found": never 403, never a hint that the id exists). The dashboard / radar / fund-radar loads by a
  `{thesis_id}` and the Workbench's optional thesis reads pass the caller's workspace; the job polls
  (`…/draft-chain/jobs/{job_id}`, `…/ingest/jobs/{job_id}`) answer 404 unless the path's thesis is the caller's AND
  the job is that thesis's.
- **The foreign-id guard.** `thesis_repo.upsert` refuses (`ThesisNotInWorkspace`) a thesis id that already exists
  under another tenant; the promote answers 404 "thesis not found", and the other workspace's row is untouched.
- **The ratify stamps the caller.** `POST /workbench/facts` stamps `ratified_by` with the signed-in user's email (the
  auto-confirm keeps its honest `"auto"`); purity lands in the caller's workspace, a steward's share count or
  cash/burn value in the data tenant (V2).
- **The dashboard cache key carries the workspace**: `(workspace, slug, params, asof)`, required in
  `dashboards.cache.cached`, so a page computed for one workspace's cards is never served to another. The card
  loaders (`dashboards.baskets.picker_cards` / `cards_as_of` / `live_card_ids` / `price_flags` / `per_company`,
  `radar.fund_cards.load_cards`, `radar.spac`'s card reads) take the workspace explicitly; their DATA reads (the
  master, the price flags, the splits) name `DATA_TENANT_ID`.
- **The point-in-time view names its workspace.** `PointInTimeData` / `ReplayPointInTimeData` / `export_snapshot`
  take a REQUIRED `workspace` (and `data_tenant`, default `DATA_TENANT_ID`); the PR 1 `tenant_id=` alias is gone.
- **Per-workspace system jobs.** The Scoreboard reads the caller's workspace (`scoreboard_records` /
  `assemble_scoreboard` / `edition_diff` take `workspace`). The editions nightly and cut loop every `kind='workspace'`
  tenant (`repositories.tenant_repo.workspaces`); the editions trial / publish and the backtest run take
  `--workspace` (default: the founding workspace); the replay harness and the backtest read one workspace's cards.

- **The steward routes act on the DATA tenant, never on the caller's workspace.** The splits, onboarding and
  fundamentals-set-aside readouts and writes (`security_split`, `split_jump_dismissal`, `security_onboarding`,
  `fundamentals_set_aside` are DATA tables) name `DATA_TENANT_ID`; a steward route that must reach workspace
  tables does so by id for every workspace (the editions cut line reads every tenant that holds an edition). A grep
  test pins that `get_workspace` appears in none of `admin.py`, `admin_splits.py`, `admin_onboarding.py`.
- **The Admin readouts show other workspaces' cards as ids only** (`workspace <8 hex> · thesis <8 hex>`; tickers and
  security ids stay), and `GET /admin/status` carries `data_tenant_theses`, the count of `thesis` rows left under the
  data tenant (expected 0; the Admin page shows one loud line when it is not). `docs/ADMIN.md`.
- **Notify and the warm-up are per workspace.** The nightly builds each card's push destination from its workspace
  (every enabled user's `user_meta.slack_webhook_url`, else the env webhook only for a super admin's workspace,
  else the log), the digest is
  grouped by workspace, the SPAC radar's announced page goes per workspace to its own destination (only its own cards), and the dashboard warm-up walks every workspace with the system token and the
  `X-Alphadeck-Workspace` header (`docs/FEED_LOOP.md`).

`db.session.DEFAULT_TENANT_ID` (`…0001`, equal to `DATA_TENANT_ID`) stays for TESTS and the demo SEED only: the suite's
signed-in steward (`tests.auth_fixtures.TEST_STEWARD`) and the seed functions' default sit there, so the existing API
tests read their cards exactly as before. Nothing under `app/`, `dashboards/` or `radar/` names it (a grep test pins
it): a read there names `DATA_TENANT_ID` or the caller's workspace.

## The growth rule

1. **A new tenant-bearing table is named in `SCOPE_OF` in the same change.** `tests/db/test_scopes.py` parses every
   migration and fails on a table with a `tenant_id` column the map does not name.
2. **A new read names its scope through the map** — `tenants_for` / the PIT / the accessors — never a hand-picked
   tenant on a raw query.
3. **The isolation tests grow with each new read surface**, and **no test uses the data tenant as a user's
   workspace** (with the two equal, a missed filter reads the right rows by accident).

## The isolation proof

- `tests/db/test_scopes.py` — the map's completeness; the migration and the boot assert; with workspaces W and V
  (neither is `…0001`): every table names one tenant; share counts, cash/burn and company catalysts read the data
  tenant only (a row under W reaches neither W nor V, on the per-security read, the prefetch, the public clock and
  `as_of_many`); purity reads the workspace only; a DATA read ignores a row stored under W; a WORKSPACE read ignores a row stored under DATA; V sees none of W's rows;
  `fact_exists` honors the same lists; the memo keys on the scope; the replay mirror applies the same map.
- `tests/db/test_0078_founding_workspace.py` — the move on old-shape rows (a thesis, its members, a snapshot, a
  call, a decision, a ratified row, a feed row, a user): every workspace row and the ratified row land in `…0002`,
  the feed row stays DATA, the user moves; a second run changes nothing; the `app_user` trigger refuses the data
  tenant; the SQL's table lists equal the 19 + 4 tables 0078 moved (pinned as literals since V2) and its trigger
  list the live catalog.
- `tests/app/test_workspace_isolation.py` — the two-user proof with REAL users and session cookies (no gate
  override) on three workspaces, none of them `…0001`: B lists no theses; EVERY `{thesis_id}` operation in the
  schema answers 404 for A's id; every list / dashboard GET answers 200 and carries neither A's id nor A's name; a
  cached dashboard page is never served across workspaces; B's promote carrying A's id is a 404; B's own card on the
  same company reads the shared bars and none of A's ratified fact; every row B writes carries B's workspace, and no
  DATA-scope row sits under a user workspace; a plain user's share-count or cash/burn confirm is a 403 that writes no
  row, the steward's lands in the data tenant and A reads it, a plain user's purity stays private, and the
  company-catalyst form answers 409; the
  super admin sees no other workspace's card and the Admin readouts name it as ids only; the system principal's
  header; a steward who sits on a workspace (not the data tenant) lists and confirms a data-tenant split and reads the
  data tenant's onboarding queue; and the grep guards (`WORKSPACE_ANY` never appears under `app/`, `dashboards/` or
  the route-facing radar modules; `DEFAULT_TENANT_ID` nowhere under `app/`, `dashboards/`, `radar/`; `get_workspace`
  in no admin router; `get_current_tenant` nowhere).
- `tests/db/test_tenant_isolation.py` — (a) read isolation on both axes; (b) an empty workspace re-derives to an
  honest Incubating from DATA facts alone, and a company catalyst stored under a workspace reaches no card; (c) a
  workspace thesis arms off DATA facts and cites only what it can see; (d) the call of record lands in the thesis's
  workspace; the smoke runs the whole cut on the identical path; and every DATA read surface (the master, the 8-K /
  13D tapes, the prefetch, the split list, the onboarding queue, the worklists) reads the data tenant and never a
  stray workspace row.

## Known limitations

- **The "off every live card" wording** is judged against the CALLER's cards: a company on another workspace's card
  but none of the caller's reads as no longer priced on the caller's dashboards (the wording, not the data).
- **Isolation is discipline + the tests, not RLS.** The `security_id` FK carries no tenant; RLS is the
  defense-in-depth for the multi-user era.
- **The frozen backtest archive and the editions store** keep their single-workspace shape: their runs pass the
  data tenant and one workspace.
