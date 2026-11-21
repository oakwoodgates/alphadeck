-- 0204 — the thesis catalyst's new fields (V2 W1b; the V2 W0/W1 plan, Q5 and finding 3).
--
-- WHAT. Four columns on the thesis `catalyst` row (a WORKSPACE table):
--   * security_ids — the basket members the entry names. An ARRAY on the row, not a child table: the editor's
--                    save (`thesis_repo.set_catalysts`) deletes and re-inserts every row with a fresh id, so a
--                    child table would be wiped on each save. No foreign key is possible on an array; W6
--                    validates at write time that each id is a member of the thesis and ignores ids that have
--                    since left the basket. Empty = names no member.
--   * grade        — 'core' | 'flip' (the trigger grade, CLAUDE.md), NULL = ungraded.
--   * citation     — the source the entry cites, NULL = none.
--   * entered_at   — when the entry was entered. NULL on every existing row: no time is invented.
-- "Counts toward the call" is NOT stored: W6 derives it from these fields (no stored flag can disagree with them).
--
-- Existing rows: unchanged; they read an empty member list and NULL grade, citation and entry time, so they stay
-- calendar items. Until W6 changes the writer, `set_catalysts` writes none of these and so wipes them on every
-- save (`tests/db/test_v2_schema.py::test_set_catalysts_drops_the_new_fields_on_save` pins that). Nothing reads
-- them yet.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS and a guarded constraint (the 0076 pattern). No row changes.
--
-- The inverse, by hand, after a backup:
--   ALTER TABLE catalyst DROP COLUMN security_ids, DROP COLUMN grade, DROP COLUMN citation,
--       DROP COLUMN entered_at;
--   DELETE FROM schema_migrations WHERE filename = '0204_v2_thesis_catalyst_columns.sql';

ALTER TABLE catalyst ADD COLUMN IF NOT EXISTS security_ids uuid[]      NOT NULL DEFAULT '{}';  -- named members
ALTER TABLE catalyst ADD COLUMN IF NOT EXISTS grade        text        NULL;                   -- core | flip
ALTER TABLE catalyst ADD COLUMN IF NOT EXISTS citation     text        NULL;
ALTER TABLE catalyst ADD COLUMN IF NOT EXISTS entered_at   timestamptz NULL;                   -- NULL on every existing row

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'catalyst_grade_check' AND conrelid = 'catalyst'::regclass
    ) THEN
        ALTER TABLE catalyst ADD CONSTRAINT catalyst_grade_check
            CHECK (grade IS NULL OR grade IN ('core', 'flip'));
    END IF;
END $$;
