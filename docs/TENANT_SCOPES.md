# Tenant scopes

Every table that carries `tenant_id` belongs to one of two scopes: DATA, the one shared tenant that holds the
company master, every ingested fact and the platform values, or WORKSPACE, one private tenant per user that holds
their cards, calls, decisions and purity. This doc is the scope map, how a read names its scope, the workspace
rules for routes and jobs, and the tests that prove isolation. Read it before you add a table, a read, a route or
a job that touches tenant-bearing rows. The standing obligations are [invariant 12](INVARIANTS.md#12-two-tenant-scopes-every-read-names-its-scope); users and roles
are in [AUTH](AUTH.md).

## Code

| Piece | Module |
|---|---|
| The map (`SCOPE_OF`, `UNSCOPED_TABLES`, `owner_of`), `tenants_for`, `require_write_tenant`, `WORKSPACE_ANY`, `assert_data_tenant`, the two fixed ids | `backend/domain/scopes.py` |
| The request's workspace (`get_workspace`), ownership (`get_thesis_or_404`) | `backend/app/deps.py` |
| Card reads that take a workspace | `backend/repositories/thesis_repo.py` |
| Every workspace, for per-workspace jobs | `backend/repositories/tenant_repo.py` (`workspaces`) |
| The `tenant.kind` marker; the founding workspace and the `app_user` trigger | migrations `0076_tenant_kind.sql`, `0078_founding_workspace.sql` |

## How it works

### The map

`domain.scopes.SCOPE_OF` names the scope of every table with a `tenant_id`; `UNSCOPED_TABLES` names every table
without one, as `platform` or `user-keyed`. Together they name every table the schema has, and no table is in both
(`owner_of` answers for any table). The map is the list; this table groups it.

| Scope | Tables | Who writes | Who reads |
|---|---|---|---|
| DATA | `security_master`; the ingested facts (`fact_insider_txn`, `fact_price_eod`, `fact_dilution`, `fact_fundamentals`, `fact_corporate_event`, `fact_activist_stake`, `fact_fund_shares`, `fact_spac_event`, `fact_filing_index`, `fact_fund_filing`, `fact_fund_series`, `fact_fund_symbol`); the split, ticker and CIK history (`security_split`, `security_split_history`, `security_ticker_history`, `security_cik_change`); `security_onboarding`; the price-feed repairs (`price_symbol_change`, `price_feed_ack`, `price_restatement_hold`, `price_bar_fill`); the Form 4/A tables (`insider_amendment`, `insider_amendment_link`, `insider_amendment_decision`); `insider_txn_hold`, `insider_txn_hold_decision`; `fundamentals_set_aside`; `split_jump_dismissal`; `data_repair`; `security_change`; the platform values (`fact_shares_outstanding`, `fact_cash_burn`, `fact_catalyst`) and their workflow (`extraction_candidate`, `steward_pin`, `platform_value_decision`, `platform_extraction_attempt`) | the machine (the nightly, the on-promote ingest, the onboarding job, background extraction, the weekly jobs, the repair CLIs) and the steward (Admin, the steward's Workbench writes); never a plain user's typed value | every workspace |
| WORKSPACE | `thesis` and its children (`basket_member`, `evidence`, `catalyst`, `kill_criterion`, `thesis_exclusion`, `basket_snapshot`); `calls`, `call_annotation`, `operator_decision`; the editions store (`edition`, `edition_event`, `edition_entry`, `edition_call`, `edition_outcome`, `edition_cause`); `fact_thesis_catalyst`, `fact_theme_conviction`, `fact_term_mentions`, `fact_spac_match`; `fact_revenue_mix` (purity) | the owner through the app, and the nightly on the owner's behalf | the owner |
| unscoped, `platform` | `tenant`, `sec_request_budget`, `schema_migrations` | the platform | the platform |
| unscoped, `user-keyed` | `app_user`, `user_session`, `api_token`, `user_meta`, the six `oauth_*` tables, `user_action_count`, `value_flag` | per user | per user |

**Which value is whose.** A share count, a cash/burn figure and a company catalyst are facts about a company that
every workspace should read the same, so they are DATA: read from the data tenant only, with no fallback to any
workspace and no union. Purity is a user's judgment: two users may ratify different numbers and neither may feed
the other's view (invariants 4 and 6), so it is WORKSPACE, written and read in the caller's workspace only.

### The tenants

- **The data tenant** is `DATA_TENANT_ID` (`…0001`), the row migration 0001 seeded, marked `kind='data'` by 0076; a
  partial unique index allows exactly one. `scopes.assert_data_tenant` raises when the row is missing or not
  `kind='data'`; the API lifespan and every nightly pass call it, so nothing defaults silently.
- **A workspace** is any other `tenant` row (`kind='workspace'`). `pipeline.users create` makes one per user by
  default ([AUTH](AUTH.md) "The CLI"); `tests/workspaces.new_workspace` makes one for a test.
- **The founding workspace** is `FOUNDING_WORKSPACE_ID` (`…0002`), a fixed id so migration 0078 could create it and
  move the founding operator's rows into it unattended on boot. The boot seed (only with `ALPHADECK_SEED_ON_BOOT`,
  on in the dev overlay) writes its demo cards there and their facts to the data tenant.
- The `workspace_kind_check` trigger on `app_user` refuses a `workspace_id` that is not a workspace: no user sits
  on the data tenant.
- `db.session.DEFAULT_TENANT_ID` equals `DATA_TENANT_ID` and exists for tests and the demo seed only; nothing under
  `app/`, `dashboards/` or `radar/` names it. Why it matters: a test that uses it as a workspace reads the right
  rows by accident and hides a missing filter.

### How a read names its scope

- **`tenants_for(table, *, data, workspace)`** returns one id: the data tenant for a DATA table, the workspace for a
  WORKSPACE table. It is the one place a table's scope is decided.
- **`require_write_tenant(table, tenant_id)`** makes a writer of a DATA table refuse (`ValueError`) any tenant but
  the data tenant before it writes, because a row no read would see must be refused, not written. The platform-value
  writers and repositories call it first.
- **The accessors** (`db.bitemporal.as_of`, `as_of_many`, `as_of_thesis`, `fact_exists`, and `db.insider_resolution`)
  take one tenant or several (`tenant_id = ANY(...)`).
- **The point-in-time view** `signals.base.PointInTimeData(..., data_tenant=DATA_TENANT_ID, workspace=...)` reads
  each table for `tenants_for`, with `workspace` required and a memo key that carries the tenants. The replay mirror
  (`replay/export.py`, `replay/pit.py`) applies the same map, so a mirror reads what the live view reads.
- **The call path** (`pipeline/call_for_thesis.py`) builds the view with the card's workspace and writes the call of
  record under that workspace.
- **The feed writers name `DATA_TENANT_ID`**, never `thesis.tenant_id`: every ingest leg and master read in
  `pipeline.daily`, `pipeline.ingest_thesis` and `pipeline.onboarding` (its queue is keyed on the data tenant: one
  history load per company platform-wide), the steward's Workbench price pull and ETF resolve, and the data-layer
  CLIs. A job that walks every live basket walks every workspace's baskets.
- **The ratify paths.** `POST /workbench/facts` writes purity under the caller's workspace and stamps `ratified_by`
  with the signed-in user's email; a share count or cash/burn there is the steward's and goes to the data tenant.
  The machine writer (`platform_values.machine`) and the steward's writer (`platform_values.steward`) write the
  data tenant. `pipeline.ratify_revenue_mix` is the only ratify CLI: it writes purity into the workspace its
  required `--workspace` names; there is no deployment-wide default tenant.

### Workspace rules

- **Every card-selecting read names its workspace.** `thesis_repo.get`, `get_asof`, `list_all`, `lifespans` and
  `created_at_for` take a required keyword `workspace`: the caller's id, or `scopes.WORKSPACE_ANY` (no filter) for
  system code only (the nightly's walk, the editions job, the repair CLIs, the replay harness). `None` raises: a
  forgotten workspace fails, it never reads every workspace. Functions keyed by an already-owned thesis id are
  unchanged.
- **The request's workspace** is `app.deps.get_workspace`: the caller's `workspace_id`. The system principal (the
  dashboard warm-up) has none; it names one per request in the `X-Alphadeck-Workspace` header (400 when missing).
  The header is honored only for the system principal; a user's header is ignored.
- **Ownership** is one choke point, `app.deps.get_thesis_or_404`: another workspace's thesis is a 404 "thesis not
  found", never a 403, so its existence is not revealed. The job polls answer 404 unless the path's thesis is the
  caller's and the job is that thesis's.
- **The foreign-id guard.** `thesis_repo.upsert` refuses a thesis id that exists under another tenant
  (`ThesisNotInWorkspace`); the promote answers 404 and the other row is untouched.
- **The dashboard cache key carries the workspace** (`dashboards.cache.cached` requires it), so one workspace's
  page is never served to another.
- **Per-workspace jobs.** The Scoreboard reads the caller's workspace. The editions nightly and cut loop every
  workspace (`tenant_repo.workspaces`); editions trial and publish and the backtest run take `--workspace` (default:
  the founding workspace). `pipeline.term_mentions` runs once per workspace (`--workspace` scopes a hand run).
  Notify ([FEED_LOOP](FEED_LOOP.md)) and the warm-up (`pipeline/warm_dashboards.py`) are per workspace.
- **Steward routes act on the data tenant, never on the caller's workspace.** The splits, onboarding, set-aside and
  platform-value routes name `DATA_TENANT_ID`; a steward route that must reach workspace tables does so by id for
  every workspace. Admin readouts show another workspace's card as `workspace <8 hex> · thesis <8 hex>`, and
  `GET /admin/status` carries `data_tenant_theses`, the count of `thesis` rows under the data tenant (expected 0;
  Admin shows a loud line otherwise; [ADMIN](ADMIN.md)).

### Adding a table

A table with a `tenant_id` goes in `SCOPE_OF`; a table without one goes in `UNSCOPED_TABLES` as `platform` or
`user-keyed`, in the same change. `tests/db/test_scopes.py` (`test_every_table_has_one_owner`) parses every
migration and fails on a table named in neither, in both, or named but never created. A new migration is numbered
after the highest file in `backend/db/migrations/`, and a migration is never renumbered or renamed:
`db/migrate.py` records applied file names in `schema_migrations`, so a renamed file runs again. The other
obligations (reads go through the map, the isolation tests grow with each read surface, no test uses the data
tenant as a workspace) are [invariant 12](INVARIANTS.md#12-two-tenant-scopes-every-read-names-its-scope).

## Who can do what

| Route or CLI | Who | Enforced by |
|---|---|---|
| Read or write a workspace's cards, calls, decisions, purity | the workspace's user | `get_workspace`, `get_thesis_or_404` |
| Name a workspace with `X-Alphadeck-Workspace` | the system principal only | `get_workspace` |
| Write a share count or cash/burn by hand | the steward | `POST /workbench/facts`'s steward check; `/admin/platform-values` under `require_super_admin` |
| Write DATA from a filing or a feed | the machine | `require_write_tenant` |
| `python -m pipeline.ratify_revenue_mix --workspace <uuid>` | the operator, in a shell | the CLI's required flag |
| Read every workspace (`WORKSPACE_ANY`) | system code only | grep guard in `test_workspace_isolation.py` |

## Tests

- `tests/db/test_scopes.py`: the map's completeness, the boot assert, and with two workspaces (neither `…0001`)
  each scope's reads ignore rows stored under the other, through the memo and the replay mirror too.
- `tests/db/test_tenant_isolation.py`: the poison-row proof on both axes, through the call path and every DATA read
  surface.
- `tests/db/test_0078_founding_workspace.py`: the founding move and the `app_user` trigger.
- `tests/app/test_workspace_isolation.py`: two real users on three workspaces. Every `{thesis_id}` operation answers
  404 for another workspace's id, no list or dashboard leaks across, and the grep guards hold: `WORKSPACE_ANY`
  never under `app/`, `dashboards/` or the route-facing radar modules, `DEFAULT_TENANT_ID` nowhere under `app/`,
  `dashboards/` or `radar/`, `get_workspace` in no admin router.

## Known limits

- Isolation is the map plus these tests, not row-level security: the `security_id` key carries no tenant.
- "Off every live card" wording on the dashboards is judged against the caller's cards: a company on another
  workspace's card but none of the caller's reads as no longer priced (the wording, not the data).
- The frozen backtest archive and the editions store keep a single-workspace shape: their runs pass the data tenant
  and one workspace.

## Related

[Invariant 12](INVARIANTS.md#12-two-tenant-scopes-every-read-names-its-scope), [AUTH](AUTH.md), [FEED_LOOP](FEED_LOOP.md) (the writers),
[REPLAY](REPLAY.md) (the mirror), [PLATFORM_VALUES](PLATFORM_VALUES.md) (the platform values), [ADMIN](ADMIN.md).
