-- 0203 — the workspace caps on `tenant` (V2 W1b; the V2 W0/W1 plan, Q4).
--
-- Three nullable integer columns; NULL = NO CAP, so every existing tenant (the data tenant, the founding workspace,
-- every user's workspace) is unchanged and never blocked. Zero is a real cap. Nothing reads them yet: W7 sets the
-- defaults where new workspaces are created (pipeline/users.py) and enforces them.
--   * cap_live_cards     — live (non-archived) cards in the workspace;
--   * cap_companies      — distinct companies across the workspace's live cards;
--   * cap_terms_per_card — search terms on one card.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS and guarded constraints (the 0076 pattern). No row changes.
--
-- The inverse, by hand, after a backup:
--   ALTER TABLE tenant DROP COLUMN cap_live_cards, DROP COLUMN cap_companies, DROP COLUMN cap_terms_per_card;
--   DELETE FROM schema_migrations WHERE filename = '0203_v2_workspace_caps.sql';

ALTER TABLE tenant ADD COLUMN IF NOT EXISTS cap_live_cards     integer NULL;   -- NULL = no cap
ALTER TABLE tenant ADD COLUMN IF NOT EXISTS cap_companies      integer NULL;   -- distinct companies across live cards
ALTER TABLE tenant ADD COLUMN IF NOT EXISTS cap_terms_per_card integer NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tenant_cap_live_cards_check' AND conrelid = 'tenant'::regclass
    ) THEN
        ALTER TABLE tenant ADD CONSTRAINT tenant_cap_live_cards_check
            CHECK (cap_live_cards IS NULL OR cap_live_cards >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tenant_cap_companies_check' AND conrelid = 'tenant'::regclass
    ) THEN
        ALTER TABLE tenant ADD CONSTRAINT tenant_cap_companies_check
            CHECK (cap_companies IS NULL OR cap_companies >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'tenant_cap_terms_per_card_check' AND conrelid = 'tenant'::regclass
    ) THEN
        ALTER TABLE tenant ADD CONSTRAINT tenant_cap_terms_per_card_check
            CHECK (cap_terms_per_card IS NULL OR cap_terms_per_card >= 0);
    END IF;
END $$;
