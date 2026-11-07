-- 0076 — tenant.kind + tenant.notify_webhook: the two tenant SCOPES (docs/TENANT_SCOPES.md).
--
-- The one `tenant_id` column now means one of two scopes, per table (`domain/scopes.py` names every table):
-- the single shared DATA tenant (the company master, every ingested fact, splits, ticker/CIK history, the
-- onboarding queue, the repair journal) and one WORKSPACE per user (the thesis and everything hanging off it).
-- This migration marks which `tenant` row is which:
--
--   kind = 'data'       exactly one row: the seeded tenant ...0001 (renamed 'platform')
--   kind = 'workspace'  every other row (the default; `pipeline.provision_tenant` creates these)
--
-- No other table changes and no row moves: after this migration the data tenant IS the only workspace, so every
-- read and write still touches ...0001 and every call is byte-identical (PR 2 moves the founding workspace out).
-- The API lifespan and the nightly refuse to run unless ...0001 is `kind='data'` (`scopes.assert_data_tenant`).
--
-- `notify_webhook` is the per-workspace push destination (NULL = log only); PR 2 reads it. Added now so the
-- `tenant` table needs no second migration.
--
-- Idempotent: every statement is re-runnable (IF NOT EXISTS / a guarded constraint / a no-op UPDATE). The `tenant`
-- table carries no append-only trigger (it is identity, not a fact), so the UPDATE needs no trigger dance.
ALTER TABLE tenant ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'workspace';
ALTER TABLE tenant ADD COLUMN IF NOT EXISTS notify_webhook text NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tenant_kind_check' AND conrelid = 'tenant'::regclass
    ) THEN
        ALTER TABLE tenant ADD CONSTRAINT tenant_kind_check CHECK (kind IN ('data', 'workspace'));
    END IF;
END $$;

-- the seeded tenant becomes THE data tenant (0001_init.sql inserted it; a fresh install has it too)
INSERT INTO tenant (id, name, kind)
VALUES ('00000000-0000-0000-0000-000000000001', 'platform', 'data')
ON CONFLICT (id) DO NOTHING;
UPDATE tenant SET kind = 'data', name = 'platform'
WHERE id = '00000000-0000-0000-0000-000000000001'
  AND (kind IS DISTINCT FROM 'data' OR name IS DISTINCT FROM 'platform');

-- exactly one data tenant can exist: a second `kind='data'` row fails on insert
CREATE UNIQUE INDEX IF NOT EXISTS tenant_one_data_kind ON tenant (kind) WHERE kind = 'data';
