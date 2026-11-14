-- 0202 — value_flag: a user's "this platform value looks wrong" (V2 W1b; the V2 W0/W1 plan, Q4).
--
-- WHAT. One row per flag a user raises on a company's share count, cash/burn, or one platform catalyst
-- (`value_ref` = that catalyst's source_ref; '' for shares and cash). `seen_fact_id` is the fact version the user
-- was looking at (no foreign key: it can point into several fact tables). The steward closes it once, with an
-- outcome: 'fixed', 'kept' or 'withdrawn' (the user's own withdrawal).
--   * ONE OPEN FLAG per (user, company, value type, value ref): a partial unique index. After it closes, the same
--     user may open a new one.
--   * CLOSED ONCE: `closed_at` and `outcome` are set together (a CHECK), and any UPDATE of a row that is already
--     closed raises (the `value_flag_closed_once` trigger) — a closed flag is never reopened or edited.
--   * Text limits live on the request model (CLAUDE.md), never in the database. The daily flag count rides the
--     existing allowance (user_action_count) with a new action in W5, no schema here.
--
-- USER-KEYED, no tenant_id (like user_action_count): domain/scopes.py names it in UNSCOPED_TABLES. A user's flags
-- go with the user (ON DELETE CASCADE). Nothing reads or writes it yet (W5).
-- Depends on 0001 (security_master) and 0077 (app_user).
-- Additive + idempotent (CREATE ... IF NOT EXISTS / OR REPLACE; the trigger DROP-then-CREATE).
--
-- The inverse, by hand, after a backup:
--   DROP TABLE value_flag;
--   DROP FUNCTION value_flag_closed_once();
--   DELETE FROM schema_migrations WHERE filename = '0202_v2_value_flag.sql';

CREATE TABLE IF NOT EXISTS value_flag (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id      uuid        NOT NULL REFERENCES app_user (id) ON DELETE CASCADE,
    security_id  uuid        NOT NULL REFERENCES security_master (id),
    value_type   text        NOT NULL CHECK (value_type IN ('shares_outstanding', 'cash_burn', 'catalyst')),
    value_ref    text        NOT NULL DEFAULT '',   -- a platform catalyst's source_ref; '' for shares and cash
    seen_fact_id uuid        NULL,                  -- the version the user saw (no FK: several fact tables)
    note         text        NULL,                  -- limits live on the request model, not here
    opened_at    timestamptz NOT NULL DEFAULT now(),
    closed_at    timestamptz NULL,
    closed_by    text        NULL,
    outcome      text        NULL CHECK (outcome IN ('fixed', 'kept', 'withdrawn')),
    outcome_note text        NULL,
    CONSTRAINT value_flag_closed_pairing CHECK ((closed_at IS NULL) = (outcome IS NULL)),
    CONSTRAINT value_flag_ref_only_for_catalysts CHECK (value_type = 'catalyst' OR value_ref = '')
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_value_flag_one_open
    ON value_flag (user_id, security_id, value_type, value_ref) WHERE closed_at IS NULL;
CREATE INDEX IF NOT EXISTS ix_value_flag_open_company
    ON value_flag (security_id) WHERE closed_at IS NULL;

-- a flag closes once: any UPDATE of a row that is already closed raises
CREATE OR REPLACE FUNCTION value_flag_closed_once() RETURNS trigger AS $$
BEGIN
    IF OLD.closed_at IS NOT NULL THEN
        RAISE EXCEPTION
            'value_flag % is closed; a closed flag is never reopened or edited (open a new one)', OLD.id;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS value_flag_closed_once ON value_flag;
CREATE TRIGGER value_flag_closed_once BEFORE UPDATE ON value_flag
    FOR EACH ROW EXECUTE FUNCTION value_flag_closed_once();
