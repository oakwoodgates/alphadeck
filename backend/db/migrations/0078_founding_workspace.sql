-- 0078 — the founding workspace: move the operator's workspace rows OUT of the data tenant (auth PR 2,
-- docs/TENANT_SCOPES.md).
--
-- Before this migration the one data tenant (...0001, `kind='data'`, 0076) also held the founding operator's
-- theses and their ratified facts. Left there, a ratified judgment would sit in the DATA scope, where every
-- other workspace's union read of a MIXED table would pick it up. So this migration:
--
--   1. creates the FOUNDING WORKSPACE, a fixed id (...0002, `domain.scopes.FOUNDING_WORKSPACE_ID`), so the
--      migration is safe to run unattended on boot;
--   2. moves into it every row of the 19 WORKSPACE tables that sits under ...0001, and every RATIFIED row
--      (`ratified_by IS NOT NULL`) of the 4 MIXED tables (a feed row, `ratified_by IS NULL`, stays DATA);
--   3. moves every `app_user` on ...0001 to ...0002;
--   4. adds a trigger on `app_user`: a user's `workspace_id` must name a `kind='workspace'` tenant, so no user
--      can sit on the data tenant again;
--   5. checks its own post-conditions and raises (rolling the whole file back) when any fails.
--
-- `tenant_id` is a SCOPE LABEL, not a temporal value, so this is an UPDATE of bookkeeping, not a correction of a
-- fact (invariant #1 is about facts; no value, date or recorded_at changes). The append-only `no_update` trigger
-- is disabled for this file's own statements and re-enabled right after, by name, never dropped. `db/migrate.py`
-- runs every pending file inside ONE transaction (one commit at the end), so there is no committed state with a
-- guard off.
--
-- Idempotent: a second run moves nothing (no WORKSPACE row and no ratified row is left under ...0001) and the
-- post-conditions still hold. On a fresh install, CI or a test database it moves nothing at all.
--
-- PRE-RUN COUNT (read-only; the operator runs it on prod before deploying, and again after, expecting zeros
-- under ...0001 for every line):
--
--   SELECT 'thesis' AS t, count(*) FROM thesis WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'basket_member', count(*) FROM basket_member WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'evidence', count(*) FROM evidence WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'catalyst', count(*) FROM catalyst WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'kill_criterion', count(*) FROM kill_criterion WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'thesis_exclusion', count(*) FROM thesis_exclusion WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'basket_snapshot', count(*) FROM basket_snapshot WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'calls', count(*) FROM calls WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'call_annotation', count(*) FROM call_annotation WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'operator_decision', count(*) FROM operator_decision WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'edition', count(*) FROM edition WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'edition_event', count(*) FROM edition_event WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'edition_entry', count(*) FROM edition_entry WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'edition_call', count(*) FROM edition_call WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'edition_outcome', count(*) FROM edition_outcome WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'edition_cause', count(*) FROM edition_cause WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'fact_theme_conviction', count(*) FROM fact_theme_conviction WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'fact_term_mentions', count(*) FROM fact_term_mentions WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'fact_spac_match', count(*) FROM fact_spac_match WHERE tenant_id = '00000000-0000-0000-0000-000000000001'
--   UNION ALL SELECT 'fact_revenue_mix (ratified)', count(*) FROM fact_revenue_mix WHERE tenant_id = '00000000-0000-0000-0000-000000000001' AND ratified_by IS NOT NULL
--   UNION ALL SELECT 'fact_cash_burn (ratified)', count(*) FROM fact_cash_burn WHERE tenant_id = '00000000-0000-0000-0000-000000000001' AND ratified_by IS NOT NULL
--   UNION ALL SELECT 'fact_shares_outstanding (ratified)', count(*) FROM fact_shares_outstanding WHERE tenant_id = '00000000-0000-0000-0000-000000000001' AND ratified_by IS NOT NULL
--   UNION ALL SELECT 'fact_catalyst (ratified)', count(*) FROM fact_catalyst WHERE tenant_id = '00000000-0000-0000-0000-000000000001' AND ratified_by IS NOT NULL
--   UNION ALL SELECT 'app_user', count(*) FROM app_user WHERE workspace_id = '00000000-0000-0000-0000-000000000001';
--
-- THE INVERSE (the move back; one transaction, by hand, only after a backup and only if a rollback is wanted).
-- It moves EVERYTHING under ...0002 back, so run it only while ...0002 is the only workspace holding rows:
--
--   BEGIN;
--   DROP TRIGGER IF EXISTS workspace_kind_check ON app_user;
--   ALTER TABLE <each table carrying no_update below> DISABLE TRIGGER no_update;
--   UPDATE <each of the 19 WORKSPACE tables> SET tenant_id = '00000000-0000-0000-0000-000000000001'
--       WHERE tenant_id = '00000000-0000-0000-0000-000000000002';
--   UPDATE <each of the 4 MIXED tables> SET tenant_id = '00000000-0000-0000-0000-000000000001'
--       WHERE tenant_id = '00000000-0000-0000-0000-000000000002' AND ratified_by IS NOT NULL;
--   UPDATE app_user SET workspace_id = '00000000-0000-0000-0000-000000000001'
--       WHERE workspace_id = '00000000-0000-0000-0000-000000000002';
--   ALTER TABLE <each table carrying no_update below> ENABLE TRIGGER no_update;
--   DELETE FROM schema_migrations WHERE filename = '0078_founding_workspace.sql';
--   COMMIT;
--
-- (The `tenant` row ...0002 can stay: an empty workspace reads as empty.)

-- 1. the founding workspace ------------------------------------------------------------------------------------
INSERT INTO tenant (id, name, kind)
VALUES ('00000000-0000-0000-0000-000000000002', 'founding workspace', 'workspace')
ON CONFLICT (id) DO NOTHING;

-- 2. the move (the append-only guard off for these statements only, by name) ---------------------------------
ALTER TABLE calls DISABLE TRIGGER no_update;
ALTER TABLE call_annotation DISABLE TRIGGER no_update;
ALTER TABLE operator_decision DISABLE TRIGGER no_update;
ALTER TABLE edition DISABLE TRIGGER no_update;
ALTER TABLE edition_event DISABLE TRIGGER no_update;
ALTER TABLE edition_entry DISABLE TRIGGER no_update;
ALTER TABLE edition_call DISABLE TRIGGER no_update;
ALTER TABLE edition_outcome DISABLE TRIGGER no_update;
ALTER TABLE edition_cause DISABLE TRIGGER no_update;
ALTER TABLE fact_theme_conviction DISABLE TRIGGER no_update;
ALTER TABLE fact_term_mentions DISABLE TRIGGER no_update;
ALTER TABLE fact_spac_match DISABLE TRIGGER no_update;
ALTER TABLE fact_revenue_mix DISABLE TRIGGER no_update;
ALTER TABLE fact_cash_burn DISABLE TRIGGER no_update;
ALTER TABLE fact_shares_outstanding DISABLE TRIGGER no_update;
ALTER TABLE fact_catalyst DISABLE TRIGGER no_update;

-- the 19 WORKSPACE tables (domain.scopes.SCOPE_OF; tests/db/test_0078_founding_workspace.py checks this list)
UPDATE thesis SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE basket_member SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE evidence SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE catalyst SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE kill_criterion SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE thesis_exclusion SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE basket_snapshot SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE calls SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE call_annotation SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE operator_decision SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE edition SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE edition_event SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE edition_entry SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE edition_call SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE edition_outcome SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE edition_cause SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE fact_theme_conviction SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE fact_term_mentions SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';
UPDATE fact_spac_match SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001';

-- the 4 MIXED tables: the RATIFIED rows only (a feed row, ratified_by NULL, is DATA and stays)
UPDATE fact_revenue_mix SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001' AND ratified_by IS NOT NULL;
UPDATE fact_cash_burn SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001' AND ratified_by IS NOT NULL;
UPDATE fact_shares_outstanding SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001' AND ratified_by IS NOT NULL;
UPDATE fact_catalyst SET tenant_id = '00000000-0000-0000-0000-000000000002' WHERE tenant_id = '00000000-0000-0000-0000-000000000001' AND ratified_by IS NOT NULL;

ALTER TABLE calls ENABLE TRIGGER no_update;
ALTER TABLE call_annotation ENABLE TRIGGER no_update;
ALTER TABLE operator_decision ENABLE TRIGGER no_update;
ALTER TABLE edition ENABLE TRIGGER no_update;
ALTER TABLE edition_event ENABLE TRIGGER no_update;
ALTER TABLE edition_entry ENABLE TRIGGER no_update;
ALTER TABLE edition_call ENABLE TRIGGER no_update;
ALTER TABLE edition_outcome ENABLE TRIGGER no_update;
ALTER TABLE edition_cause ENABLE TRIGGER no_update;
ALTER TABLE fact_theme_conviction ENABLE TRIGGER no_update;
ALTER TABLE fact_term_mentions ENABLE TRIGGER no_update;
ALTER TABLE fact_spac_match ENABLE TRIGGER no_update;
ALTER TABLE fact_revenue_mix ENABLE TRIGGER no_update;
ALTER TABLE fact_cash_burn ENABLE TRIGGER no_update;
ALTER TABLE fact_shares_outstanding ENABLE TRIGGER no_update;
ALTER TABLE fact_catalyst ENABLE TRIGGER no_update;

-- 3. the users -------------------------------------------------------------------------------------------------
UPDATE app_user SET workspace_id = '00000000-0000-0000-0000-000000000002'
WHERE workspace_id = '00000000-0000-0000-0000-000000000001';

-- 4. no user on the data tenant, ever again -------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app_user_workspace_kind_check() RETURNS trigger AS $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM tenant WHERE id = NEW.workspace_id AND kind = 'workspace') THEN
        RAISE EXCEPTION 'app_user.workspace_id % is not a workspace tenant (kind=''workspace''): a user never sits on the data tenant',
            NEW.workspace_id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS workspace_kind_check ON app_user;
CREATE TRIGGER workspace_kind_check BEFORE INSERT OR UPDATE OF workspace_id ON app_user
    FOR EACH ROW EXECUTE FUNCTION app_user_workspace_kind_check();

-- 5. the post-conditions (any failure raises and rolls the whole migration back) ------------------------------
DO $$
DECLARE
    t text;
    n bigint;
    data_tenant constant uuid := '00000000-0000-0000-0000-000000000001';
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'thesis', 'basket_member', 'evidence', 'catalyst', 'kill_criterion', 'thesis_exclusion',
        'basket_snapshot', 'calls', 'call_annotation', 'operator_decision', 'edition', 'edition_event',
        'edition_entry', 'edition_call', 'edition_outcome', 'edition_cause', 'fact_theme_conviction',
        'fact_term_mentions', 'fact_spac_match'
    ] LOOP
        EXECUTE format('SELECT count(*) FROM %I WHERE tenant_id = $1', t) INTO n USING data_tenant;
        IF n <> 0 THEN
            RAISE EXCEPTION '0078 post-condition: % rows of WORKSPACE table % remain on the data tenant', n, t;
        END IF;
    END LOOP;
    FOREACH t IN ARRAY ARRAY['fact_revenue_mix', 'fact_cash_burn', 'fact_shares_outstanding', 'fact_catalyst'] LOOP
        EXECUTE format('SELECT count(*) FROM %I WHERE tenant_id = $1 AND ratified_by IS NOT NULL', t)
            INTO n USING data_tenant;
        IF n <> 0 THEN
            RAISE EXCEPTION '0078 post-condition: % ratified rows of MIXED table % remain on the data tenant', n, t;
        END IF;
    END LOOP;
    SELECT count(*) INTO n FROM app_user WHERE workspace_id = data_tenant;
    IF n <> 0 THEN
        RAISE EXCEPTION '0078 post-condition: % users remain on the data tenant', n;
    END IF;
    SELECT count(*) INTO n FROM tenant
    WHERE id = '00000000-0000-0000-0000-000000000002' AND kind = 'workspace';
    IF n <> 1 THEN
        RAISE EXCEPTION '0078 post-condition: the founding workspace ...0002 is missing or not kind=workspace';
    END IF;
END $$;
