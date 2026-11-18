-- 0211 — value_flag.reopened_by: WHO reopened a user's flag (V2 W5c; the W5 plan's Q6, decision B3 (a)).
--
-- WHAT. One nullable column on the flag table (0202, 0210):
--   * reopened_by text NULL   the steward's email on a REOPEN row (a new open row whose `reopens` names the closed
--                             flag); WHEN is that row's own opened_at. The server fills it, never the request.
--                             The steward's history shows who reopened and when; a plain user's answers never
--                             carry it (the user's routes list fixed fields that do not include it).
-- A CHECK keeps it to reopen rows: an ordinary flag never names a reopener. It is not required on a reopen row in
-- the database (the app always sets it, platform_values/flags.py); a reopen row written before this migration, or
-- by hand, simply has none.
-- 0210's reopen trigger and 0202's closed-once trigger are unchanged: a reopen still appends, a closed row (its
-- reopener included) is never edited. No new table: domain/scopes.py and the cutover map already name value_flag.
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; the check added only when absent).
--
-- The inverse, by hand, after a backup:
--   ALTER TABLE value_flag DROP CONSTRAINT value_flag_reopener_only_on_reopen;
--   ALTER TABLE value_flag DROP COLUMN reopened_by;
--   DELETE FROM schema_migrations WHERE filename = '0211_v2_value_flag_reopened_by.sql';

ALTER TABLE value_flag ADD COLUMN IF NOT EXISTS reopened_by text NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'value_flag_reopener_only_on_reopen' AND conrelid = 'value_flag'::regclass
    ) THEN
        ALTER TABLE value_flag ADD CONSTRAINT value_flag_reopener_only_on_reopen
            CHECK (reopened_by IS NULL OR reopens IS NOT NULL);
    END IF;
END
$$;
