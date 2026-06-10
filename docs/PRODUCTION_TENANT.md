# PRODUCTION_TENANT.md — cutting production as a fresh tenant

> Repo path: `docs/PRODUCTION_TENANT.md`. This is **Phase 1, Step 3** of `ROADMAP.md` ("Cut production as a
> fresh tenant"): make the back half live for real use by provisioning an **empty production tenant on the
> identical code path, alongside the demo** — never a destructive wipe. Siblings: `INVARIANTS.md` (the
> load-bearing rules; `tenant_id` per row is the data-isolation seam), `ROADMAP.md` (sequencing),
> `REPLAY.md` (the harness whose lookahead-boundary posture this mirrors).
>
> **The trust claim is isolation PROVEN, not a tenant row.** A demo fact must never appear in a production
> read and vice versa; that is certified structurally by the poison-row test (`tests/db/test_tenant_isolation.py`),
> the same discipline-plus-test posture as the harness's no-lookahead boundary.

---

## The model — a tenant is DATA isolation, derived from the thesis

Runtime auth is deferred (out of scope, `INVARIANTS.md`), so a tenant here is **not** an authenticated
login — it is a **data-isolation boundary**. Every row in every table carries a `tenant_id` (the seam has
been present since the initial schema); production is simply a **second tenant** living beside the seeded
demo tenant (`DEFAULT_TENANT_ID = 00000000-…-0001`).

Because there is no login, the tenant for a given read is **intrinsic to the thesis**: every thesis row
carries its `tenant_id`, a thesis lives in exactly one tenant, and **a call for thesis X uses
`thesis.tenant_id` for every fact read it makes.** That is the whole threading model — no ambient
"current tenant", no request-scoped context; the thesis carries it.

`DEFAULT_TENANT_ID` remains **only** a seed/demo/test convenience (the default on the ingest functions and
the seed). It is **never a fallback on a production read/write path** — `call_for_thesis` passes
`thesis.tenant_id` explicitly, so a production thesis can never silently read or write demo.

## The threading map (what carries the tenant end-to-end)

The seam was present but **defaulted, not threaded**: `call_for_thesis` built `PointInTimeData` with no
tenant, so a production thesis would have read demo facts. Step 3 threads it:

1. **Read (load-bearing, one line).** `pipeline/call_for_thesis.py` loads the thesis, then builds
   `PointInTimeData(..., tenant_id=thesis.tenant_id)`. `PointInTimeData` already threads `self.tenant_id`
   into every `as_of` / `as_of_thesis` accessor, so this single line makes **both** the API read path
   (`record=False`) and the batch `pipeline.run` (`record=True`) tenant-correct. Passed **explicitly**
   (never `or DEFAULT`): the column is `NOT NULL`, so a loaded thesis's tenant is non-None and a bug would
   surface rather than silently fall back to demo.
2. **Calls-log write.** `repositories/calls_repo.append(conn, card, tenant_id)` +
   `mappers.call_to_row(card, tenant_id)` carry the tenant; `call_for_thesis` passes `thesis.tenant_id`.
   (The `tenant_id` defaults to demo for the seed/tests, exactly like the ingest fns; the production path
   always passes it.) Threading the read but hardcoding the write would **split the record across tenants**
   — a production read with a demo call-of-record. `CallCard` itself is unchanged (the tenant is a column
   on the `calls` row, not a card field), so the `card` JSON and the API wire models are byte-identical.
3. **API ticker resolution.** `app/routers/theses.py::get_call` loads the thesis (404 if absent) and threads
   `thesis.tenant_id` into `master.ciks_for` / `master.tickers_for` — because the security master is
   per-tenant (below), a production thesis's tickers resolve from production's master, not demo's.
4. **Ingest completeness (additive).** `ingest/doe/feed.run_doe_feed` and `pipeline/ratify_catalyst`
   (`--tenant-id`) take a tenant (default demo), threaded to `ingest_catalyst`, so production can run the
   DOE feed and operator-ratify catalysts into production. The five core ingest fns (form4, converts,
   prices, catalyst, theme) already took `tenant_id`.
5. **Replay** is already tenant-parameterized end-to-end (`export_snapshot` / `replay_all` / `RealizedPrices`);
   the CLI defaults demo — left as-is until production replay earns it.

## The security master is per-tenant

`security_master` has `tenant_id NOT NULL` and **all four** `master.*` reads filter by it; there is no
ticker-unique constraint. So the model is **per-tenant**: production **re-ingests its own securities** under
its tenant via the normal ingest path (`master.resolve` is idempotent). Each tenant owns its own rows even
for the same ticker — which is exactly what makes the poison-row test sharp (a production read of "HIMS"
must resolve production's HIMS row, never demo's). A shared/reference security master is a deferred
consideration if cross-tenant sharing ever earns it; it would need the ticker-unique identity the schema
deliberately omits.

## Provisioning + the cut

`pipeline/provision_tenant.py` adds the `tenant` FK row that every table needs before any ingest can target
it. It only inserts that row — it never wipes or touches another tenant.

```powershell
# 1. provision an empty production tenant (prints the new tenant UUID; record it)
python -m pipeline.provision_tenant --name production

# 2. ingest the operator's real data UNDER that tenant (every ingest fn takes tenant_id=)
#    e.g. operator-ratify a catalyst into production:
python -m pipeline.ratify_catalyst --tenant-id <PROD_UUID> --ticker OKLO --type contract --grade core \
    --label "..." --source-url "https://www.sec.gov/..." --date 2026-07-15

# 3. upsert a thesis with tenant_id=<PROD_UUID>, then call it — it re-derives from ONLY this tenant's facts
```

`provision_tenant(conn, name, *, tenant_id=None)` defaults to a fresh `uuid4`; pass a fixed id for an
idempotent re-provision (`ON CONFLICT (id) DO NOTHING`). The caller owns the transaction.

## The isolation proof — `tests/db/test_tenant_isolation.py`

Isolation is **discipline + test**, not a DB-enforced guarantee (see the limitation below). These DB-backed
tests certify the discipline holds end-to-end:

- **(a) Read isolation, both axes.** A demo fact and a production fact at the same as-of are each visible
  only under their own tenant; the **cross-reads** (one tenant querying the other's security/thesis id)
  return `[]` — proving the `WHERE tenant_id` filter on `as_of` *and* `as_of_thesis` is load-bearing, not
  just the scope-id.
- **(b) Empty production → honest no-data.** A production thesis naming the **same ticker** as a fully-armed
  demo thesis still re-derives to `Incubating` with no triggers — the demo arm does not bleed across the
  seam (never a crash, never a leak).
- **(c) Production re-derives from production facts only.** With both tenants armed off the same fixtures
  under **distinct accessions**, each call cites only its own provenance — the production card carries the
  production accession and never the demo one, and vice versa (call-level isolation, not just row-level).
- **(d) The call of record lands in the thesis's tenant.** A `record=True` production call writes
  `calls.tenant_id = PROD`; a demo call writes demo. (Option B, proven — no split-tenant write.)
- **Smoke.** The whole cut on the identical code path: provision → ingest production's own facts (the proven
  cluster-buy + breakout arm) → upsert a production thesis → re-derive an **Armed** call isolated to
  production, with the per-tenant master resolving production's ticker and not bleeding to demo.

**Demo stays byte-unchanged.** For a demo thesis (`tenant_id == DEFAULT_TENANT_ID`), threading
`thesis.tenant_id` equals the old hardcoded default → byte-identical reads, writes, calls, and `card` JSON;
the existing suite stays green untouched.

## The current-tenant resolver (Slice 3 — the Workbench surface)

The Workbench is the front door for hunting + promoting theses, so it works **in** a tenant and resolves a
**current tenant** rather than listing all tenants mixed. This does **NOT** need auth — a deployment-config
setting scopes it:

- **`db.session.current_tenant_id()`** reads the **`ALPHADECK_TENANT_ID`** env var (a UUID), defaulting to
  `DEFAULT_TENANT_ID` (the demo); **`app.deps.get_current_tenant()`** is the FastAPI dependency (overridable
  in tests). **Deployment config, NOT authentication** — it states "this deployment serves tenant X".
- **Read and write take the tenant from different places, both correct:** the **`GET …/scored`** read takes
  its tenant from the **thesis** (intrinsic — a thesis lives in one tenant, exactly like the call read); the
  **`POST /workbench/theses`** promote/create takes its tenant from the **resolver** (which tenant to create
  the new thesis under).
- **Standing maintenance obligation (there is no RLS):** isolation is discipline + the poison-row test, so
  **every new read path MUST route through the tenant-filtered accessors** (`as_of` / `as_of_thesis` /
  `master.*` / the `PointInTimeData` methods) — never a raw fact query — **and
  `tests/db/test_tenant_isolation.py` MUST grow to cover it.** It now covers insider/price/theme, the three
  scoring-fact accessors, and the Workbench scored read. A forgotten filter on a raw query would leak with no
  DB backstop. (See `INVARIANTS.md` #5 + #8.)

## Known limitations — read these before relying on the cut

- **⚠️ The Board shows ALL tenants' theses mixed together (display mixing — NOT a fact leak).**
  `thesis_repo.list_all` is **not** tenant-scoped, so `GET /theses` (the Board/Cockpit list) returns the demo
  seed theses **and** production theses together. This is **display-only**: every *per-call* fact read is
  tenant-isolated (proven above), so no demo number ever appears inside a production call or vice versa — you
  will just see both tenants' theses in the list until `list_all` is tenant-scoped. The current-tenant
  resolver now **exists** (above), but `list_all` itself isn't yet wired to it — the Workbench's *per-thesis*
  reads are already tenant-correct; scoping the Board's *list* is the remaining piece, **deferred**. Do not
  mistake the mixed Board for a data leak, and do not rely on the Board to separate tenants yet.
- **⚠️ Isolation is discipline + the poison-row test, not DB-enforced.** There is **no row-level security
  (RLS)** — the `security_id` FK carries no tenant, so nothing at the database layer *forces* a read to pass
  the right `tenant_id`. The guarantee is the audited discipline "every read funnels through `as_of` /
  `master.*` with the tenant threaded", certified by the test above. This is appropriate for a single
  operator with a small, audited read surface; **RLS / DB-enforced tenancy is the defense-in-depth for the
  auth era**, not now.

## Out of scope / deferred

Runtime **auth / login / access control** (the tenant here is data isolation, not authentication);
**tenant-scoped `list_all`** (the Board mixing above — the current-tenant resolver now exists, but wiring
`list_all` to it is deferred); **RLS / DB-enforced tenancy**; a shared/reference security master;
production-replay CLI flags; and **deploy / infra** —
"production" here is a **tenant in the same deployment**; a separate production host / database / deployment
is a follow-up infra step, not this seam.
