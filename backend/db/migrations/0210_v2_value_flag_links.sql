-- 0210 — value_flag links: what a flag saw, what fixed it, what it reopens (V2 W5b; the W5 plan's Q4, decision B3 (a)).
--
-- WHAT. Three nullable columns on the flag table (0202), each set only on INSERT or on the one close:
--   * seen_candidate_id uuid NULL -> extraction_candidate  the candidate awaiting the steward when the flag was raised
--                                                          (a flag on "awaiting the steward"); the server fills it.
--                                                          A flag sees a value (seen_fact_id) OR a candidate, never
--                                                          both; neither = "no value yet".
--   * fixed_decision_id uuid NULL -> platform_value_decision  the steward's decision behind a 'fixed' close. NULL-SAFE:
--                                                          allowed only when outcome IS 'fixed' (an open row has
--                                                          outcome NULL, where a plain `outcome = 'fixed'` CHECK would
--                                                          be UNKNOWN and pass, so the check says IS NOT DISTINCT FROM).
--                                                          That the decision is of the same company and value type is
--                                                          checked by the close (platform_values/flags.py), in its
--                                                          transaction.
--   * reopens uuid NULL -> value_flag                       the steward's REOPEN (the inverse of a close): a NEW open
--                                                          row naming the closed one. At most one row reopens a given
--                                                          flag (a unique index). A trigger checks on INSERT that the
--                                                          reopened flag is closed, was not withdrawn by its user, and
--                                                          has the same user, company and value: the identity is
--                                                          copied, never chosen.
-- The closed-once trigger (0202) is unchanged: a closed row is never edited, so a reopen appends.
-- Plus one index for a user's own list (user_id, opened_at). No new table: domain/scopes.py and the cutover map
-- already name value_flag.
--
-- Additive + idempotent (ADD COLUMN IF NOT EXISTS; each check added only when absent; the trigger DROP-then-CREATE).
--
-- The inverse, by hand, after a backup:
--   DROP TRIGGER value_flag_reopen_shape ON value_flag; DROP FUNCTION value_flag_reopen_shape();
--   ALTER TABLE value_flag DROP COLUMN seen_candidate_id, DROP COLUMN fixed_decision_id, DROP COLUMN reopens;
--   DROP INDEX IF EXISTS ix_value_flag_user;
--   DELETE FROM schema_migrations WHERE filename = '0210_v2_value_flag_links.sql';

ALTER TABLE value_flag ADD COLUMN IF NOT EXISTS seen_candidate_id uuid NULL REFERENCES extraction_candidate (id);
ALTER TABLE value_flag ADD COLUMN IF NOT EXISTS fixed_decision_id uuid NULL REFERENCES platform_value_decision (id);
ALTER TABLE value_flag ADD COLUMN IF NOT EXISTS reopens           uuid NULL REFERENCES value_flag (id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'value_flag_fixed_link_only_when_fixed' AND conrelid = 'value_flag'::regclass
    ) THEN
        ALTER TABLE value_flag ADD CONSTRAINT value_flag_fixed_link_only_when_fixed
            CHECK (fixed_decision_id IS NULL OR outcome IS NOT DISTINCT FROM 'fixed');
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'value_flag_sees_one_thing' AND conrelid = 'value_flag'::regclass
    ) THEN
        ALTER TABLE value_flag ADD CONSTRAINT value_flag_sees_one_thing
            CHECK (seen_fact_id IS NULL OR seen_candidate_id IS NULL);
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_value_flag_one_reopen
    ON value_flag (reopens) WHERE reopens IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_value_flag_user
    ON value_flag (user_id, opened_at);

-- a reopen copies the reopened flag's identity and reopens only a steward's close
CREATE OR REPLACE FUNCTION value_flag_reopen_shape() RETURNS trigger AS $$
DECLARE
    t value_flag%ROWTYPE;
BEGIN
    IF NEW.reopens IS NULL THEN
        RETURN NEW;
    END IF;
    SELECT * INTO t FROM value_flag WHERE id = NEW.reopens;
    IF NOT FOUND THEN
        RETURN NEW;  -- an unknown id: the foreign key refuses it
    END IF;
    IF t.closed_at IS NULL THEN
        RAISE EXCEPTION 'value_flag: % is open, so it cannot be reopened', NEW.reopens;
    END IF;
    IF t.outcome = 'withdrawn' THEN
        RAISE EXCEPTION 'value_flag: % was withdrawn by its user; only a steward''s close is reopened', NEW.reopens;
    END IF;
    IF t.user_id IS DISTINCT FROM NEW.user_id
       OR t.security_id IS DISTINCT FROM NEW.security_id
       OR t.value_type IS DISTINCT FROM NEW.value_type
       OR t.value_ref IS DISTINCT FROM NEW.value_ref THEN
        RAISE EXCEPTION 'value_flag: a reopen must keep the user, company and value of %', NEW.reopens;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS value_flag_reopen_shape ON value_flag;
CREATE TRIGGER value_flag_reopen_shape BEFORE INSERT ON value_flag
    FOR EACH ROW EXECUTE FUNCTION value_flag_reopen_shape();
