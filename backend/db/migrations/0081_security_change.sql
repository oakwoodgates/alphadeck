-- 0081 — security_change: the per-company CHANGE LOG the dashboards' cache reads (docs/DASHBOARDS.md rule 7).
--
-- WHY. The dashboards cache per-company pieces (a company's price flags, a display member's reading) and whole
-- pages. A company's stored data changes through many writers the cache's old generation token never saw: the
-- end of a fact ingest (the nightly, the fast-lane ingest, the staged history load, the per-name price pull), a
-- ratified fact, a business-type re-tag, an identity enrichment, a split decision, a price-feed fix, a Form 4/A
-- decision, the weekly company-list refresh. Each of those events now appends a row for THIS company, and the
-- cache drops exactly that company's pieces (and the page entries) on its next lookup.
--
-- APPEND-ONLY. One row per change: `seq` (from `security_change_seq`, the key) and `changed_at`. A stamp is an
-- INSERT, so no stamp ever waits for another (no row lock is shared), and a rolled-back stamp never hides a
-- committed one. Written by `db/change_stamp.mark_changed` only: the company write lock's release
-- (`db/write_lock.CompanyLock.release`) and the narrow writers that change a company's stored data outside
-- that lock. BOUNDED by the prune (`python -m db.change_stamp --prune`, rows older than 24 hours), which the
-- cron sidecar runs after each nightly pass.
--
-- WHAT IT IS NOT. Bookkeeping: never a fact, never read by the call path, never a history gate (invariant #1
-- judges history on the public clock). A lost row only makes a dashboard serve a company stale until the next
-- change or pass; an extra one only makes it recompute.
--
-- DATA scope (domain/scopes.py), like `security_master`: the row copies its master row's tenant_id. NO foreign
-- key to `security_master`: a stamp must never take a lock on a master row.
-- Additive + idempotent (IF NOT EXISTS).
--
-- The inverse, by hand, after a backup:
--   DROP TABLE security_change; DROP SEQUENCE security_change_seq;
--   DELETE FROM schema_migrations WHERE filename = '0081_security_change.sql';

CREATE SEQUENCE IF NOT EXISTS security_change_seq AS bigint;

CREATE TABLE IF NOT EXISTS security_change (
    seq          bigint      PRIMARY KEY DEFAULT nextval('security_change_seq'),
    tenant_id    uuid        NOT NULL REFERENCES tenant (id),
    security_id  uuid        NOT NULL,
    changed_at   timestamptz NOT NULL DEFAULT now()
);

-- the prune's range delete
CREATE INDEX IF NOT EXISTS ix_security_change_changed_at ON security_change (changed_at);
