-- 0212 — fact_thesis_catalyst: the counting statements of a card's catalyst entries (V2 W6a; the W6 plan, Q2).
--
-- WHAT. A new WORKSPACE, thesis-scoped, append-only fact table: one row per VERSION of an operator's statement
-- "this entry on my card's catalyst list counts toward the call" (the signed rule R2-R6). The calendar row
-- (`catalyst`: label, kind, typed date) stays display only; its id is the entry's `entry_id`. Nothing reads this
-- table into a call yet (W6c does); W6a adds the store, its registrations and its one writer
-- (`repositories/thesis_catalyst_repo.py`).
--
--   * entry_id       the calendar row's id (stable across saves from W6b): the entry's identity within the card
--   * counts         the switch "counts toward the call" (R2); a switch-off is a version with counts = false
--   * security_ids   the named members, sorted and unique; empty = the whole card (R4, the theme conviction)
--   * grade          'core' | 'flip'; a whole-card entry is flip only (N2)
--   * label          the trigger's label at this version
--   * citation       a URL or an SEC accession (R2); never empty
--   * relevant_until the operator's horizon (R6); NULL = the default horizon
--   * source         'thesis_catalyst' (an operator version) | 'ratified' (the one converted theme row, W6c 0213)
--   * entered_at     an operator version's PUBLIC instant: the database's now() at the save, equal to its
--                    recorded_at (CHECK). No caller supplies it: the writer passes the SQL now(), never a value.
--                    NULL only on the converted row, which counts from 06:00 of its stated date (N4 (a))
--   * entered_by     the signed-in user's app_user id; NULL on the converted row
--   * valid_from     the night it counts from: edgar_night(entered_at) when the version turns the switch on or
--                    changes the grade or the members; the previous version's night otherwise (N5 (a))
--
-- The typed calendar date (`catalyst.when_date`) is not here and never moves when an entry counts (R5).
--
-- ALSO drops 0204's four columns on `catalyst` (security_ids, grade, citation, entered_at; operator decision N11
-- (a)): never read by any code and empty on every row; a delete-and-reinsert table cannot carry versions.
--
-- Idempotent: CREATE ... IF NOT EXISTS, DROP ... IF EXISTS, the trigger dropped and re-created.
--
-- The inverse, by hand, after a backup:
--   DROP TABLE fact_thesis_catalyst;
--   ALTER TABLE catalyst ADD COLUMN security_ids uuid[] NOT NULL DEFAULT '{}', ADD COLUMN grade text NULL,
--       ADD COLUMN citation text NULL, ADD COLUMN entered_at timestamptz NULL,
--       ADD CONSTRAINT catalyst_grade_check CHECK (grade IS NULL OR grade IN ('core', 'flip'));
--   DELETE FROM schema_migrations WHERE filename = '0212_v2_thesis_catalyst_fact.sql';

CREATE TABLE IF NOT EXISTS fact_thesis_catalyst (
    id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id      uuid NOT NULL REFERENCES tenant (id),
    thesis_id      uuid NOT NULL REFERENCES thesis (id) ON DELETE CASCADE,
    entry_id       uuid NOT NULL,
    counts         boolean NOT NULL,
    security_ids   uuid[] NOT NULL DEFAULT '{}',
    grade          text NOT NULL,
    label          text NOT NULL,
    citation       text NOT NULL,
    relevant_until date NULL,
    source         text NOT NULL,
    entered_at     timestamptz NULL,
    entered_by     uuid NULL,
    valid_from     date NOT NULL,
    valid_to       date NULL,
    recorded_at    timestamptz NOT NULL DEFAULT now(),
    supersedes     uuid REFERENCES fact_thesis_catalyst (id),
    CONSTRAINT fact_thesis_catalyst_grade CHECK (grade IN ('core', 'flip')),
    CONSTRAINT fact_thesis_catalyst_source CHECK (source IN ('thesis_catalyst', 'ratified')),
    CONSTRAINT fact_thesis_catalyst_citation CHECK (btrim(citation) <> ''),
    CONSTRAINT fact_thesis_catalyst_whole_card_is_flip
        CHECK (cardinality(security_ids) > 0 OR grade = 'flip'),
    CONSTRAINT fact_thesis_catalyst_no_null_member CHECK (array_position(security_ids, NULL) IS NULL),
    CONSTRAINT fact_thesis_catalyst_operator_has_entry_time
        CHECK ((source = 'thesis_catalyst') = (entered_at IS NOT NULL)),
    CONSTRAINT fact_thesis_catalyst_entry_time_is_recorded_at
        CHECK (entered_at IS NULL OR entered_at = recorded_at),
    UNIQUE (tenant_id, thesis_id, entry_id, recorded_at)
);

CREATE INDEX IF NOT EXISTS ix_thesis_catalyst_asof
    ON fact_thesis_catalyst (tenant_id, thesis_id, valid_from, recorded_at);

-- Append-only guard (same mechanism as the other fact tables).
DROP TRIGGER IF EXISTS no_update ON fact_thesis_catalyst;
CREATE TRIGGER no_update BEFORE UPDATE ON fact_thesis_catalyst
    FOR EACH ROW EXECUTE FUNCTION raise_no_update();

-- N11 (a): 0204's four unused columns on the calendar row
ALTER TABLE catalyst DROP CONSTRAINT IF EXISTS catalyst_grade_check;
ALTER TABLE catalyst DROP COLUMN IF EXISTS security_ids;
ALTER TABLE catalyst DROP COLUMN IF EXISTS grade;
ALTER TABLE catalyst DROP COLUMN IF EXISTS citation;
ALTER TABLE catalyst DROP COLUMN IF EXISTS entered_at;
