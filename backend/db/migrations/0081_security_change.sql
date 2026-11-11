-- 0081 — security_change: the per-company CHANGE STAMP the dashboards' cache reads (docs/DASHBOARDS.md rule 7).
--
-- WHY. The dashboards cache per-company pieces (a company's price flags, a display member's reading) and whole
-- pages. A company's stored data changes through many writers the cache's old generation token never saw: the
-- end of a fact ingest (the nightly, the fast-lane ingest, the staged history load, the per-name price pull), a
-- ratified fact, a business-type re-tag, an identity enrichment, a split decision, a price-feed fix, a Form 4/A
-- decision, the weekly company-list refresh. Each of those events now moves THIS company's stamp, and the
-- cache drops exactly that company's pieces (and the page entries) on its next lookup.
--
-- ONE ROW PER COMPANY (security_id is the key). `seq` comes from `security_change_seq` (also its column
-- default) and is re-drawn on every change; `changed_at` is when. Written by `db/change_stamp.mark_changed` only: the company write lock's
-- release (`db/write_lock.CompanyLock.release`) and the narrow writers that change a company's stored data
-- outside that lock.
--
-- WHAT IT IS NOT. Bookkeeping, updated in place (precedent: `security_onboarding`, 0059): never a fact, never
-- read by the call path, never a history gate (invariant #1 judges history on the public clock). A lost or
-- extra row only makes a dashboard recompute (or, lost, serve a company stale until the next change).
--
-- DATA scope (domain/scopes.py), like `security_master`: the row copies its master row's tenant_id.
-- Additive + idempotent (IF NOT EXISTS). ON DELETE CASCADE from security_master: never blocks a master delete.
--
-- The inverse, by hand, after a backup:
--   DROP TABLE security_change; DROP SEQUENCE security_change_seq;
--   DELETE FROM schema_migrations WHERE filename = '0081_security_change.sql';

CREATE SEQUENCE IF NOT EXISTS security_change_seq AS bigint;

CREATE TABLE IF NOT EXISTS security_change (
    tenant_id    uuid        NOT NULL REFERENCES tenant (id),
    security_id  uuid        PRIMARY KEY REFERENCES security_master (id) ON DELETE CASCADE,
    seq          bigint      NOT NULL DEFAULT nextval('security_change_seq'),
    changed_at   timestamptz NOT NULL DEFAULT now()
);
